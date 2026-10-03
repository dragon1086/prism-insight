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


class _FrozenDateTime(datetime):
    """``datetime.now`` pinned to ENTERED so fixture bars never go stale as the calendar moves."""

    @classmethod
    def now(cls, tz=None):
        return ENTERED.astimezone(tz) if tz else ENTERED.replace(tzinfo=None)


@pytest.fixture(autouse=True)
def _frozen_now(monkeypatch):
    import observability.b3_ae_capture as capture
    monkeypatch.setattr(capture, "datetime", _FrozenDateTime)


@pytest.fixture
def live_on(monkeypatch):
    monkeypatch.setenv("MICRO_SPLIT_LIVE_ENABLED", "true")
    monkeypatch.setenv("MICRO_SPLIT_LIVE_ADDS_ENABLED", "true")
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
    scenario["micro_split"]["add_plan"] = {"plan_hash": "ph", "status": "ACTIVE", "valid_for": "2026-10-05"}
    meta = {"plan_hash": "ph", "scenario_id": "breakout_1", "scenario_type": "breakout", "lens": ["oneil"],
            "session": "2026-10-05", "trigger_price": price, "rationale": "prior high reclaimed on volume"}
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
    campaign = {"position_id": f"legacy:{market}:7", "account_key": "acc", "symbol": "ABC", "campaign_id": "c1"}

    # The fixed ladder (no add_plan meta) and a stale plan never order.
    for decision, status in (({"target_allocation": "0.8", "price": price, "bar_end": "b1"}, "NOT_A_PLAN_ADD"),
                             ({"target_allocation": "0.8", "price": price, "bar_end": "b1",
                               "add_plan": dict(meta, plan_hash="old")}, "PLAN_CHANGED")):
        assert asyncio.run(live.execute_add(agent, market=market, campaign=campaign, decision=decision,
                                            now="t1"))["status"] == status
    assert _FakeTrading.calls == [] and agent.message_queue == []

    result = asyncio.run(live.execute_add(
        agent, market=market, campaign=campaign,
        decision={"target_allocation": "0.8", "price": price, "bar_end": "add-plan:2026-10-05:breakout_1",
                  "add_plan": meta}, now="t1"))

    assert result["status"] == "EXECUTED" and result["allocation"] == pytest.approx(0.8)
    stored = conn.execute(f"SELECT scenario, buy_price FROM {table} WHERE id=7").fetchone()
    assert json.loads(stored["scenario"])["micro_split"]["allocation"] == "0.8"
    leg = json.loads(stored["scenario"])["micro_split"]["legs"][-1]
    assert (leg["scenario_id"], leg["plan_hash"], leg["session"], leg["source"]) == (
        "breakout_1", "ph", "2026-10-05", "add_plan")
    with pytest.raises(ValueError, match="bar already used"):  # one add per scenario and session
        asyncio.run(live.execute_add(agent, market=market, campaign=campaign, now="t2", decision={
            "target_allocation": "0.9", "price": price, "bar_end": "add-plan:2026-10-05:breakout_1",
            "add_plan": meta}))
    assert stored["buy_price"] == pytest.approx(price / 1.02)  # initial entry stays (positions mirror, stop basis)
    average, fresh = live.exit_basis(conn.cursor(), market, [7], "{}")
    assert price / 1.02 < average < price and json.loads(fresh)["micro_split"]["allocation"] == "0.8"
    line = live.allocation_line(fresh, current_price=price, market=market)
    assert ("평균 매수가" if market == "KR" else "Average entry") in line
    order = _FakeTrading.calls[0]
    assert order["buy_amount"] == live.scaled_cash(unit, "0.3", market)
    assert order["strict_budget"] is True and order["limit_price"] == price
    assert "50% → 80%" in agent.message_queue[0] and agent._msg_types == ["analysis"]
    assert ("Scenario: breakout (breakout_1)" if market == "US" else "시나리오: 돌파 (breakout_1)") in \
        agent.message_queue[0] and "prior high reclaimed" in agent.message_queue[0]
    name, kw = emitted[0]
    assert name == "micro_split.add_executed"
    assert kw["attributes"]["allocation_before"] == 0.5 and kw["attributes"]["allocation_after"] == 0.8
    assert kw["attributes"]["scenario_id"] == "breakout_1" and kw["attributes"]["plan_hash"] == "ph"
    assert kw["attributes"]["lens"] == ["oneil"]


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


