"""Re-entry v3 LIVE (prism_core/reentry_v3_live.py) with a fake broker / fake agent; KR and US aligned."""
import ast
import asyncio
import copy
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

import pytest

from prism_core import reentry_campaign as C
from prism_core import reentry_v3_live as LIVE
from prism_core.isolated_agent_runtime import require_execution_runtime
from prism_core.isolated_strategy_effects import EffectsFailure, effects_for
from prism_core.trading_scenario_contract import apply_buy_scenario_contract

ROOT = Path(__file__).resolve().parents[1]
KR_1400 = datetime(2026, 10, 7, 5, 0, tzinfo=timezone.utc)       # Wed 14:00 KST
US_1350 = datetime(2026, 10, 7, 17, 50, tzinfo=timezone.utc)     # Wed 13:50 EDT
DAY = "2026-10-07"


@pytest.fixture
def live_on(monkeypatch):
    monkeypatch.setenv("REENTRY_V3_LIVE_ENABLED", "true")
    monkeypatch.delenv("REENTRY_V3_LIVE_MARKETS", raising=False)


def _item(n=1, trigger="R2S", source="STOP_EXIT", attempt=1):
    return {"event_id": f"ev{n}", "watch_ref": f"w{n}", "ticker": f"00000{n}", "source": source,
            "trigger": trigger, "trigger_date": DAY, "decision_price": 12150.0,
            "decision_time": "2026-10-07T05:00:00+00:00", "level": {"L": 11950.0, "basis": "primary_support"},
            "stop": 11591.5, "target": 12990.0, "stop_rule": "STRUCT", "target_rule": "nearest",
            "attempts": {"L97": {"attempt": attempt, "max": 3, "prior": []}}, "report_ref": None}


def _record(n=1, decision="진입", status="OK"):
    return {"event_id": f"ev{n}", "trigger_date": DAY, "status": status, "approved": decision == "진입",
            "scenario": {"decision": decision, "buy_score": 7, "min_score": 5, "entry_price": 12100,
                         "target_price": 13000, "stop_loss": 11300, "risk_reward_ratio": 1.1, "sector": "반도체",
                         "rationale": "재진입 근거", "add_plan": {"scenarios": []}}}


def _state(n=3):
    return {"watches": [{"watch_id": f"w{k}", "ticker": f"00000{k}", "source": "STOP_EXIT",
                         "decisions": {DAY: {"by_rule": {}}}} for k in range(1, n + 1)]}


def _context(entry):
    return {"company_name": "와이씨", "sector": "반도체", "bars": [{"date": "2026-10-06", "close": 1.0}],
            "report_path": None}


class FakeExecutor:
    def __init__(self, outcome=None, error=None):
        self.calls, self.outcome, self.error = [], outcome, error

    def __call__(self, market, entries):
        self.calls.append((market, entries))
        if self.error:
            raise self.error
        return {e["key"]: dict(self.outcome or {"bought": True, "reason": "bought", "holding_ids": [7],
                                                "entry_price": 12150.0, "account_refs": ["acct-x"]})
                for e in entries}


def _process(tmp_path, records, items, executor, now=KR_1400, market="KR", state=None, emitted=None):
    state = state or _state()
    return state, LIVE.process(state, market, DAY, records, items, tmp_path / "live.jsonl", executor,
                               entry_context=_context, now=now,
                               emit=(lambda *a: emitted.append(a)) if emitted is not None else None)


# ---------------------------------------------------------------- switches, hours, labels
def test_kill_switch_defaults_off_and_markets(monkeypatch):
    monkeypatch.delenv("REENTRY_V3_LIVE_ENABLED", raising=False)
    assert not LIVE.live_enabled("KR") and not LIVE.live_enabled("US")
    monkeypatch.setenv("REENTRY_V3_LIVE_ENABLED", "true")
    assert LIVE.live_enabled("KR") and LIVE.live_enabled("US")
    monkeypatch.setenv("REENTRY_V3_LIVE_MARKETS", "US")
    assert not LIVE.live_enabled("KR") and LIVE.live_enabled("US")


