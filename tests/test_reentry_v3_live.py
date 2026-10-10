"""Re-entry v3 LIVE (prism_core/reentry_v3_live.py) with a fake broker / fake agent; KR and US aligned."""
import ast
import asyncio
import copy
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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


@pytest.fixture(autouse=True)
def lock_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("PRISM_ENTRY_LOCK_DIR", str(tmp_path / "locks"))


@pytest.fixture
def live_on(monkeypatch):
    monkeypatch.setenv("REENTRY_V3_LIVE_ENABLED", "true")
    monkeypatch.delenv("REENTRY_V3_LIVE_MARKETS", raising=False)


def _item(n=1, trigger="R2S", source="STOP_EXIT", attempt=1, rr=1.5, ticker=None, basis="primary_support",
          live_rule="L97"):
    return {"event_id": f"ev{n}", "watch_ref": f"w{n}", "ticker": ticker or f"00000{n}", "source": source,
            "trigger": trigger, "trigger_date": DAY, "decision_price": 12150.0, "live_rule": live_rule,
            "decision_time": "2026-10-07T05:00:00+00:00", "level": {"L": 11950.0, "basis": basis},
            "stop": 11591.5, "target": 12990.0, "stop_rule": "STRUCT", "target_rule": "nearest", "rr": rr,
            "attempts": {"L97": {"attempt": attempt, "max": 3, "prior": []}}, "report_ref": None,
            "campaign": {"windows": {}}}


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


def _process(tmp_path, records, items, executor, now=KR_1400, market="KR", state=None, emitted=None, db_path=None):
    state = state or _state()
    return state, LIVE.process(state, market, DAY, records, items, tmp_path / "live.jsonl", executor,
                               entry_context=_context, now=now, db_path=db_path,
                               emit=(lambda *a: emitted.append(a)) if emitted is not None else None)


# ---------------------------------------------------------------- switches, hours, deadline, band, labels
def test_kill_switch_defaults_off_and_markets(monkeypatch):
    monkeypatch.delenv("REENTRY_V3_LIVE_ENABLED", raising=False)
    assert not LIVE.live_enabled("KR") and not LIVE.live_enabled("US")
    monkeypatch.setenv("REENTRY_V3_LIVE_ENABLED", "true")
    assert LIVE.live_enabled("KR") and LIVE.live_enabled("US")
    monkeypatch.setenv("REENTRY_V3_LIVE_MARKETS", "US")
    assert not LIVE.live_enabled("KR") and LIVE.live_enabled("US")


def test_regular_hours_only_never_in_auctions_and_an_order_deadline():
    utc = lambda h, m: datetime(2026, 10, 7, h, m, tzinfo=timezone.utc)
    assert LIVE.in_safe_window("KR", KR_1400) and LIVE.before_deadline("KR", KR_1400)
    assert not LIVE.in_safe_window("KR", utc(6, 20))       # 15:20 KST closing auction
    assert not LIVE.in_safe_window("KR", utc(23, 50))      # 08:50 KST opening auction
    assert LIVE.in_safe_window("US", US_1350) and LIVE.before_deadline("US", US_1350)
    assert not LIVE.in_safe_window("US", utc(19, 50))      # 15:50 ET closing-auction cutoffs
    assert not LIVE.in_safe_window("KR", datetime(2026, 10, 10, 5, 0, tzinfo=timezone.utc))   # Saturday
    assert not LIVE.before_deadline("KR", utc(5, 40)) and LIVE.before_deadline("KR", utc(5, 39))   # 14:40 KST
    assert not LIVE.before_deadline("US", utc(18, 25)) and LIVE.before_deadline("US", utc(18, 24))  # 14:25 EDT


def test_signal_price_bands_on_a_fresh_quote():
    assert LIVE.price_in_band("REBREAK", 100, 103) and not LIVE.price_in_band("REBREAK", 100, 100)
    assert not LIVE.price_in_band("REBREAK", 100, 103.1)
    assert LIVE.price_in_band("RETEST", 100, 100) and LIVE.price_in_band("RETEST", 100, 105)
    assert not LIVE.price_in_band("RETEST", 100, 99.9) and not LIVE.price_in_band("RETEST", 100, 105.1)
    assert LIVE.price_in_band("SHAKEOUT_RECLAIM", 100, 105) and not LIVE.price_in_band("SHAKEOUT_RECLAIM", 100, 100)


def test_scenario_carries_reentry_metadata_capped_stop_and_buy_rule_target():
    scenario = LIVE.build_scenario(_item(trigger="R2S", attempt=2), _record(), "KR")
    meta = scenario["reentry"]
    assert (meta["version"], meta["signal"], meta["attempt"], meta["attempt_label"], meta["level"], meta["watch_id"],
            meta["source"], meta["rule"], meta["band_level"]) == \
        ("reentry_v3", "RETEST", 2, "2/3", 11950.0, "w1", "STOP_EXIT", "L97", 11950.0)
    assert scenario["stop_loss"] == 11591.5 and scenario["target_price"] == 12990.0
    assert scenario["entry_price"] == 12150.0
    assert scenario["risk_reward_ratio"] == pytest.approx((12990 - 12150) / (12150 - 11591.5), abs=1e-4)
    assert scenario["trigger_type"] == "재진입(기준 가격 눌림 지지 매수)"
    assert scenario["_decision_id"] == "reentry_v3:ev1" and scenario["add_plan"] == {"scenarios": []}
    assert LIVE.trigger_label("US", "SHAKEOUT_RECLAIM") == "Re-entry (SHAKEOUT_RECLAIM)"
    shake = _item(trigger="SHAKEOUT_RECLAIM")
    shake["campaign"]["windows"] = {"L97": {"R": 11800.0, "line": 11446.0}}
    assert LIVE.build_scenario(shake, _record(), "KR")["reentry"]["band_level"] == 11800.0


