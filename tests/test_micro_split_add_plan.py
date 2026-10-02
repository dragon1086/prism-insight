"""Scenario-based micro-split add plans: validator, evaluator rails and the LIVE worker path (no network/orders)."""
import asyncio
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from prism_core import add_plan as P

SEOUL = ZoneInfo("Asia/Seoul")
NY = ZoneInfo("America/New_York")
SESSION = "2026-10-05"  # Monday after a Friday 2026-10-02 entry
OPEN = datetime(2026, 10, 5, 9, 0, tzinfo=SEOUL)


def _iso(moment):
    return moment.astimezone(timezone.utc).isoformat()


def _scenario(sid="breakout_1", kind="breakout", target=0.6, lens=("oneil",), **trigger):
    trigger = trigger or {"price_above": 10400}
    return {"id": sid, "lens": list(lens), "type": kind, "trigger": trigger, "target_allocation": target,
            "rationale": f"{sid} rationale"}


def _plan(*scenarios, valid_for=SESSION, allocation="0.45", last_step="0.45", invalidation=None, source="BUY"):
    raw = {"thesis_check": "earnings intact", "invalidation": invalidation or {},
           "scenarios": list(scenarios) or [_scenario(), _scenario("accel_1", "acceleration", 0.65,
                                                                     ("druckenmiller",), gap_up_min_pct=3)]}
    plan, issues = P.validate_plan(raw, market="KR", source=source, created_at=_iso(OPEN - timedelta(days=3)),
                                   valid_for=valid_for, allocation=allocation, last_step=last_step)
    return plan, issues


def _state(allocation="0.45", legs=None, **extra):
    legs = legs or [{"kind": "INITIAL", "allocation": allocation, "price": "10000",
                     "at": _iso(datetime(2026, 10, 2, 9, 35, tzinfo=SEOUL))}]
    return dict({"allocation": allocation, "legs": legs, "initial_entry": 10000, "initial_stop": 9300, "current_stop": 9300,
                     "fee_rate": "0.001", "entry_session": "2026-10-02", "blocked": None}, **extra)


def _daily(closes=None, volume=1000, lows=None):
    days, day = [], date(2026, 10, 2)
    while len(days) < 20:
        if day.weekday() < 5:
            days.insert(0, day)
        day -= timedelta(days=1)
    closes = closes or [10000] * 20
    lows = lows or [c - 100 for c in closes]
    return [{"date": d.isoformat(), "open": c, "high": c + 100, "low": low, "close": c, "volume": volume}
            for d, c, low in zip(days, closes, lows)]


def _evidence(closes=(10450, 10460), price=10460, minutes=60, phase="INTRADAY", open_price=10000, volume=10,
              **extra):
    count = minutes // 5
    bars = []
    for i in range(count):
        close = closes[i - count + len(closes)] if i >= count - len(closes) else max(10100, open_price + 10)
        bars.append({"start_at": _iso(OPEN + timedelta(minutes=5 * i)), "end_at": _iso(OPEN + timedelta(minutes=5 * i + 5)),
                         "open": open_price if i == 0 else close, "high": close + 20, "low": min(close, open_price) - 20,
                         "close": close, "volume": volume})
    return dict({"status": "OK", "phase": phase, "session_date": SESSION, "open_at": _iso(OPEN),
                     "close_at": _iso(OPEN + timedelta(minutes=390)), "price": price, "today_bars": bars, "daily": _daily()},
                **extra)


def _now(minutes=60, seconds=20):
    return _iso(OPEN + timedelta(minutes=minutes, seconds=seconds))


# ---------------------------------------------------------------- validator