def test_regular_hours_only_never_in_auctions():
    utc = lambda h, m: datetime(2026, 10, 7, h, m, tzinfo=timezone.utc)  # noqa: E731
    assert LIVE.in_safe_window("KR", KR_1400)
    assert not LIVE.in_safe_window("KR", utc(6, 20))       # 15:20 KST closing auction
    assert not LIVE.in_safe_window("KR", utc(23, 50))      # 08:50 KST opening auction
    assert LIVE.in_safe_window("US", US_1350)
    assert not LIVE.in_safe_window("US", utc(19, 50))      # 15:50 ET closing-auction cutoffs
    assert not LIVE.in_safe_window("US", utc(13, 25))      # 09:25 ET pre-open
    assert not LIVE.in_safe_window("KR", datetime(2026, 10, 10, 5, 0, tzinfo=timezone.utc))   # Saturday


def test_scenario_carries_reentry_metadata_capped_stop_and_buy_rule_target():
    scenario = LIVE.build_scenario(_item(trigger="R2S", attempt=2), _record(), "KR")
    assert scenario["reentry"] == {
        "version": "reentry_v3", "signal": "RETEST", "signal_ko": "기준 가격 눌림 지지 매수", "attempt": 2,
        "max_attempts": 3, "attempt_label": "2/3", "level": 11950.0, "level_basis": "primary_support",
        "watch_id": "w1", "source": "STOP_EXIT", "trigger_date": DAY, "decision_price": 12150.0,
        "decision_time": "2026-10-07T05:00:00+00:00", "event_id": "ev1", "stop_rule": "STRUCT",
        "target_rule": "nearest"}
    assert scenario["stop_loss"] == 11591.5 and scenario["target_price"] == 12990.0
    assert scenario["entry_price"] == 12150.0
    assert scenario["risk_reward_ratio"] == pytest.approx((12990 - 12150) / (12150 - 11591.5), abs=1e-4)
    assert scenario["expected_loss_pct"] == pytest.approx((1 - 11591.5 / 12150) * 100, abs=1e-4)
    assert scenario["trigger_type"] == "재진입(기준 가격 눌림 지지 매수)"
    assert scenario["_decision_id"] == "reentry_v3:ev1" and scenario["add_plan"] == {"scenarios": []}
    assert LIVE.trigger_label("US", "SHAKEOUT_RECLAIM") == "Re-entry (SHAKEOUT_RECLAIM)"
    assert LIVE.build_scenario(_item(trigger="R1C"), _record(), "US")["reentry"]["signal"] == "REBREAK"


def test_buy_message_says_plainly_it_is_a_reentry_and_why():
    line = LIVE.entry_message_line(LIVE.build_scenario(_item(trigger="SHAKEOUT_RECLAIM"), _record(), "KR"), "KR")
    assert line.startswith("🔁 재진입 매수 (흔들기 후 회복 매수, 1/3번째 시도)")
    assert "이전에 손절했던 종목" in line and "11,950원" in line and "거래량을 동반해 다시 회복" in line
    assert "$11,950.00" in LIVE.entry_message_line(LIVE.build_scenario(_item(), _record(), "US"), "US")
    assert LIVE.entry_message_line({"decision": "진입"}, "KR") == "" and LIVE.entry_message_line(None, "US") == ""


# ---------------------------------------------------------------- process: approve -> one buy, safety
def test_approved_recheck_places_one_buy_with_the_scenario_fields(tmp_path, live_on):
    executor, emitted = FakeExecutor(), []
    state, results = _process(tmp_path, [_record()], {"ev1": _item()}, executor, emitted=emitted)
    assert [r["status"] for r in results] == ["BOUGHT"] and len(executor.calls) == 1
    market, entries = executor.calls[0]
    entry = entries[0]
    assert market == "KR" and entry["ticker"] == "000001" and entry["price"] == 12150.0
    assert entry["scenario"]["reentry"]["watch_id"] == "w1" and entry["scenario"]["stop_loss"] == 11591.5
    assert entry["trigger_type"] == "재진입(기준 가격 눌림 지지 매수)" and entry["company_name"] == "와이씨"
    live = state["watches"][0]["live"][DAY]
    assert live["status"] == "BOUGHT" and live["holding_ids"] == [7] and live["entry_price"] == 12150.0
    assert state["watches"][0]["decisions"][DAY]["live"]["status"] == "BOUGHT"
    journal = LIVE.load_journal(tmp_path / "live.jsonl")
    assert [j["phase"] for j in journal] == ["submit", "result"] and journal[0]["key"] == LIVE.idempotency_key("w1", DAY)
    assert [e[0] for e in emitted] == ["reentry_v3.live_entry"]