@pytest.mark.parametrize("basis,name", [("primary_support", "1차 지지선"), ("secondary_support", "2차 지지선")])
def test_held_off_shakeout_message_names_the_actual_support_reclaim_level(basis, name):
    """A held-off/blocked name that never closed above L reclaims a support, not the 1st resistance."""
    item = _item(trigger="SHAKEOUT_RECLAIM", source="LOCATION_SKIP", basis="primary_resistance")
    item["campaign"] = {"windows": {"L97": {"R": 11400.0, "line": 11058.0}}, "reclaim_basis": basis}
    scenario = LIVE.build_scenario(item, _record(), "KR")
    assert (scenario["reentry"]["band_level"], scenario["reentry"]["band_basis"]) == (11400.0, basis)
    line = LIVE.entry_message_line(scenario, "KR")
    assert f"이전 분석에서 매수를 보류했던 종목입니다. 분석 당시 {name}(11,400원) 아래로 크게 흔들린 뒤" in line
    assert "1차 저항" not in line
    us = LIVE.build_scenario(item, _record(), "US")
    english = {"1차 지지선": "first support", "2차 지지선": "second support"}[name]
    us_line = LIVE.entry_message_line(us, "US")
    assert f"the {english} from the original analysis ($11,400.00)" in us_line and "분석 당시" not in us_line
    # a stopped name (or a held-off name that already closed above L) still reclaims the level itself
    stopped = _item(trigger="SHAKEOUT_RECLAIM")
    stopped["campaign"] = {"windows": {"L97": {"R": 11950.0, "line": 11591.5}}, "reclaim_basis": "L"}
    assert "첫 매수 때 돌파했던 가격대(11,950원) 아래로" in \
        LIVE.entry_message_line(LIVE.build_scenario(stopped, _record(), "KR"), "KR")


def test_stop_cap_follows_the_tighter_tracker_regime():
    scenario = LIVE.build_scenario(_item(), _record(), "KR")
    scenario["stop_loss"] = 11000.0
    capped = LIVE.apply_stop_cap(scenario, 12150.0, "moderate_bear")          # -5% cap
    assert capped["stop_loss"] == pytest.approx(11542.5)
    assert capped["reentry"]["stop_capped_by_tracker_regime"] == "moderate_bear"
    assert capped["expected_loss_pct"] == pytest.approx(5.0, abs=1e-3)
    wide_ok = LIVE.apply_stop_cap(LIVE.build_scenario(_item(), _record(), "KR"), 12150.0, "strong_bull")
    assert wide_ok["stop_loss"] == 11591.5                                      # never widened


@pytest.mark.parametrize("source,basis,expected", [
    ("STOP_EXIT", "primary_support", "이전에 손절했던 종목입니다. 첫 매수 때 돌파했던 가격대(11,950원)까지"),
    ("LOCATION_SKIP", "primary_resistance", "이전 분석에서 매수를 보류했던 종목입니다. 분석 당시 기준 가격(1차 저항, 11,950원)까지"),
    ("ENTER_BLOCKED", "primary_resistance", "이전에 매수 조건에 막혔던 종목입니다. 분석 당시 기준 가격(1차 저항, 11,950원)까지"),
    ("STOP_EXIT", "primary_resistance_fallback", "이전에 손절했던 종목입니다. 분석 당시 기준 가격(1차 저항, 11,950원)까지"),
])
def test_buy_message_says_plainly_it_is_a_reentry_and_why(source, basis, expected):
    line = LIVE.entry_message_line(LIVE.build_scenario(_item(source=source, basis=basis), _record(), "KR"), "KR")
    assert line.startswith("🔁 재진입 매수 (기준 가격 눌림 지지 매수, 1/3번째 시도)\n") and expected in line
    shake = LIVE.entry_message_line(LIVE.build_scenario(_item(trigger="SHAKEOUT_RECLAIM"), _record(), "KR"), "KR")
    assert "아래로 크게 흔들린 뒤 거래량을 동반해 다시 회복했습니다" in shake
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


def test_live_entry_links_new_position_and_original_decision(tmp_path, live_on):
    emitted = []
    item = dict(_item(), original={"decision_id": "report:000001_A_20261001_morning_x.pdf"})
    _process(tmp_path, [_record()], {"ev1": item}, FakeExecutor(), emitted=emitted)
    [(name, _watch, _key, attrs)] = emitted
    assert name == "reentry_v3.live_entry" and attrs["entry_decision_id"] == "reentry_v3:ev1"
    scenario = LIVE.build_scenario(item, _record(), "KR")
    assert scenario["reentry"]["origin_decision_id"] == "report:000001_A_20261001_morning_x.pdf"
    assert LIVE.build_scenario(_item(), _record(), "KR")["reentry"]["origin_decision_id"] is None


def test_only_primary_rule_signals_trade(tmp_path, live_on):
    executor = FakeExecutor()
    _, results = _process(tmp_path, [_record()], {"ev1": _item(live_rule=None)}, executor)
    assert results == [] and executor.calls == []


