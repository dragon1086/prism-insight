"""No-order effects are unit-only until the independent runtime gate review."""
import json
from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo
from types import SimpleNamespace

import pytest

from prism_core.isolated_agent_runtime import prepare_isolated_runtime
from prism_core import isolated_strategy_effects as effects
from prism_core.strategy_ledger import StrategyLedger
from prism_core.strategy_ledger_outbox import StrategyLedgerOutbox


@pytest.fixture
def bound(tmp_path):
    tmp_path.chmod(0o700)
    marker = tmp_path / ".prism-agent-isolation.json"
    marker.write_text(json.dumps({"schema_version": 1, "purpose": "PRISM_AGENT_SHADOW", "market": "KR", "runtime_id": "test"}))
    marker.chmod(0o600)
    account = {"name": "SHADOW test", "account_key": "virtual:test", "market": "kr", "virtual": True}
    runtime = prepare_isolated_runtime(str(tmp_path / "state.sqlite"), [account], tmp_path, lambda: {}, "KR")
    conn = runtime.connect()
    conn.row_factory = __import__("sqlite3").Row
    conn.execute("CREATE TABLE stock_holdings (id INTEGER PRIMARY KEY, account_key TEXT, account_name TEXT, ticker TEXT, company_name TEXT, buy_price REAL, buy_date TEXT, current_price REAL, last_updated TEXT, scenario TEXT, target_price REAL, stop_loss REAL, trigger_type TEXT, trigger_mode TEXT, sector TEXT)")
    conn.commit()
    agent = SimpleNamespace(_isolated_runtime=runtime, conn=conn, cursor=conn.cursor(), db_path=runtime.db_path)
    ledger = StrategyLedger(tmp_path / "strategy.sqlite")
    ledger.create_book("book", "KR", mode="SHADOW")
    registration = effects.EffectsRegistration(case_id="case1", book_id="book", source_hash="a" * 64,
        occurred_at="2026-09-10T00:00:00Z", campaigns=(("005930", "campaign1"),))
    adapter = effects.IsolatedStrategyEffects(agent, ledger, registration)
    yield agent, ledger, adapter
    conn.close()


def enter(adapter):
    return adapter.record_entry(ticker="005930", company_name="SYNTHETIC test", price=100,
        scenario={"target_price": 120, "stop_loss": 90, "sector": "Technology"}, is_add=False)


def test_gate_closed_and_direct_helpers_never_authorized(bound, monkeypatch):
    agent, _, adapter = bound
    agent._no_order_effects = adapter
    with pytest.raises(RuntimeError, match="review"):
        effects.effects_for(agent, "process_reports")
    monkeypatch.setattr(effects, "EFFECTS_RUNTIME_ENABLED", True)
    with pytest.raises(effects.EffectsFailure, match="context"):
        effects.effects_for(agent, "process_reports")
    raw = "{}"
    digest = __import__("hashlib").sha256(raw.encode()).hexdigest()
    adapter = effects.IsolatedStrategyEffects(agent, adapter.ledger,
        replace(adapter.registration, context_hash=digest),
        pipeline_context=effects.EffectsPipelineContext("case1", digest, raw))
    agent._no_order_effects = adapter
    assert effects.effects_for(agent, "process_reports") is adapter
    with pytest.raises(RuntimeError):
        effects.effects_for(agent, "buy_stock")
    assert effects.effects_for(SimpleNamespace(), "process_reports") is None


@pytest.mark.parametrize("cash", [None, 0, 100000])
def test_cash_never_changes_strategy_or_creates_execution(bound, cash):
    agent, ledger, adapter = bound
    agent.untrusted_account_cash = cash
    assert enter(adapter)
    snapshot = ledger.snapshot("book")
    assert snapshot["occupied_slots"] == 1 and snapshot["executions"] == []
    assert len(StrategyLedgerOutbox(ledger).pending()) == 1
    row = dict(agent.cursor.execute("SELECT * FROM stock_holdings").fetchone())
    projection = json.loads(row["scenario"])["_strategy_projection"]
    assert projection["account_execution_status"] == "UNKNOWN"
    assert projection["units_basis"] == "NORMALIZED_UNITS_NOT_SHARES"
    assert row["buy_price"] == 100
    assert row["buy_date"] == "2026-09-10 09:00:00"
    assert not {"quantity", "cash", "investment_amount"} & row.keys()


