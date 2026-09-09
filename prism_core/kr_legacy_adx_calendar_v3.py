"""Exactly two source-verified research holidays; frozen prices remain intact."""
from collections import Counter, defaultdict
from copy import deepcopy
import random
import statistics

from prism_core import kr_legacy_adx_identity_v2 as v2

HOLIDAYS = ("2026-06-03", "2026-07-17")
FACTS_SHA256 = "d98832baf64ca693315a2b17feeae0029cab8db95e1bb036708375867e7a09fb"


def calendar_overlay(collection, facts, registration_sha256):
    if collection.get("kind") != "KR_LEGACY_RECONSTRUCTED_PUBLIC_DAILY_IDENTITY_V2" or "calendar_overlay" in collection:
        raise ValueError("original_identity_v2_collection_required")
    if collection["artifact_sha256"] != v2.v1.digest({k: v for k, v in collection.items() if k != "artifact_sha256"}):
        raise ValueError("collection_hash_mismatch")
    if v2.v1.digest(facts) != FACTS_SHA256 or tuple(f["session_date"] for f in facts["facts"]) != HOLIDAYS:
        raise ValueError("unapproved_calendar_facts")
    result = deepcopy(collection)
    for day in HOLIDAYS:
        if day not in result["schedule"]:
            raise ValueError("source_calendar_version_already_changed")
        del result["schedule"][day]
    result["parent_identity_v2_artifact_sha256"] = collection["artifact_sha256"]
    result["identity_v2_registration_sha256"] = collection["registration_sha256"]
    result["registration_sha256"] = registration_sha256
    result["calendar_quality_version"] = "kr-research-two-holiday-overlay-v3"
    result["calendar_overlay"] = {
        "removed_session_dates": list(HOLIDAYS), "source_calendar_version": collection["calendar_version"],
        "facts_sha256": FACTS_SHA256, "facts": facts,
        "raw_prices_metadata_unchanged": True, "runtime_calendar_changed": False,
    }
    result["artifact_sha256"] = v2.v1.digest({k: v for k, v in result.items() if k != "artifact_sha256"})
    return result


def interval12(rows, trial, cost=0):
    blocks = defaultdict(list)
    for row in rows:
        value = row["recorded_return_pct"] if not cost else (
            (1 + row["recorded_return_pct"] / 100) * (1 - cost / 10000) / (1 + cost / 10000) - 1) * 100
        blocks[row["entry_session_date"]].append(0 if v2.v1.passes(trial, row["feature"]) else -value)
    if len(rows) < 30 or len(blocks) < 20:
        return None
    rng = random.Random(20260910)
    keys = sorted(blocks)
    draws = sorted(statistics.mean(v for key in rng.choices(keys, k=len(keys)) for v in blocks[key]) for _ in range(1000))
    return [draws[2], draws[997]]


def build_study(source, collection):
    overlay = collection.get("calendar_overlay") or {}
    if collection.get("calendar_quality_version") != "kr-research-two-holiday-overlay-v3" or overlay.get("facts_sha256") != FACTS_SHA256 or overlay.get("removed_session_dates") != list(HOLIDAYS):
        raise ValueError("approved_two_holiday_overlay_required")
    result = v2.build_study(source, collection)
    result["kind"] = "KR_LEGACY_CLOSED_RECORD_ADX_CALENDAR_V3"
    result["research_quality_iteration"] = 3
    result["registry"].update(version="kr-legacy-closed-adx-calendar-v3", multiple_testing_family=12,
                              interval="99.5833% Bonferroni date-cluster exploratory interval")
    result["calendar_overlay"] = overlay
    result["parent_identity_v2_artifact_sha256"] = collection["parent_identity_v2_artifact_sha256"]
    result["insufficiency_reasons"].append("V2_RESULTS_PREVIOUSLY_VIEWED_CALENDAR_QUALITY_CORRECTION")
    for book, summary in result["books"].items():
        all_rows = [r for r in result["rows"] if r["legacy_book_ref"] == book]
        eligible = [r for r in all_rows if r["feature"]["status"] == "OK"]
        embargo, boundary = summary["embargo_start"], summary["test_boundary"]
        partitions = {
            "all_eligible": eligible,
            "train_purged": [r for r in eligible if embargo and v2.v1.timestamp(r["recorded_entry_at"]) < v2.v1.timestamp(embargo)
                             and v2.v1.timestamp(r["recorded_exit_at"]) < v2.v1.timestamp(embargo)],
            "retrospective_test": [r for r in eligible if boundary and r["entry_session_date"] >= boundary],
        }
        for name, rows in partitions.items():
            for trial, m in summary["partitions"][name].items():
                m["bonferroni_date_cluster_interval_pp"] = interval12(rows, trial)
        for trigger, trials in summary["trigger_diagnostics"].items():
            rows = [r for r in eligible if r["trigger_type"] == trigger]
            for trial, m in trials.items():
                m["bonferroni_date_cluster_interval_pp"] = interval12(rows, trial)
        for cost, trials in summary["additional_cost_stress"].items():
            for trial, m in trials.items():
                m["bonferroni_date_cluster_interval_pp"] = interval12(eligible, trial, int(cost))
        scoped = [r for r in all_rows if r["trigger_type"] in v2.v1.TRIGGERS]
        winners = [r for r in scoped if v2.v1.finite(r["recorded_return_pct"]) and r["recorded_return_pct"] > 0]
        excluded = [r for r in winners if r["feature"]["status"] != "OK"]
        summary["excluded_winner_coverage"] = {
            "original_scoped_winner_count": len(winners),
            "eligible_winner_count": len(winners) - len(excluded), "excluded_winner_count": len(excluded),
            "excluded_reason_counts": dict(Counter(reason for r in excluded for reason in r["feature"]["reasons"])),
            "excluded_records": [{"source_record_ref": r["source_record_ref"], "ticker": r["ticker"],
                                  "recorded_return_pct": r["recorded_return_pct"], "reasons": r["feature"]["reasons"]} for r in excluded],
            "excluded_feature_values_or_no_entry_effect_not_imputed": True}
    result["artifact_sha256"] = v2.v1.digest({k: v for k, v in result.items() if k != "artifact_sha256"})
    return result
