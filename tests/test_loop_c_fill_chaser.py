"""Tests for Loop C fill-chaser (tools/loop_c_fill_chaser.py) — KR side.

Network-free: the KIS unfilled-inquiry / amend / cancel / current-price calls
are all served by a FakeTrader, and the async trading context is replaced with a
FakeCtx. No real TR is ever issued. Safety-critical behaviour covered:

  - SHADOW (default): places NO real amend/cancel — only logs + audit rows.
  - Stale-order detection: an order is only chased after the grace window.
  - SELL chase: amends the limit DOWN toward the market (faster fill).
  - BUY ceiling: never chases above order_price * (1 + premium); cancels instead.
  - max-chases -> cancel (buy) / stop (sell).
  - owner_lock exclusivity: a held lock blocks the chase.
  - partial-fill reconcile: unfilled_qty comes from the live inquiry, not a cache.
  - ENABLED / LIVE gating.

Run in the KR (root) pytest session (cores-shadowing: never mix with the US
session in one process).
"""
import asyncio
import sqlite3
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
import tools.loop_c_fill_chaser as lc  # noqa: E402


# ── Fakes ──────────────────────────────────────────────────────────────────────
class FakeTrader:
    """Serves KR open-order inquiry + price + amend/cancel from in-memory state."""

    def __init__(self, open_orders, prices, calls=None,
                 amend_result=None, cancel_result=None):
        self._open_orders = open_orders          # raw KIS-style rows
        self._prices = prices
        self.calls = calls if calls is not None else []
        self._amend_result = amend_result or {"success": True, "order_no": "NEW", "message": "ok"}
        self._cancel_result = cancel_result or {"success": True, "order_no": "CXL", "message": "ok"}

    def get_revisable_orders(self):
        return list(self._open_orders)

    def get_current_price(self, stock_code):
        return {"current_price": self._prices.get(stock_code, 0)}

    def amend_order(self, stock_code, orgn_odno, limit_price, krx_fwdg_ord_orgno="",
                    dry_run=False):
        if dry_run:
            # Mirror the real wrapper's dry-run dict (tr_id/api_url/params).
            return {
                "dry_run": True, "tr_id": "TTTC0013U",
                "api_url": "/uapi/domestic-stock/v1/trading/order-rvsecncl",
                "params": {
                    "CANO": "X", "ACNT_PRDT_CD": "01",
                    "KRX_FWDG_ORD_ORGNO": krx_fwdg_ord_orgno,
                    "ORGN_ODNO": str(orgn_odno), "ORD_DVSN": "00",
                    "RVSE_CNCL_DVSN_CD": "01", "ORD_QTY": "0",
                    "ORD_UNPR": str(int(limit_price)), "QTY_ALL_ORD_YN": "Y",
                    "EXCG_ID_DVSN_CD": "KRX", "CNDT_PRIC": "",
                },
            }
        self.calls.append(f"amend:{stock_code}:{orgn_odno}:{limit_price}")
        return self._amend_result

    def cancel_order(self, stock_code, orgn_odno, krx_fwdg_ord_orgno="",
                     dry_run=False):
        if dry_run:
            return {
                "dry_run": True, "tr_id": "TTTC0013U",
                "api_url": "/uapi/domestic-stock/v1/trading/order-rvsecncl",
                "params": {
                    "CANO": "X", "ACNT_PRDT_CD": "01",
                    "KRX_FWDG_ORD_ORGNO": krx_fwdg_ord_orgno,
                    "ORGN_ODNO": str(orgn_odno), "ORD_DVSN": "00",
                    "RVSE_CNCL_DVSN_CD": "02", "ORD_QTY": "0", "ORD_UNPR": "0",
                    "QTY_ALL_ORD_YN": "Y", "EXCG_ID_DVSN_CD": "KRX", "CNDT_PRIC": "",
                },
            }
        self.calls.append(f"cancel:{stock_code}:{orgn_odno}")
        return self._cancel_result