def test_rerun_never_buys_twice_and_one_ticker_per_session(tmp_path, live_on):
    executor = FakeExecutor()
    _process(tmp_path, [_record()], {"ev1": _item()}, executor)
    _, results = _process(tmp_path, [_record()], {"ev1": _item()}, executor)
    assert [r["reason"] for r in results] == ["duplicate"] and len(executor.calls) == 1
    # a second watch on the same ticker (new enrolment) cannot spend the cap again today
    _, results = _process(tmp_path, [_record(2)], {"ev2": _item(2, ticker="000001")}, executor)
    assert [r["reason"] for r in results] == ["ticker_already_today"] and len(executor.calls) == 1


def test_daily_cap_of_two_per_market_in_rank_order(tmp_path, live_on):
    executor = FakeExecutor()
    records = [_record(k) for k in (1, 2, 3)]
    items = {"ev1": _item(1, attempt=2, rr=3.0), "ev2": _item(2, attempt=1, rr=1.2), "ev3": _item(3, attempt=1, rr=2.0)}
    _, results = _process(tmp_path, records, items, executor)
    assert [(r["ticker"], r["status"]) for r in results] == [("000003", "BOUGHT"), ("000002", "BOUGHT"),
                                                             ("000001", "SKIPPED")]
    assert results[2]["reason"] == "daily_cap" and len(executor.calls[0][1]) == 2
    _, again = _process(tmp_path, [_record(4)], {"ev4": _item(4)}, executor, state=_state(4))
    assert again[0]["reason"] == "daily_cap" and len(executor.calls) == 1


def test_no_buy_after_the_deadline_outside_hours_or_when_not_approved(tmp_path, live_on):
    executor = FakeExecutor()
    _, late = _process(tmp_path, [_record()], {"ev1": _item()}, executor,
                       now=datetime(2026, 10, 7, 5, 45, tzinfo=timezone.utc))        # 14:45 KST
    assert late[0]["status"] == "SKIPPED_DEADLINE" and executor.calls == []
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


def test_an_error_after_the_order_is_reconciled_with_the_holdings_table(tmp_path, live_on):
    import subprocess
    db = tmp_path / "t.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE stock_holdings (id INTEGER PRIMARY KEY, ticker TEXT, account_key TEXT, buy_price REAL, "
                 "scenario TEXT)")
    conn.execute("INSERT INTO stock_holdings (ticker, account_key, buy_price, scenario) VALUES (?,?,?,?)",
                 ("000001", "acct", 12160.0, json.dumps({"reentry": {"watch_id": "w1", "trigger_date": DAY}})))
    conn.commit()
    conn.close()
    timeout = FakeExecutor(error=subprocess.TimeoutExpired("entry", 600))
    state, results = _process(tmp_path, [_record()], {"ev1": _item()}, timeout, db_path=db)
    assert results[0]["status"] == "BOUGHT_RECONCILED" and results[0]["reason"] == "reconciled_after:timeout"
    live = state["watches"][0]["live"][DAY]
    assert live["status"] == "BOUGHT" and live["holding_ids"] == [1] and live["entry_price"] == 12160.0
    assert live["account_refs"] == [LIVE.account_ref("acct")]


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


def test_agent_errors_auction_hours_and_the_deadline_never_buy():
    agent = FakeAgent(error=ValueError("x"))
    assert asyncio.run(LIVE.enter_with_agent(agent, "KR", _entry(), now=KR_1400)) == \
        {"bought": False, "reason": "error:ValueError"}
    late = FakeAgent()
    out = asyncio.run(LIVE.enter_with_agent(late, "KR", _entry(),
                                            now=datetime(2026, 10, 7, 6, 21, tzinfo=timezone.utc)))
    assert out["reason"] == "outside_regular_hours" and late.calls == []
    out = asyncio.run(LIVE.enter_with_agent(late, "KR", _entry(),
                                            now=datetime(2026, 10, 7, 5, 41, tzinfo=timezone.utc)))
    assert out["reason"] == "deadline" and late.calls == []
    assert LIVE.outcome_status(out) == "SKIPPED_DEADLINE"
    assert LIVE.outcome_status({"bought": False, "reason": "no_micro_plan"}) == "SKIPPED_NO_MICRO_PLAN"


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
    monkeypatch.setattr(LIVE, "before_deadline", lambda market, now=None: True)
    outcomes = asyncio.run(tool.run("KR", [dict(_entry(), key="k1")], agent_factory=factory, chat_id="chan"))
    assert outcomes["k1"]["bought"] is True and sent == [("chan", True)]


def test_entry_lock_is_shared_and_fails_closed_for_reentry(tmp_path):
    from prism_core.entry_lock import entry_lock

    async def scenario():
        async with entry_lock("KR", timeout=1) as first, entry_lock("KR", timeout=0.3) as second:
            return first, second
    assert asyncio.run(scenario()) == (True, False)


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


EVENTS = []


def _base_ns():
    def observe_or_emit(agent, fn, *args, **kwargs):
        EVENTS.append((getattr(fn, "__name__", str(fn)), args[0] if args else kwargs.get("market")))
    return {"Any": Any, "Dict": dict, "logger": logging.getLogger("reentry-live-test"), "effects_for": effects_for,
            "EffectsFailure": EffectsFailure, "require_execution_runtime": require_execution_runtime,
            "apply_buy_scenario_contract": apply_buy_scenario_contract, "observe_or_emit": observe_or_emit,
            "emit_trading_context": type("E", (), {"__name__": "emit_trading_context"})(),
            "emit_micro_split_shadow": type("M", (), {"__name__": "emit_micro_split_shadow"})(),
            "_capture_entry_quality_context": lambda **kw: {}}


