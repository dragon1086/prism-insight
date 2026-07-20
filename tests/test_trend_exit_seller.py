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
import os
import sys
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
import tools.trend_exit_seller as lb  # noqa: E402


# ── Fakes ──────────────────────────────────────────────────────────────────────
class FakeTrader:
    def __init__(self, prices, holding_qty=None, sell_result=None, calls=None,
                 checked_holding=None):
        self._prices = prices
        self._holding_qty = holding_qty or {}
        self._sell_result = sell_result or {"success": True, "order_no": "ORD1", "message": "ok"}
        self._checked_holding = checked_holding
        self.calls = calls if calls is not None else []

    def get_current_price(self, ticker, exchange=None):
        return {"current_price": self._prices.get(ticker, 0)}

    def get_holding_quantity(self, ticker):
        return self._holding_qty.get(ticker, 0)

    def get_holding_quantity_checked(self, ticker):
        self.calls.append(f"checked:{ticker}")
        if self._checked_holding is not None:
            return self._checked_holding
        quantity = self._holding_qty.get(ticker, 0)
        return ("HELD", quantity) if quantity > 0 else ("FLAT", 0)

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
    def __init__(self, calls, *, pending_result=None, pending_error=None,
                 complete_error=None):
        self.calls = calls
        self.conn = None
        self.pending_result = pending_result or {
            "success": True,
            "intent_status": "SUBMITTED",
            "intent_id": "intent-1",
            "order_no": "ORD1",
            "message": "ok",
        }
        self.pending_error = pending_error
        self.complete_error = complete_error
        self.prepare_kwargs = None

    async def sell_stock(self, stock_data, sell_reason, **kwargs):
        self.calls.append(f"sim:{stock_data.get('ticker')}")
        return True

    def _link_position_exit_intent(self, **kwargs):
        self.calls.append(f"link:{kwargs.get('legacy_holding_id')}")
        return True

    @staticmethod
    def _position_pending_kr_enabled():
        return os.environ.get("POSITION_PENDING_KR_ENABLED", "false").lower() in {
            "1", "true", "yes", "on"
        }

    def _prepare_pending_kr_exit(self, **kwargs):
        self.prepare_kwargs = kwargs
        self.calls.append(f"prepare:{kwargs.get('quantity')}")
        return SimpleNamespace(
            intent=SimpleNamespace(id="intent-1"),
            symbol=kwargs["stock_data"]["ticker"],
            quantity=kwargs.get("quantity"),
        )

    async def _execute_pending_kr_exit(self, prepared):
        self.calls.append(f"kis:{prepared.symbol}:{prepared.quantity}")
        if self.pending_error is not None:
            raise self.pending_error
        return dict(self.pending_result)

    async def _execute_pending_kr_local_flat_exit(self, prepared):
        self.calls.append("local-flat")
        if self.pending_error is not None:
            raise self.pending_error
        return {
            "success": True,
            "local_flat": True,
            "intent_status": "SUBMITTED",
            "intent_id": prepared.intent.id,
            "quantity": 0,
        }

    def _complete_pending_kr_exit(self, _prepared):
        self.calls.append("complete")
        if self.complete_error is not None:
            raise self.complete_error

    def _fail_pending_kr_exit(self, _prepared):
        self.calls.append("fail")

    def _quarantine_pending_kr_exit(self, _prepared):
        self.calls.append("quarantine")

    async def _run_pending_kr_exit_post_commit(self, _prepared):
        self.calls.append("postcommit")

    async def _deliver_pending_kr_exit_publish_effects(
        self, prepared, trade_result
    ):
        self.calls.append("effects")
        return {"REDIS": "delivered", "GCP": "delivered"}

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
    from prism_core.execution_service import ExecutionService
    from prism_core.order_intents import IntentStore

    monkeypatch.setattr(
        lb,
        "_open_context",
        lambda market, account_name=None: ExecutionService(
            FakeCtx(trader), intent_store=IntentStore(lb.DB_PATH)
        ),
    )
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


