"""B3 micro-split LIVE (KR/US): fractional entry, in-slot adds, allocation display."""
import asyncio
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from prism_core import micro_split_live as live
from prism_core.slot_weight import slot_fraction, weighted_profit_rate
from tracking.helpers import _stored_pyramid_ownership, evaluate_pyramid_add_gate

SEOUL = ZoneInfo("Asia/Seoul")
ENTERED = datetime(2026, 10, 2, 9, 35, tzinfo=SEOUL)


def _agent(market="KR", ticker="005930"):
    bars, day = [], date(2026, 9, 1)
    while day < date(2026, 10, 2):
        if day.weekday() < 5:
            bars.append(dict(date=day.isoformat(), open=9900.0, high=10100.0, low=9800.0, close=10000.0,
                             volume=1000.0))
        day += timedelta(days=1)
    captured = (ENTERED - timedelta(minutes=10)).astimezone(timezone.utc).isoformat()
    return SimpleNamespace(_decision_input_bars={ticker: dict(market=market, bars=bars, captured_at=captured)})


@pytest.fixture
def live_on(monkeypatch):
    monkeypatch.setenv("MICRO_SPLIT_LIVE_ENABLED", "true")
    monkeypatch.delenv("MICRO_SPLIT_LIVE_MARKETS", raising=False)


def _prepared(market="KR", unit=1_000_000):
    plan, cash, scenario = live.prepare_entry(
        _agent(market), market=market, ticker="005930", current_price=10000,
        scenario={"stop_loss": 9300, "sector": "IT"}, decision_ref="report:x.pdf",
        account={"buy_amount_krw": unit, "buy_amount_usd": unit})
    return plan, cash, scenario


def test_live_flag_defaults_off_and_filters_markets(monkeypatch):
    monkeypatch.delenv("MICRO_SPLIT_LIVE_ENABLED", raising=False)
    assert not live.live_enabled("KR")
    monkeypatch.setenv("MICRO_SPLIT_LIVE_ENABLED", "true")
    monkeypatch.setenv("MICRO_SPLIT_LIVE_MARKETS", "US")
    assert live.live_enabled("us") and not live.live_enabled("KR")


def test_off_keeps_the_legacy_full_slot(monkeypatch):
    monkeypatch.delenv("MICRO_SPLIT_LIVE_ENABLED", raising=False)
    plan, cash, scenario = _prepared()
    assert (plan, cash) == (None, None) and "micro_split" not in scenario
    assert slot_fraction(scenario) == 1.0


def test_scaled_cash_rounds_down_per_currency():
    assert live.scaled_cash(1_000_000, "0.7777", "KR") == 777_700
    assert live.scaled_cash(1000, "0.33333", "US") == 333.33
    with pytest.raises(ValueError):
        live.scaled_cash(1, "0.3", "KR")


def test_prepare_entry_sizes_the_first_order_from_atr(live_on):
    plan, cash, scenario = _prepared()
    # ATR14 = 300 -> stop proxy 4.5% -> initial = 0.035 / 0.045
    assert plan["initial_nominal"] == "0.7777"
    assert cash == 777_700
    block = scenario["micro_split"]
    assert block["contract"] == live.CONTRACT and block["allocation"] == "0.7777"
    assert block["legs"][0]["kind"] == "INITIAL" and block["unit_amount"] == "1000000"
    assert slot_fraction(scenario) == pytest.approx(0.7777)
    assert weighted_profit_rate(-5.0, scenario) == pytest.approx(-3.8885)


def test_prepare_entry_falls_back_without_decision_bars(live_on):
    plan, cash, scenario = live.prepare_entry(
        SimpleNamespace(_decision_input_bars={}), market="KR", ticker="005930", current_price=10000,
        scenario={"stop_loss": 9300}, decision_ref="r", account={"buy_amount_krw": 1_000_000})
    assert (plan, cash) == (None, None) and "micro_split" not in scenario


def test_apply_add_weights_entry_caps_one_slot_and_one_add_per_bar(live_on):
    _, _, scenario = _prepared()
    updated, average = live.apply_add(scenario, delta="0.0223", price=10200, at="t", bar_end="b1")
    assert updated["micro_split"]["allocation"] == "0.8000"
    # allocation-weighted harmonic mean of 10000 x 0.7777 and 10200 x 0.0223
    expected = 0.8 / (0.7777 / 10000 + 0.0223 / 10200)
    assert average == pytest.approx(expected)
    assert scenario["micro_split"]["allocation"] == "0.7777"  # input untouched
    with pytest.raises(ValueError, match="bar already used"):
        live.apply_add(updated, delta="0.1", price=10400, at="t", bar_end="b1")
    with pytest.raises(ValueError, match="exceed one slot"):
        live.apply_add(updated, delta="0.3", price=10400, at="t", bar_end="b2")


