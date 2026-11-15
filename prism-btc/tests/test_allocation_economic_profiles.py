"""Explicit opt-in allocation research; never orders or model network."""
import pytest
import json
from types import SimpleNamespace

from analysis.scenario_replay import contract_for, POLICY_PROFILES
from analysis.scenario_calculated_replay import ECONOMIC_PROFILES, REVIEW_PROFILES
from tests.test_scenario_replay import inputs
from tests.test_calculated_economic_study import data_files, study
from tests.test_scenario_calculated_replay import wire
from analysis.scenario_replay import run_replay, network_boundary
from tests.test_scenario_review_guard import model_receipt_hash


@pytest.mark.parametrize("profile,mode,cap", [
    ("allocation-single-economic-v1", "allocation-single", 1),
    ("allocation-calculated-economic-v1", "allocation-calculated", 2),
])
def test_explicit_allocation_contract(profile, mode, cap):
    bundle, _ = inputs()
    contract = contract_for(bundle, "OHLC", 1., 10000., policy_profile=profile)
    assert profile in ECONOMIC_PROFILES & REVIEW_PROFILES & POLICY_PROFILES.keys()
    assert contract["schema"] == 4
    assert contract["phase_contracts"]["research_mode"] == mode
    assert contract["phase_contracts"]["max_model_calls"] == cap
    assert contract["review_binding"]


def test_default_study_still_legacy_and_opt_in_is_explicit(tmp_path):
    market, observed, pin = data_files(tmp_path)
    legacy = study.prepare(tmp_path / "legacy", market, observed, pin, "test")
    assert "protocol" not in legacy
    assert {v["profile"] for v in legacy["variants"]} == set(study.PROTOCOLS["legacy"])
    candidate = study.prepare(tmp_path / "allocation", market, observed, pin, "test",
                              protocol="risk-allocation-v1")
    assert candidate["protocol"] == "risk-allocation-v1"
    assert {v["profile"] for v in candidate["variants"]} == set(study.PROTOCOLS["risk-allocation-v1"])
    assert candidate["slots"] == 4 and candidate["max_http_calls"] == 24
    assert candidate["next_stage_automatic"] is False
    study._verify_inputs(tmp_path / "allocation", candidate)


def test_unregistered_protocol_rejected_before_creation(tmp_path):
    with pytest.raises(ValueError, match="registered_protocol_required"):
        study.prepare(tmp_path / "bad", "unused", "unused", "unused", "test", protocol="arbitrary")
    assert not (tmp_path / "bad").exists()


@pytest.mark.parametrize("registry,error", [
    ({"protocol": "unknown"}, "registered_protocol_required"),
    ({"protocol": "legacy", "variants": [dict(name=name, profile=study.PROTOCOLS["risk-allocation-v1"][0])
                                           for name in study.ORDER]}, "registered_protocol_profiles_required"),
])
def test_run_rejects_protocol_substitution_before_start(tmp_path, registry, error):
    (tmp_path / "registry.json").write_text(json.dumps(registry))
    with pytest.raises(ValueError, match=error):
        study.run(tmp_path)
    assert not (tmp_path / "run_started.json").exists()


@pytest.mark.parametrize("profile", study.PROTOCOLS["risk-allocation-v1"])
@pytest.mark.parametrize("path", ["OHLC", "OLHC"])
def test_allocation_lifecycle_exact_frozen_zero_calls(tmp_path, profile, path):
    bundle, market = inputs(1200000)
    wall, calls = [0.], []
    def generate(**request):
        calls.append(request)
        wall[0] += 2.5
        payload = json.loads(request["user_prompt"])
        ctx = payload["contract_context"]
        if "selection_token" in request["response_schema"]["properties"]:
            receipts = payload["calculated_candidates"]
            selected = next((r for r in receipts["candidates"] if r["eligible"]), receipts["wait"])
            result = dict(selection_token=f"{selected['id']}:{model_receipt_hash(selected)}", rationale="계산 확인")
        else:
            action = ("EXIT" if ctx.get("scenario_id") and ctx["now"] >= bundle["start_ms"] / 1000 + 600
                      else "WAIT" if ctx.get("scenario_id") else "OPEN")
            proposal = wire(ctx, action)
            for entry in proposal["entries"]:
                entry.pop("quantity")
                entry["allocation_fraction"] = .01
            result = (dict(wait_proposal=wire(ctx, "WAIT"), candidates=[proposal] if action != "WAIT" else [],
                           no_candidate_reason="" if action != "WAIT" else "기존 포지션 유지")
                      if "wait_proposal" in request["response_schema"]["properties"] else proposal)
        return SimpleNamespace(text=json.dumps(result))
    tape = tmp_path / "tape"
    with network_boundary("frozen") as blocked:
        fresh = run_replay(bundle, market, tmp_path / "fresh", mode="fixture", tape_path=tape,
            path=path, policy_profile=profile, model_generator=generate, clock_monotonic=lambda: wall[0])
        frozen = run_replay(bundle, market, tmp_path / "frozen", mode="frozen", tape_path=tape,
            path=path, policy_profile=profile)
    assert blocked == []
    assert fresh["execution_status"] == "COMPLETE"
    assert fresh["economic"]["completed_scenarios"] >= 1
    assert fresh["economic"]["fees"] > 0
    assert fresh["result_hash"] == frozen["result_hash"]
    assert fresh["exchange_hash"] == frozen["exchange_hash"]
    assert frozen["execution_metadata"]["actual_model_calls"] == 0
    assert len(calls) <= 4 * (1 if "single" in profile else 2)
