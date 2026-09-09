"""Retrospective-only validator and fixed trial diagnostics; no observed_at fiction."""
from collections import Counter
from datetime import timedelta
from zoneinfo import ZoneInfo

from prism_core.trend_quality_research import (
    SUPPORTED_TRIGGERS, TRIALS, _metrics, compute_wilder_values, digest, passes, registry, timestamp,
)


def reconstructed_feature(candidate, dataset):
    import pandas_market_calendars as calendars
    reasons = []
    decision = timestamp(candidate["decided_at"])
    if candidate.get("trigger_type") not in SUPPORTED_TRIGGERS:
        reasons.append("OUT_OF_SCOPE_TRIGGER")
    if not candidate.get("eligible_for_analysis"):
        reasons.append("PACKET_EXCLUDED")
    if dataset.get("calendar") not in {"NYSE", "NASDAQ"}:
        reasons.append("CALENDAR_UNKNOWN")
    all_bars = dataset.get("bars", [])
    bars = [b for b in all_bars if timestamp(b["bar_close_at"]) <= decision][-60:]
    if len(bars) != 60:
        reasons.append("WARMUP_REQUIRES_60_COMPLETED_BARS")
    if bars:
        relevant = [b for b in all_bars if b["session_date"] >= bars[0]["session_date"]]
        if any(b.get("stock_splits") not in (0, 0.0) for b in relevant):
            reasons.append("SPLIT_OR_ACTION_STATUS_UNRESOLVED")
        if dataset.get("calendar"):
            calendar = calendars.get_calendar(dataset["calendar"])
            expected = calendar.schedule(bars[0]["session_date"], bars[-1]["session_date"])
            if list(expected.index.strftime("%Y-%m-%d")) != [b["session_date"] for b in bars]:
                reasons.append("MISSING_OR_DUPLICATE_SESSION")
        if decision - timestamp(bars[-1]["bar_close_at"]) > timedelta(days=7):
            reasons.append("STALE_LAST_COMPLETED_BAR")
    if reasons:
        return {"status": "MISSING", "reasons": sorted(set(reasons))}
    try:
        numeric = compute_wilder_values([(b["high"], b["low"], b["close"]) for b in bars])
    except (ValueError, TypeError):
        return {"status": "MISSING", "reasons": ["INVALID_PRICE_SERIES"]}
    return {"status": "OK", "reasons": [], **numeric, "kind": "RECONSTRUCTED_REPLAY",
            "retrieved_at": dataset["retrieved_at"], "bar_count": 60,
            "last_close_at": bars[-1]["bar_close_at"], "input_hash": digest(bars),
            "source_sha256": dataset["source_sha256"],
            "action_audit": {"split_events": 0, "dividend_events": sum(bool(b.get("dividends")) for b in bars),
                             "policy": "as_delivered_no_total_return_adjustment",
                             "announcement_times_known": False}}