class _Agent:
    """Holds the stubbed collaborators of a tracker; the real method is bound per test."""

    def __init__(self, table, held=False, slots=0, sector_ok=True, gate=True, quote=12150.0, broken_db=False):
        self.max_slots, self.db_path, self.enable_journal = 10, ":memory:", False
        self.conn = sqlite3.connect(":memory:")
        if not broken_db:
            self.conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, ticker TEXT, account_key TEXT, "
                              "buy_price REAL)")
            if held:
                self.conn.execute(f"INSERT INTO {table} (ticker, account_key, buy_price) VALUES ('000001','acct-key',1)")
            for k in range(slots):
                self.conn.execute(f"INSERT INTO {table} (ticker, account_key, buy_price) VALUES (?, 'acct-key', 1)",
                                  (f"9{k:05d}",))
        self.cursor, self.table, self.calls = self.conn.cursor(), table, []
        self._sector_ok, self._gate, self._quote = sector_ok, gate, quote
        self.trigger_info_map = {}

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

    async def _get_fresh_buy_quote(self, ticker):            # KR
        return {"price": self._quote}

    async def _refresh_buy_quote(self, ticker):              # US
        return self._quote

    async def _enter_eligible_candidate(self, **kwargs):     # fake broker: the real order path stops here
        self.calls.append(kwargs)
        self.conn.execute(f"INSERT INTO {self.table} (ticker, account_key, buy_price) VALUES (?,?,?)",
                          (kwargs["ticker"], "acct-key", kwargs["current_price"]))
        return 1


@pytest.fixture
def no_cooldown(monkeypatch):
    import reentry_cooldown
    from cores import regime_policy
    from observability import decision_inputs
    from prism_core import micro_split_live
    monkeypatch.setattr(reentry_cooldown, "reentry_block", lambda *a, **k: None)
    monkeypatch.setattr(regime_policy, "regime_min_score_floor_enabled", lambda: False)   # env-gated, off here
    monkeypatch.setattr(micro_split_live, "live_enabled", lambda market: True)
    monkeypatch.setattr(micro_split_live, "plan_available", lambda agent, **kw: True)
    monkeypatch.setattr(micro_split_live, "relaxed_min_score",
                        lambda agent, *, min_score, scenario, **kw: (min_score, scenario))
    monkeypatch.setattr(decision_inputs, "emit_decision_inputs",
                        lambda agent, **kw: EVENTS.append(("emit_decision_inputs", kw.get("market"))))
    EVENTS.clear()


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
    if market == "US":
        scenario["decision"] = "entry"
    return {"ticker": "000001", "company_name": "와이씨", "current_price": 12150.0, "scenario": scenario,
            "sector": "반도체", "source_decision_id": scenario["_decision_id"]}


def _enter(market, agent, monkeypatch):
    return _kr(agent) if market == "KR" else _us(agent, monkeypatch)


def _table(market):
    return "stock_holdings" if market == "KR" else "us_stock_holdings"


@pytest.mark.parametrize("market", ["KR", "US"])
def test_real_agent_method_buys_once_through_the_batch_entry_pipeline(market, monkeypatch, no_cooldown):
    agent = _Agent(_table(market))
    out = asyncio.run(_enter(market, agent, monkeypatch)(**_kwargs(market)))
    assert out["bought"] is True and out["holding_ids"] == [1] and out["account_refs"] == [LIVE.account_ref("acct-key")]
    assert len(agent.calls) == 1
    call = agent.calls[0]
    assert call["source"] == ("kr_reentry_v3" if market == "KR" else "us_reentry_v3")
    assert call["lock_held"] is True and call["require_micro_plan"] is True          # never a full slot
    assert call["scenario"]["reentry"]["watch_id"] == "w1" and call["scenario"]["stop_loss"] == 11591.5
    assert call["is_add"] is False and call["rebound_pilot"] is False
    assert call["scenario"]["_decision_context"]["source"] == "reentry_v3"
    assert call["scenario"]["_deterministic_market_regime"] == "sideways"
    assert call["scenario"]["_deterministic_trend_facts"] == "T1_hit: false"
    names = [e[0] for e in EVENTS]
    assert "emit_trading_context" in names and "emit_decision_inputs" in names
    assert ("emit_micro_split_shadow" in names) == (market == "US")    # KR emits it inside the batch entry step


@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize("blocker,reason", [({"held": True}, "already_held"), ({"slots": 10}, "max_slots"),
                                             ({"sector_ok": False}, "sector_limit"),
                                             ({"gate": False}, "buy_gate:rr_below_floor"),
                                             ({"quote": 13000.0}, "outside_band(13000)"),
                                             ({"broken_db": True}, "holdings_check_error")])
def test_real_agent_method_refuses_without_ordering(market, blocker, reason, monkeypatch, no_cooldown):
    agent = _Agent(_table(market), **blocker)
    out = asyncio.run(_enter(market, agent, monkeypatch)(**_kwargs(market)))
    assert out == {"bought": False, "reason": reason} and agent.calls == []


