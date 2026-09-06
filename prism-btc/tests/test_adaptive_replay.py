"""Synthetic chronological ledger tests; never read historical market data."""
import pytest

from backtest.adaptive_replay import AdaptiveConfig, BAR, run_adaptive


def bars(n=12, price=100.):
    return [[i*BAR, price, price, price, price] for i in range(n)]


def signal(direction=1, lane="S", at=0, distance=2., **kwargs):
    return dict(signal_id=f"{lane}:{at}:{direction}", lane=lane, available_at=at,
                direction=direction, reference_price=100., stop_distance=distance,
                strong=True, max_hold_ms=None, **kwargs)


def run(data=None, signals=None, funding=(), contexts=None, **kwargs):
    data = bars() if data is None else data
    events = []
    config = AdaptiveConfig(start_ms=int(data[0][0]), end_ms=int(data[-1][0])+BAR,
                            fixed_lots=20, **kwargs)
    result = run_adaptive(data, funding, [signal()] if signals is None else signals,
                          contexts or {}, config, events.append)
    return result, events


def exits(events, reason=None):
    return [e for e in events if e["kind"] == "fill" and e["action"] == "exit"
            and (reason is None or e["reason"] == reason)]


def test_cash_and_deterministic_hashes():
    result, _ = run(signals=[])
    other, _ = run(signals=[])
    assert result["metrics"]["final_nav"] == 10_000
    assert result["hashes"] == other["hashes"]
    assert result["daily_nav"] == [[12*BAR, 10_000]]


@pytest.mark.parametrize("direction", [-1, 1])
def test_entry_fee_funding_and_unrealized_identity(direction):
    result, events = run(signals=[signal(direction)], funding=[(2*BAR, .001)])
    p = result["final_positions"]["S"]
    expected_fee = .02*100*(1+direction*.0005)*.00055
    expected_funding = .02*direction*100*.001
    expected_unrealized = .02*direction*(100-100*(1+direction*.0005))
    assert result["metrics"]["fees"] == pytest.approx(expected_fee)
    assert result["metrics"]["funding"] == pytest.approx(expected_funding)
    assert result["metrics"]["final_nav"] == pytest.approx(10000-expected_fee-expected_funding+expected_unrealized)
    assert p["stop"] == pytest.approx(p["entry"]-direction*2)
    assert result["entry_fills"][0]["ts"] == BAR


@pytest.mark.parametrize("direction", [-1, 1])
def test_non_gap_stop_crossing_not_node_overshoot(direction):
    data = bars()
    data[2] = [2*BAR, 100., 110., 90., 100.]
    result, events = run(data, signals=[signal(direction)])
    stop = 100*(1+direction*.0005)-direction*2
    fill = exits(events, "stop")[0]
    assert fill["price"] == pytest.approx(stop*(1-direction*.0005))
    assert result["metrics"]["completed_campaigns"] == 1
    assert result["metrics"]["identity_error"] < 1e-8


@pytest.mark.parametrize("direction", [-1, 1])
def test_gap_stop_fills_at_adverse_open(direction):
    data = bars()
    gap = 90. if direction == 1 else 110.
    data[2] = [2*BAR, gap, gap, gap, gap]
    _, events = run(data, signals=[signal(direction)])
    assert exits(events, "stop")[0]["price"] == pytest.approx(gap*(1-direction*.0005))


def test_native_stop_protects_first_partial_and_cancels_remaining():
    data = bars()
    data[1] = [BAR, 100., 100., 95., 100.]
    result, events = run(data, partial=True)
    assert result["entry_fills"][0]["lots"] == 8
    assert len(result["entry_fills"]) == 1
    assert exits(events, "stop")[0]["lots"] == 4
    assert sum(e["lots"] for e in exits(events)) == 8
    assert result["final_positions"] == {}


def test_partial_exit_capacity_shared_across_tp_and_stop():
    data = bars(20)
    # Entry finishes over bars 1,2,3,4. On bar 5 TP1 uses all exit capacity.
    data[5] = [5*BAR, 100., 102., 95., 100.]
    result, events = run(data, profile="H", partial=True)
    same_bar = [e for e in exits(events) if 5*BAR <= e["ts"] < 6*BAR]
    assert sum(e["lots"] for e in same_bar) <= 10
    assert not any(e["reason"] == "tp2" for e in exits(events))
    assert sum(e["lots"] for e in exits(events)) == 20
    assert result["final_positions"] == {}