def test_display_lines_show_allocation_and_slot_weighted_pnl(live_on):
    _, _, scenario = _prepared()
    added, _ = live.apply_add(scenario, delta="0.2223", price=10400, at="t", bar_end="b1")
    assert live.display(scenario) == "비중 78%"
    assert live.display(added) == "비중 100% (78%→100%)"
    assert live.allocation_line(json.dumps(scenario), profit_rate=-5.0, market="KR", indent="  ") == \
        "  비중 78% (1슬롯 기준) / 슬롯 기준 손익 -3.89%\n"
    assert live.allocation_line(scenario, profit_rate=-5.0, market="US").startswith("Allocation 78% of one slot")
    pilot = {"regime_entry_policy": {"position_fraction": 0.5}}
    assert live.allocation_line(pilot) == "비중 50% (1슬롯 기준)\n"
    assert live.allocation_line({"sector": "IT"}) == ""
    assert live.used_slots([json.dumps(scenario), "{}", pilot]) == pytest.approx(2.2777)
    assert "초분할 비중: 78%" in live.entry_message_line(scenario, "KR")
    assert "78% of one slot" in live.entry_message_line(scenario, "US")


def test_legacy_pyramid_is_blocked_for_micro_split_rows(live_on):
    _, _, scenario = _prepared()
    assert _stored_pyramid_ownership(json.dumps(scenario)) == "MICRO_SPLIT"
    ok, reason = evaluate_pyramid_add_gate("strong_bull", 10000, 11000, 1, ownership="MICRO_SPLIT")
    assert not ok and reason == "LEGACY_PYRAMID_BLOCKED_MICRO_SPLIT"


class _FakeTrading:
    calls = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute_buy(self, **kwargs):
        _FakeTrading.calls.append(kwargs)
        return {"success": True, "status": "submitted"}


@pytest.mark.parametrize("market,table,unit,price", [("KR", "stock_holdings", 1_000_000, 10200),
                                                     ("US", "us_stock_holdings", 1000, 102.0)])
def test_execute_add_updates_row_orders_delta_and_reports(live_on, monkeypatch, tmp_path, market, table, unit,
                                                          price):
    from observability import events
    from prism_core import execution_service

    scenario = {"stop_loss": 93, "micro_split": live.entry_record(
        plan={"initial_nominal": "0.5", "policy_version": "v3", "plan_hash": "h", "entry_reference": price / 1.02},
        unit_amount=unit, market=market, entered_at="t0")}
    conn = sqlite3.connect(tmp_path / "h.sqlite")
    conn.row_factory = sqlite3.Row
    conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, ticker TEXT, company_name TEXT, "
                 "account_key TEXT, scenario TEXT, buy_price REAL)")
    conn.execute(f"INSERT INTO {table} VALUES (7, 'ABC', 'Abc', 'acc', ?, ?)", (json.dumps(scenario), price / 1.02))
    conn.commit()
    agent = SimpleNamespace(conn=conn, cursor=conn.cursor(), db_path=str(tmp_path / "h.sqlite"),
                            account_configs=[{"account_key": "acc", "name": "primary"}],
                            _set_active_account=lambda account: None, message_queue=[], _msg_types=[])
    emitted = []
    monkeypatch.setattr(events, "emit_event", lambda name, **kw: emitted.append((name, kw)))
    monkeypatch.setattr(execution_service.ExecutionService, "domestic", _FakeTrading, raising=False)
    monkeypatch.setattr(execution_service.ExecutionService, "us", _FakeTrading, raising=False)
    _FakeTrading.calls.clear()

    result = asyncio.run(live.execute_add(
        agent, market=market, campaign={"position_id": f"legacy:{market}:7", "account_key": "acc", "symbol": "ABC",
                                        "campaign_id": "c1"},
        decision={"target_allocation": "0.8", "price": price, "bar_end": "b1"}, now="t1"))

    assert result["status"] == "EXECUTED" and result["allocation"] == pytest.approx(0.8)
    stored = conn.execute(f"SELECT scenario, buy_price FROM {table} WHERE id=7").fetchone()
    assert json.loads(stored["scenario"])["micro_split"]["allocation"] == "0.8"
    assert stored["buy_price"] == pytest.approx(price / 1.02)  # initial entry stays (positions mirror, stop basis)
    average, fresh = live.exit_basis(conn.cursor(), market, [7], "{}")
    assert price / 1.02 < average < price and json.loads(fresh)["micro_split"]["allocation"] == "0.8"
    line = live.allocation_line(fresh, current_price=price, market=market)
    assert ("평균 매수가" if market == "KR" else "Average entry") in line
    order = _FakeTrading.calls[0]
    assert order["buy_amount"] == live.scaled_cash(unit, "0.3", market)
    assert order["strict_budget"] is True and order["limit_price"] == price
    assert "50% → 80%" in agent.message_queue[0] and agent._msg_types == ["analysis"]
    name, kw = emitted[0]
    assert name == "micro_split.add_executed"
    assert kw["attributes"]["allocation_before"] == 0.5 and kw["attributes"]["allocation_after"] == 0.8


