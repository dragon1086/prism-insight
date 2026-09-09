"""Fixed KR legacy-record diagnostic; no broker, DB, candidate or strategy IDs."""
from collections import Counter, defaultdict
from datetime import timedelta
import math
import random
import statistics
from zoneinfo import ZoneInfo

from prism_core.trend_quality_research import (
    TRIALS, compute_wilder_values, digest, passes, registry, timestamp,
)

CUTOFF = "2026-09-09T16:18:06.031436Z"
TRIGGERS = ("갭 상승 모멘텀 상위주", "일중 상승률 상위주")
ZONE = ZoneInfo("Asia/Seoul")


def session_date(value):
    return timestamp(value).astimezone(ZONE).date().isoformat()


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate_source(source):
    if source.get("analysis_contract_version") != "historical-closed-record-evidence-v1":
        raise ValueError("wrong_source_contract")
    if source.get("data_cutoff") != CUTOFF or source.get("canonical_strategy_book_verified") is not False:
        raise ValueError("wrong_scope_or_cutoff")
    if source.get("artifact_sha256") != digest({k: v for k, v in source.items() if k != "artifact_sha256"}):
        raise ValueError("source_hash_mismatch")
    refs = [r["source_record_ref"] for r in source["records"]]
    if len(refs) != len(set(refs)) or any(r.get("market") != "KR" for r in source["records"]):
        raise ValueError("duplicate_or_non_kr_records")


def feature(record, mapping, schedule):
    """Exclude all entry-day data; never manufacture original decision time."""
    reasons = []
    if record.get("trigger_type") not in TRIGGERS:
        return {"status": "MISSING", "reasons": ["OUT_OF_SCOPE_TRIGGER"]}
    if mapping.get("status") != "OK":
        return {"status": "MISSING", "reasons": [mapping.get("status", "PRICE_DATA_MISSING")]}
    try:
        day = session_date(record["recorded_entry_at"])
        exit_day = session_date(record["recorded_exit_at"])
        if timestamp(record["recorded_exit_at"]) > timestamp(CUTOFF) or timestamp(record["recorded_entry_at"]) > timestamp(record["recorded_exit_at"]):
            reasons.append("INVALID_RECORD_CHRONOLOGY")
    except (KeyError, ValueError, TypeError):
        return {"status": "MISSING", "reasons": ["ENTRY_OR_EXIT_TIME_MISSING"]}
    if day not in schedule:
        return {"status": "MISSING", "reasons": ["ENTRY_DATE_NOT_REGULAR_SESSION"]}
    all_bars = mapping["dataset"]["bars"]
    bars = [b for b in all_bars if b["session_date"] < day
            and timestamp(b["bar_close_at"]) < timestamp(schedule[day]["open"])][-60:]
    expected = sorted(d for d in schedule if d < day)[-60:]
    if len(bars) != 60:
        reasons.append("WARMUP_REQUIRES_60_COMPLETED_BARS")
    if [b["session_date"] for b in bars] != expected:
        reasons.append("MISSING_DUPLICATE_OR_STALE_SESSION")
    action_bars = [b for b in all_bars if bars and b["session_date"] >= bars[0]["session_date"]]
    if any(b.get("stock_splits") is None or b.get("dividends") is None for b in action_bars):
        reasons.append("ACTION_STATUS_UNKNOWN")
    if any(b.get("stock_splits") not in (0, 0.0, None) for b in bars):
        reasons.append("SPLIT_IN_FEATURE_WINDOW")
    holding_splits = [b for b in action_bars if day <= b["session_date"] <= exit_day and b.get("stock_splits") not in (0, 0.0, None)]
    holding_dates = [b["session_date"] for b in all_bars if day <= b["session_date"] <= exit_day]
    if holding_dates != sorted(d for d in schedule if day <= d <= exit_day):
        reasons.append("HOLDING_SESSION_ACTION_COVERAGE_MISSING")
    if holding_splits:
        reasons.append("SPLIT_DURING_HOLD_RECORDED_RETURN_UNRESOLVED")
    if not finite(record.get("recorded_return_pct")):
        reasons.append("RECORDED_RETURN_MISSING")
    if record.get("book_scope_status") != "PSEUDONYMOUS_LEGACY_SCOPE" or not record.get("legacy_book_ref"):
        reasons.append("LEGACY_BOOK_UNGROUPABLE")
    if reasons:
        return {"status": "MISSING", "reasons": sorted(set(reasons))}
    try:
        values = compute_wilder_values([(b["high"], b["low"], b["close"]) for b in bars])
    except (ValueError, TypeError, KeyError):
        return {"status": "MISSING", "reasons": ["INVALID_PRICE_SERIES"]}
    return {"status": "OK", "reasons": [], **values,
            "basis": "previous-session-entry-proxy", "original_decision_at": None,
            "anchor_regular_open_at": schedule[day]["open"], "bar_count": 60,
            "first_session": bars[0]["session_date"], "last_session": bars[-1]["session_date"],
            "last_close_at": bars[-1]["bar_close_at"], "input_hash": digest(bars),
            "source_sha256": mapping["dataset"]["source_sha256"],
            "retrieved_at": mapping["dataset"]["retrieved_at"],
            "post_exit_split_events": sum(b.get("stock_splits") not in (0, 0.0, None)
                                         for b in action_bars if b["session_date"] > exit_day),
            "dividend_events": sum(bool(b.get("dividends")) for b in action_bars),
            "price_basis": "YAHOO_AS_DELIVERED_NOT_ORIGINAL_EXCHANGE_TAPE",
            "uniform_prefix_scale_invariance": "ADX_DI_ER_and_net20_sign_only_not_recorded_return_validation"}


