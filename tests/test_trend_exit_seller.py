"""Tests for Trend-exit closing-confirmation trend-exit loop (tools/trend_exit_seller.py).

Trend-exit owns the slower O'Neil trend-exit tiers (TIER1.5_MA50 / TIER2_TRAIL /
TIER3_TARGET) and gates them behind a consecutive-breach / close-window confirm
so a single intraday dip below the 50MA does NOT whipsaw the position. Safety-
critical behaviour covered:
  - TIER1 (pure hard stop) reasons are SKIPPED (Hardstop owns them).
  - TIER1.5 / TIER2 / TIER3 signals are recognised and acted on (after the gate).
  - breach_streak increments at most once per calendar day, resets on recovery.
  - the gate fires only at streak >= N, OR in the close window.
  - SHADOW (default): touches NO agent and places NO order, only logs.
  - LIVE: runs sell_stock (sim) -> async_sell_stock (KIS) -> send_telegram_message.
  - owner_lock exclusivity + inflight guard prevent double-selling.
  - Pyramided tickers (>1 row) are skipped.
  - ma_50=0 -> TIER1.5 stays dormant.

ma_50 and the LIVE regime fetch are network-bound, so they are monkeypatched to
constants — these tests are fully network-free. Run in the KR (root) session.
"""
import asyncio
import sys
import sqlite3
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
import tools.trend_exit_seller as lb  # noqa: E402


# ── Fakes ──────────────────────────────────────────────────────────────────────
class FakeTrader:
    def __init__(self, prices, holding_qty=None, sell_result=None, calls=None):
        self._prices = prices
        self._holding_qty = holding_qty or {}
        self._sell_result = sell_result or {"success": True, "order_no": "ORD1", "message": "ok"}
        self.calls = calls if calls is not None else []

    def get_current_price(self, ticker, exchange=None):
        return {"current_price": self._prices.get(ticker, 0)}

    def get_holding_quantity(self, ticker):
        return self._holding_qty.get(ticker, 0)

    async def async_sell_stock(self, ticker, exchange=None, timeout=30.0,
                               limit_price=None, use_moo=False, quantity=None):
        self.calls.append(f"kis:{ticker}:{quantity}")
        return self._sell_result


class FakeCtx:
    def __init__(self, trader):
        self._trader = trader

    async def __aenter__(self):
        return self._trader

    async def __aexit__(self, *a):
        return False


class FakeAgent:
    def __init__(self, calls):
        self.calls = calls
        self.conn = None

    async def sell_stock(self, stock_data, sell_reason, **kwargs):
        self.calls.append(f"sim:{stock_data.get('ticker')}")
        return True

    async def send_telegram_message(self, chat_id, language="ko", **kwargs):
        self.calls.append("tg")
        return True


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db = tmp_path / "t.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE stock_holdings (id INTEGER PRIMARY KEY, ticker TEXT, company_name TEXT, "
        "buy_price REAL, buy_date TEXT, scenario TEXT, target_price REAL, stop_loss REAL, "
        "highest_price REAL, account_key TEXT, account_name TEXT)"
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(lb, "DB_PATH", str(db))
    return str(db)