@pytest.mark.parametrize("market", ["KR", "US"])
def test_real_agent_method_never_buys_a_full_slot_without_a_micro_split_plan(market, monkeypatch, no_cooldown):
    from prism_core import micro_split_live
    agent = _Agent(_table(market))
    monkeypatch.setattr(micro_split_live, "plan_available", lambda agent, **kw: False)
    assert asyncio.run(_enter(market, agent, monkeypatch)(**_kwargs(market))) == \
        {"bought": False, "reason": "no_micro_plan"}
    monkeypatch.setattr(micro_split_live, "plan_available", lambda agent, **kw: True)
    monkeypatch.setattr(micro_split_live, "live_enabled", lambda market: False)
    assert asyncio.run(_enter(market, agent, monkeypatch)(**_kwargs(market)))["reason"] == "no_micro_plan"
    assert agent.calls == []


@pytest.mark.parametrize("market", ["KR", "US"])
def test_real_agent_method_respects_the_reentry_cooldown_score_and_the_entry_lock(market, monkeypatch, no_cooldown):
    import reentry_cooldown
    from prism_core.entry_lock import entry_lock
    agent = _Agent(_table(market))
    enter = _enter(market, agent, monkeypatch)
    low = _kwargs(market)
    low["scenario"] = dict(low["scenario"], buy_score=3)
    assert asyncio.run(enter(**low))["reason"].startswith("score_below_min")

    async def busy():
        async with entry_lock(market, timeout=1):
            import prism_core.entry_lock as module
            original = module.entry_lock

            def quick(m, *, timeout=60.0, poll=0.2):
                return original(m, timeout=0.2, poll=0.05)
            monkeypatch.setattr(module, "entry_lock", quick)
            return await enter(**_kwargs(market))
    assert asyncio.run(busy())["reason"] == "entry_lock_busy"
    import prism_core.entry_lock as module
    monkeypatch.setattr(module, "entry_lock", entry_lock)
    monkeypatch.setattr(reentry_cooldown, "reentry_block", lambda *a, **k: {"after_loss": True, "risk_exit": True})
    monkeypatch.setattr(reentry_cooldown, "COOLDOWN_LIVE", True)
    assert asyncio.run(enter(**_kwargs(market)))["reason"] == "reentry_cooldown" and agent.calls == []


class _LockProbe:
    """Records entry_lock calls; the first step after the lock raises so the long entry body is not run."""

    class Reached(EffectsFailure):                                 # re-raised by the entry body
        pass

    def __init__(self):
        self.calls = []

    def __call__(self, market, *, timeout=60.0, poll=0.2):
        import contextlib
        self.calls.append((market, timeout))

        @contextlib.asynccontextmanager
        async def held():
            yield True
        return held()


@pytest.mark.parametrize("market", ["KR", "US"])
def test_virtual_account_runs_never_take_the_real_entry_lock(market, monkeypatch):
    import prism_core.entry_lock as module
    probe = _LockProbe()
    monkeypatch.setattr(module, "entry_lock", probe)
    path, cls = (("stock_tracking_enhanced_agent.py", "EnhancedStockTrackingAgent") if market == "KR"
                 else ("prism-us/us_stock_tracking_agent.py", "USStockTrackingAgent"))
    method = _load_method(ROOT / path, cls, "_enter_eligible_candidate", _base_ns())

    class Agent:
        async def _refresh_buy_boundary(self, *a, **k):            # KR: first step after the lock
            raise _LockProbe.Reached

        async def _is_ticker_in_holdings(self, ticker):            # US: first step with effects
            raise _LockProbe.Reached

    agent = Agent()
    agent._enter_eligible_candidate = lambda **kw: method(agent, **kw)
    common = {"ticker": "000001", "company_name": "x", "current_price": 1.0, "scenario": {}, "analysis_result": {},
              "is_add": False, "rebound_pilot": False, "entry_cash_amount": None, "rank_change_msg": "",
              "source_decision_id": None}
    extra = ({"buy_score": 7, "min_score": 5, "sector": "x", "buy_gate": None} if market == "KR" else
             {"account": None, "state": {}, "adjusted_score": 7, "trigger_type": "x", "trigger_info": {},
              "scenario_slot_limit": 10, "signaled_tickers": set()})
    with pytest.raises(_LockProbe.Reached):
        asyncio.run(method(agent, effects=object(), **common, **extra))       # virtual account (SHADOW)
    assert probe.calls == []
    if market == "KR":                                             # the real batch still takes the lock
        with pytest.raises(_LockProbe.Reached):
            asyncio.run(method(agent, effects=None, **common, **extra))
        assert probe.calls == [("KR", 120)]


def test_test_runs_keep_the_entry_lock_out_of_runtime():
    from prism_core.entry_lock import lock_path
    assert ROOT / "runtime" not in lock_path("KR").parents


def test_buy_message_builders_add_the_reentry_line_only_for_reentries():
    namespace = {"Dict": dict, "Any": Any}
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
def _yc():
    import test_reentry_campaign as T
    bars = T.yc_bars()
    setup = T.yc_setup(bars)
    before = C.replay(setup, bars[:71], 69)
    trigger = C.evaluate(setup, bars=bars[:71], i=71, flags=before["flags"], price=12150.0, day_low=12000.0,
                         regime=None)["trigger"]
    return bars, setup, trigger


def _bought(trigger, exit_info=None, key="k"):
    return {"by_rule": {"L97": trigger, "SS": trigger}, "decision_price": 12150.0, "day_low": 12000.0,
            "live": {"status": "BOUGHT", "key": key, "holding_ids": [7], "entry_price": 12160.0, "exit": exit_info}}


