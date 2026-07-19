"""US-market tests for Trend-exit closing-confirmation trend-exit loop.

Mirrors tests/test_trend_exit_seller.py but exercises the US path: the
us_stock_holdings table and run_market("US", ...). The trader context, agent,
ma_50 fetch and LIVE regime are all monkeypatched, so this is network-free and
does NOT need the real US runtime. Per the cores-shadowing rule, KR and US tests
must run in SEPARATE pytest sessions; this file is the US session.

Run:  .venv/bin/python -m pytest prism-us/tests/test_trend_exit_seller_us.py -q
"""
import asyncio
import sys
import sqlite3
from pathlib import Path

import pytest

# tools/trend_exit_seller.py lives at repo root /tools.
_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))
import tools.trend_exit_seller as lb  # noqa: E402

MKT = "US"


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

    def _link_position_exit_intent(self, **kwargs):
        self.calls.append(f"link:{kwargs.get('legacy_holding_id')}")
        return True

    async def send_telegram_message(self, chat_id, language="en", **kwargs):
        self.calls.append("tg")
        return True


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db = tmp_path / "t.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE us_stock_holdings (id INTEGER PRIMARY KEY, ticker TEXT, company_name TEXT, "
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
        "INSERT INTO us_stock_holdings (id, ticker, company_name, buy_price, buy_date, scenario, "
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


def _streak(db, ticker, market=MKT):
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


# ── TIER1 skipped (Hardstop territory) ───────────────────────────────────────────
def test_us_tier1_hardstop_is_skipped(tmp_db, monkeypatch):
    _enable(monkeypatch, live=False, confirm=1)
    _seed(tmp_db, [_row(1, "AAPL", 100.0)])
    calls = []
    trader = FakeTrader({"AAPL": 92.0}, calls=calls)  # -8% = TIER1_ABS7
    _patch(monkeypatch, trader, agent_holder=FakeAgent(calls), ma50=0.0)

    summary = asyncio.run(lb.run_market(MKT, "run1"))

    assert summary["checked"] == 1
    assert summary["signaled"] == 0 and summary["acted"] == 0
    assert calls == []


# ── TIER1.5 dormant on ma_50=0, fires when injected ────────────────────────────
def test_us_ma50_zero_dormant(tmp_db, monkeypatch):
    _enable(monkeypatch, live=False, confirm=1)
    _seed(tmp_db, [_row(1, "AAPL", 100.0)])
    calls = []
    trader = FakeTrader({"AAPL": 98.0}, calls=calls)  # -2% loss
    _patch(monkeypatch, trader, agent_holder=FakeAgent(calls), ma50=0.0)

    summary = asyncio.run(lb.run_market(MKT, "run1"))
    assert summary["signaled"] == 0


def test_us_tier15_fires_below_ma50_losing(tmp_db, monkeypatch):
    _enable(monkeypatch, live=False, confirm=1)
    _seed(tmp_db, [_row(1, "AAPL", 100.0)])
    calls = []
    trader = FakeTrader({"AAPL": 98.0}, calls=calls)
    _patch(monkeypatch, trader, agent_holder=FakeAgent(calls), ma50=105.0)

    summary = asyncio.run(lb.run_market(MKT, "run1"))
    assert summary["signaled"] == 1 and summary["acted"] == 1 and summary["shadow"] == 1


# ── TIER2 trailing fires in weak regime ────────────────────────────────────────
def test_us_tier2_trailing_fires(tmp_db, monkeypatch):
    # buy 100, peak 120 (highest), cur 108. live weak regime -> -5% band off peak
    # trail line = 120*0.95*(1-0.005)=113.43 -> 108 <= line -> TIER2_TRAIL.
    _enable(monkeypatch, live=False, confirm=1)
    _seed(tmp_db, [_row(1, "AAPL", 100.0, highest_price=120.0)])
    calls = []
    trader = FakeTrader({"AAPL": 108.0}, calls=calls)
    _patch(monkeypatch, trader, agent_holder=FakeAgent(calls), ma50=0.0, regime="sideways")

    summary = asyncio.run(lb.run_market(MKT, "run1"))
    assert summary["signaled"] == 1 and summary["acted"] == 1


