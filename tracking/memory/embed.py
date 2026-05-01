"""
EmbeddingProvider — pluggable text embeddings.

Three concretes:
  - VoyageEmbedder (HTTP voyage-3-lite, 512 dim)
  - OpenAIEmbedder (text-embedding-3-small truncated to 512 dim)
  - NullEmbedder (deterministic zero-mean fake vector for testing/fallback)

All return np.ndarray[float32] of dim = DEFAULT_DIM (512).
Cache provider instance; pick automatically via get_default_provider().
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from abc import ABC, abstractmethod
from typing import List, Optional

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_DIM = 512
DEFAULT_VOYAGE_MODEL = "voyage-3-lite"
DEFAULT_OPENAI_MODEL = "text-embedding-3-small"


# --------------------------------------------------------------------------- #
# Abstract base
# --------------------------------------------------------------------------- #
class EmbeddingProvider(ABC):
    name: str = "abstract"
    dim: int = DEFAULT_DIM

    @abstractmethod
    def embed(self, text: str) -> np.ndarray:
        ...

    @abstractmethod
    def embed_batch(self, texts: List[str]) -> List[np.ndarray]:
        ...


# --------------------------------------------------------------------------- #
# Helpers — float16 BLOB + cosine
# --------------------------------------------------------------------------- #
def to_blob(vec: np.ndarray) -> bytes:
    """Serialize a vector as float16 bytes for compact SQLite BLOB storage."""
    if vec is None:
        return b""
    arr = np.asarray(vec, dtype=np.float32).astype(np.float16)
    return arr.tobytes()


def from_blob(blob: bytes) -> np.ndarray:
    """Deserialize a float16 BLOB back to float32 ndarray."""
    if not blob:
        return np.zeros(DEFAULT_DIM, dtype=np.float32)
    return np.frombuffer(blob, dtype=np.float16).astype(np.float32)


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two vectors. Robust to zero vectors."""
    if a is None or b is None:
        return 0.0
    a = np.asarray(a, dtype=np.float32).reshape(-1)
    b = np.asarray(b, dtype=np.float32).reshape(-1)
    if a.size == 0 or b.size == 0 or a.size != b.size:
        return 0.0
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


# --------------------------------------------------------------------------- #
# Null embedder — deterministic, offline-safe
# --------------------------------------------------------------------------- #
class NullEmbedder(EmbeddingProvider):
    """
    Deterministic seeded zero-mean vector.

    Same input text → same vector across processes. Great for offline tests and
    as a graceful fallback when no API key is configured (degrades to BM25-only
    retrieval since cosine between two random hashes is near-zero).
    """

    name = "null"

    def __init__(self, dim: int = DEFAULT_DIM):
        self.dim = dim

    def _seed(self, text: str) -> int:
        h = hashlib.sha256(text.encode("utf-8")).digest()
        # Use first 8 bytes as a 64-bit seed.
        return int.from_bytes(h[:8], "big", signed=False) % (2 ** 32 - 1)

    def embed(self, text: str) -> np.ndarray:
        rng = np.random.default_rng(self._seed(text or ""))
        v = rng.standard_normal(self.dim).astype(np.float32)
        v -= float(v.mean())  # zero-mean
        return v

    def embed_batch(self, texts: List[str]) -> List[np.ndarray]:
        return [self.embed(t) for t in texts]


# --------------------------------------------------------------------------- #
# Voyage embedder — HTTP voyage-3-lite
# --------------------------------------------------------------------------- #
class VoyageEmbedder(EmbeddingProvider):
    name = "voyage-3-lite"

    URL = "https://api.voyageai.com/v1/embeddings"

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = DEFAULT_VOYAGE_MODEL,
        dim: int = DEFAULT_DIM,
        timeout: float = 10.0,
    ):
        self.api_key = api_key or os.environ.get("VOYAGE_API_KEY")
        self.model = model
        self.dim = dim
        self.timeout = timeout
        if not self.api_key:
            raise ValueError("VOYAGE_API_KEY not set")

    def _post(self, payload: dict) -> dict:
        # Prefer httpx (already a transitive dep); fallback to urllib.
        try:
            import httpx  # type: ignore
            with httpx.Client(timeout=self.timeout) as client:
                r = client.post(
                    self.URL,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                r.raise_for_status()
                return r.json()
        except ImportError:
            import urllib.request
            req = urllib.request.Request(
                self.URL,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))

    def _vec_from(self, data_item: dict) -> np.ndarray:
        v = np.asarray(data_item.get("embedding", []), dtype=np.float32)
        if v.size > self.dim:
            v = v[: self.dim]
        elif v.size < self.dim:
            pad = np.zeros(self.dim - v.size, dtype=np.float32)
            v = np.concatenate([v, pad])
        return v

    def embed(self, text: str) -> np.ndarray:
        payload = {"input": [text or ""], "model": self.model}
        try:
            resp = self._post(payload)
        except Exception as e:
            logger.warning("memory.embed.voyage.failed err=%s", e)
            raise
        items = resp.get("data") or []
        if not items:
            return np.zeros(self.dim, dtype=np.float32)
        return self._vec_from(items[0])

    def embed_batch(self, texts: List[str]) -> List[np.ndarray]:
        if not texts:
            return []
        payload = {"input": list(texts), "model": self.model}
        try:
            resp = self._post(payload)
        except Exception as e:
            logger.warning("memory.embed.voyage.batch_failed err=%s", e)
            raise
        items = resp.get("data") or []
        return [self._vec_from(it) for it in items]