def _position_state(db, ticker="005930", market="KR"):
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT state FROM loop_b_position_state WHERE ticker=? AND market=?",
            (ticker, market),
        ).fetchone()
        return row[0] if row else None
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
    monkeypatch.setenv("POSITION_PENDING_KR_ENABLED", "false")
    _enable(monkeypatch, live=True, confirm=1, close_window=True)
    monkeypatch.setattr(lb, "CHAT_ID", "chat1")
    _seed(tmp_db, [_row(1, "005930", 100.0)])
    calls = []
    trader = FakeTrader({"005930": 98.0}, holding_qty={"005930": 10}, calls=calls)
    _patch(monkeypatch, trader, agent_holder=FakeAgent(calls), ma50=105.0)

    async def publish_loop_sell(**_kwargs):
        calls.append("publish")

    monkeypatch.setattr("sell_broadcast.publish_loop_sell", publish_loop_sell)

    summary = asyncio.run(lb.run_market("KR", "run1"))

    assert summary["sold"] == 1
    # per-sell flush + run-end flush each invoke send_telegram_message; the run-end
    # portfolio summary is de-duplicated (portfolio_broadcast) so only ONE actual
    # portfolio message goes out in prod (see tests/test_portfolio_broadcast.py).
    assert calls == [
        "sim:005930",
        "kis:005930:10",
        "link:1",
        "tg",
        "publish",
        "tg",
    ]  # exact order
    assert _inflight(tmp_db, "FILLED") == 1


def test_pending_kr_gate_does_not_change_us_live_order(tmp_db, monkeypatch):
    monkeypatch.setenv("POSITION_PENDING_KR_ENABLED", "true")
    _enable(monkeypatch, live=True, confirm=1, close_window=True)
    calls = []
    trader = FakeTrader({"AAPL": 92.0}, holding_qty={"AAPL": 10}, calls=calls)
    agent = FakeAgent(calls)
    _patch(monkeypatch, trader, agent_holder=agent, ma50=105.0)

    async def publish_loop_sell(**_kwargs):
        calls.append("publish")

    monkeypatch.setattr("sell_broadcast.publish_loop_sell", publish_loop_sell)
    conn = lb._connect()
    lb._ensure_schema(conn)
    summary = {"sold": 0, "skipped": 0}
    stock = {
        "id": 1,
        "ticker": "AAPL",
        "company_name": "Apple",
        "buy_price": 100.0,
        "buy_date": "2026-06-01 10:00:00",
        "current_price": 92.0,
        "account_key": "us:account:01",
        "account_name": "primary",
    }
    try:
        asyncio.run(
            lb._act_on_trigger(
                conn, "US", "AAPL", stock, "trend exit", 1,
                "run-us", {"ref": agent}, summary,
            )
        )
    finally:
        conn.close()

    assert summary["sold"] == 1
    assert calls == [
        "sim:AAPL",
        "kis:AAPL:10",
        "link:1",
        "tg",
        "publish",
    ]
    assert agent.prepare_kwargs is None


def test_pending_kr_submitted_closes_before_telegram_and_publish(tmp_db, monkeypatch):
    monkeypatch.setenv("POSITION_PENDING_KR_ENABLED", "true")
    _enable(monkeypatch, live=True, confirm=1, close_window=True)
    _seed(tmp_db, [_row(1, "005930", 100.0)])
    calls = []
    trader = FakeTrader(
        {"005930": 98.0},
        holding_qty={"005930": 10},
        calls=calls,
    )
    agent = FakeAgent(calls)
    _patch(monkeypatch, trader, agent_holder=agent, ma50=105.0)

    async def publish_loop_sell(**_kwargs):
        calls.append("publish")

    monkeypatch.setattr("sell_broadcast.publish_loop_sell", publish_loop_sell)

    summary = asyncio.run(lb.run_market("KR", "run1"))

    assert summary["sold"] == 1
    assert calls == [
        "checked:005930",
        "prepare:10",
        "kis:005930:10",
        "complete",
        "postcommit",
        "tg",
        "effects",
        "tg",
    ]
    assert agent.prepare_kwargs["source"] == "trend_exit"
    assert agent.prepare_kwargs["exit_kind"] == "trend_exit"
    assert agent.prepare_kwargs["order_style"] == "market"
    assert agent.prepare_kwargs["limit_price"] is None
    assert _inflight(tmp_db, "FILLED") == 1
    assert _position_state(tmp_db) == "SOLD"


