"""Closed strategy campaign projections, never broker fills or cash PnL."""
from dataclasses import replace
from datetime import datetime
from decimal import Decimal, localcontext
import json

import pytest

from prism_core.isolated_strategy_effects import EffectsFailure, IsolatedStrategyEffects
from prism_core.strategy_ledger_outbox import StrategyLedgerOutbox
from reentry_cooldown import reentry_block
from test_isolated_strategy_effects import bound as _bound, enter

bound = _bound


@pytest.fixture
def history_bound(bound):
    agent, ledger, adapter = bound
    return agent, ledger, adapter


def basis(agent, ledger, adapter, case_id, occurred_at):
    guard = ledger.campaign_guard("campaign1")
    scenario = agent.conn.execute("SELECT scenario FROM stock_holdings").fetchone()[0]
    registration = replace(adapter.registration, case_id=case_id, occurred_at=occurred_at,
        exit_bases=(("005930", guard["campaign_hash"], guard["normalized_units"], scenario),))
    return IsolatedStrategyEffects(agent, ledger, registration)


def test_half_slot_loss_history_uses_campaign_return_not_slot_contribution(history_bound):
    agent, ledger, adapter = history_bound
    snapshot = ledger.apply_target("seed", "book", "campaign1", "005930", 50, 100, adapter.registration.occurred_at)
    adapter._project(snapshot, "005930", "SYNTHETIC half slot", {"target_price": 120, "stop_loss": 95})
    assert adapter.record_exit(ticker="005930", price=90, sell_reason="Stop-loss synthetic fixture")
    row = dict(agent.conn.execute("SELECT * FROM trading_history").fetchone())
    assert row["profit_rate"] == -10
    assert Decimal(ledger.snapshot("book")["realized_contribution"]) == Decimal("-.05")
    assert row["buy_price"] == 100 and row["sell_price"] == 90
    assert row["exit_kind"] == "stop"
    metadata = json.loads(row["scenario"])["_strategy_projection"]
    assert metadata["account_execution_status"] == "UNKNOWN"
    assert metadata["return_basis"] == "CAMPAIGN_NET_RETURN_ON_CUMULATIVE_ALLOCATION"
    verdict = reentry_block("KR", "005930", "virtual:test", db_path=agent.db_path,
        now=datetime(2026, 9, 10, 10, 0, 0), fail_closed=True)
    assert verdict and verdict["after_loss"] is True


def test_partial_then_full_has_one_history_with_weighted_execution_prices(history_bound):
    agent, ledger, adapter = history_bound
    enter(adapter)
    first = basis(agent, ledger, adapter, "partial", "2026-09-10T00:01:00Z")
    assert first.record_exit(ticker="005930", price=90, fraction=.5, sell_reason="Stop-loss synthetic partial")
    assert agent.conn.execute("SELECT COUNT(*) FROM trading_history").fetchone()[0] == 0
    final = basis(agent, ledger, adapter, "final", "2026-09-10T00:02:00Z")
    assert final.record_exit(ticker="005930", price=110, sell_reason="Target synthetic close")
    assert not final.record_exit(ticker="005930", price=110, sell_reason="Target synthetic close")
    rows = agent.conn.execute("SELECT * FROM trading_history").fetchall()
    assert len(rows) == 1
    row = dict(rows[0])
    assert row["buy_price"] == row["sell_price"] == 100
    assert row["profit_rate"] == 0
    assert ledger.snapshot("book")["executions"] == []


