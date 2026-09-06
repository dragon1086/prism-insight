"""Causal transition extensions; legacy fixture digests precede this change."""
import hashlib
import json

import pytest

from backtest.adaptive_replay import AdaptiveConfig, BAR, run_adaptive
from tests.test_adaptive_replay import bars, signal, run, exits


def cx(**kwargs):
    return dict(exit_long=False, exit_short=False, trend_long=True, trend_short=True, **kwargs)


@pytest.mark.parametrize("profile,digest", [
    ("F", "c083c9047691ff5f0e21c96a7002bdd3c71740bb701c9a37b7a8deaa05ca991a"),
    ("P", "3a2c17b035d45a4ecfd4bd99214aa84f786886daa958e2f2e8f2381a4eaab051"),
    ("Q", "6285cbc0a8d52cf6d83ecbac31dd68edcfbaf15489c66acaf1bb686b3c3e3ba8"),
    ("H", "6285cbc0a8d52cf6d83ecbac31dd68edcfbaf15489c66acaf1bb686b3c3e3ba8"),
])
def test_legacy_full_result_digest(profile, digest):
    data = bars()
    data[2] = [2*BAR, 100., 103., 99., 102.]
    result, _ = run(data, profile=profile)
    assert hashlib.sha256(json.dumps(result, sort_keys=True, separators=(",", ":")).encode()).hexdigest() == digest


def test_legacy_joint_h_second_target_funding_digest():
    data = bars(12)
    for i in range(2, 12):
        data[i] = [i*BAR, 104., 104., 104., 104.]
    result, _ = run(data, profile="H", signals=[signal(), signal(lane="C")], funding=[(3*BAR, .001)])
    assert hashlib.sha256(json.dumps(result, sort_keys=True, separators=(",", ":")).encode()).hexdigest() == "dde9953dc6bb7b7a2e3420f4aaa72066f084e6df05fd625bb2651b66aa9cf683"


@pytest.mark.parametrize("profile", ["K", "U"])
@pytest.mark.parametrize("direction", [-1, 1])
def test_no_ratchet_before_tp1(profile, direction):
    result, events = run(profile=profile, signals=[signal(direction)])
    assert not [e for e in events if e["kind"] == "stop_requested"]
    p = result["final_positions"]["S"]
    assert p["stop"] == p["first_native_stop"]
    assert [t["lots"] for t in p["targets"]] == [10, 5]


@pytest.mark.parametrize("profile", ["K", "U"])
def test_partial_entry_cannot_arm_ratchet_from_unfrozen_extreme(profile):
    data = bars(4)
    for i in range(2, 4):
        data[i] = [i*BAR, 101.5, 101.5, 101.5, 101.5]
    result, events = run(data, profile=profile, partial=True,
                         contexts={2*BAR: {"S": cx(trail_long=100.8)}})
    assert len(result["entry_fills"]) == 3
    assert not any(e["kind"] == "stop_requested" for e in events)
    assert not result["final_positions"]["S"]["frozen"]


@pytest.mark.parametrize("profile", ["K", "U"])
def test_partially_filled_tp1_quota_cannot_arm(profile):
    # Deliberately extreme synthetic funding causes a cap reduction that shares
    # this bar's exit capacity: only 8 of the original 10 TP1 lots can fill.
    data = bars(9)
    data[5] = [5*BAR, 100., 102., 100., 100.]
    result, events = run(data, profile=profile, partial=True, lanes=("S",),
                         initial_cash=2.5, funding=[(5*BAR, .3)],
                         contexts={5*BAR: {"S": cx(trail_long=100.8)}})
    p = result["final_positions"]["S"]
    assert p["targets"][0]["filled"] == 8 < p["targets"][0]["lots"] == 10
    assert p["stop"] == p["first_native_stop"]
    assert not any(e["kind"] == "stop_requested" for e in events)


@pytest.mark.parametrize("profile", ["K", "U"])
def test_tp1_arms_be_with_existing_latency(profile):
    data = bars(6)
    for i in range(2, 6):
        data[i] = [i*BAR, 102., 102., 102., 102.]
    _, events = run(data, profile=profile, contexts={2*BAR: {"S": cx(trail_long=100.8)}})
    req = next(e for e in events if e["kind"] == "stop_requested")
    amended = next(e for e in events if e["kind"] == "stop_amended")
    assert req["ts"] == 2*BAR
    assert amended["ts"] == req["eligible_at"] == 3*BAR
    assert req["stop"] >= 100.05*(1+.0016)
    if profile == "U":
        assert req["stop"] == 100.8