def test_valid_plan_keeps_scenarios_and_a_stable_hash():
    plan, issues = _plan()
    assert issues == [] and plan["status"] == "ACTIVE" and P.plan_intact(plan)
    assert [s["id"] for s in plan["scenarios"]] == ["breakout_1", "accel_1"]
    assert plan["scenarios"][0]["trigger"] == {"price_above": 10400, "confirm": "5m_close", "bars": 2}
    assert plan["scenarios"][0]["max_chase_pct"] == 2.0 and plan["scenarios"][0]["target_allocation"] == "0.60"
    assert _plan()[0]["plan_hash"] == plan["plan_hash"]
    tampered = dict(plan, valid_for="2026-10-06")
    assert not P.plan_intact(tampered)
    assert P.evaluate_plan(tampered, _state(), _evidence(), now=_now())["reason"] == "PLAN_HASH_MISMATCH"


@pytest.mark.parametrize("scenario, reason", [
    (_scenario(price_above=10400, rsi_above=70), "UNKNOWN_CONDITION:rsi_above"),
    (_scenario(target=0.62), "TARGET_NOT_ON_5PCT_GRID"),
    (_scenario(target=0.75), "STEP_TOO_LARGE"),
    (_scenario(target=0.45), "TARGET_NOT_ABOVE_ALLOCATION"),
    (_scenario(target=1.05), "INVALID_TARGET"),
    (_scenario(kind="moonshot"), "UNKNOWN_TYPE"),
    (_scenario(zone_low=9800, zone_high=9900), "ZONE_INCOMPLETE"),
    (_scenario(zone_low=9900, zone_high=9800, reclaim_above=10000), "ZONE_ORDER"),
    (_scenario(volume_pace_min=2), "NO_TRIGGER_LEVEL"),
    (_scenario(price_above=10400, confirm="weekly_close"), "INVALID_VALUE:confirm"),
    (_scenario(price_above=10400, confirm="daily_close", bars=2), "CONFIRM_MISMATCH"),
    (_scenario(price_above=10400, hold_above_open_minutes=7), "INVALID_VALUE:hold_above_open_minutes"),
    (_scenario(new_closing_high_lookback=60), "INVALID_VALUE:new_closing_high_lookback"),
    (_scenario(price_above=-1), "INVALID_VALUE:price_above"),
])
def test_invalid_scenarios_are_dropped_with_a_reason(scenario, reason):
    plan, issues = _plan(scenario, _scenario("accel_1", "acceleration", 0.65, gap_up_min_pct=3))
    assert [s["id"] for s in plan["scenarios"]] == ["accel_1"]
    assert {"id": scenario["id"], "reason": reason} in issues and plan["dropped"] == issues


def test_pyramid_step_scenario_count_duplicates_and_chase_clamp():
    # After a 0.10 add the next add may not exceed 0.10 (pyramid).
    plan, issues = _plan(_scenario(target=0.7), _scenario("b2", target=0.65), allocation="0.55", last_step="0.10")
    assert [s["id"] for s in plan["scenarios"]] == ["b2"] and issues[0]["reason"] == "PYRAMID_STEP"
    for count in (1, 5):
        assert P.validate_plan({"scenarios": [_scenario(f"s{i}") for i in range(count)]}, market="KR",
                               source="BUY", created_at=_iso(OPEN), valid_for=SESSION, allocation="0.45",
                               last_step="0.45") == (None, [{"id": None, "reason": "PLAN_SCENARIO_COUNT"}])
    plan, issues = _plan(_scenario(), _scenario())
    assert len(plan["scenarios"]) == 1 and issues == [{"id": "breakout_1", "reason": "DUPLICATE_ID"}]
    plan, issues = _plan(dict(_scenario(), max_chase_pct=5), dict(_scenario("b2"), max_chase_pct=1))
    assert [s["max_chase_pct"] for s in plan["scenarios"]] == [2.0, 1.0]
    assert issues == [{"id": "breakout_1", "reason": "CHASE_CLAMPED"}] and plan["dropped"] == []
    cancelled, _ = P.validate_plan({"cancel": True, "reason": "thesis broken"}, market="KR", source="REVIEW",
                                   created_at=_iso(OPEN), valid_for=SESSION, allocation="0.45", last_step="0.45")
    assert cancelled["status"] == "CANCELLED" and cancelled["cancel_reason"] == "thesis broken"


