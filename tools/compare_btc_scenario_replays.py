"""Read-only accounting and reproducibility checks for preregistered simulated arms."""
from __future__ import annotations

import argparse
import hashlib
import json
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


def verify_run(directory, variant, registry, *, frozen=False):
    report, exchange, curve = (read(directory / name) for name in
                               ("report.json", "exchange.json", "equity.json"))
    require(isinstance(report, dict) and isinstance(exchange, dict) and isinstance(curve, list),
            "invalid_artifact_shape")
    require(report["simulated_only"] is True and exchange["simulation"] is True,
            "simulation_required")
    require(report["result_hash"] == digest({k: v for k, v in report.items()
            if k not in {"result_hash", "execution_metadata"}}), "result_hash_mismatch")
    require(report["exchange_hash"] == digest(exchange), "exchange_hash_mismatch")
    contract = report["contract"]
    verify_registry_contract(contract, registry)
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


def compare(root):
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
            report, exchange, curve, cohort, accounting = verify_run(fresh, variant, registry)
            cohorts.append(cohort)
            row.update(status="VERIFIED_FRESH", accounting=accounting,
                       reported_metrics=report["economic"], result_hash=report["result_hash"],
                       frozen_status="MISSING")
            if (frozen / "report.json").exists():
                replay, replay_exchange, replay_curve, _, _ = verify_run(frozen, variant, registry, frozen=True)
                require(report["result_hash"] == replay["result_hash"]
                        and exchange == replay_exchange and curve == replay_curve,
                        "fresh_frozen_mismatch")
                row.update(status="VERIFIED", frozen_status="VERIFIED_ZERO_MODEL_CALLS")
            row["insufficiency_reasons"] = ["NO_FORWARD_PROFITABILITY_EVIDENCE"]
            if report["economic"]["completed_scenarios"] < 60:
                row["insufficiency_reasons"].append("CLOSED_SCENARIOS_LT_60")
        except (ValueError, KeyError, TypeError, OSError, ArithmeticError) as exc:
            row.update(status="INVALID", error=str(exc))
    matching = bool(cohorts) and all(c == cohorts[0] for c in cohorts)
    return {"schema": 1, "registry_hash": digest(registry), "arms": rows,
            "cohort_contracts_match": matching,
            "comparison_complete": matching and all(r["status"] == "VERIFIED" for r in rows),
            "verdict": "PREREGISTER_REPLAY", "profitability_proven": False,
            "automatic_promotion": False, "postprocessor_actual_order_calls": 0,
            "mfe_mae": "NOT_COMPUTED_ASSUMED_INTRABAR_PATH_NOT_OBSERVED_TICKS",
            "largest_winner_removed": "REPORTED_METRIC_NOT_INDEPENDENTLY_RECONSTRUCTED",
            "closed_scenario_statistics": "REPORTED_NOT_INDEPENDENTLY_RECONSTRUCTED",
            "unrealized_basis": "REPORTED_TERMINAL_EQUITY_MINUS_CASH_NOT_INDEPENDENT_MARK_VALUATION",
            "limits": ["SIMULATED_NOT_ACTUAL_PNL", "NO_UNTOUCHED_HOLDOUT",
                       "NO_FORWARD_PROFITABILITY_EVIDENCE", "OHLC_PATH_ASSUMED",
                       "FUNDING_REPORTED_SEPARATELY_NOT_COST_MULTIPLIED"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    require(not args.output.exists(), "never_overwrite_comparison")
    result = compare(args.root)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)


if __name__ == "__main__":
    main()