def test_rerun_never_buys_twice(tmp_path, live_on):
    executor = FakeExecutor()
    _process(tmp_path, [_record()], {"ev1": _item()}, executor)
    _, results = _process(tmp_path, [_record()], {"ev1": _item()}, executor)
    assert [r["reason"] for r in results] == ["duplicate"] and len(executor.calls) == 1


def test_daily_cap_of_two_per_market(tmp_path, live_on):
    executor = FakeExecutor()
    records = [_record(k) for k in (1, 2, 3)]
    items = {f"ev{k}": _item(k) for k in (1, 2, 3)}
    _, results = _process(tmp_path, records, items, executor)
    assert [r["status"] for r in results] == ["BOUGHT", "BOUGHT", "SKIPPED"] and results[2]["reason"] == "daily_cap"
    assert len(executor.calls[0][1]) == 2
    _, again = _process(tmp_path, [_record(4)], {"ev4": _item(4)}, executor, state=_state(4))
    assert again[0]["reason"] == "daily_cap" and len(executor.calls) == 1


def test_no_buy_outside_regular_hours_or_when_not_approved(tmp_path, live_on):
    executor = FakeExecutor()
    _, results = _process(tmp_path, [_record()], {"ev1": _item()}, executor,
                          now=datetime(2026, 10, 7, 6, 20, tzinfo=timezone.utc))
    assert results[0]["reason"] == "outside_regular_hours" and executor.calls == []
    _, results = _process(tmp_path, [_record(decision="미진입")], {"ev1": _item()}, executor)
    _, errors = _process(tmp_path, [_record(status="ERROR")], {"ev1": _item()}, executor)
    assert results == [] and errors == [] and executor.calls == []


def test_an_exception_is_contained_recorded_and_never_retried_the_same_day(tmp_path, live_on):
    broken, emitted = FakeExecutor(error=RuntimeError("broker down")), []
    state, results = _process(tmp_path, [_record()], {"ev1": _item()}, broken, emitted=emitted)
    assert results[0]["status"] == "ERROR" and results[0]["reason"] == "executor_error:RuntimeError"
    assert state["watches"][0]["live"][DAY]["status"] == "ERROR" and emitted[0][3]["status"] == "ERROR"
    healthy = FakeExecutor()
    _, again = _process(tmp_path, [_record()], {"ev1": _item()}, healthy)
    assert again[0]["reason"] == "duplicate" and healthy.calls == []