def test_session_dates_for_buy_and_review_plans():
    kr_morning = _iso(datetime(2026, 10, 2, 9, 35, tzinfo=SEOUL))
    assert P.buy_valid_for("KR", kr_morning) == "2026-10-05"  # Friday entry -> Monday
    assert P.review_valid_for("KR", kr_morning) == "2026-10-02"  # morning review revises today's plan
    assert P.review_valid_for("KR", _iso(datetime(2026, 10, 2, 14, 46, tzinfo=SEOUL))) == "2026-10-05"
    us_after_close = _iso(datetime(2026, 10, 2, 10, 15, tzinfo=SEOUL))  # 21:15 New York, Thursday
    assert P.entry_session_date("US", us_after_close).isoformat() == "2026-10-02"  # reserved fill on Friday
    assert P.buy_valid_for("US", us_after_close) == "2026-10-05"
    assert P.review_valid_for("US", us_after_close) == "2026-10-02"
    assert P.review_valid_for("US", _iso(datetime(2026, 10, 2, 14, 30, tzinfo=SEOUL))) == "2026-10-02"
    assert P.session_index("2026-10-02", "2026-10-05") == 2


# ---------------------------------------------------------------- evaluator

def test_breakout_qualifies_on_two_5m_closes_with_the_quote_as_limit():
    plan, _ = _plan()
    decision = P.evaluate_plan(plan, _state(), _evidence(), now=_now())
    assert decision["action"] == "ADD" and decision["scenario_id"] == "breakout_1"
    assert decision["target_allocation"] == "0.60" and decision["delta"] == "0.15"
    assert decision["limit_price"] == 10460 and decision["trigger_price"] == 10400
    assert decision["key"] == "add-plan:2026-10-05:breakout_1" and decision["lens"] == ["oneil"]
    assert decision["risk_clipped"] is False


def test_a_single_spike_does_not_qualify():
    plan, _ = _plan(_scenario(), _scenario("b2", price_above=10600))
    for closes, price in (((10300, 10460), 10460), ((10460, 10300), 10460), ((10450, 10460), 10390)):
        decision = P.evaluate_plan(plan, _state(), _evidence(closes=closes, price=price), now=_now())
        assert decision["action"] == "WAIT" and decision["reasons"]["breakout_1"] == "PRICE_NOT_ABOVE"


def test_daily_close_pullback_reclaim_waits_for_the_close():
    pullback = _scenario("pullback_1", "pullback_reclaim", 0.55, ("oneil", "minervini"), zone_low=9800,
                         zone_high=9950, reclaim_above=10100, volume_dry_up_max=0.8, confirm="daily_close")
    plan, issues = _plan(pullback, _scenario(price_above=11000))
    assert issues == []
    daily = _daily(closes=[10000] * 18 + [9900, 9960], lows=[9900] * 18 + [9850, 9880])
    daily[-2]["volume"] = 600  # dry-up on the pullback day
    intraday = _evidence(closes=(10200, 10210), price=10210, daily=daily)
    assert P.evaluate_plan(plan, _state(), intraday, now=_now())["reasons"]["pullback_1"] == "WAITING_FOR_CLOSE"
    close = dict(intraday, phase="CLOSE", price=10150,
                 today_daily={"date": SESSION, "open": 9990, "high": 10200, "low": 9970, "close": 10150, "volume": 1200})
    decision = P.evaluate_plan(plan, _state(), close, now=_iso(OPEN + timedelta(hours=7)))
    assert decision["action"] == "ADD" and decision["scenario_id"] == "pullback_1" and decision["phase"] == "CLOSE"
    # No dry-up, no touch, or an undercut close -> no add.
    wet = [dict(b, volume=1000) for b in daily]
    assert P.evaluate_plan(plan, _state(), dict(close, daily=wet), now=_now(420))["reasons"]["pullback_1"] == \
        "NO_VOLUME_DRY_UP"
    high = _daily(closes=[10000] * 20, lows=[9990] * 20)
    high[-2]["volume"] = 600
    assert P.evaluate_plan(plan, _state(), dict(close, daily=high, today_daily=dict(close["today_daily"], low=9990)),
                           now=_now(420))["reasons"]["pullback_1"] == "ZONE_NOT_TOUCHED"
    undercut = [dict(b) for b in daily]
    undercut[-3]["close"] = 9700
    assert P.evaluate_plan(plan, _state(), dict(close, daily=undercut), now=_now(420))["reasons"]["pullback_1"] == \
        "ZONE_UNDERCUT"


