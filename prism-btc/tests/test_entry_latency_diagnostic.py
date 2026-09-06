"""Post-hoc entry-clock isolation: synthetic fixtures only, no market returns."""
import pytest

from backtest.adaptive_replay import AdaptiveConfig, BAR, run_adaptive
from tests.test_adaptive_replay import bars, signal, run, exits
from tests.test_transition_replay import cx


@pytest.mark.parametrize("profile", ["F", "P", "Q", "H", "K", "U"])
@pytest.mark.parametrize("timing", [BAR, 6*BAR])
def test_default_and_explicit_entry_clock_identical(profile, timing):
    baseline, baseline_events = run(profile=profile, timing_ms=timing)
    explicit, events = run(profile=profile, timing_ms=timing, entry_latency_ms=timing)
    assert baseline == explicit
    assert baseline_events == events


@pytest.mark.parametrize("invalid", [True, False, -BAR, 1, 2*BAR, 0., float("nan"), "0"])
def test_reject_invalid_entry_latency(invalid):
    with pytest.raises(ValueError, match="entry latency"):
        AdaptiveConfig(0, BAR, entry_latency_ms=invalid)


@pytest.mark.parametrize("direction", [-1, 1])
def test_zero_uses_signal_boundary_open_not_future_bar_prices(direction):
    plain = bars()
    future = bars()
    future[1] = [BAR, 100., 110., 90., 105.]
    kwargs = dict(entry_latency_ms=0, signals=[signal(direction, at=BAR)])
    first, _ = run(plain, **kwargs)
    changed, _ = run(future, **kwargs)
    assert first["entry_fills"] == changed["entry_fills"]
    assert first["entry_fills"][0]["ts"] == BAR
    assert first["entry_fills"][0]["price"] == 100*(1+direction*.0005)


@pytest.mark.parametrize("direction", [-1, 1])
def test_zero_first_partial_has_native_stop_same_bar(direction):
    data = bars()
    data[0] = [0, 100., 110., 90., 100.]
    result, events = run(data, entry_latency_ms=0, partial=True, signals=[signal(direction)])
    assert len(result["entry_fills"]) == 1
    assert result["entry_fills"][0]["ts"] == 0
    assert exits(events, "stop")[0]["ts"] < BAR
    assert sum(e["lots"] for e in exits(events)) == 8


def test_funding_before_zero_latency_new_entry():
    _, events = run(entry_latency_ms=0, signals=[signal(), signal(lane="C", at=BAR)],
                    funding=[(0, .001), (BAR, .001)])
    funding = [e for e in events if e["kind"] == "funding"]
    assert len(funding) == 1 and funding[0]["lane"] == "S"
    new_entry = next(e for e in events if e["kind"] == "fill" and
                     e["action"] == "entry" and e["lane"] == "C")
    assert events.index(funding[0]) < events.index(new_entry)


@pytest.mark.parametrize("latency", [0, BAR, 6*BAR])
def test_ttl_starts_at_entry_eligibility(latency):
    events = []
    result = run_adaptive(bars(20), [], [signal()], {},
        AdaptiveConfig(0, 20*BAR, lanes=("S",), partial=True, fixed_lots=1024,
                       entry_latency_ms=latency), events.append)
    freeze = next(e for e in events if e["kind"] == "entry_frozen")
    assert freeze["ts"] == latency+6*BAR
    assert all(f["ts"] < freeze["ts"] for f in result["entry_fills"])
    assert result["counters"]["expired"] == 1
    assert result["final_positions"]["S"]["initial_lots"] == 1008


@pytest.mark.parametrize("timing", [BAR, 6*BAR])
@pytest.mark.parametrize("reason", ["phase", "deadline", "rule"])
def test_zero_entry_does_not_accelerate_market_exit_clock(timing, reason):
    s = signal()
    contexts = {}
    if reason == "deadline":
        s["max_hold_ms"] = BAR
    elif reason == "phase":
        contexts = {BAR: {"S": cx(phase_at=BAR, phase_valid_long=False)}}
    else:
        value = cx()
        value["exit_long"] = True
        contexts = {BAR: {"S": value}}
    _, events = run(bars(20), signals=[s], contexts=contexts, profile="U",
                    timing_ms=timing, entry_latency_ms=0)
    assert exits(events)[0]["ts"] == 2*timing


@pytest.mark.parametrize("timing", [BAR, 6*BAR])
def test_zero_entry_does_not_accelerate_stop_amendment(timing):
    data = bars(20)
    for i in range(1, 20):
        data[i] = [i*BAR, 102., 102., 102., 102.]
    _, events = run(data, profile="U", timing_ms=timing, entry_latency_ms=0,
                    contexts={BAR: {"S": cx(trail_long=100.8)}})
    request = next(e for e in events if e["kind"] == "stop_requested")
    amend = next(e for e in events if e["kind"] == "stop_amended")
    assert request["ts"] == timing
    assert amend["ts"] == request["eligible_at"] == 2*timing


def test_zero_invalid_context_cancels_before_new_entry():
    result, events = run(entry_latency_ms=0, signals=[signal(cancel_if_context_invalid=True)],
                         contexts={0: {"S": cx(allow_entry_long=False)}})
    assert not result["entry_fills"]
    assert any(e["kind"] == "entry_cancelled" for e in events)


@pytest.mark.parametrize("allocation", ["fixed", "flex"])
@pytest.mark.parametrize("partial", [False, True])
def test_zero_postcost_shared_pending_limits(allocation, partial):
    result = run_adaptive(bars(), [(BAR, .001)],
        [signal(risk_share=.5), signal(lane="C", risk_share=.5)], {},
        AdaptiveConfig(0, 12*BAR, allocation=allocation, partial=partial, entry_latency_ms=0))
    assert {f["lane"] for f in result["entry_fills"]} == {"S", "C"}
    assert all(next(f for f in result["entry_fills"] if f["lane"] == lane)["ts"] == 0
               for lane in ("S", "C"))
    assert result["metrics"]["max_gross"] <= 1
    assert result["metrics"]["max_heat"] <= .02
    assert result["metrics"]["identity_error"] < 1e-6