# ---------------------------------------------------------------- agent glue (fake agent)
class FakeAgent:
    def __init__(self, accounts=None, outcome=None, error=None):
        self.calls, self.account_configs, self.outcome, self.error = [], accounts or [], outcome, error

    async def enter_reentry_candidate(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return dict(self.outcome or {"bought": True, "reason": "bought", "holding_ids": [len(self.calls)],
                                     "entry_price": kwargs["current_price"], "account_refs": ["acct-a"]})


def _entry(market="KR"):
    scenario = LIVE.build_scenario(_item(), _record(), market)
    return {"key": "k1", "ticker": "000001", "price": 12150.0, "scenario": scenario, "sector": "반도체",
            "company_name": "와이씨", "bars": [{"date": "2026-10-06", "high": 2, "low": 1, "close": 1.5}] * 90,
            "trigger_type": LIVE.trigger_label(market, "RETEST"), "report_path": "/r.md"}


def test_kr_entry_goes_through_the_agent_once_with_trigger_type_and_decision_bars():
    agent = FakeAgent()
    out = asyncio.run(LIVE.enter_with_agent(agent, "KR", _entry(), now=KR_1400))
    assert out["bought"] is True and len(agent.calls) == 1
    call = agent.calls[0]
    assert call["scenario"]["reentry"]["signal"] == "RETEST" and call["sector"] == "반도체"
    assert call["source_decision_id"] == "reentry_v3:ev1" and "account" not in call
    assert agent.trigger_info_map["000001"] == {"trigger_type": "재진입(기준 가격 눌림 지지 매수)",
                                                "trigger_mode": "reentry_v3"}
    assert agent._decision_input_bars["000001"]["market"] == "KR" and len(agent._decision_input_bars["000001"]["bars"]) == 80


def test_us_entry_fans_out_per_account_like_the_batch():
    agent = FakeAgent(accounts=[{"name": "a"}, {"name": "b"}])
    out = asyncio.run(LIVE.enter_with_agent(agent, "US", _entry("US"), now=US_1350))
    assert [c["account"]["name"] for c in agent.calls] == ["a", "b"] and out["holding_ids"] == [1, 2]
    assert agent.calls[0]["report_path"] == "/r.md" and out["bought"] is True


def test_agent_errors_and_auction_hours_never_buy():
    agent = FakeAgent(error=ValueError("x"))
    assert asyncio.run(LIVE.enter_with_agent(agent, "KR", _entry(), now=KR_1400)) == \
        {"bought": False, "reason": "error:ValueError"}
    late = FakeAgent()
    out = asyncio.run(LIVE.enter_with_agent(late, "KR", _entry(),
                                            now=datetime(2026, 10, 7, 6, 21, tzinfo=timezone.utc)))
    assert out["reason"] == "outside_regular_hours" and late.calls == []


def test_entry_subprocess_runner_sends_the_buy_message_once(monkeypatch, live_on):
    import importlib
    tool = importlib.import_module("tools.run_reentry_v3_entry")
    sent = []

    class MessagingAgent(FakeAgent):
        conn = None

        async def send_telegram_message(self, chat_id, await_broadcast=False):
            sent.append((chat_id, await_broadcast))

    async def factory(market):
        return MessagingAgent()
    monkeypatch.setattr(LIVE, "in_safe_window", lambda market, now=None: True)
    outcomes = asyncio.run(tool.run("KR", [dict(_entry(), key="k1")], agent_factory=factory, chat_id="chan"))
    assert outcomes["k1"]["bought"] is True and sent == [("chan", True)]


# ---------------------------------------------------------------- the real agent methods (AST, fake broker)
def _load_method(path, class_name, name, namespace):
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name)
    method = copy.deepcopy(next(n for n in node.body if isinstance(n, ast.AsyncFunctionDef) and n.name == name))
    module = ast.Module(body=[method], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(path), "exec"), namespace)  # noqa: S102 - test-only compile of a real method
    return namespace[name]


def _load_functions(path, names, namespace):
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    nodes = [copy.deepcopy(n) for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    module = ast.Module(body=nodes, type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(path), "exec"), namespace)  # noqa: S102 - test-only
    return namespace


def _base_ns():
    return {"Any": Any, "Dict": Dict, "logger": logging.getLogger("reentry-live-test"), "effects_for": effects_for,
            "EffectsFailure": EffectsFailure, "require_execution_runtime": require_execution_runtime,
            "apply_buy_scenario_contract": apply_buy_scenario_contract}