def test_chase_limit_profitable_only_and_risk_clip():
    plan, _ = _plan()
    chased = P.evaluate_plan(plan, _state(), _evidence(closes=(10450, 10620), price=10620), now=_now())
    assert chased["reasons"]["breakout_1"] == "CHASE_LIMIT"  # 10400 x 1.02 = 10608
    below_average = _state(legs=[{"kind": "INITIAL", "allocation": "0.45", "price": "10500", "at": "x"}])
    assert P.evaluate_plan(plan, below_average, _evidence(), now=_now())["reason"] == "NOT_PROFITABLE"
    # Far above the stop the add is clipped to the initial-entry risk of one slot.
    far, _ = _plan(_scenario(target=0.7, price_above=11900), _scenario("b2", price_above=13000))
    decision = P.evaluate_plan(far, _state(), _evidence(closes=(11950, 12000), price=12000), now=_now())
    assert decision["action"] == "ADD" and decision["risk_clipped"] is True
    assert decision["target_allocation"] == "0.60" and decision["planned_target"] == "0.70"
    clip = P.risk_clip(legs=_state()["legs"], target="0.70", price=12000, initial_entry=10000, initial_stop=9300,
                       current_stop=9300, fee_rate="0.001")
    assert clip == Decimal("0.60")
    # A risk budget already spent leaves no add.
    full = P.risk_clip(legs=[{"allocation": "0.95", "price": "10000"}], target="1.0", price=10100,
                       initial_entry=10000, initial_stop=9300, current_stop=9300, fee_rate="0.001")
    assert full == Decimal("0.95")


def test_one_add_per_session_with_one_acceleration_exception():
    accel = _scenario("accel_1", "acceleration", 0.65, ("druckenmiller",), gap_up_min_pct=3,
                      hold_above_open_minutes=30, volume_pace_min=2.0)
    plan, _ = _plan(_scenario(), accel)  # BUY plan at 0.45; breakout already added to 0.60 today
    added = _state("0.60", legs=_state()["legs"] + [{"kind": "ADD", "allocation": "0.15", "price": "10450",
                                                     "session": SESSION, "scenario_type": "breakout",
                                                     "bar_end": "add-plan:2026-10-05:breakout_1"}])
    evidence = _evidence(closes=(10500, 10520), price=10520, open_price=10350, volume=40,
                         prior_cumulative={"end_at": _iso(OPEN + timedelta(minutes=60)), "samples": [200] * 20})
    decision = P.evaluate_plan(plan, added, evidence, now=_now())
    assert decision["action"] == "ADD" and decision["scenario_id"] == "accel_1"
    assert decision["reasons"]["breakout_1"] == "DONE_THIS_SESSION"
    assert decision["target_allocation"] == "0.65" and decision["trigger_price"] == 10350
    accelerated = dict(added, legs=added["legs"] + [{"kind": "ADD", "allocation": "0.05", "price": "10520",
                                                     "session": SESSION, "scenario_type": "acceleration",
                                                     "bar_end": "add-plan:2026-10-05:accel_1"}],
                       allocation="0.65")
    again = P.evaluate_plan(plan, accelerated, evidence, now=_now())
    assert again["action"] == "WAIT" and set(again["reasons"].values()) == {"DONE_THIS_SESSION"}
    # A non-acceleration scenario after one add in the session waits.
    other, _ = _plan(_scenario(), _scenario("b2", price_above=10500, target=0.75), allocation="0.60",
                     last_step="0.15")
    assert P.evaluate_plan(other, added, evidence, now=_now())["reasons"]["b2"] == "ONE_ADD_PER_SESSION"


