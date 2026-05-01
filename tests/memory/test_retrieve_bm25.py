"""BM25 / FTS5 retrieval tests."""

import sqlite3
from datetime import datetime

from tracking.memory.retrieve import bm25_search
from tracking.memory.schema import fts_insert, run_migrations


def _seed(conn, user_id, text, ticker, ticker_name):
    cur = conn.execute(
        """
        INSERT INTO user_memories (
            user_id, memory_type, content, ticker, ticker_name, created_at
        ) VALUES (?, 'journal', ?, ?, ?, ?)
        """,
        (user_id, '{"text":' + repr(text).replace("'", '"') + "}", ticker, ticker_name,
         datetime.now().isoformat()),
    )
    conn.commit()
    mid = cur.lastrowid
    fts_insert(conn, mid, text, ticker or "", ticker_name or "")
    return mid


def test_bm25_finds_ticker_code(tmp_path):
    db = str(tmp_path / "bm.sqlite")
    conn = sqlite3.connect(db)
    try:
        run_migrations(conn)
        a = _seed(conn, 1, "삼성전자 단타 후회", "005930", "삼성전자")
        b = _seed(conn, 1, "테슬라 장기보유", "TSLA", "Tesla")
        results = bm25_search(conn, user_id=1, query="005930", k=5)
        ids = [r["id"] for r in results]
        assert a in ids
        assert b not in ids
    finally:
        conn.close()


def test_bm25_finds_korean_ticker_name(tmp_path):
    db = str(tmp_path / "bm.sqlite")
    conn = sqlite3.connect(db)
    try:
        run_migrations(conn)
        a = _seed(conn, 1, "삼성전자 매수했음", "005930", "삼성전자")
        b = _seed(conn, 1, "현대차 분할매수", "005380", "현대차")
        results = bm25_search(conn, user_id=1, query="삼성전자", k=5)
        ids = [r["id"] for r in results]
        assert a in ids
        assert b not in ids
    finally:
        conn.close()


def test_bm25_empty_query_returns_empty(tmp_path):
    db = str(tmp_path / "bm.sqlite")
    conn = sqlite3.connect(db)
    try:
        run_migrations(conn)
        _seed(conn, 1, "anything", None, None)
        assert bm25_search(conn, 1, "", 5) == []
        assert bm25_search(conn, 1, "    ", 5) == []
    finally:
        conn.close()


def test_bm25_isolates_users(tmp_path):
    db = str(tmp_path / "bm.sqlite")
    conn = sqlite3.connect(db)
    try:
        run_migrations(conn)
        _seed(conn, 1, "삼성 매수", "005930", "삼성전자")
        b = _seed(conn, 2, "삼성 매도", "005930", "삼성전자")
        results = bm25_search(conn, user_id=2, query="삼성", k=5)
        ids = [r["id"] for r in results]
        assert ids == [b]
    finally:
        conn.close()
