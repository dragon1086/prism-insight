"""Versioned economic rejection is evidence, never WAIT or an order."""
import json
import sqlite3
from types import SimpleNamespace
from pathlib import Path
import sys

import pytest

from analysis.scenario_replay import run_replay, network_boundary, contract_for
from tests.test_scenario_replay import inputs
from tests.test_scenario_calculated_replay import wire

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.compare_btc_scenario_replays import verify_economic_rejections  # noqa: E402
from tests.test_scenario_review_guard import model_receipt_hash


def generator(calls, *, calculated, bad_raw=False, held=False):
    def generate(**request):
        calls.append(request)
        payload = json.loads(request["user_prompt"])
        ctx = payload["contract_context"]
        if "selection_token" in request["response_schema"]["properties"]:
            receipts = payload["calculated_candidates"]
            chosen = next((r for r in receipts["candidates"] if r["eligible"]), receipts["wait"])
            result = dict(selection_token=f"{chosen['id']}:{model_receipt_hash(chosen)}", rationale="검산 확인")
        else:
            raw = wire(ctx, "OPEN")
            if ctx.get("scenario_id"):
                raw.update(action="ADJUST", side=ctx["side"], hard_stop=ctx["previous_hard_stop"])
                raw["take_profits"] = []
            raw["entries"][0].pop("quantity")
            raw["entries"][0]["allocation_fraction"] = .01 if held and len(calls) == 1 else 1e-10
            if bad_raw:
                raw["unexpected"] = "invalid"
            result = (dict(wait_proposal=wire(ctx, "WAIT"), candidates=[raw], no_candidate_reason="")
                      if calculated else dict(proposal=raw))
        return SimpleNamespace(text=json.dumps(result))
    return generate


@pytest.mark.parametrize("calculated", [False, True])
def test_rejection_then_next_planned_tick_exact_zero_model_replay(tmp_path, calculated):
    bundle, market = inputs()
    profile = "economic-reject-calculated-v1" if calculated else "economic-reject-single-v1"
    calls = []
    with network_boundary("frozen") as blocked:
        fresh = run_replay(bundle, market, tmp_path / "fresh", mode="fixture", tape_path=tmp_path / "tape",
            policy_profile=profile, model_generator=generator(calls, calculated=calculated), clock_monotonic=lambda: 0.)
        frozen = run_replay(bundle, market, tmp_path / "frozen", mode="frozen", tape_path=tmp_path / "tape",
            policy_profile=profile)
    assert blocked == [] and len(calls) == 2
    assert fresh["execution_status"] == "COMPLETE", fresh["failure_evidence"]
    assert fresh["decision_outcomes"] == {"rejected_no_action": 2}
    assert fresh["economic_rejections"]["rejected_ticks"] == 2
    assert fresh["economic_rejections"]["rejected_candidates"] == 2
    assert fresh["economic_rejections"]["codes"] == {"allocation_below_minimum": 2}
    assert fresh["model_failures"] == 0
    assert fresh["result_hash"] == frozen["result_hash"]
    assert frozen["execution_metadata"]["actual_model_calls"] == 0
    assert fresh["protection_outcomes"].get("failed", 0) == 0
    with sqlite3.connect(tmp_path / "fresh/replay.sqlite") as conn:
        assert conn.execute("SELECT count(*) FROM llm_scenario_intents").fetchone()[0] == 0
    verify_economic_rejections(tmp_path / "fresh", fresh)
    fresh["economic_rejections"]["rejected_candidates"] += 1
    with pytest.raises(ValueError, match="economic_rejection_evidence_mismatch"):
        verify_economic_rejections(tmp_path / "fresh", fresh)


@pytest.mark.parametrize("calculated", [False, True])
def test_bad_raw_still_first_error_stop(tmp_path, calculated):
    bundle, market = inputs()
    calls = []
    profile = "economic-reject-calculated-v1" if calculated else "economic-reject-single-v1"
    with network_boundary("frozen"):
        fresh = run_replay(bundle, market, tmp_path / "fresh", mode="fixture", tape_path=tmp_path / "tape",
            policy_profile=profile, model_generator=generator(calls, calculated=calculated, bad_raw=True),
            clock_monotonic=lambda: 0.)
    assert len(calls) == 1
    assert fresh["execution_status"] == "STOPPED"
    assert fresh["model_failures"] == 1
    assert fresh["economic_rejections"]["rejected_ticks"] == 0
    # Failed decisions may have NULL context; diagnostic recount must remain
    # available without making that stopped run eligible for comparison.
    with sqlite3.connect(tmp_path / "fresh/replay.sqlite") as conn:
        conn.execute("UPDATE llm_scenario_decisions SET context=NULL")
    verify_economic_rejections(tmp_path / "fresh", fresh)


def test_legacy_rejection_recount_does_not_require_new_profile(tmp_path):
    verify_economic_rejections(tmp_path, {"contract": {}})
    with pytest.raises(ValueError, match="unexpected_rejection_protocol"):
        verify_economic_rejections(tmp_path, {"contract": {}, "economic_rejections": {}})


def test_rejected_tick_requires_its_own_context(tmp_path):
    bundle, market = inputs()
    with network_boundary("frozen"):
        fresh = run_replay(bundle, market, tmp_path / "fresh", mode="fixture", tape_path=tmp_path / "tape",
            policy_profile="economic-reject-single-v1", model_generator=generator([], calculated=False),
            clock_monotonic=lambda: 0.)
    with sqlite3.connect(tmp_path / "fresh/replay.sqlite") as conn:
        conn.execute("UPDATE llm_scenario_decisions SET context=NULL")
    with pytest.raises(ValueError, match="economic_rejection_context_missing"):
        verify_economic_rejections(tmp_path / "fresh", fresh)


