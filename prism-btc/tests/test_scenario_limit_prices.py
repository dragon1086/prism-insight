"""New-proposal-only deterministic execution pricing; no exchange/network calls."""
from copy import deepcopy

import pytest

from core.llm_scenario import ScenarioValidationError
from core.scenario_limit_prices import POLICY_VERSION, observed_quotes, validate_execution_prices
from tests.test_llm_scenario import sample, active


def context(c):
    c.update(execution_price_policy={"version": POLICY_VERSION}, price_tick=.1,
             limit_price_quotes={"bid": 100100, "ask": 100110, "captured_at": 999})
    return c


@pytest.mark.parametrize("side,entry,tp", [("LONG",100007.3,101992.7),
                                           ("SHORT",99992.7,98007.3)])
def test_signs_and_unchanged_fields(side, entry, tp):
    p,c=sample(); context(c)
    if side=="SHORT":
        p.update(side=side,hard_stop=101000)
        p["take_profits"][0]["price"]=98000
        c["limit_price_quotes"].update(bid=99890,ask=99900)
    before=deepcopy((p,c)); out=validate_execution_prices(p,c)
    assert out["entries"][0]["price"]==entry
    assert out["take_profits"][0]["price"]==tp
    for key in ("hard_stop","partial_stops","chase","confidence","leverage"):
        assert out[key]==p[key]
    assert (p,c)==before


@pytest.mark.parametrize("tick,offset",[(.1,7.3),(.5,7.5),(1,7),(10,0),(20,0)])
def test_tick_alignment(tick,offset):
    p,c=sample(); context(c); c["price_tick"]=tick
    assert validate_execution_prices(p,c)["entries"][0]["price"]==100000+offset


def test_no_policy_and_missing_quotes_and_nonround():
    p,c=sample()
    assert "execution_pricing" not in validate_execution_prices(p,c)
    context(c); del c["limit_price_quotes"]
    assert validate_execution_prices(p,c)["entries"]==p["entries"]
    context(c); p["entries"][0]["price"]=100003.2
    assert validate_execution_prices(p,c)["entries"]==p["entries"]


def test_raw_invalid_not_repaired_or_host_metadata_accepted():
    p,c=sample(); context(c); p["execution_pricing"]={}
    with pytest.raises(ScenarioValidationError): validate_execution_prices(p,c)


def test_passive_to_marketable_skips_but_marketable_allowed():
    p,c=sample(); context(c); c["limit_price_quotes"].update(bid=100001,ask=100005)
    assert validate_execution_prices(p,c)["entries"]==p["entries"]
    c["limit_price_quotes"].update(bid=99990,ask=100000)
    assert validate_execution_prices(p,c)["entries"][0]["price"]==100007.3


def test_same_live_tp_price_preserved_even_renamed_after_partial_fill():
    p,c=active(); context(c)
    c["target_status"]=[dict(kind="tp",status="LIVE",price="102000",logical_target_id="old",remaining_quantity=".01")]
    p["take_profits"][0]["id"]="new"
    p["hard_stop"]=99500
    out=validate_execution_prices(p,c)
    assert out["take_profits"]==p["take_profits"]
    assert out["hard_stop"]==99500


def test_new_price_risk_rejected_without_losing_valid_original():
    p,c=sample(); context(c); p["confidence"]=1
    p["entries"][0]["quantity"]=200/1219
    out=validate_execution_prices(p,c)
    assert out["entries"]==p["entries"]
    assert out["risk"]["total_risk"]==pytest.approx(200)


def test_positive_cost_edge_cannot_be_consumed():
    p,c=sample(); context(c)
    p["take_profits"][0]["price"]=100230
    original=deepcopy(p)
    out=validate_execution_prices(p,c)
    assert out["entries"][0]["price"]==100007.3
    assert out["take_profits"][0]["price"]==100230
    assert out["execution_pricing"]["rows"][1]["reason"]=="cost_edge_or_basis"
    assert p==original


def test_wait_unchanged_and_adjust_unknown_basis_does_not_reprice_tp():
    p,c=sample(); context(c); p.update(action="WAIT",entries=[],take_profits=[])
    for key in ("side", "hard_stop", "chase"):
        del p[key]
    assert "execution_pricing" not in validate_execution_prices(p,c)
    p,c=active(); context(c); c["positions"]=[]
    assert validate_execution_prices(p,c)["take_profits"]==p["take_profits"]