# --------------------------------------------------------------------------- #
# OpenAI embedder — text-embedding-3-small (1536) truncated to 512
# --------------------------------------------------------------------------- #
class OpenAIEmbedder(EmbeddingProvider):
    name = "openai-3-small-512"

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = DEFAULT_OPENAI_MODEL,
        dim: int = DEFAULT_DIM,
    ):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        self.model = model
        self.dim = dim
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY not set")
        try:
            from openai import OpenAI  # type: ignore
            self._client = OpenAI(api_key=self.api_key)
        except Exception as e:
            raise RuntimeError(f"OpenAI client unavailable: {e}")

    def _truncate(self, vec) -> np.ndarray:
        v = np.asarray(vec, dtype=np.float32)
        if v.size > self.dim:
            v = v[: self.dim]
        elif v.size < self.dim:
            v = np.concatenate([v, np.zeros(self.dim - v.size, dtype=np.float32)])
        # Renormalize since truncation breaks unit-length.
        n = float(np.linalg.norm(v))
        if n > 0:
            v = v / n
        return v.astype(np.float32)

    def embed(self, text: str) -> np.ndarray:
        try:
            resp = self._client.embeddings.create(model=self.model, input=[text or ""])
            return self._truncate(resp.data[0].embedding)
        except Exception as e:
            logger.warning("memory.embed.openai.failed err=%s", e)
            raise

    def embed_batch(self, texts: List[str]) -> List[np.ndarray]:
        if not texts:
            return []
        try:
            resp = self._client.embeddings.create(model=self.model, input=list(texts))
            return [self._truncate(d.embedding) for d in resp.data]
        except Exception as e:
            logger.warning("memory.embed.openai.batch_failed err=%s", e)
            raise


# --------------------------------------------------------------------------- #
# Module-level cache + auto-pick
# --------------------------------------------------------------------------- #
_PROVIDER: Optional[EmbeddingProvider] = None


def _force_null() -> bool:
    return os.environ.get("MEMORY_FORCE_NULL_EMBEDDER", "").strip().lower() in (
        "1", "true", "yes", "on"
    )


def get_default_provider() -> EmbeddingProvider:
    """Pick Voyage > OpenAI > Null based on env keys; cache the result."""
    global _PROVIDER
    if _PROVIDER is not None:
        return _PROVIDER

    if _force_null():
        _PROVIDER = NullEmbedder()
        return _PROVIDER

    if os.environ.get("VOYAGE_API_KEY"):
        try:
            _PROVIDER = VoyageEmbedder()
            logger.info("memory.embed.provider=voyage")
            return _PROVIDER
        except Exception as e:
            logger.warning("memory.embed.voyage_init_failed err=%s", e)

    if os.environ.get("OPENAI_API_KEY"):
        try:
            _PROVIDER = OpenAIEmbedder()
            logger.info("memory.embed.provider=openai")
            return _PROVIDER
        except Exception as e:
            logger.warning("memory.embed.openai_init_failed err=%s", e)

    _PROVIDER = NullEmbedder()
    logger.info("memory.embed.provider=null")
    return _PROVIDER


def set_provider(provider: Optional[EmbeddingProvider]) -> None:
    """Test seam: explicitly install (or reset) the cached provider."""
    global _PROVIDER
    _PROVIDER = provider


def reset_provider() -> None:
    set_provider(None)
