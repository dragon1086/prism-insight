"""Round-two identity correction, never a change to the frozen v1 trial."""
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import random
import statistics

from prism_core import kr_legacy_adx_research as v1

IDENTITY_FIELDS = ("symbol", "instrumentType", "exchangeName", "exchangeTimezoneName", "firstTradeDate", "currency")
FAMILY = 8


def identity_reasons(dataset):
    meta = dataset.get("identity_metadata") or {}
    requested = dataset["request"]["ticker"]
    reasons = []
    if meta.get("instrumentType") != "EQUITY":
        reasons.append("PROVIDER_TYPE_NOT_EQUITY")
    if meta.get("symbol") != requested:
        reasons.append("PROVIDER_SYMBOL_MISMATCH")
    expected = "KSC" if requested.endswith(".KS") else "KOE" if requested.endswith(".KQ") else None
    if expected is None or meta.get("exchangeName") != expected:
        reasons.append("PROVIDER_EXCHANGE_MISMATCH")
    if meta.get("exchangeTimezoneName") != "Asia/Seoul":
        reasons.append("PROVIDER_TIMEZONE_UNVERIFIED")
    if any(not b.get("provider_timestamp", "").endswith("+09:00") for b in dataset.get("raw_rows", [])):
        reasons.append("PROVIDER_BAR_TIMEZONE_MISMATCH")
    first = meta.get("firstTradeDate")
    if not v1.finite(first):
        reasons.append("PROVIDER_FIRST_TRADE_DATE_UNKNOWN")
    else:
        try:
            datetime.fromtimestamp(first, timezone.utc)
        except (ValueError, OverflowError, OSError):
            reasons.append("PROVIDER_FIRST_TRADE_DATE_INVALID")
    return reasons


def resolve_mapping(code, datasets):
    candidates = [d for d in datasets if d["request"]["ticker"] in (code + ".KS", code + ".KQ")]
    checks = {d["request"]["ticker"]: identity_reasons(d) for d in candidates}
    valid = [d for d in candidates if not checks[d["request"]["ticker"]] and d["bars"]
             and not d["exclusions"].get("DUPLICATE_SESSION_DATE")]
    if len(valid) != 1:
        return {"status": "AMBIGUOUS_EQUITY_IDENTITIES" if len(valid) > 1 else "PROVIDER_EQUITY_IDENTITY_UNVERIFIED",
                "identity_checks": checks}
    return {"status": "OK", "provider_symbol": valid[0]["request"]["ticker"],
            "identity_checks": checks, "dataset": valid[0],
            "mapping_basis": "SAME_RESPONSE_CANONICAL_EQUITY_IDENTITY_NOT_ISIN_OR_HISTORICAL_ISSUER_CERTIFICATION"}


def feature(record, mapping, schedule):
    if record.get("trigger_type") not in v1.TRIGGERS:
        return {"status": "MISSING", "reasons": ["OUT_OF_SCOPE_TRIGGER"]}
    if mapping.get("status") != "OK":
        return {"status": "MISSING", "reasons": [mapping.get("status", "PRICE_DATA_MISSING")]}
    reasons = identity_reasons(mapping["dataset"])
    if reasons:
        return {"status": "MISSING", "reasons": reasons}
    try:
        day = v1.session_date(record["recorded_entry_at"])
        if day in schedule and datetime.fromtimestamp(mapping["dataset"]["identity_metadata"]["firstTradeDate"], timezone.utc) > v1.timestamp(schedule[day]["open"]):
            return {"status": "MISSING", "reasons": ["PROVIDER_FIRST_TRADE_AFTER_ENTRY_ANCHOR"]}
    except (TypeError, ValueError, KeyError):
        return {"status": "MISSING", "reasons": ["ENTRY_OR_IDENTITY_TIME_INVALID"]}
    result = v1.feature(record, mapping, schedule)
    if result["status"] == "OK":
        result["identity_metadata"] = mapping["dataset"]["identity_metadata"]
        result["identity_basis"] = mapping["mapping_basis"]
    return result


def metrics(rows, trial, cost=0):
    result = v1.metrics(rows, trial, cost)
    # Same bootstrap seed/dates/effects, conservatively extended family 4 -> 8.
    if result["bonferroni_date_cluster_interval_pp"] is not None:
        blocks = defaultdict(list)
        for row in rows:
            value = row["recorded_return_pct"] if not cost else (
                (1 + row["recorded_return_pct"] / 100) * (1 - cost / 10000) / (1 + cost / 10000) - 1) * 100
            blocks[row["entry_session_date"]].append(0 if v1.passes(trial, row["feature"]) else -value)
        rng = random.Random(20260910)
        keys = sorted(blocks)
        draws = sorted(statistics.mean(v for key in rng.choices(keys, k=len(keys)) for v in blocks[key]) for _ in range(1000))
        result["bonferroni_date_cluster_interval_pp"] = [draws[3], draws[996]]
    return result


