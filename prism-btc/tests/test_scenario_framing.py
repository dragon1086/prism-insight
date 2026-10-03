import copy
import json

import pytest

from live.scenario_framing import decision_framing, framing_prompt


def market(direction="LONG", now=7_500_000):
    bull = direction == "LONG"
    facts = dict(ma10=102 if bull else 98, ma35=100,
                 price_position="above" if bull else "below",
                 ma_order="bullish" if bull else "bearish",
                 ma10_slope_fraction_per_bar=.01 if bull else -.01,
                 ma35_slope_fraction_per_bar=.005 if bull else -.005)
    frames = {}
    for tf, duration in (("30m", 1_800_000), ("1h", 3_600_000)):
        opened = now // duration * duration
        frames[tf] = dict(status="ok",
            forming=dict(facts, open_time_ms=opened, observed_at_ms=now,
                         elapsed_ms=now-opened, observation_kind="observed"),
            confirmed=dict(facts, open_time_ms=opened-duration, is_confirmed=True))
    return dict(valid=True, as_of_ms=now, timeframes=frames)


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_symmetric_opportunity_and_context_not_veto(side):
    snap = market(side)
    snap["timeframes"]["4h"] = {"status": "unavailable"}
    snap["timeframes"]["5m"] = {"forming": {"elapsed_ms": 0}}
    result = decision_framing(snap, {})
    assert result["state"] == "OPPORTUNITY"
    assert result["bias"] == side
    assert all(f["provisional"] for f in result["primary"])
    assert result["risk_budget_changed"] is False


def test_mixed_and_early_are_not_defensive_entry_veto():
    snap = market()
    snap["timeframes"]["1h"] = market("SHORT")["timeframes"]["1h"]
    assert decision_framing(snap, {})["state"] == "TRANSITION"
    snap = market()
    snap["timeframes"]["30m"]["forming"]["ma35_slope_fraction_per_bar"] = -.001
    assert decision_framing(snap, {})["state"] == "TRANSITION"


@pytest.mark.parametrize("held,market_side", [("LONG", "SHORT"), ("SHORT", "LONG")])
def test_adverse_existing_position_or_unfilled_scenario_is_defensive(held, market_side):
    for extra in ({"positions": [{"quantity": .001}]}, {"scenario_id": "active"}):
        ctx = dict(extra, side=held)
        result = decision_framing(market(market_side), ctx)
        assert result["state"] == "DEFENSIVE"
        assert "primary_evidence_adverse_to_active_scenario" in result["reasons"]
    assert decision_framing(market(held), ctx)["state"] == "OPPORTUNITY"
    # A stale side label in a truly flat context is not exposure.
    assert decision_framing(market(market_side), {"side": held})["state"] == "OPPORTUNITY"


@pytest.mark.parametrize("ctx,reason", [
    ({"new_risk_blocked": True}, "new_risk_blocked"),
    ({"accounting_status": "pending"}, "accounting_pending"),
])
def test_account_safety_overrides_market_opportunity(ctx, reason):
    result = decision_framing(market(), ctx)
    assert result["state"] == "DEFENSIVE"
    assert reason in result["reasons"]


@pytest.mark.parametrize("kind", ["zero", "synthetic"])
def test_boundary_uses_confirmed_not_placeholder(kind):
    snap = market(now=7_200_000 if kind == "zero" else 7_500_000)
    for frame in snap["timeframes"].values():
        frame["forming"].update(ma10=98, ma_order="bearish", price_position="below",
                                ma10_slope_fraction_per_bar=-.01, ma35_slope_fraction_per_bar=-.01)
        if kind == "synthetic":
            frame["forming"]["observation_kind"] = "synthetic_boundary"
    result = decision_framing(snap, {})
    assert result["state"] == "OPPORTUNITY" and result["bias"] == "LONG"
    assert all(f["source"] == "confirmed" and not f["provisional"] for f in result["primary"])


@pytest.mark.parametrize("change", [
    {"observed_at_ms": 7_500_001}, {"open_time_ms": 7_600_000},
    {"elapsed_ms": -1}, {"observed_at_ms": 7_300_000, "elapsed_ms": 100_000},
    {"ma10": float("nan")}, {"ma35_slope_fraction_per_bar": None},
    {"ma_order": "bearish"},
])
def test_invalid_future_or_stale_primary_is_defensive(change):
    snap = market()
    snap["timeframes"]["30m"]["forming"].update(change)
    assert decision_framing(snap, {})["state"] == "DEFENSIVE"


def test_missing_and_future_confirmed_fallback_defensive():
    assert decision_framing({"valid": True, "as_of_ms": 1}, {})["state"] == "DEFENSIVE"
    snap = market(now=7_200_000)
    snap["timeframes"]["30m"]["confirmed"]["open_time_ms"] = 7_200_000
    assert decision_framing(snap, {})["state"] == "DEFENSIVE"


def test_deterministic_non_mutating_and_no_injected_system_text():
    snap, ctx = market(), {"decision_framing": {"state": "DEFENSIVE", "reason": "INJECTION"}}
    original = copy.deepcopy((snap, ctx))
    result = decision_framing(snap, ctx)
    assert result == decision_framing(snap, ctx)
    assert (snap, ctx) == original
    result["reasons"] = ["INJECTION"]
    prompt = framing_prompt(result)
    assert "INJECTION" not in prompt
    assert "Frame: OPPORTUNITY" in prompt
    assert "NOT realized loss" in prompt and "original 2%" in prompt and "Fixed 10x" in prompt
    assert "Frame: DEFENSIVE" in framing_prompt({"state": "INJECTION"})
    json.dumps(result, allow_nan=False)