def test_u_missing_trail_retains_native_and_reports():
    data = bars(5)
    for i in range(2, 5):
        data[i] = [i*BAR, 102., 102., 102., 102.]
    result, events = run(data, profile="U")
    assert any(e["kind"] == "policy_error" for e in events)
    assert result["counters"]["policy_errors"] > 0
    assert result["final_positions"]["S"]["stop"] == 98.05


@pytest.mark.parametrize("direction", [-1, 1])
def test_u_anchor_monotone_for_both_directions(direction):
    data = bars(6)
    price = 102. if direction == 1 else 98.
    for i in range(2, 6):
        data[i] = [i*BAR, price, price, price, price]
    side = "long" if direction == 1 else "short"
    anchor = 100.8 if direction == 1 else 99.2
    result, events = run(data, profile="U", signals=[signal(direction)], contexts={
        2*BAR: {"S": cx(**{"trail_"+side: anchor})},
        3*BAR: {"S": cx(**{"trail_"+side: 100.-direction})}})
    assert result["final_positions"]["S"]["stop"] == anchor
    assert len([e for e in events if e["kind"] == "stop_requested"]) == 1


@pytest.mark.parametrize("profile", ["K", "U"])
def test_new_profiles_reject_nonpositive_far_short_target(profile):
    result, events = run(profile=profile, signals=[signal(-1, distance=80.)])
    assert not result["entry_fills"]
    assert any(e["kind"] == "entry_rejected_geometry" for e in events)


@pytest.mark.parametrize("phase_at,expected", [(0, False), (BAR, False), (2*BAR, True)])
def test_phase_exit_not_older_than_entry(phase_at, expected):
    _, events = run(profile="U", contexts={2*BAR: {"S": cx(phase_exit_long=True, phase_at=phase_at)}})
    fills = exits(events, "phase_exit")
    assert bool(fills) is expected
    if expected:
        assert fills[0]["ts"] == 3*BAR


@pytest.mark.parametrize("profile", ["F", "H", "K"])
def test_phase_exit_u_only(profile):
    _, events = run(profile=profile, contexts={2*BAR: {"S": cx(phase_exit_long=True, phase_at=2*BAR)}})
    assert not exits(events, "phase_exit")


@pytest.mark.parametrize("partial,invalid_at,expected_lots", [(False, BAR, 0), (True, 2*BAR, 8)])
def test_context_cancels_tail_before_fill_and_freezes_partial(partial, invalid_at, expected_lots):
    result, events = run(profile="U", partial=partial,
                         signals=[signal(cancel_if_context_invalid=True)],
                         contexts={invalid_at: {"S": cx(allow_entry_long=False)}})
    assert sum(f["lots"] for f in result["entry_fills"]) == expected_lots
    assert any(e["kind"] == "entry_cancelled" and e["reason"] == "context_invalid" for e in events)
    if expected_lots:
        p = result["final_positions"]["S"]
        assert p["frozen"] and p["initial_lots"] == expected_lots
        assert p["stop"] == p["first_native_stop"]
        assert [t["lots"] for t in p["targets"]] == [4, 2]


@pytest.mark.parametrize("allocation", ["fixed", "flex"])
@pytest.mark.parametrize("share", [.25, .5, 1.])
@pytest.mark.parametrize("partial", [False, True])
def test_frozen_share_reservation_and_postcost_funding_drift(allocation, share, partial):
    data = bars(12)
    for i in range(2, 12):
        data[i] = [i*BAR, 100.1, 100.1, 100.1, 100.1]
    events = []
    s = signal(lane="C", risk_share=share)
    s["strong"] = False
    result = run_adaptive(data, [(2*BAR, .001)], [s], {},
                          AdaptiveConfig(0, 12*BAR, lanes=("C",), allocation=allocation, partial=partial), events.append)
    reserved = next(e for e in events if e["kind"] == "entry_reserved")
    assert reserved["allocation_share"] == share
    assert reserved["gross_budget"] == 10000*share
    assert reserved["heat_budget"] == 200*share
    assert result["metrics"]["identity_error"] < 1e-6
    assert result["metrics"]["max_heat"] <= .02*share+1e-4


def test_lane_profiles_independent_and_explicit_legacy_equivalent():
    result, _ = run(profile="F", lane_profiles=(("S", "F"), ("C", "U")), signals=[signal(), signal(lane="C")])
    assert result["final_positions"]["S"]["targets"] == []
    assert len(result["final_positions"]["C"]["targets"]) == 2
    baseline, _ = run(profile="H")
    explicit, _ = run(profile="F", lane_profiles=(("S", "H"), ("C", "H")))
    assert baseline == explicit