def build_study(source, collection):
    v1.validate_source(source)
    if collection.get("kind") != "KR_LEGACY_RECONSTRUCTED_PUBLIC_DAILY_IDENTITY_V2" or collection["source_artifact_sha256"] != source["artifact_sha256"]:
        raise ValueError("identity_v2_collection_required")
    if collection["artifact_sha256"] != v1.digest({k: v for k, v in collection.items() if k != "artifact_sha256"}):
        raise ValueError("collection_hash_mismatch")
    rows = []
    for record in source["records"]:
        f = feature(record, collection["mapping"].get(record.get("ticker"), {}), collection["schedule"])
        rows.append({"source_record_ref": record["source_record_ref"], "legacy_book_ref": record["legacy_book_ref"],
                     "ticker": record.get("ticker"), "trigger_type": record.get("trigger_type"),
                     "recorded_entry_at": record.get("recorded_entry_at"), "recorded_exit_at": record.get("recorded_exit_at"),
                     "entry_session_date": v1.session_date(record["recorded_entry_at"]) if record.get("recorded_entry_at") else None,
                     "recorded_return_pct": record.get("recorded_return_pct"), "feature": f,
                     "original_decision_at": None, "canonical_strategy_book_verified": False})
    books = {}
    for book in sorted({r["legacy_book_ref"] for r in rows if r["legacy_book_ref"]}):
        group = [r for r in rows if r["legacy_book_ref"] == book]
        dates = sorted({r["entry_session_date"] for r in group if r["trigger_type"] in v1.TRIGGERS and r["entry_session_date"]})
        boundary = dates[int(len(dates) * .7)] if len(dates) > 1 else None
        embargo = v1.timestamp(boundary + "T00:00:00+09:00") - timedelta(days=30) if boundary else None
        eligible = [r for r in group if r["feature"]["status"] == "OK"]
        train = [r for r in eligible if embargo and v1.timestamp(r["recorded_entry_at"]) < embargo and v1.timestamp(r["recorded_exit_at"]) < embargo]
        test = [r for r in eligible if boundary and r["entry_session_date"] >= boundary]
        partitions = {"all_eligible": eligible, "train_purged": train, "retrospective_test": test}
        books[book] = {
            "scope": "LEGACY_ACCOUNT_SCOPED_NOT_CANONICAL_STRATEGY", "original_record_count": len(group),
            "eligible_record_count": len(eligible), "original_scoped_entry_dates": len(dates),
            "test_boundary": boundary, "embargo_start": embargo.isoformat() if embargo else None,
            "embargo_or_closure_purged_count": len(eligible) - len(train) - len(test),
            "partitions": {name: {trial: metrics(part, trial) for trial, _ in v1.TRIALS} for name, part in partitions.items()},
            "trigger_diagnostics": {trigger: {trial: metrics([r for r in eligible if r["trigger_type"] == trigger], trial)
                                             for trial, _ in v1.TRIALS} for trigger in v1.TRIGGERS},
            "additional_cost_stress": {str(cost): {trial: metrics(eligible, trial, cost) for trial, _ in v1.TRIALS} for cost in (10, 30)},
            "independent_holdout": False}
    registry = v1.registry()
    registry.update(version="kr-legacy-closed-adx-identity-v2", triggers=list(v1.TRIGGERS),
                    multiple_testing_family=FAMILY, interval="99.375% Bonferroni date-cluster exploratory interval",
                    feature_basis="previous-session-entry-proxy", required_completed_bars=60)
    registry.pop("max_snapshot_age_hours")
    registry.pop("max_completed_bar_age_days")
    reasons = ["ORIGINAL_DECISION_TIME_MISSING", "CANONICAL_STRATEGY_BOOK_UNVERIFIED", "NO_INDEPENDENT_PROSPECTIVE_HOLDOUT",
               "COUNTERFACTUAL_CANDIDATE_UNIVERSE_MISSING", "CURRENT_PROVIDER_IDENTITY_NOT_HISTORICAL_ISIN_CERTIFICATION",
               "V1_RESULTS_PREVIOUSLY_VIEWED_IDENTITY_QUALITY_CORRECTION"]
    if any(b["partitions"]["retrospective_test"]["baseline"]["status"] != "EXPLORATORY_ONLY" for b in books.values()):
        reasons.append("RETROSPECTIVE_TEST_ROWS_OR_DATES_INSUFFICIENT")
    result = {"kind": "KR_LEGACY_CLOSED_RECORD_ADX_IDENTITY_V2", "collection_round": 2, "data_cutoff": v1.CUTOFF,
              "source_artifact_sha256": source["artifact_sha256"], "logical_source_sha256": source["logical_source_sha256"],
              "collection_artifact_sha256": collection["artifact_sha256"], "registration_sha256": collection["registration_sha256"],
              "quarantined_v1_collection_sha256": collection["quarantined_v1_collection_sha256"],
              "v1_status": "PROVIDER_IDENTITY_UNVERIFIED_NOT_VALIDATED_SAMPLE",
              "registry": registry, "books": books, "rows": rows, "insufficiency_reasons": reasons,
              "coverage": {"source_record_count": len(rows),
                           "original_trigger_counts": dict(Counter(r["trigger_type"] or "MISSING" for r in rows)),
                           "source_quality_counts": source.get("quality_counts", {}),
                           "symbol_mapping_status": dict(Counter(m["status"] for m in collection["mapping"].values())),
                           "provider_response_identity_failure_counts": dict(Counter(
                               reason for dataset in collection.get("datasets", []) for reason in identity_reasons(dataset))),
                           "feature_status": dict(Counter(r["feature"]["status"] for r in rows)),
                           "exclusion_reasons": dict(Counter(reason for r in rows for reason in r["feature"]["reasons"]))},
              "canonical_strategy_book_verified": False, "broker_fill_filter_applied": False, "independent_holdout": False,
              "automatic_shadow_forbidden": True, "automatic_live_forbidden": True, "verdict": "CONTINUE_CAPTURE"}
    result["artifact_sha256"] = v1.digest(result)
    return result