class FakeCtx:
    def __init__(self, trader):
        self._trader = trader

    async def __aenter__(self):
        return self._trader

    async def __aexit__(self, *a):
        return False


def _row(order_no, stock_code, side_cd, psbl_qty, ord_unpr, fwdg="GNO1"):
    """A raw KIS revisable-order row (sll_buy_dvsn_cd: 01 sell / 02 buy)."""
    return {
        "order_no": order_no,
        "orgn_odno": order_no,
        "stock_code": stock_code,
        "sll_buy_dvsn_cd": side_cd,
        "psbl_qty": psbl_qty,
        "ord_unpr": ord_unpr,
        "krx_fwdg_ord_orgno": fwdg,
    }


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db = tmp_path / "loop_c.sqlite"
    monkeypatch.setattr(lc, "DB_PATH", str(db))
    return str(db)


@pytest.fixture(autouse=True)
def _fast_defaults(monkeypatch):
    """Deterministic, fast config for every test (overridable per-test)."""
    monkeypatch.setattr(lc, "LOOP_C_ENABLED", True)
    monkeypatch.setattr(lc, "LOOP_C_LIVE", False)        # SHADOW by default
    monkeypatch.setattr(lc, "GRACE_SEC", 0)              # no grace unless a test wants it
    monkeypatch.setattr(lc, "CHASE_AFTER_SEC", 0)
    monkeypatch.setattr(lc, "CHASE_STEP_PCT", 1.0)       # chase straight to market
    monkeypatch.setattr(lc, "BUY_MAX_PREMIUM_PCT", 0.02) # 2% ceiling
    monkeypatch.setattr(lc, "MAX_CHASES", 3)
    monkeypatch.setattr(lc, "CANCEL_ON_CEILING", True)
    monkeypatch.setattr(lc, "LOCK_TTL_SEC", 300)


def _patch_ctx(monkeypatch, trader):
    monkeypatch.setattr(lc, "_open_context", lambda market, account_name=None: FakeCtx(trader))


def _logs(db, action=None):
    conn = sqlite3.connect(db)
    q = "SELECT ticker, side, order_no, action, mode, old_price, new_price FROM loop_c_chase_log"
    if action:
        q += f" WHERE action='{action}'"
    rows = conn.execute(q).fetchall()
    conn.close()
    return rows


def _seed_seen(db, order_no, ticker="005930", side="SELL", market="KR"):
    """Pre-mark an order as already SEEN with an old timestamp so the grace window
    has elapsed — Loop C marks a fresh order SEEN on its first sighting and only
    acts on a subsequent cycle. Tests that exercise the act path seed this row."""
    conn = sqlite3.connect(db)
    lc._ensure_schema(conn)
    conn.execute(
        "INSERT INTO loop_c_chase_log (ticker, market, side, order_no, action, mode, "
        "old_price, new_price, unfilled_qty, chase_count, reason, loop_run_id, logged_ts) "
        "VALUES (?,?,?,?,'SEEN','SHADOW',0,0,0,0,'seed','seed','2000-01-01T00:00:00+00:00')",
        (ticker, market, side, order_no),
    )
    conn.commit()
    conn.close()


def _run(market="KR"):
    return asyncio.run(lc.run_market(market, "run-test"))


# ── Tests ───────────────────────────────────────────────────────────────────────
def test_disabled_kill_switch(tmp_db, monkeypatch):
    monkeypatch.setattr(lc, "LOOP_C_ENABLED", False)
    rc = asyncio.run(lc.main_async(["KR"]))
    assert rc == 0
    # Loop never opened a context / wrote a log.
    assert not Path(tmp_db).exists() or _logs(tmp_db) == []