def test_ledger_follows_the_real_position_and_counts_its_stop():
    bars, setup, trigger = _yc()
    camp = C.replay(setup, bars, 69, decisions={bars[71]["date"]: _bought(trigger)})["campaigns"]
    first = camp["L97"]["attempts"][0]
    assert first["real"] is True and first["status"] == "OPEN" and first["exit_rule"] == "REAL"
    assert first["live"]["holding_ids"] == [7] and camp["L97"]["status"] == "ENDED"   # the watch ended, holding stays
    assert "real" not in camp["SS"]["attempts"][0]                                   # SS stays a virtual record
    sold = _bought(trigger, {"date": bars[74]["date"], "price": 11500.0, "stop": True, "exit_kind": "stop"})
    camp = C.replay(setup, bars, 69, decisions={bars[71]["date"]: sold})["campaigns"]["L97"]
    first = camp["attempts"][0]
    assert (first["status"], first["exit_reason"], first["exit_date"], first["real_exit_kind"]) == \
        ("CLOSED", "stop", bars[74]["date"], "stop")
    assert bars[74]["date"] in camp["virtual_stop_dates"]


def test_reviewer_repro_entry_day_real_stop_keeps_the_cooldown_and_a_later_real_buy():
    """Bought and hard-stopped on day 71; the intraday run of day 72 must see the stop cooldown, and a
    later recorded real buy is always opened (never dropped as a conflict)."""
    bars, setup, trigger = _yc()
    day = bars[71]["date"]
    sold_same_day = _bought(trigger, {"date": day, "price": 11700.0, "stop": True, "exit_kind": "stop"})
    led = C.replay(setup, bars[:72], 69, decisions={day: sold_same_day})
    first = led["campaigns"]["L97"]["attempts"][0]
    assert (first["status"], first["exit_reason"], first["exit_date"]) == ("CLOSED", "stop", day)  # entry bar
    assert led["next"]["L97"]["reason"] == "stop_cooldown"
    day2 = bars[72]["date"]
    led = C.replay(setup, bars[:73], 69, decisions={day: sold_same_day, day2: _bought(trigger, key="k2")})
    attempts = led["campaigns"]["L97"]["attempts"]
    assert [(a["date"], a["status"], a.get("real")) for a in attempts] == [(day, "CLOSED", True), (day2, "OPEN", True)]
    assert {"date": day2, "reason": "real_buy_forced"} in led["conflicts"]


@pytest.mark.parametrize("stop,expected_next_day", [(True, "stop_cooldown"), (False, "ok")])
def test_a_real_sell_earlier_today_blocks_a_buy_today(stop, expected_next_day):
    """Position from day 71, sold on the morning of day 75 (before the 14:00 run): the intraday run of
    day 75 (bars through day 74) sees an exit-day block; the following session sees the stop cooldown
    only for a stop (an AI/target exit carries no cooldown)."""
    bars, setup, trigger = _yc()
    exit_info = {"date": bars[75]["date"], "price": 12500.0 if not stop else 11500.0, "stop": stop,
                 "exit_kind": "stop" if stop else None}
    decisions = {bars[71]["date"]: _bought(trigger, exit_info)}
    today = C.replay(setup, bars[:75], 69, decisions=decisions)
    assert today["campaigns"]["L97"]["attempts"][0]["status"] == "CLOSED"
    assert today["next"]["L97"]["reason"] == "exit_day"
    tomorrow = C.replay(setup, bars[:76], 69, decisions=decisions)
    nxt = tomorrow["next"]["L97"]
    assert (nxt["reason"] if not nxt["eligible"] else "ok") == expected_next_day


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
    bars, _root, _sent, run, quote = T._runtime(tmp_path, monkeypatch)
    executor = FakeExecutor()
    run(70, llm_recheck=False)
    summary = run(70, phase="intraday", decision_day=bars[71]["date"], quote_fn=quote, llm_recheck=True,
                  llm=T._fake_llm([]), live_executor=executor)
    assert summary["mode"] == "SHADOW" and executor.calls == []
    monkeypatch.setenv("REENTRY_V3_LIVE_ENABLED", "true")
    summary = run(71, phase="intraday", decision_day=bars[72]["date"], quote_fn=quote, dry_run=True,
                  llm_recheck=True, llm=T._fake_llm([]), live_executor=executor)
    assert executor.calls == [] and summary["live_results"] == {}


def test_ss_only_triggers_are_recorded_but_never_rechecked_or_traded(monkeypatch):
    import test_reentry_campaign as T

    from observability import reentry_v3_shadow as V3
    watch = {"ledger": {"next": {"L97": {"eligible": False, "attempt": 2, "reason": "stop_cooldown"},
                                 "SS": {"eligible": True, "attempt": 1, "window": None}}, "flags": {}},
             "setup": {}, "ticker": "000001", "decisions": {}, "row": {}}
    monkeypatch.setattr(V3.C, "evaluate", lambda *a, **k: {"trigger": {"trigger": "R2S"}, "checks": {},
                                                           "prev_close": 1, "prior_high": 1, "regime": "sideways",
                                                           "regime_missing": False})
    monkeypatch.setattr(V3, "_frames_for", lambda w, f, c: (T.yc_bars()[:71], None))
    ctx = type("Ctx", (), {"regime_detail": lambda self, d: {"effective": "sideways"},
                           "pulse_fn": lambda self, b: (lambda d: None)})()
    decision = V3.decide(watch, {"price": 1.0, "observed_at": "2026-10-07T05:00:00+00:00"}, {}, "2026-10-06",
                         DAY, "KR", ctx)
    assert decision["by_rule"] == {"SS": {"trigger": "R2S"}}       # recorded for the SS campaign
    assert decision["trigger"] is None                              # nothing to recheck, freeze or trade


