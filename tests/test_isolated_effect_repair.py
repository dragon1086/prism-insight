"""Expired-context repair must prove an existing event, never replay trading."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

from prism_core import isolated_strategy_effects as effects
from test_isolated_strategy_effects import bound as _bound, enter
from test_isolated_agent_effect_branches import bind_pipeline, bound_us as _bound_us
from test_pilot_lifecycle import calendar, signal_bar, evidence, ENTRY, NOW

bound = _bound
bound_us = _bound_us


def test_prepared_without_ledger_commit_cannot_be_repaired(bound, monkeypatch):
    _, ledger, adapter = bound
    monkeypatch.setattr(ledger, "apply_target", lambda *a, **k: (_ for _ in ()).throw(ValueError("synthetic ledger failure")))
    with pytest.raises(effects.EffectsFailure):
        enter(adapter)
    with pytest.raises(effects.EffectsFailure):
        adapter.repair_projection(ticker="005930", operation="entry")
    assert ledger.snapshot("book")["occupied_slots"] == 0


def test_expired_context_repairs_only_committed_projection_without_ledger_mutation(bound, monkeypatch):
    agent, ledger, adapter = bound
    # Persist a genuine prep and strategy event but interrupt the second DB.
    original = adapter._project
    monkeypatch.setattr(adapter, "_project", lambda *a: (_ for _ in ()).throw(ValueError("synthetic interruption")))
    with pytest.raises(effects.EffectsFailure):
        enter(adapter)
    before = ledger.snapshot("book")
    ledger_bytes = hashlib.sha256(Path(ledger.path).read_bytes()).hexdigest()
    adapter = bind_pipeline(agent, ledger, adapter, monkeypatch)
    monkeypatch.setattr(effects, "_source_now", lambda: "2026-09-10T00:10:00Z")
    # This is a different adapter instance, not retained Python callback state.
    monkeypatch.setattr(ledger, "apply_target", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not mutate ledger")))
    assert adapter.repair_projection(ticker="005930", operation="entry")
    assert not adapter.repair_projection(ticker="005930", operation="entry")
    assert ledger.snapshot("book") == before
    assert hashlib.sha256(Path(ledger.path).read_bytes()).hexdigest() == ledger_bytes
    assert agent.conn.execute("SELECT COUNT(*) FROM stock_holdings").fetchone()[0] == 1
    assert original is not None


def test_old_entry_repair_cannot_roll_back_new_mark_or_uncommitted_stop_change(bound):
    agent, ledger, adapter = bound
    enter(adapter)
    later = effects.IsolatedStrategyEffects(agent, ledger,
        replace(adapter.registration, case_id="later", occurred_at="2026-09-10T00:01:00Z"))
    later.record_mark(ticker="005930", price=110)
    with pytest.raises(effects.EffectsFailure):
        adapter.repair_projection(ticker="005930", operation="entry")
    assert agent.conn.execute("SELECT current_price FROM stock_holdings").fetchone()[0] == 110
    agent.conn.execute("UPDATE stock_holdings SET stop_loss=105")
    agent.conn.commit()
    with pytest.raises(effects.EffectsFailure):
        later.repair_projection(ticker="005930", operation="mark")
    assert agent.conn.execute("SELECT stop_loss FROM stock_holdings").fetchone()[0] == 105


def test_preparation_is_immutable_and_foreign_case_has_no_repair_authority(bound):
    agent, ledger, adapter = bound
    enter(adapter)
    row = agent.conn.execute("SELECT spec_json FROM prism_isolated_effect_preparations").fetchone()
    assert json.loads(row[0])["book_id"] == "book"
    with pytest.raises(__import__("sqlite3").IntegrityError):
        agent.conn.execute("UPDATE prism_isolated_effect_preparations SET spec_json='{}'")
    agent.conn.rollback()
    foreign = effects.IsolatedStrategyEffects(agent, ledger, replace(adapter.registration, case_id="foreign"))
    with pytest.raises(effects.EffectsFailure):
        foreign.repair_projection(ticker="005930", operation="entry")


def test_committed_event_with_wrong_payload_is_not_projection_authority(bound, monkeypatch):
    agent, ledger, adapter = bound
    real_apply = ledger.apply_target
    monkeypatch.setattr(ledger, "apply_target", lambda *a, **k: (_ for _ in ()).throw(ValueError("synthetic precommit failure")))
    with pytest.raises(effects.EffectsFailure):
        enter(adapter)
    event = agent.conn.execute("SELECT event_id FROM prism_isolated_effect_preparations").fetchone()[0]
    real_apply(event, "book", "campaign1", "005930", 100, 101, adapter.registration.occurred_at,
        source_hash=adapter.registration.source_hash)
    with pytest.raises(effects.EffectsFailure):
        adapter.repair_projection(ticker="005930", operation="entry")
    assert agent.conn.execute("SELECT COUNT(*) FROM stock_holdings").fetchone()[0] == 0


def test_new_uncommitted_preparation_blocks_older_stop_rollback(bound, monkeypatch):
    agent, ledger, adapter = bound
    enter(adapter)
    agent.conn.execute("UPDATE stock_holdings SET stop_loss=105")
    agent.conn.commit()
    later = effects.IsolatedStrategyEffects(agent, ledger,
        replace(adapter.registration, case_id="later", occurred_at="2026-09-10T00:01:00Z"))
    monkeypatch.setattr(ledger, "mark", lambda *a, **k: (_ for _ in ()).throw(ValueError("synthetic beforemark failure")))
    with pytest.raises(effects.EffectsFailure):
        later.record_mark(ticker="005930", price=110)
    with pytest.raises(effects.EffectsFailure):
        adapter.repair_projection(ticker="005930", operation="entry")
    with pytest.raises(effects.EffectsFailure):
        later.repair_projection(ticker="005930", operation="mark")
    assert agent.conn.execute("SELECT stop_loss FROM stock_holdings").fetchone()[0] == 105


def test_old_pilot_repair_cannot_undo_later_promotion_and_stop(bound_us):
    agent, ledger, original = bound_us
    ledger.create_book("pilot", "US", cohort="split-pilot-v1", mode="SHADOW")
    registration = replace(original.registration, book_id="pilot", occurred_at=ENTRY)
    first = effects.IsolatedStrategyEffects(agent, ledger, registration)
    assert first.open_pilot(ticker="SNDK", company_name="SYNTHETIC pilot", price=100,
        scenario={"stop_loss": 95}, signal_bar=signal_bar(), calendar=calendar(), entry_eligible=True)
    later = effects.IsolatedStrategyEffects(agent, ledger,
        replace(registration, case_id="promotion", occurred_at=NOW))
    assert later.advance_pilot(ticker="SNDK", company_name="SYNTHETIC pilot", scenario={"stop_loss": 102},
        expected_revision=0, evidence=evidence())
    with pytest.raises(effects.EffectsFailure):
        first.repair_projection(ticker="SNDK", operation="pilot_open")
    assert ledger.snapshot("pilot")["campaigns"][0]["pilot"]["state"] == "FULL_100"
    assert agent.conn.execute("SELECT stop_loss FROM us_stock_holdings").fetchone()[0] == 102