def test_grace_window_skips_first_sighting(tmp_db, monkeypatch):
    """A brand-new order is recorded as SEEN and NOT chased on first cycle."""
    monkeypatch.setattr(lc, "GRACE_SEC", 999)
    trader = FakeTrader([_row("O1", "005930", "01", 10, 70000)], {"005930": 69000})
    _patch_ctx(monkeypatch, trader)
    summary = _run()
    assert summary["grace_skipped"] == 1
    assert trader.calls == []                 # no amend/cancel
    assert [r[3] for r in _logs(tmp_db)] == ["SEEN"]


def test_sell_chase_amends_toward_market_shadow(tmp_db, monkeypatch):
    """SELL: amend the limit DOWN toward the market; SHADOW => no real call."""
    trader = FakeTrader([_row("O1", "005930", "01", 10, 70000)], {"005930": 69000})
    _patch_ctx(monkeypatch, trader)
    _seed_seen(tmp_db, "O1")
    summary = _run()
    assert summary["shadow"] == 1
    assert trader.calls == []                 # SHADOW: no real amend
    amend = _logs(tmp_db, "AMEND")
    assert len(amend) == 1
    # new price moved down toward market (69000), below the original 70000.
    old_price, new_price = amend[0][5], amend[0][6]
    assert old_price == 70000
    assert 69000 <= new_price < 70000


def test_sell_chase_amends_live(tmp_db, monkeypatch):
    """LIVE SELL: a real amend_order TR is issued toward the market."""
    monkeypatch.setattr(lc, "LOOP_C_LIVE", True)
    trader = FakeTrader([_row("O1", "005930", "01", 10, 70000)], {"005930": 69000})
    _patch_ctx(monkeypatch, trader)
    _seed_seen(tmp_db, "O1")
    summary = _run()
    assert summary["amended"] == 1
    assert len(trader.calls) == 1 and trader.calls[0].startswith("amend:005930:O1:")


def test_buy_within_ceiling_chases(tmp_db, monkeypatch):
    """BUY: market just under the 2% ceiling => chase UP (no cancel)."""
    monkeypatch.setattr(lc, "LOOP_C_LIVE", True)
    # ceiling = 10000 * 1.02 = 10200; market 10100 < ceiling => chase.
    trader = FakeTrader([_row("O1", "000660", "02", 5, 10000)], {"000660": 10100})
    _patch_ctx(monkeypatch, trader)
    _seed_seen(tmp_db, "O1", ticker="000660", side="BUY")
    summary = _run()
    assert summary["amended"] == 1
    assert any(c.startswith("amend:000660:O1:") for c in trader.calls)
    assert not any(c.startswith("cancel") for c in trader.calls)


def test_buy_ceiling_hit_cancels_instead_of_overpaying(tmp_db, monkeypatch):
    """BUY: market above the 2% ceiling => CANCEL, never chase into a bad fill."""
    monkeypatch.setattr(lc, "LOOP_C_LIVE", True)
    # ceiling = 10000 * 1.02 = 10200; market 10500 > ceiling => cancel.
    trader = FakeTrader([_row("O1", "000660", "02", 5, 10000)], {"000660": 10500})
    _patch_ctx(monkeypatch, trader)
    _seed_seen(tmp_db, "O1", ticker="000660", side="BUY")
    summary = _run()
    assert summary["cancelled"] == 1
    assert any(c.startswith("cancel:000660:O1") for c in trader.calls)
    assert not any(c.startswith("amend") for c in trader.calls)
    reason = _logs(tmp_db, "CANCEL")[0]
    assert reason[3] == "CANCEL"


def test_buy_ceiling_shadow_no_real_call(tmp_db, monkeypatch):
    """BUY ceiling hit in SHADOW: logs a WOULD-CANCEL, issues no real TR."""
    trader = FakeTrader([_row("O1", "000660", "02", 5, 10000)], {"000660": 10500})
    _patch_ctx(monkeypatch, trader)
    _seed_seen(tmp_db, "O1", ticker="000660", side="BUY")
    summary = _run()
    assert summary["shadow"] == 1
    assert trader.calls == []
    assert _logs(tmp_db, "CANCEL")