def metrics(rows, trial, cost=0):
    """Same fixed date bootstrap as research v1, using source-row refs only."""
    returns = {r["source_record_ref"]: (r["recorded_return_pct"] if cost == 0 else
               ((1 + r["recorded_return_pct"] / 100) *
                (1 - cost / 10000) / (1 + cost / 10000) - 1) * 100) for r in rows}
    keep = {r["source_record_ref"]: passes(trial, r["feature"]) for r in rows}
    effects = {ref: 0.0 if keep[ref] else -value for ref, value in returns.items()}
    blocks = defaultdict(list)
    for row in rows:
        blocks[row["entry_session_date"]].append(effects[row["source_record_ref"]])
    winners = [ref for ref, value in returns.items() if value > 0]
    losers = [ref for ref, value in returns.items() if value < 0]
    enough = len(rows) >= 30 and len(blocks) >= 20
    interval = None
    if enough:
        rng = random.Random(20260910)
        keys = sorted(blocks)
        draws = sorted(statistics.mean(v for key in rng.choices(keys, k=len(keys)) for v in blocks[key])
                       for _ in range(1000))
        interval = [draws[6], draws[993]]
    highest = sorted(winners, key=lambda ref: (-returns[ref], ref))[0] if winners else None
    remaining = [effect for ref, effect in effects.items() if ref != highest]
    kept = [returns[ref] for ref in returns if keep[ref]]
    dropped = [returns[ref] for ref in returns if not keep[ref]]
    return {"status": "EXPLORATORY_ONLY" if enough else "INSUFFICIENT_ROWS_OR_DATES",
            "closed_legacy_record_count": len(rows), "entry_dates": len(blocks),
            "kept_count": len(kept), "dropped_count": len(dropped),
            "baseline_mean_recorded_return_pct": statistics.mean(returns.values()) if returns else None,
            "kept_mean_recorded_return_pct": statistics.mean(kept) if kept else None,
            "dropped_mean_recorded_return_pct": statistics.mean(dropped) if dropped else None,
            "paired_no_entry_zero_delta_pp": statistics.mean(effects.values()) if effects else None,
            "bonferroni_date_cluster_interval_pp": interval,
            "winner_preservation_rate": sum(keep[ref] for ref in winners) / len(winners) if winners else None,
            "loss_removal_rate": sum(not keep[ref] for ref in losers) / len(losers) if losers else None,
            "highest_winner_removed_source_ref": highest,
            "delta_without_highest_winner_pp": statistics.mean(remaining) if remaining else None,
            "cost_bps_each_side_additional_stress": cost}