def test_us_korean_summary_uses_dollars(live_on):
    scenario = {"micro_split": live.entry_record(
        plan={"initial_nominal": "0.8", "policy_version": "v3", "plan_hash": "h", "entry_reference": 230},
        unit_amount=1000, market="US", entered_at="t0")}
    added, _ = live.apply_add(scenario, delta="0.2", price=235, at="t", bar_end="b")
    line = live.allocation_line(added, current_price=240, market="US", language="ko")
    assert "평균 매수가 $" in line and "원" not in line and "슬롯 기준 손익" in line


PLAN = {"thesis_check": "intact", "invalidation": {"close_below": 9500},
        "scenarios": [{"id": "breakout_1", "lens": ["oneil"], "type": "breakout",
                       "trigger": {"price_above": 10500, "volume_pace_min": 1.5}, "target_allocation": 0.95},
                      {"id": "pullback_1", "lens": ["minervini"], "type": "pullback_reclaim",
                       "trigger": {"zone_low": 9800, "zone_high": 9950, "reclaim_above": 10100,
                                   "confirm": "daily_close"}, "target_allocation": 0.9}]}


def test_buy_add_plan_is_validated_onto_the_entry_record_and_summarized(live_on):
    _, _, scenario = live.prepare_entry(
        _agent(), market="KR", ticker="005930", current_price=10000,
        scenario={"stop_loss": 9300, "add_plan": PLAN}, decision_ref="report:x.pdf",
        account={"buy_amount_krw": 1_000_000})
    block = scenario["micro_split"]
    assert "add_plan" not in scenario and block["add_plan"]["status"] == "ACTIVE"
    assert block["add_plan"]["source"] == "BUY" and [s["id"] for s in block["add_plan"]["scenarios"]] == [
        "breakout_1", "pullback_1"]
    assert block["add_plan"]["valid_for"] > block["legs"][0]["at"][:10]  # never the entry session
    assert block["add_plan_history"][-1]["plan_hash"] == block["add_plan"]["plan_hash"]
    line = live.entry_message_line(scenario, "KR")
    assert "증액 시나리오: 돌파 10,500원 → 95%; 눌림 회복 10,100원 → 90%" in line and "+2%" not in line
    assert "add scenarios: breakout $10,500.00 → 95%" in live.entry_message_line(scenario, "US")
    # Missing or invalid plan: the entry stays, the position has no adds until a review sets one.
    _, _, bare = live.prepare_entry(_agent(), market="KR", ticker="005930", current_price=10000,
                                    scenario={"stop_loss": 9300, "add_plan": {"scenarios": []}},
                                    decision_ref="r", account={"buy_amount_krw": 1_000_000})
    assert "add_plan" not in bare["micro_split"] and bare["micro_split"]["add_plan_history"][0]["issues"]
    assert "다음 보유 점검에서 세우며" in live.entry_message_line(bare, "KR")


def test_plan_adds_follow_live_with_an_adds_only_kill_switch(monkeypatch):
    monkeypatch.setenv("MICRO_SPLIT_LIVE_ENABLED", "true")
    monkeypatch.delenv("MICRO_SPLIT_LIVE_ADDS_ENABLED", raising=False)
    monkeypatch.delenv("MICRO_SPLIT_LIVE_MARKETS", raising=False)
    assert live.plan_adds_enabled("KR") and live.plan_adds_enabled("US")
    monkeypatch.setenv("MICRO_SPLIT_LIVE_ADDS_ENABLED", "false")
    assert live.live_enabled("KR") and not live.plan_adds_enabled("KR")
    _, cash, scenario = _prepared()
    assert cash == 777_700  # the fractional first entry stays LIVE
    assert "멈춰 있고" in live.entry_message_line(scenario, "KR")
    assert "adds are currently paused" in live.entry_message_line(scenario, "US")
    monkeypatch.setenv("MICRO_SPLIT_LIVE_ENABLED", "false")
    monkeypatch.setenv("MICRO_SPLIT_LIVE_ADDS_ENABLED", "true")
    assert not live.plan_adds_enabled("KR")
    assert live.add_plan_buy_block(_agent(), market="KR", ticker="005930", language="ko") == ""
    assert live.review_prompt_block(json.dumps(scenario), market="KR", language="ko") == ""