@pytest.mark.parametrize(
    ("intent_status", "transition_call", "inflight_status", "owner_state"),
    [
        ("FAILED", "fail", "REJECTED", "HOLDING"),
        ("UNKNOWN", "quarantine", "UNKNOWN", "QUARANTINED"),
        ("SUBMITTING", "quarantine", "UNKNOWN", "QUARANTINED"),
        ("QUEUED", None, "QUEUED", "QUARANTINED"),
    ],
)
def test_pending_kr_unresolved_matrix_has_no_external_effects(
    tmp_db, monkeypatch, intent_status, transition_call, inflight_status, owner_state
):
    monkeypatch.setenv("POSITION_PENDING_KR_ENABLED", "true")
    _enable(monkeypatch, live=True, confirm=1, close_window=True)
    _seed(tmp_db, [_row(1, "005930", 100.0)])
    calls = []
    trader = FakeTrader(
        {"005930": 98.0}, holding_qty={"005930": 10}, calls=calls
    )
    agent = FakeAgent(
        calls,
        pending_result={
            "success": False,
            "intent_status": intent_status,
            "intent_id": "intent-1",
            "order_no": None,
            "message": intent_status,
        },
    )
    _patch(monkeypatch, trader, agent_holder=agent, ma50=105.0)

    async def unexpected_publish(**_kwargs):
        raise AssertionError("unresolved pending exit must not publish")

    monkeypatch.setattr("sell_broadcast.publish_loop_sell", unexpected_publish)

    summary = asyncio.run(lb.run_market("KR", "run1"))

    assert summary["sold"] == 0
    assert "complete" not in calls
    assert "postcommit" not in calls
    assert "tg" not in calls
    assert "publish" not in calls
    if transition_call is None:
        assert "fail" not in calls and "quarantine" not in calls
    else:
        assert transition_call in calls
    assert _inflight(tmp_db, inflight_status) == 1
    assert _position_state(tmp_db) == owner_state


def test_pending_kr_authoritative_unknown_blocks_before_prepare(tmp_db, monkeypatch):
    monkeypatch.setenv("POSITION_PENDING_KR_ENABLED", "true")
    _enable(monkeypatch, live=True, confirm=1, close_window=True)
    _seed(tmp_db, [_row(1, "005930", 100.0)])
    calls = []
    trader = FakeTrader(
        {"005930": 98.0}, checked_holding=("UNKNOWN", None), calls=calls
    )
    agent = FakeAgent(calls)
    _patch(monkeypatch, trader, agent_holder=agent, ma50=105.0)

    summary = asyncio.run(lb.run_market("KR", "run1"))

    assert summary["sold"] == 0
    assert calls == ["checked:005930"]
    assert agent.prepare_kwargs is None
    assert _inflight(tmp_db) == 0
    assert _position_state(tmp_db) == "HOLDING"


def test_pending_kr_malformed_flat_quantity_never_closes(tmp_db, monkeypatch):
    monkeypatch.setenv("POSITION_PENDING_KR_ENABLED", "true")
    _enable(monkeypatch, live=True, confirm=1, close_window=True)
    _seed(tmp_db, [_row(1, "005930", 100.0)])
    calls = []
    trader = FakeTrader(
        {"005930": 98.0}, checked_holding=("FLAT", 7), calls=calls
    )
    agent = FakeAgent(calls)
    _patch(monkeypatch, trader, agent_holder=agent, ma50=105.0)

    summary = asyncio.run(lb.run_market("KR", "run-malformed-flat"))

    assert summary["sold"] == 0
    assert calls == ["checked:005930"]
    assert agent.prepare_kwargs is None
    assert _inflight(tmp_db) == 0
    assert _position_state(tmp_db) == "HOLDING"


def test_pending_kr_local_flat_is_audited_without_broker_call(tmp_db, monkeypatch):
    monkeypatch.setenv("POSITION_PENDING_KR_ENABLED", "true")
    _enable(monkeypatch, live=True, confirm=1, close_window=True)
    _seed(tmp_db, [_row(1, "005930", 100.0)])
    calls = []
    trader = FakeTrader(
        {"005930": 98.0}, checked_holding=("FLAT", 0), calls=calls
    )
    agent = FakeAgent(calls)
    _patch(monkeypatch, trader, agent_holder=agent, ma50=105.0)

    async def publish_loop_sell(**_kwargs):
        calls.append("publish")

    monkeypatch.setattr("sell_broadcast.publish_loop_sell", publish_loop_sell)

    summary = asyncio.run(lb.run_market("KR", "run1"))

    assert summary["sold"] == 1
    assert calls == [
        "checked:005930",
        "prepare:None",
        "local-flat",
        "complete",
        "postcommit",
        "tg",
        "effects",
        "tg",
    ]
    assert not any(call.startswith("kis:") for call in calls)
    assert agent.prepare_kwargs["quantity"] is None
    assert _inflight(tmp_db, "FILLED") == 1
    assert _position_state(tmp_db) == "SOLD"


