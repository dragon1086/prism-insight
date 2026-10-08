"""Ledger coverage events: broker orders, holding reviews, run heartbeats."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from observability import broker_orders, holding_decisions, job_runs  # noqa: E402
from observability.trading_context import _stable_hex, execution_profile_ref  # noqa: E402


@pytest.fixture
def spool(monkeypatch, tmp_path):
    path = tmp_path / "events.jsonl"
    monkeypatch.setenv("PRISM_OBSERVABILITY_SPOOL", str(path))

    def read():
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    return read


class _Response:
    headers: dict = {}

    def __init__(self, status_code=200, body=None, text=""):
        self.status_code = status_code
        self._body = body
        self.text = text

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


ORDER_PARAMS = {"CANO": "12345678", "ACNT_PRDT_CD": "01", "PDNO": "005930",
                "ORD_DVSN": "00", "ORD_QTY": "3", "ORD_UNPR": "70000"}


# --- broker.order_request ----------------------------------------------------------


@pytest.mark.parametrize("tr_id,expected", [
    ("TTTC0012U", ("KR", "buy")), ("VTTC0012U", ("KR", "buy")),
    ("TTTC0011U", ("KR", "sell")), ("TTTC0013U", ("KR", "amend_cancel")),
    ("CTSC0008U", ("KR", "reserved")), ("TTTT1002U", ("US", "buy")),
    ("TTTT1006U", ("US", "sell")), ("VTTT1001U", ("US", "sell")),
    ("TTTT3014U", ("US", "reserved_buy")), ("TTTT3016U", ("US", "reserved_sell")),
    ("TTTT1004U", ("US", "amend_cancel")),
    ("FHKST01010100", None), ("TTTC8434R", None), ("", None),
])
def test_order_tr_mapping(tr_id, expected):
    assert broker_orders.order_action(tr_id) == expected


def test_accepted_order_drops_account_and_keeps_profile_hash(spool):
    body = {"rt_cd": "0", "msg_cd": "APBK0013", "msg1": "주문 전송 완료",
            "output": {"ODNO": "0000123456", "ORD_TMD": "091500"}}
    broker_orders.note_order_request("TTTC0012U", ORDER_PARAMS, paper=False, account="12345678",
                                     product="01", response=_Response(body=body), latency_s=0.25)
    [event] = spool()
    attrs = event["attributes"]
    assert event["event_type"] == "broker.order_request"
    assert (event["market"], event["ticker"], event["severity"]) == ("KR", "005930", "INFO")
    assert attrs["status"] == "ACCEPTED" and attrs["order_no"] == "0000123456"
    assert attrs["action"] == "buy" and attrs["environment"] == "prod"
    assert "CANO" not in attrs["params"] and "ACNT_PRDT_CD" not in attrs["params"]
    assert attrs["params"]["ORD_QTY"] == "3"
    assert attrs["execution_profile_ref"] == execution_profile_ref("prod:12345678:01")
    assert "12345678" not in json.dumps(event)


def test_rejected_http_error_and_exception_statuses(spool):
    rejected = _Response(body={"rt_cd": "1", "msg_cd": "APBK0919", "msg1": "주문가능금액 초과"})
    broker_orders.note_order_request("TTTT1006U", {"PDNO": "AAPL"}, paper=True, account="1",
                                     product="01", response=rejected)
    broker_orders.note_order_request("TTTT1006U", {"PDNO": "AAPL"}, paper=False, account=None,
                                     product=None, response=_Response(500, text="gateway down"))
    broker_orders.note_order_request("TTTC0011U", {"PDNO": "005930"}, paper=False, account=None,
                                     product=None, error=TimeoutError("read timed out"))
    statuses = [(e["attributes"]["status"], e["severity"]) for e in spool()]
    assert statuses == [("REJECTED", "WARNING"), ("HTTP_ERROR", "WARNING"), ("EXCEPTION", "WARNING")]
    first, second, third = spool()
    assert first["attributes"]["environment"] == "demo"
    assert first["attributes"]["message"] == "주문가능금액 초과"
    assert second["attributes"]["message"] == "gateway down"
    assert third["attributes"]["message"].startswith("TimeoutError")


def test_non_order_tr_and_broken_input_never_raise(spool):
    assert broker_orders.note_order_request("FHKST01010100", {}, paper=False, account=None,
                                            product=None) is None
    assert broker_orders.note_order_request("TTTC0012U", object(), paper=False, account=None,
                                            product=None) is None
    assert spool() == []


# --- kis_auth._url_fetch hook ----------------------------------------------------


@pytest.fixture
def kis(monkeypatch):
    from test_multi_account_kis_auth import ka

    class Env:
        my_url, my_acct, my_prod = "https://kis.example", "12345678", "01"

    monkeypatch.setattr(ka, "getTREnv", lambda: Env)
    monkeypatch.setattr(ka, "_getBaseHeader", lambda: {})
    monkeypatch.setattr(ka, "isPaperTrading", lambda: False)
    return ka


def test_url_fetch_records_order_posts_and_returns_unchanged(kis, spool, monkeypatch):
    body = {"rt_cd": "0", "msg_cd": "0", "msg1": "ok", "output": {"ODNO": "77"}}

    class Fake:
        status_code, headers, text = 200, {}, ""

        def json(self):
            return body

    monkeypatch.setattr(kis.requests, "post", lambda *a, **k: Fake())
    result = kis._url_fetch("/uapi/order", "TTTC0012U", "", dict(ORDER_PARAMS), postFlag=True)
    assert result.isOK()
    [event] = spool()
    assert event["attributes"]["order_no"] == "77"
    assert event["attributes"]["execution_profile_ref"] == execution_profile_ref("prod:12345678:01")


def test_url_fetch_ignores_get_and_reraises_post_errors(kis, spool, monkeypatch):
    monkeypatch.setattr(kis.requests, "get", lambda *a, **k: _Response(body={"rt_cd": "0", "msg_cd": "", "msg1": ""}))
    kis._url_fetch("/uapi/quote", "FHKST01010100", "", {"PDNO": "005930"})
    assert spool() == []

    def boom(*a, **k):
        raise ConnectionError("reset")

    monkeypatch.setattr(kis.requests, "post", boom)
    with pytest.raises(ConnectionError):
        kis._url_fetch("/uapi/order", "TTTC0011U", "", dict(ORDER_PARAMS), postFlag=True)
    [event] = spool()
    assert event["attributes"]["status"] == "EXCEPTION"


# --- holding.evaluated --------------------------------------------------------------


def test_holding_evaluation_shares_the_position_trace(spool):
    stock = {"id": 118, "ticker": "327260", "company_name": "RF머트리얼즈", "buy_price": 10000,
             "current_price": 10500, "stop_loss": 9500, "target_price": 12000,
             "buy_date": "2026-09-30 10:08:57", "trigger_type": "거래량 급증",
             "scenario": json.dumps({"_decision_id": "dec-1"})}
    from datetime import datetime
    holding_decisions.emit_holding_evaluation("kr", stock, False, "추세 유지", source="sell_review",
                                              now=datetime(2026, 10, 2, 9, 30))
    [event] = spool()
    attrs = event["attributes"]
    assert event["event_type"] == "holding.evaluated"
    assert event["position_id"] == "legacy:KR:118" and event["decision_id"] == "dec-1"
    assert event["trace_id"] == _stable_hex("trade-trace", "KR", "dec-1", length=32)
    assert attrs["decision"] == "HOLD" and attrs["profit_rate_pct"] == 5.0
    assert attrs["holding_days"] == 2 and attrs["reason"] == "추세 유지"


def test_holding_evaluation_never_raises(spool):
    assert holding_decisions.emit_holding_evaluation("US", None, True, "x", source="sell_review") is None


# --- job.run_completed ------------------------------------------------------------


def test_job_run_flattens_summary_and_marks_errors(spool):
    job_runs.emit_job_run("hardstop", market="kr", mode="LIVE", run_id="r1", status="OK", started=0.0,
                          summary={"market": "KR", "evaluated": 3, "nested": {"x": 1}})
    job_runs.emit_job_run("analysis-batch", market="US", status="ERROR", error=RuntimeError("boom"))
    ok, err = spool()
    assert ok["event_type"] == "job.run_completed" and ok["market"] == "KR"
    assert ok["attributes"]["summary"] == {"market": "KR", "evaluated": 3}
    assert ok["attributes"]["duration_s"] >= 0
    assert err["severity"] == "ERROR" and err["attributes"]["error"] == "RuntimeError: boom"


@pytest.mark.parametrize("module_name,flag,job", [
    ("tools.hardstop_seller", "HARDSTOP_ENABLED", "hardstop"),
    ("tools.trend_exit_seller", "TREND_EXIT_ENABLED", "trend-exit"),
    ("tools.fill_chaser", "FILL_CHASER_ENABLED", "fill-chaser"),
])
def test_intraday_loops_leave_one_heartbeat_per_run(module_name, flag, job, spool, monkeypatch):
    import importlib
    module = importlib.import_module(module_name)

    async def fake_run(market, run_id):
        return {"market": market, "evaluated": 2}

    monkeypatch.setattr(module, flag, True)
    monkeypatch.setattr(module, "run_market", fake_run)
    assert asyncio.run(module.main_async(["KR"])) == 0

    async def failing_run(market, run_id):
        raise RuntimeError("inquiry down")

    monkeypatch.setattr(module, "run_market", failing_run)
    with pytest.raises(RuntimeError):
        asyncio.run(module.main_async(["US"]))

    monkeypatch.setattr(module, flag, False)
    assert asyncio.run(module.main_async(["KR"])) == 0

    events = spool()
    assert [(e["attributes"]["job"], e["market"], e["attributes"]["status"]) for e in events] == [
        (job, "KR", "OK"), (job, "US", "ERROR"), (job, "KR", "DISABLED")]
    assert events[0]["attributes"]["summary"] == {"market": "KR", "evaluated": 2}


def _load_orchestrator(market):
    import importlib.util
    path = ROOT / ("stock_analysis_orchestrator.py" if market == "KR"
                   else "prism-us/us_stock_analysis_orchestrator.py")
    spec = importlib.util.spec_from_file_location(f"orchestrator_heartbeat_{market}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("market", ["KR", "US"])
def test_analysis_batch_heartbeat_statuses(market, spool, monkeypatch):
    module = _load_orchestrator(market)
    monkeypatch.setattr(sys, "argv", ["orchestrator", "--mode", "morning"])

    async def ok():
        return None

    async def rested():
        module._JOB_RUN_SKIPS.append("morning:weak_market")

    async def crashed():
        raise RuntimeError("pipeline crashed")

    async def config_exit():
        sys.exit(1)

    for main in (ok, rested):
        monkeypatch.setattr(module, "main", main)
        module._run_main_with_heartbeat(market, 0.0)
    monkeypatch.setattr(module, "main", crashed)
    with pytest.raises(RuntimeError):
        module._run_main_with_heartbeat(market, 0.0)
    monkeypatch.setattr(module, "main", config_exit)
    with pytest.raises(SystemExit):
        module._run_main_with_heartbeat(market, 0.0)

    events = spool()
    assert [(e["market"], e["attributes"]["status"]) for e in events] == [
        (market, "OK"), (market, "SKIPPED"), (market, "ERROR"), (market, "ERROR")]
    assert all(e["attributes"]["mode"] == "morning" for e in events)
    assert events[1]["attributes"]["reason"] == "morning:weak_market"
    assert events[3]["attributes"]["error"] == "SystemExit(1)"
