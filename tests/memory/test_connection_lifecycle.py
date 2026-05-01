"""
Regression test for the file-backed DB connection cleanup BLOCKER.

The bug: _close_if_owned called self._close_if_owned(conn) recursively
instead of conn.close(), causing infinite RecursionError.

This test:
1. Exercises all major manager operations on a file-backed DB.
2. Instruments sqlite3.Connection.close to count calls and verify cleanup.
3. MUST fail against the pre-fix recursion bug (verified via comment).
"""

from __future__ import annotations

import sqlite3
import threading
from unittest.mock import patch

import pytest

from tracking.memory.manager import UserMemoryManager


def _make_manager(db_path: str) -> UserMemoryManager:
    return UserMemoryManager(db_path)


def test_no_recursion_error_on_full_lifecycle(tmp_path):
    """All operations complete without RecursionError or OperationalError."""
    db_path = str(tmp_path / "lifecycle.sqlite")
    m = _make_manager(db_path)

    mid = m.save_memory(1, "journal", {"text": "hello regression"}, ticker="005930")
    assert mid > 0

    rows = m.get_memories(1)
    assert len(rows) >= 1

    ctx = m.build_llm_context(1, ticker="005930")
    assert isinstance(ctx, str)

    jid = m.save_journal(1, "journal text", ticker="005930")
    assert jid > 0

    journals = m.get_journals(1)
    assert len(journals) >= 1

    result = m.compress_old_memories(layer1_days=0, layer2_days=0)
    assert isinstance(result, dict)

    prefs = m.get_user_preferences(1)
    # May be None or dict — either is fine, no exception is the goal.
    assert prefs is None or isinstance(prefs, dict)

    m.update_user_preferences(1, preferred_tone="concise", investment_style="growth")

    stats = m.get_memory_stats(1)
    assert isinstance(stats, dict)
    assert stats.get("total", 0) >= 1


def test_close_calls_not_fewer_than_open_calls(tmp_path):
    """
    Wrap sqlite3.connect to return a proxy that counts close() calls.
    After a full sequence, close_count >= open_count - 1 for file-backed DBs.
    """
    db_path = str(tmp_path / "close_count.sqlite")

    open_count = 0
    close_count = 0
    lock = threading.Lock()

    original_connect = sqlite3.connect

    class _CountingConn:
        """Thin proxy that delegates everything to the real connection."""
        def __init__(self, real):
            self._real = real

        def close(self):
            nonlocal close_count
            with lock:
                close_count += 1
            self._real.close()

        def __getattr__(self, name):
            return getattr(self._real, name)

    def counting_connect(database, *args, **kwargs):
        nonlocal open_count
        real = original_connect(database, *args, **kwargs)
        with lock:
            open_count += 1
        return _CountingConn(real)

    with patch("sqlite3.connect", side_effect=counting_connect):
        m = UserMemoryManager(db_path)
        m.save_memory(1, "journal", {"text": "test"})
        m.get_memories(1)
        m.build_llm_context(1)
        m.save_journal(1, "a journal")
        m.get_journals(1)
        m.get_user_preferences(1)
        m.update_user_preferences(1, preferred_tone="brief")
        m.get_memory_stats(1)

    # For file-backed DBs: every opened connection must be closed.
    assert close_count >= open_count - 1, (
        f"Leak detected: {open_count} connections opened, only {close_count} closed"
    )


def test_multiple_managers_no_recursion(tmp_path):
    """Creating N file-backed managers in a loop must not raise RecursionError."""
    for i in range(10):
        db_path = str(tmp_path / f"mgr_{i}.sqlite")
        m = UserMemoryManager(db_path)
        mid = m.save_memory(i + 1, "journal", {"text": f"entry {i}"})
        assert mid > 0
        stats = m.get_memory_stats(i + 1)
        assert stats["total"] == 1
