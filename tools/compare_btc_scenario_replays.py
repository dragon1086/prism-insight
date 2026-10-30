"""Read-only accounting and reproducibility checks for preregistered simulated arms."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from collections import Counter
from decimal import Decimal
from datetime import datetime
from pathlib import Path


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def number(value):
    require(not isinstance(value, bool), "boolean_number")
    result = Decimal(str(value))
    require(result.is_finite(), "nonfinite_number")
    return result


def equal(left, right, reason):
    require(abs(number(left) - number(right)) <= Decimal("0.000001"), reason)


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def verify_registry_contract(contract, registry):
    require(isinstance(contract, dict), "invalid_contract")
    for key in ("start", "end"):
        timestamp = datetime.fromisoformat(registry[key].replace("Z", "+00:00"))
        require(timestamp.tzinfo is not None and timestamp.utcoffset().total_seconds() == 0,
                "registry_utc_required")
        require(contract[key + "_ms"] == int(timestamp.timestamp() * 1000), "registry_period_mismatch")
    equal(contract["initial_equity"], registry["initial_equity"], "registry_equity_mismatch")
    require(contract["model"] == registry["model"] and contract["effort"] == registry["reasoning"],
            "registry_model_mismatch")
    require(registry["service_tier"] == "priority" and contract["tier"] == "fast",
            "registry_service_tier_mismatch")
    require(registry["source_pin_reference"] == registry["source_reference"], "source_pin_reference_mismatch")
    require(contract["data_hash"] == registry["expected_data_hash"], "registry_data_mismatch")
    require(contract["prompt_hash"] == registry["expected_prompt_hash"], "registry_prompt_mismatch")
    require(digest(contract["source_hashes"]) == registry["expected_source_hashes_sha256"],
            "registry_sources_mismatch")
    require(contract["runtime_versions"] == registry["expected_runtime_versions"], "registry_runtime_mismatch")
    require(digest({k: v for k, v in contract.items() if k not in {"path", "costs"}})
            == registry["expected_contract_common_hash"], "registry_contract_mismatch")


# These lists are part of the versioned comparison protocol, not arbitrary
# registry-controlled exclusions. Unknown fields fail closed, even when pinned.
CALCULATED_COMMON_FIELDS = (
    "schema", "kind", "decision_origin", "data_hash", "initial_state",
    "identity_scheme", "assembled_prompt_hash_basis", "request_audit",
    "start_ms", "end_ms", "initial_equity", "model", "effort", "tier",
    "source_hashes", "runtime_versions", "latencies", "decision_interval_ms",
    "protection_interval_ms", "terminal_policy", "liquidity",
    "economic_clock_contract", "observed_data",
)
CALCULATED_ARM_FIELDS = (
    "policy_profile", "runtime_flags", "context_contracts", "assembled_prompt_hashes",
    "prompt_hash", "review_binding", "phase_contracts",
)
CALCULATED_VARIANT_FIELDS = ("path", "costs")


def _exact_fields(value, fields, reason):
    require(isinstance(value, dict) and set(value) == set(fields), reason)


def verify_calculated_registry_contract(contract, registry, variant):
    """Check every field against a frozen shared or arm-specific expectation."""
    require(type(registry.get("schema")) is int and registry["schema"] == 2,
            "calculated_registry_schema_required")
    spec = registry["contract_comparison"]
    _exact_fields(spec, ("common_fields", "arm_fields", "variant_fields",
                        "expected_common_contract", "expected_arm_contracts",
                        "expected_variant_contracts"), "comparison_registry_fields")
    for key, fields in (("common_fields", CALCULATED_COMMON_FIELDS),
                        ("arm_fields", CALCULATED_ARM_FIELDS),
                        ("variant_fields", CALCULATED_VARIANT_FIELDS)):
        require(isinstance(spec[key], list) and len(spec[key]) == len(fields)
                and all(isinstance(item, str) for item in spec[key])
                and set(spec[key]) == set(fields), "comparison_field_allowlist")
    _exact_fields(contract, (*CALCULATED_COMMON_FIELDS, *CALCULATED_ARM_FIELDS,
                             *CALCULATED_VARIANT_FIELDS), "unknown_or_missing_contract_field")
    names = [item["name"] for item in registry["variants"]]
    for key in ("expected_arm_contracts", "expected_variant_contracts"):
        _exact_fields(spec[key], names, "unregistered_contract_arm")
    expected = {}
    for fields, value in (
        (CALCULATED_COMMON_FIELDS, spec["expected_common_contract"]),
        (CALCULATED_ARM_FIELDS, spec["expected_arm_contracts"][variant["name"]]),
        (CALCULATED_VARIANT_FIELDS, spec["expected_variant_contracts"][variant["name"]]),
    ):
        _exact_fields(value, fields, "incomplete_registered_contract")
        require(digest({key: contract[key] for key in fields}) == digest(value),
                "registered_contract_field_mismatch")
        expected.update(value)
    require(type(contract["schema"]) is int and contract["schema"] == 4,
            "calculated_contract_schema_required")
    require(contract["policy_profile"] in {"single-pass-economic-v4", "calculated-economic-v4",
            "allocation-single-economic-v1", "allocation-calculated-economic-v1",
            "economic-reject-single-v1", "economic-reject-calculated-v1", "economic-reject-goal-v2"},
            "unregistered_economic_profile")
    require(contract["economic_clock_contract"] == "NATIVE_MONOTONIC_INTEGER_MS_FULL_TICK_V1",
            "economic_clock_contract_mismatch")
    expected_identity = ("EXACT_REPLAY_UUID5_CALLSITE_PID0_V2"
                         if contract["policy_profile"] == "economic-reject-goal-v2"
                         else "EXACT_REPLAY_UUID5_MODULE_CALLSITE_SEQUENCE_V1")
    require(contract["identity_scheme"] == expected_identity,
            "economic_identity_contract_mismatch")
    observed = contract["observed_data"]
    require(isinstance(observed, dict) and observed.get("mode") == "observed",
            "observed_primary_analysis_required")
    manifest = observed["provider_manifest"]
    require(isinstance(manifest, dict) and manifest.get("input_coverage_eligible") is True
            and type(manifest.get("missing_slots")) is int
            and manifest.get("missing_slots") == 0 and manifest.get("allow_gaps") is False,
            "observed_coverage_ineligible")
    # Reuse all legacy provenance guards. Only the formerly global prompt and
    # combined contract pins become exact registered per-arm pins in this schema.
    arm_registry = dict(registry, expected_prompt_hash=expected["prompt_hash"],
                        expected_contract_common_hash=digest(
                            {key: value for key, value in expected.items()
                             if key not in CALCULATED_VARIANT_FIELDS}))
    verify_registry_contract(contract, arm_registry)


def verify_terminal_mark(report, exchange):
    """Separate Decimal valuation; do not infer open PnL from reported equity."""
    mark = number(report["economic"]["terminal_mark_price"])
    require(mark > 0, "invalid_terminal_mark_price")
    unrealized = number(exchange["position"]) * (mark - number(exchange["average_price"]))
    equal(exchange["equity"], number(exchange["cash"]) + unrealized,
          "terminal_mark_equity_mismatch")


def verify_calculated_economics(report, exchange, registry):
    """Audit fees, each realized cash flow and complete funding from pinned inputs.

    Settlement precedes fills at an identical timestamp, as in the exchange.
    This is weighted-average inventory accounting, not a FIFO trade statistic.
    """
    inputs = registry["economic_oracle_inputs"]
    _exact_fields(inputs, ("terminal_mark_price", "funding_events"), "economic_oracle_fields")
    equal(report["economic"]["terminal_mark_price"], inputs["terminal_mark_price"],
          "unregistered_terminal_mark")
    verify_terminal_mark(report, exchange)
    executions = exchange["executions"]
    trades = {row["tradeId"]: row for row in exchange["transactions"] if row["type"] == "TRADE"}
    position = average = Decimal(0)
    for row in executions:
        require(type(row["isMaker"]) is bool, "execution_maker_flag_required")
        quantity, price = number(row["execQty"]), number(row["execPrice"])
        rate = number(report["contract"]["costs"]["maker_fee" if row["isMaker"] else "taker_fee"])
        fee = (quantity * price * rate).quantize(Decimal(".00000001"))
        equal(row["execFee"], fee, "execution_fee_rate_mismatch")
        signed = quantity if row["side"] == "Buy" else -quantity
        old = position
        closing = min(abs(old), quantity) if old * signed < 0 else Decimal(0)
        gross = (closing * (price - average) * (1 if old > 0 else -1)).quantize(Decimal(".00000001"))
        equal(trades[row["execId"]]["cashFlow"], gross, "individual_realized_cashflow_mismatch")
        position += signed
        if old == 0 or old * signed > 0:
            average = (abs(old) * average + quantity * price) / abs(position)
        elif position == 0:
            average = Decimal(0)
        elif old * position < 0:
            average = price
    events = inputs["funding_events"]
    require(isinstance(events, list), "funding_schedule_required")
    expected, position, index, previous = {}, Decimal(0), 0, report["contract"]["start_ms"]
    for event in events:
        _exact_fields(event, ("timestamp_ms", "rate", "mark_price"), "funding_event_fields")
        at = event["timestamp_ms"]
        require(type(at) is int and previous < at <= report["contract"]["end_ms"],
                "funding_schedule_time_order")
        previous = at
        while index < len(executions) and int(executions[index]["execTime"]) < at:
            row = executions[index]
            position += number(row["execQty"]) * (1 if row["side"] == "Buy" else -1)
            index += 1
        mark, rate = number(event["mark_price"]), number(event["rate"])
        require(mark > 0, "invalid_funding_mark")
        if position:
            expected[at] = ((-position * mark * rate).quantize(Decimal(".00000001")), abs(position))
    settlements = [row for row in exchange["transactions"] if row["type"] == "SETTLEMENT"]
    times = [int(row["transactionTime"]) for row in settlements]
    require(len(times) == len(set(times)) and set(times) == set(expected),
            "funding_schedule_coverage_mismatch")
    for row in settlements:
        amount, quantity = expected[int(row["transactionTime"])]
        equal(row["funding"], amount, "funding_oracle_mismatch")
        equal(row["qty"], quantity, "funding_inventory_mismatch")


def compare_calculated(root):
    """Opt-in economic arms. Legacy callers retain their original hash contract."""
    return compare(root, calculated=True)


def verify_calculated_completion(report, exchange):
    """A drained stopped run is diagnostic evidence, never an eligible arm."""
    coverage = report["coverage"]
    _exact_fields(coverage, ("planned_slots", "processed_slots", "model_free_drain_status",
                            "model_free_drain_end_ms", "final_economic_time_ms", "eligible"),
                  "economic_coverage_fields")
    require(report["execution_status"] == "COMPLETE" and report["failure_evidence"] is None,
            "economic_execution_not_complete")
    require(coverage["eligible"] is True, "economic_coverage_ineligible")
    for key in ("planned_slots", "processed_slots"):
        require(type(coverage[key]) is int and coverage[key] == report["decisions"],
                "economic_slot_coverage_mismatch")
    require(sum(report["decision_outcomes"].values()) == coverage["processed_slots"],
            "economic_outcome_coverage_mismatch")
    end = report["contract"]["end_ms"]
    require(coverage["model_free_drain_status"] == "COMPLETE"
            and type(coverage["model_free_drain_end_ms"]) is int
            and coverage["model_free_drain_end_ms"] == end
            and type(coverage["final_economic_time_ms"]) is int
            and coverage["final_economic_time_ms"] == exchange["timestamp_ms"] == end,
            "economic_drain_incomplete")
    for key in ("model_failures", "input_preparation_failures"):
        require(type(report[key]) is int and report[key] == 0, "economic_failure_count_nonzero")
    require(not ({"MODEL_FAILURES", "INPUT_PREPARATION_FAILURES", "STOPPED_TECHNICAL_FAILURE",
                  "OBSERVED_INPUT_COVERAGE_INELIGIBLE"}
                 & set(report["readiness"]["insufficiency_reasons"])), "economic_failure_warning")
    require(report["decision_outcomes"].get("INPUT_GAP", 0) == 0, "economic_input_gap")
    protection = report["protection_outcomes"]
    require(isinstance(protection, dict)
            and all(type(value) is int and value >= 0 for value in protection.values())
            and protection.get("failed", 0) == 0, "economic_protection_failure")


def verify_economic_rejections(directory, report):
    """Independently recount durable evidence, including mixed-candidate rejections."""
    profiles = {"economic-reject-single-v1", "economic-reject-calculated-v1", "economic-reject-goal-v2"}
    if report["contract"].get("policy_profile") not in profiles:
        require("economic_rejections" not in report, "unexpected_rejection_protocol")
        return
    root = str(Path(__file__).resolve().parents[1])
    if root not in sys.path:
        sys.path.insert(0, root)
    from observability.review_storage import review_text
    with sqlite3.connect((directory / "replay.sqlite").resolve().as_uri() + "?mode=ro", uri=True) as conn:
        from utils.btc_review_event_references import read_event
        events = [(slot, json.loads(read_event(body, conn, slot, sequence, decode_legacy=review_text)))
                  for slot, sequence, body in conn.execute(
            "SELECT slot,sequence,body FROM llm_scenario_review_events ORDER BY slot,sequence")]
        decisions = {slot: (proposal, context) for slot, proposal, context in conn.execute(
            "SELECT slot,proposal,context FROM llm_scenario_decisions")}
        intent_inputs = {json.loads(payload)["input_id"] for (payload,) in conn.execute(
            "SELECT payload FROM llm_scenario_intents")}
        consumed_slots = {slot for (slot,) in conn.execute(
            "SELECT slot FROM llm_scenario_recovery_events WHERE kind='PERMIT_CONSUMED'")}
    slots, candidates = set(), {}
    for slot, event in events:
        if event["kind"] == "RESEARCH_REJECTION":
            body = event["body"]
            require(body["disposition"] == "REJECTED_NO_ACTION"
                    and body["rejection_policy"] == "preauthority-economic-reject-v1"
                    and body["executable"] is False and body["orders_submitted"] == 0
                    and event["sha256"] == hashlib.sha256(json.dumps(body, ensure_ascii=False,
                        sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest(),
                    "rejection_event_invalid")
            slots.add(slot)
            bundle = body["candidates"]
        elif event["kind"] == "CALCULATED_CANDIDATES":
            bundle = event["body"]
        else:
            continue
        for candidate in bundle["candidates"]:
            if candidate.get("rejection_type") == "PREAUTHORITY_ECONOMIC":
                require(candidate["eligible"] is False and isinstance(candidate["rejection"], str)
                        and candidate["rejection"], "economic_rejection_candidate_invalid")
                key = (slot, candidate["id"])
                require(key not in candidates or candidates[key] == candidate["rejection"],
                        "economic_rejection_candidate_conflict")
                candidates[key] = candidate["rejection"]
    expected = dict(policy="preauthority-economic-reject-v1", rejected_ticks=len(slots),
                    rejected_candidates=len(candidates), codes=dict(Counter(candidates.values())),
                    durable_events_sha256=digest(events))
    require(report.get("economic_rejections") == expected, "economic_rejection_evidence_mismatch")
    require(len(slots) == report["decision_outcomes"].get("rejected_no_action", 0),
            "economic_rejection_tick_mismatch")
    for slot in slots:
        require(slot in decisions and decisions[slot][0] is None
                and slot not in consumed_slots, "economic_rejection_authority_mutation")
        context = json.loads(decisions[slot][1]) if decisions[slot][1] is not None else None
        require(isinstance(context, dict) and isinstance(context.get("input_id"), str)
                and bool(context["input_id"]), "economic_rejection_context_missing")
        require(context["input_id"] not in intent_inputs, "economic_rejection_authority_mutation")


def verify_run(directory, variant, registry, *, frozen=False, contract_verifier=None):
    report, exchange, curve = (read(directory / name) for name in
                               ("report.json", "exchange.json", "equity.json"))
    require(isinstance(report, dict) and isinstance(exchange, dict) and isinstance(curve, list),
            "invalid_artifact_shape")
    require(report["simulated_only"] is True and exchange["simulation"] is True,
            "simulation_required")
    readiness = report["readiness"]
    require(isinstance(readiness, dict) and readiness.get("profitability_proven") is False
            and isinstance(readiness.get("insufficiency_reasons"), list)
            and all(isinstance(reason, str) for reason in readiness["insufficiency_reasons"]),
            "research_readiness_contract")
    require(report["result_hash"] == digest({k: v for k, v in report.items()
            if k not in {"result_hash", "execution_metadata"}}), "result_hash_mismatch")
    require(report["exchange_hash"] == digest(exchange), "exchange_hash_mismatch")
    contract = report["contract"]
    verify_economic_rejections(directory, report)
    if contract_verifier is None:
        verify_registry_contract(contract, registry)
    else:
        contract_verifier(contract, registry, variant)
    require(contract["kind"] == "SIMULATED_LLM_SCENARIO" and contract["decision_origin"] == "luna"
            and report["mode"] == "HISTORICAL_LLM_RESEARCH", "historical_luna_required")
    duration = contract["end_ms"] - contract["start_ms"]
    require(duration > 0 and duration % 300000 == 0 and contract["decision_interval_ms"] == 300000,
            "invalid_decision_window")
    require(type(report["decisions"]) is int and report["decisions"] == duration // 300000,
            "decision_coverage_mismatch")
    outcomes = report["decision_outcomes"]
    require(isinstance(outcomes, dict) and all(type(v) is int and v >= 0 for v in outcomes.values())
            and sum(outcomes.values()) == report["decisions"], "decision_outcome_coverage_mismatch")
    require(exchange["timestamp_ms"] == contract["end_ms"], "terminal_exchange_time_mismatch")
    metadata = report["execution_metadata"]
    require(isinstance(metadata, dict) and type(metadata["actual_model_calls"]) is int
            and metadata["actual_model_calls"] >= 0, "invalid_execution_metadata")
    require(metadata["mode"] == ("frozen" if frozen else "fresh"), "execution_mode_mismatch")
    require(all(contract.get(key) for key in ("data_hash", "prompt_hash", "source_hashes")),
            "missing_provenance")
    require(report["contract_hash"] == digest(contract), "contract_hash_mismatch")
    require(contract["path"] == variant["path"], "unregistered_path")
    multiplier = number(variant["cost_multiplier"])
    require(multiplier > 0, "invalid_cost_multiplier")
    for key, base in (("maker_fee", ".0002"), ("taker_fee", ".00055"), ("slippage", ".0005")):
        require(abs(number(contract["costs"][key]) - number(base) * multiplier)
                <= Decimal("1e-15"), "unregistered_cost")
    require(set(contract["costs"]) == {"maker_fee", "taker_fee", "slippage", "spread", "participation"},
            "unregistered_cost_fields")
    require(number(contract["costs"]["spread"]) == Decimal(".0001")
            and number(contract["costs"]["participation"]) == Decimal(".01"), "unregistered_fixed_cost")
    if frozen:
        require(report["execution_metadata"]["mode"] == "frozen"
                and report["execution_metadata"]["actual_model_calls"] == 0,
                "frozen_actual_model_calls")
    executions = exchange["executions"]
    transactions = exchange["transactions"]
    ids = [row["execId"] for row in executions]
    require(all(ids) and len(ids) == len(set(ids)), "duplicate_execution_id")
    tids = [row["id"] for row in transactions]
    require(all(tids) and len(tids) == len(set(tids)), "duplicate_transaction_id")
    trades = [row for row in transactions if row["type"] == "TRADE"]
    require(len(trades) == len(executions) and {r["tradeId"] for r in trades} == set(ids),
            "trade_execution_bijection")
    by_id = {row["execId"]: row for row in executions}
    initial = number(contract["initial_equity"])
    require(initial > 0, "invalid_initial_equity")
    position = average = reconstructed_gross = Decimal(0)
    last_time = contract["start_ms"]
    for execution in executions:
        require(number(execution["execQty"]) > 0 and number(execution["execPrice"]) > 0,
                "invalid_execution_quantity_or_price")
        require(contract["start_ms"] <= int(execution["execTime"]) <= contract["end_ms"],
                "execution_outside_window")
        require(int(execution["execTime"]) >= last_time, "execution_time_order")
        last_time = int(execution["execTime"])
        require(execution["side"] in {"Buy", "Sell"}, "invalid_execution_side")
        qty, price = number(execution["execQty"]), number(execution["execPrice"])
        signed = qty * (1 if execution["side"] == "Buy" else -1)
        old = position
        closing = min(abs(old), qty) if old * signed < 0 else Decimal(0)
        reconstructed_gross += (closing * (price - average) *
                                (1 if old > 0 else -1)).quantize(Decimal(".00000001"))
        position += signed
        if old == 0 or old * signed > 0:
            average = (abs(old) * average + qty * price) / abs(position)
        elif position == 0:
            average = Decimal(0)
        elif old * position < 0:
            average = price
    equal(position, exchange["position"], "execution_position_mismatch")
    equal(average, exchange["average_price"], "execution_average_mismatch")
    for trade in trades:
        execution = by_id[trade["tradeId"]]
        require(trade["orderId"] == execution["orderId"]
                and int(trade["transactionTime"]) == int(execution["execTime"]),
                "trade_execution_identity")
        equal(trade["fee"], execution["execFee"], "trade_execution_fee")
        equal(trade["funding"], 0, "trade_funding_mixed")
        equal(trade["change"], number(trade["cashFlow"]) - number(trade["fee"]),
              "trade_change_mismatch")
    for row in transactions:
        require(contract["start_ms"] <= int(row["transactionTime"]) <= contract["end_ms"],
                "transaction_outside_window")
        require(row["type"] in {"TRADE", "SETTLEMENT"}, "unknown_transaction_type")
        if row["type"] == "SETTLEMENT":
            equal(row["fee"], 0, "funding_fee_mixed")
            equal(row["cashFlow"], 0, "funding_cashflow_mixed")
            equal(row["change"], row["funding"], "funding_change_mismatch")
    fees = sum((number(row["execFee"]) for row in executions), Decimal(0))
    gross = sum((number(row["cashFlow"]) for row in trades), Decimal(0))
    equal(gross, reconstructed_gross, "execution_gross_mismatch")
    funding = sum((number(row["funding"]) for row in transactions), Decimal(0))
    unrealized = number(exchange["equity"]) - number(exchange["cash"])
    economic = report["economic"]
    equal(exchange["cash"], initial + gross - fees + funding, "cash_identity")
    equal(economic["final_equity"], exchange["equity"], "final_equity_mismatch")
    equal(economic["net_change"], gross - fees + funding + unrealized, "net_identity")
    equal(economic["fees"], fees, "fees_mismatch")
    equal(economic["funding_net"], funding, "funding_mismatch")
    equal(economic["open_quantity"], exchange["position"], "position_mismatch")
    require(bool(curve), "missing_equity_curve")
    require(all(isinstance(point, list) and len(point) == 2 and type(point[0]) is int
                for point in curve), "invalid_equity_curve_shape")
    require(curve[-1][0] == contract["end_ms"], "terminal_curve_time_mismatch")
    peak, drawdown, previous = initial, Decimal(0), contract["start_ms"] - 1
    for timestamp, value in curve:
        require(previous <= timestamp <= contract["end_ms"], "equity_time_order")
        previous = timestamp
        value = number(value)
        require(value > 0, "nonpositive_equity")
        peak = max(peak, value)
        drawdown = max(drawdown, (peak - value) / peak * 100)
    equal(curve[-1][1], exchange["equity"], "curve_final_equity")
    equal(economic["max_drawdown_pct"], drawdown, "drawdown_mismatch")
    normalized = {k: v for k, v in contract.items() if k not in {"path", "costs"}}
    normalized["costs"] = dict(contract["costs"])
    for key in ("maker_fee", "taker_fee", "slippage"):
        normalized["costs"].pop(key)
    return report, exchange, curve, normalized, {
        "gross_realized": str(gross), "fees_counted_once": str(fees),
        "funding_net": str(funding), "unrealized": str(unrealized),
        "net_equity_change": str(gross - fees + funding + unrealized),
    }


def compare(root, *, calculated=False):
    root = Path(root).resolve()
    registry = read(root / "registry.json")
    variants = registry["variants"]
    names = [v["name"] for v in variants]
    require(bool(names) and len(names) == len(set(names)), "unique_registered_arms_required")
    rows, cohorts = [], []
    for variant in variants:
        name = variant["name"]
        require(Path(name).name == name and name not in {".", ".."}, "invalid_arm_name")
        row = {"name": name, "label": "SIMULATED", "status": "MISSING"}
        rows.append(row)
        arm = root / name
        fresh = arm / "fresh" if (arm / "fresh").is_dir() else arm
        frozen = arm / "frozen"
        if not (fresh / "report.json").exists():
            continue
        try:
            if calculated:
                diagnostic = read(fresh / "report.json")
                if isinstance(diagnostic, dict):
                    row["diagnostic_only"] = True
                    row["unverified_reported_metrics"] = diagnostic.get("economic")
                    row["execution_status"] = diagnostic.get("execution_status")
                    row["failure_evidence"] = diagnostic.get("failure_evidence")
                    row["coverage"] = diagnostic.get("coverage")
            verifier = verify_calculated_registry_contract if calculated else None
            report, exchange, curve, cohort, accounting = verify_run(
                fresh, variant, registry, contract_verifier=verifier)
            if calculated:
                verify_calculated_economics(report, exchange, registry)
                verify_calculated_completion(report, exchange)
                cohort = {key: report['contract'][key] for key in CALCULATED_COMMON_FIELDS}
                row.pop("unverified_reported_metrics", None)
            cohorts.append(cohort)
            row.update(status="VERIFIED_FRESH", accounting=accounting,
                       reported_metrics=report["economic"], result_hash=report["result_hash"],
                       frozen_status="MISSING")
            if (frozen / "report.json").exists():
                replay, replay_exchange, replay_curve, _, _ = verify_run(
                    frozen, variant, registry, frozen=True, contract_verifier=verifier)
                if calculated:
                    verify_calculated_economics(replay, replay_exchange, registry)
                    verify_calculated_completion(replay, replay_exchange)
                require(report["result_hash"] == replay["result_hash"]
                        and exchange == replay_exchange and curve == replay_curve,
                        "fresh_frozen_mismatch")
                row.update(status="VERIFIED", frozen_status="VERIFIED_ZERO_MODEL_CALLS")
                if calculated:
                    row["diagnostic_only"] = False
            row["insufficiency_reasons"] = sorted(set(report["readiness"]["insufficiency_reasons"]
                                                      + ["NO_FORWARD_PROFITABILITY_EVIDENCE"]))
            if (report["economic"]["completed_scenarios"] < 60
                    and "CLOSED_SCENARIOS_LT_60" not in row["insufficiency_reasons"]):
                row["insufficiency_reasons"].append("CLOSED_SCENARIOS_LT_60")
        except (ValueError, KeyError, TypeError, OSError, ArithmeticError) as exc:
            row.update(status="INVALID", error=str(exc))
    matching = bool(cohorts) and all(c == cohorts[0] for c in cohorts)
    return {"schema": 1, "registry_hash": digest(registry), "arms": rows,
            "postprocessor_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "cohort_contracts_match": matching,
            "comparison_complete": matching and all(r["status"] == "VERIFIED" for r in rows),
            "verdict": "PREREGISTER_REPLAY", "profitability_proven": False,
            "automatic_promotion": False, "postprocessor_actual_order_calls": 0,
            "mfe_mae": "NOT_COMPUTED_ASSUMED_INTRABAR_PATH_NOT_OBSERVED_TICKS",
            "largest_winner_removed": "REPORTED_METRIC_NOT_INDEPENDENTLY_RECONSTRUCTED",
            "closed_scenario_statistics": "REPORTED_NOT_INDEPENDENTLY_RECONSTRUCTED",
            "unrealized_basis": ("DECIMAL_POSITION_TIMES_TERMINAL_MARK_MINUS_AVERAGE"
                                 if calculated else
                                 "REPORTED_TERMINAL_EQUITY_MINUS_CASH_NOT_INDEPENDENT_MARK_VALUATION"),
            "limits": ["SIMULATED_NOT_ACTUAL_PNL", "NO_UNTOUCHED_HOLDOUT",
                       "NO_FORWARD_PROFITABILITY_EVIDENCE", "OHLC_PATH_ASSUMED",
                       "FUNDING_REPORTED_SEPARATELY_NOT_COST_MULTIPLIED"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--calculated-economic", action="store_true",
                        help="Require the strict preregistered schema-4 arm contract")
    args = parser.parse_args()
    require(not args.output.exists(), "never_overwrite_comparison")
    result = compare_calculated(args.root) if args.calculated_economic else compare(args.root)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)


if __name__ == "__main__":
    main()