@pytest.mark.parametrize("mapping", [[("S", "F")], (("S", "F"),), (("S", "F"), ("S", "U")), (("S", "X"), ("C", "U"))])
def test_invalid_lane_profiles(mapping):
    with pytest.raises(ValueError):
        AdaptiveConfig(0, BAR, lane_profiles=mapping)


@pytest.mark.parametrize("share", [True, 0, -1, 1.01, float("nan"), float("inf"), "0.5"])
def test_invalid_risk_share(share):
    with pytest.raises(ValueError):
        run(signals=[signal(risk_share=share)])


@pytest.mark.parametrize("extra", [dict(trail_long=0), dict(trail_short=True), dict(allow_entry_long=1),
                                  dict(phase_exit_long=True), dict(phase_at=True), dict(phase_at=1),
                                  dict(phase_at=3*BAR), dict(unknown=True)])
def test_invalid_context_extensions(extra):
    with pytest.raises(ValueError):
        run(contexts={2*BAR: {"S": cx(**extra)}})


@pytest.mark.parametrize("direction", [-1, 1])
@pytest.mark.parametrize("timing", [BAR, 6*BAR])
@pytest.mark.parametrize("validity", [False, True, None])
def test_failed_forming_confirmation_only_explicit_false(direction, timing, validity):
    side = "long" if direction == 1 else "short"
    observation = 2*timing
    fields = {"phase_at": observation}
    if validity is not None:
        fields["phase_valid_"+side] = validity
    _, events = run(bars(25), profile="U", timing_ms=timing,
                    signals=[signal(direction)], contexts={observation: {"S": cx(**fields)}})
    fills = exits(events, "phase_exit")
    assert bool(fills) is (validity is False)
    if fills:
        assert fills[0]["ts"] == observation+timing


@pytest.mark.parametrize("direction", [-1, 1])
@pytest.mark.parametrize("timing", [BAR, 6*BAR])
@pytest.mark.parametrize("phase_offset", [0, 1])
def test_invalid_confirmation_stale_or_equal_is_ignored(direction, timing, phase_offset):
    # Entry is at timing. Even a newly delivered context must retain its own
    # original closed-observation timestamp rather than pretending it is fresh.
    _, events = run(bars(25), profile="U", timing_ms=timing,
                    signals=[signal(direction)], contexts={2*timing: {"S": cx(
                        phase_at=phase_offset*timing, **{"phase_valid_long" if direction == 1 else "phase_valid_short": False})}})
    assert not exits(events, "phase_exit")


@pytest.mark.parametrize("profile", ["F", "H", "K"])
def test_failed_confirmation_does_not_change_legacy_or_k(profile):
    _, events = run(profile=profile, contexts={2*BAR: {"S": cx(
        phase_at=2*BAR, phase_valid_long=False, phase_valid_short=False)}})
    assert not exits(events, "phase_exit")


@pytest.mark.parametrize("direction", [-1, 1])
def test_neutral_confirmation_cancels_partial_tail_without_removing_sl(direction):
    data = bars(6)
    # Both validity sides False represents a neutral closed phase. The native
    # SL can still execute before the delayed failed-confirmation market exit.
    adverse = 95. if direction == 1 else 105.
    data[2] = [2*BAR, 100., max(100., adverse), min(100., adverse), 100.]
    result, events = run(data, profile="U", partial=True,
        signals=[signal(direction, cancel_if_context_invalid=True)],
        contexts={2*BAR: {"S": cx(phase_at=2*BAR, phase_valid_long=False,
                                  phase_valid_short=False, allow_entry_long=False,
                                  allow_entry_short=False)}})
    assert len(result["entry_fills"]) == 1
    assert result["entry_fills"][0]["lots"] == 8
    assert any(e["kind"] == "entry_cancelled" for e in events)
    assert exits(events, "stop")
    assert sum(e["lots"] for e in exits(events)) == 8


@pytest.mark.parametrize("fields", [dict(phase_valid_long=0, phase_at=BAR),
                                   dict(phase_valid_short=None, phase_at=BAR),
                                   dict(phase_valid_long=False)])
def test_invalid_phase_validity_schema(fields):
    with pytest.raises(ValueError):
        run(contexts={2*BAR: {"S": cx(**fields)}})
