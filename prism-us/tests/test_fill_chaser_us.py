"""Tests for Fill-chaser fill-chaser (tools/fill_chaser.py) — US side.

Network-free: the KIS overseas unfilled-inquiry / amend / cancel / current-price
calls are served by a FakeTrader and the async context by a FakeCtx. No real TR
is ever issued. Mirrors the KR test coverage with the US dict shape
(get_unfilled_orders -> nccs_qty/ticker/ord_unpr/exchange; cent-precision prices).

Covered: SHADOW default (no real call), stale/grace detection, SELL chase down,
BUY ceiling -> cancel (never overpay), max-chases -> cancel, owner_lock
exclusivity, partial-fill reconcile, ENABLED/LIVE gating.

⚠️ cores-shadowing: run this in its OWN pytest session, never in the same process
as the KR (root) session. We mock _open_context so no trading module is imported.
"""
import asyncio
import sqlite3
import sys
from pathlib import Path

import pytest

# tools/ lives under the repo root (one level above prism-us/).
_PRISM_US = Path(__file__).resolve().parent.parent
_ROOT = _PRISM_US.parent
sys.path.insert(0, str(_ROOT))
import tools.fill_chaser as lc  # noqa: E402

MARKET = "US"


# ── Fakes ──────────────────────────────────────────────────────────────────────
class FakeTrader:
    def __init__(self, open_orders, prices, calls=None,
                 amend_result=None, cancel_result=None):
        self._open_orders = open_orders
        self._prices = prices
        self.calls = calls if calls is not None else []
        self._amend_result = amend_result or {"success": True, "order_no": "NEW", "message": "ok"}
        self._cancel_result = cancel_result or {"success": True, "order_no": "CXL", "message": "ok"}

    def get_unfilled_orders(self):
        return list(self._open_orders)

    def get_current_price(self, ticker):
        return {"current_price": self._prices.get(ticker, 0)}

    def amend_order(self, ticker, orgn_odno, limit_price, quantity, exchange=None,
                    dry_run=False):
        if dry_run:
            return {
                "dry_run": True, "tr_id": "TTTT1004U",
                "api_url": "/uapi/overseas-stock/v1/trading/order-rvsecncl",
                "params": {
                    "CANO": "X", "ACNT_PRDT_CD": "01",
                    "OVRS_EXCG_CD": exchange or "NASD", "PDNO": ticker.upper(),
                    "ORGN_ODNO": str(orgn_odno), "RVSE_CNCL_DVSN_CD": "01",
                    "ORD_QTY": str(int(quantity)),
                    "OVRS_ORD_UNPR": f"{limit_price:.2f}", "ORD_SVR_DVSN_CD": "0",
                },
            }
        self.calls.append(f"amend:{ticker}:{orgn_odno}:{limit_price}:{quantity}:{exchange}")
        return self._amend_result

    def cancel_order(self, ticker, orgn_odno, quantity, exchange=None,
                     dry_run=False):
        if dry_run:
            return {
                "dry_run": True, "tr_id": "TTTT1004U",
                "api_url": "/uapi/overseas-stock/v1/trading/order-rvsecncl",
                "params": {
                    "CANO": "X", "ACNT_PRDT_CD": "01",
                    "OVRS_EXCG_CD": exchange or "NASD", "PDNO": ticker.upper(),
                    "ORGN_ODNO": str(orgn_odno), "RVSE_CNCL_DVSN_CD": "02",
                    "ORD_QTY": str(int(quantity)),
                    "OVRS_ORD_UNPR": "0", "ORD_SVR_DVSN_CD": "0",
                },
            }
        self.calls.append(f"cancel:{ticker}:{orgn_odno}:{quantity}:{exchange}")
        return self._cancel_result


class FakeCtx:
    def __init__(self, trader):
        self._trader = trader

    async def __aenter__(self):
        return self._trader

    async def __aexit__(self, *a):
        return False


def _row(order_no, ticker, side_cd, nccs_qty, ord_unpr, exchange="NASD"):
    """A raw KIS overseas unfilled row (sll_buy_dvsn_cd: 01 sell / 02 buy)."""
    return {
        "order_no": order_no,
        "ticker": ticker,
        "sll_buy_dvsn_cd": side_cd,
        "nccs_qty": nccs_qty,
        "ord_unpr": ord_unpr,
        "exchange": exchange,
    }


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db = tmp_path / "loop_c_us.sqlite"
    monkeypatch.setattr(lc, "DB_PATH", str(db))
    return str(db)


