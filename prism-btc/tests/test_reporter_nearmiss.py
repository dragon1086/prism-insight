# tests/test_reporter_nearmiss.py — 일일 리포트 "최근 7일 게이트 최접근" 단위 테스트.
#
# 원칙: 네트워크 0, in-memory DB, now 주입으로 결정적 (healthcheck 테스트와 동일).
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from live import tracking
from live.telegram_reporter import _near_miss_7d
from live.tracking import ensure_schema

_NOW = datetime(2026, 7, 4, 0, 0, 0, tzinfo=timezone.utc)
_NOW_STR = _NOW.isoformat()


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    return conn


def _sig(conn, *, days_ago: float, score: float, ts_4h: float,
         mode: str = "shadow") -> str:
    ts = (_NOW - timedelta(days=days_ago)).isoformat()
    tracking.log_signal(conn, ts, score=score, ts_4h=ts_4h, ts_1d=1.0,
                        side="none", reason="test", mode=mode)
    return ts


def test_empty_log_returns_none():
    conn = _conn()
    assert _near_miss_7d(conn, "shadow", 70.0, 2.0, now=_NOW_STR) is None


def test_score_gate_passed_picks_max_ts4h():
    conn = _conn()
    _sig(conn, days_ago=5, score=-80.0, ts_4h=1.2)
    best_ts = _sig(conn, days_ago=3, score=-95.0, ts_4h=1.98)
    _sig(conn, days_ago=1, score=-30.0, ts_4h=2.5)  # 점수 미달 — 후보 아님
    nm = _near_miss_7d(conn, "shadow", 70.0, 2.0, now=_NOW_STR)
    assert nm is not None
    assert nm["blocked_by"] == "ts"
    assert nm["ts"] == best_ts
    assert nm["ts_4h"] == 1.98


def test_no_score_pass_falls_back_to_max_abs_score():
    conn = _conn()
    _sig(conn, days_ago=4, score=-55.0, ts_4h=2.2)
    _sig(conn, days_ago=2, score=40.0, ts_4h=0.5)
    nm = _near_miss_7d(conn, "shadow", 70.0, 2.0, now=_NOW_STR)
    assert nm is not None
    assert nm["blocked_by"] == "score"
    assert nm["score"] == -55.0


def test_window_excludes_older_than_7_days():
    conn = _conn()
    _sig(conn, days_ago=8, score=-100.0, ts_4h=3.0)  # 창 밖
    _sig(conn, days_ago=2, score=-60.0, ts_4h=1.0)
    nm = _near_miss_7d(conn, "shadow", 70.0, 2.0, now=_NOW_STR)
    assert nm is not None
    assert nm["blocked_by"] == "score"
    assert nm["score"] == -60.0


def test_mode_isolation():
    conn = _conn()
    _sig(conn, days_ago=1, score=-90.0, ts_4h=1.9, mode="demo")
    assert _near_miss_7d(conn, "shadow", 70.0, 2.0, now=_NOW_STR) is None
    nm = _near_miss_7d(conn, "demo", 70.0, 2.0, now=_NOW_STR)
    assert nm is not None and nm["blocked_by"] == "ts"


def test_absorbs_missing_table():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row  # 스키마 없음 → 쿼리 실패 → None
    assert _near_miss_7d(conn, "shadow", 70.0, 2.0, now=_NOW_STR) is None