def test_max_chases_cancels_buy(tmp_db, monkeypatch):
    """After MAX_CHASES amends already logged, a buy is cancelled."""
    monkeypatch.setattr(lc, "LOOP_C_LIVE", True)
    monkeypatch.setattr(lc, "MAX_CHASES", 2)
    # market under ceiling so we don't trip the ceiling branch first.
    trader = FakeTrader([_row("O1", "000660", "02", 5, 10000)], {"000660": 10050})
    _patch_ctx(monkeypatch, trader)
    # Pre-seed 2 AMEND rows so chase_count_for() == MAX_CHASES.
    lc._ensure_schema  # noqa: B018 (ensure import resolved)
    conn = sqlite3.connect(tmp_db)
    lc._ensure_schema(conn)
    for _ in range(2):
        conn.execute(
            "INSERT INTO loop_c_chase_log (ticker, market, side, order_no, action, mode, "
            "old_price, new_price, unfilled_qty, chase_count, reason, loop_run_id, logged_ts) "
            "VALUES ('000660','KR','BUY','O1','AMEND','LIVE',10000,10010,5,1,'x','seed','2000-01-01T00:00:00+00:00')"
        )
    conn.commit()
    conn.close()
    summary = _run()
    assert summary["cancelled"] == 1
    assert any(c.startswith("cancel:000660:O1") for c in trader.calls)


def test_owner_lock_blocks_chase(tmp_db, monkeypatch):
    """If another loop holds the owner_lock, Loop C defers (no amend)."""
    monkeypatch.setattr(lc, "LOOP_C_LIVE", True)
    trader = FakeTrader([_row("O1", "005930", "01", 10, 70000)], {"005930": 69000})
    _patch_ctx(monkeypatch, trader)
    conn = sqlite3.connect(tmp_db)
    lc._ensure_schema(conn)
    # Hold the lock for ticker under a different owner, far-future expiry.
    conn.execute(
        "INSERT INTO loop_a_position_state (ticker, market, state, owner_lock, lock_expires_at) "
        "VALUES ('005930','KR','HOLDING','other-owner','2999-01-01T00:00:00+00:00')"
    )
    conn.commit()
    conn.close()
    summary = _run()
    assert summary["skipped"] == 1
    assert trader.calls == []


def test_partial_fill_reconcile_uses_inquiry_remaining(tmp_db, monkeypatch):
    """unfilled_qty comes from the live inquiry's psbl_qty, not any local cache."""
    monkeypatch.setattr(lc, "LOOP_C_LIVE", True)
    # psbl_qty=3 means 3 remain after a partial fill of the original order.
    trader = FakeTrader([_row("O1", "005930", "01", 3, 70000)], {"005930": 69000})
    _patch_ctx(monkeypatch, trader)
    _seed_seen(tmp_db, "O1")
    _run()
    amend = _logs(tmp_db, "AMEND")
    assert len(amend) == 1
    # The qty fed to the (live) amend reflects the remaining 3.
    conn = sqlite3.connect(tmp_db)
    qty = conn.execute(
        "SELECT unfilled_qty FROM loop_c_chase_log WHERE action='AMEND'"
    ).fetchone()[0]
    conn.close()
    assert qty == 3


def test_fully_filled_order_is_ignored(tmp_db, monkeypatch):
    """psbl_qty == 0 (nothing left to amend) is filtered out by the inquiry layer."""
    monkeypatch.setattr(lc, "LOOP_C_LIVE", True)
    trader = FakeTrader([_row("O1", "005930", "01", 0, 70000)], {"005930": 69000})
    _patch_ctx(monkeypatch, trader)
    summary = _run()
    assert summary["open_orders"] == 0
    assert trader.calls == []