def test_recheck_cap_ranks_today_and_stops_after_the_cap(tmp_path, monkeypatch, live_on):
    from observability import reentry_v3_recheck as RC3
    from observability import reentry_v3_shadow as V3
    root = tmp_path / "rt"
    root.mkdir()
    p = V3.paths("KR", root)
    items = [dict(_item(k, attempt=a, rr=r), report_ref={"kind": "file"}) for k, a, r in
             ((1, 2, 3.0), (2, 1, 1.2), (3, 1, 2.0), (4, 1, 0.5))]
    p["inputs"].write_text("".join(json.dumps(i) + "\n" for i in items))
    state = {"watches": [{"watch_id": f"w{k}", "ticker": f"00000{k}",
                          "rechecks": {f"ev{k}": {"status": "PENDING", "attempts": 0, "last": None}}}
                         for k in (1, 2, 3, 4)]}
    calls = []

    def fake_recheck(item, **kw):
        calls.append(item["ticker"])
        return dict(_record(int(item["event_id"][2:])), event_id=item["event_id"], status="OK", approved=True)
    monkeypatch.setattr(RC3, "recheck", fake_recheck)
    monkeypatch.setattr(RC3, "instruction", lambda market: "SYS")
    monkeypatch.setattr(V3, "emit_event", lambda *a, **k: None)
    out = V3._run_rechecks(state, "KR", "2026-10-06", p, tmp_path, None, None, only_day=DAY, limit=3,
                           stop_after_approvals=2)
    assert calls == ["000003", "000002"] and len(out) == 2                 # rank: attempt 1 by R/R, then stop
    statuses = {w["watch_id"]: w["rechecks"][f"ev{w['watch_id'][1:]}"]["status"] for w in state["watches"]}
    assert statuses == {"w1": "SKIPPED_CAP", "w2": "OK", "w3": "OK", "w4": "SKIPPED_CAP"}


def test_sector_falls_back_to_the_original_decision_row(tmp_path):
    db = tmp_path / "t.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE trading_history (ticker TEXT, sell_date TEXT, sector TEXT)")
    conn.execute("CREATE TABLE watchlist_history (id INTEGER PRIMARY KEY, sector TEXT)")
    conn.execute("INSERT INTO trading_history VALUES ('000001', '2026-04-08 14:00:00', '반도체')")
    conn.execute("INSERT INTO watchlist_history (id, sector) VALUES (5, '2차전지')")
    conn.commit()
    conn.close()
    assert LIVE.original_sector(db, "KR", {"source": "STOP_EXIT", "ticker": "000001", "exit_date": "2026-04-08"}) == "반도체"
    assert LIVE.original_sector(db, "KR", {"source": "LOCATION_SKIP", "ticker": "x", "analysis_id": 5}) == "2차전지"
    assert LIVE.original_sector(db, "KR", {"source": "LOCATION_SKIP", "ticker": "x", "analysis_id": 9}) is None


def test_recheck_prompt_marks_real_and_virtual_prior_attempts_and_the_actual_shakeout_lines():
    from observability import reentry_v3_recheck as RC3
    block = RC3._attempts_block({"L97": {"attempt": 3, "max": 3, "prior": [
        {"date": "2026-10-01", "trigger": "R1C", "entry": 100.0, "exit_date": "2026-10-02", "exit_reason": "stop",
         "ret": -0.04, "real": True},
        {"date": "2026-10-05", "trigger": "R2S", "entry": 101.0, "exit_date": "2026-10-06", "exit_reason": "ma20",
         "ret": 0.02}]}})
    assert "[실제 매수] 2026-10-01" in block and "[가상 기록, 실제 매매 아님] 2026-10-05" in block
    item = {"trigger": "SHAKEOUT_RECLAIM", "level": {"L": 11950.0}, "decision_price": 11900.0, "live_rule": "L97",
            "decision_time_local": "2026-10-07 14:00 KST", "volume_share": 0.8,
            "shakeout": {"window_start": "2026-10-05", "shakeout_low": 11000.0, "volume_ratio": 1.3},
            "campaign": {"windows": {"L97": {"R": 11800.0, "line": 11446.0}}, "reclaim_level": 11950.0}}
    lines = RC3._trigger_lines(item)
    assert "흔들기 확인 시작선 11,446.00" in lines and "회복 기준 11,800.00" in lines and "97%" not in lines
    assert "97%" not in RC3.RECHECK_V3_KO and "실제 매수(실계좌)와 시스템의 가상 기록" in RC3.RECHECK_V3_KO


def test_position_counts_use_fixed_queries_and_fail_closed_on_an_unknown_table():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE us_stock_holdings (ticker TEXT, account_key TEXT)")
    conn.executemany("INSERT INTO us_stock_holdings VALUES (?, ?)", [("AAPL", "a"), ("MSFT", "a"), ("AAPL", "b")])
    assert LIVE.strict_position_counts(conn.cursor(), "us_stock_holdings", "AAPL", "a") == (True, 2)
    with pytest.raises(KeyError):
        LIVE.strict_position_counts(conn.cursor(), "us_stock_holdings; DROP TABLE x", "AAPL", "a")