@pytest.fixture(autouse=True)
def _fast_defaults(monkeypatch):
    monkeypatch.setattr(lc, "FILL_CHASER_ENABLED", True)
    monkeypatch.setattr(lc, "FILL_CHASER_LIVE", False)
    monkeypatch.setattr(lc, "GRACE_SEC", 0)
    monkeypatch.setattr(lc, "CHASE_AFTER_SEC", 0)
    monkeypatch.setattr(lc, "CHASE_STEP_PCT", 1.0)
    monkeypatch.setattr(lc, "BUY_MAX_PREMIUM_PCT", 0.02)
    monkeypatch.setattr(lc, "MAX_CHASES", 3)
    monkeypatch.setattr(lc, "CANCEL_ON_CEILING", True)
    monkeypatch.setattr(lc, "LOCK_TTL_SEC", 300)


def _patch_ctx(monkeypatch, trader):
    from prism_core.execution_service import ExecutionService

    monkeypatch.setattr(
        lc,
        "_open_context",
        lambda market, account_name=None: ExecutionService(FakeCtx(trader)),
    )


def _logs(db, action=None):
    conn = sqlite3.connect(db)
    q = "SELECT ticker, side, order_no, action, mode, old_price, new_price FROM loop_c_chase_log"
    if action:
        q += f" WHERE action='{action}'"
    rows = conn.execute(q).fetchall()
    conn.close()
    return rows


def _seed_seen(db, order_no, ticker="AAPL", side="SELL"):
    conn = sqlite3.connect(db)
    lc._ensure_schema(conn)
    conn.execute(
        "INSERT INTO loop_c_chase_log (ticker, market, side, order_no, action, mode, "
        "old_price, new_price, unfilled_qty, chase_count, reason, loop_run_id, logged_ts) "
        "VALUES (?,?,?,?,'SEEN','SHADOW',0,0,0,0,'seed','seed','2000-01-01T00:00:00+00:00')",
        (ticker, MARKET, side, order_no),
    )
    conn.commit()
    conn.close()


def _run():
    return asyncio.run(lc.run_market(MARKET, "run-test-us"))


# ── Tests ───────────────────────────────────────────────────────────────────────
def test_disabled_kill_switch(tmp_db, monkeypatch):
    monkeypatch.setattr(lc, "FILL_CHASER_ENABLED", False)
    rc = asyncio.run(lc.main_async([MARKET]))
    assert rc == 0
    assert not Path(tmp_db).exists() or _logs(tmp_db) == []


def test_grace_window_skips_first_sighting(tmp_db, monkeypatch):
    monkeypatch.setattr(lc, "GRACE_SEC", 999)
    trader = FakeTrader([_row("O1", "AAPL", "01", 10, 190.00)], {"AAPL": 189.00})
    _patch_ctx(monkeypatch, trader)
    summary = _run()
    assert summary["grace_skipped"] == 1
    assert trader.calls == []
    assert [r[3] for r in _logs(tmp_db)] == ["SEEN"]


def test_sell_chase_amends_toward_market_shadow(tmp_db, monkeypatch):
    trader = FakeTrader([_row("O1", "AAPL", "01", 10, 190.00)], {"AAPL": 189.00})
    _patch_ctx(monkeypatch, trader)
    _seed_seen(tmp_db, "O1")
    summary = _run()
    assert summary["shadow"] == 1
    assert trader.calls == []                 # SHADOW: no real amend
    amend = _logs(tmp_db, "AMEND")
    assert len(amend) == 1
    old_price, new_price = amend[0][5], amend[0][6]
    assert old_price == 190.00
    assert 189.00 <= new_price < 190.00


def test_sell_chase_amends_live(tmp_db, monkeypatch):
    monkeypatch.setattr(lc, "FILL_CHASER_LIVE", True)
    trader = FakeTrader([_row("O1", "AAPL", "01", 10, 190.00)], {"AAPL": 189.00})
    _patch_ctx(monkeypatch, trader)
    _seed_seen(tmp_db, "O1")
    summary = _run()
    assert summary["amended"] == 1
    assert len(trader.calls) == 1 and trader.calls[0].startswith("amend:AAPL:O1:")
    # exchange threaded through to the wrapper.
    assert trader.calls[0].endswith(":NASD")


