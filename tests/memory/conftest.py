"""
Shared fixtures for Memory V2 tests.

Provides:
  - tmp_db: temp file-backed SQLite path with V2 schema applied.
  - fake_embedder: deterministic, theme-aware embedder so synthetic Korean texts
    that share theme keywords cluster.
  - mock_anthropic: a stub anthropic client returning a canned tool-use response.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List

import numpy as np
import pytest

# Make project root importable when running `pytest tests/memory`.
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tracking.memory import embed as embed_mod
from tracking.memory import retrieve as retrieve_mod
from tracking.memory.embed import EmbeddingProvider, NullEmbedder
from tracking.memory.schema import run_migrations


# ---------------------------------------------------------------- DB fixture
@pytest.fixture()
def tmp_db(tmp_path):
    db_path = str(tmp_path / "memory_v2.sqlite")
    conn = sqlite3.connect(db_path)
    try:
        run_migrations(conn)
    finally:
        conn.close()
    yield db_path


@pytest.fixture()
def db_conn(tmp_db):
    conn = sqlite3.connect(tmp_db)
    yield conn
    conn.close()


# ---------------------------------------------------------------- Embedder fixture
THEMES: Dict[str, List[str]] = {
    "단타": ["단타", "데이트레이딩", "당일", "스캘핑", "당일매도", "급등주"],
    "장기보유": ["장기보유", "장기투자", "오래", "기다리며", "버틸", "신뢰"],
    "손절": ["손절", "손해", "손실", "물려", "물타기", "마이너스"],
    "분할매수": ["분할매수", "분할", "쪼개서", "조금씩", "추가매수"],
    "배당": ["배당", "배당주", "배당금", "현금흐름", "배당락"],
}


def _theme_vector(text: str, dim: int = 512) -> np.ndarray:
    """
    Build a theme-aware embedding:
      - For each theme, count keyword hits and place a unit vector in a fixed slot.
      - Add a small hashed perturbation so identical-theme docs aren't identical.
    """
    v = np.zeros(dim, dtype=np.float32)
    theme_slot_size = dim // (len(THEMES) + 1)
    for ti, (theme, kws) in enumerate(THEMES.items()):
        weight = 0.0
        for kw in kws:
            if kw in text:
                weight += 1.0
        if weight > 0:
            start = ti * theme_slot_size
            end = start + theme_slot_size
            v[start:end] += weight

    # Hash perturbation (low magnitude)
    seed = int.from_bytes(hashlib.md5(text.encode()).digest()[:8], "big")
    rng = np.random.default_rng(seed % (2**32 - 1))
    v += 0.05 * rng.standard_normal(dim).astype(np.float32)

    n = float(np.linalg.norm(v))
    if n > 0:
        v = v / n
    return v.astype(np.float32)


@dataclass
class FakeEmbedder(EmbeddingProvider):
    name: str = "fake-theme"
    dim: int = 512

    def embed(self, text: str) -> np.ndarray:
        return _theme_vector(text or "", self.dim)

    def embed_batch(self, texts: List[str]) -> List[np.ndarray]:
        return [self.embed(t) for t in texts]


@pytest.fixture(autouse=True)
def _force_fake_embedder():
    """Force every test to use the deterministic FakeEmbedder."""
    embed_mod.set_provider(FakeEmbedder())
    retrieve_mod.reset_vector_cache()
    yield
    embed_mod.reset_provider()
    retrieve_mod.reset_vector_cache()


# ---------------------------------------------------------------- Anthropic mock
class _Block:
    def __init__(self, type: str, **kw):
        self.type = type
        for k, v in kw.items():
            setattr(self, k, v)


class _Resp:
    def __init__(self, content):
        self.content = content


@dataclass
class FakeAnthropic:
    """
    Mocked anthropic client. Default behaviour: when extracting facts, return a
    synthetic [{"fact":"단타로 손절 후회","category":"aversion","confidence":0.9}].
    For compression, return a JSON list of one-line summaries.
    """
    canned_facts: List[Dict[str, Any]] = field(default_factory=lambda: [
        {"fact": "단타 후회 경향", "category": "aversion", "confidence": 0.9},
        {"fact": "분할매수 선호", "category": "style", "confidence": 0.7},
    ])
    raise_on_call: bool = False
    call_count: int = 0

    class _Messages:
        def __init__(self, outer):
            self._outer = outer

        def create(self, **kwargs):
            self._outer.call_count += 1
            if self._outer.raise_on_call:
                raise RuntimeError("simulated haiku failure")

            # Detect the shape of request: tool-use → facts; text-only → summary array.
            tools = kwargs.get("tools")
            if tools:
                return _Resp([_Block(
                    type="tool_use",
                    name=tools[0]["name"],
                    input={"facts": list(self._outer.canned_facts)},
                )])
            # Compression path: emit a JSON array of summaries.
            user = ""
            for m in kwargs.get("messages", []):
                if m.get("role") == "user":
                    user = m.get("content", "")
                    break
            ids = []
            import json as _json
            # Find the *last* JSON object containing an "items" key.
            for start in range(len(user) - 1, -1, -1):
                if user[start] != "{":
                    continue
                try:
                    payload = _json.loads(user[start:])
                except Exception:
                    continue
                if isinstance(payload, dict) and "items" in payload:
                    ids = [it.get("id") for it in payload.get("items", []) if it.get("id")]
                    break
            arr = [{"id": i, "summary": f"요약-{i}"} for i in ids]
            return _Resp([_Block(type="text", text=_json.dumps(arr, ensure_ascii=False))])

    def __post_init__(self):
        self.messages = FakeAnthropic._Messages(self)


@pytest.fixture()
def mock_anthropic_client():
    return FakeAnthropic()