def test_duplicate_event_repairs_projection_without_new_notice(bound):
    agent, ledger, adapter = bound
    assert enter(adapter)
    agent.cursor.execute("DELETE FROM stock_holdings")
    agent.conn.commit()
    assert not enter(adapter)
    assert agent.cursor.execute("SELECT COUNT(*) FROM stock_holdings").fetchone()[0] == 1
    assert len(StrategyLedgerOutbox(ledger).pending()) == 1
    with pytest.raises(effects.EffectsFailure):
        adapter.record_entry(ticker="005930", company_name="test", price=99, scenario={}, is_add=True)


def test_projection_failure_keeps_committed_ledger_event(bound, monkeypatch):
    _, ledger, adapter = bound
    monkeypatch.setattr(adapter, "_project", lambda *args: (_ for _ in ()).throw(ValueError("secret")))
    with pytest.raises(effects.EffectsFailure) as error:
        enter(adapter)
    assert "secret" not in str(error.value)
    assert ledger.snapshot("book")["occupied_slots"] == 1
    assert len(StrategyLedgerOutbox(ledger).pending()) == 1


def test_exit_is_strategy_only_and_retry_idempotent(bound):
    agent, ledger, adapter = bound
    enter(adapter)
    assert adapter.record_exit(ticker="005930", price=110)
    assert not adapter.record_exit(ticker="005930", price=110)
    snapshot = ledger.snapshot("book")
    assert snapshot["occupied_slots"] == 0
    assert snapshot["executions"] == []
    assert agent.conn.execute("SELECT COUNT(*) FROM stock_holdings").fetchone()[0] == 0
    assert len(StrategyLedgerOutbox(ledger).pending()) == 2


@pytest.mark.parametrize("change", [
    {"source_hash": "bad"}, {"occurred_at": "2026-09-10"},
    {"campaigns": (("005930", "c"), ("005930", "d"))},
    {"campaigns": (("005930", "c"), ("005931", "c"))},
    {"case_id": []},
])
def test_invalid_registration_fails_before_effect(bound, change):
    _, ledger, adapter = bound
    with pytest.raises(effects.EffectsFailure):
        replace(adapter.registration, **change)
    assert ledger.snapshot("book")["occupied_slots"] == 0


def test_binding_swap_and_unregistered_ticker_fail_closed(bound):
    agent, ledger, adapter = bound
    with pytest.raises(effects.EffectsFailure):
        adapter.record_exit(ticker="OTHER", price=100)
    agent._isolated_runtime = None
    with pytest.raises(effects.EffectsFailure):
        enter(adapter)
    assert ledger.snapshot("book")["occupied_slots"] == 0


def test_projection_refuses_foreign_holding(bound):
    agent, ledger, adapter = bound
    agent.conn.execute("INSERT INTO stock_holdings(account_key,ticker,scenario) VALUES(?,?,?)",
                       ("virtual:test", "005930", "{}"))
    agent.conn.commit()
    with pytest.raises(effects.EffectsFailure, match="projection"):
        enter(adapter)
    assert agent.conn.execute("SELECT scenario FROM stock_holdings").fetchone()[0] == "{}"
    assert ledger.snapshot("book")["occupied_slots"] == 1


def test_partial_exit_denied_without_stable_registered_units(bound):
    _, ledger, adapter = bound
    enter(adapter)
    with pytest.raises(effects.EffectsFailure):
        adapter.record_exit(ticker="005930", price=100, fraction=.5)
    assert ledger.snapshot("book")["remaining_allocation"] == "1"


