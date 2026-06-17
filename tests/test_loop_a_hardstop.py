"""Tests for Loop A high-frequency hard-stop loop (tools/loop_a_hardstop.py).

Safety-critical guards covered:
  - SHADOW mode (default) places NO real order, but logs an intended sell.
  - LIVE mode places a market sell and reconciles qty against KIS first.
  - owner_lock prevents two concurrent runs from double-selling.
  - inflight guard prevents re-issuing a sell for a ticker already in flight.
  - TIER1-only: trailing/target winners are NOT sold by Loop A.

Run in the KR (root) pytest session.
"""
import asyncio
import os
import sys
import sqlite3
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
import tools.loop_a_hardstop as la  # noqa: E402


# ── Fakes ──────────────────────────────────────────────────────────────────────
class FakeTrader:
    def __init__(self, portfolio, holding_qty=None, sell_result=None):
        self._portfolio = portfolio
        self._holding_qty = holding_qty or {}
        self._sell_result = sell_result or {"success": True, "order_no": "ORD1", "message": "ok"}
        self.sell_calls = []

    def get_portfolio(self):
        return self._portfolio

    def get_holding_quantity(self, ticker):
        return self._holding_qty.get(ticker, 0)

    async def async_sell_stock(self, ticker, exchange=None, timeout=30.0,
                               limit_price=None, use_moo=False, quantity=None):
        self.sell_calls.append((ticker, quantity))
        return self._sell_result


class FakeCtx:
    def __init__(self, trader):
        self._trader = trader

    async def __aenter__(self):
        return self._trader

    async def __aexit__(self, *a):
        return False


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db = tmp_path / "t.sqlite"
    # seed holdings table so load_stop_map works
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE stock_holdings (ticker TEXT, stop_loss REAL)")
    conn.execute("INSERT INTO stock_holdings VALUES ('005930', 0)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(la, "DB_PATH", str(db))
    return str(db)


def _patch_trader(monkeypatch, trader):
    monkeypatch.setattr(la, "_open_context", lambda market: FakeCtx(trader))


def _count_inflight(db, status=None):
    conn = sqlite3.connect(db)
    try:
        if status:
            return conn.execute(
                "SELECT COUNT(*) FROM loop_a_inflight_orders WHERE status=?", (status,)
            ).fetchone()[0]
        return conn.execute("SELECT COUNT(*) FROM loop_a_inflight_orders").fetchone()[0]
    finally:
        conn.close()


# a -8% loser (buy 100, now 92) -> TIER1 abs-7 fires
_LOSER = [{"stock_code": "005930", "quantity": 10, "avg_price": 100.0, "current_price": 92.0}]


def test_shadow_mode_places_no_order(tmp_db, monkeypatch):
    monkeypatch.setattr(la, "LOOP_A_LIVE", False)
    monkeypatch.setattr(la, "LOOP_A_ENABLED", True)
    trader = FakeTrader(_LOSER)
    _patch_trader(monkeypatch, trader)

    summary = asyncio.run(la.run_market("KR", "run1"))

    assert summary["triggered"] == 1
    assert summary["shadow"] == 1
    assert trader.sell_calls == []  # NO real order in shadow mode
    assert _count_inflight(tmp_db, "SHADOW") == 1


def test_live_mode_sells_and_reconciles(tmp_db, monkeypatch):
    monkeypatch.setattr(la, "LOOP_A_LIVE", True)
    monkeypatch.setattr(la, "LOOP_A_ENABLED", True)
    trader = FakeTrader(_LOSER, holding_qty={"005930": 10})
    _patch_trader(monkeypatch, trader)

    summary = asyncio.run(la.run_market("KR", "run1"))

    assert summary["sold"] == 1
    assert trader.sell_calls == [("005930", 10)]  # market sell of full reconciled qty
    assert _count_inflight(tmp_db, "FILLED") == 1


def test_live_mode_skips_when_already_flat_at_kis(tmp_db, monkeypatch):
    # KIS says qty 0 (batch already sold) -> Loop A must NOT sell.
    monkeypatch.setattr(la, "LOOP_A_LIVE", True)
    monkeypatch.setattr(la, "LOOP_A_ENABLED", True)
    trader = FakeTrader(_LOSER, holding_qty={"005930": 0})
    _patch_trader(monkeypatch, trader)

    summary = asyncio.run(la.run_market("KR", "run1"))

    assert trader.sell_calls == []
    assert summary["sold"] == 0


def test_inflight_guard_blocks_second_trigger(tmp_db, monkeypatch):
    monkeypatch.setattr(la, "LOOP_A_LIVE", False)
    monkeypatch.setattr(la, "LOOP_A_ENABLED", True)
    trader = FakeTrader(_LOSER)
    _patch_trader(monkeypatch, trader)

    asyncio.run(la.run_market("KR", "run1"))      # creates SHADOW inflight
    summary2 = asyncio.run(la.run_market("KR", "run2"))  # different run id

    # second cycle sees the open SHADOW inflight and skips
    assert summary2["skipped"] == 1
    assert _count_inflight(tmp_db) == 1


def test_owner_lock_is_exclusive(tmp_db, monkeypatch):
    conn = la._connect()
    la._ensure_schema(conn)
    assert la.claim_lock(conn, "005930", "KR", "runA") is True
    # second claimant cannot take a live lock
    assert la.claim_lock(conn, "005930", "KR", "runB") is False
    la.release_lock(conn, "005930", "KR", "runA")
    assert la.claim_lock(conn, "005930", "KR", "runB") is True
    conn.close()


def test_winner_not_sold_tier1_only(tmp_db, monkeypatch):
    # +20% winner well above a trailing peak: evaluate_oneil_sell might trail, but
    # Loop A is TIER1-only and must HOLD.
    monkeypatch.setattr(la, "LOOP_A_LIVE", False)
    monkeypatch.setattr(la, "LOOP_A_ENABLED", True)
    winner = [{"stock_code": "005930", "quantity": 10, "avg_price": 100.0, "current_price": 120.0}]
    trader = FakeTrader(winner)
    _patch_trader(monkeypatch, trader)

    summary = asyncio.run(la.run_market("KR", "run1"))

    assert summary["triggered"] == 0
    assert trader.sell_calls == []


def test_disabled_flag_is_noop(tmp_db, monkeypatch):
    monkeypatch.setattr(la, "LOOP_A_ENABLED", False)
    rc = asyncio.run(la.main_async(["KR"]))
    assert rc == 0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