def test_acceleration_needs_gap_hold_and_volume_pace():
    accel = _scenario("accel_1", "acceleration", 0.65, ("druckenmiller",), gap_up_min_pct=3,
                      hold_above_open_minutes=30, volume_pace_min=2.0)
    plan, _ = _plan(accel, _scenario(price_above=11000))
    pace = {"end_at": _iso(OPEN + timedelta(minutes=60)), "samples": [200] * 20}
    ok = _evidence(closes=(10400, 10420), price=10420, open_price=10350, volume=40, prior_cumulative=pace)
    assert P.evaluate_plan(plan, _state(), ok, now=_now())["action"] == "ADD"  # 480 / 200 = 2.4x
    cases = [
        ({"open_price": 10250}, "GAP_NOT_MET"),
        ({"volume": 30}, "VOLUME_PACE_LOW"),  # 360 / 200 = 1.8x
        ({"prior_cumulative": None}, "VOLUME_PACE_UNAVAILABLE"),
        ({"minutes": 25}, "HOLD_ABOVE_OPEN_PENDING"),
        ({"closes": (10340, 10420)}, "LOST_SESSION_OPEN"),
    ]
    for change, reason in cases:
        kwargs = {"closes": (10400, 10420), "price": 10420, "open_price": 10350, "volume": 40, "prior_cumulative": pace}
        kwargs.update(change)
        minutes = kwargs.pop("minutes", 60)
        evidence = _evidence(minutes=minutes, **kwargs)
        if evidence.get("prior_cumulative"):
            evidence["prior_cumulative"] = dict(pace, end_at=evidence["today_bars"][-1]["end_at"])
        assert P.evaluate_plan(plan, _state(), evidence, now=_now(minutes))["reasons"]["accel_1"] == reason, reason


def test_new_closing_high_hold_sessions_and_earliest_session():
    high = _scenario("nch_1", "new_closing_high", 0.6, new_closing_high_lookback=10, confirm="daily_close")
    hold = _scenario("hold_1", "breakout", 0.6, price_above=9950, hold_sessions=3)
    early = _scenario("early_1", "breakout", 0.6, price_above=10400, earliest_session=3)
    plan, _ = _plan(high, hold, early)
    evidence = _evidence(closes=(10080, 10100), price=10100)  # within 2% of the 9,950 level
    decision = P.evaluate_plan(plan, _state(), evidence, now=_now())
    assert decision["scenario_id"] == "hold_1"  # last 3 closes 10000 > 9950
    assert decision["reasons"] == {"nch_1": "WAITING_FOR_CLOSE"}
    assert P.evaluate_plan(plan, _state(), dict(evidence, daily=_daily([10000] * 18 + [9900, 10000])),
                           now=_now())["reasons"]["early_1"] == "TOO_EARLY"
    close = dict(evidence, phase="CLOSE", price=10150,
                 today_daily={"date": SESSION, "open": 10000, "high": 10200, "low": 9990, "close": 10150, "volume": 900})
    assert P.evaluate_plan(_plan(high, early)[0], _state(), close, now=_now(420))["scenario_id"] == "nch_1"


