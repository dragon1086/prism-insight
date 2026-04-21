"""
embedding.py — OpenAI 임베딩 생성 + SQLite BLOB 직렬화.

text-embedding-3-small (1536 dim float32) 사용.
BLOB은 numpy float32 배열의 바이트 표현.
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 1536
_MAX_INPUT_CHARS = 8000


def _get_openai_client(api_key: str):
    """Lazy import to avoid cost when module is loaded but not used."""
    import openai
    return openai.AsyncOpenAI(api_key=api_key)


async def embed_text(text: str, api_key: str) -> Optional[bytes]:
    """
    Embed a single text into a 1536-dim float32 BLOB.
    Returns None on empty input or failure (caller stores NULL).
    """
    if not text or not text.strip() or not api_key:
        return None
    try:
        client = _get_openai_client(api_key)
        resp = await client.embeddings.create(
            model=EMBEDDING_MODEL,
            input=text[:_MAX_INPUT_CHARS],
        )
        vec = np.asarray(resp.data[0].embedding, dtype=np.float32)
        if vec.shape != (EMBEDDING_DIM,):
            logger.warning(f"Unexpected embedding shape {vec.shape}")
            return None
        return vec.tobytes()
    except Exception as e:
        logger.warning(f"embed_text failed: {e}")
        return None


def decode_embedding(blob: Optional[bytes]) -> Optional[np.ndarray]:
    if not blob:
        return None
    try:
        vec = np.frombuffer(blob, dtype=np.float32)
        if vec.shape != (EMBEDDING_DIM,):
            return None
        return vec
    except Exception:
        return None


def cosine(a: bytes, b: bytes) -> float:
    va = decode_embedding(a)
    vb = decode_embedding(b)
    if va is None or vb is None:
        return 0.0
    na = float(np.linalg.norm(va))
    nb = float(np.linalg.norm(vb))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(va, vb) / (na * nb))