def build_historical_study(packets, source):
    if source.get("kind") != "RECONSTRUCTED_REPLAY":
        raise ValueError("reconstructed_source_required")
    datasets = {(d["request"]["market"], d["request"]["ticker"]): d for d in source["datasets"]
                if d["request"]["interval"] == "1d"}
    rows, originals = [], {}
    for packet in packets:
        for candidate in packet["analysis_rows"]:
            ref = candidate["decision_ref"]
            if ref in originals:
                raise ValueError("duplicate_decision")
            originals[ref] = candidate
            dataset = datasets.get((packet["market"], candidate["ticker"]), {})
            feature = reconstructed_feature(candidate, dataset)
            decision = timestamp(candidate["decided_at"])
            local_date = decision.astimezone(ZoneInfo("America/New_York")).date().isoformat()
            past = [b for b in dataset.get("bars", []) if timestamp(b["bar_close_at"]) <= decision]
            future = [b for b in dataset.get("bars", []) if b["session_date"] > local_date]
            # Explicit price-window proxy, not an original decision/entry quote.
            forward = {str(n): ((future[n - 1]["close"] / past[-1]["close"] - 1) * 100
                               if len(future) >= n and past else None) for n in (1, 3, 5)}
            outcome = candidate.get("outcomes", {})
            linked = candidate.get("entry", {}).get("observed") and outcome.get("strategy_closed_at")
            rows.append({"decision_ref": ref, "ticker": candidate["ticker"], "market": packet["market"],
                         "decided_at": candidate["decided_at"], "session_date": local_date,
                         "feature": feature, "trigger": candidate["trigger_type"],
                         "regime": candidate.get("regime"), "policy_version": candidate.get("policy_version"),
                         "strategy_return_pct": outcome.get("strategy_return_pct") if linked else None,
                         "forward_price_window_return_pct": forward,
                         "forward_reference": "last_predecision_close_to_Nth_next_session_close_not_entry_return"})
    eligible = [r for r in rows if r["feature"]["status"] == "OK"]
    dates = sorted({r["session_date"] for r in eligible})
    boundary = dates[int(len(dates) * .7)] if len(dates) > 1 else None
    embargo = timestamp(boundary + "T00:00:00Z") - timedelta(days=30) if boundary else None
    train = [r for r in eligible if embargo and timestamp(r["decided_at"]) < embargo
             and originals[r["decision_ref"]].get("outcomes", {}).get("strategy_closed_at")
             and timestamp(originals[r["decision_ref"]]["outcomes"]["strategy_closed_at"]) < embargo]
    paired = {name: _metrics(eligible, name) for name, _ in TRIALS}
    strata = {}
    for row in eligible:
        key = (row["market"], row["trigger"], row["regime"], row["policy_version"])
        strata.setdefault(key, []).append(row)
    cost_sensitivity = {}
    for cost in (0, 10, 30):
        adjusted = [{**r, "strategy_return_pct": ((1 + r["strategy_return_pct"] / 100) *
                     (1 - cost / 10000) / (1 + cost / 10000) - 1) * 100
                     if r["strategy_return_pct"] is not None else None} for r in eligible]
        cost_sensitivity[str(cost)] = {name: _metrics(adjusted, name) for name, _ in TRIALS}
    restricted = {}
    for name, _ in TRIALS:
        events, unknown = [], []
        for ref, candidate in originals.items():
            if not candidate.get("entry", {}).get("observed"):
                continue
            decision = candidate.get("decision", {})
            entry_at = candidate.get("outcomes", {}).get("strategy_entry_at")
            if not entry_at or decision.get("decision") != "entry" or decision.get("gate_allowed") is not True:
                unknown.append(ref)
                continue
            feature = next(r["feature"] for r in rows if r["decision_ref"] == ref)
            scoped = candidate["trigger_type"] in SUPPORTED_TRIGGERS
            keep = passes(name, feature) if scoped else True
            if keep is None:
                unknown.append(ref)
                continue
            if not keep:
                continue
            events.append((entry_at, 1, ref))
            closed = candidate.get("outcomes", {}).get("strategy_closed_at")
            if closed:
                events.append((closed, -1, ref))
        occupied = peak = 0
        timeline = []
        for when, change, ref in sorted(events):
            occupied += change
            peak = max(peak, occupied)
            timeline.append({"at": when, "decision_ref": ref, "unit_slot_change": change, "occupied_unit_slots": occupied})
        restricted[name] = {"timeline": timeline, "peak_unit_slots": peak, "ending_unit_slots": occupied,
                            "unknown_entries": unknown,
                            "interpretation": "Restricted recorded entries only; one-unit-slot normalization, not verified allocation or capacity counterfactual. No alternate candidates or monetary portfolio return."}
    return {"kind": "RECONSTRUCTED_REPLAY", "registry": registry(), "registry_hash": digest(registry()),
            "status": "INSUFFICIENT", "holdout_status": "NO_VALID_HOLDOUT",
            "train_rows_after_30_day_embargo": len(train), "date_count": len(dates), "holdout_boundary": boundary,
            "rows": rows, "paired_closed_diagnostics": paired,
            "strata": [{"market_trigger_regime_policy": list(key), "row_count": len(members),
                        "trials": {name: _metrics(members, name) for name, _ in TRIALS}}
                       for key, members in sorted(strata.items(), key=lambda item: str(item[0]))],
            "cost_sensitivity_bps_each_side": cost_sensitivity,
            "restricted_unit_slot_replay": restricted,
            "exclusion_counts": dict(Counter(reason for row in rows for reason in row["feature"]["reasons"])),
            "limitations": ["No prospective holdout", "No threshold optimization", "No broker-realized PnL",
                "Forward returns use predecision-close proxy, not unavailable actual decision quote",
                "Costs absent in source ledger return; fixed 0/10/30 bps round-trip sensitivity only",
                "Source hashes prove dataset identity, not original historical availability"]}