# ── gate: streak<N gated; close window confirms ────────────────────────────────
def test_us_gate_blocks_single_breach(tmp_db, monkeypatch):
    _enable(monkeypatch, live=False, confirm=2)
    _seed(tmp_db, [_row(1, "AAPL", 100.0)])
    calls = []
    trader = FakeTrader({"AAPL": 98.0}, calls=calls)
    _patch(monkeypatch, trader, agent_holder=FakeAgent(calls), ma50=105.0)

    summary = asyncio.run(lb.run_market(MKT, "run1"))
    assert summary["signaled"] == 1 and summary["acted"] == 0 and summary["gated"] == 1
    assert _streak(tmp_db, "AAPL") == 1
    assert calls == []


def test_us_close_window_confirms_single_breach(tmp_db, monkeypatch):
    _enable(monkeypatch, live=False, confirm=2, close_window=True)
    _seed(tmp_db, [_row(1, "AAPL", 100.0)])
    calls = []
    trader = FakeTrader({"AAPL": 98.0}, calls=calls)
    _patch(monkeypatch, trader, agent_holder=FakeAgent(calls), ma50=105.0)

    summary = asyncio.run(lb.run_market(MKT, "run1"))
    assert summary["acted"] == 1 and summary["shadow"] == 1


# ── SHADOW / LIVE / guards ─────────────────────────────────────────────────────
def test_us_shadow_no_agent_no_order(tmp_db, monkeypatch):
    _enable(monkeypatch, live=False, confirm=1, close_window=True)
    _seed(tmp_db, [_row(1, "AAPL", 100.0)])
    calls = []
    counter = []
    trader = FakeTrader({"AAPL": 98.0}, calls=calls)
    _patch(monkeypatch, trader, agent_holder=FakeAgent(calls),
           make_agent_counter=counter, ma50=105.0)

    summary = asyncio.run(lb.run_market(MKT, "run1"))
    assert summary["shadow"] == 1 and summary["sold"] == 0
    assert counter == [] and calls == []
    assert _inflight(tmp_db, "SHADOW") == 1


def test_us_live_sim_kis_telegram_order(tmp_db, monkeypatch):
    _enable(monkeypatch, live=True, confirm=1, close_window=True)
    monkeypatch.setattr(lb, "CHAT_ID", "chat1")
    _seed(tmp_db, [_row(1, "AAPL", 100.0)])
    calls = []
    trader = FakeTrader({"AAPL": 98.0}, holding_qty={"AAPL": 5}, calls=calls)
    _patch(monkeypatch, trader, agent_holder=FakeAgent(calls), ma50=105.0)

    summary = asyncio.run(lb.run_market(MKT, "run1"))
    assert summary["sold"] == 1
    # per-sell flush + run-end flush each invoke send_telegram_message; the run-end
    # portfolio summary is de-duplicated (portfolio_broadcast) so only ONE actual
    # portfolio message goes out in prod (see tests/test_portfolio_broadcast.py).
    assert calls == ["sim:AAPL", "kis:AAPL:5", "link:1", "tg", "tg"]
    assert _inflight(tmp_db, "FILLED") == 1


def test_us_pyramided_skipped(tmp_db, monkeypatch):
    _enable(monkeypatch, live=False, confirm=1, close_window=True)
    _seed(tmp_db, [_row(1, "AAPL", 100.0), _row(2, "AAPL", 110.0)])
    calls = []
    trader = FakeTrader({"AAPL": 80.0}, calls=calls)
    _patch(monkeypatch, trader, agent_holder=FakeAgent(calls), ma50=105.0)

    summary = asyncio.run(lb.run_market(MKT, "run1"))
    assert summary["pyramided_skipped"] == 1 and summary["checked"] == 0
    assert calls == []


def test_us_inflight_guard(tmp_db, monkeypatch):
    _enable(monkeypatch, live=False, confirm=1, close_window=True)
    _seed(tmp_db, [_row(1, "AAPL", 100.0)])
    calls = []
    trader = FakeTrader({"AAPL": 98.0}, calls=calls)
    _patch(monkeypatch, trader, agent_holder=FakeAgent(calls), ma50=105.0)

    asyncio.run(lb.run_market(MKT, "run1"))
    s2 = asyncio.run(lb.run_market(MKT, "run2"))
    assert s2["skipped"] == 1
    assert _inflight(tmp_db) == 1
