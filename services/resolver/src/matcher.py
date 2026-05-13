"""Market matcher: finds equivalent markets between Kalshi and Polymarket.

Matching pipeline:
  1. Rule-based pre-filter (keyword overlap, date proximity)
  2. Embedding similarity via Qdrant

Outputs MatchedPair objects published to Redis.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
from datetime import datetime, timezone
from typing import List, Optional, Sequence, Tuple

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    OptimizersConfigDiff,
    PointIdsList,
    PointStruct,
    ScoredPoint,
    VectorParams,
)

from shared.models import MatchedPair

logger = logging.getLogger(__name__)

COLLECTION = "markets"
QDRANT_TARGET_SEGMENTS = int(os.getenv("QDRANT_TARGET_SEGMENTS", "1"))
QDRANT_INDEXING_THRESHOLD_KB = int(os.getenv("QDRANT_INDEXING_THRESHOLD_KB", "4096"))


def _pair_id(kalshi_id: str, poly_id: str) -> str:
    raw = f"{kalshi_id}:{poly_id}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _normalize_text(text: str) -> str:
    """Lowercase, remove punctuation noise."""
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _keyword_overlap(a: str, b: str) -> float:
    """Jaccard overlap of significant words (len > 3)."""
    sa = {w for w in _normalize_text(a).split() if len(w) > 3}
    sb = {w for w in _normalize_text(b).split() if len(w) > 3}
    if not sa and not sb:
        return 0.0
    inter = sa & sb
    union = sa | sb
    return len(inter) / len(union) if union else 0.0


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


def _normalize_name(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _extract_vs_entities(text: str) -> List[str]:
    match = re.search(
        r"(.+?)\s+(?:vs\.?|versus|v\.?|at|@)\s+(.+)",
        text,
        re.IGNORECASE,
    )
    if not match:
        return []

    left = match.group(1)
    right = match.group(2)
    left = re.sub(r"^will\s+", "", left, flags=re.IGNORECASE)
    left = re.sub(
        r".*\bwin(?:\s+set\s+\d+)?(?:\s+in\s+the)?\s+",
        "",
        left,
        flags=re.IGNORECASE,
    )
    right = re.split(r"\s*[:?]", right, maxsplit=1)[0]
    right = re.split(
        r"\s+(?:winner|match|game|quarterfinal|semifinal|round|end|set)\b",
        right,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]

    left = left.strip()
    right = right.strip()
    if not left or not right:
        return []
    return [left, right]


def _shared_entity_count(a: Sequence[str], b: Sequence[str]) -> int:
    aset = {_normalize_name(part) for part in a if part}
    bset = {_normalize_name(part) for part in b if part}
    return len(aset & bset)


def _title_contains_all_entities(title: str, entities: Sequence[str]) -> bool:
    normalized_title = f" {_normalize_name(title)} "
    for entity in entities:
        normalized_entity = _normalize_name(entity)
        if not normalized_entity:
            continue
        if f" {normalized_entity} " not in normalized_title:
            return False
    return True


def _looks_like_prop_market(text: str) -> bool:
    lowered = text.lower()
    markers = (
        ": o/u",
        "both teams to score",
        "first half",
        "second half",
        "to score",
    )
    return any(marker in lowered for marker in markers)


def _looks_like_draw_market(text: str) -> bool:
    lowered = text.lower()
    return "draw" in lowered or "tie" in lowered


_OUTCOME_STOPWORDS = {
    "at",
    "least",
    "most",
    "more",
    "less",
    "than",
    "between",
    "above",
    "below",
    "over",
    "under",
    "exactly",
    "or",
    "to",
    "of",
    "the",
    "a",
    "an",
    "and",
    "up",
    "down",
    "reach",
    "higher",
    "lower",
    "will",
    "what",
    "who",
    "outcome",
    "during",
    "next",
    "new",
    "have",
    "has",
    "had",
    "being",
    "been",
    "before",
    "after",
    "into",
    "onto",
    "from",
    "with",
    "within",
    "end",
}


def _normalize_numeric_token(value: str) -> str:
    normalized = value.strip()
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return normalized


def _extract_numeric_tokens(text: str) -> List[str]:
    return [_normalize_numeric_token(token) for token in re.findall(r"\d+(?:\.\d+)?", text)]


def _extract_outcome_words(text: str) -> List[str]:
    words = []
    for word in _normalize_text(text).split():
        if len(word) <= 2:
            continue
        if word in _OUTCOME_STOPWORDS:
            continue
        if word.isdigit():
            continue
        words.append(word)
    return words


def _extract_explicit_date(text: str) -> Optional[Tuple[str, int, Optional[int]]]:
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


def _titles_have_compatible_explicit_dates(a: str, b: str) -> bool:
    date_a = _extract_explicit_date(a)
    date_b = _extract_explicit_date(b)
    if not date_a or not date_b:
        return True
    if date_a[:2] != date_b[:2]:
        return False
    year_a = date_a[2]
    year_b = date_b[2]
    if year_a is not None and year_b is not None and year_a != year_b:
        return False
    return True


def _extract_district_token(text: str) -> Optional[str]:
    match = re.search(r"\b([a-z]{2,3})\s*-\s*0?(\d{1,2})\b", text, re.IGNORECASE)
    if not match:
        return None
    return f"{match.group(1).lower()}-{int(match.group(2))}"


def _extract_group_token(text: str) -> Optional[str]:
    match = re.search(r"\bgroup\s+([a-z0-9]+)\b", text, re.IGNORECASE)
    if not match:
        return None
    return match.group(1).lower()


def _extract_rank_token(text: str) -> Optional[str]:
    lowered = text.lower()
    if re.search(r"\bmost seats\b", lowered):
        return "rank:1"

    for pattern in (
        r"\btop\s+(\d+)\b",
        r"\bfinish(?:ing)?\s+in\s+(?:the\s+)?top\s+(\d+)\b",
        r"\bfinish(?:ing)?\s+(\d+)(?:st|nd|rd|th)\b",
        r"\b(\d+)(?:st|nd|rd|th)\s+place\b",
    ):
        match = re.search(pattern, lowered)
        if match:
            return f"rank:{int(match.group(1))}"
    return None


def _extract_division_token(text: str) -> Optional[str]:
    lowered = _normalize_text(text)
    patterns = (
        r"\b(atlantic division|metropolitan division|pacific division|central division)\b",
        r"\b((?:nl|al) (?:east|west|central))(?: division| title)?\b",
    )
    for pattern in patterns:
        match = re.search(pattern, lowered)
        if match:
            return match.group(1)
    return None


def _extract_round_token(text: str) -> Optional[str]:
    lowered = _normalize_text(text)
    patterns = (
        ("first_round", r"\b(?:1st|first) round\b"),
        ("second_round", r"\b(?:2nd|second) round\b"),
        ("third_round", r"\b(?:3rd|third) round\b"),
        ("final_four", r"\bfinal four\b"),
        ("elite_eight", r"\belite eight\b"),
        ("conference_finals", r"\b(?:eastern|western) conference finals?\b"),
    )
    for value, pattern in patterns:
        if re.search(pattern, lowered):
            return value
    return None


def _extract_artifact_token(text: str) -> Optional[str]:
    lowered = _normalize_text(text)
    if "album" in lowered:
        return "album"
    if "song" in lowered:
        return "song"
    if "top artist" in lowered or re.search(r"\bartist\b", lowered):
        return "artist"
    return None


def _has_relegation_marker(text: str) -> bool:
    return bool(re.search(r"\brelegat", text, re.IGNORECASE))


def _has_single_match_day_marker(text: str) -> bool:
    lowered = text.lower()
    return bool(re.search(r"\bwin on \d{4}-\d{2}-\d{2}\b", lowered))


def _has_explicit_deadline(text: str) -> bool:
    lowered = text.lower()
    return bool(_extract_explicit_date(text) and re.search(r"\b(before|by|after|prior to|on)\b", lowered))


def _has_year_only_window(text: str) -> bool:
    return bool(re.search(r"\bin\s+20\d{2}\b", text, re.IGNORECASE))


def _titles_have_compatible_structure(a: str, b: str) -> bool:
    district_a = _extract_district_token(a)
    district_b = _extract_district_token(b)
    if district_a and district_b and district_a != district_b:
        return False

    group_a = _extract_group_token(a)
    group_b = _extract_group_token(b)
    if group_a or group_b:
        if group_a != group_b:
            return False

    rank_a = _extract_rank_token(a)
    rank_b = _extract_rank_token(b)
    if rank_a and rank_b and rank_a != rank_b:
        return False
    if (rank_a or rank_b) and rank_a != rank_b:
        other = b if rank_a else a
        if _has_relegation_marker(other) or re.search(r"\bwin\b", other, re.IGNORECASE):
            return False

    division_a = _extract_division_token(a)
    division_b = _extract_division_token(b)
    if division_a and division_b and division_a != division_b:
        return False
    if division_a and not division_b and re.search(r"\bcup\b|\bchampionship\b", b, re.IGNORECASE):
        return False
    if division_b and not division_a and re.search(r"\bcup\b|\bchampionship\b", a, re.IGNORECASE):
        return False

    round_a = _extract_round_token(a)
    round_b = _extract_round_token(b)
    if round_a or round_b:
        if round_a != round_b:
            return False

    artifact_a = _extract_artifact_token(a)
    artifact_b = _extract_artifact_token(b)
    if artifact_a and artifact_b and artifact_a != artifact_b:
        return False

    if _has_relegation_marker(a) != _has_relegation_marker(b):
        return False

    if _has_single_match_day_marker(a) != _has_single_match_day_marker(b):
        return False

    if _has_explicit_deadline(a) != _has_explicit_deadline(b):
        if _has_year_only_window(a) or _has_year_only_window(b):
            return False

    return True


def _word_matches_candidate(word: str, candidate_words: Sequence[str]) -> bool:
    for candidate_word in candidate_words:
        if candidate_word == word:
            return True
        if candidate_word.startswith(word) or word.startswith(candidate_word):
            return True
    return False


def _candidate_matches_yes_sub_title(yes_sub_title: str, candidate_title: str) -> bool:
    subtitle = str(yes_sub_title or "").strip()
    if not subtitle:
        return True

    candidate_numbers = set(_extract_numeric_tokens(candidate_title))
    subtitle_numbers = _extract_numeric_tokens(subtitle)
    if subtitle_numbers and not set(subtitle_numbers).issubset(candidate_numbers):
        return False

    subtitle_words = _extract_outcome_words(subtitle)
    if not subtitle_words:
        return True

    candidate_words = _extract_outcome_words(candidate_title)
    return all(_word_matches_candidate(word, candidate_words) for word in subtitle_words)


def _extract_title_core_tokens(title: str, yes_sub_title: str) -> List[str]:
    outcome_words = set(_extract_outcome_words(yes_sub_title))
    tokens: List[str] = []
    for word in _extract_outcome_words(title):
        if word in outcome_words:
            continue
        tokens.append(word)
    return tokens


def _candidate_matches_title_core(title: str, yes_sub_title: str, candidate_title: str) -> bool:
    core_tokens = _extract_title_core_tokens(title, yes_sub_title)
    if not core_tokens:
        return True
    candidate_words = _extract_outcome_words(candidate_title)
    return all(_word_matches_candidate(word, candidate_words) for word in core_tokens)


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


def build_match_text(exchange: str, title: str, meta: Optional[dict] = None) -> str:
    """Build outcome-aware text used for embeddings and lexical checks."""
    normalized_title = str(title or "").strip()
    if exchange != "kalshi" or not normalized_title:
        return normalized_title

    meta = meta or {}
    yes_sub_title = str(meta.get("yes_sub_title") or "").strip()
    if not yes_sub_title:
        return normalized_title

    synthesized = _synthesize_kalshi_candidate_question(normalized_title, yes_sub_title)
    if synthesized:
        return synthesized

    lower_title = normalized_title.lower()
    if lower_title.startswith("will "):
        return normalized_title

    stripped = normalized_title.rstrip("?").strip()
    if stripped.lower().endswith(" winner"):
        event_name = stripped[: -len(" winner")].strip()
        if yes_sub_title.lower() == "tie":
            return f"Will {event_name} end in a tie?"
        return f"Will {yes_sub_title} win {event_name}?"

    return f"{normalized_title} Outcome: {yes_sub_title}"


def _kalshi_candidate_compatible(
    match_title: str,
    meta: Optional[dict],
    candidate_title: str,
    payload: dict,
    keyword_score: float,
) -> bool:
    meta = meta or {}
    if _looks_like_prop_market(candidate_title):
        return False

    query_entities = _extract_vs_entities(match_title)
    candidate_entities = _extract_vs_entities(candidate_title)
    yes_sub_title = str(meta.get("yes_sub_title") or "").strip()
    normalized_yes = _normalize_name(yes_sub_title)

    query_ts = _parse_timestamp(meta.get("end_date"))
    candidate_ts = _parse_timestamp(payload.get("end_date"))
    day_delta: Optional[float] = None
    if query_ts is not None and candidate_ts is not None:
        day_delta = abs(query_ts - candidate_ts) / 86400.0
    if not _titles_have_compatible_explicit_dates(match_title, candidate_title):
        return False

    if query_entities:
        if candidate_entities:
            if _shared_entity_count(query_entities, candidate_entities) < 2:
                return False
            if yes_sub_title.lower() == "tie":
                return _looks_like_draw_market(candidate_title)
            if _looks_like_draw_market(candidate_title):
                return False
            return True

        if not _title_contains_all_entities(candidate_title, query_entities):
            return False
        if yes_sub_title.lower() == "tie":
            return _looks_like_draw_market(candidate_title)
        if _looks_like_draw_market(candidate_title):
            return False
        if not normalized_yes or normalized_yes not in _normalize_name(candidate_title):
            return False
        if keyword_score < 0.5:
            return False
        if day_delta is not None and day_delta > 3:
            return False
            return True

    if not _titles_have_compatible_structure(match_title, candidate_title):
        return False

    if normalized_yes:
        if not _candidate_matches_yes_sub_title(yes_sub_title, candidate_title):
            return False
        if not _candidate_matches_title_core(match_title, yes_sub_title, candidate_title):
            return False
        if keyword_score < 0.5:
            return False
        if day_delta is not None and day_delta > 7:
            return False

    return True


class MarketMatcher:
    def __init__(
        self,
        qdrant: AsyncQdrantClient,
        embedder,
        threshold: float = 0.85,
        collection: str = COLLECTION,
    ) -> None:
        self._qdrant = qdrant
        self._embedder = embedder
        self._threshold = threshold
        self._collection = collection
        # Detect vector dimension from the actual loaded model
        self._vector_dim: int = len(embedder.embed_one("test"))

    @staticmethod
    def point_id_for(exchange: str, market_id: str) -> int:
        # Deterministic 64-bit ID from market identity
        return int(hashlib.sha256(f"{exchange}:{market_id}".encode()).hexdigest()[:16], 16)

    def embed_title(self, title: str) -> List[float]:
        return self._embedder.embed_one(title)

    def embed_titles(self, titles: Sequence[str]) -> List[List[float]]:
        return self._embedder.embed(list(titles))

    # ── Qdrant collection setup ───────────────────────────────────────────────

    async def ensure_collection(self) -> None:
        optimizers = OptimizersConfigDiff(
            default_segment_number=QDRANT_TARGET_SEGMENTS,
            indexing_threshold=QDRANT_INDEXING_THRESHOLD_KB,
        )
        collections = await self._qdrant.get_collections()
        names = [c.name for c in collections.collections]
        if self._collection not in names:
            await self._qdrant.create_collection(
                collection_name=self._collection,
                vectors_config=VectorParams(size=self._vector_dim, distance=Distance.COSINE),
                optimizers_config=optimizers,
            )
            logger.info("Created Qdrant collection '%s' (dim=%d)", self._collection, self._vector_dim)

        await self._qdrant.update_collection(
            collection_name=self._collection,
            optimizers_config=optimizers,
        )
        logger.info(
            "Applied Qdrant optimizer config to '%s' (segments=%d, indexing_threshold=%dKB)",
            self._collection,
            QDRANT_TARGET_SEGMENTS,
            QDRANT_INDEXING_THRESHOLD_KB,
        )

    # ── Indexing ─────────────────────────────────────────────────────────────

    async def index_market(
        self,
        exchange: str,
        market_id: str,
        title: str,
        meta: dict,
        vector: Optional[List[float]] = None,
    ) -> None:
        """Embed a market title and upsert into Qdrant."""
        point_vector = vector or self._embedder.embed_one(title)
        await self.index_markets(
            [
                {
                    "exchange": exchange,
                    "market_id": market_id,
                    "title": title,
                    "meta": meta,
                    "vector": point_vector,
                }
            ]
        )

    async def index_markets(self, markets: Sequence[dict]) -> None:
        """Batch upsert market vectors into Qdrant."""
        if not markets:
            return

        points = []
        for market in markets:
            exchange = str(market["exchange"])
            market_id = str(market["market_id"])
            title = str(market["title"])
            meta = dict(market.get("meta") or {})
            vector = list(market["vector"])
            points.append(
                PointStruct(
                    id=self.point_id_for(exchange, market_id),
                    vector=vector,
                    payload={
                        "exchange": exchange,
                        "market_id": market_id,
                        "title": title,
                        **{k: v for k, v in meta.items() if isinstance(v, (str, int, float, bool))},
                    },
                )
            )

        await self._qdrant.upsert(
            collection_name=self._collection,
            points=points,
        )

    async def remove_market(self, exchange: str, market_id: str) -> None:
        point_id = self.point_id_for(exchange, market_id)
        await self._qdrant.delete(
            collection_name=self._collection,
            points_selector=PointIdsList(points=[point_id]),
            wait=False,
        )

    async def remove_markets(self, markets: Sequence[Tuple[str, str]]) -> None:
        point_ids = [self.point_id_for(exchange, market_id) for exchange, market_id in markets]
        if not point_ids:
            return
        await self._qdrant.delete(
            collection_name=self._collection,
            points_selector=PointIdsList(points=point_ids),
            wait=False,
        )

    # ── Matching ──────────────────────────────────────────────────────────────

    async def find_matches_for(
        self,
        exchange: str,
        market_id: str,
        title: str,
        meta: Optional[dict] = None,
        top_k: int = 5,
        query_vector: Optional[List[float]] = None,
    ) -> List[MatchedPair]:
        """Find matching markets on the *other* exchange."""
        other_exchange = "polymarket" if exchange == "kalshi" else "kalshi"
        match_title = build_match_text(exchange, title, meta)
        resolved_query_vector = query_vector or self._embedder.embed_one(match_title)

        query_filter = Filter(
            must=[FieldCondition(key="exchange", match=MatchValue(value=other_exchange))]
        )
        if hasattr(self._qdrant, "search"):
            results: List[ScoredPoint] = await self._qdrant.search(
                collection_name=self._collection,
                query_vector=resolved_query_vector,
                query_filter=query_filter,
                limit=top_k,
                with_payload=True,
            )
        else:
            response = await self._qdrant.query_points(
                collection_name=self._collection,
                query=resolved_query_vector,
                query_filter=query_filter,
                limit=top_k,
                with_payload=True,
            )
            results = response.points

        pairs: List[MatchedPair] = []
        for hit in results:
            if hit.score < self._threshold:
                continue
            payload = hit.payload or {}
            if exchange == "kalshi" and (
                not payload.get("yes_token_id") or not payload.get("no_token_id")
            ):
                # Downstream strategy/executors assume Polymarket binary YES/NO
                # markets. Skip multi-outcome conditions that only expose
                # outcome-specific token IDs.
                continue
            other_id = payload.get("market_id", "")
            other_title = payload.get("title", "")
            other_match_title = payload.get("match_title", other_title)

            # Secondary rule-based check to reduce false positives
            keyword_score = _keyword_overlap(match_title, other_match_title)
            if keyword_score < 0.15:
                logger.debug(
                    "Rejected match (low keyword overlap=%.2f): '%s' vs '%s'",
                    keyword_score,
                    match_title[:60],
                    str(other_match_title)[:60],
                )
                continue
            if exchange == "kalshi" and not _kalshi_candidate_compatible(
                match_title=match_title,
                meta=meta,
                candidate_title=str(other_match_title),
                payload=payload,
                keyword_score=keyword_score,
            ):
                continue

            if exchange == "kalshi":
                kalshi_id, poly_id = market_id, other_id
            else:
                kalshi_id, poly_id = other_id, market_id

            pairs.append(
                MatchedPair(
                    pair_id=_pair_id(kalshi_id, poly_id),
                    kalshi_market_id=kalshi_id,
                    polymarket_market_id=poly_id,
                    similarity_score=round(hit.score, 4),
                )
            )
            logger.info(
                "Match %.3f | Kalshi: %s | Poly: %s",
                hit.score,
                kalshi_id,
                poly_id,
            )

        return pairs
