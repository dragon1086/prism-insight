"""Provider-neutral suitability, not live accuracy or trading policy tests."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from prism_core.source_observation_quality import (
    ObservationPolicy,
    PriceObservation,
    assess_observation,
)

NOW = datetime(2026, 9, 18, 15, tzinfo=timezone.utc)


def policy(**changes):
    values = {"decision_at": NOW, "expected_asof": NOW, "max_lag_seconds": 1200,
              "symbol": "NASDAQ:MSFT", "interval": "1m", "session": "regular",
              "adjustment": "split_only", "currency": "USD"}
    values.update(changes)
    return ObservationPolicy(**values)


def observation(**changes):
    values = {"source": "TradingView", "symbol": "NASDAQ:MSFT", "interval": "1m",
              "session": "regular", "adjustment": "split_only", "currency": "USD",
              "asof": NOW - timedelta(minutes=15), "available_at": NOW,
              "captured_at": NOW, "final": True}
    values.update(changes)
    return PriceObservation(**values)


@pytest.mark.parametrize("source", ["TradingView", "KIS", "yfinance"])
def test_delayed_observation_is_eligible_for_explicit_use_case(source):
    result = assess_observation(observation(source=source), policy())
    assert result["status"] == "FIT_FOR_COMPARISON"
    assert result["lag_seconds"] == 900
    assert result["source"] == source
    assert not result["fact_validated"]
    assert not result["execution_authorized"]


def test_provider_identity_never_changes_quality():
    results = [assess_observation(observation(source=name), policy())
               for name in ("TradingView", "KIS", "yfinance")]
    for result in results:
        result.pop("source")
    assert results[0] == results[1] == results[2]


def test_same_data_stale_for_tighter_explicit_use_case():
    assert assess_observation(observation(), policy(max_lag_seconds=60))["status"] == "STALE"


def test_boundary_is_inclusive():
    assert assess_observation(observation(), policy(max_lag_seconds=900))["status"] == "FIT_FOR_COMPARISON"


def test_weekend_uses_caller_supplied_last_session_without_inventing_calendar():
    end = NOW.replace(hour=20)
    observed = observation(asof=end, available_at=end, captured_at=end, interval="1D")
    expected = policy(decision_at=end + timedelta(days=1), expected_asof=end,
                      interval="1D", max_lag_seconds=0)
    assert assess_observation(observed, expected)["status"] == "FIT_FOR_COMPARISON"


@pytest.mark.parametrize("field", ["asof", "captured_at", "available_at"])
def test_unknown_time_not_fabricated(field):
    result = assess_observation(observation(**{field: None}), policy())
    assert result["status"] == "UNKNOWN"
    assert f"MISSING_{field.upper()}" in result["reasons"]


@pytest.mark.parametrize("field", ["asof", "captured_at", "available_at"])
def test_naive_time_is_unknown_not_localized(field):
    result = assess_observation(observation(**{field: NOW.replace(tzinfo=None)}), policy())
    assert result["status"] == "UNKNOWN"
    assert f"NAIVE_{field.upper()}" in result["reasons"]


@pytest.mark.parametrize("field", ["asof", "captured_at", "available_at"])
def test_future_time_is_invalid(field):
    result = assess_observation(observation(**{field: NOW + timedelta(seconds=1)}), policy())
    assert result["status"] == "INVALID"


def test_capture_after_historical_decision_cannot_certify_snapshot():
    assert assess_observation(observation(captured_at=NOW + timedelta(days=2)), policy())["status"] == "INVALID"


@pytest.mark.parametrize("changes", [
    {"available_at": NOW - timedelta(minutes=16)},
    {"captured_at": NOW - timedelta(minutes=1)},
    {"asof": "2026-09-18"}, {"asof": 0}, {"final": 1},
])
def test_malformed_or_inconsistent_observation(changes):
    assert assess_observation(observation(**changes), policy())["status"] == "INVALID"


@pytest.mark.parametrize("field,value", [
    ("symbol", "KRX:005930"), ("interval", "1D"), ("session", "extended"),
    ("adjustment", "split_and_dividend"), ("currency", "KRW"),
])
def test_known_scope_mismatch_is_not_blended(field, value):
    result = assess_observation(observation(**{field: value}), policy())
    assert result["status"] == "INCOMPARABLE"
    assert f"MISMATCH_{field.upper()}" in result["reasons"]


@pytest.mark.parametrize("field", ["symbol", "interval", "session", "adjustment", "currency"])
def test_missing_scope_is_unknown(field):
    assert assess_observation(observation(**{field: None}), policy())["status"] == "UNKNOWN"


def test_finality_is_explicit_not_inferred_from_clock():
    assert assess_observation(observation(final=None), policy())["status"] == "UNKNOWN"
    assert assess_observation(observation(final=False), policy())["status"] == "INCOMPARABLE"
    assert assess_observation(observation(final=False), policy(require_final=False))["status"] == "FIT_FOR_COMPARISON"


def test_timezone_equivalent_instants():
    offset = timezone(timedelta(hours=9))
    obs = observation()
    shifted = replace(obs, asof=obs.asof.astimezone(offset),
                      available_at=obs.available_at.astimezone(offset),
                      captured_at=obs.captured_at.astimezone(offset))
    assert assess_observation(obs, policy()) == assess_observation(shifted, policy())


def test_dst_fallback_compares_absolute_time_not_wall_clock():
    ny = ZoneInfo("America/New_York")
    first = datetime(2026, 11, 1, 1, 45, tzinfo=ny, fold=0)
    second = datetime(2026, 11, 1, 1, 15, tzinfo=ny, fold=1)
    result = assess_observation(observation(asof=first, available_at=second, captured_at=second),
                                policy(decision_at=second, expected_asof=second, max_lag_seconds=1800))
    assert result["status"] == "FIT_FOR_COMPARISON"
    assert result["lag_seconds"] == 1800


def test_policy_rejects_absolute_future_during_dst_fold():
    ny = ZoneInfo("America/New_York")
    earlier = datetime(2026, 11, 1, 1, 45, tzinfo=ny, fold=0)
    later = datetime(2026, 11, 1, 1, 15, tzinfo=ny, fold=1)
    with pytest.raises(ValueError):
        policy(decision_at=earlier, expected_asof=later)


def test_dst_fold_future_observation_is_never_accepted():
    ny = ZoneInfo("America/New_York")
    decision = datetime(2026, 11, 1, 1, 30, tzinfo=ny, fold=0)
    future = datetime(2026, 11, 1, 1, 15, tzinfo=ny, fold=1)
    result = assess_observation(observation(asof=future, available_at=future, captured_at=future),
                                policy(decision_at=decision, expected_asof=decision))
    assert result["status"] == "INVALID"
    assert {"FUTURE_ASOF", "FUTURE_AVAILABLE_AT", "FUTURE_CAPTURED_AT"} <= set(result["reasons"])


def test_precedence_retains_all_reasons():
    result = assess_observation(observation(currency="KRW", final=None,
                                           captured_at=NOW + timedelta(seconds=1)), policy(max_lag_seconds=1))
    assert result["status"] == "INVALID"
    assert {"MISMATCH_CURRENCY", "MISSING_FINALITY", "STALE_ASOF",
            "FUTURE_CAPTURED_AT"} <= set(result["reasons"])


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), True, "900", 10**400, -(10**400)])
def test_policy_rejects_bad_lag(value):
    with pytest.raises(ValueError):
        policy(max_lag_seconds=value)


@pytest.mark.parametrize("changes", [
    {"decision_at": NOW.replace(tzinfo=None)},
    {"expected_asof": NOW + timedelta(seconds=1)},
    {"symbol": ""}, {"currency": None}, {"require_final": "false"},
])
def test_policy_rejects_missing_or_inconsistent_expectations(changes):
    with pytest.raises(ValueError):
        policy(**changes)
