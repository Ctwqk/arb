"""LLM client for pair validation and news refinement via OpenAI-compatible API."""

import asyncio
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Optional, Sequence

import httpx

logger = logging.getLogger(__name__)

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_GENERIC_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "be",
    "for",
    "in",
    "is",
    "new",
    "of",
    "on",
    "or",
    "the",
    "to",
    "will",
}
_OPTIONAL_CONTEXT_TOKENS = {
    "fc",
    "cf",
    "ac",
    "sc",
    "uefa",
    "nhl",
    "nba",
    "nfl",
    "mlb",
    "ncaa",
    "fifa",
    "ufc",
}


def _strip_think(text: str) -> str:
    """Remove <think>...</think> blocks from model output."""
    return _THINK_RE.sub("", text).strip()


def _normalize_name(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _normalize_contract_text(text: str) -> str:
    normalized = (text or "").strip().lower()
    normalized = normalized.replace("–", "-").replace("—", "-")
    normalized = re.sub(r"\bversus\b|\bv\.\b|\bvs\.\b|@", " vs ", normalized)
    normalized = re.sub(r"\bend in (?:a )?(?:tie|draw)\b", " draw ", normalized)
    normalized = re.sub(r"\btie\b", "draw", normalized)
    normalized = re.sub(r"\b(20\d{2})-(\d{2})\b", " ", normalized)
    normalized = re.sub(r"\b(20\d{2})-(20\d{2})\b", " ", normalized)
    normalized = re.sub(r"[^\w\s]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _extract_years(text: str) -> set[int]:
    return {int(value) for value in re.findall(r"\b(20\d{2})\b", text or "")}


def _extract_contract_tokens(text: str) -> list[str]:
    tokens = []
    for token in _normalize_contract_text(text).split():
        if token in _GENERIC_STOPWORDS:
            continue
        if token in _OPTIONAL_CONTEXT_TOKENS:
            continue
        if re.fullmatch(r"20\d{2}", token):
            continue
        tokens.append(token)
    return tokens


def _token_equivalent(left: str, right: str) -> bool:
    if left == right:
        return True
    if left.startswith(right) or right.startswith(left):
        return True
    return False


def _token_set_contains(container: Sequence[str], members: Sequence[str]) -> bool:
    return all(any(_token_equivalent(member, candidate) for candidate in container) for member in members)


def _extract_vs_entities(text: str) -> list[str]:
    match = re.search(r"(.+?)\s+(?:vs\.?|versus|v\.?|at|@)\s+(.+)", text, re.IGNORECASE)
    if not match:
        return []
    left = match.group(1)
    right = match.group(2)
    left = re.sub(r"^will\s+", "", left, flags=re.IGNORECASE)
    left = re.sub(r".*\bwin(?:\s+in\s+the)?\s+", "", left, flags=re.IGNORECASE)
    right = re.split(r"\s*[:?]", right, maxsplit=1)[0]
    right = re.split(r"\s+(?:winner|match|game|round|end)\b", right, maxsplit=1, flags=re.IGNORECASE)[0]
    left = left.strip()
    right = right.strip()
    if not left or not right:
        return []
    return [left, right]


def _shared_entity_count(left: Sequence[str], right: Sequence[str]) -> int:
    left_set = {_normalize_name(item) for item in left if item}
    right_set = {_normalize_name(item) for item in right if item}
    return len(left_set & right_set)


def _looks_like_draw_market(text: str) -> bool:
    lowered = text.lower()
    return "draw" in lowered or "tie" in lowered


def _extract_explicit_date(text: str) -> Optional[tuple[str, int, Optional[int]]]:
    match = re.search(
        r"\b("
        r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
        r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|"
        r"nov(?:ember)?|dec(?:ember)?"
        r")\s+(\d{1,2})(?:,\s*(\d{4}))?\b",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    month = match.group(1).lower()[:3]
    day = int(match.group(2))
    year = int(match.group(3)) if match.group(3) else None
    return (month, day, year)


def _titles_have_compatible_explicit_dates(left: str, right: str) -> bool:
    left_date = _extract_explicit_date(left)
    right_date = _extract_explicit_date(right)
    if not left_date or not right_date:
        return True
    if left_date[:2] != right_date[:2]:
        return False
    if left_date[2] is not None and right_date[2] is not None and left_date[2] != right_date[2]:
        return False
    return True


def _parse_timestamp(raw: object) -> Optional[float]:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if not isinstance(raw, str):
        return None
    value = raw.strip()
    if not value:
        return None
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _synthesize_kalshi_candidate_question(title: str, yes_sub_title: str) -> Optional[str]:
    candidate = str(yes_sub_title or "").strip()
    normalized_title = str(title or "").strip().rstrip("?")
    if not candidate or not normalized_title:
        return None

    lowered = normalized_title.lower()
    if lowered.startswith("will "):
        return None

    if lowered.startswith("who will "):
        remainder = normalized_title[9:].strip()
        remainder_lower = remainder.lower()
        if remainder_lower.startswith("win "):
            return f"Will {candidate} win {remainder[4:].strip()}?"
        if remainder_lower.startswith("be "):
            return f"Will {candidate} be {remainder[3:].strip()}?"
        if remainder_lower.startswith("run "):
            return f"Will {candidate} run {remainder[4:].strip()}?"
        if remainder_lower.startswith("have "):
            return f"Will {candidate} have {remainder[5:].strip()}?"
        return f"Will {candidate} {remainder}?"

    return None


def _build_kalshi_contract_text(title: str, yes_sub_title: str) -> str:
    normalized_title = str(title or "").strip()
    yes_sub_title = str(yes_sub_title or "").strip()
    if not normalized_title:
        return ""
    if not yes_sub_title:
        return normalized_title

    synthesized = _synthesize_kalshi_candidate_question(normalized_title, yes_sub_title)
    if synthesized:
        return synthesized

    stripped = normalized_title.rstrip("?").strip()
    if stripped.lower().endswith(" winner"):
        event_name = stripped[: -len(" winner")].strip()
        if yes_sub_title.lower() == "tie":
            return f"Will {event_name} end in a draw?"
        return f"Will {yes_sub_title} win {event_name}?"

    return f"{normalized_title} Outcome: {yes_sub_title}"


def _years_are_compatible(kalshi_title: str, poly_title: str, kalshi_end_date: str, poly_end_date: str) -> bool:
    kalshi_years = _extract_years(kalshi_title)
    poly_years = _extract_years(poly_title)
    if kalshi_years and poly_years and kalshi_years.isdisjoint(poly_years):
        return False

    kalshi_end_ts = _parse_timestamp(kalshi_end_date)
    poly_end_ts = _parse_timestamp(poly_end_date)
    if kalshi_end_ts is None or poly_end_ts is None:
        return True
    return abs(kalshi_end_ts - poly_end_ts) <= 370 * 86400


def _prevalidate_pair(
    kalshi_title: str,
    kalshi_yes_outcome: str,
    kalshi_end_date: str,
    poly_title: str,
    poly_end_date: str,
) -> Optional[bool]:
    kalshi_contract = _build_kalshi_contract_text(kalshi_title, kalshi_yes_outcome)
    poly_contract = str(poly_title or "").strip()
    if not kalshi_contract or not poly_contract:
        return None

    if not _titles_have_compatible_explicit_dates(kalshi_contract, poly_contract):
        return None
    if not _years_are_compatible(kalshi_contract, poly_contract, kalshi_end_date, poly_end_date):
        return None

    kalshi_entities = _extract_vs_entities(kalshi_contract)
    poly_entities = _extract_vs_entities(poly_contract)
    if kalshi_entities and poly_entities and _shared_entity_count(kalshi_entities, poly_entities) == 2:
        if _looks_like_draw_market(kalshi_contract) == _looks_like_draw_market(poly_contract):
            return True

    kalshi_tokens = _extract_contract_tokens(kalshi_contract)
    poly_tokens = _extract_contract_tokens(poly_contract)
    if not kalshi_tokens or not poly_tokens:
        return None

    if _token_set_contains(poly_tokens, kalshi_tokens) and _token_set_contains(kalshi_tokens, poly_tokens):
        return True

    overlap = len(set(kalshi_tokens) & set(poly_tokens))
    baseline = max(len(set(kalshi_tokens)), len(set(poly_tokens)))
    if baseline and overlap / baseline >= 0.9:
        return True

    return None


class LLMClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        timeout: float = 120.0,
        source: str = "arb-validator",
        max_retries: int = 3,
    ):
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout
        self._source = source
        self._max_retries = max(1, max_retries)
        self._client = httpx.AsyncClient(timeout=timeout)

    async def _chat(self, prompt: str, max_tokens: int = 1000) -> Optional[str]:
        """Send a chat completion request. Returns stripped response text."""
        for attempt in range(1, self._max_retries + 1):
            client_request_id = f"{self._source}:{uuid.uuid4()}"
            try:
                resp = await self._client.post(
                    f"{self._base_url}/chat/completions",
                    json={
                        "source": self._source,
                        "client_request_id": client_request_id,
                        "model": self._model,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0,
                        "max_tokens": max_tokens,
                    },
                )
                if resp.status_code != 200:
                    logger.warning(
                        "LLM returned %d client_request_id=%s attempt=%d/%d watchdog_job_id=%s: %s",
                        resp.status_code,
                        client_request_id,
                        attempt,
                        self._max_retries,
                        resp.headers.get("x-watchdog-job-id"),
                        resp.text[:200],
                    )
                    if attempt < self._max_retries:
                        await asyncio.sleep(min(5.0, float(attempt)))
                        continue
                    return None
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                cleaned = _strip_think(content)
                if cleaned:
                    return cleaned
                logger.warning(
                    "LLM returned empty content client_request_id=%s attempt=%d/%d",
                    client_request_id,
                    attempt,
                    self._max_retries,
                )
                if attempt < self._max_retries:
                    await asyncio.sleep(min(5.0, float(attempt)))
                    continue
                return None
            except Exception:
                logger.exception(
                    "LLM request failed client_request_id=%s attempt=%d/%d",
                    client_request_id,
                    attempt,
                    self._max_retries,
                )
                if attempt < self._max_retries:
                    await asyncio.sleep(min(5.0, float(attempt)))
                    continue
                return None
        return None

    async def validate_pair(
        self,
        kalshi_title: str,
        kalshi_desc: str,
        kalshi_yes_outcome: str,
        kalshi_end_date: str,
        poly_title: str,
        poly_desc: str,
        poly_end_date: str,
    ) -> Optional[bool]:
        """Ask LLM if two markets are about the same event. Returns True/False/None."""
        prevalidated = _prevalidate_pair(
            kalshi_title=kalshi_title,
            kalshi_yes_outcome=kalshi_yes_outcome,
            kalshi_end_date=kalshi_end_date,
            poly_title=poly_title,
            poly_end_date=poly_end_date,
        )
        if prevalidated is not None:
            logger.info("Prevalidated pair without LLM: K=%s P=%s", kalshi_title[:80], poly_title[:80])
            return prevalidated

        desc_a = f"\n  Description: {kalshi_desc}" if kalshi_desc else ""
        desc_b = f"\n  Description: {poly_desc}" if poly_desc else ""
        outcome_a = f"\n  YES resolves for: {kalshi_yes_outcome}" if kalshi_yes_outcome else ""
        end_a = f"\n  Resolution/end date: {kalshi_end_date}" if kalshi_end_date else ""
        end_b = f"\n  Resolution/end date: {poly_end_date}" if poly_end_date else ""
        normalized_a = _build_kalshi_contract_text(kalshi_title, kalshi_yes_outcome)
        normalized_b = str(poly_title or "").strip()

        prompt = (
            "You verify whether two prediction market contracts are EXACTLY equivalent.\n"
            "Say yes only if both markets refer to the same real-world event and the same YES resolution outcome.\n"
            "Kalshi often stores the specific YES outcome in the YES-resolves-for field. Treat that field as part of the contract.\n"
            "A missing season year can still be equivalent when the event and resolution window clearly match.\n"
            'Treat "tie" and "draw" as equivalent wording.\n'
            "If there is any mismatch in candidate/person/team/location/date/time window/threshold/range/statistic/"
            "round/stage/division/seat/song-vs-album/top-10-vs-win/qualify-vs-win/advance-vs-win, answer no.\n"
            "When uncertain, answer no.\n\n"
            f"Market A (Kalshi):\n  Title: {kalshi_title}{desc_a}{outcome_a}{end_a}\n\n"
            f"Normalized interpretation of Market A: {normalized_a}\n\n"
            f"Market B (Polymarket):\n  Title: {poly_title}{desc_b}{end_b}\n"
            f"Normalized interpretation of Market B: {normalized_b}\n\n"
            "Examples:\n"
            "  - 'Germany vs Ghana Winner?' with YES resolves for 'Tie' matches 'Will Germany vs. Ghana end in a draw?' => yes\n"
            "  - 'Will Porto win the Europa League?' matches 'Will Porto win the 2025-26 UEFA Europa League?' => yes\n"
            "  - 'Will Olivia Rodrigo release a new album in 2026?' matches 'Will Olivia Rodrigo release an album in 2026?' => yes\n"
            "  - 'Who will win Hart Memorial Trophy?' with YES resolves for 'Connor Bedard' matches "
            "'Will Connor Bedard win the 2025-2026 NHL Hart Memorial Trophy?' => yes\n"
            "  - 'Will Team X finish top 10?' does NOT match 'Will Team X win the championship?' => no\n"
            "  - 'Unemployment rate in August 2026' does NOT match 'March 2026 unemployment rate' => no\n\n"
            'Are these the SAME contract? Answer ONLY "yes" or "no".'
        )
        answer = await self._chat(prompt, max_tokens=1000)
        if answer is None:
            return None
        lower = answer.lower().strip().rstrip(".")
        if lower.startswith("yes"):
            return True
        if lower.startswith("no"):
            return False
        logger.warning("LLM gave ambiguous answer: %s", answer[:100])
        return None

    async def refine_news(self, title: str, content: str) -> Optional[str]:
        """Compress/refine a news article into a concise summary."""
        text = content[:3000] if content else title
        prompt = (
            "Summarize this news article in 2-3 concise sentences. "
            "Focus on the key facts: what happened, who is involved, and market impact. "
            "Do NOT add opinions or speculation.\n\n"
            f"Title: {title}\n\n{text}"
        )
        return await self._chat(prompt, max_tokens=500)

    async def close(self):
        await self._client.aclose()