# ---------------------------------------------------------------- rule-approved re-entry (no LLM, 2026-10-10)
def test_rule_approval_switch_defaults_off(monkeypatch):
    monkeypatch.delenv("REENTRY_V3_DETERMINISTIC", raising=False)
    assert LIVE.deterministic_enabled() is False
    monkeypatch.setenv("REENTRY_V3_DETERMINISTIC", "true")
    assert LIVE.deterministic_enabled() is True


def test_rule_approval_is_an_approved_record_with_the_rule_stop_and_target():
    item = dict(_item(), original={"fundamental_check": {"all_passed": True}})
    record = LIVE.deterministic_record(item, "KR")
    assert LIVE.approved(record) and record["approval"] == "deterministic" and record["model"] is None
    scenario = LIVE.build_scenario(item, record, "KR")
    assert LIVE.deterministic_approval(scenario) and scenario["reentry"]["approval"] == "deterministic"
    assert scenario["stop_loss"] == 11591.5 and scenario["target_price"] == 12990.0
    assert scenario["fundamental_check"] == {"all_passed": True} and "buy_score" not in scenario
    assert "AI 재점검 없음" in scenario["rationale"] and scenario["investment_period"] == "중기"
    apply_buy_scenario_contract(scenario, market="KR", entry_price=12150.0)    # a valid entry scenario
    assert not LIVE.deterministic_approval(LIVE.build_scenario(_item(), _record(), "KR"))
    line = LIVE.entry_message_line(scenario, "KR")
    assert line.startswith("🔁 재진입 매수 (기준 가격 눌림 지지 매수, 1/3번째 시도)")


@pytest.mark.parametrize("market", ["KR", "US"])
def test_rule_approved_reentry_skips_only_the_score_floors(market, monkeypatch, no_cooldown):
    from types import SimpleNamespace

    from cores import regime_policy
    monkeypatch.setattr(regime_policy, "regime_min_score_floor_enabled", lambda: True)      # KR floor: 8
    monkeypatch.setattr(regime_policy, "effective_min_score", lambda *a, **k: 8)
    monkeypatch.setattr(regime_policy, "get_market_pulse_state", lambda market: None)
    floor = SimpleNamespace(regime_min_score_floor_enabled=lambda: True, effective_min_score=lambda *a, **k: 8,
                            get_market_pulse_state=lambda market: None)
    rule = _kwargs(market)
    rule["scenario"] = LIVE.build_scenario(_item(), LIVE.deterministic_record(_item(), market), market)
    agent = _Agent(_table(market))
    agent._regime_policy_mod = lambda: floor                                               # US floor: 8
    assert asyncio.run(_enter(market, agent, monkeypatch)(**_kwargs(market)))["reason"].startswith("score_below_min")
    out = asyncio.run(_enter(market, agent, monkeypatch)(**rule))
    assert out["bought"] is True and len(agent.calls) == 1
    assert agent.calls[0]["scenario"]["reentry"]["approval"] == "deterministic"
    blocked = _Agent(_table(market), gate=False)                     # every other check still applies
    blocked._regime_policy_mod = lambda: floor
    assert asyncio.run(_enter(market, blocked, monkeypatch)(**rule)) == {"bought": False,
                                                                        "reason": "buy_gate:rr_below_floor"}


def test_runtime_rule_approval_orders_without_any_llm_call(tmp_path, monkeypatch, live_on):
    import test_reentry_campaign as T
    monkeypatch.setenv("REENTRY_V3_DETERMINISTIC", "true")
    bars, root, sent, run, quote = T._runtime(tmp_path, monkeypatch)
    executor = FakeExecutor()

    def no_llm(*a, **k):
        raise AssertionError("the LLM must not be called")
    run(70, llm_recheck=False)
    summary = run(70, phase="intraday", decision_day=bars[71]["date"], quote_fn=quote, llm_recheck=True,
                  llm=no_llm, live_executor=executor,
                  now_fn=lambda: datetime.fromisoformat(bars[71]["date"] + "T05:00:00+00:00"))
    assert summary["live_results"] == {"BOUGHT": 1} and summary["llm_calls"] == 0 and len(executor.calls) == 1
    entry = executor.calls[0][1][0]
    assert entry["scenario"]["reentry"]["approval"] == "deterministic"
    results = [json.loads(line) for line in (root / "reentry_v3_recheck_results_kr.jsonl").read_text().splitlines()]
    assert [r["approval"] for r in results] == ["deterministic"]
    state = json.loads((root / "reentry_v3_state_kr.json").read_text())
    assert state["watches"][0]["live"][bars[71]["date"]]["status"] == "BOUGHT"
    again = run(70, phase="intraday", decision_day=bars[71]["date"], quote_fn=quote, llm=no_llm,
                live_executor=executor)
    assert len(executor.calls) == 1 and again["live_results"] == {}                  # never twice
    monkeypatch.delenv("REENTRY_V3_LIVE_ENABLED")
    shadow = run(71, phase="intraday", decision_day=bars[72]["date"], quote_fn=quote, llm=no_llm,
                 live_executor=executor)
    assert shadow["mode"] == "SHADOW" and shadow["llm_calls"] == 0 and len(executor.calls) == 1
