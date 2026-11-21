"""Post-close fill confirmation (tools/reconcile_fills.py): broker facts → entry.fill_reconciled.

Fake broker rows only; no KIS call, no order TR. A failed or ambiguous inquiry stays MISSING.
"""
import asyncio
import importlib.util
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ACCOUNT = "prod:12345678:01"
PROFILE_ACCOUNTS = {ACCOUNT: {"account_key": ACCOUNT, "name": "main"}}


def _tool():
    spec = importlib.util.spec_from_file_location("reconcile_fills_under_test", ROOT / "tools" / "reconcile_fills.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _db(path, intents, positions=()):
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE order_intents (id TEXT PRIMARY KEY, market TEXT, account_id TEXT, symbol TEXT, side TEXT,
            source TEXT, status TEXT, quantity INTEGER, limit_price TEXT, source_decision_id TEXT,
            source_position_id TEXT, created_at TEXT);
        CREATE TABLE broker_orders (id TEXT PRIMARY KEY, intent_id TEXT, broker_order_id TEXT, accepted INTEGER,
            submitted_quantity INTEGER, submitted_at TEXT);
        CREATE TABLE positions (id TEXT PRIMARY KEY, entry_intent_id TEXT);
    """)
    for item in intents:
        conn.execute("INSERT INTO order_intents VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                     (item["id"], item.get("market", "KR"), item.get("account", ACCOUNT), item["symbol"], "BUY",
                      item.get("source", "kr_enhanced_batch"), item.get("status", "SUBMITTED"), None,
                      item.get("limit", "100"), item.get("decision"), item.get("position"), item["created"]))
        if item.get("odno") is not None or item.get("qty") is not None:
            conn.execute("INSERT INTO broker_orders VALUES (?,?,?,?,?,?)",
                         ("b-" + item["id"], item["id"], item.get("odno"), 1, item.get("qty"), item["created"]))
    for position_id, entry_intent in positions:
        conn.execute("INSERT INTO positions VALUES (?,?)", (position_id, entry_intent))
    conn.commit()
    conn.close()


def _kr(odno, pdno, ord_qty, filled, amount, *, orig="", tmd="100500", dt="20261021", rmn=None):
    return {"ord_dt": dt, "ord_tmd": tmd, "odno": odno, "orgn_odno": orig, "sll_buy_dvsn_cd": "02", "pdno": pdno,
            "ord_qty": str(ord_qty), "ord_unpr": "100", "avg_prvs": str(amount / filled if filled else 0),
            "tot_ccld_qty": str(filled), "tot_ccld_amt": str(amount), "rmn_qty": str(ord_qty - filled if rmn is None else rmn),
            "rjct_qty": "0", "cncl_yn": "N"}


class _Service:
    def __init__(self, rows, ok=True, key=ACCOUNT, error=None, calls=None):
        self.rows, self.ok, self.account_key, self.error, self.calls = rows, ok, key, error, calls

    async def __aenter__(self):
        if self.error:
            raise self.error
        return self

    async def __aexit__(self, *args):
        return False

    def get_daily_executions_checked(self, start, end):
        if self.calls is not None:
            self.calls.append((start, end))
        return self.ok, self.rows


def _run(tmp_path, monkeypatch, intents, rows, *, market="KR", ok=True, positions=(), error=None, dry_run=False,
         calls=None, accounts=PROFILE_ACCOUNTS):
    db = tmp_path / "t.sqlite"
    if not db.exists():
        _db(db, intents, positions)
    spool = tmp_path / "events.jsonl"
    monkeypatch.setenv("PRISM_OBSERVABILITY_SPOOL", str(spool))
    tool = _tool()
    summary = asyncio.run(tool.run(market, "20261021", db_path=db, dry_run=dry_run, state_path=tmp_path / "state.json",
                                   accounts=accounts,
                                   service_factory=lambda m, name: _Service(rows, ok=ok, error=error, calls=calls)))
    events = [json.loads(line) for line in spool.read_text().splitlines()] if spool.exists() else []
    return summary, [e for e in events if e["event_type"] == "entry.fill_reconciled"]


KR_ENTRY = {"id": "i1", "symbol": "053800", "decision": "dec-1", "odno": "0000123", "qty": 4,
            "created": "2026-10-21T01:05:00+00:00", "limit": "88800"}


def test_kr_confirmed_fill_on_decision_trace(tmp_path, monkeypatch):
    from prism_core.trace_ids import trade_trace
    summary, events = _run(tmp_path, monkeypatch, [KR_ENTRY], [_kr("0000123", "053800", 4, 4, 354000)])
    assert summary["statuses"] == {"CONFIRMED": 1}
    event = events[0]
    attrs = event["attributes"]
    assert event["trace_id"] == trade_trace("KR", decision_id="dec-1")
    assert event["decision_id"] == "dec-1"
    assert attrs["fill_status"] == "CONFIRMED" and attrs["match_basis"] == "order_no" and attrs["order_no_matched"]
    assert attrs["filled_qty"] == 4 and attrs["avg_fill_price"] == pytest.approx(88500)
    assert attrs["fill_vs_limit_pct"] == pytest.approx(-0.338, abs=1e-3)
    prov = attrs["fill_provenance"]
    assert prov["status"] == "CONFIRMED" and prov["confirmed_fill_quantity"] == 4
    assert prov["confirmed_fill_price"] == pytest.approx(88500) and prov["evidence_source"] == "kis_inquire_daily_ccld"


def test_partial_and_unfilled_at_close(tmp_path, monkeypatch):
    intents = [KR_ENTRY, dict(KR_ENTRY, id="i2", symbol="028670", odno="0000200", qty=64, decision="dec-2")]
    rows = [_kr("0000123", "053800", 4, 1, 88000), _kr("0000200", "028670", 64, 0, 0)]
    summary, events = _run(tmp_path, monkeypatch, intents, rows)
    by_symbol = {e["ticker"]: e["attributes"] for e in events}
    assert by_symbol["053800"]["fill_status"] == "PARTIAL" and by_symbol["053800"]["remaining_qty"] == 3
    assert by_symbol["053800"]["fill_provenance"]["status"] == "PARTIAL"
    assert by_symbol["028670"]["fill_status"] == "NOT_FILLED"
    assert by_symbol["028670"]["fill_provenance"]["status"] == "CANCELLED"
    assert by_symbol["028670"]["fill_provenance"]["confirmed_fill_price"] is None


def test_query_failure_is_missing_not_unfilled(tmp_path, monkeypatch):
    summary, events = _run(tmp_path, monkeypatch, [KR_ENTRY], [], ok=False)
    attrs = events[0]["attributes"]
    assert attrs["fill_status"] == "MISSING" and attrs["reason_code"] == "BROKER_QUERY_FAILED"
    assert attrs["filled_qty"] is None and attrs["fill_provenance"]["status"] == "UNKNOWN"


def test_broker_exception_and_unconfigured_account_are_missing(tmp_path, monkeypatch):
    summary, events = _run(tmp_path, monkeypatch, [KR_ENTRY], [], error=RuntimeError("token"))
    assert events[0]["attributes"]["reason_code"] == "BROKER_QUERY_FAILED"
    other = tmp_path / "other"
    other.mkdir()
    summary, events = _run(other, monkeypatch, [KR_ENTRY], [], accounts={})
    assert events[0]["attributes"]["reason_code"] == "ACCOUNT_NOT_CONFIGURED"


def test_order_absent_from_authoritative_answer_is_missing(tmp_path, monkeypatch):
    summary, events = _run(tmp_path, monkeypatch, [KR_ENTRY], [_kr("0000999", "005930", 4, 4, 400)])
    attrs = events[0]["attributes"]
    assert attrs["fill_status"] == "MISSING" and attrs["reason_code"] == "ORDER_NOT_FOUND"


def test_blank_quantity_makes_answer_non_authoritative(tmp_path, monkeypatch):
    row = _kr("0000123", "053800", 4, 4, 354000)
    row["tot_ccld_qty"] = ""
    summary, events = _run(tmp_path, monkeypatch, [KR_ENTRY], [row])
    assert events[0]["attributes"]["reason_code"] == "BROKER_ROWS_INVALID"


def test_amended_order_chain_sums_fills(tmp_path, monkeypatch):
    rows = [_kr("0000123", "053800", 4, 1, 88000, rmn=0), _kr("0000130", "053800", 3, 3, 267000, orig="0000123")]
    summary, events = _run(tmp_path, monkeypatch, [KR_ENTRY], rows)
    attrs = events[0]["attributes"]
    assert attrs["fill_status"] == "CONFIRMED" and attrs["match_basis"] == "order_no+amend_chain"
    assert attrs["filled_qty"] == 4 and attrs["avg_fill_price"] == pytest.approx(88750)


def test_no_order_number_matches_by_symbol_qty_and_time(tmp_path, monkeypatch):
    intent = dict(KR_ENTRY, odno=None)  # 10:05 KST
    rows = [_kr("0000777", "053800", 4, 4, 354000, tmd="100700"), _kr("0000778", "053800", 4, 4, 354000, tmd="140000")]
    summary, events = _run(tmp_path, monkeypatch, [intent], rows)
    attrs = events[0]["attributes"]
    assert attrs["fill_status"] == "CONFIRMED" and attrs["match_basis"] == "symbol_side_qty_time"
    assert attrs["order_no_matched"] is False


def test_two_candidates_without_order_number_is_ambiguous(tmp_path, monkeypatch):
    intent = dict(KR_ENTRY, odno=None)
    rows = [_kr("0000777", "053800", 4, 4, 354000, tmd="100400"), _kr("0000778", "053800", 4, 4, 354000, tmd="100600")]
    summary, events = _run(tmp_path, monkeypatch, [intent], rows)
    assert events[0]["attributes"]["reason_code"] == "ORDER_MATCH_AMBIGUOUS"


def test_account_number_never_in_event(tmp_path, monkeypatch):
    summary, events = _run(tmp_path, monkeypatch, [KR_ENTRY], [_kr("0000123", "053800", 4, 4, 354000)])
    blob = json.dumps(events) + json.dumps(summary)
    assert "12345678" not in blob and ACCOUNT not in blob
    assert events[0]["attributes"]["execution_profile_ref"]
    assert "0000123" not in blob  # order numbers travel only as a hash


def test_rerun_does_not_repeat_final_status(tmp_path, monkeypatch):
    rows = [_kr("0000123", "053800", 4, 4, 354000)]
    _run(tmp_path, monkeypatch, [KR_ENTRY], rows)
    summary, events = _run(tmp_path, monkeypatch, [KR_ENTRY], rows)
    assert len(events) == 1 and summary["orders"][0]["emitted"] is False


def test_missing_then_confirmed_emits_again(tmp_path, monkeypatch):
    _run(tmp_path, monkeypatch, [KR_ENTRY], [], ok=False)
    summary, events = _run(tmp_path, monkeypatch, [KR_ENTRY], [_kr("0000123", "053800", 4, 4, 354000)])
    assert [e["attributes"]["fill_status"] for e in events] == ["MISSING", "CONFIRMED"]
    assert events[0]["event_id"] != events[1]["event_id"]


def test_dry_run_emits_nothing(tmp_path, monkeypatch):
    summary, events = _run(tmp_path, monkeypatch, [KR_ENTRY], [_kr("0000123", "053800", 4, 4, 354000)], dry_run=True)
    assert events == [] and summary["orders"][0]["fill_status"] == "CONFIRMED"
    assert not (tmp_path / "state.json").exists()


def test_add_lands_on_the_entry_trace(tmp_path, monkeypatch):
    from prism_core.trace_ids import trade_trace
    entry = dict(KR_ENTRY, created="2026-10-20T01:05:00+00:00")  # yesterday's buy, not a target today
    add = {"id": "i9", "symbol": "053800", "source": "micro_split_add", "decision": "add-hash", "position": "pos-1",
           "odno": "0000400", "qty": 2, "created": "2026-10-21T02:00:00+00:00"}
    summary, events = _run(tmp_path, monkeypatch, [entry, add], [_kr("0000400", "053800", 2, 2, 180000)],
                           positions=[("pos-1", "i1")])
    assert summary["targets"] == 1
    event = events[0]
    assert event["decision_id"] == "dec-1" and event["trace_id"] == trade_trace("KR", decision_id="dec-1")
    assert event["attributes"]["entry_kind"] == "add" and event["attributes"]["add_ref"]


def test_us_ccnl_rows_and_kst_date_range(tmp_path, monkeypatch):
    intent = {"id": "u1", "market": "US", "symbol": "HALO", "source": "us_batch", "decision": "dec-u",
              "odno": "30001", "qty": 6, "created": "2026-10-21T19:04:00+00:00", "limit": "118.01"}
    row = {"ord_dt": "20261022", "ord_tmd": "040400", "odno": "30001", "orgn_odno": "", "sll_buy_dvsn_cd": "02",
           "pdno": "HALO", "ft_ord_qty": "6", "ft_ord_unpr3": "118.01", "ft_ccld_qty": "6", "ft_ccld_unpr3": "117.9",
           "ft_ccld_amt3": "707.40", "nccs_qty": "0", "rvse_cncl_dvsn": "00", "prcs_stat_name": "완료",
           "tr_crcy_cd": "USD", "ovrs_excg_cd": "NASD"}
    calls = []
    summary, events = _run(tmp_path, monkeypatch, [intent], [row], market="US", calls=calls)
    assert calls == [("20261021", "20261022")]
    attrs = events[0]["attributes"]
    assert attrs["fill_status"] == "CONFIRMED" and attrs["avg_fill_price"] == pytest.approx(117.9)
    assert attrs["fill_provenance"]["evidence_source"] == "kis_overseas_inquire_ccnl"


def test_us_without_order_number_requires_unique_row(tmp_path, monkeypatch):
    intent = {"id": "u1", "market": "US", "symbol": "HALO", "source": "us_batch", "decision": "dec-u",
              "odno": None, "qty": 6, "created": "2026-10-21T19:04:00+00:00"}
    base = {"ord_dt": "20261022", "ord_tmd": "040400", "orgn_odno": "", "sll_buy_dvsn_cd": "02", "pdno": "HALO",
            "ft_ord_qty": "6", "ft_ccld_qty": "0", "ft_ccld_unpr3": "0", "ft_ccld_amt3": "0", "nccs_qty": "6"}
    summary, events = _run(tmp_path, monkeypatch, [intent], [dict(base, odno="1"), dict(base, odno="2")], market="US")
    assert events[0]["attributes"]["reason_code"] == "ORDER_MATCH_AMBIGUOUS"


def test_session_bounds_and_close_gate():
    from datetime import datetime, timezone
    from observability.fill_reconciliation import after_close, session_bounds
    assert session_bounds("KR", "20261021") == ("2026-10-20T15:00:00+00:00", "2026-10-21T15:00:00+00:00")
    assert not after_close("KR", "20261021", datetime(2026, 10, 21, 6, 0, tzinfo=timezone.utc))  # 15:00 KST
    assert after_close("KR", "20261021", datetime(2026, 10, 21, 7, 10, tzinfo=timezone.utc))  # 16:10 KST
    assert after_close("US", "20261021", datetime(2026, 10, 21, 22, 10, tzinfo=timezone.utc))  # 18:10 ET


def test_broker_methods_are_inquiry_only():
    """The new broker reads use inquiry TRs and GET (postFlag never set)."""
    kr = (ROOT / "trading" / "domestic_stock_trading.py").read_text()
    us = (ROOT / "prism-us" / "trading" / "us_stock_trading.py").read_text()
    kr_body = kr.split("def get_daily_executions_checked", 1)[1].split("\nclass ", 1)[0]
    us_body = us.split("def get_daily_executions_checked", 1)[1].split("\nclass ", 1)[0]
    assert '"TTTC0081R"' in kr_body and "postFlag" not in kr_body
    assert '"TTTS3035R"' in us_body and "postFlag" not in us_body
    tool = (ROOT / "tools" / "reconcile_fills.py").read_text() + (ROOT / "observability" / "fill_reconciliation.py").read_text()
    for forbidden in ("execute_buy(", "execute_sell(", "amend_order(", "cancel_order(", "amend_or_cancel(", "async_buy_stock("):
        assert forbidden not in tool


def test_cli_dry_run_before_close_refuses_without_flag(tmp_path):
    db = tmp_path / "t.sqlite"
    _db(db, [])
    proc = subprocess.run([sys.executable, str(ROOT / "tools" / "reconcile_fills.py"), "--market", "KR",
                           "--date", "29991231", "--db", str(db)], capture_output=True, text=True, timeout=60,
                          env={"PATH": "/usr/bin:/bin", "PRISM_DISABLE_SIGNAL_PUBLISH": "1"})
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "market not closed yet" in proc.stderr


# ── The final fill quantity is written to the micro-split ledger leg (2026-11-14) ─────────────

def _holding(db, row_id, symbol, legs, account=ACCOUNT):
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE IF NOT EXISTS stock_holdings (id INTEGER PRIMARY KEY, ticker TEXT, company_name TEXT, "
                 "account_key TEXT, scenario TEXT, buy_price REAL, stop_loss REAL)")
    scenario = {"micro_split": {"contract": "micro-split-live-v1", "market": "KR", "unit_amount": "1000000",
                                "allocation": "1", "legs": legs}}
    conn.execute("INSERT INTO stock_holdings VALUES (?,?,?,?,?,?,?)",
                 (row_id, symbol, "x", account, json.dumps(scenario), 100, 90))
    conn.commit()
    conn.close()


def _legs(db, row_id):
    conn = sqlite3.connect(db)
    scenario = json.loads(conn.execute("SELECT scenario FROM stock_holdings WHERE id=?", (row_id,)).fetchone()[0])
    conn.close()
    return scenario["micro_split"]["legs"]


def _ledger_run(tmp_path, monkeypatch, intents, rows, legs, **kwargs):
    db = tmp_path / "t.sqlite"
    _db(db, intents)
    _holding(db, 7, "053800", legs)
    summary, _ = _run(tmp_path, monkeypatch, intents, rows, **kwargs)
    return summary, _legs(db, 7)


INITIAL_LEG = {"kind": "INITIAL", "allocation": "0.5", "price": "100", "at": "t0"}
ADD_LEG = {"kind": "ADD", "allocation": "0.5", "price": "100", "at": "t1", "bar_end": "s1", "decision_id": "add-1"}


def test_partial_fills_are_written_to_the_initial_and_add_legs(tmp_path, monkeypatch):
    entry = dict(KR_ENTRY, position="legacy:KR:7")
    add = {"id": "i2", "symbol": "053800", "decision": "add-1", "odno": "0000124", "qty": 5, "position": "legacy:KR:7",
           "source": "micro_split_add", "created": "2026-10-21T02:05:00+00:00"}
    rows = [_kr("0000123", "053800", 4, 4, 400), _kr("0000124", "053800", 5, 2, 200, tmd="110500")]
    summary, legs = _ledger_run(tmp_path, monkeypatch, [entry, add], rows, [dict(INITIAL_LEG), dict(ADD_LEG)])
    assert [leg.get("filled_qty") for leg in legs] == ["4", "2"]
    assert [o["ledger_fill_recorded"] for o in summary["orders"]] == [True, True]


def test_missing_or_dry_run_never_touches_the_ledger(tmp_path, monkeypatch):
    entry = dict(KR_ENTRY, position="legacy:KR:7")
    _, legs = _ledger_run(tmp_path, monkeypatch, [entry], [], [dict(INITIAL_LEG)], ok=False)
    assert "filled_qty" not in legs[0]
    other = tmp_path / "dry"
    other.mkdir()
    _, legs = _ledger_run(other, monkeypatch, [entry], [_kr("0000123", "053800", 4, 0, 0)], [dict(INITIAL_LEG)],
                          dry_run=True)
    assert "filled_qty" not in legs[0]


def test_unfilled_is_zero_and_a_foreign_or_ambiguous_row_is_left_alone(tmp_path, monkeypatch):
    entry = dict(KR_ENTRY, position="legacy:KR:7")
    _, legs = _ledger_run(tmp_path, monkeypatch, [entry], [_kr("0000123", "053800", 4, 0, 0)], [dict(INITIAL_LEG)])
    assert legs[0]["filled_qty"] == "0"
    for name, intents in (("other_row", [dict(KR_ENTRY, position="legacy:KR:8")]),
                          ("us_ref", [dict(KR_ENTRY, position="legacy:US:7")]),
                          ("two_buys", [dict(KR_ENTRY, position="legacy:KR:7"),
                                        dict(KR_ENTRY, id="i9", odno="0000125", position="legacy:KR:7")])):
        path = tmp_path / name
        path.mkdir()
        rows = [_kr("0000123", "053800", 4, 4, 400), _kr("0000125", "053800", 4, 4, 400)]
        _, legs = _ledger_run(path, monkeypatch, intents, rows, [dict(INITIAL_LEG)])
        assert "filled_qty" not in legs[0], name