def test_registered_partial_exit_preserves_residual_scenario_and_repairs_once(bound):
    agent, ledger, adapter = bound
    enter(adapter)
    guard = ledger.campaign_guard("campaign1")
    original_scenario = agent.conn.execute("SELECT scenario FROM stock_holdings").fetchone()[0]
    basis = ("005930", guard["campaign_hash"], guard["normalized_units"], original_scenario)
    registration = replace(adapter.registration, exit_bases=(basis,))
    adapter = effects.IsolatedStrategyEffects(agent, ledger, registration)
    assert adapter.record_exit(ticker="005930", price=110, fraction=.5)
    state = ledger.snapshot("book")
    assert state["remaining_allocation"] == "0.5" and state["occupied_slots"] == 1
    # Loss of a projection is repairable from the frozen registered scenario,
    # not by recomputing half of the now-reduced holding.
    agent.conn.execute("DELETE FROM stock_holdings")
    agent.conn.commit()
    assert not adapter.record_exit(ticker="005930", price=110, fraction=.5)
    assert ledger.snapshot("book") == state
    row = agent.conn.execute("SELECT scenario,target_price,stop_loss FROM stock_holdings").fetchone()
    assert json.loads(row[0])["target_price"] == row[1] == 120
    assert json.loads(row[0])["stop_loss"] == row[2] == 90
    assert len(StrategyLedgerOutbox(ledger).pending()) == 2
    stale = effects.IsolatedStrategyEffects(agent, ledger, replace(registration, case_id="stale"))
    with pytest.raises(effects.EffectsFailure):
        stale.record_exit(ticker="005930", price=110, fraction=.5)
    assert ledger.snapshot("book") == state


def test_exit_basis_units_must_match_canonical_hash_state(bound):
    agent, ledger, adapter = bound
    enter(adapter)
    guard = ledger.campaign_guard("campaign1")
    scenario = agent.conn.execute("SELECT scenario FROM stock_holdings").fetchone()[0]
    registration = replace(adapter.registration,
        exit_bases=(("005930", guard["campaign_hash"], "0.02", scenario),))
    adapter = effects.IsolatedStrategyEffects(agent, ledger, registration)
    before = ledger.snapshot("book")
    with pytest.raises(effects.EffectsFailure):
        adapter.record_exit(ticker="005930", price=110, fraction=.5)
    assert ledger.snapshot("book") == before


def test_pilot_scenario_cannot_silently_become_full_entry(bound):
    _, ledger, adapter = bound
    with pytest.raises(effects.EffectsFailure):
        adapter.record_entry(ticker="005930", company_name="test", price=100,
            scenario={"regime_entry_policy": {"mode": "rebound_pilot"}}, is_add=False)
    assert ledger.snapshot("book")["occupied_slots"] == 0


@pytest.mark.parametrize("operation", ["exit", "advance"])
@pytest.mark.parametrize("foreign", ["book", "symbol"])
def test_registered_campaign_cannot_mutate_foreign_identity(bound, operation, foreign):
    agent, ledger, adapter = bound
    ledger.create_book("other", "KR", cohort="other-v1", mode="SHADOW")
    ledger.apply_target("foreign", "other" if foreign == "book" else "book", "campaign1",
        "OTHER" if foreign == "symbol" else "005930", 100, 100, adapter.registration.occurred_at)
    before = ledger.snapshot("other" if foreign == "book" else "book")
    with pytest.raises(effects.EffectsFailure, match="identity"):
        if operation == "exit":
            adapter.record_exit(ticker="005930", price=110)
        else:
            adapter.advance_pilot(ticker="005930", company_name="test", scenario={}, expected_revision=0, evidence={})
    assert ledger.snapshot("other" if foreign == "book" else "book") == before
    assert agent.conn.execute("SELECT COUNT(*) FROM stock_holdings").fetchone()[0] == 0