def test_history_and_holding_delete_rollback_together_but_ledger_survives(history_bound):
    agent, ledger, adapter = history_bound
    enter(adapter)
    agent.conn.execute("CREATE TRIGGER synthetic_delete_failure BEFORE DELETE ON stock_holdings BEGIN SELECT RAISE(ABORT, 'synthetic projection failure'); END")
    agent.conn.commit()
    with pytest.raises(EffectsFailure):
        adapter.record_exit(ticker="005930", price=90, sell_reason="Stop-loss synthetic fixture")
    assert ledger.snapshot("book")["occupied_slots"] == 0
    assert agent.conn.execute("SELECT COUNT(*) FROM stock_holdings").fetchone()[0] == 1
    assert agent.conn.execute("SELECT COUNT(*) FROM trading_history").fetchone()[0] == 0
    agent.conn.execute("DROP TRIGGER synthetic_delete_failure")
    agent.conn.commit()
    assert not adapter.record_exit(ticker="005930", price=90, sell_reason="Stop-loss synthetic fixture")
    assert agent.conn.execute("SELECT COUNT(*) FROM stock_holdings").fetchone()[0] == 0
    assert agent.conn.execute("SELECT COUNT(*) FROM trading_history").fetchone()[0] == 1


def test_hold_mark_updates_canonical_pnl_without_new_trade_or_notice(history_bound):
    agent, ledger, adapter = history_bound
    enter(adapter)
    assert adapter.record_mark(ticker="005930", price=110)
    assert not adapter.record_mark(ticker="005930", price=110)
    result = ledger.snapshot("book")
    assert Decimal(result["unrealized_contribution"]) == Decimal(".1")
    assert agent.conn.execute("SELECT current_price FROM stock_holdings").fetchone()[0] == 110
    assert agent.conn.execute("SELECT COUNT(*) FROM trading_history").fetchone()[0] == 0
    assert len(StrategyLedgerOutbox(ledger).pending()) == 1


def test_registered_full_exit_preserves_all_40_digit_normalized_units(history_bound):
    agent, ledger, adapter = history_bound
    adapter.record_entry(ticker="005930", company_name="SYNTHETIC repeating units", price="100.01", scenario={}, is_add=False)
    final = basis(agent, ledger, adapter, "final", "2026-09-10T00:01:00Z")
    assert final.record_exit(ticker="005930", price=110, sell_reason="Target synthetic full close")
    assert ledger.snapshot("book")["occupied_slots"] == 0
    assert agent.conn.execute("SELECT COUNT(*) FROM trading_history").fetchone()[0] == 1


def test_history_execution_prices_and_net_return_keep_modeled_costs_separate(history_bound):
    agent, ledger, adapter = history_bound
    snapshot = ledger.apply_target("cost-seed", "book", "campaign1", "005930", 100, 100,
        adapter.registration.occurred_at, fee_rate=".01", slippage_rate=".02")
    adapter._project(snapshot, "005930", "SYNTHETIC cost fixture", {})
    with localcontext() as context:
        context.prec = 40
        half = Decimal(ledger.campaign_guard("campaign1")["normalized_units"]) / 2
    snapshot = ledger.sell("cost-partial", "campaign1", 110, "2026-09-10T00:01:00Z",
        quantity=half, fee_rate=".01", slippage_rate=".02")
    adapter._project(snapshot, "005930", "SYNTHETIC cost fixture", {})
    final = basis(agent, ledger, adapter, "final", "2026-09-10T00:02:00Z")
    assert final.record_exit(ticker="005930", price=90, sell_reason="Stop-loss synthetic close")
    row = dict(agent.conn.execute("SELECT * FROM trading_history").fetchone())
    assert row["buy_price"] == pytest.approx(102)
    assert row["sell_price"] == pytest.approx(98.9)
    expected_net = float(Decimal(ledger.snapshot("book")["realized_contribution"]) * 100)
    assert row["profit_rate"] == pytest.approx(expected_net)
    assert row["profit_rate"] < (row["sell_price"] / row["buy_price"] - 1) * 100
    metadata = json.loads(row["scenario"])["_strategy_projection"]
    with localcontext() as context:
        context.prec = 40
        expected_cost = Decimal(".01") + half * Decimal("107.8") * Decimal(".01")
    assert Decimal(metadata["cost_contribution_total"]) == expected_cost
    assert len(metadata["canonical_event_sources"]) == 3
