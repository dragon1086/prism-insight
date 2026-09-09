import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal as D

import pytest

from prism_core.strategy_ledger import LedgerError, StrategyLedger

T0 = "2026-09-10T00:00:00+00:00"
T1 = "2026-09-10T01:00:00+00:00"
T2 = "2026-09-10T02:00:00+00:00"


@pytest.fixture
def ledger(tmp_path):
    result = StrategyLedger(tmp_path / "strategy.sqlite")
    result.create_book("kr", "KR")
    return result


def buy(
    ledger, target=50, price=10, event="entry", campaign="sdi", timestamp=T0, **kwargs
):
    return ledger.apply_target(
        event, "kr", campaign, campaign, target, price, timestamp, **kwargs
    )


def test_half_budget_price_return_not_portfolio_return(ledger):
    buy(ledger)
    state = ledger.mark("mark", "sdi", 11, T1)
    assert D(state["unrealized_contribution"]) == D(".05")
    assert D(state["capacity_normalized_contribution"]) == D(".005")
    assert D(state["remaining_allocation"]) == D(".5")
    assert D(state["campaigns"][0]["normalized_units"]) == D(".05")
    assert state["campaigns"][0]["mark_basis"] == "explicit_mark"
    json.dumps(state)

def test_staged_budget_weighted_cost_and_identity(ledger):
    for index, (target, price) in enumerate([(10, 10), (30, 20), (60, 30), (100, 40)]):
        state = buy(ledger, target, price, str(index), timestamp=f"2026-09-10T0{index}:00:00Z")
    campaign = state["campaigns"][0]
    assert D(campaign["normalized_units"]) == D(".04")
    assert D(campaign["average_cost"]) == 25
    assert [D(leg["allocation"]) for leg in campaign["legs"]] == [D(".1"), D(".2"), D(".3"), D(".4")]
    assert D(campaign["cumulative_deployed_allocation"]) == 1
    assert D(state["total_slot_contribution"]) == D(state["realized_contribution"]) + D(state["unrealized_contribution"])

def test_partial_exit_does_not_reset_cumulative_budget(ledger):
    buy(ledger)
    state = ledger.sell("sell", "sdi", 12, T1, quantity=".02", fee_rate=".01")
    assert D(state["realized_contribution"]) == D(".0376")
    assert D(state["campaigns"][0]["remaining_allocation"]) == D(".3")
    state = buy(ledger, event="same-target", timestamp=T2)
    assert len(state["campaigns"][0]["legs"]) == 2
    with pytest.raises(LedgerError, match="reduction"):
        buy(ledger, target=100, event="raise", timestamp=T2)
    assert D(state["campaigns"][0]["cumulative_deployed_allocation"]) == D(".5")
    assert D(state["campaigns"][0]["normalized_units"]) == D(".03")

def test_full_exit_closed_campaign_and_cash(ledger):
    buy(ledger)
    state = ledger.sell("exit", "sdi", 11, T1)
    assert D(state["total_slot_contribution"]) == D(".05")
    assert D(state["campaigns"][0]["remaining_allocation"]) == 0
    with pytest.raises(LedgerError, match="cannot reopen"):
        buy(ledger, 100, 11, event="reopen", timestamp=T2)
    assert len(ledger.snapshot("kr")["campaigns"][0]["legs"]) == 2


@pytest.mark.parametrize(
    "status", ["REJECTED", "PARTIAL", "UNKNOWN", "SUBMITTED", "FILLED"]
)
def test_execution_never_changes_strategy(ledger, status):
    before = buy(ledger)
    evidence = ({"confirmed_quantity": 1, "confirmed_price": 10,
                 "evidence_source": "broker_fill_query"}
                if status in {"PARTIAL", "FILLED"} else {})
    after = ledger.observe_execution("broker", "sdi", "profile-alias", status, T1, **evidence)
    assert {
        k: v for k, v in before.items() if k not in {"executions", "event_applied"}
    } == {k: v for k, v in after.items() if k not in {"executions", "event_applied"}}
    assert after["executions"][0]["confirmed_quantity"] == ("1" if evidence else None)


@pytest.mark.parametrize("status", ["PARTIAL", "FILLED"])
def test_fill_status_without_evidence_is_rejected(ledger, status):
    buy(ledger)
    with pytest.raises(LedgerError, match="confirmed fill"):
        ledger.observe_execution("missing", "sdi", "profile", status, T1)
    assert not ledger.snapshot("kr")["executions"]


