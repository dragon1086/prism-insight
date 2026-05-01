"""user_facts CRUD + supersede + similarity."""

import sqlite3

import numpy as np
import pytest

from tracking.memory.facts import (
    CATEGORIES,
    deactivate,
    find_similar,
    get_facts,
    get_top_by_category,
    save_fact,
    supersede,
)
from tracking.memory.schema import run_migrations


def _conn(tmp_path):
    p = str(tmp_path / "f.sqlite")
    c = sqlite3.connect(p)
    run_migrations(c)
    return c


def test_save_and_get_fact(tmp_path):
    conn = _conn(tmp_path)
    try:
        fid = save_fact(conn, user_id=1, fact="단타로 손해 본 적 많음", category="aversion", confidence=0.8)
        assert fid > 0
        rows = get_facts(conn, user_id=1)
        assert len(rows) == 1
        assert rows[0]["category"] == "aversion"
        assert rows[0]["active"] == 1
    finally:
        conn.close()


def test_get_facts_returns_active_only(tmp_path):
    conn = _conn(tmp_path)
    try:
        a = save_fact(conn, 1, "fact A", "style")
        b = save_fact(conn, 1, "fact B", "style")
        deactivate(conn, a)
        rows = get_facts(conn, 1, active=1)
        ids = {r["id"] for r in rows}
        assert b in ids
        assert a not in ids
        # active=None returns both
        rows_all = get_facts(conn, 1, active=None)
        assert {r["id"] for r in rows_all} == {a, b}
    finally:
        conn.close()


def test_supersede_flips_active_and_links(tmp_path):
    conn = _conn(tmp_path)
    try:
        a = save_fact(conn, 1, "old fact", "risk")
        b = save_fact(conn, 1, "new fact", "risk")
        ok = supersede(conn, a, b)
        assert ok is True
        rows = {r["id"]: r for r in get_facts(conn, 1, active=None)}
        assert rows[a]["active"] == 0
        assert rows[a]["superseded_by"] == b
        assert rows[b]["active"] == 1
    finally:
        conn.close()


def test_find_similar_threshold_085(tmp_path):
    conn = _conn(tmp_path)
    try:
        # Two near-identical embeddings (cos≈1.0).
        v1 = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
        v2 = np.array([0.999, 0.045, 0.0, 0.0], dtype=np.float32)
        v_far = np.array([0.0, 0.0, 1.0, 0.0], dtype=np.float32)
        a = save_fact(conn, 1, "close A", "style", embedding=v1, embedding_model="t")
        b = save_fact(conn, 1, "far B", "style", embedding=v_far, embedding_model="t")

        hits = find_similar(conn, 1, "style", v2, threshold=0.85)
        ids = [h["id"] for h in hits]
        assert a in ids
        assert b not in ids
    finally:
        conn.close()


def test_find_similar_returns_empty_when_no_embedding(tmp_path):
    conn = _conn(tmp_path)
    try:
        save_fact(conn, 1, "x", "style")  # no embedding
        out = find_similar(conn, 1, "style", embedding=None, threshold=0.5)
        assert out == []
    finally:
        conn.close()


def test_unknown_category_clamped_to_event(tmp_path):
    conn = _conn(tmp_path)
    try:
        save_fact(conn, 1, "weird", "made_up_category")
        rows = get_facts(conn, 1)
        assert rows[0]["category"] == "event"
    finally:
        conn.close()


def test_get_top_by_category_respects_min_confidence(tmp_path):
    conn = _conn(tmp_path)
    try:
        save_fact(conn, 1, "low", "style", confidence=0.4)
        save_fact(conn, 1, "high", "style", confidence=0.9)
        out = get_top_by_category(conn, 1, k=5, min_confidence=0.6)
        facts = [r["fact"] for r in out]
        assert "high" in facts
        assert "low" not in facts
    finally:
        conn.close()


def test_categories_enum_complete():
    assert set(CATEGORIES) == {"style", "risk", "holdings", "aversion", "goal", "event"}