class _Agent:
    """Holds the stubbed collaborators of a tracker; the real method is bound per test."""

    def __init__(self, table, held=False, slots=0, sector_ok=True, gate=True):
        self.max_slots, self.db_path, self.enable_journal = 10, ":memory:", False
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, ticker TEXT, account_key TEXT, buy_price REAL)")
        self.cursor, self.table, self.calls = self.conn.cursor(), table, []
        self._held, self._slots, self._sector_ok, self._gate = held, slots, sector_ok, gate
        self.trigger_info_map = {}

    async def _is_ticker_in_holdings(self, ticker):
        return self._held

    async def _get_current_slots_count(self):
        return self._slots

    async def _check_sector_diversity(self, sector, is_pyramiding_add=False):
        return self._sector_ok

    def _evaluate_production_buy_gate(self, scenario, price, score_override=None, is_add=False):
        return {"allowed": self._gate, "reason": "ok" if self._gate else "rr_below_floor", "findings": []}

    def _account_scope(self):
        return "acct-key", "main"

    def _set_active_account(self, account):
        self.active_account = account

    def _buy_floor_regime(self):
        return "sideways"

    def _stamp_scenario_market_regime(self, scenario):
        return dict(scenario, _deterministic_market_regime="sideways")

    def _get_trend_facts(self, ticker):
        return "T1_hit: false"

    def _regime_policy_mod(self):
        return None

    async def _refresh_buy_quote(self, ticker):
        return 12150.0

    async def _enter_eligible_candidate(self, **kwargs):     # fake broker: the real order path stops here
        self.calls.append(kwargs)
        self.conn.execute(f"INSERT INTO {self.table} (ticker, account_key, buy_price) VALUES (?,?,?)",
                          (kwargs["ticker"], "acct-key", kwargs["current_price"]))
        return 1


@pytest.fixture
def no_cooldown(monkeypatch):
    import reentry_cooldown
    import cores.regime_policy as regime_policy
    monkeypatch.setattr(reentry_cooldown, "reentry_block", lambda *a, **k: None)
    monkeypatch.setattr(regime_policy, "regime_min_score_floor_enabled", lambda: False)   # env-gated, off here
    monkeypatch.delenv("MICRO_SPLIT_LIVE_ENABLED", raising=False)


def _kr(agent):
    method = _load_method(ROOT / "stock_tracking_enhanced_agent.py", "EnhancedStockTrackingAgent",
                          "enter_reentry_candidate", _base_ns())
    return lambda **kw: method(agent, **kw)


def _us(agent, monkeypatch):
    import prism_core.oneil_routing as routing

    async def not_owned(*a, **k):
        return False
    monkeypatch.setattr(routing, "route_owned_entry", not_owned)
    ns = _load_functions(ROOT / "prism-us/us_stock_tracking_agent.py",
                         {"_safe_number", "_scenario_slot_limit", "_effective_buy_score"}, _base_ns())
    method = _load_method(ROOT / "prism-us/us_stock_tracking_agent.py", "USStockTrackingAgent",
                          "enter_reentry_candidate", ns)
    return lambda **kw: method(agent, account={"name": "main", "account_key": "acct-key"}, **kw)


def _kwargs(market="KR"):
    scenario = LIVE.build_scenario(_item(), _record(), market)
    return dict(ticker="000001", company_name="와이씨", current_price=12150.0, scenario=scenario,
                sector="반도체", source_decision_id=scenario["_decision_id"])


@pytest.mark.parametrize("market", ["KR", "US"])
def test_real_agent_method_buys_once_through_the_batch_entry_pipeline(market, monkeypatch, no_cooldown):
    agent = _Agent("stock_holdings" if market == "KR" else "us_stock_holdings")
    enter = _kr(agent) if market == "KR" else _us(agent, monkeypatch)
    kwargs = _kwargs(market)
    if market == "US":
        kwargs["scenario"] = dict(kwargs["scenario"], decision="entry")
    out = asyncio.run(enter(**kwargs))
    assert out["bought"] is True and out["holding_ids"] == [1] and out["account_refs"] == [LIVE.account_ref("acct-key")]
    assert len(agent.calls) == 1
    call = agent.calls[0]
    assert call["source"] == ("kr_reentry_v3" if market == "KR" else "us_reentry_v3")
    assert call["scenario"]["reentry"]["watch_id"] == "w1" and call["scenario"]["stop_loss"] == 11591.5
    assert call["is_add"] is False and call["rebound_pilot"] is False
    assert call["scenario"]["_decision_context"]["source"] == "reentry_v3"
    assert call["scenario"]["_deterministic_market_regime"] == "sideways"
    assert call["scenario"]["_deterministic_trend_facts"] == "T1_hit: false"


@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize("blocker,reason", [({"held": True}, "already_held"), ({"slots": 10}, "max_slots"),
                                             ({"sector_ok": False}, "sector_limit"),
                                             ({"gate": False}, "buy_gate:rr_below_floor")])
