"""Original quote observation time never changes current validation authority."""
from copy import deepcopy
import json
import sqlite3
from types import SimpleNamespace

import pytest

from core.llm_scenario import ScenarioValidationError
from core.scenario_limit_prices import POLICY_VERSION, validate_execution_prices
from tests.test_llm_scenario import sample, active
from tests.test_scenario_review_guard import model_receipt_hash


def preview(payload, context, original_input_now=1000):
    from core.scenario_limit_prices import preview_execution_prices
    return preview_execution_prices(payload,context,original_input_now=original_input_now)


def pricing_context(context, *, quote_time=999, now=1020):
    context.update(now=now,execution_price_policy={"version":POLICY_VERSION},price_tick=.1,
        limit_price_quotes=dict(bid=100100,ask=100110,captured_at=quote_time))
    return context


def test_preview_uses_original_input_only_for_quote_age_live_remains_current():
    payload,ctx=sample()
    pricing_context(ctx)
    before=deepcopy((payload,ctx))
    planned=preview(payload,ctx)
    assert planned["entries"][0]["price"]==100007.3
    assert planned["take_profits"][0]["price"]==101992.7
    assert planned["execution_pricing"]["quotes"]["captured_at"]==999
    live=validate_execution_prices(payload,ctx)
    assert live["entries"][0]["price"]==100000
    assert live["take_profits"][0]["price"]==102000
    assert live["execution_pricing"]["applied"] is False
    assert (payload,ctx)==before


@pytest.mark.parametrize("quote_time", [989,1001,float("nan")])
def test_initial_stale_future_invalid_quote_is_not_revalidated(quote_time):
    payload,ctx=sample()
    pricing_context(ctx,quote_time=quote_time)
    result=preview(payload,ctx)
    assert result["execution_pricing"]["applied"] is False
    assert result["take_profits"][0]["price"]==102000


@pytest.mark.parametrize("origin", [1021,float("nan"),float("inf"),True])
def test_invalid_original_input_clock_rejected(origin):
    payload,ctx=sample()
    pricing_context(ctx)
    with pytest.raises(ValueError,match="preview"):
        preview(payload,ctx,origin)


@pytest.mark.parametrize("change", ["expiry","input_age","risk"])
def test_preview_does_not_extend_current_expiry_input_age_or_risk(change):
    payload,ctx=sample()
    pricing_context(ctx)
    if change=="expiry":
        payload["expires_at"]=1010
    elif change=="input_age":
        ctx["now"]=1120
    else:
        payload["entries"][0]["quantity"]=1
    with pytest.raises(ScenarioValidationError):
        preview(payload,ctx)


@pytest.mark.parametrize("action", ["ADJUST","EXIT"])
def test_holding_exit_and_noquote_keep_live_validation_and_no_double_pad(action):
    payload,ctx=active()
    pricing_context(ctx)
    if action=="EXIT":
        payload.update(action="EXIT",take_profits=[],partial_stops=[])
        for field in ("side","hard_stop","chase"):
            payload.pop(field)
    planned=preview(payload,ctx)
    current=validate_execution_prices(payload,dict(ctx,limit_price_quotes=dict(ctx["limit_price_quotes"],captured_at=1020)))
    assert planned==current or (planned["take_profits"]==current["take_profits"] and planned["risk"]==current["risk"])
    assert preview(payload,ctx)==planned
    ctx.pop("limit_price_quotes")
    assert preview(payload,ctx)==validate_execution_prices(payload,ctx)


def test_delayed_receipt_still_rejects_real_quote_drift_and_never_double_pads():
    from analysis.scenario_review_tape import record_review
    from live.scenario_calculated_review import research_review
    from live.scenario_preview import response_contract
    from live.scenario_review_guard import guard_review
    from tests.test_scenario_review_guard import context,wire
    ctx=context()
    ctx.update(execution_price_policy={"version":POLICY_VERSION},
               limit_price_quotes=dict(bid=83400,ask=83401,captured_at=1000))
    original=deepcopy(ctx)
    wall,calls=[1000.],[]
    def generate(**request):
        calls.append(request)
        if len(calls)==1:
            wall[0]+=20
            result=dict(wait_proposal=wire(ctx,"WAIT"),candidates=[wire(ctx,"OPEN")],
                no_candidate_reason="")
        else:
            wall[0]+=1
            candidate=json.loads(request["user_prompt"])["calculated_candidates"]["candidates"][0]
            result=dict(selection_token=f"{candidate['id']}:{model_receipt_hash(candidate)}",rationale="계산 검토")
        return SimpleNamespace(text=json.dumps(result))
    snapshot=dict(valid=True,as_of_ms=1000000)
    contract=response_contract(ctx)
    envelope=record_review(research_review,snapshot,ctx,contract,generate=generate,
        record=lambda event:None,clock=lambda:wall[0])
    assert envelope["outcome"]["status"]=="COMPLETE"
    fresh=dict(ctx,now=1021,account_captured_at=1021,
        limit_price_quotes=dict(bid=83400,ask=83401,captured_at=1021))
    checked=guard_review(snapshot,ctx,contract,envelope,fresh)
    assert checked["validated"]["entries"][0]["price"]==83417.3
    assert envelope["outcome"]["result"]["selected"]["raw_proposal"]["entries"][0]["price"]==83410
    fresh["limit_price_quotes"]=dict(bid=83411,ask=83412,captured_at=1021)
    with pytest.raises(ValueError,match="review_guard_effective_plan_changed"):
        guard_review(snapshot,ctx,contract,envelope,fresh)
    assert ctx==original


