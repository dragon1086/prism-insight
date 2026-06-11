# tests/test_live_shadow.py — 섀도우 페이퍼 데몬 핵심 경로 단위 테스트 (오프라인).
#
# - 스키마 생성 + btc_* 테이블 격리 (기존 테이블 미손상)
# - 포지션 저장/복원 라운드트립
# - 가상 체결 1사이클 (조작된 미니 데이터로 진입→체결→equity 변화)
# - equity 기록
from __future__ import annotations

import sqlite3
import pandas as pd
import pytest

from live import tracking
from live.tracking import PositionRow, TradeRow, ensure_schema
from live.shadow import ShadowAdapter, bar_index_for, INITIAL_EQUITY, _30M_MS


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _root_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    return conn


def _make_indexed_df(n: int, start_ms: int, interval_ms: int,
                     base: float = 50000.0, trend: float = 0.0) -> pd.DataFrame:
    closes = [base + trend * i for i in range(n)]
    highs = [c * 1.01 for c in closes]
    lows = [c * 0.99 for c in closes]
    opens = [c * 0.999 for c in closes]
    df = pd.DataFrame({
        "open": opens, "high": highs, "low": lows, "close": closes,
        "volume": [1.0] * n, "turnover": [1.0] * n,
    })
    df.index = pd.to_datetime(
        [start_ms + i * interval_ms for i in range(n)], unit="ms", utc=True
    )
    return df


def _make_tf_data(n_30m: int = 120, base: float = 50000.0, trend: float = 0.0):
    intervals = {
        "30m": 30 * 60 * 1000, "1h": 60 * 60 * 1000, "4h": 4 * 60 * 60 * 1000,
        "12h": 12 * 60 * 60 * 1000, "1d": 24 * 60 * 60 * 1000,
        "1w": 7 * 24 * 60 * 60 * 1000,
    }
    start_ms = 1640995200000  # 2022-01-01 UTC
    tf_data = {}
    for tf, iv in intervals.items():
        n = max(n_30m // (iv // intervals["30m"]), 80)
        tf_data[tf] = _make_indexed_df(n, start_ms, iv, base=base, trend=trend)
    return tf_data


# ---------------------------------------------------------------------------
# Schema + isolation
# ---------------------------------------------------------------------------

def test_ensure_schema_creates_btc_tables():
    conn = _root_conn()
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"btc_positions", "btc_trading_history", "btc_equity_curve",
            "btc_events", "btc_meta"} <= names