def test_real_agent_method_refuses_without_ordering(market, blocker, reason, monkeypatch, no_cooldown):
    agent = _Agent("stock_holdings" if market == "KR" else "us_stock_holdings", **blocker)
    enter = _kr(agent) if market == "KR" else _us(agent, monkeypatch)
    out = asyncio.run(enter(**_kwargs(market)))
    assert out == {"bought": False, "reason": reason} and agent.calls == []


@pytest.mark.parametrize("market", ["KR", "US"])
def test_real_agent_method_respects_the_reentry_cooldown_and_score(market, monkeypatch, no_cooldown):
    import reentry_cooldown
    agent = _Agent("stock_holdings" if market == "KR" else "us_stock_holdings")
    enter = _kr(agent) if market == "KR" else _us(agent, monkeypatch)
    low = _kwargs(market)
    low["scenario"] = dict(low["scenario"], buy_score=3)
    assert asyncio.run(enter(**low))["reason"].startswith("score_below_min")
    monkeypatch.setattr(reentry_cooldown, "reentry_block", lambda *a, **k: {"after_loss": True, "risk_exit": True})
    monkeypatch.setattr(reentry_cooldown, "COOLDOWN_LIVE", True)
    assert asyncio.run(enter(**_kwargs(market)))["reason"] == "reentry_cooldown" and agent.calls == []


def test_buy_message_builders_add_the_reentry_line_only_for_reentries():
    namespace = {"Dict": Dict, "Any": Any}
    tree = ast.parse((ROOT / "stock_tracking_agent.py").read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "StockTrackingAgent")
    method = copy.deepcopy(next(n for n in node.body if isinstance(n, ast.FunctionDef)
                                and n.name == "_build_pending_kr_entry_message"))
    module = ast.Module(body=[method], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, "stock_tracking_agent.py", "exec"), namespace)  # noqa: S102 - test-only
    build = namespace["_build_pending_kr_entry_message"]

    class Agent:
        def _get_trigger_win_rate(self, trigger_type):
            return ""

        def _parse_price_value(self, value):
            return float(value or 0)
    plain = {"target_price": 13000, "stop_loss": 11300, "sector": "반도체", "rationale": "근거"}
    reentry = LIVE.build_scenario(_item(), _record(), "KR")
    base = build(Agent(), ticker="000001", company_name="와이씨", current_price=12150, scenario=plain,
                 rank_change_msg="", trigger_type="AI Analysis")
    assert "재진입" not in base
    with_line = build(Agent(), ticker="000001", company_name="와이씨", current_price=12150, scenario=reentry,
                      rank_change_msg="", trigger_type="재진입(기준 가격 눌림 지지 매수)")
    assert "🔁 재진입 매수 (기준 가격 눌림 지지 매수, 1/3번째 시도)" in with_line


# ---------------------------------------------------------------- ledger: the real position replaces the virtual one
def test_ledger_follows_the_real_position_and_counts_its_stop():
    import test_reentry_campaign as T
    bars = T.yc_bars()
    setup = T.yc_setup(bars)
    before = C.replay(setup, bars[:71], 69)
    trigger = C.evaluate(setup, bars=bars[:71], i=71, flags=before["flags"], price=12150.0, day_low=12000.0,
                         regime=None)["trigger"]
    live = {"status": "BOUGHT", "key": "k", "holding_ids": [7], "entry_price": 12160.0, "exit": None}
    decision = {"by_rule": {"L97": trigger, "SS": trigger}, "decision_price": 12150.0, "day_low": 12000.0,
                "live": live}
    camp = C.replay(setup, bars, 69, decisions={bars[71]["date"]: decision})["campaigns"]["L97"]
    first = camp["attempts"][0]
    assert first["real"] is True and first["status"] == "OPEN" and first["exit_rule"] == "REAL"
    assert first["live"]["holding_ids"] == [7] and camp["status"] == "ENDED"      # the watch ended, the holding stays
    sold = dict(decision, live=dict(live, exit={"date": bars[74]["date"], "price": 11500.0, "stop": True,
                                                "exit_kind": "stop"}))
    camp = C.replay(setup, bars, 69, decisions={bars[71]["date"]: sold})["campaigns"]["L97"]
    first = camp["attempts"][0]
    assert (first["status"], first["exit_reason"], first["exit_date"], first["real_exit_kind"]) == \
        ("CLOSED", "stop", bars[74]["date"], "stop")
    assert first["ret"] == pytest.approx(11500 / 12150 - 1, abs=1e-6)
    assert bars[74]["date"] in camp["virtual_stop_dates"]