def test_invalidation_expiry_sell_day_and_no_regime_ban():
    plan, _ = _plan(invalidation={"close_below": 9950, "stall_sessions": 3})
    weak = dict(_evidence(), daily=_daily(closes=[10000] * 19 + [9900]))
    assert P.evaluate_plan(plan, _state(), weak, now=_now())["action"] == "INVALIDATED"
    assert P.evaluate_plan(plan, _state(), weak, now=_now())["reason"] == "CLOSE_BELOW"
    stalled = dict(_evidence(), daily=_daily(closes=[10000] * 16 + [10200, 10100, 10050, 10000]))
    stall_state = _state(entry_session="2026-09-25")
    assert P.evaluate_plan(plan, stall_state, stalled, now=_now())["reason"] == "STALL"
    assert P.evaluate_plan(plan, _state(), _evidence(session_date="2026-10-06"), now=_now())["reason"] == \
        "PLAN_NOT_VALID_FOR_SESSION"
    assert P.evaluate_plan(plan, _state(blocked="LOOP_SELL_ORDER"), _evidence(), now=_now())["reason"] == \
        "SELL_DAY_BLOCK"
    assert P.evaluate_plan(None, _state(), _evidence(), now=_now())["reason"] == "NO_PLAN"
    cancelled, _ = P.validate_plan({"cancel": True}, market="KR", source="REVIEW", created_at=_iso(OPEN),
                                   valid_for=SESSION, allocation="0.45", last_step="0.45")
    assert P.evaluate_plan(cancelled, _state(), _evidence(), now=_now())["reason"] == "PLAN_CANCELLED"
    # Market regime is never an input: a bear tag changes nothing.
    calm = P.evaluate_plan(plan, _state(), _evidence(), now=_now())
    bear = P.evaluate_plan(plan, _state(), _evidence(market_regime="strong_bear", market_pulse="CORRECTION"),
                           now=_now())
    assert calm["action"] == bear["action"] == "ADD" and calm["target_allocation"] == bear["target_allocation"]


def test_llm_json_parser_keeps_a_four_level_add_plan():
    from cores.utils import parse_llm_json
    decision = {"should_sell": True, "analysis_summary": {}, "next_session_add_plan": {
        "invalidation": {"close_below": 1}, "scenarios": [_scenario()]}}
    for text in (json.dumps(decision), "Decision follows.\n" + json.dumps(decision) + "\nDone.",
                 "```json\n" + json.dumps(decision) + "\n```"):
        assert parse_llm_json(text, context="test") == decision
    assert parse_llm_json('{"a": 1, "b": [1, 2,]}', context="test") == {"a": 1, "b": [1, 2]}  # repair path kept


# ---------------------------------------------------------------- LIVE worker path

