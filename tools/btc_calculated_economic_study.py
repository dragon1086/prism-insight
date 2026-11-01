"""Explicit prepare/run for two fixed, separately preregistered development stages.

No automatic expansion, retry, restart, orders, or profitability approval.
"""
# Standalone CLI must add the hyphenated package directory before imports.
# ruff: noqa: E402
from copy import deepcopy
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "prism-btc"))

from analysis.scenario_dataset import load_bundle, digest
from analysis.scenario_replay import contract_for, run_replay, network_boundary
from backtest.observed_scenario_data import ObservedScenarioData
from analysis.scenario_calculated_replay import EconomicTape
from tools.btc_research_call_guard import CallGuard
from tools.btc_research_model_call import GuardedModelCall
from tools.compare_btc_scenario_replays import (
    CALCULATED_COMMON_FIELDS, CALCULATED_ARM_FIELDS, CALCULATED_VARIANT_FIELDS,
    compare_calculated, verify_run, verify_calculated_registry_contract,
    verify_calculated_economics, verify_calculated_completion,
)

ORDER = ("A_OHLC", "B_OHLC", "B_OLHC", "A_OLHC")
MAX_HTTP = 24
STAGES = {1: (0, 4, 24), 2: (4, 24, 144)}
PROTOCOLS = {
    "legacy": ("single-pass-economic-v4", "calculated-economic-v4"),
    "risk-allocation-v1": ("allocation-single-economic-v1", "allocation-calculated-economic-v1"),
    "preauthority-economic-reject-v1": ("economic-reject-single-v1", "economic-reject-calculated-v1"),
}