def _seed(db, rows):
    conn = sqlite3.connect(db)
    conn.executemany(
        "INSERT INTO stock_holdings (id, ticker, company_name, buy_price, buy_date, scenario, "
        "target_price, stop_loss, highest_price, account_key, account_name) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()


def _row(id_, ticker, buy_price, stop_loss=0.0, target_price=0.0, highest_price=0.0):
    return (id_, ticker, ticker, buy_price, "2026-06-01 10:00:00", "{}", target_price,
            stop_loss, highest_price, "acc1", "primary")


def _patch(monkeypatch, trader, agent_holder=None, make_agent_counter=None,
           ma50=0.0, regime="moderate_bull"):
    monkeypatch.setattr(lb, "_open_context", lambda market, account_name=None: FakeCtx(trader))
    # Network-free: fixed ma_50 + regime.
    monkeypatch.setattr(lb, "_fetch_ma50", lambda market, ticker: ma50)
    monkeypatch.setattr(lb, "_compute_live_regime", lambda market: regime)

    async def _fake_make_agent(market):
        if make_agent_counter is not None:
            make_agent_counter.append(1)
        return agent_holder

    monkeypatch.setattr(lb, "_make_agent", _fake_make_agent)


def _inflight(db, status=None):
    conn = sqlite3.connect(db)
    try:
        if status:
            return conn.execute(
                "SELECT COUNT(*) FROM loop_b_inflight_orders WHERE status=?", (status,)
            ).fetchone()[0]
        return conn.execute("SELECT COUNT(*) FROM loop_b_inflight_orders").fetchone()[0]
    finally:
        conn.close()


def _streak(db, ticker, market="KR"):
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT breach_streak FROM loop_b_position_state WHERE ticker=? AND market=?",
            (ticker, market),
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _enable(monkeypatch, live=False, confirm=2, close_window=False):
    monkeypatch.setattr(lb, "TREND_EXIT_LIVE", live)
    monkeypatch.setattr(lb, "TREND_EXIT_ENABLED", True)
    monkeypatch.setattr(lb, "TREND_EXIT_CONFIRM_CHECKS", confirm)
    monkeypatch.setattr(lb, "TREND_EXIT_CLOSE_WINDOW", close_window)


# ── Pure reason-classifier tests ───────────────────────────────────────────────
def test_tier1_reason_is_not_a_loop_b_signal():
    assert lb._is_trend_exit_signal("TIER1_STOPLOSS: price<=stop_loss(90.0)") is False
    assert lb._is_trend_exit_signal("TIER1_ABS7: loss -8.00% <= -7%") is False


def test_tier15_and_trail_and_target_are_loop_b_signals():
    assert lb._is_trend_exit_signal("TIER1.5_MA50: below 50MA(95.0) while losing (-3.00%)") is True
    assert lb._is_trend_exit_signal("TIER2_TRAIL: regime=moderate_bull peak=120 trail(-8%)=110 >= price") is True
    assert lb._is_trend_exit_signal("TIER3_TARGET(weak): regime=sideways target reached") is True
    assert lb._is_trend_exit_signal("HOLD: trend intact") is False


# ── TIER1 must be skipped (Hardstop's territory) ─────────────────────────────────
def test_tier1_hardstop_is_skipped_by_loop_b(tmp_db, monkeypatch):
    # buy 100, cur 92 = -8% -> TIER1_ABS7 in oneil. Trend-exit must NOT signal/act.
    _enable(monkeypatch, live=False, confirm=1)  # confirm=1 so any signal would act
    _seed(tmp_db, [_row(1, "005930", 100.0)])
    calls = []
    trader = FakeTrader({"005930": 92.0}, calls=calls)
    _patch(monkeypatch, trader, agent_holder=FakeAgent(calls), ma50=0.0)

    summary = asyncio.run(lb.run_market("KR", "run1"))

    assert summary["checked"] == 1
    assert summary["signaled"] == 0  # TIER1 not owned by Trend-exit
    assert summary["acted"] == 0
    assert calls == []
    assert _inflight(tmp_db) == 0


# ── TIER1.5 MA50: dormant when ma_50=0, fires when ma_50 injected ──────────────
def test_ma50_zero_keeps_tier15_dormant(tmp_db, monkeypatch):
    # cur 98 (-2% loss). With ma_50=0, TIER1.5 cannot fire; no other tier either.
    _enable(monkeypatch, live=False, confirm=1)
    _seed(tmp_db, [_row(1, "005930", 100.0)])
    calls = []
    trader = FakeTrader({"005930": 98.0}, calls=calls)
    _patch(monkeypatch, trader, agent_holder=FakeAgent(calls), ma50=0.0)

    summary = asyncio.run(lb.run_market("KR", "run1"))

    assert summary["signaled"] == 0 and summary["acted"] == 0


def test_tier15_fires_when_below_ma50_and_losing(tmp_db, monkeypatch):
    # cur 98 (-2% loss), ma_50=105 -> price clearly below 50MA while losing -> TIER1.5.
    # confirm=1 so the first day's breach immediately opens the gate.
    _enable(monkeypatch, live=False, confirm=1)
    _seed(tmp_db, [_row(1, "005930", 100.0)])
    calls = []
    trader = FakeTrader({"005930": 98.0}, calls=calls)
    _patch(monkeypatch, trader, agent_holder=FakeAgent(calls), ma50=105.0)

    summary = asyncio.run(lb.run_market("KR", "run1"))

    assert summary["signaled"] == 1
    assert summary["acted"] == 1
    assert summary["shadow"] == 1
    assert _inflight(tmp_db, "SHADOW") == 1


# ── breach_streak: increments once/day, resets on recovery ─────────────────────
def test_breach_streak_increments_once_per_day_and_gates(tmp_db, monkeypatch):
    # confirm=2: a single day's breach (streak=1) must NOT act (gated).
    _enable(monkeypatch, live=False, confirm=2)
    _seed(tmp_db, [_row(1, "005930", 100.0)])
    calls = []
    trader = FakeTrader({"005930": 98.0}, calls=calls)
    _patch(monkeypatch, trader, agent_holder=FakeAgent(calls), ma50=105.0)

    s1 = asyncio.run(lb.run_market("KR", "run1"))
    # Same calendar day, second checkpoint: streak stays 1 (once/day), still gated.
    s2 = asyncio.run(lb.run_market("KR", "run2"))

    assert _streak(tmp_db, "005930") == 1
    assert s1["signaled"] == 1 and s1["acted"] == 0 and s1["gated"] == 1
    assert s2["signaled"] == 1 and s2["acted"] == 0
    assert calls == []  # never acted


def test_breach_streak_resets_on_recovery(tmp_db, monkeypatch):
    _enable(monkeypatch, live=False, confirm=2)
    _seed(tmp_db, [_row(1, "005930", 100.0)])
    calls = []
    # First cycle: breach (below 50MA, losing). Second cycle: recovered above 50MA.
    trader_breach = FakeTrader({"005930": 98.0}, calls=calls)
    _patch(monkeypatch, trader_breach, agent_holder=FakeAgent(calls), ma50=105.0)
    asyncio.run(lb.run_market("KR", "run1"))
    assert _streak(tmp_db, "005930") == 1

    trader_ok = FakeTrader({"005930": 110.0}, calls=calls)  # winner, no signal
    _patch(monkeypatch, trader_ok, agent_holder=FakeAgent(calls), ma50=105.0)
    asyncio.run(lb.run_market("KR", "run2"))

    assert _streak(tmp_db, "005930") == 0  # reset on recovery


def test_gate_fires_when_streak_reaches_n(tmp_db, monkeypatch):
    # Simulate streak already at N-1 from a prior day, then today's breach -> act.
    _enable(monkeypatch, live=False, confirm=2)
    _seed(tmp_db, [_row(1, "005930", 100.0)])
    # Pre-seed state: streak=1 with last_breach_date = yesterday (relative to lb._today()).
    from datetime import date, timedelta as _td
    yesterday = (date.fromisoformat(lb._today()) - _td(days=1)).isoformat()
    conn = sqlite3.connect(tmp_db)
    lb._ensure_schema(conn)
    conn.execute(
        "INSERT INTO loop_b_position_state (ticker, market, state, breach_streak, last_breach_date) "
        "VALUES ('005930','KR','HOLDING',1,?)",
        (yesterday,),
    )
    conn.commit()
    conn.close()
    calls = []
    trader = FakeTrader({"005930": 98.0}, calls=calls)
    _patch(monkeypatch, trader, agent_holder=FakeAgent(calls), ma50=105.0)

    summary = asyncio.run(lb.run_market("KR", "run1"))

    assert _streak(tmp_db, "005930") == 2
    assert summary["acted"] == 1 and summary["shadow"] == 1


def test_gate_fires_in_close_window_even_at_streak_1(tmp_db, monkeypatch):
    # confirm=2 normally gates streak=1, but TREND_EXIT_CLOSE_WINDOW=true confirms now.
    _enable(monkeypatch, live=False, confirm=2, close_window=True)
    _seed(tmp_db, [_row(1, "005930", 100.0)])
    calls = []
    trader = FakeTrader({"005930": 98.0}, calls=calls)
    _patch(monkeypatch, trader, agent_holder=FakeAgent(calls), ma50=105.0)

    summary = asyncio.run(lb.run_market("KR", "run1"))

    assert summary["acted"] == 1 and summary["shadow"] == 1


# ── SHADOW vs LIVE ─────────────────────────────────────────────────────────────
def test_shadow_touches_no_agent_and_no_order(tmp_db, monkeypatch):
    _enable(monkeypatch, live=False, confirm=1, close_window=True)
    _seed(tmp_db, [_row(1, "005930", 100.0)])
    calls = []
    counter = []
    trader = FakeTrader({"005930": 98.0}, calls=calls)
    _patch(monkeypatch, trader, agent_holder=FakeAgent(calls),
           make_agent_counter=counter, ma50=105.0)

    summary = asyncio.run(lb.run_market("KR", "run1"))

    assert summary["shadow"] == 1 and summary["sold"] == 0
    assert counter == []          # agent never created
    assert calls == []            # no sim / kis / tg calls
    assert _inflight(tmp_db, "SHADOW") == 1


def test_live_order_is_sim_then_kis_then_telegram(tmp_db, monkeypatch):
    _enable(monkeypatch, live=True, confirm=1, close_window=True)
    monkeypatch.setattr(lb, "CHAT_ID", "chat1")
    _seed(tmp_db, [_row(1, "005930", 100.0)])
    calls = []
    trader = FakeTrader({"005930": 98.0}, holding_qty={"005930": 10}, calls=calls)
    _patch(monkeypatch, trader, agent_holder=FakeAgent(calls), ma50=105.0)

    summary = asyncio.run(lb.run_market("KR", "run1"))

    assert summary["sold"] == 1
    # per-sell flush + run-end flush each invoke send_telegram_message; the run-end
    # portfolio summary is de-duplicated (portfolio_broadcast) so only ONE actual
    # portfolio message goes out in prod (see tests/test_portfolio_broadcast.py).
    assert calls == ["sim:005930", "kis:005930:10", "tg", "tg"]   # exact order
    assert _inflight(tmp_db, "FILLED") == 1


# ── Guards ─────────────────────────────────────────────────────────────────────
def test_pyramided_ticker_is_skipped(tmp_db, monkeypatch):
    _enable(monkeypatch, live=False, confirm=1, close_window=True)
    _seed(tmp_db, [_row(1, "005930", 100.0), _row(2, "005930", 110.0)])  # 2 rows
    calls = []
    trader = FakeTrader({"005930": 80.0}, calls=calls)  # deep loss, but must skip
    _patch(monkeypatch, trader, agent_holder=FakeAgent(calls), ma50=105.0)

    summary = asyncio.run(lb.run_market("KR", "run1"))

    assert summary["pyramided_skipped"] == 1
    assert summary["checked"] == 0 and summary["acted"] == 0
    assert calls == []


def test_shadow_record_does_not_block_second_trigger(tmp_db, monkeypatch):
    _enable(monkeypatch, live=False, confirm=1, close_window=True)
    _seed(tmp_db, [_row(1, "005930", 100.0)])
    calls = []
    trader = FakeTrader({"005930": 98.0}, calls=calls)
    _patch(monkeypatch, trader, agent_holder=FakeAgent(calls), ma50=105.0)

    asyncio.run(lb.run_market("KR", "run1"))
    summary2 = asyncio.run(lb.run_market("KR", "run2"))

    assert summary2["shadow"] == 1
    assert _inflight(tmp_db) == 2


def test_owner_lock_is_exclusive(tmp_db):
    conn = lb._connect()
    lb._ensure_schema(conn)
    assert lb.claim_lock(conn, "005930", "KR", "runA") is True
    assert lb.claim_lock(conn, "005930", "KR", "runB") is False
    lb.release_lock(conn, "005930", "KR", "runA")
    assert lb.claim_lock(conn, "005930", "KR", "runB") is True
    conn.close()