def test_buy_within_ceiling_chases(tmp_db, monkeypatch):
    monkeypatch.setattr(lc, "FILL_CHASER_LIVE", True)
    # ceiling = 100 * 1.02 = 102; market 101 < ceiling => chase up.
    trader = FakeTrader([_row("O1", "TSLA", "02", 5, 100.00)], {"TSLA": 101.00})
    _patch_ctx(monkeypatch, trader)
    _seed_seen(tmp_db, "O1", ticker="TSLA", side="BUY")
    summary = _run()
    assert summary["amended"] == 1
    assert any(c.startswith("amend:TSLA:O1:") for c in trader.calls)
    assert not any(c.startswith("cancel") for c in trader.calls)


def test_buy_ceiling_hit_cancels_instead_of_overpaying(tmp_db, monkeypatch):
    monkeypatch.setattr(lc, "FILL_CHASER_LIVE", True)
    # ceiling = 100 * 1.02 = 102; market 105 > ceiling => cancel.
    trader = FakeTrader([_row("O1", "TSLA", "02", 5, 100.00)], {"TSLA": 105.00})
    _patch_ctx(monkeypatch, trader)
    _seed_seen(tmp_db, "O1", ticker="TSLA", side="BUY")
    summary = _run()
    assert summary["cancelled"] == 1
    assert any(c.startswith("cancel:TSLA:O1") for c in trader.calls)
    assert not any(c.startswith("amend") for c in trader.calls)


def test_buy_ceiling_shadow_no_real_call(tmp_db, monkeypatch):
    trader = FakeTrader([_row("O1", "TSLA", "02", 5, 100.00)], {"TSLA": 105.00})
    _patch_ctx(monkeypatch, trader)
    _seed_seen(tmp_db, "O1", ticker="TSLA", side="BUY")
    summary = _run()
    assert summary["shadow"] == 1
    assert trader.calls == []
    assert _logs(tmp_db, "CANCEL")


def test_max_chases_cancels_buy(tmp_db, monkeypatch):
    monkeypatch.setattr(lc, "FILL_CHASER_LIVE", True)
    monkeypatch.setattr(lc, "MAX_CHASES", 2)
    trader = FakeTrader([_row("O1", "TSLA", "02", 5, 100.00)], {"TSLA": 100.50})
    _patch_ctx(monkeypatch, trader)
    conn = sqlite3.connect(tmp_db)
    lc._ensure_schema(conn)
    for _ in range(2):
        conn.execute(
            "INSERT INTO loop_c_chase_log (ticker, market, side, order_no, action, mode, "
            "old_price, new_price, unfilled_qty, chase_count, reason, loop_run_id, logged_ts) "
            "VALUES ('TSLA','US','BUY','O1','AMEND','LIVE',100,100.1,5,1,'x','seed','2000-01-01T00:00:00+00:00')"
        )
    conn.commit()
    conn.close()
    summary = _run()
    assert summary["cancelled"] == 1
    assert any(c.startswith("cancel:TSLA:O1") for c in trader.calls)


def test_owner_lock_blocks_chase(tmp_db, monkeypatch):
    monkeypatch.setattr(lc, "FILL_CHASER_LIVE", True)
    trader = FakeTrader([_row("O1", "AAPL", "01", 10, 190.00)], {"AAPL": 189.00})
    _patch_ctx(monkeypatch, trader)
    conn = sqlite3.connect(tmp_db)
    lc._ensure_schema(conn)
    conn.execute(
        "INSERT INTO loop_a_position_state (ticker, market, state, owner_lock, lock_expires_at) "
        "VALUES ('AAPL','US','HOLDING','other-owner','2999-01-01T00:00:00+00:00')"
    )
    conn.commit()
    conn.close()
    summary = _run()
    assert summary["skipped"] == 1
    assert trader.calls == []


def test_partial_fill_reconcile_uses_inquiry_remaining(tmp_db, monkeypatch):
    monkeypatch.setattr(lc, "FILL_CHASER_LIVE", True)
    trader = FakeTrader([_row("O1", "AAPL", "01", 4, 190.00)], {"AAPL": 189.00})
    _patch_ctx(monkeypatch, trader)
    _seed_seen(tmp_db, "O1")
    _run()
    conn = sqlite3.connect(tmp_db)
    qty = conn.execute(
        "SELECT unfilled_qty FROM loop_c_chase_log WHERE action='AMEND'"
    ).fetchone()[0]
    conn.close()
    assert qty == 4
    # qty also threaded into the live amend call.
    assert any(":4:" in c for c in trader.calls)