def test_inquiry_failure_degrades_to_noop(tmp_db, monkeypatch):
    """A throwing inquiry must NOT be treated as 'everything filled' — just no-op."""
    monkeypatch.setattr(lc, "LOOP_C_LIVE", True)

    class Boom(FakeTrader):
        def get_revisable_orders(self):
            raise RuntimeError("KIS down")

    trader = Boom([], {})
    _patch_ctx(monkeypatch, trader)
    summary = _run()
    assert summary["open_orders"] == 0
    assert trader.calls == []


# ── SHADOW verification helpers (dry-run payload + fill plausibility) ────────────
def test_dry_run_payload_has_required_kr_fields(tmp_db, monkeypatch):
    """dry_run=True returns the exact KR amend body without any network/order."""
    trader = FakeTrader([], {})
    order = {"ticker": "005930", "side": "SELL", "order_no": "O1",
             "ord_unpr": 70000, "unfilled_qty": 10, "krx_fwdg_ord_orgno": "GNO1"}
    payload = lc._build_dry_run_payload(trader, "KR", order, "AMEND", 69500)
    assert payload["tr_id"] and "order-rvsecncl" in payload["api_url"]
    p = payload["params"]
    for k in ("CANO", "ACNT_PRDT_CD", "KRX_FWDG_ORD_ORGNO", "ORGN_ODNO",
              "RVSE_CNCL_DVSN_CD", "ORD_QTY", "ORD_UNPR", "QTY_ALL_ORD_YN",
              "EXCG_ID_DVSN_CD"):
        assert k in p
    assert p["RVSE_CNCL_DVSN_CD"] == "01"        # amend
    assert trader.calls == []                    # no real call


def test_fill_verdict_sell_and_buy():
    """SELL fills if new<=mkt; BUY fills if new>=mkt; else UNLIKELY."""
    assert lc._fill_verdict("SELL", 69000, 69000) == "FILL_LIKELY"
    assert lc._fill_verdict("SELL", 69500, 69000) == "FILL_UNLIKELY"
    assert lc._fill_verdict("BUY", 10100, 10100) == "FILL_LIKELY"
    assert lc._fill_verdict("BUY", 10050, 10100) == "FILL_UNLIKELY"
    assert lc._fill_verdict("SELL", 0, 100) == "FILL_UNKNOWN"


def test_shadow_amend_logs_payload_and_verdict(tmp_db, monkeypatch, caplog):
    """SHADOW amend records fill-verdict + dry-run payload, issues no real TR."""
    import logging
    trader = FakeTrader([_row("O1", "005930", "01", 10, 70000)], {"005930": 69000})
    _patch_ctx(monkeypatch, trader)
    _seed_seen(tmp_db, "O1")
    with caplog.at_level(logging.INFO, logger="loop_c"):
        _run()
    assert trader.calls == []                    # SHADOW: still no real amend
    assert any("[LOOP_C][SHADOW]" in r.message and "fill=" in r.message
               for r in caplog.records)
    # The audit row persists the verdict + payload text.
    conn = sqlite3.connect(tmp_db)
    reason = conn.execute(
        "SELECT reason FROM loop_c_chase_log WHERE action='AMEND'"
    ).fetchone()[0]
    conn.close()
    assert "fill=" in reason and "payload=" in reason


def test_selftest_runs_without_api_or_orders(monkeypatch, caplog):
    """--selftest path exercises chase->payload->verdict->log; no API, no orders."""
    import logging
    # If anything tried to open a trading context, this would explode the test.
    monkeypatch.setattr(lc, "_open_context", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("selftest must NOT open a trading context")))
    with caplog.at_level(logging.INFO, logger="loop_c"):
        summary = lc.run_selftest("KR")
    assert summary["market"] == "KR"
    assert summary["amend"] == 2                 # one SELL + one BUY
    assert summary["cancel"] == 1                # the BUY also exercises cancel
    assert summary["likely"] + summary["unlikely"] == 2
    assert any("[LOOP_C][SHADOW] selftest" in r.message for r in caplog.records)
