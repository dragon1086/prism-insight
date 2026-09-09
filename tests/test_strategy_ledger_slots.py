import sqlite3
from decimal import Decimal as D

import pytest

from prism_core.strategy_ledger import LedgerError, StrategyLedger

T0, T1, T2 = (f"2026-09-10T0{i}:00:00Z" for i in range(3))


def test_slot_math_and_reduction_cancels_add(tmp_path):
    ledger = StrategyLedger(tmp_path / "slots.sqlite")
    ledger.create_book("kr", "KR")
    ledger.apply_target("a", "kr", "c", "TEST", 50, 100, T0)
    state = ledger.mark("m", "c", 110, T1)
    campaign, = state["campaigns"]
    assert D(campaign["normalized_units"]) == D(".005")
    assert D(campaign["invested_price_return_pct"]) == 10
    assert D(campaign["one_slot_contribution"]) == D(".05")
    assert D(state["capacity_normalized_contribution"]) == D(".005")
    assert not {"currency", "initial_capital", "unit_budget", "free_cash", "equity"}.intersection(state)
    state = ledger.sell("s", "c", 110, T1, quantity=".0025")
    campaign, = state["campaigns"]
    assert D(campaign["remaining_allocation"]) == D(".25")
    assert D(campaign["cumulative_deployed_allocation"]) == D(".5")
    assert campaign["add_permission"] == "CANCELLED_BY_REDUCTION"
    with pytest.raises(LedgerError, match="reduction"):
        ledger.apply_target("b", "kr", "c", "TEST", 100, 125, T2)


def test_half_then_half_uses_normalized_units_not_arithmetic_average(tmp_path):
    ledger = StrategyLedger(tmp_path / "slots.sqlite")
    ledger.create_book("us", "US")
    ledger.apply_target("a", "us", "c", "TEST", 50, 100, T0)
    state = ledger.apply_target("b", "us", "c", "TEST", 100, 125, T1)
    campaign, = state["campaigns"]
    assert D(campaign["normalized_units"]) == D(".009")
    assert abs(D(campaign["average_cost"]) - D(1000) / 9) < D("1e-25")
    assert D(campaign["one_slot_contribution"]) == D(".125")


def test_schema_one_rejected_byte_identical(tmp_path):
    path = tmp_path / "old.sqlite"
    StrategyLedger(path)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE strategy_ledger_metadata SET version=1")
    before = path.read_bytes()
    with pytest.raises(LedgerError, match="schema"):
        StrategyLedger(path)
    assert path.read_bytes() == before


def test_existing_unversioned_ledger_rejected_byte_identical(tmp_path):
    path = tmp_path / "unversioned.sqlite"
    ledger = StrategyLedger(path)
    ledger.create_book("kr", "KR")
    with sqlite3.connect(path) as db:
        db.execute("DELETE FROM strategy_ledger_metadata")
    before = path.read_bytes()
    with pytest.raises(LedgerError, match="schema"):
        StrategyLedger(path)
    assert path.read_bytes() == before


@pytest.mark.parametrize("rate", [-1, 1, 2, "NaN", "Infinity", True])
def test_invalid_slippage_has_no_effect(tmp_path, rate):
    ledger = StrategyLedger(tmp_path / "slots.sqlite")
    ledger.create_book("kr", "KR")
    with pytest.raises(LedgerError):
        ledger.apply_target("a", "kr", "c", "TEST", 50, 100, T0, slippage_rate=rate)
    assert ledger.snapshot("kr")["campaigns"] == []


def test_cost_rates_partial_rounding_full_exit_and_reentry(tmp_path):
    ledger = StrategyLedger(tmp_path / "slots.sqlite")
    ledger.create_book("us", "US", max_slots=1)
    state = ledger.apply_target("a", "us", "c", "TEST", 100, 100, T0,
                                fee_rate=".01", slippage_rate=".01")
    campaign, = state["campaigns"]
    assert abs(D(campaign["average_cost"]) - 101) < D("1e-25")
    # Three awkward reductions must allocate entry costs exactly by final exit.
    ledger.sell("s1", "c", 120, T1, quantity=".003", fee_rate=".01", slippage_rate=".01")
    ledger.sell("s2", "c", 120, T1, quantity=".003", fee_rate=".01", slippage_rate=".01")
    state = ledger.sell("s3", "c", 120, T1, fee_rate=".01", slippage_rate=".01")
    campaign, = state["campaigns"]
    assert D(campaign["remaining_allocation"]) == 0
    assert D(campaign["remaining_entry_cost"]) == 0
    assert D(campaign["unrealized_contribution"]) == 0
    expected = D(1) / 101 * 120 * D(".99") * D(".99") - D("1.01")
    assert abs(D(campaign["one_slot_contribution"]) - expected) < D("1e-25")
    assert state["occupied_slots"] == 0
    state = ledger.apply_target("new", "us", "new-campaign", "TEST", 100, 120, T2)
    assert state["occupied_slots"] == 1
    assert D(state["remaining_allocation"]) == 1
    assert D(state["campaigns"][1]["cumulative_deployed_allocation"]) == 1


def test_campaign_and_book_identity_do_not_accept_alternate_aliases(tmp_path):
    ledger = StrategyLedger(tmp_path / "slots.sqlite")
    ledger.create_book("kr", "KR", cohort="baseline-v1", mode="SHADOW")
    with pytest.raises(LedgerError, match="identity already"):
        ledger.create_book("account-alias", "KR", cohort="baseline-v1", mode="SHADOW")
    ledger.create_book("other-cohort", "KR", cohort="candidate-v1", mode="SHADOW")
    ledger.apply_target("a", "kr", "c", "TEST", 50, 100, T0)
    with pytest.raises(LedgerError, match="identity conflict"):
        ledger.apply_target("b", "other-cohort", "c", "TEST", 100, 110, T1)
    assert ledger.snapshot("other-cohort")["campaigns"] == []


def test_failure_after_leg_insert_rolls_back_all_state(tmp_path, monkeypatch):
    ledger = StrategyLedger(tmp_path / "slots.sqlite")
    ledger.create_book("kr", "KR")
    before = ledger.snapshot("kr")
    save = ledger._save

    def fail(db, table, identifier, value):
        if table == "campaigns":
            raise RuntimeError("simulated crash after inserting leg")
        return save(db, table, identifier, value)

    monkeypatch.setattr(ledger, "_save", fail)
    with pytest.raises(RuntimeError):
        ledger.apply_target("a", "kr", "c", "TEST", 50, 100, T0)
    assert ledger.snapshot("kr") == before
    with sqlite3.connect(ledger.path) as db:
        assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM legs").fetchone()[0] == 0
