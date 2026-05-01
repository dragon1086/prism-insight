"""compress_old_memories: Haiku failure must not raise; cron-safe truncate fallback."""

import asyncio
import sqlite3
from datetime import datetime, timedelta

import pytest

from tracking.memory import compress as compress_mod
from tracking.memory.schema import run_migrations


def _seed_old_journal(conn, days_old=10, layer=1):
    cur = conn.execute(
        """
        INSERT INTO user_memories (
            user_id, memory_type, content, ticker, ticker_name,
            compression_layer, fact_extracted, created_at
        ) VALUES (?, 'journal', ?, ?, ?, ?, 1, ?)
        """,
        (
            1,
            '{"text":"이건 오래된 메모리. 단타 후회 손절 어려움 등등의 내용을 길게 길게 적은 것."}',
            "005930",
            "삼성전자",
            layer,
            (datetime.now() - timedelta(days=days_old)).isoformat(),
        ),
    )
    conn.commit()
    return cur.lastrowid


def test_layer1_to_2_falls_back_to_truncate_when_haiku_raises(tmp_path, mock_anthropic_client):
    db = str(tmp_path / "c.sqlite")
    conn = sqlite3.connect(db)
    try:
        run_migrations(conn)
        mid = _seed_old_journal(conn, days_old=10, layer=1)

        mock_anthropic_client.raise_on_call = True
        n = asyncio.run(
            compress_mod.compress_layer_1_to_2(
                conn, anthropic_client=mock_anthropic_client, layer1_days=7
            )
        )
        assert n == 1
        row = conn.execute(
            "SELECT compression_layer, summary FROM user_memories WHERE id=?", (mid,)
        ).fetchone()
        assert row[0] == 2
        assert row[1] is not None
        assert len(row[1]) > 0
    finally:
        conn.close()


def test_layer1_to_2_uses_haiku_summary_when_available(tmp_path, mock_anthropic_client):
    db = str(tmp_path / "c2.sqlite")
    conn = sqlite3.connect(db)
    try:
        run_migrations(conn)
        mid = _seed_old_journal(conn, days_old=10, layer=1)
        n = asyncio.run(
            compress_mod.compress_layer_1_to_2(
                conn, anthropic_client=mock_anthropic_client, layer1_days=7
            )
        )
        assert n == 1
        row = conn.execute(
            "SELECT summary FROM user_memories WHERE id=?", (mid,)
        ).fetchone()
        assert row[0] == f"요약-{mid}"
    finally:
        conn.close()


def test_compress_all_sync_never_raises_with_no_client(tmp_path):
    db = str(tmp_path / "c3.sqlite")
    conn = sqlite3.connect(db)
    try:
        run_migrations(conn)
        _seed_old_journal(conn, days_old=10, layer=1)
        out = compress_mod.compress_all_sync(conn, anthropic_client=None)
        assert isinstance(out, dict)
        assert "layer2_count" in out and "layer3_count" in out
    finally:
        conn.close()


def test_layer1_to_2_skips_when_fact_not_extracted(tmp_path, mock_anthropic_client):
    db = str(tmp_path / "c4.sqlite")
    conn = sqlite3.connect(db)
    try:
        run_migrations(conn)
        # Force fact_extracted=0 manually.
        conn.execute(
            """
            INSERT INTO user_memories (
                user_id, memory_type, content, ticker, ticker_name,
                compression_layer, fact_extracted, created_at
            ) VALUES (1, 'journal', ?, '005930', '삼성전자', 1, 0, ?)
            """,
            ('{"text":"old"}', (datetime.now() - timedelta(days=10)).isoformat()),
        )
        conn.commit()
        n = asyncio.run(
            compress_mod.compress_layer_1_to_2(conn, anthropic_client=mock_anthropic_client)
        )
        assert n == 0  # not yet extracted → skipped
    finally:
        conn.close()