@pytest.mark.parametrize("profile", ["Q", "H"])
def test_tp_touch_is_not_fill_and_trade_through_exact_quota(profile):
    data = bars()
    target = 100.05+1.
    data[2] = [2*BAR, 100., target, 100., 100.]
    data[3] = [3*BAR, 100., target+.01, 100., 100.]
    _, events = run(data, profile=profile)
    fills = exits(events, "tp1")
    assert len(fills) == 1 and fills[0]["lots"] == 10
    assert fills[0]["ts"] >= 3*BAR
    assert fills[0]["price"] == target
    assert fills[0]["fee"] == pytest.approx(10*.001*target*.0002)


@pytest.mark.parametrize("direction", [-1, 1])
def test_h_two_targets_leave_quarter_runner(direction):
    data = bars(4)
    extreme = 104. if direction == 1 else 96.
    data[2] = [2*BAR, 100., max(100, extreme), min(100, extreme), extreme]
    data[3] = [3*BAR, extreme, extreme, extreme, extreme]
    result, events = run(data, signals=[signal(direction)], profile="H")
    assert [e["lots"] for e in exits(events)] == [10, 5]
    assert result["final_positions"]["S"]["lots"] == 5


def test_trail_amendment_future_open_and_already_crossed_market():
    data = bars(6)
    data[1] = [BAR, 100., 105., 100., 104.]
    data[2] = [2*BAR, 104., 104., 103., 103.]
    data[3] = [3*BAR, 101., 101., 101., 101.]
    _, events = run(data, profile="P")
    requested = [e for e in events if e["kind"] == "stop_requested"][0]
    amended = [e for e in events if e["kind"] == "stop_amended"][0]
    assert requested["ts"] == 2*BAR and amended["ts"] == 3*BAR
    assert exits(events)[0]["reason"] == "amended_stop_crossed"
    assert exits(events)[0]["price"] == pytest.approx(101*.9995)


def test_slow_pending_amend_is_not_postponed():
    data = bars(20)
    for i in range(7, 20):
        data[i] = [i*BAR, 103., 104., 103., 103.]
    _, events = run(data, profile="P", timing_ms=6*BAR)
    requests = [e for e in events if e["kind"] == "stop_requested"]
    amendments = [e for e in events if e["kind"] == "stop_amended"]
    assert requests[0]["ts"] == 12*BAR
    assert amendments[0]["ts"] == 18*BAR


def test_rule_exit_delay_preserved_and_opposite_signal_rejected():
    cx = {2*BAR: {"S": dict(exit_long=True, exit_short=False, trend_long=False, trend_short=False)}}
    result, events = run(signals=[signal(), signal(-1, "C", 2*BAR)], contexts=cx)
    assert exits(events)[0]["ts"] == 3*BAR
    assert result["counters"]["rejected_opposite"] == 1


def test_priority_s_before_c_and_pending_blocks_opposite():
    result, _ = run(signals=[signal(-1, "C"), signal(1, "S")])
    assert [x["lane"] for x in result["entry_fills"]] == ["S"]
    assert result["counters"]["rejected_opposite"] == 1


def test_deadline_and_latched_partial_market_exit():
    s = signal()
    s["max_hold_ms"] = 2*BAR
    result, events = run(signals=[s], partial=True)
    assert len(result["entry_fills"]) == 2
    assert exits(events)[0]["ts"] == 4*BAR
    assert sum(e["lots"] for e in exits(events)) == sum(e["lots"] for e in result["entry_fills"])


def test_partial_ttl_starts_after_slow_eligibility():
    data = bars(15)
    config = AdaptiveConfig(0, 15*BAR, partial=True, timing_ms=6*BAR,
                            fixed_lots=4000, initial_cash=1_000_000)
    events = []
    result = run_adaptive(data, (), [signal()], {}, config, events.append)
    assert result["entry_fills"][0]["ts"] == 6*BAR
    assert max(e["ts"] for e in result["entry_fills"]) == 11*BAR
    assert result["counters"]["expired"] == 1
    frozen = [e for e in events if e["kind"] == "entry_frozen"][0]
    assert frozen["ts"] == 12*BAR and frozen["lots"] % 4 == 0


def test_post_cost_caps_include_pending_and_fixed_lots_never_silently_resize():
    data = bars()
    config = AdaptiveConfig(0, 12*BAR, initial_cash=100, fixed_lots=1000)
    result = run_adaptive(data, (), [signal()], {}, config)
    assert result["entry_fills"] == []
    assert result["counters"]["rejected_budget"] == 1
    config = AdaptiveConfig(0, 12*BAR, initial_cash=100)
    result = run_adaptive(data, (), [signal(), signal(lane="C")], {}, config)
    assert result["metrics"]["max_gross"] <= 1
    assert result["metrics"]["max_heat"] <= .02