def build_study(source, collection):
    validate_source(source)
    if collection["source_artifact_sha256"] != source["artifact_sha256"]:
        raise ValueError("collection_source_mismatch")
    if collection["artifact_sha256"] != digest({k: v for k, v in collection.items() if k != "artifact_sha256"}):
        raise ValueError("collection_hash_mismatch")
    rows = []
    for record in source["records"]:
        f = feature(record, collection["mapping"].get(record.get("ticker"), {}), collection["schedule"])
        rows.append({"source_record_ref": record["source_record_ref"], "legacy_book_ref": record["legacy_book_ref"],
                     "ticker": record.get("ticker"), "trigger_type": record.get("trigger_type"),
                     "recorded_entry_at": record.get("recorded_entry_at"), "recorded_exit_at": record.get("recorded_exit_at"),
                     "entry_session_date": session_date(record["recorded_entry_at"]) if record.get("recorded_entry_at") else None,
                     "recorded_return_pct": record.get("recorded_return_pct"), "feature": f,
                     "original_decision_at": None, "canonical_strategy_book_verified": False})
    books = {}
    for book in sorted({r["legacy_book_ref"] for r in rows if r["legacy_book_ref"]}):
        group = [r for r in rows if r["legacy_book_ref"] == book]
        dates = sorted({r["entry_session_date"] for r in group if r["trigger_type"] in TRIGGERS and r["entry_session_date"]})
        boundary = dates[int(len(dates) * .7)] if len(dates) > 1 else None
        embargo = timestamp(boundary + "T00:00:00+09:00") - timedelta(days=30) if boundary else None
        eligible = [r for r in group if r["feature"]["status"] == "OK"]
        train = [r for r in eligible if embargo and timestamp(r["recorded_entry_at"]) < embargo
                 and timestamp(r["recorded_exit_at"]) < embargo]
        test = [r for r in eligible if boundary and r["entry_session_date"] >= boundary]
        partitions = {"all_eligible": eligible, "train_purged": train, "retrospective_test": test}
        books[book] = {
            "scope": "LEGACY_ACCOUNT_SCOPED_NOT_CANONICAL_STRATEGY",
            "original_record_count": len(group), "eligible_record_count": len(eligible),
            "original_scoped_entry_dates": len(dates), "test_boundary": boundary,
            "embargo_start": embargo.isoformat() if embargo else None,
            "embargo_or_closure_purged_count": len(eligible) - len(train) - len(test),
            "partitions": {name: {trial: metrics(part, trial) for trial, _ in TRIALS} for name, part in partitions.items()},
            "trigger_diagnostics": {trigger: {trial: metrics([r for r in eligible if r["trigger_type"] == trigger], trial)
                                             for trial, _ in TRIALS} for trigger in TRIGGERS},
            "additional_cost_stress": {str(cost): {trial: metrics(eligible, trial, cost) for trial, _ in TRIALS} for cost in (10, 30)},
            "independent_holdout": False,
        }
    trial_registry = registry()
    trial_registry.update(version="kr-legacy-closed-adx-v1", triggers=list(TRIGGERS),
                          feature_basis="previous-session-entry-proxy", required_completed_bars=60)
    trial_registry.pop("max_snapshot_age_hours")
    trial_registry.pop("max_completed_bar_age_days")
    result = {"kind": "KR_LEGACY_CLOSED_RECORD_ADX_DIAGNOSTIC_V1", "data_cutoff": CUTOFF,
              "source_artifact_sha256": source["artifact_sha256"], "logical_source_sha256": source["logical_source_sha256"],
              "collection_artifact_sha256": collection["artifact_sha256"], "registration_sha256": collection["registration_sha256"],
              "registry": trial_registry, "books": books, "rows": rows,
              "coverage": {"source_record_count": len(rows),
                           "original_trigger_counts": dict(Counter(r["trigger_type"] or "MISSING" for r in rows)),
                           "source_quality_counts": source.get("quality_counts", {}),
                           "symbol_mapping_status": dict(Counter(m["status"] for m in collection["mapping"].values())),
                           "feature_status": dict(Counter(r["feature"]["status"] for r in rows)),
                           "exclusion_reasons": dict(Counter(reason for r in rows for reason in r["feature"]["reasons"]))},
              "canonical_strategy_book_verified": False, "broker_fill_filter_applied": False,
              "independent_holdout": False, "automatic_shadow_forbidden": True, "automatic_live_forbidden": True,
              "verdict": "CONTINUE_CAPTURE"}
    result["insufficiency_reasons"] = ["ORIGINAL_DECISION_TIME_MISSING", "CANONICAL_STRATEGY_BOOK_UNVERIFIED",
                                       "NO_INDEPENDENT_PROSPECTIVE_HOLDOUT", "COUNTERFACTUAL_CANDIDATE_UNIVERSE_MISSING"]
    if any(b["partitions"]["retrospective_test"]["baseline"]["status"] != "EXPLORATORY_ONLY" for b in books.values()):
        result["insufficiency_reasons"].append("RETROSPECTIVE_TEST_ROWS_OR_DATES_INSUFFICIENT")
    if any(m["status"] == "AMBIGUOUS_SUFFIX" for m in collection["mapping"].values()):
        result["insufficiency_reasons"].append("PROVIDER_SYMBOL_MAPPING_AMBIGUOUS")
    result["artifact_sha256"] = digest(result)
    return result