def predecessor_manifest(root, registry_sha256):
    """Read an externally pinned completed study; never open its guard for writing."""
    root = Path(root).resolve()
    require(isinstance(registry_sha256, str) and re.fullmatch("[a-f0-9]{64}", registry_sha256) is not None,
            "external_predecessor_registry_sha256_required")
    require(sha(root / "registry.json") == registry_sha256, "predecessor_registry_pin_mismatch")
    registry = json.loads((root / "registry.json").read_text())
    require(json.loads((root / "registry.sha256.json").read_text())["sha256"] == registry_sha256
            and json.loads((root / "run_started.json").read_text())["registry_sha256"] == registry_sha256,
            "predecessor_registry_anchor_mismatch")
    require(registry["stage"] == 1 and registry["slots"] == 4 and registry["max_http_calls"] == 24
            and registry["run_order"] == list(ORDER), "completed_stage_one_required")
    require(registry["source_manifest"]["tools/compare_btc_scenario_replays.py"]
            == sha(ROOT / "tools/compare_btc_scenario_replays.py"), "predecessor_comparator_changed")
    result = json.loads((root / "study_result.json").read_text())
    require(result["status"] == "COMPLETE_DEVELOPMENT_SMOKE" and result["comparison"]["comparison_complete"] is True,
            "predecessor_not_complete")
    require(compare_calculated(root)["comparison_complete"], "predecessor_artifact_validation_failed")
    # _read is a pure existing chain/anchor auditor. This namespace has no guard
    # constructor/lock/write methods, so the old study cannot be resumed or changed.
    records, state = CallGuard._read(SimpleNamespace(ledger=root / "call_guard/attempts.jsonl",
        anchors=root / "call_guard/anchors", registry_sha256=registry_sha256, max_calls=24))
    require(not state["stopped"] and state["inflight"] is None and state["unknown_http_attempts"] == 0,
            "predecessor_call_ledger_ineligible")
    require(result["call_guard"]["actual_http_calls"] == state["actual_http_calls"]
            and result["call_guard"]["registry_sha256"] == registry_sha256
            and sum(json.loads((root / name / "fresh/report.json").read_text())
                    ["execution_metadata"]["actual_model_calls"] for name in ORDER) == state["actual_http_calls"],
            "predecessor_http_count_mismatch")
    require((root / "model_events.jsonl").is_file(), "predecessor_wire_events_missing")
    events = [json.loads(line) for line in (root / "model_events.jsonl").read_text().splitlines()]
    requests = [event for event in events if event["kind"] == "HTTP_REQUEST"]
    reservations = {row["attempt_id"]: row for row in records if row["kind"] == "reserve"}
    require(len(requests) == len(reservations) == state["actual_http_calls"]
            and {event["attempt_id"] for event in requests} == set(reservations),
            "predecessor_wire_attempt_coverage")
    for event in requests:
        saved = reservations[event["attempt_id"]]
        require(event["request_sha256"] == saved["request_sha256"]
                == hashlib.sha256(event["request_body"].encode()).hexdigest()
                and event["arm"] == saved["arm"] and event["phase"] == saved["phase"]
                and event["generate_kwargs"]["retry_transport"] is False and "headers" not in event,
                "predecessor_wire_request_mismatch")
        if 'runtime_readiness_sha256' in saved:
            readiness = event.get('runtime_readiness')
            require(isinstance(readiness, dict) and readiness.get('status') == 'PASSED'
                    and hashlib.sha256(json.dumps(readiness, sort_keys=True, separators=(',', ':'),
                                                  ensure_ascii=True, allow_nan=False).encode()).hexdigest()
                        == saved['runtime_readiness_sha256'], "predecessor_runtime_readiness_mismatch")
    for kind in ("HTTP_RESPONSE", "MODEL_RESULT"):
        rows = [event for event in events if event["kind"] == kind]
        require(len(rows) == len(reservations)
                and {event["attempt_id"] for event in rows} == set(reservations),
                "predecessor_wire_result_coverage")
    responses = {event["attempt_id"]: event for event in events if event["kind"] == "HTTP_RESPONSE"}
    results = {event["attempt_id"]: event for event in events if event["kind"] == "MODEL_RESULT"}
    for attempt, saved in reservations.items():
        response, model_result = responses[attempt], results[attempt]
        for event in (response, model_result):
            require(event["request_sha256"] == saved["request_sha256"]
                    and event["arm"] == saved["arm"] and event["phase"] == saved["phase"],
                    "predecessor_wire_result_identity")
        require(response["raw_sha256"] == hashlib.sha256(response["raw_response"].encode()).hexdigest()
                and response["truncated"] is False and response["http_status"] == 200,
                "predecessor_wire_response_mismatch")
        body = json.loads(response["raw_response"])
        text = "".join(block["text"] for item in body["output"] if item["type"] == "message"
                       for block in item["content"] if block["type"] == "output_text")
        require(body["status"] == "completed" and body["model"] == registry["model"]
                and model_result["text"] == text and model_result["actual_http_calls"] == 1,
                "predecessor_wire_model_result_mismatch")
    for name in ORDER:
        require((root / name / "tape.jsonl").is_file(), "predecessor_tape_missing")
        fresh = json.loads((root / name / "fresh/report.json").read_text())
        frozen = json.loads((root / name / "frozen/report.json").read_text())
        tape = EconomicTape(root / name / "tape.jsonl", "frozen", fresh["contract"])
        require(tape.transcript_hash() == fresh["tape_hash"] == frozen["tape_hash"]
                and [tick["at_ms"] for tick in tape.ticks] == [item["slot"] * 300000 for item in registry["schedule"]],
                "predecessor_tape_hash_or_coverage")
    files = {str(path.relative_to(root)): sha(path) for path in sorted(root.rglob("*")) if path.is_file()}
    return registry, dict(root=str(root), registry_sha256=registry_sha256, artifacts=files,
                         evidence_scope="TECHNICAL_PARITY_NOT_PROFITABILITY_OR_COMPLETE_FILL_EXPOSURE")


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_new(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    fd = os.open(Path(path).parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def source_manifest():
    paths = [p for p in (ROOT / "prism-btc").rglob("*.py")
             if "tests" not in p.relative_to(ROOT / "prism-btc").parts]
    paths += [ROOT / "tools" / name for name in (
        "btc_calculated_economic_study.py", "btc_research_call_guard.py",
        "btc_research_model_call.py", "compare_btc_scenario_replays.py")]
    return {str(path.relative_to(ROOT)): sha(path) for path in sorted(paths)}


def _oracle(market, bundle):
    def previous_mark(at):
        rows = market.mark.loc[market.mark.index.view("i8") // 1_000_000 == at - 60000]
        require(len(rows) == 1, "missing_previous_confirmed_mark")
        return str(float(rows.iloc[0].close))
    start, end = bundle["start_ms"], bundle["end_ms"]
    return dict(terminal_mark_price=previous_mark(end), funding_events=[
        dict(timestamp_ms=row["timestamp"], rate=str(row["rate"]), mark_price=previous_mark(row["timestamp"]))
        for row in bundle["funding"] if start < row["timestamp"] <= end])


def prepare(root, market_path, observed_path, observed_sha256, source_reference, *, initial_equity=10000.,
            stage=1, predecessor_root=None, predecessor_registry_sha256=None, protocol="legacy"):
    """Freeze original files and the registered fixed stage window before calls."""
    root, market_path, observed_path = map(lambda p: Path(p).resolve(), (root, market_path, observed_path))
    require(not root.exists(), "never_overwrite_study")
    require(type(stage) is int and stage in STAGES, "fixed_stage_required")
    require(protocol in PROTOCOLS, "registered_protocol_required")
    offset, slots, cap = STAGES[stage]
    predecessor = None
    if stage == 2:
        require(predecessor_root is not None, "stage_two_predecessor_required")
        require(Path(predecessor_root).resolve() not in root.parents, "never_write_inside_predecessor")
        previous_registry, predecessor = predecessor_manifest(predecessor_root, predecessor_registry_sha256)
        require(previous_registry.get("protocol", "legacy") == protocol, "predecessor_protocol_changed")
    else:
        require(predecessor_root is None and predecessor_registry_sha256 is None, "stage_one_has_no_predecessor")
    require(isinstance(source_reference, str) and bool(source_reference), "source_reference_required")
    full, market = load_bundle(market_path)
    observed = ObservedScenarioData(observed_path, observed_sha256, allow_gaps=False)
    all_slots = observed.schedule()
    require(len(all_slots) >= offset + slots, "fixed_observed_slots_required")
    start = (all_slots[0]["slot"] + offset) * 300000
    end = start + slots * 300000
    if stage == 2:
        require(previous_registry["original_inputs"] == dict(market_path=str(market_path), market_sha256=sha(market_path),
            observed_path=str(observed_path), observed_sha256=observed_sha256, full_data_hash=full["data_hash"]),
            "predecessor_original_inputs_changed")
        require(previous_registry["initial_equity"] == initial_equity
                and previous_registry["schedule"][-1]["slot"] + 1 == start // 300000,
                "predecessor_window_or_equity_mismatch")
    require(full["start_ms"] <= start < end <= full["end_ms"], "market_window_not_contained")
    pilot = deepcopy(full)
    pilot.update(start_ms=start, end_ms=end)
    pilot["data_hash"] = digest({k: v for k, v in pilot.items() if k not in {"data_hash", "coverage", "provenance"}})
    variants, contracts = [], {}
    for name in ORDER:
        profile = PROTOCOLS[protocol][0 if name.startswith("A_") else 1]
        path = name.split("_")[1]
        variants.append(dict(name=name, path=path, cost_multiplier=1, profile=profile))
        contracts[name] = contract_for(pilot, path, 1., initial_equity, origin="luna", policy_profile=profile,
            initial_state="recovery-stage0", observed_manifest=observed.manifest(),
            market_input_mode="observed", allow_input_gaps=False)
        if stage == 2:
            old = previous_registry["contract_comparison"]
            require(contracts[name]["source_hashes"] == old["expected_common_contract"]["source_hashes"]
                    and {key: contracts[name][key] for key in CALCULATED_ARM_FIELDS}
                        == old["expected_arm_contracts"][name], "stage_two_policy_or_receipt_changed")
            for tool in ("btc_research_call_guard.py", "btc_research_model_call.py", "compare_btc_scenario_replays.py"):
                require(sha(ROOT / "tools" / tool) == previous_registry["source_manifest"]["tools/" + tool],
                        "stage_two_guard_or_comparator_changed")
    first = contracts[ORDER[0]]
    common = {k: first[k] for k in CALCULATED_COMMON_FIELDS}
    require(all({k: c[k] for k in CALCULATED_COMMON_FIELDS} == common for c in contracts.values()),
            "prepared_common_contract_mismatch")
    def utc(at):
        return datetime.fromtimestamp(at / 1000, timezone.utc).isoformat().replace("+00:00", "Z")
    registry = dict(schema=2, stage=stage, purpose="DEVELOPMENT_SMOKE_NOT_PROFITABILITY_PROOF",
        max_http_calls=cap, run_order=list(ORDER), slots=slots, next_stage_automatic=False,
        latency_analysis="NATIVE_LATENCY_ONLY", initial_state="recovery-stage0",
        source_reference=source_reference, source_pin_reference=source_reference,
        source_root=str(ROOT), source_manifest=source_manifest(),
        original_inputs=dict(market_path=str(market_path), market_sha256=sha(market_path),
            observed_path=str(observed_path), observed_sha256=observed_sha256, full_data_hash=full["data_hash"]),
        pilot_data_hash=pilot["data_hash"], schedule=observed.schedule(start, end), variants=variants,
        start=utc(start), end=utc(end), initial_equity=initial_equity,
        model=first["model"], reasoning=first["effort"], service_tier="priority",
        expected_data_hash=pilot["data_hash"], expected_source_hashes_sha256=digest(first["source_hashes"]),
        expected_runtime_versions=first["runtime_versions"],
        funding_mark_basis="ASSUMED_PREVIOUS_CONFIRMED_MINUTE_MARK_NOT_EXCHANGE_EVENT_MARK",
        economic_oracle_inputs=_oracle(market, pilot),
        contract_comparison=dict(common_fields=list(CALCULATED_COMMON_FIELDS), arm_fields=list(CALCULATED_ARM_FIELDS),
            variant_fields=list(CALCULATED_VARIANT_FIELDS), expected_common_contract=common,
            expected_arm_contracts={name: {k: c[k] for k in CALCULATED_ARM_FIELDS} for name, c in contracts.items()},
            expected_variant_contracts={name: {k: c[k] for k in CALCULATED_VARIANT_FIELDS} for name, c in contracts.items()}))
    if protocol != "legacy":
        registry["protocol"] = protocol
    if stage == 2:
        registry.update(predecessor=predecessor, purpose="DEVELOPMENT_COMPARISON_NOT_INDEPENDENT_OOS_OR_M5_PROOF",
            trial_ledger=[dict(stage=1, root=predecessor["root"], registry_sha256=predecessor["registry_sha256"], max_http_calls=24),
                          dict(stage=2, root=str(root), registry_file="registry.json", max_http_calls=144)],
            inference_limits=dict(development_period_count=1, historical_not_untouched=True,
                independent_oos_samples=0, forward_samples=0, statistical_confidence_intervals="NOT_COMPUTED",
                intrabar_paths="ASSUMED_SENSITIVITY_NOT_INDEPENDENT_REPLICATES",
                mde="UNCOMPUTED_NO_INDEPENDENT_VARIANCE", power="UNCOMPUTED",
                inferential_tests="NONE_DESCRIPTIVE_PAIRED_NET_EQUITY_AND_MTM_DRAWDOWN_PER_PATH",
                zero_fills="INSUFFICIENT_ECONOMIC_EXPOSURE_NO_FORCED_ENTRY_OR_RESAMPLING",
                candidate_rejections="PRESERVE_SEPARATELY_FROM_MODEL_TRANSPORT_FAILURES",
                receipt_and_risk_policy="UNCHANGED_FROM_STAGE_ONE"))
    for variant in variants:
        verify_calculated_registry_contract(contracts[variant["name"]], registry, variant)
    root.mkdir(parents=True)
    write_new(root / "pilot.json", pilot)
    registry["pilot_file_sha256"] = sha(root / "pilot.json")
    write_new(root / "registry.json", registry)
    write_new(root / "registry.sha256.json", {"sha256": sha(root / "registry.json")})
    return registry


def _verify_pins(root, registry):
    require(sha(root / "registry.json") == json.loads((root / "registry.sha256.json").read_text())["sha256"],
            "registry_changed")
    require(registry["source_root"] == str(ROOT) and source_manifest() == registry["source_manifest"],
            "source_manifest_changed")
    inputs = registry["original_inputs"]
    require(sha(inputs["market_path"]) == inputs["market_sha256"]
            and sha(inputs["observed_path"]) == inputs["observed_sha256"]
            and sha(root / "pilot.json") == registry["pilot_file_sha256"], "frozen_data_changed")
    if registry["stage"] == 2:
        predecessor = registry["predecessor"]
        old = Path(predecessor["root"])
        require(sha(old / "registry.json") == predecessor["registry_sha256"]
                and {str(p.relative_to(old)): sha(p) for p in sorted(old.rglob("*")) if p.is_file()}
                    == predecessor["artifacts"], "predecessor_artifacts_changed")


def _verify_inputs(root, registry):
    _verify_pins(root, registry)
    inputs = registry["original_inputs"]
    pilot, market = load_bundle(root / "pilot.json")
    observed = ObservedScenarioData(inputs["observed_path"], inputs["observed_sha256"], allow_gaps=False)
    offset, slots, _ = STAGES[registry["stage"]]
    expected_start = (observed.schedule()[0]["slot"] + offset) * 300000
    expected_end = expected_start + slots * 300000
    require(pilot["start_ms"] == expected_start and pilot["end_ms"] == expected_end
            and registry["schedule"] == observed.schedule(expected_start, expected_end),
            "fixed_stage_window_mismatch")
    if registry["stage"] == 2:
        previous = json.loads((Path(registry["predecessor"]["root"]) / "registry.json").read_text())
        require(previous["schedule"][-1]["slot"] + 1 == expected_start // 300000,
                "predecessor_window_discontinuity")
    require(pilot["data_hash"] == registry["pilot_data_hash"]
            and observed.schedule(pilot["start_ms"], pilot["end_ms"]) == registry["schedule"]
            and _oracle(market, pilot) == registry["economic_oracle_inputs"], "frozen_window_changed")
    for variant in registry["variants"]:
        contract = contract_for(pilot, variant["path"], variant["cost_multiplier"], registry["initial_equity"],
            origin="luna", policy_profile=variant["profile"], initial_state=registry["initial_state"],
            observed_manifest=observed.manifest(), market_input_mode="observed", allow_input_gaps=False)
        verify_calculated_registry_contract(contract, registry, variant)
    return pilot, market


def run(root, *, replay=run_replay, model_factory=GuardedModelCall):
    """One explicit serial run. An existing start marker forbids all restarts."""
    root = Path(root).resolve()
    registry = json.loads((root / "registry.json").read_text())
    protocol = registry.get("protocol", "legacy")
    require(protocol in PROTOCOLS, "registered_protocol_required")
    require([v["name"] for v in registry["variants"]] == list(ORDER)
            and all(v["profile"] == PROTOCOLS[protocol][0 if v["name"].startswith("A_") else 1]
                    for v in registry["variants"]), "registered_protocol_profiles_required")
    require(type(registry["stage"]) is int and registry["stage"] in STAGES, "fixed_stage_required")
    _, slots, cap = STAGES[registry["stage"]]
    require(registry["max_http_calls"] == cap
            and registry["run_order"] == list(ORDER) and registry["slots"] == slots
            and registry["next_stage_automatic"] is False, "fixed_stage_configuration_required")
    write_new(root / "run_started.json", {"registry_sha256": sha(root / "registry.json")})
    guard = CallGuard(root / "call_guard", sha(root / "registry.json"), cap)
    arm_counts, dispatches, parity = {}, {}, {}
    comparison_status, diagnostic_record_status, coordinator_error = "AVAILABLE", "COMPLETE", None
    events_path = root / "model_events.jsonl"
    with events_path.open("x", encoding="utf-8") as events:
        def record(event):
            events.write(json.dumps(event, sort_keys=True, allow_nan=False) + "\n")
            events.flush()
            os.fsync(events.fileno())

        def before_http(_dispatch):
            try:
                _verify_pins(root, registry)
                require(not guard.snapshot()["stopped"], "global_model_calls_stopped")
            except BaseException:
                guard.stop("source_or_data_changed")
                raise

        def fail(evidence):
            guard.stop("technical_failure")
            record(dict(kind="STUDY_FAILURE", evidence=evidence))

        try:
            for variant in registry["variants"]:
                if guard.snapshot()["stopped"]:
                    break
                pilot, market = _verify_inputs(root, registry)
                name = variant["name"]
                arm = root / name
                arm.mkdir()
                tape = arm / "tape.jsonl"
                generator = model_factory(guard, name, record)
                kwargs = dict(path=variant["path"], cost_multiplier=variant["cost_multiplier"],
                    initial_equity=registry["initial_equity"], policy_profile=variant["profile"],
                    initial_state=registry["initial_state"], max_decisions=slots,
                    observed_packet=registry["original_inputs"]["observed_path"],
                    observed_packet_sha256=registry["original_inputs"]["observed_sha256"],
                    market_input_mode="observed", allow_input_gaps=False)
                before_count = guard.snapshot()["actual_http_calls"]
                parity[name] = "MISSING_FRESH_REPORT_OR_TAPE"
                try:
                    with network_boundary("fresh", os.environ.get("PRISM_BTC_SCENARIO_OAUTH_URL")):
                        report = replay(pilot, market, arm / "fresh", mode="fresh", tape_path=tape,
                            model_generator=generator, before_http=before_http, failure_callback=fail, **kwargs)
                finally:
                    arm_counts[name] = guard.snapshot()["actual_http_calls"] - before_count
                dispatches[name] = report["execution_metadata"].get("model_generator_dispatches")
                parity[name] = "MISSING_FROZEN"
                pilot, market = _verify_inputs(root, registry)
                with network_boundary("frozen"):
                    frozen = replay(pilot, market, arm / "frozen", mode="frozen", tape_path=tape, **kwargs)
                require(report["result_hash"] == frozen["result_hash"], "fresh_frozen_result_mismatch")
                for produced in (report, frozen):
                    require(produced["result_hash"] == digest({k: v for k, v in produced.items()
                        if k not in {"result_hash", "execution_metadata"}}), "fresh_frozen_report_hash_mismatch")
                for filename in ("exchange.json", "equity.json"):
                    require(json.loads((arm / "fresh" / filename).read_text())
                            == json.loads((arm / "frozen" / filename).read_text()), "fresh_frozen_artifact_mismatch")
                require(frozen["execution_metadata"]["actual_model_calls"] == 0, "frozen_actual_http_calls")
                parity[name] = ("VERIFIED_FAILED_ZERO_MODEL_CALLS" if report["execution_status"] == "STOPPED"
                                else "VERIFIED_ZERO_MODEL_CALLS")
                require(report["execution_metadata"]["actual_model_calls"] == arm_counts[name],
                        "report_http_count_mismatch")
                require(report["execution_status"] == "COMPLETE" and report["failure_evidence"] is None,
                        "fresh_arm_stopped")
                for mode in ("fresh", "frozen"):
                    verified, exchange, _, _, _ = verify_run(arm / mode, variant, registry,
                        frozen=mode == "frozen", contract_verifier=verify_calculated_registry_contract)
                    verify_calculated_economics(verified, exchange, registry)
                    verify_calculated_completion(verified, exchange)
            comparison = compare_calculated(root)
            require(comparison["comparison_complete"], "stage_one_comparison_incomplete")
            require(sum(arm_counts.values()) == guard.snapshot()["actual_http_calls"], "study_http_count_mismatch")
            status = "COMPLETE_DEVELOPMENT_SMOKE"
        except BaseException as error:
            guard.stop("coordinator_failure")
            reason = str(error)
            if re.fullmatch("[a-z][a-z0-9_]{1,100}", reason) is None:
                reason = "coordinator_external_error"
            coordinator_error = dict(error_type=type(error).__name__, reason=reason)
            try:
                record(dict(kind="COORDINATOR_ERROR", **coordinator_error))
            except Exception:
                diagnostic_record_status = "FAILED"
            try:
                comparison = compare_calculated(root)
            except Exception:
                comparison, comparison_status = None, "UNAVAILABLE"
            status = "STOPPED"
        result = dict(status=status, call_guard=guard.snapshot(), comparison=comparison,
                      comparison_status=comparison_status, coordinator_error=coordinator_error,
                      diagnostic_record_status=diagnostic_record_status,
                      per_arm_actual_http_calls=arm_counts, per_arm_model_generator_dispatches=dispatches,
                      per_arm_frozen_parity=parity,
                      profitability_proven=False, automatic_promotion=False, next_stage_automatic=False)
        write_new(root / "study_result.json", result)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    prep = subs.add_parser("prepare")
    prep.add_argument("--root", type=Path, required=True)
    prep.add_argument("--market", type=Path, required=True)
    prep.add_argument("--observed", type=Path, required=True)
    prep.add_argument("--observed-sha256", required=True)
    prep.add_argument("--source-reference", required=True)
    prep.add_argument("--initial-equity", type=float, default=10000.)
    prep.add_argument("--stage", type=int, choices=(1, 2), default=1)
    prep.add_argument("--protocol", choices=tuple(PROTOCOLS), default="legacy")
    prep.add_argument("--predecessor-root", type=Path)
    prep.add_argument("--predecessor-registry-sha256")
    execute = subs.add_parser("run")
    execute.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare(args.root, args.market, args.observed, args.observed_sha256,
                         args.source_reference, initial_equity=args.initial_equity, stage=args.stage,
                         predecessor_root=args.predecessor_root,
                         predecessor_registry_sha256=args.predecessor_registry_sha256, protocol=args.protocol)
        print(json.dumps(dict(status="PREPARED_ZERO_CALLS", registry_sha256=sha(args.root / "registry.json"),
                              registry_canonical_sha256=digest(result))))
    else:
        result = run(args.root)
        print(json.dumps(dict(status=result["status"], call_guard=result["call_guard"])))


if __name__ == "__main__":
    main()