def test_fully_filled_order_is_ignored(tmp_db, monkeypatch):
    monkeypatch.setattr(lc, "FILL_CHASER_LIVE", True)
    trader = FakeTrader([_row("O1", "AAPL", "01", 0, 190.00)], {"AAPL": 189.00})
    _patch_ctx(monkeypatch, trader)
    summary = _run()
    assert summary["open_orders"] == 0
    assert trader.calls == []


def test_inquiry_failure_degrades_to_noop(tmp_db, monkeypatch):
    monkeypatch.setattr(lc, "FILL_CHASER_LIVE", True)

    class Boom(FakeTrader):
        def get_unfilled_orders(self):
            raise RuntimeError("KIS down")

    trader = Boom([], {})
    _patch_ctx(monkeypatch, trader)
    summary = _run()
    assert summary["open_orders"] == 0
    assert trader.calls == []


# ── SHADOW verification helpers (dry-run payload + fill plausibility) ────────────
def test_dry_run_payload_has_required_us_fields(tmp_db, monkeypatch):
    """dry_run=True returns the exact US amend body incl. the TODO(live-validate)
    fields (PDNO/ORD_SVR_DVSN_CD/OVRS_EXCG_CD/OVRS_ORD_UNPR) — no network/order."""
    from prism_core.execution_service import ExecutionService

    trader = FakeTrader([], {})
    order = {"ticker": "AAPL", "side": "SELL", "order_no": "O1",
             "ord_unpr": 190.00, "unfilled_qty": 10, "exchange": "NASD",
             "krx_fwdg_ord_orgno": ""}
    payload = lc._build_dry_run_payload(
        ExecutionService(trader), "US", order, "AMEND", 189.50
    )
    assert payload["tr_id"] and "order-rvsecncl" in payload["api_url"]
    p = payload["params"]
    for k in ("CANO", "ACNT_PRDT_CD", "OVRS_EXCG_CD", "PDNO", "ORGN_ODNO",
              "RVSE_CNCL_DVSN_CD", "ORD_QTY", "OVRS_ORD_UNPR", "ORD_SVR_DVSN_CD"):
        assert k in p
    assert p["PDNO"] == "AAPL" and p["OVRS_EXCG_CD"] == "NASD"
    assert p["RVSE_CNCL_DVSN_CD"] == "01"
    assert trader.calls == []


def test_fill_verdict_sell_and_buy_us():
    assert lc._fill_verdict("SELL", 189.00, 189.00) == "FILL_LIKELY"
    assert lc._fill_verdict("SELL", 189.50, 189.00) == "FILL_UNLIKELY"
    assert lc._fill_verdict("BUY", 101.00, 101.00) == "FILL_LIKELY"
    assert lc._fill_verdict("BUY", 100.50, 101.00) == "FILL_UNLIKELY"


def test_shadow_amend_logs_payload_and_verdict(tmp_db, monkeypatch, caplog):
    import logging
    trader = FakeTrader([_row("O1", "AAPL", "01", 10, 190.00)], {"AAPL": 189.00})
    _patch_ctx(monkeypatch, trader)
    _seed_seen(tmp_db, "O1")
    with caplog.at_level(logging.INFO, logger="fill_chaser"):
        _run()
    assert trader.calls == []
    assert any("[FILL_CHASER][SHADOW]" in r.message and "fill=" in r.message
               for r in caplog.records)
    conn = sqlite3.connect(tmp_db)
    reason = conn.execute(
        "SELECT reason FROM loop_c_chase_log WHERE action='AMEND'"
    ).fetchone()[0]
    conn.close()
    assert "fill=" in reason and "payload=" in reason


def test_selftest_runs_without_api_or_orders(monkeypatch, caplog):
    import logging
    monkeypatch.setattr(lc, "_open_context", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("selftest must NOT open a trading context")))
    with caplog.at_level(logging.INFO, logger="fill_chaser"):
        summary = lc.run_selftest("US")
    assert summary["market"] == "US"
    assert summary["amend"] == 2
    assert summary["cancel"] == 1
    assert summary["likely"] + summary["unlikely"] == 2
    assert any("[FILL_CHASER][SHADOW] selftest" in r.message for r in caplog.records)
