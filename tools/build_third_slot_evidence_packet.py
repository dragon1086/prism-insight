#!/usr/bin/env python3
"""Build deterministic evidence for the KR weak-regime third-slot SHADOW."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from observability.third_slot_shadow import (  # noqa: E402
    EVALUATION_EVENT,
    HORIZONS,
    OUTCOME_EVENT,
    POLICY_VERSION,
)

PACKET_SCHEMA_VERSION = 1
ANALYSIS_CONTRACT_VERSION = "kr-third-slot-shadow-evidence-v1"
MIN_DATES = 20
MIN_MATURED_10D = 30


def _canonical(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _median(values: list[float]) -> float | None:
    return round(float(statistics.median(values)), 6) if values else None


def _deduplicate(
    raw_events: Iterable[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], int, int]:
    by_id = {}
    raw_supported = 0
    duplicates = 0
    for raw in raw_events:
        event = dict(raw)
        if event.get("event_type") not in {EVALUATION_EVENT, OUTCOME_EVENT}:
            continue
        raw_supported += 1
        event_id = str(event.get("event_id") or "")
        if not event_id:
            continue
        existing = by_id.get(event_id)
        if existing is not None:
            duplicates += 1
            if (str(event.get("timestamp") or ""), _canonical(event)) <= (
                str(existing.get("timestamp") or ""),
                _canonical(existing),
            ):
                continue
        by_id[event_id] = event
    return list(by_id.values()), raw_supported, duplicates


def _valid_evaluation(attributes: Mapping[str, Any]) -> bool:
    candidates = attributes.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != 3:
        return False
    roles = [str(row.get("role") or "") for row in candidates]
    ranks = [row.get("rank") for row in candidates]
    tickers = [str(row.get("ticker") or "") for row in candidates]
    prices = [_number(row.get("screening_price")) for row in candidates]
    return (
        roles == ["LIVE_SELECTED", "LIVE_SELECTED", "SHADOW_THIRD"]
        and ranks == [1, 2, 3]
        and all(tickers)
        and len(set(tickers)) == 3
        and all(price is not None and price > 0 for price in prices)
        and attributes.get("trading_impact") == "none"
    )


def _horizon_metrics(
    evaluations: dict[str, dict[str, Any]],
    outcomes: dict[tuple[str, str, int], dict[str, Any]],
    horizon: int,
) -> dict[str, Any]:
    rows = []
    for experiment_ref, evaluation in evaluations.items():
        candidates = (evaluation.get("attributes") or {}).get("candidates") or []
        linked = []
        for candidate in candidates:
            outcome = outcomes.get(
                (experiment_ref, str(candidate.get("ticker") or ""), horizon)
            )
            if outcome is None:
                break
            linked.append(outcome.get("attributes") or {})
        if len(linked) != 3:
            continue
        returns = [_number(row.get("return_pct")) for row in linked]
        mfe = [_number(row.get("mfe_pct")) for row in linked]
        mae = [_number(row.get("mae_pct")) for row in linked]
        if any(value is None for value in returns + mfe + mae):
            continue
        live_return = (returns[0] + returns[1]) / 2.0
        live_mfe = (mfe[0] + mfe[1]) / 2.0
        live_mae = (mae[0] + mae[1]) / 2.0
        rows.append(
            {
                "third_return": returns[2],
                "live_return": live_return,
                "delta": returns[2] - live_return,
                "third_mfe": mfe[2],
                "live_mfe": live_mfe,
                "third_mae": mae[2],
                "live_mae": live_mae,
            }
        )
    removal_rows = rows
    if len(rows) > 1:
        winner = max(range(len(rows)), key=lambda index: rows[index]["third_return"])
        removal_rows = [row for index, row in enumerate(rows) if index != winner]
    return {
        "matured_experiment_count": len(rows),
        "median_third_return_pct": _median([row["third_return"] for row in rows]),
        "third_positive_rate": (
            round(sum(row["third_return"] > 0 for row in rows) / len(rows), 6)
            if rows
            else None
        ),
        "median_live_selected_mean_return_pct": _median(
            [row["live_return"] for row in rows]
        ),
        "median_pair_delta_pct_points": _median([row["delta"] for row in rows]),
        "median_third_mfe_pct": _median([row["third_mfe"] for row in rows]),
        "median_live_selected_mean_mfe_pct": _median(
            [row["live_mfe"] for row in rows]
        ),
        "median_third_mae_pct": _median([row["third_mae"] for row in rows]),
        "median_live_selected_mean_mae_pct": _median(
            [row["live_mae"] for row in rows]
        ),
        "median_mae_delta_pct_points": _median(
            [row["third_mae"] - row["live_mae"] for row in rows]
        ),
        "highest_third_winner_removed_count": len(removal_rows),
        "highest_third_winner_removed_median_pair_delta_pct_points": _median(
            [row["delta"] for row in removal_rows]
        ),
    }


def build_third_slot_evidence_packet(
    raw_events: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    raw_list = list(raw_events)
    events, raw_supported, duplicate_count = _deduplicate(raw_list)
    evaluations = {}
    invalid_evaluations = 0
    regime_distribution = Counter()
    mode_distribution = Counter()
    dates = set()
    for event in events:
        if event.get("event_type") != EVALUATION_EVENT:
            continue
        attributes = event.get("attributes") or {}
        experiment_ref = str(attributes.get("experiment_ref") or "")
        if not experiment_ref or not _valid_evaluation(attributes):
            invalid_evaluations += 1
            continue
        evaluations[experiment_ref] = event
        dates.add(str(attributes.get("trade_date") or ""))
        regime_distribution[str(attributes.get("regime") or "UNKNOWN")] += 1
        mode_distribution[str(attributes.get("trigger_mode") or "UNKNOWN")] += 1

    outcomes = {}
    leakage_count = 0
    orphan_outcome_count = 0
    for event in events:
        if event.get("event_type") != OUTCOME_EVENT:
            continue
        attributes = event.get("attributes") or {}
        experiment_ref = str(attributes.get("experiment_ref") or "")
        ticker = str(event.get("ticker") or "")
        try:
            horizon = int(attributes.get("horizon_trading_days"))
        except (TypeError, ValueError):
            orphan_outcome_count += 1
            continue
        evaluation = evaluations.get(experiment_ref)
        if evaluation is None or horizon not in HORIZONS:
            orphan_outcome_count += 1
            continue
        trade_date = str((evaluation.get("attributes") or {}).get("trade_date") or "")
        outcome_date = str(attributes.get("outcome_date") or "")
        if not outcome_date or outcome_date <= trade_date:
            leakage_count += 1
            continue
        outcomes[(experiment_ref, ticker, horizon)] = event

    horizon_metrics = {
        str(horizon): _horizon_metrics(evaluations, outcomes, horizon)
        for horizon in HORIZONS
    }
    reasons = []
    for code, observed, minimum in (
        ("PROSPECTIVE_DATES_LT_20", len(dates), MIN_DATES),
        (
            "MATURED_10D_EXPERIMENTS_LT_30",
            horizon_metrics["10"]["matured_experiment_count"],
            MIN_MATURED_10D,
        ),
    ):
        if observed < minimum:
            reasons.append({"code": code, "observed": observed, "minimum": minimum})
    for code, observed, maximum in (
        ("DUPLICATE_EVENT_IDS_PRESENT", duplicate_count, 0),
        ("INVALID_EVALUATIONS_PRESENT", invalid_evaluations, 0),
        ("OUTCOME_LEAKAGE_PRESENT", leakage_count, 0),
        ("ORPHAN_OUTCOMES_PRESENT", orphan_outcome_count, 0),
    ):
        if observed > maximum:
            reasons.append({"code": code, "observed": observed, "maximum": maximum})

    criteria = {
        "median_5d_pair_delta_at_least_1pp": False,
        "median_10d_third_return_positive": False,
        "median_10d_mae_not_worse_by_more_than_2pp": False,
        "winner_removal_direction_positive": False,
    }
    if not reasons:
        five = horizon_metrics["5"]
        ten = horizon_metrics["10"]
        criteria = {
            "median_5d_pair_delta_at_least_1pp": (
                five["median_pair_delta_pct_points"] is not None
                and five["median_pair_delta_pct_points"] >= 1.0
            ),
            "median_10d_third_return_positive": (
                ten["median_third_return_pct"] is not None
                and ten["median_third_return_pct"] > 0
            ),
            "median_10d_mae_not_worse_by_more_than_2pp": (
                ten["median_mae_delta_pct_points"] is not None
                and ten["median_mae_delta_pct_points"] >= -2.0
            ),
            "winner_removal_direction_positive": (
                five[
                    "highest_third_winner_removed_median_pair_delta_pct_points"
                ]
                is not None
                and five[
                    "highest_third_winner_removed_median_pair_delta_pct_points"
                ]
                > 0
            ),
        }
    if reasons:
        verdict = "CONTINUE_CAPTURE"
    elif all(criteria.values()):
        verdict = "REVIEW_THIRD_SLOT_LIVE"
    else:
        verdict = "KEEP_TWO_SLOT_CAP"

    payload = {
        "packet_schema_version": PACKET_SCHEMA_VERSION,
        "analysis_contract_version": ANALYSIS_CONTRACT_VERSION,
        "market": "KR",
        "policy_version": POLICY_VERSION,
        "as_of": max(
            (str(event.get("timestamp") or "") for event in events), default=None
        ),
        "data_quality": {
            "raw_supported_event_count": raw_supported,
            "deduplicated_event_count": len(events),
            "duplicate_event_id_count": duplicate_count,
            "invalid_evaluation_count": invalid_evaluations,
            "outcome_leakage_count": leakage_count,
            "orphan_outcome_count": orphan_outcome_count,
        },
        "coverage": {
            "prospective_date_count": len(dates),
            "experiment_count": len(evaluations),
            "regime_distribution": dict(sorted(regime_distribution.items())),
            "trigger_mode_distribution": dict(sorted(mode_distribution.items())),
            "outcome_event_count": len(outcomes),
        },
        "horizon_metrics": horizon_metrics,
        "promotion_criteria": criteria,
        "security": {
            "packet_sanitized": True,
            "identifier_exposure": "hashed_or_omitted",
            "actual_fill_claimed": False,
        },
        "readiness": {
            "data_sufficient": not reasons,
            "reasons": reasons,
            "verdict": verdict,
            "automatic_live_forbidden": True,
        },
    }
    packet_id = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()[:24]
    return {"packet_id": packet_id, **payload}


def _load_jsonl(paths: Iterable[str | Path]) -> list[dict[str, Any]]:
    events = []
    for raw_path in paths:
        with Path(raw_path).open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict):
                    events.append(event)
    return events


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", action="append", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    packet = build_third_slot_evidence_packet(_load_jsonl(args.input))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": "ok",
                "packet_id": packet["packet_id"],
                "experiments": packet["coverage"]["experiment_count"],
                "verdict": packet["readiness"]["verdict"],
                "output": str(output),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