# ---------------------------------------------------------------- runtime wiring
def test_runtime_live_switch_routes_approved_rechecks_to_the_executor(tmp_path, monkeypatch, live_on):
    import test_reentry_campaign as T
    bars, root, sent, run, quote = T._runtime(tmp_path, monkeypatch)
    executor = FakeExecutor()
    run(70, llm_recheck=False)
    summary = run(70, phase="intraday", decision_day=bars[71]["date"], quote_fn=quote, llm_recheck=True,
                  llm=T._fake_llm([]), live_executor=executor,
                  now_fn=lambda: datetime.fromisoformat(bars[71]["date"] + "T05:00:00+00:00"))
    assert summary["mode"] == "LIVE" and summary["live_results"] == {"BOUGHT": 1} and len(executor.calls) == 1
    entry = executor.calls[0][1][0]
    assert entry["scenario"]["reentry"]["signal"] == "RETEST" and entry["company_name"] == "YC"
    assert len(entry["bars"]) == 71 and entry["bars"][-1]["date"] == bars[70]["date"]
    state = json.loads((root / "reentry_v3_state_kr.json").read_text())
    assert state["watches"][0]["live"][bars[71]["date"]]["status"] == "BOUGHT"
    assert "reentry_v3.live_entry" in [n for n, _ in sent]
    # the sale in trading_history closes the ledger attempt as a real stop at the next close run
    conn = sqlite3.connect(tmp_path / "t.sqlite")
    conn.execute("INSERT INTO trading_history VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                 ("acct-key", "000001", "YC", bars[71]["date"] + " 14:00:00", 12150, bars[73]["date"] + " 10:00:00",
                  11500, -5.35, "재진입(기준 가격 눌림 지지 매수)", "stop",
                  json.dumps({"reentry": {"watch_id": state["watches"][0]["watch_id"],
                                          "trigger_date": bars[71]["date"]}})))
    conn.commit()
    conn.close()
    monkeypatch.setattr(LIVE, "account_ref", lambda key: "acct-x")
    run(len(bars) - 1, llm_recheck=False)
    state = json.loads((root / "reentry_v3_state_kr.json").read_text())
    first = state["watches"][0]["ledger"]["campaigns"]["L97"]["attempts"][0]
    assert first["real"] is True and first["exit_reason"] == "stop" and first["exit_date"] == bars[73]["date"]
    again = run(70, phase="intraday", decision_day=bars[71]["date"], quote_fn=quote, llm_recheck=True,
                llm=T._fake_llm([]), live_executor=executor)
    assert len(executor.calls) == 1 and again["live_results"] == {}


def test_runtime_never_orders_with_the_kill_switch_off_or_in_a_dry_run(tmp_path, monkeypatch):
    import test_reentry_campaign as T
    monkeypatch.delenv("REENTRY_V3_LIVE_ENABLED", raising=False)
    bars, root, sent, run, quote = T._runtime(tmp_path, monkeypatch)
    executor = FakeExecutor()
    run(70, llm_recheck=False)
    summary = run(70, phase="intraday", decision_day=bars[71]["date"], quote_fn=quote, llm_recheck=True,
                  llm=T._fake_llm([]), live_executor=executor)
    assert summary["mode"] == "SHADOW" and executor.calls == []
    monkeypatch.setenv("REENTRY_V3_LIVE_ENABLED", "true")
    summary = run(71, phase="intraday", decision_day=bars[72]["date"], quote_fn=quote, dry_run=True,
                  llm_recheck=True, llm=T._fake_llm([]), live_executor=executor)
    assert executor.calls == [] and summary["live_results"] == {}