def test_exit_basis_ignores_legacy_rows():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE stock_holdings (id INTEGER PRIMARY KEY, scenario TEXT)")
    conn.execute("INSERT INTO stock_holdings VALUES (1, '{}')")
    assert live.exit_basis(conn.cursor(), "KR", [1], "{}") is None


def test_dashboard_signal_and_context_fields_carry_allocation(live_on, monkeypatch):
    from observability import trading_context
    from observability.shipper import build_otlp_payload

    _, _, scenario = _prepared()
    added, _ = live.apply_add(scenario, delta="0.0223", price=10200, at="t", bar_end="b1")
    fields = live.dashboard_fields(added, buy_price=10000, current_price=10500)
    assert fields["allocation"] == pytest.approx(0.8) and fields["add_count"] == 1
    assert fields["allocation_label"] == "비중 80% (78%→80%)"
    assert 10000 < fields["average_entry"] < 10200
    assert fields["slot_profit_rate"] == pytest.approx((10500 / fields["average_entry"] - 1) * 80)
    assert live.dashboard_fields({}, buy_price=100, current_price=110)["allocation_label"] is None

    context = trading_context.build_trading_context(market="KR", scenario=json.dumps(added))
    assert context["policy_context"]["slot_allocation"] == pytest.approx(0.8)
    assert context["policy_context"]["micro_split"]["allocation"] == "0.8000"
    payload = build_otlp_payload([{"event_type": "entry.executed", "attributes": {"slot_allocation": 0.8}}])
    attributes = payload["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]["attributes"]
    assert {"key": "prism.slot_allocation", "value": {"doubleValue": 0.8}} in attributes

    from messaging.redis_signal_publisher import SignalPublisher
    sent = []
    publisher = SignalPublisher.__new__(SignalPublisher)
    publisher._redis = SimpleNamespace(xadd=lambda stream, mid, data: sent.append(json.loads(data["data"])) or "1-0")
    monkeypatch.setattr(publisher, "_is_connected", lambda: True, raising=False)
    monkeypatch.setattr("messaging.redis_signal_publisher.signal_publishing_disabled", lambda: False)
    asyncio.run(publisher.publish_buy_signal(ticker="005930", company_name="삼성전자", price=10000, scenario=scenario))
    assert sent[0]["position_fraction"] == pytest.approx(0.7777)


def test_us_korean_summary_uses_dollars_and_an_80pct_entry_has_one_step_left():
    scenario = {"micro_split": live.entry_record(
        plan={"initial_nominal": "0.8", "policy_version": "v3", "plan_hash": "h", "entry_reference": 230},
        unit_amount=1000, market="US", entered_at="t0")}
    assert "+4% above entry" in live.entry_message_line(scenario, "US") and "80%/100%" not in \
        live.entry_message_line(scenario, "US")
    assert "+4% 상승이 확인되면 100%까지" in live.entry_message_line(scenario, "KR")
    added, _ = live.apply_add(scenario, delta="0.2", price=235, at="t", bar_end="b")
    line = live.allocation_line(added, current_price=240, market="US", language="ko")
    assert "평균 매수가 $" in line and "원" not in line and "슬롯 기준 손익" in line
