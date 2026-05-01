"""
Task 5: Provider-mismatch guard in _load_user_vectors.

Inserts 5 rows with embedding_model='voyage-3-lite' and 3 with
embedding_model='openai-512'. Asserts that vector_search only considers
the 5 matching-model rows (the most-recently-created model wins).
"""

from __future__ import annotations

import sqlite3

import numpy as np
import pytest

import tracking.memory.embed as embed_mod
import tracking.memory.retrieve as retrieve_mod
from tracking.memory.embed import EmbeddingProvider, to_blob
from tracking.memory.retrieve import reset_vector_cache, vector_search
from tracking.memory.schema import run_migrations


# ---------------------------------------------------------------------------
# Minimal fake provider so tests don't need real API keys
# ---------------------------------------------------------------------------
class _FixedEmbedder(EmbeddingProvider):
    name: str = "fake-fixed"
    dim: int = 4

    def __init__(self, name: str = "fake-fixed", dim: int = 4):
        self.name = name
        self.dim = dim

    def embed(self, text: str) -> np.ndarray:
        return np.ones(self.dim, dtype=np.float32)

    def embed_batch(self, texts):
        return [self.embed(t) for t in texts]


@pytest.fixture(autouse=True)
def _reset_cache():
    reset_vector_cache()
    yield
    reset_vector_cache()


@pytest.fixture()
def db_conn(tmp_path):
    db_path = str(tmp_path / "mismatch.sqlite")
    conn = sqlite3.connect(db_path)
    run_migrations(conn)
    yield conn
    conn.close()


def _insert_memory(conn, user_id: int, embedding: np.ndarray, model: str, ts: str) -> int:
    """Insert a minimal user_memories row with pre-computed embedding."""
    blob = to_blob(embedding)
    cur = conn.execute(
        """
        INSERT INTO user_memories
            (user_id, memory_type, content, market_type, importance_score,
             compression_layer, created_at, last_accessed_at,
             embedding, embedding_model, fact_extracted)
        VALUES (?, 'journal', '{"text":"x"}', 'kr', 0.5, 1, ?, ?, ?, ?, 0)
        """,
        (user_id, ts, ts, blob, model),
    )
    conn.commit()
    return int(cur.lastrowid)


def test_mixed_providers_only_uses_most_recent_model(db_conn, caplog):
    """
    5 rows with 'voyage-3-lite' (newer ts) + 3 rows with 'openai-512' (older ts).
    vector_search must return only ids from the 5 voyage rows.
    """
    import logging

    user_id = 42
    dim = 4

    voyage_vec = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
    openai_vec = np.array([0.0, 1.0, 0.0, 0.0], dtype=np.float32)

    # Insert older openai rows first.
    openai_ids = set()
    for i in range(3):
        ts = f"2024-01-0{i+1}T00:00:00"
        rid = _insert_memory(db_conn, user_id, openai_vec, "openai-512", ts)
        openai_ids.add(rid)

    # Insert newer voyage rows.
    voyage_ids = set()
    for i in range(5):
        ts = f"2025-06-0{i+1}T00:00:00"
        rid = _insert_memory(db_conn, user_id, voyage_vec, "voyage-3-lite", ts)
        voyage_ids.add(rid)

    # Query with a vector aligned to voyage embeddings.
    query_vec = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)

    with caplog.at_level(logging.WARNING, logger="tracking.memory.retrieve"):
        results = vector_search(db_conn, user_id, query_vec, k=10)

    result_ids = {r["id"] for r in results}

    # Only voyage rows should appear.
    assert result_ids <= voyage_ids, (
        f"Got ids outside voyage set: {result_ids - voyage_ids}"
    )
    assert not (result_ids & openai_ids), (
        f"OpenAI rows leaked into results: {result_ids & openai_ids}"
    )

    # Warning must have been emitted once.
    mixed_warnings = [
        r for r in caplog.records
        if "memory.embed.mixed_providers" in r.message
    ]
    assert len(mixed_warnings) >= 1, "Expected mixed_providers warning to be logged"
    assert "openai-512" in mixed_warnings[0].message
    assert "voyage-3-lite" in mixed_warnings[0].message


def test_single_provider_no_warning(db_conn, caplog):
    """No warning when all rows share the same embedding_model."""
    import logging

    user_id = 99
    vec = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)

    for i in range(4):
        ts = f"2025-03-0{i+1}T00:00:00"
        _insert_memory(db_conn, user_id, vec, "voyage-3-lite", ts)

    with caplog.at_level(logging.WARNING, logger="tracking.memory.retrieve"):
        results = vector_search(db_conn, user_id, vec, k=10)

    assert len(results) == 4
    mixed_warnings = [
        r for r in caplog.records
        if "memory.embed.mixed_providers" in r.message
    ]
    assert not mixed_warnings, "Unexpected mixed_providers warning with single model"