def test_schema_does_not_touch_existing_tables():
    """기존(가짜 주식) 테이블을 만든 뒤 ensure_schema 호출해도 무손상."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE trading_history (id INTEGER PRIMARY KEY, x TEXT)")
    conn.execute("INSERT INTO trading_history (x) VALUES ('keep-me')")
    conn.commit()
    ensure_schema(conn)
    rows = conn.execute("SELECT x FROM trading_history").fetchall()
    assert len(rows) == 1 and rows[0]["x"] == "keep-me"


# ---------------------------------------------------------------------------
# Position roundtrip
# ---------------------------------------------------------------------------

def test_position_save_load_roundtrip():
    conn = _root_conn()
    pos = PositionRow(
        side="long", entry_price=50000.0, qty=0.1, leverage=5.0,
        sl_price=49000.0, tp1_price=51000.0, tp2_price=52000.0, tp3_price=53000.0,
        liq_price=45000.0, entry_time="2022-01-01T00:00:00+00:00",
        tranche_index=0, entry_bar_idx=911664, initial_risk=200.0,
        trailing_active=True, tp1_hit=True, entry_fee=1.0, initial_qty=0.1,
        acc_funding=0.5, legs_closed=1,
    )
    pid = tracking.save_position(conn, pos)
    assert pid is not None and pos.id == pid

    loaded = tracking.load_open_positions(conn, "shadow")
    assert len(loaded) == 1
    lp = loaded[0]
    assert lp.side == "long"
    assert lp.entry_price == 50000.0
    assert lp.trailing_active is True and lp.tp1_hit is True
    assert lp.acc_funding == 0.5 and lp.legs_closed == 1
    assert lp.id == pid

    # update path
    lp.qty = 0.05
    tracking.save_position(conn, lp)
    reloaded = tracking.load_open_positions(conn, "shadow")
    assert len(reloaded) == 1 and reloaded[0].qty == 0.05

    # remove path
    tracking.remove_position(conn, lp.id)
    assert tracking.load_open_positions(conn, "shadow") == []


def test_record_trade_and_equity():
    conn = _root_conn()
    trade = TradeRow(
        trade_id=0, side="short", entry_time="t0", entry_price=50000.0,
        exit_time="t1", exit_price=49000.0, qty=0.1, leverage=3.0,
        sl_price=51000.0, exit_reason="tp1", r_multiple=1.0, fee_paid=0.5,
        funding_paid=0.1, tranche_index=0, liq_price=55000.0, net_pnl=98.0,
    )
    tracking.record_trade(conn, trade)
    rows = conn.execute("SELECT * FROM btc_trading_history").fetchall()
    assert len(rows) == 1 and rows[0]["exit_reason"] == "tp1"
    assert rows[0]["mode"] == "shadow"

    tracking.record_equity(conn, 10100.0, "shadow", "t1")
    assert tracking.latest_equity(conn, "shadow") == 10100.0
    tracking.record_equity(conn, 9900.0, "shadow", "t2")
    assert tracking.latest_equity(conn, "shadow") == 9900.0
    assert tracking.peak_equity(conn, "shadow") == 10100.0


def test_meta_roundtrip():
    conn = _root_conn()
    assert tracking.get_meta(conn, "missing") is None
    tracking.set_meta(conn, "last_close_bar", {"long": 5, "short": -10000})
    assert tracking.get_meta(conn, "last_close_bar") == {"long": 5, "short": -10000}
    tracking.set_meta(conn, "last_close_bar", {"long": 7, "short": -10000})
    assert tracking.get_meta(conn, "last_close_bar")["long"] == 7


# ---------------------------------------------------------------------------
# Virtual fill cycle
# ---------------------------------------------------------------------------

def test_bar_index_funding_alignment():
    """bar_idx % 16 == 0 이 실제 8h UTC 펀딩 경계와 정렬되는지."""
    # 2022-01-01 00:00 UTC == funding boundary
    assert bar_index_for(1640995200000) % 16 == 0
    # +8h
    assert bar_index_for(1640995200000 + 8 * 3600 * 1000) % 16 == 0
    # +30m → not a boundary
    assert bar_index_for(1640995200000 + 30 * 60 * 1000) % 16 != 0


def test_virtual_fill_cycle_pending_to_position():
    """수동으로 pending order 메타를 심고, 다음 봉이 체결하면 포지션이 생기고
    entry fee 만큼 equity 가 감소하는지 (가상 체결 1사이클)."""
    conn = _root_conn()
    tf_data = _make_tf_data()
    adapter = ShadowAdapter(conn, tf_data, [], [], mode="shadow")

    # 초기 equity 시드.
    tracking.record_equity(conn, INITIAL_EQUITY, "shadow", "seed")

    # pending order: limit 가 다음 봉 [low,high] 안에 들도록 base 가격으로.
    bar_time = tf_data["30m"].index[-1]
    bar_idx = bar_index_for(int(bar_time.value // 1_000_000))
    lp = 50000.0
    tracking.set_meta(conn, "pending_order", {
        "side": "long", "limit_price": lp, "bar_idx": bar_idx - 1,
        "sizing_qty": 0.1, "sizing_leverage": 5.0, "sizing_sl_price": 49000.0,
        "sizing_tp1_price": 51000.0, "sizing_tp2_price": 52000.0,
        "sizing_tp3_price": 53000.0, "sizing_liq_price": 45000.0,
        "initial_risk": 200.0, "tranche_index": 0,
    }, "shadow")

    bar = tf_data["30m"].iloc[-1]
    eq_before = tracking.latest_equity(conn, "shadow")
    adapter.process_bar(bar_time, bar, new_4h_confirmed=False, cur_4h_ns=None)

    positions = tracking.load_open_positions(conn, "shadow")
    assert len(positions) == 1
    pos = positions[0]
    assert pos.side == "long" and pos.entry_price == lp and pos.qty == 0.1
    # entry fee = nominal * MAKER_FEE 만큼 equity 감소.
    eq_after = tracking.latest_equity(conn, "shadow")
    expected_fee = 0.1 * lp * 0.0002  # MAKER_FEE
    assert eq_after == pytest.approx(eq_before - expected_fee, abs=1e-6)
    # pending 은 소진.
    assert tracking.get_meta(conn, "pending_order") is None
    # equity 곡선에 새 점 기록.
    assert tracking.latest_equity(conn, "shadow") is not None


def test_virtual_fill_then_sl_close():
    """포지션 체결 후, SL 아래로 급락하는 봉을 주면 SL 종결 + trade 기록 + 포지션 제거."""
    conn = _root_conn()
    tf_data = _make_tf_data()
    adapter = ShadowAdapter(conn, tf_data, [], [], mode="shadow")
    tracking.record_equity(conn, INITIAL_EQUITY, "shadow", "seed")

    bar_time = tf_data["30m"].index[-1]
    bar_idx = bar_index_for(int(bar_time.value // 1_000_000))
    # 이미 열린 long 포지션을 직접 심는다 (SL=49000).
    pos = PositionRow(
        side="long", entry_price=50000.0, qty=0.1, leverage=5.0,
        sl_price=49000.0, tp1_price=51000.0, tp2_price=52000.0, tp3_price=53000.0,
        liq_price=45000.0, entry_time="t0", tranche_index=0,
        entry_bar_idx=bar_idx - 1, initial_risk=100.0, entry_fee=1.0, initial_qty=0.1,
    )
    tracking.save_position(conn, pos)

    # SL(49000) 을 관통하는 봉: low 를 48000 으로.
    bar = pd.Series({"open": 49500.0, "high": 49600.0, "low": 48000.0, "close": 48500.0,
                     "volume": 1.0, "turnover": 1.0})
    adapter.process_bar(bar_time, bar, new_4h_confirmed=False, cur_4h_ns=None)

    assert tracking.load_open_positions(conn, "shadow") == []
    trades = conn.execute("SELECT * FROM btc_trading_history").fetchall()
    assert len(trades) == 1
    assert trades[0]["exit_reason"] in ("sl", "be")
    assert trades[0]["net_pnl"] < 0  # 손실 종결
    # 쿨다운 트래커가 SL 로 기록됐는지.
    assert tracking.get_meta(conn, "last_close_was_sl")["long"] is True


def test_heartbeat_event_logged_on_process():
    """process_bar 후 signal/fill 이벤트가 없더라도 equity 점이 남는다(상태 진행 증거)."""
    conn = _root_conn()
    tf_data = _make_tf_data()
    adapter = ShadowAdapter(conn, tf_data, [], [], mode="shadow")
    tracking.record_equity(conn, INITIAL_EQUITY, "shadow", "seed")
    bar_time = tf_data["30m"].index[-1]
    bar = tf_data["30m"].iloc[-1]
    n_before = conn.execute("SELECT COUNT(*) c FROM btc_equity_curve").fetchone()["c"]
    adapter.process_bar(bar_time, bar, new_4h_confirmed=False, cur_4h_ns=None)
    n_after = conn.execute("SELECT COUNT(*) c FROM btc_equity_curve").fetchone()["c"]
    assert n_after == n_before + 1