def test_resource_check_on_empty_period():
    seen = []
    run(bars(600), signals=[], resource_check=seen.append)
    assert len(seen) == 3


def test_small_heat_breach_reduces_one_lot_not_whole_campaign():
    data = bars(5)
    config = AdaptiveConfig(0, 5*BAR, lanes=("S",), profile="H",
                            initial_cash=2.2, fixed_lots=20)
    events = []
    result = run_adaptive(data, [(2*BAR, .05)], [signal()], {}, config, events.append)
    forced = exits(events, "risk_cap")
    assert [e["lots"] for e in forced] == [1]
    p = result["final_positions"]["S"]
    assert p["lots"] == 19 and p["exit_due"] is None
    assert sum(x["lots"]-x["filled"]-x["retired"] for x in p["targets"]) == 15


def test_risk_cap_retires_runner_then_farthest_target():
    config = AdaptiveConfig(0, 4*BAR, lanes=("S",), profile="H",
                            initial_cash=2.2, fixed_lots=20)
    result = run_adaptive(bars(4), [(2*BAR, .7)], [signal()], {}, config)
    p = result["final_positions"]["S"]
    assert p["lots"] < 15
    assert p["targets"][1]["retired"] > 0
    assert sum(x["lots"]-x["filled"]-x["retired"] for x in p["targets"]) <= p["lots"]


@pytest.mark.parametrize("direction", [-1, 1])
def test_path_changes_tp_stop_order_on_shared_bar(direction):
    data = bars(4)
    data[2] = [2*BAR, 100., 105., 95., 100.]
    favorable_path = "OHLC" if direction == 1 else "OLHC"
    adverse_path = "OLHC" if direction == 1 else "OHLC"
    _, favorable = run(data, signals=[signal(direction)], profile="H", path=favorable_path)
    _, adverse = run(data, signals=[signal(direction)], profile="H", path=adverse_path)
    assert [e["reason"] for e in exits(favorable)] == ["tp1", "tp2", "stop"]
    assert [e["reason"] for e in exits(adverse)] == ["stop"]
    assert sum(e["lots"] for e in exits(favorable)) == 20


def test_old_stop_survives_pending_amendment():
    data = bars(15)
    data[7] = [7*BAR, 100., 105., 100., 104.]
    for i in range(8, 13):
        data[i] = [i*BAR, 104., 104., 104., 104.]
    data[13] = [13*BAR, 104., 104., 95., 100.]
    _, events = run(data, profile="P", timing_ms=6*BAR)
    request = [e for e in events if e["kind"] == "stop_requested"][0]
    assert request["eligible_at"] == 18*BAR
    stop = exits(events, "stop")[0]
    assert stop["ts"] < request["eligible_at"]
    assert stop["price"] == pytest.approx((100.05-2)*.9995)


def test_partial_ttl_freezes_actual_targets_not_original_order_size():
    config = AdaptiveConfig(0, 15*BAR, lanes=("S",), profile="H", partial=True,
                            timing_ms=6*BAR, fixed_lots=4000, initial_cash=1_000_000)
    result = run_adaptive(bars(15), (), [signal()], {}, config)
    p = result["final_positions"]["S"]
    assert p["initial_lots"] < 4000
    assert p["targets"][0]["lots"] == p["initial_lots"]//2
    assert p["targets"][1]["lots"] == p["initial_lots"]//4
    assert p["initial_risk"] == pytest.approx(p["initial_lots"]*.001*2)


def test_flex_weak_candidate_uses_half_available_budget():
    results = []
    for strong in (False, True):
        s = signal(lane="C")
        s["strong"] = strong
        config = AdaptiveConfig(0, 5*BAR, allocation="flex")
        results.append(run_adaptive(bars(5), (), [s], {}, config))
    weak, strong = [r["entry_fills"][0]["lots"] for r in results]
    assert 1.99 <= strong/weak <= 2.01


def test_two_lanes_one_cash_and_entry_fees_in_nav_limit():
    config = AdaptiveConfig(0, 5*BAR)
    result = run_adaptive(bars(5), (), [signal(), signal(lane="C")], {}, config)
    assert {e["lane"] for e in result["entry_fills"]} == {"S", "C"}
    assert result["metrics"]["max_heat"] <= .02+1e-12
    assert result["metrics"]["max_gross"] <= 1+1e-12
    assert result["metrics"]["identity_error"] < 1e-8