def test_pending_kr_finalize_failure_quarantines_without_external_effects(
    tmp_db, monkeypatch
):
    monkeypatch.setenv("POSITION_PENDING_KR_ENABLED", "true")
    _enable(monkeypatch, live=True, confirm=1, close_window=True)
    _seed(tmp_db, [_row(1, "005930", 100.0)])
    calls = []
    trader = FakeTrader(
        {"005930": 98.0}, holding_qty={"005930": 10}, calls=calls
    )
    agent = FakeAgent(
        calls, complete_error=sqlite3.OperationalError("injected finalize failure")
    )
    _patch(monkeypatch, trader, agent_holder=agent, ma50=105.0)

    summary = asyncio.run(lb.run_market("KR", "run1"))

    assert summary["sold"] == 0
    assert "complete" in calls and "quarantine" in calls
    assert "postcommit" not in calls and "tg" not in calls
    assert _inflight(tmp_db, "UNKNOWN") == 1
    assert _position_state(tmp_db) == "QUARANTINED"


def test_pending_kr_order_outcome_unknown_quarantines_without_external_effects(
    tmp_db, monkeypatch
):
    from prism_core.execution_service import OrderOutcomeUnknown

    monkeypatch.setenv("POSITION_PENDING_KR_ENABLED", "true")
    _enable(monkeypatch, live=True, confirm=1, close_window=True)
    _seed(tmp_db, [_row(1, "005930", 100.0)])
    calls = []
    trader = FakeTrader(
        {"005930": 98.0}, holding_qty={"005930": 10}, calls=calls
    )
    agent = FakeAgent(
        calls,
        pending_error=OrderOutcomeUnknown(
            "intent-1", broker_result="opaque non-dict broker result"
        ),
    )
    _patch(monkeypatch, trader, agent_holder=agent, ma50=105.0)

    summary = asyncio.run(lb.run_market("KR", "run1"))

    assert summary["sold"] == 0
    assert "quarantine" in calls
    assert "complete" not in calls and "tg" not in calls
    assert _inflight(tmp_db, "UNKNOWN") == 1
    assert _position_state(tmp_db) == "QUARANTINED"


def test_pending_kr_prepare_failure_has_no_broker_or_external_effects(
    tmp_db, monkeypatch
):
    monkeypatch.setenv("POSITION_PENDING_KR_ENABLED", "true")
    _enable(monkeypatch, live=True, confirm=1, close_window=True)
    _seed(tmp_db, [_row(1, "005930", 100.0)])
    calls = []
    trader = FakeTrader(
        {"005930": 98.0}, holding_qty={"005930": 10}, calls=calls
    )
    agent = FakeAgent(calls)

    def fail_prepare(**_kwargs):
        calls.append("prepare-failed")
        raise sqlite3.OperationalError("injected prepare failure")

    agent._prepare_pending_kr_exit = fail_prepare
    _patch(monkeypatch, trader, agent_holder=agent, ma50=105.0)

    summary = asyncio.run(lb.run_market("KR", "run1"))

    assert summary["sold"] == 0
    assert calls == ["checked:005930", "prepare-failed"]
    assert _inflight(tmp_db) == 0
    assert _position_state(tmp_db) == "HOLDING"


def test_pending_kr_cancellation_quarantines_releases_and_propagates(
    tmp_db, monkeypatch
):
    monkeypatch.setenv("POSITION_PENDING_KR_ENABLED", "true")
    _enable(monkeypatch, live=True, confirm=1, close_window=True)
    _seed(tmp_db, [_row(1, "005930", 100.0)])
    calls = []
    trader = FakeTrader(
        {"005930": 98.0}, holding_qty={"005930": 10}, calls=calls
    )
    agent = FakeAgent(calls, pending_error=asyncio.CancelledError())
    _patch(monkeypatch, trader, agent_holder=agent, ma50=105.0)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(lb.run_market("KR", "run1"))

    assert "quarantine" in calls
    assert "tg" not in calls and "publish" not in calls
    assert _inflight(tmp_db, "UNKNOWN") == 1
    assert _position_state(tmp_db) == "QUARANTINED"