def test_future_cash_cannot_fund_past_other_campaign(ledger):
    buy(ledger, 100)
    ledger.sell("future-sale", "sdi", 20, T2)
    before = ledger.snapshot("kr")
    with pytest.raises(LedgerError, match="book accounting"):
        buy(ledger, 100, event="past-buy", campaign="other", timestamp=T1)
    assert ledger.snapshot("kr") == before
    assert buy(ledger, 100)["event_applied"] is False  # old exact retry remains safe


def test_book_rejects_unsupported_market(ledger):
    with pytest.raises(LedgerError, match="market"):
        ledger.create_book("wrong", "EU")
    with pytest.raises(TypeError):
        ledger.create_book("wrong", "KR", currency="USD")

def test_confirmed_fill_evidence_and_independence(ledger):
    buy(ledger)
    with pytest.raises(LedgerError, match="confirmed fill"):
        ledger.observe_execution(
            "e",
            "sdi",
            "alias",
            "SUBMITTED",
            T1,
            confirmed_quantity=1,
            confirmed_price=10,
            evidence_source="broker_fill_query",
        )
    with pytest.raises(LedgerError, match="confirmed fill"):
        ledger.observe_execution(
            "e", "sdi", "alias", "FILLED", T1, confirmed_quantity=1, confirmed_price=10
        )
    state = ledger.observe_execution(
        "e",
        "sdi",
        "alias",
        "PARTIAL",
        T1,
        confirmed_quantity=1,
        confirmed_price=10,
        evidence_source="broker_fill_query",
    )
    assert D(state["campaigns"][0]["normalized_units"]) == D(".05")
    assert state["executions"][0]["confirmed_quantity"] == "1"


def test_duplicate_conflict_restart_and_immutable_history(ledger):
    buy(ledger, source_hash="one")
    reopened = StrategyLedger(ledger.path)
    assert buy(reopened, source_hash="one")["event_applied"] is False
    with pytest.raises(LedgerError, match="conflict"):
        buy(reopened, source_hash="two")
    with pytest.raises(LedgerError, match="conflict"):
        buy(reopened, target=60, source_hash="one")
    with sqlite3.connect(ledger.path) as db, pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute("DELETE FROM legs")
    assert len(reopened.snapshot("kr")["campaigns"][0]["legs"]) == 1


def test_two_connections_duplicate_concurrency(ledger):
    def attempt(_):
        return buy(StrategyLedger(ledger.path))["event_applied"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, range(2)))
    assert sorted(results) == [False, True]
    assert D(ledger.snapshot("kr")["remaining_allocation"]) == D(".5")


def test_cash_race_is_atomic(tmp_path):
    path = tmp_path / "race.sqlite"
    ledger = StrategyLedger(path)
    ledger.create_book("kr", "KR", max_slots=1)

    def attempt(index):
        try:
            buy(StrategyLedger(path), 100, event=str(index), campaign=str(index))
            return True
        except LedgerError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, range(2))) == [False, True]
    assert ledger.snapshot("kr")["occupied_slots"] == 1
    assert len(ledger.snapshot("kr")["campaigns"]) == 1


def test_policy_caps_names_and_total_units(ledger):
    for regime in ("moderate_bull", "strong_bull", "parabolic"):
        with pytest.raises(LedgerError, match="100 percent cap"):
            buy(ledger, 101, regime=regime)
    for index in range(10):
        buy(ledger, 100, campaign=str(index), event=str(index))
    state = ledger.snapshot("kr")
    assert D(state["remaining_allocation"]) == 10
    assert state["occupied_slots"] == 10
    with pytest.raises(LedgerError, match="slot"):
        buy(ledger, 100, campaign="eleven", event="eleven")

def test_slot_limit(ledger):
    for i in range(10):
        buy(ledger, 10, campaign=str(i), event=str(i))
    with pytest.raises(LedgerError, match="slot"):
        buy(ledger, 10, campaign="eleven", event="eleven")


def test_no_averaging_down_and_ordering(ledger):
    buy(ledger)
    with pytest.raises(LedgerError, match="below average"):
        buy(ledger, 100, 9, event="down", timestamp=T1)
    ledger.mark("later", "sdi", 8, T2)
    with pytest.raises(LedgerError, match="out-of-order"):
        ledger.sell("early", "sdi", 10, T1)
    assert len(ledger.snapshot("kr")["campaigns"][0]["legs"]) == 1


@pytest.mark.parametrize("invalid", [0, -1, "NaN", "Infinity", "oops", True, None])
def test_invalid_numbers(ledger, invalid):
    with pytest.raises(LedgerError):
        buy(ledger, price=invalid)
    assert ledger.snapshot("kr")["campaigns"] == []