def test_fixed_lane_half_allocation_rechecked_after_execution_price_gap():
    data = bars(5)
    for i in range(1, 5):
        data[i] = [i*BAR, 200., 200., 200., 200.]
    result = run_adaptive(data, (), [signal()], {}, AdaptiveConfig(0, 5*BAR))
    assert result["entry_fills"]
    assert result["metrics"]["max_gross"] <= .5+1e-12
    assert result["metrics"]["max_heat"] <= .01+1e-12


def test_partial_price_jump_preserves_native_stop_and_budgets_actual_distance():
    data = bars(8)
    for i in range(2, 8):
        data[i] = [i*BAR, 110., 110., 110., 110.]
    config = AdaptiveConfig(0, 8*BAR, lanes=("S",), partial=True, initial_cash=100.)
    events = []
    result = run_adaptive(data, (), [signal(distance=10.)], {}, config, events.append)
    entries = [e for e in events if e["kind"] == "fill" and e["action"] == "entry"]
    assert len(entries) >= 2
    assert all(e["stop"] == pytest.approx(90.05) for e in entries)
    assert entries[1]["price"] == pytest.approx(110.055)
    assert result["metrics"]["max_heat"] <= .02+1e-12
    p = result["final_positions"]["S"]
    assert p["initial_stop_risk_amount"] > p["initial_risk"]
    assert p["stop_distance"] == 10.
    assert any(e["kind"] == "reservation_reduced" for e in events)


@pytest.mark.parametrize("profile,direction", [("F", 1), ("H", -1)])
def test_adverse_entry_gap_invalid_native_or_target_geometry_is_rejected(profile, direction):
    data = bars(4)
    for i in range(1, 4):
        data[i] = [i*BAR, 50., 50., 50., 50.]
    result, _ = run(data, signals=[signal(direction, distance=90.)], profile=profile)
    assert result["entry_fills"] == []
    assert result["counters"]["rejected_geometry"] == 1


def test_funding_credit_nav_peak_sampled_before_adverse_open_gap():
    data = bars(4)
    data[2] = [2*BAR, 50., 50., 50., 50.]
    data[3] = [3*BAR, 50., 50., 50., 50.]
    result, _ = run(data, funding=[(2*BAR, -.1)])
    # 0.2 credit is first valued at prior close100, before the gap/stop loss.
    entry_fee = .02*100.05*.00055
    funded_peak = 10000-entry_fee-.001+.2
    expected_mdd = (funded_peak-result["metrics"]["final_nav"])/funded_peak
    assert result["metrics"]["mtm_mdd"] == pytest.approx(expected_mdd)


def test_invalid_targets_after_partial_average_are_suppressed_not_unprotected():
    data = bars(8)
    for i in range(2, 8):
        data[i] = [i*BAR, 50., 50., 50., 50.]
    result, events = run(data, signals=[signal(-1, distance=60.)], profile="H", partial=True)
    p = result["final_positions"]["S"]
    assert p["frozen"] and p["lots"] == 20 and p["targets"] == []
    assert p["stop"] > 0 and p["first_native_stop"] == pytest.approx(159.95)
    assert result["counters"]["suppressed_target_plans"] == 1
    assert any(e["kind"] == "target_plan_suppressed" for e in events)


@pytest.mark.parametrize("profile", ["F", "P", "Q", "H"])
def test_initial_entry_time_and_quantity_match_across_profiles(profile):
    result, _ = run(profile=profile)
    assert result["entry_fills"] == [dict(ts=BAR, lane="S", signal_id="S:0:1", lots=20, price=100.05)]


def test_cost_stress_doubles_fee_slippage_not_funding_rate():
    base, _ = run(funding=[(2*BAR, .001)])
    stress, _ = run(funding=[(2*BAR, .001)], cost_multiple=2)
    assert stress["metrics"]["funding"] == base["metrics"]["funding"]
    assert stress["metrics"]["slippage"] == pytest.approx(2*base["metrics"]["slippage"])
    assert stress["metrics"]["fees"] > 2*base["metrics"]["fees"]  # adverse fill also changes notional