@pytest.mark.parametrize("entry_at", ["2026-03-08T07:00:00Z", "2026-11-01T06:00:00Z"])
def test_us_projection_uses_us_table_and_real_price(tmp_path, entry_at):
    tmp_path.chmod(0o700)
    marker = tmp_path / ".prism-agent-isolation.json"
    marker.write_text(json.dumps({"schema_version": 1, "purpose": "PRISM_AGENT_SHADOW", "market": "US", "runtime_id": "test"}))
    marker.chmod(0o600)
    runtime = prepare_isolated_runtime(str(tmp_path / "state.sqlite"),
        [{"name": "SHADOW test", "account_key": "virtual:test", "market": "us", "virtual": True}], tmp_path, lambda: {}, "US")
    conn = runtime.connect()
    try:
        conn.execute("CREATE TABLE us_stock_holdings (id INTEGER PRIMARY KEY, account_key TEXT, account_name TEXT, ticker TEXT, company_name TEXT, buy_price REAL, buy_date TEXT, current_price REAL, last_updated TEXT, scenario TEXT, target_price REAL, stop_loss REAL, sector TEXT)")
        conn.commit()
        agent = SimpleNamespace(_isolated_runtime=runtime, conn=conn, db_path=runtime.db_path)
        ledger = StrategyLedger(tmp_path / "strategy.sqlite")
        ledger.create_book("book", "US", mode="SHADOW")
        registration = effects.EffectsRegistration("case", "book", "a" * 64, entry_at, (("SNDK", "campaign"),))
        adapter = effects.IsolatedStrategyEffects(agent, ledger, registration)
        assert adapter.record_entry(ticker="SNDK", company_name="SYNTHETIC test", price=100, scenario={}, is_add=False)
        assert conn.execute("SELECT buy_price FROM us_stock_holdings").fetchone()[0] == 100
        buy_date, scenario = conn.execute("SELECT buy_date, scenario FROM us_stock_holdings").fetchone()
        aware = datetime.fromisoformat(entry_at.replace("Z", "+00:00"))
        runtime_now = aware.astimezone(ZoneInfo("Asia/Seoul")).replace(tzinfo=None)
        assert runtime_now - datetime.strptime(buy_date, "%Y-%m-%d %H:%M:%S") == __import__("datetime").timedelta(0)
        assert datetime.fromisoformat(json.loads(scenario)["_strategy_projection"]["strategy_entry_at"]) == aware
        assert adapter.record_exit(ticker="SNDK", price=101)
        assert conn.execute("SELECT COUNT(*) FROM us_stock_holdings").fetchone()[0] == 0
    finally:
        conn.close()


def test_pilot_uses_one_slot_and_only_remaining_half_once(bound):
    agent, ledger, adapter = bound
    ledger.create_book("pilot", "KR", cohort="split-pilot-v1", mode="SHADOW")
    registration = replace(adapter.registration, book_id="pilot", occurred_at="2026-09-10T09:05:00+09:00")
    adapter = effects.IsolatedStrategyEffects(agent, ledger, registration)
    calendar = {"market": "KR", "timezone": "Asia/Seoul", "source_kind": "test_fixture",
        "source_ref": "synthetic-session-fixture", "verification_status": "VERIFIED",
        "sessions": [{"date": day, "open_at": day + "T09:00:00+09:00", "close_at": day + "T15:30:00+09:00"}
                     for day in ("2026-09-10", "2026-09-11", "2026-09-14", "2026-09-15")]}
    bar = {"open_at": "2026-09-10T09:00:00+09:00", "close_at": registration.occurred_at,
        "close": 100, "high": 102, "completed": True, "observed_at": registration.occurred_at}
    args = dict(ticker="005930", company_name="SYNTHETIC test", price=100, scenario={},
                signal_bar=bar, calendar=calendar, entry_eligible=True)
    assert adapter.open_pilot(**args)
    assert not adapter.open_pilot(**args)
    assert ledger.snapshot("pilot")["remaining_allocation"] == "0.5"
    assert ledger.snapshot("pilot")["occupied_slots"] == 1
    with pytest.raises(effects.EffectsFailure):
        adapter.record_entry(ticker="005930", company_name="test", price=105, scenario={}, is_add=True)
    now = "2026-09-11T09:35:00+09:00"
    adapter = effects.IsolatedStrategyEffects(agent, ledger, replace(registration, case_id="next", occurred_at=now))
    evidence = {"observed_at": now, "thesis_valid": True, "market_regime": "sideways", "pulse": "UPTREND",
        "bar": {"open_at": "2026-09-11T09:05:00+09:00", "close_at": "2026-09-11T09:30:00+09:00",
                "close": 103, "high": 104, "completed": True, "observed_at": now},
        "quote": {"price": 104, "observed_at": now},
        "ai": {"decision": "Enter", "score": 7, "ordinary_min_score": 7, "observed_at": now},
        "gates": {name: True for name in ("risk", "regime", "pulse", "risk_reward", "sector", "slot")}}
    advance = dict(ticker="005930", company_name="test", scenario={}, expected_revision=0, evidence=evidence)
    assert adapter.advance_pilot(**advance)
    assert not adapter.advance_pilot(**advance)
    result = ledger.snapshot("pilot")
    assert result["occupied_slots"] == 1
    assert result["campaigns"][0]["cumulative_deployed_allocation"] == "1.0"
    assert len(result["campaigns"][0]["legs"]) == 2
    assert agent.conn.execute("SELECT COUNT(*) FROM stock_holdings").fetchone()[0] == 1
    assert result["executions"] == []