def test_oversell_and_decrease_rollback(ledger):
    buy(ledger)
    with pytest.raises(LedgerError, match="quantity"):
        ledger.sell("bad", "sdi", 10, T1, quantity=6)
    with pytest.raises(LedgerError, match="decrease"):
        buy(ledger, 10, event="decrease")
    assert D(ledger.snapshot("kr")["remaining_allocation"]) == D(".5")


def test_unknown_database_refused(tmp_path):
    path = tmp_path / "legacy.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE holdings (id INTEGER)")
    with pytest.raises(LedgerError, match="unknown database"):
        StrategyLedger(path)
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall() == [("holdings",)]


def test_explicit_book_config_no_cross_market_merge(ledger):
    ledger.create_book("us", "US")
    assert ledger.snapshot("us")["market"] == "US"
    assert ledger.snapshot("kr")["market"] == "KR"
    with pytest.raises(LedgerError, match="configuration conflict"):
        ledger.create_book("kr", "KR", max_slots=9)
    with pytest.raises(LedgerError):
        buy(ledger, timestamp="2026-09-10T00:00:00")

def test_sell_mark_and_execution_source_conflict(ledger):
    buy(ledger)
    ledger.mark("m", "sdi", 11, T1, source_hash="one")
    with pytest.raises(LedgerError, match="conflict"):
        ledger.mark("m", "sdi", 11, T1, source_hash="two")
    ledger.sell("s", "sdi", 11, T2, quantity=".01", source_hash="one")
    assert not ledger.sell("s", "sdi", 11, T2, quantity=".01", source_hash="one")["event_applied"]
    with pytest.raises(LedgerError, match="conflict"):
        ledger.sell("s", "sdi", 11, T2, quantity=".01", source_hash="two")
    ledger.observe_execution("x", "sdi", "alias", "UNKNOWN", T2, source_hash="one")
    with pytest.raises(LedgerError, match="conflict"):
        ledger.observe_execution("x", "sdi", "alias", "UNKNOWN", T2, source_hash="two")


def test_failed_event_rolls_back_and_can_retry(ledger):
    buy(ledger)
    with pytest.raises(LedgerError, match="below average"):
        buy(ledger, 100, 9, event="retry", timestamp=T1)
    assert buy(ledger, 100, 10, event="retry", timestamp=T1)["event_applied"]
    assert len(ledger.snapshot("kr")["campaigns"][0]["legs"]) == 2
    assert ledger.list_book_ids() == ["kr"]


def test_partial_metadata_is_not_permission_to_migrate(tmp_path):
    path = tmp_path / "other.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE strategy_ledger_metadata (version INTEGER)")
        db.execute("CREATE TABLE real_positions (quantity INTEGER)")
    with pytest.raises(LedgerError, match="unknown database"):
        StrategyLedger(path)


def test_buy_fee_rate_cost_and_realized_identity(ledger):
    state = buy(ledger, price=100, fee_rate=".01")
    campaign = state["campaigns"][0]
    assert D(campaign["normalized_units"]) == D(".005")
    assert D(campaign["remaining_allocation"]) == D(".5")
    assert D(campaign["average_cost"]) == 100
    assert D(campaign["remaining_entry_cost"]) == D(".005")
    assert D(campaign["legs"][0]["fee_rate"]) == D(".01")
    assert D(state["total_slot_contribution"]) == D("-.005")
    assert not buy(ledger, price=100, fee_rate=".01")["event_applied"]
    with pytest.raises(LedgerError, match="conflict"):
        buy(ledger, price=100, fee_rate=".02")
    state = ledger.sell("exit", "sdi", 110, T1, fee_rate=".01")
    assert D(state["realized_contribution"]) == D(".0395")
    assert D(state["total_slot_contribution"]) == D(".0395")

@pytest.mark.parametrize("fee", [-1, "NaN", "Infinity", True])
def test_buy_fee_invalid_rollback(ledger, fee):
    with pytest.raises(LedgerError):
        buy(ledger, fee_rate=fee)
    assert ledger.snapshot("kr")["campaigns"] == []


def test_buy_fee_rate_limit_and_unchanged_target(ledger):
    with pytest.raises(LedgerError, match="rate"):
        buy(ledger, fee_rate=1)
    assert ledger.snapshot("kr")["campaigns"] == []
    buy(ledger)
    with pytest.raises(LedgerError, match="unchanged target"):
        buy(ledger, event="same", fee_rate=".01", timestamp=T1)
    assert D(ledger.snapshot("kr")["remaining_allocation"]) == D(".5")