@pytest.mark.parametrize("fault", ["expired_candidate","model_deadline"])
def test_original_pricing_reference_cannot_extend_review_expiry_or_75_seconds(fault):
    from live.scenario_calculated_review import research_review
    from live.scenario_preview import response_contract
    from tests.test_scenario_review_guard import context,wire
    ctx=context()
    ctx.update(execution_price_policy={"version":POLICY_VERSION},
               limit_price_quotes=dict(bid=83400,ask=83401,captured_at=1000))
    wall,calls=[1000.],[]
    def generate(**request):
        calls.append(request)
        if len(calls)==1:
            wall[0]+=76 if fault=="model_deadline" else 20
            candidate=wire(ctx,"OPEN")
            if fault=="expired_candidate":
                candidate["expires_at"]=1010
            result=dict(wait_proposal=wire(ctx,"WAIT"),candidates=[candidate],
                no_candidate_reason="")
        else:
            bundle=json.loads(request["user_prompt"])["calculated_candidates"]
            assert bundle["candidates"][0]["eligible"] is False
            receipt=bundle["wait"]
            result=dict(selection_token=f"{receipt['id']}:{model_receipt_hash(receipt)}",rationale="만료 후보는 선택하지 않음")
        return SimpleNamespace(text=json.dumps(result))
    def run():
        return research_review(dict(valid=True,as_of_ms=1000000),ctx,response_contract(ctx),
            generate=generate,record=lambda event:None,clock=lambda:wall[0])
    if fault=="model_deadline":
        with pytest.raises(ValueError,match="research_deadline"):
            run()
        assert len(calls)==1
    else:
        result=run()
        assert result["selected"]["raw_proposal"]["action"]=="WAIT"
        assert result["candidates"]["candidates"][0]["rejection"]=="stale input or invalid expiry"


@pytest.mark.parametrize("side", ["LONG","SHORT"])
@pytest.mark.parametrize("draft_seconds", [2.,20.])
@pytest.mark.parametrize("round_target", [True,False])
def test_native_round_delayed_draft_selected_open_and_exact_frozen(tmp_path,side,draft_seconds,round_target):
    from analysis.scenario_replay import run_replay,network_boundary
    from analysis.scenario_dataset import digest
    from tests.test_scenario_replay import inputs
    from tests.test_scenario_calculated_replay import wire
    bundle,market=inputs(300000)
    for frame in (market.source,market.mark):
        frame.loc[:,["open","high","low","close"]]=100.
    bundle["data_hash"]=digest(market.manifest())
    wall=[0.]
    raw_tp=(120. if side=="LONG" else 80.)+(0 if round_target else .1)
    expected_tp=raw_tp+(-7.3 if side=="LONG" else 7.3) if round_target else raw_tp
    def generate(**request):
        assert request["retry_transport"] is False
        data=json.loads(request["user_prompt"])
        if "selection_token" in request["response_schema"]["properties"]:
            wall[0]+=1
            receipt=next(row for row in data["calculated_candidates"]["candidates"] if row["eligible"])
            result=dict(selection_token=f"{receipt['id']}:{model_receipt_hash(receipt)}",rationale="원래 자료의 계산 검토")
        else:
            wall[0]+=draft_seconds
            ctx=data["contract_context"]
            proposal=wire(ctx,"OPEN")
            proposal.update(side=side,hard_stop=98 if side=="LONG" else 102)
            proposal["entries"][0].update(price=101 if side=="LONG" else 99,trigger_price=101 if side=="LONG" else 99)
            proposal["take_profits"][0]["price"]=raw_tp
            result=dict(wait_proposal=wire(ctx,"WAIT"),candidates=[proposal],no_candidate_reason="")
        return SimpleNamespace(text=json.dumps(result))
    tape=tmp_path/"tape"
    with network_boundary("frozen") as blocked:
        fresh=run_replay(bundle,market,tmp_path/"fresh",mode="fixture",tape_path=tape,
            policy_profile="calculated-economic-v4",model_generator=generate,clock_monotonic=lambda:wall[0],max_decisions=1)
        frozen=run_replay(bundle,market,tmp_path/"frozen",mode="frozen",tape_path=tape,
            policy_profile="calculated-economic-v4",max_decisions=1)
    assert blocked==[] and fresh["execution_metadata"]["actual_model_calls"]==0
    with sqlite3.connect(tmp_path/"fresh"/"replay.sqlite") as conn:
        intents=conn.execute("SELECT payload FROM llm_scenario_intents").fetchall()
    assert len(intents)==1
    assert json.loads(intents[0][0])["take_profits"][0]["price"]==pytest.approx(expected_tp)
    assert fresh["result_hash"]==frozen["result_hash"]
    assert fresh["runtime_event_hash"]==frozen["runtime_event_hash"]
    envelope=json.loads(tape.read_text().splitlines()[1])["decision"]["envelope"]
    candidate=envelope["outcome"]["result"]["candidates"]["candidates"][0]
    assert candidate["pricing_basis"]["kind"]=="ORIGINAL_INPUT_HYPOTHETICAL"
    assert candidate["pricing_basis"]["original_input_now"]==envelope["inputs"]["context"]["now"]
    assert candidate["pricing_basis"]["validation_now"]>=candidate["pricing_basis"]["original_input_now"]+draft_seconds
    assert candidate["pricing_basis"]["quote_captured_at"]==envelope["inputs"]["context"]["limit_price_quotes"]["captured_at"]
    assert candidate["execution_authorized"] is False
