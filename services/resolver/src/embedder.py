"""Text embedder wrapping sentence-transformers."""
from __future__ import annotations

from collections import OrderedDict
import json
import logging
import os
from typing import Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import numpy as np
from sentence_transformers import SentenceTransformer

try:
    import torch
except Exception:  # pragma: no cover - torch is optional at import-time
    torch = None

logger = logging.getLogger(__name__)


class Embedder:
    def __init__(
        self,
        model_name: str = "all-MiniLM-L6-v2",
        device: str = "auto",
        cache_size: int = 2048,
    ) -> None:
        selected_device = self._select_device(device)
        self._cache_size = max(0, cache_size)
        self._cache: OrderedDict[str, List[float]] = OrderedDict()
        self._device = selected_device
        self._model_name = model_name
        self._model: Optional[SentenceTransformer] = None
        self._gateway_url = os.getenv("EMBEDDING_GATEWAY_URL", "").rstrip("/")
        self._gateway_timeout = float(os.getenv("EMBEDDING_GATEWAY_TIMEOUT", "10"))
        if self._gateway_url:
            logger.info("Embedding gateway enabled: %s", self._gateway_url)
        else:
            self._ensure_local_model(device)

    @property
    def device(self) -> str:
        if self._gateway_url:
            return f"gateway:{self._gateway_url}"
        return self._device

    def _ensure_local_model(self, requested: str) -> None:
        if self._model is not None:
            return
        logger.info("Loading embedding model: %s", self._model_name)
        self._model = SentenceTransformer(self._model_name, device=self._device)
        logger.info("Embedding model loaded on device=%s (requested=%s)", self._device, requested)

    def _select_device(self, requested: str) -> str:
        req = (requested or "auto").strip().lower()
        cuda_available = bool(torch and torch.cuda.is_available())

        if req == "cuda":
            if cuda_available:
                return "cuda"
            logger.warning("EMBED_DEVICE=cuda requested but CUDA unavailable; falling back to CPU")
            return "cpu"
        if req == "cpu":
            return "cpu"
        if req != "auto":
            logger.warning("Unknown EMBED_DEVICE=%s; using auto selection", requested)
        return "cuda" if cuda_available else "cpu"

    def _cache_get(self, text: str) -> Optional[List[float]]:
        if self._cache_size <= 0:
            return None
        vector = self._cache.get(text)
        if vector is None:
            return None
        self._cache.move_to_end(text)
        return vector

    def _cache_set(self, text: str, vector: List[float]) -> None:
        if self._cache_size <= 0:
            return
        self._cache[text] = vector
        self._cache.move_to_end(text)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)

    def _gateway_request(self, path: str, payload: Optional[dict] = None) -> Optional[dict]:
        if not self._gateway_url:
            return None
        body = None
        headers = {}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(f"{self._gateway_url}{path}", data=body, headers=headers)
        try:
            with urlopen(request, timeout=self._gateway_timeout) as response:
                return json.load(response)
        except (HTTPError, URLError, OSError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("Embedding gateway request failed (%s): %s", path, exc)
            return None

    def _embed_via_gateway(self, texts: List[str]) -> Optional[List[List[float]]]:
        payload = self._gateway_request("/embed", {"texts": texts})
        if not payload:
            return None
        vectors = payload.get("vectors")
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            logger.warning("Embedding gateway returned unexpected vector payload")
            return None
        return vectors

    def _embed_locally(self, texts: List[str]) -> List[List[float]]:
        self._ensure_local_model(self._device)
        assert self._model is not None
        encoded = self._model.encode(
            texts,
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        return encoded.tolist()

    def embed(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []

        vectors: List[Optional[List[float]]] = [None] * len(texts)
        missing_texts: List[str] = []
        missing_indices: Dict[str, List[int]] = {}

        for idx, text in enumerate(texts):
            cached = self._cache_get(text)
            if cached is not None:
                vectors[idx] = cached
                continue
            if text not in missing_indices:
                missing_indices[text] = []
                missing_texts.append(text)
            missing_indices[text].append(idx)

        if missing_texts:
            encoded_list = self._embed_via_gateway(missing_texts)
            if encoded_list is None:
                encoded_list = self._embed_locally(missing_texts)
            for text, vector in zip(missing_texts, encoded_list):
                self._cache_set(text, vector)
                for idx in missing_indices[text]:
                    vectors[idx] = vector

        out: List[List[float]] = []
        for vector in vectors:
            if vector is None:
                raise RuntimeError("embedding pipeline produced an empty vector slot")
            out.append(vector)
        return out

    def embed_one(self, text: str) -> List[float]:
        return self.embed([text])[0]

    @staticmethod
    def cosine_similarity(a: List[float], b: List[float]) -> float:
        va = np.array(a)
        vb = np.array(b)
        denom = np.linalg.norm(va) * np.linalg.norm(vb)
        if denom < 1e-9:
            return 0.0
        return float(np.dot(va, vb) / denom)