@pytest.mark.parametrize("direction", [-1, 1])
def test_h_background_can_tighten_but_never_loosen_existing_stop(direction):
    data = bars(9)
    favorable = 105. if direction == 1 else 95.
    data[2] = [2*BAR, 100., max(100., favorable), min(100., favorable), favorable]
    for i in range(3, 9):
        data[i] = [i*BAR, favorable, favorable, favorable, favorable]
    def cx(strong):
        return {"S": dict(exit_long=False, exit_short=False,
                          trend_long=strong and direction == 1,
                          trend_short=strong and direction == -1)}
    _, events = run(data, signals=[signal(direction)], profile="H",
                    contexts={0: cx(True), 5*BAR: cx(False), 7*BAR: cx(True)})
    stops = [e["stop"] for e in events if e["kind"] == "stop_amended"]
    assert len(stops) >= 2
    assert all(direction*(b-a) >= 0 for a, b in zip(stops, stops[1:]))
    assert stops[1] == pytest.approx(favorable-direction*2)


@pytest.mark.parametrize("kwargs", [dict(profile="X"), dict(path="XXX"),
                                    dict(timing_ms=1), dict(fixed_lots=3),
                                    dict(lanes=("C", "S")), dict(initial_cash=0)])
def test_invalid_policy(kwargs):
    with pytest.raises(ValueError):
        AdaptiveConfig(0, BAR, **kwargs)


def test_missing_bars_and_future_context_type_rejected():
    data = bars()
    data.pop(3)
    with pytest.raises(ValueError):
        run(data)
    with pytest.raises(ValueError):
        run(contexts={BAR: {"S": dict(exit_long=1, exit_short=False, trend_long=False, trend_short=False)}})


def test_duplicate_signals_and_funding_rejected():
    with pytest.raises(ValueError):
        run(signals=[signal(), signal()])
    with pytest.raises(ValueError):
        run(funding=[(BAR, .01), (BAR, .02)])


def test_insolvency_fails_closed():
    data = bars()
    data[2] = [2*BAR, 1_000_000., 1_000_000., 1_000_000., 1_000_000.]
    with pytest.raises(RuntimeError, match="insolvent"):
        run(data, signals=[signal(-1)])


@pytest.mark.parametrize("rate", [-1., 1., -100., 100.])
def test_implausible_funding_magnitude_rejected(rate):
    with pytest.raises(ValueError, match="funding rate magnitude"):
        run(funding=[(BAR, rate)])


@pytest.mark.parametrize("partial", [False, True])
def test_flex_weak_frozen_half_budget_survives_execution_price_doubling(partial):
    data = bars(10)
    for i in range(2 if partial else 1, 10):
        data[i] = [i*BAR, 200., 200., 200., 200.]
    s = signal(lane="C", distance=1.)
    s["strong"] = False
    events = []
    config = AdaptiveConfig(0, 10*BAR, allocation="flex", partial=partial)
    result = run_adaptive(data, (), [s], {}, config, events.append)
    reservation = next(e for e in events if e["kind"] == "entry_reserved")
    assert reservation["gross_budget"] == 5000.
    assert result["entry_fills"]
    # The partial first fill's price appreciation is market drift, not a new
    # admission. Inspect every new fill against its own current account NAV.
    fills = [e for e in events if e["kind"] == "fill" and e["action"] == "entry"]
    average = 0.
    lots = 0
    for event in fills:
        average = (average*lots+event["price"]*event["lots"])/(lots+event["lots"])
        lots += event["lots"]
        mark = data[event["ts"]//BAR][1]
        value = event["cash"]+lots*.001*(mark-average)
        assert lots*.001*mark <= min(5000., .5*value)+1e-7


def test_flex_weak_budget_does_not_grow_after_funding_credit():
    data = bars(12)
    s = signal(lane="C", distance=2.)
    s["strong"] = False
    events = []
    config = AdaptiveConfig(0, 12*BAR, allocation="flex", partial=True)
    result = run_adaptive(data, [(2*BAR, -.5)], [s], {}, config, events.append)
    assert result["metrics"]["final_nav"] > 10000.
    for event in events:
        if event["kind"] == "fill" and event["action"] == "entry":
            assert event["remaining_lots"]*.001*(2+100*(.00055+.0005)) <= 100.+1e-8


def test_flex_weak_partial_budget_tightens_after_funding_debit():
    s = signal(lane="C", distance=2.)
    s["strong"] = False
    events = []
    config = AdaptiveConfig(0, 12*BAR, allocation="flex", partial=True)
    run_adaptive(bars(12), [(2*BAR, .5)], [s], {}, config, events.append)
    fills = [e for e in events if e["kind"] == "fill" and e["action"] == "entry"]
    assert len(fills) >= 2
    for event in fills:
        lots = event["remaining_lots"]
        value = event["cash"]+lots*.001*(100.-100.05)
        heat = lots*.001*(2+100*(.00055+.0005))
        assert heat <= min(100., .01*value)+1e-8
