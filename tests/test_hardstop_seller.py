"""Tests for Hardstop high-frequency hard-stop loop (tools/hardstop_seller.py).

Hardstop reuses the batch's sell path so the simulator, the real KIS account and
Telegram stay consistent. Safety-critical guards covered:
  - SHADOW (default): touches NO agent and places NO order, only logs.
  - LIVE: runs sell_stock (sim) -> async_sell_stock (KIS) -> send_telegram_message
    (telegram), in that order; reconciles qty against KIS first.
  - Pyramided tickers (>1 row) are skipped (batch owns fractional sells).
  - owner_lock exclusivity + inflight guard prevent double-selling.
  - TIER1-only: a winner is never sold by Hardstop.

Run in the KR (root) pytest session.
"""
import asyncio
import sys
import sqlite3
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
import tools.hardstop_seller as la  # noqa: E402


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
        "account_key TEXT, account_name TEXT)"
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(la, "DB_PATH", str(db))
    return str(db)


def _seed(db, rows):
    conn = sqlite3.connect(db)
    conn.executemany(
        "INSERT INTO stock_holdings (id, ticker, company_name, buy_price, buy_date, scenario, "
        "target_price, stop_loss, account_key, account_name) VALUES (?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()


def _row(id_, ticker, buy_price, stop_loss=0.0):
    return (id_, ticker, ticker, buy_price, "2026-06-01 10:00:00", "{}", 0.0,
            stop_loss, "acc1", "primary")


def _patch(monkeypatch, trader, agent_holder=None, make_agent_counter=None):
    monkeypatch.setattr(la, "_open_context", lambda market, account_name=None: FakeCtx(trader))

    async def _fake_make_agent(market):
        if make_agent_counter is not None:
            make_agent_counter.append(1)
        return agent_holder

    monkeypatch.setattr(la, "_make_agent", _fake_make_agent)


def _inflight(db, status=None):
    conn = sqlite3.connect(db)
    try:
        if status:
            return conn.execute(
                "SELECT COUNT(*) FROM loop_a_inflight_orders WHERE status=?", (status,)
            ).fetchone()[0]
        return conn.execute("SELECT COUNT(*) FROM loop_a_inflight_orders").fetchone()[0]
    finally:
        conn.close()


def test_shadow_touches_no_agent_and_no_order(tmp_db, monkeypatch):
    monkeypatch.setattr(la, "HARDSTOP_LIVE", False)
    monkeypatch.setattr(la, "HARDSTOP_ENABLED", True)
    _seed(tmp_db, [_row(1, "005930", 100.0)])  # buy 100, price 92 => -8% TIER1
    calls = []
    trader = FakeTrader({"005930": 92.0}, calls=calls)
    made = []
    _patch(monkeypatch, trader, agent_holder=FakeAgent(calls), make_agent_counter=made)

    summary = asyncio.run(la.run_market("KR", "run1"))

    assert summary["triggered"] == 1 and summary["shadow"] == 1
    assert made == []                 # agent NEVER created in shadow
    assert calls == []                # no sim, no kis, no telegram
    assert _inflight(tmp_db, "SHADOW") == 1


def test_live_order_is_sim_then_kis_then_telegram(tmp_db, monkeypatch):
    monkeypatch.setattr(la, "HARDSTOP_LIVE", True)
    monkeypatch.setattr(la, "HARDSTOP_ENABLED", True)
    monkeypatch.setattr(la, "CHAT_ID", "chat1")
    _seed(tmp_db, [_row(1, "005930", 100.0)])
    calls = []
    trader = FakeTrader({"005930": 92.0}, holding_qty={"005930": 10}, calls=calls)
    _patch(monkeypatch, trader, agent_holder=FakeAgent(calls))

    summary = asyncio.run(la.run_market("KR", "run1"))

    assert summary["sold"] == 1
    # per-sell flush + run-end flush each invoke send_telegram_message; the run-end
    # portfolio summary is de-duplicated (portfolio_broadcast) so only ONE actual
    # portfolio message goes out in prod (see tests/test_portfolio_broadcast.py).
    assert calls == ["sim:005930", "kis:005930:10", "tg", "tg"]   # exact order
    assert _inflight(tmp_db, "FILLED") == 1


def test_live_skips_kis_when_flat_but_still_closes_sim(tmp_db, monkeypatch):
    # KIS says qty 0 (batch already sold real) -> no KIS order, sim still closed.
    monkeypatch.setattr(la, "HARDSTOP_LIVE", True)
    monkeypatch.setattr(la, "HARDSTOP_ENABLED", True)
    _seed(tmp_db, [_row(1, "005930", 100.0)])
    calls = []
    trader = FakeTrader({"005930": 92.0}, holding_qty={"005930": 0}, calls=calls)
    _patch(monkeypatch, trader, agent_holder=FakeAgent(calls))

    asyncio.run(la.run_market("KR", "run1"))

    assert "sim:005930" in calls
    assert not any(c.startswith("kis:") for c in calls)   # no real order placed


def test_pyramided_ticker_is_skipped(tmp_db, monkeypatch):
    monkeypatch.setattr(la, "HARDSTOP_LIVE", False)
    monkeypatch.setattr(la, "HARDSTOP_ENABLED", True)
    _seed(tmp_db, [_row(1, "005930", 100.0), _row(2, "005930", 110.0)])  # 2 rows = pyramided
    calls = []
    trader = FakeTrader({"005930": 80.0}, calls=calls)  # deep loss, but must be skipped
    _patch(monkeypatch, trader, agent_holder=FakeAgent(calls))

    summary = asyncio.run(la.run_market("KR", "run1"))

    assert summary["pyramided_skipped"] == 1
    assert summary["checked"] == 0 and summary["triggered"] == 0
    assert calls == []


def test_shadow_record_does_not_block_later_evaluation(tmp_db, monkeypatch):
    monkeypatch.setattr(la, "HARDSTOP_LIVE", False)
    monkeypatch.setattr(la, "HARDSTOP_ENABLED", True)
    _seed(tmp_db, [_row(1, "005930", 100.0)])
    calls = []
    trader = FakeTrader({"005930": 92.0}, calls=calls)
    _patch(monkeypatch, trader, agent_holder=FakeAgent(calls))

    asyncio.run(la.run_market("KR", "run1"))
    summary2 = asyncio.run(la.run_market("KR", "run2"))

    assert summary2["skipped"] == 0  # nosec B101
    assert summary2["shadow"] == 1  # nosec B101
    assert _inflight(tmp_db) == 2  # nosec B101


def test_owner_lock_is_exclusive(tmp_db):
    conn = la._connect()
    la._ensure_schema(conn)
    assert la.claim_lock(conn, "005930", "KR", "runA") is True
    assert la.claim_lock(conn, "005930", "KR", "runB") is False
    la.release_lock(conn, "005930", "KR", "runA")
    assert la.claim_lock(conn, "005930", "KR", "runB") is True
    conn.close()


def test_winner_not_sold_tier1_only(tmp_db, monkeypatch):
    monkeypatch.setattr(la, "HARDSTOP_LIVE", False)
    monkeypatch.setattr(la, "HARDSTOP_ENABLED", True)
    _seed(tmp_db, [_row(1, "005930", 100.0)])
    calls = []
    trader = FakeTrader({"005930": 120.0}, calls=calls)  # +20% winner
    _patch(monkeypatch, trader, agent_holder=FakeAgent(calls))

    summary = asyncio.run(la.run_market("KR", "run1"))

    assert summary["triggered"] == 0
    assert calls == []


def test_disabled_flag_is_noop(tmp_db, monkeypatch):
    monkeypatch.setattr(la, "HARDSTOP_ENABLED", False)
    rc = asyncio.run(la.main_async(["KR"]))
    assert rc == 0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