def test_pending_kr_quarantine_blocks_next_cycle_without_state_downgrade(
    tmp_db, monkeypatch
):
    monkeypatch.setenv("POSITION_PENDING_KR_ENABLED", "true")
    _enable(monkeypatch, live=True, confirm=1, close_window=True)
    _seed(tmp_db, [_row(1, "005930", 100.0)])
    calls = []
    trader = FakeTrader(
        {"005930": 98.0}, holding_qty={"005930": 10}, calls=calls
    )
    agent = FakeAgent(
        calls,
        pending_result={
            "success": False,
            "intent_status": "UNKNOWN",
            "intent_id": "intent-1",
            "order_no": None,
        },
    )
    _patch(monkeypatch, trader, agent_holder=agent, ma50=105.0)

    first = asyncio.run(lb.run_market("KR", "run-unknown-first"))
    first_calls = list(calls)
    second = asyncio.run(lb.run_market("KR", "run-unknown-second"))

    assert first["sold"] == 0 and second["sold"] == 0
    assert calls == first_calls
    assert _position_state(tmp_db) == "QUARANTINED"
    assert _inflight(tmp_db, "UNKNOWN") == 1


def test_pending_kr_post_closed_cancellation_keeps_sold_state(
    tmp_db, monkeypatch
):
    monkeypatch.setenv("POSITION_PENDING_KR_ENABLED", "true")
    _enable(monkeypatch, live=True, confirm=1, close_window=True)
    _seed(tmp_db, [_row(1, "005930", 100.0)])
    calls = []
    trader = FakeTrader(
        {"005930": 98.0}, holding_qty={"005930": 10}, calls=calls
    )
    agent = FakeAgent(calls)

    async def cancel_post_commit(_prepared):
        calls.append("postcommit")
        raise asyncio.CancelledError

    agent._run_pending_kr_exit_post_commit = cancel_post_commit
    _patch(monkeypatch, trader, agent_holder=agent, ma50=105.0)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(lb.run_market("KR", "run-post-closed-cancel"))

    assert "quarantine" not in calls
    assert _inflight(tmp_db, "FILLED") == 1
    assert _position_state(tmp_db) == "SOLD"


def test_ledger_failure_after_broker_success_records_unknown(tmp_db, monkeypatch):
    from prism_core.order_intents import IntentStore

    _enable(monkeypatch, live=True, confirm=1, close_window=True)
    _seed(tmp_db, [_row(1, "005930", 100.0)])
    calls = []
    trader = FakeTrader({"005930": 98.0}, holding_qty={"005930": 10}, calls=calls)
    _patch(monkeypatch, trader, agent_holder=FakeAgent(calls), ma50=105.0)

    def fail_result_persistence(*args, **kwargs):
        raise sqlite3.OperationalError("simulated ledger write failure")

    monkeypatch.setattr(IntentStore, "record_result", fail_result_persistence)

    summary = asyncio.run(lb.run_market("KR", "run1"))

    assert summary["sold"] == 1
    assert _inflight(tmp_db, "UNKNOWN") == 1
    assert _inflight(tmp_db, "REJECTED") == 0


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


def test_inflight_guard_blocks_second_trigger(tmp_db, monkeypatch):
    _enable(monkeypatch, live=False, confirm=1, close_window=True)
    _seed(tmp_db, [_row(1, "005930", 100.0)])
    calls = []
    trader = FakeTrader({"005930": 98.0}, calls=calls)
    _patch(monkeypatch, trader, agent_holder=FakeAgent(calls), ma50=105.0)

    asyncio.run(lb.run_market("KR", "run1"))
    summary2 = asyncio.run(lb.run_market("KR", "run2"))

    assert summary2["skipped"] == 1
    assert _inflight(tmp_db) == 1  # only the first SHADOW row


def test_owner_lock_is_exclusive(tmp_db):
    conn = lb._connect()
    lb._ensure_schema(conn)
    assert lb.claim_lock(conn, "005930", "KR", "runA") is True
    assert lb.claim_lock(conn, "005930", "KR", "runB") is False
    lb.release_lock(conn, "005930", "KR", "runA")
    assert lb.claim_lock(conn, "005930", "KR", "runB") is True
    conn.close()