def test_review_stores_next_session_plan_and_a_sell_cancels_it(live_on, tmp_path):
    _, _, scenario = _prepared()
    conn = sqlite3.connect(tmp_path / "r.sqlite")
    conn.execute("CREATE TABLE stock_holdings (id INTEGER PRIMARY KEY, scenario TEXT)")
    conn.execute("INSERT INTO stock_holdings VALUES (3, ?)", (json.dumps(scenario),))
    conn.commit()
    agent = SimpleNamespace(conn=conn, cursor=conn.cursor())

    def block():
        return json.loads(conn.execute("SELECT scenario FROM stock_holdings").fetchone()[0])["micro_split"]

    plan = {**PLAN, "scenarios": [dict(s, target_allocation=0.95) for s in PLAN["scenarios"]]}
    now = datetime(2026, 10, 5, 14, 46, tzinfo=SEOUL).astimezone(timezone.utc).isoformat()
    review = dict(market="KR", row_id=3, ticker="005930", now=now)
    assert live.apply_review(agent, decision={"should_sell": False, "next_session_add_plan": plan},
                             **review) == "ACTIVE"
    assert block()["add_plan"]["valid_for"] == "2026-10-06" and block()["add_plan"]["source"] == "REVIEW"
    # A missing key keeps the stored plan; a sell decision cancels it, including the same session.
    assert live.apply_review(agent, decision={"should_sell": False}, **review) == "NO_PLAN_KEY"
    assert live.apply_review(agent, decision={"should_sell": True}, **review) == "CANCELLED"
    assert block()["add_plan"]["status"] == "CANCELLED" and block()["add_plan"]["valid_for"] == "2026-10-06"
    assert [h["source"] for h in block()["add_plan_history"]] == ["BUY", "REVIEW", "SELL_DECISION"]


def test_peak_ratchet_keeps_an_add_written_after_its_snapshot(live_on):
    _, _, scenario = _prepared()
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE stock_holdings (id INTEGER PRIMARY KEY, scenario TEXT)")
    added, _ = live.apply_add(scenario, delta="0.0223", price=10200, at="t", bar_end="b1")
    conn.execute("INSERT INTO stock_holdings VALUES (5, ?)", (json.dumps(added),))
    stale = json.loads(json.dumps(scenario))
    stale["highest_price"] = 10300
    live.keep_fresh_record(conn.cursor(), "KR", 5, stale)
    assert stale["micro_split"]["allocation"] == "0.8000" and stale["highest_price"] == 10300
    assert live.keep_fresh_record(conn.cursor(), "KR", 5, {"highest_price": 1}) == {"highest_price": 1}


def test_score_floor_relaxes_only_verified_micro_split_entries(live_on, monkeypatch):
    agent = _agent()
    base = {"stop_loss": 9300, "buy_score": 6}
    floor, scenario = live.relaxed_min_score(agent, market="KR", ticker="005930", current_price=10000,
                                             scenario=base, min_score=8, is_add=False, rebound_pilot=False)
    assert floor == 5.0 and live.gate_score_override(scenario) == 5.0
    assert scenario[live.SCORE_POLICY_KEY]["legacy_required_score"] == 8.0
    for kwargs in ({"is_add": True, "rebound_pilot": False}, {"is_add": False, "rebound_pilot": True}):
        assert live.relaxed_min_score(agent, market="KR", ticker="005930", current_price=10000, scenario=base,
                                      min_score=8, **kwargs) == (8, base)
    assert live.relaxed_min_score(agent, market="KR", ticker="005930", current_price=10000, scenario=base,
                                  min_score=4, is_add=False, rebound_pilot=False) == (4, base)
    no_bars = SimpleNamespace(_decision_input_bars={})
    assert live.relaxed_min_score(no_bars, market="KR", ticker="005930", current_price=10000, scenario=base,
                                  min_score=8, is_add=False, rebound_pilot=False) == (8, base)
    monkeypatch.setenv("MICRO_SPLIT_MIN_SCORE", "off")
    assert live.score_floor("KR") is None and live.buy_prompt_block("KR") == ""
    monkeypatch.delenv("MICRO_SPLIT_LIVE_ENABLED")
    monkeypatch.setenv("MICRO_SPLIT_MIN_SCORE", "5")
    assert live.score_floor("US") is None and live.buy_prompt_block("US", "en") == ""