def test_new_contract_is_explicit_not_legacy_reclassification():
    bundle, _ = inputs()
    legacy = contract_for(bundle, "OHLC", 1, 10000, policy_profile="allocation-single-economic-v1")
    new = contract_for(bundle, "OHLC", 1, 10000, policy_profile="economic-reject-single-v1")
    assert "rejection_policy" not in legacy["phase_contracts"]
    assert new["phase_contracts"]["rejection_policy"] == "preauthority-economic-reject-v1"
    assert new["phase_contracts"]["model_error_policy"] == "STOP_FIRST_ERROR"
    assert new["phase_contracts"]["max_model_calls"] == 1


@pytest.mark.parametrize("calculated", [False, True])
def test_legacy_allocation_rejection_remains_strict(tmp_path, calculated):
    bundle, market = inputs()
    calls = []
    base = generator(calls, calculated=calculated)
    def generate(**request):
        result = base(**request)
        raw = json.loads(result.text)
        if not calculated:
            raw = raw["proposal"]
        return SimpleNamespace(text=json.dumps(raw))
    profile = "allocation-calculated-economic-v1" if calculated else "allocation-single-economic-v1"
    with network_boundary("frozen"):
        fresh = run_replay(bundle, market, tmp_path / "fresh", mode="fixture", tape_path=tmp_path / "tape",
            policy_profile=profile, model_generator=generate, clock_monotonic=lambda: 0.)
    # Old A stops while old B can discard a candidate and select WAIT. This old
    # asymmetry is preserved, never retroactively relabeled as the new protocol.
    assert "economic_rejections" not in fresh
    if not calculated:
        assert fresh["execution_status"] == "STOPPED" and len(calls) == 1
    else:
        assert fresh["execution_status"] == "COMPLETE"
        assert fresh["decision_outcomes"] == {"wait": 2}


@pytest.mark.parametrize("stop_fill", [False, True])
def test_rejection_preserves_existing_position_and_protection(tmp_path, stop_fill):
    bundle, market = inputs(600000 if stop_fill else 900000)
    if stop_fill:
        for frame in (market.source, market.mark):
            frame.loc[frame.index.view("i8") // 1000000 == bundle["start_ms"] + 360000, "low"] = 97
    calls = []
    with network_boundary("frozen") as blocked:
        fresh = run_replay(bundle, market, tmp_path / "fresh", mode="fixture", tape_path=tmp_path / "tape",
            policy_profile="economic-reject-single-v1", model_generator=generator(calls, calculated=False, held=True),
            clock_monotonic=lambda: 0.)
        frozen = run_replay(bundle, market, tmp_path / "frozen", mode="frozen", tape_path=tmp_path / "tape",
            policy_profile="economic-reject-single-v1")
    assert blocked == []
    assert fresh["execution_status"] == "COMPLETE", fresh["failure_evidence"]
    assert fresh["decision_outcomes"].get("rejected_no_action") == (1 if stop_fill else 2)
    exchange = json.loads((tmp_path / "fresh/exchange.json").read_text())
    if stop_fill:
        assert fresh["economic"]["open_quantity"] == 0
        assert len(exchange["executions"]) == 2
        assert fresh["economic"]["fees"] > 0
        assert any(order.get("stopOrderType") == "StopLoss" and order["orderStatus"] == "Filled"
                   for order in exchange["orders"])
    else:
        assert fresh["economic"]["open_quantity"] > 0
    assert fresh["protection_outcomes"].get("failed", 0) == 0
    assert fresh["result_hash"] == frozen["result_hash"]
    verify_economic_rejections(tmp_path / "fresh", fresh)


def test_protocol_registration_pins_modes_and_fixed_caps(tmp_path):
    from tools import btc_calculated_economic_study as study
    from tests.test_calculated_economic_study import data_files
    market, observed, pin = data_files(tmp_path)
    root = tmp_path / "registered"
    registry = study.prepare(root, market, observed, pin, "fixture-source",
                             protocol="preauthority-economic-reject-v1")
    assert registry["protocol"] == "preauthority-economic-reject-v1"
    assert {v["profile"] for v in registry["variants"]} == {
        "economic-reject-single-v1", "economic-reject-calculated-v1"}
    assert study.STAGES == {1: (0, 4, 24), 2: (4, 24, 144)}


def test_mixed_rejected_candidate_count_is_not_rejected_tick(tmp_path):
    bundle, market = inputs(300000)
    calls = []
    base = generator(calls, calculated=True)
    def generate(**request):
        result = base(**request)
        raw = json.loads(result.text)
        if "candidates" in raw:
            from copy import deepcopy
            valid = deepcopy(raw["candidates"][0])
            valid["entries"][0]["allocation_fraction"] = .01
            raw["candidates"].append(valid)
        return SimpleNamespace(text=json.dumps(raw))
    with network_boundary("frozen"):
        report = run_replay(bundle, market, tmp_path / "fresh", mode="fixture", tape_path=tmp_path / "tape",
            policy_profile="economic-reject-calculated-v1", model_generator=generate, clock_monotonic=lambda: 0.)
    assert report["execution_status"] == "COMPLETE", report["failure_evidence"]
    assert report["economic_rejections"]["rejected_candidates"] == 1
    assert report["economic_rejections"]["rejected_ticks"] == 0
    assert len(calls) == 2
    verify_economic_rejections(tmp_path / "fresh", report)
