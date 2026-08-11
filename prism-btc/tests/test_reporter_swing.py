"""일일 리포트의 메인/스윙 포지션 구분 회귀 테스트."""
from __future__ import annotations

import sqlite3

from live import tracking
from live.telegram_reporter import build_message


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    tracking.ensure_schema(conn)
    return conn


def _position(*, mode: str, side: str = "long") -> tracking.PositionRow:
    return tracking.PositionRow(
        side=side,
        entry_price=100_000.0,
        qty=0.01,
        leverage=1.0,
        sl_price=95_000.0,
        tp1_price=0.0,
        tp2_price=0.0,
        tp3_price=0.0,
        liq_price=0.0,
        entry_time="2026-08-10T00:00:00+00:00",
        tranche_index=0,
        entry_bar_idx=0,
        initial_risk=50.0,
        initial_qty=0.01,
        mode=mode,
    )


def test_swing_only_position_is_reported_instead_of_generic_wait(monkeypatch):
    conn = _conn()
    tracking.save_position(conn, _position(mode="swing"))
    tracking.log_signal(
        conn,
        "2026-08-11T08:00:00+00:00",
        score=-23.0,
        ts_4h=0.32,
        ts_1d=0.04,
        side="none",
        reason="횡보관망",
        mode="demo",
    )
    monkeypatch.setattr("live.telegram_reporter._last_price", lambda conn: 101_000.0)

    message = build_message(conn, "demo")

    assert "• 메인 추세 전략: 관망 중 (신규 기회를 기다리는 중)" in message
    assert "• 스윙 전략: 📈 상승 베팅 · 진입가 100,000달러" in message
    assert "• 메인 추세 전략 신규진입 판단: *관망 (진입 보류)*" in message
    assert "• 관망 중 (좋은 기회를 기다리는 중)" not in message


def test_no_positions_keeps_simple_wait_message():
    conn = _conn()

    message = build_message(conn, "demo")

    assert "• 관망 중 (좋은 기회를 기다리는 중)" in message
    assert "스윙 전략:" not in message