def test_buy_prompt_block_and_gate_override(live_on):
    assert "시장 국면과 관계없이 5점" in live.buy_prompt_block("KR", "ko")
    assert "minimum entry score is 5" in live.buy_prompt_block("US", "en")
    from cores.buy_gate import evaluate_production_buy_gate
    scenario = {"decision": "Enter", "buy_score": 6, "min_score": 8, "target_price": 12000, "stop_loss": 9500,
                "momentum_signal_count": 2, "additional_confirmation_count": 1}
    kwargs = dict(current_price=10000, market_regime="sideways", score_override=6, market_pulse="UNDER_PRESSURE")
    codes = lambda r: {f["code"] for f in r.get("findings", [])}  # noqa: E731
    assert "score_below_floor" in codes(evaluate_production_buy_gate(scenario, **kwargs))
    relaxed = evaluate_production_buy_gate(scenario, required_score_override=5, **kwargs)
    assert "score_below_floor" not in codes(relaxed)
    assert "score_below_floor" in codes(evaluate_production_buy_gate(dict(scenario, buy_score=4), **{
        **kwargs, "score_override": 4}, required_score_override=5))
    assert "score_below_floor" in codes(evaluate_production_buy_gate(scenario, required_score_override=5,
                                                                      is_add=True, **kwargs))


def test_journal_and_compression_carry_position_size(live_on):
    _, _, scenario = _prepared()
    added, average = live.apply_add(scenario, delta="0.0223", price=10200, at="t", bar_end="b1")
    line = live.journal_position_line(added, profit_rate=5.0)
    assert "80% of one slot" in line and "initial 78%, 1 add(s)" in line and "slot-weighted return +4.00%" in line
    assert live.journal_position_line({"sector": "IT"}, 5.0) == ""
    data = live.journal_stock_data({"ticker": "A", "buy_price": 10000}, buy_price=average, scenario=json.dumps(added))
    assert data["buy_price"] == average and json.loads(data["scenario"])["micro_split"]["allocation"] == "0.8000"

    from tracking.journal import JournalManager
    manager = JournalManager.__new__(JournalManager)
    manager.language = "ko"
    full = manager._build_analysis_prompt("A", "000001", 10000, "2026-10-02", {}, 10500, 5.0, 3, "target")
    partial = manager._build_analysis_prompt("A", "000001", average, "2026-10-02", added, 10500, 5.0, 3, "target")
    assert "Position Size" not in full and "Position Size (micro-split)" in partial

    from tracking.compression import _slot_note
    assert _slot_note({"buy_scenario": json.dumps(added)}) == " (slot 80%)"
    assert _slot_note({"buy_scenario": "{}"}) == ""


def test_weekly_report_marks_partial_positions(live_on):
    import weekly_insight_report as weekly
    _, _, scenario = _prepared()
    added, _ = live.apply_add(scenario, delta="0.0223", price=10200, at="t", bar_end="b1")
    suffix = weekly._slot_suffix(json.dumps(added), 10000, 10500)
    assert suffix.startswith(" · 비중 80% (78%→80%)") and "슬롯 기준" in suffix
    assert weekly._slot_suffix("{}", 10000, 10500) == "" and weekly._slot_suffix(None, 10000, None) == ""