class _FakeTrading:
    calls = []  # noqa: RUF012 - shared fake order log

    def __init__(self, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute_buy(self, **kwargs):
        _FakeTrading.calls.append(kwargs)
        return {"success": True, "status": "submitted"}


def _decision_bars_agent():
    bars, day = [], date(2026, 9, 1)
    while day < date(2026, 10, 2):
        if day.weekday() < 5:
            bars.append({"date": day.isoformat(), "open": 9900.0, "high": 10100.0, "low": 9800.0, "close": 10000.0,
                             "volume": 1000.0})
        day += timedelta(days=1)
    entered = datetime(2026, 10, 2, 9, 35, tzinfo=SEOUL)
    return SimpleNamespace(_decision_input_bars={"005930": {
        "market": "KR", "bars": bars, "captured_at": _iso(entered - timedelta(minutes=10))}}), entered


@pytest.fixture
def live_worker(tmp_path, monkeypatch):
    from observability import b3_ae_capture as capture
    from observability import events
    from prism_core import execution_service
    from prism_core import micro_split_live as live
    from prism_core.b3_ae_shadow import B3AeShadowStore
    from prism_core.b3_ae_worker import B3AeWorker

    monkeypatch.setenv("MICRO_SPLIT_LIVE_ENABLED", "true")
    monkeypatch.delenv("MICRO_SPLIT_LIVE_ADDS_ENABLED", raising=False)
    monkeypatch.delenv("MICRO_SPLIT_LIVE_MARKETS", raising=False)
    monkeypatch.setenv("B3_AE_SHADOW_DB", str(tmp_path / "b3.sqlite"))
    emitted = []
    monkeypatch.setattr(events, "emit_event", lambda name, **kw: emitted.append((name, kw)))
    monkeypatch.setattr(execution_service.ExecutionService, "domestic", _FakeTrading, raising=False)
    _FakeTrading.calls.clear()

    agent, entered = _decision_bars_agent()
    plan = capture.build_plan(agent, market="KR", ticker="005930", entry_price=10000, stop_loss=9300,
                              decision_ref="report:x.pdf", entered_at=_iso(entered))
    state = capture.capture_entry(agent, market="KR", ticker="005930", account_key="acc", position_id="legacy:KR:7",
                                  entry_price=10000, stop_loss=9300, decision_ref="report:x.pdf", plan=plan,
                                  mode="LIVE")
    scenario = {"stop_loss": 9300, "micro_split": live.entry_record(plan=plan, unit_amount=1_000_000, market="KR",
                                                                    entered_at=plan["created_at"])}
    raw = {"scenarios": [_scenario(target=0.9), _scenario("accel_1", "acceleration", 0.95, gap_up_min_pct=3)]}
    live.attach_buy_add_plan(scenario, market="KR", ticker="005930", raw=raw)
    assert scenario["micro_split"]["add_plan"]["valid_for"] == SESSION
    db = tmp_path / "holdings.sqlite"
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE stock_holdings (id INTEGER PRIMARY KEY, ticker TEXT, company_name TEXT, "
                 "account_key TEXT, scenario TEXT, buy_price REAL, stop_loss REAL)")
    conn.execute("CREATE TABLE trading_history (ticker TEXT, account_key TEXT, buy_date TEXT, sell_date TEXT, "
                 "sell_price REAL, exit_kind TEXT)")
    conn.execute("INSERT INTO stock_holdings VALUES (7, '005930', '삼성전자', 'acc', ?, 10000, 9300)",
                 (json.dumps(scenario, ensure_ascii=False),))
    conn.commit()
    executor = SimpleNamespace(conn=conn, cursor=conn.cursor(), db_path=str(db), message_queue=[], _msg_types=[],
                               account_configs=[{"account_key": "acc", "name": "primary"}],
                               _set_active_account=lambda account: None)
    store = B3AeShadowStore(tmp_path / "b3.sqlite")
    now = OPEN + timedelta(minutes=60, seconds=20)
    inputs = []

    def add_inputs(symbol, at, phase, active_plan):
        inputs.append((symbol, phase, active_plan["plan_hash"]))
        return _evidence()

    def live_add(campaign, decision, at):
        if not live.plan_adds_enabled("KR"):
            return {"status": "ADDS_PAUSED"}
        return asyncio.run(live.execute_add(executor, market="KR", campaign=campaign, decision=decision, now=at))

    def intraday(*args):
        raise RuntimeError("B3 ladder inputs are outside this test")

    providers = {"session_open": lambda moment: True, "intraday": intraday, "quote": lambda *a: {}, "market": dict,
                     "add_inputs": add_inputs, "live_add": live_add}
    worker = B3AeWorker("KR", store=store, holdings_db=db, providers=providers, clock=lambda: _iso(now))
    return SimpleNamespace(worker=worker, conn=conn, state=state, emitted=emitted, inputs=inputs, store=store,
                           executor=executor)


def _plan_rows(result):
    return [row for row in result["rows"] if row.get("kind") == "add_plan"]


def test_worker_executes_a_qualified_plan_add_once_per_scenario_and_session(live_worker):
    result = live_worker.worker.once()
    rows = _plan_rows(result)
    assert rows[0]["status"] == "ADD" and rows[0]["live"]["status"] == "EXECUTED"
    assert result["orders_submitted"] == 1
    order = _FakeTrading.calls[0]
    assert order["limit_price"] == 10460 and order["strict_budget"] is True
    assert order["buy_amount"] == 122_300  # (0.90 - 0.7777) x 1,000,000
    block = json.loads(live_worker.conn.execute("SELECT scenario FROM stock_holdings").fetchone()[0])["micro_split"]
    assert block["allocation"] == "0.9000" and block["legs"][-1]["bar_end"] == "add-plan:2026-10-05:breakout_1"
    assert block["legs"][-1]["scenario_id"] == "breakout_1" and block["legs"][-1]["session"] == SESSION
    assert "시나리오: 돌파 (breakout_1)" in live_worker.executor.message_queue[0]
    names = [name for name, _ in live_worker.emitted]
    assert "micro_split.add_plan_qualified" in names and "micro_split.add_executed" in names
    qualified = dict(live_worker.emitted)["micro_split.add_plan_qualified"]["attributes"]
    assert qualified["scenario_id"] == "breakout_1" and qualified["lens"] == ["oneil"] and qualified["plan_hash"]
    # The same boundary again: the leg key blocks a second order for this scenario and session.
    again = live_worker.worker.once()
    assert _plan_rows(again)[0]["status"] == "WAIT" and again["orders_submitted"] == 0
    assert len(_FakeTrading.calls) == 1
    kinds = [r[0] for r in sqlite3.connect(live_worker.store.path).execute(
        "SELECT kind FROM b3_events WHERE kind LIKE 'add_plan.%'")]
    assert "add_plan.qualified" in kinds and "add_plan.executed" in kinds


def test_kill_switch_stops_plan_adds_and_sell_day_blocks(live_worker, monkeypatch):
    monkeypatch.setenv("MICRO_SPLIT_LIVE_ADDS_ENABLED", "false")
    result = live_worker.worker.once()
    assert _plan_rows(result) == [] and live_worker.inputs == [] and _FakeTrading.calls == []
    monkeypatch.setenv("MICRO_SPLIT_LIVE_ADDS_ENABLED", "true")
    live_worker.conn.execute("CREATE TABLE loop_a_inflight_orders (ticker TEXT, market TEXT, submitted_ts TEXT)")
    live_worker.conn.execute("INSERT INTO loop_a_inflight_orders VALUES ('005930', 'KR', ?)",
                             (_iso(OPEN + timedelta(minutes=30)),))
    live_worker.conn.commit()
    rows = _plan_rows(live_worker.worker.once())
    assert rows[0]["reason"] == "SELL_DAY_BLOCK" and _FakeTrading.calls == []


def test_close_phase_runs_once_per_session(live_worker):
    worker = live_worker.worker
    worker.providers["session_open"] = lambda moment: False
    worker.providers["close_ready"] = lambda moment: SESSION
    worker.providers["add_inputs"] = lambda symbol, at, phase, plan: dict(
        _evidence(phase="CLOSE", price=10450), today_daily={"date": SESSION, "open": 10000, "high": 10500, "low": 9990,
                                                                "close": 10450, "volume": 900})
    first = _plan_rows(worker.once())
    assert [r["phase"] for r in first] == ["CLOSE"] and first[0]["reason"] == "NO_SCENARIO_QUALIFIED"
    assert _plan_rows(worker.once()) == []  # once per session


def test_lens_labels_are_normalized_and_never_drop_a_scenario():
    # 2026-10-02 production e2e: "druck enmiller" dropped an executable scenario.
    plan, issues = _plan(_scenario(lens=("oneil", "druck enmiller", "Quant-Risk", "soros")),
                         _scenario("accel_1", "acceleration", 0.65, gap_up_min_pct=3))
    assert issues == [] and [s["id"] for s in plan["scenarios"]] == ["breakout_1", "accel_1"]
    assert plan["scenarios"][0]["lens"] == ["oneil", "druckenmiller", "quant_risk"]
    plan, _ = _plan(_scenario(lens=("soros",)), _scenario("accel_1", "acceleration", 0.65, gap_up_min_pct=3))
    assert plan["scenarios"][0]["lens"] == []