@pytest.mark.parametrize("quote",[None, {}, {"bid":100100,"ask":100110,"captured_at":989},
    {"bid":100100,"ask":100110,"captured_at":1001},
    {"bid":100110,"ask":100100,"captured_at":999}])
def test_missing_stale_or_crossed_quotes_retain_raw_without_inventing_depth(quote):
    p,c=sample(); context(c); c["limit_price_quotes"]=quote
    out=validate_execution_prices(p,c)
    assert out["entries"]==p["entries"] and out["take_profits"]==p["take_profits"]
    assert out["execution_pricing"]["applied"] is False


@pytest.mark.parametrize("tick",[None,0,-1,float("nan"),True])
def test_invalid_tick_cannot_block_protection(tick):
    p,c=active(); context(c); c["price_tick"]=tick; p["hard_stop"]=99500
    out=validate_execution_prices(p,c)
    assert out["hard_stop"]==99500 and out["take_profits"]==p["take_profits"]


def test_reproposed_nonround_price_is_not_offset_twice():
    p,c=sample(); context(c)
    first=validate_execution_prices(p,c)
    wire={key:first[key] for key in p}
    second=validate_execution_prices(wire,c)
    assert second["entries"]==first["entries"]
    assert second["take_profits"]==first["take_profits"]
    assert not second["execution_pricing"]["applied"]


def test_minimum_notional_prevents_short_entry_price_reduction():
    p,c=sample(); context(c); p.update(side="SHORT",hard_stop=101000)
    p["take_profits"][0]["price"]=98000
    c["limit_price_quotes"].update(bid=99890,ask=99900)
    c["minimum_notional"]=5000
    out=validate_execution_prices(p,c)
    assert out["entries"]==p["entries"]
    assert out["execution_pricing"]["rows"][0]["reason"]=="minimum_notional"


def test_tp_crossing_mark_skips_row_but_preserves_stop_improvement():
    p,c=active(); context(c); c["mark_price"]=101995
    p["hard_stop"]=99500
    out=validate_execution_prices(p,c)
    assert out["take_profits"]==p["take_profits"] and out["hard_stop"]==99500


def test_initial_risk_above_limit_still_rejected_before_buffer():
    p,c=sample(); context(c); p["entries"][0]["quantity"]=.3
    with pytest.raises(ScenarioValidationError,match="risk budget exceeded"):
        validate_execution_prices(p,c)


def test_observed_ticker_quotes_are_optional_and_do_not_synthesize_depth():
    assert observed_quotes({"markPrice":"100000"},999) is None
    assert observed_quotes({"bid1Price":"100000","ask1Price":"100000"},999) is None
    assert observed_quotes({"bid1Price":"99999.9","ask1Price":"100000"},999)=={
        "bid":99999.9,"ask":100000.,"captured_at":999}


def test_float_and_string_live_tp_prices_are_equal():
    p,c=active(); context(c)
    p["take_profits"][0]["price"]=102000.0
    c["target_status"]=[dict(kind="tp",status="LIVE",price="102000")]
    assert validate_execution_prices(p,c)["take_profits"]==p["take_profits"]


@pytest.mark.parametrize("targets",[None,[{}],[dict(kind="tp",status="LIVE",price="oops")]])
def test_malformed_optional_target_data_does_not_block_sl(targets):
    p,c=active(); context(c); c["target_status"]=targets; p["hard_stop"]=99500
    out=validate_execution_prices(p,c)
    assert out["hard_stop"]==99500
    if targets != [{}]:
        assert out["take_profits"]==p["take_profits"]


def test_optional_quote_overflow_and_unknown_ticker_are_absent():
    assert observed_quotes(None,999) is None
    assert observed_quotes({"bid1Price":"1e999","ask1Price":"2e999"},999) is None


def test_short_tp_wrong_direction_after_offset_preserves_stop_improvement():
    p,c=active(); context(c)
    p.update(side="SHORT",hard_stop=100500)
    p["take_profits"][0]["price"]=98000
    c.update(side="SHORT",previous_hard_stop=101000,mark_price=98005)
    c["limit_price_quotes"].update(bid=98004,ask=98006)
    # Keep the original TP passive: crossing spread independently prevents it.
    out=validate_execution_prices(p,c)
    assert out["take_profits"]==p["take_profits"]
    assert out["hard_stop"]==100500
    assert out["execution_pricing"]["rows"][0]["reason"]=="would_cross_spread"
