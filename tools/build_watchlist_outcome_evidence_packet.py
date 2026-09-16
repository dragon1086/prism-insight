"""Build deterministic, market-separated research evidence from sanitized JSONL only."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from prism_core.watchlist_outcomes import VERSION, identity

ECONOMIC_FIELDS = ("baseline_anchor_id", "watch_ref", "market", "policy_version", "data_contract", "enrollment",
                   "benchmark", "endpoint_date", "reference_date", "reference_open", "actual_holding_bars",
                   "gross_return", "benchmark_return", "relative_return", "mfe", "mae",
                   "hypothetical_roundtrip_cost_returns", "cost_label")


def distribution(values):
    values = sorted(values)
    if not values:
        return {"n": 0}
    return {"n": len(values), "mean": sum(values) / len(values), "min": values[0], "max": values[-1],
            "median": (values[(len(values) - 1) // 2] + values[len(values) // 2]) / 2,
            "positive_fraction": sum(v > 0 for v in values) / len(values)}


def observation_date(event, market):
    """Prospective observation day, never infer it from historical price asof."""
    try:
        trade_date = str(event.get("trade_date") or "")
        if trade_date:
            return datetime.strptime(trade_date.replace("-", ""), "%Y%m%d").date().isoformat()  # noqa: DTZ007 - date-only key
        stamp = event.get("captured_at") or event.get("observed_at")
        if not stamp:
            return None
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(ZoneInfo("Asia/Seoul" if market == "KR" else "America/New_York")).date().isoformat()
    except (TypeError, ValueError):
        return None


def build_packet(rows):
    unique, conflicts = {}, set()
    expected = {market: {} for market in ("KR", "US")}
    legacy = {market: set() for market in ("KR", "US")}
    ancillary_ids, unavailable = set(), {market: set() for market in ("KR", "US")}
    for row in rows:
        event = row.get("attributes", row)
        market = event.get("market") or row.get("market")
        if row.get("event_type") == "watchlist.outcomes_unavailable" and market in unavailable:
            unavailable[market].add(event.get("batch_ref"))
            if row.get("event_id"):
                ancillary_ids.add(row["event_id"])
        if row.get("event_type") == "watchlist.shadow_evaluated" and market in expected:
            watch_ref = event.get("watch_ref") or event.get("watch_id")
            if watch_ref:
                if event.get("outcome_collection_expected") is True and event.get("outcome_contract_version") == VERSION:
                    for kind in (["BASELINE", "FIRST_READY"] if event.get("status") == "READY" else ["BASELINE"]):
                        anchor_id = identity(VERSION, market, watch_ref, kind)
                        expected[market][anchor_id] = {"watch_ref": watch_ref, "anchor_kind": kind}
                else:
                    legacy[market].add(watch_ref)
            if row.get("event_id"):
                ancillary_ids.add(row["event_id"])
        if event.get("research_kind") != "PRICE_PATH_PROXY_NOT_FILL" or event.get("contract_version") != VERSION:
            continue
        key = event.get("event_id") or row.get("event_id")
        if not key:
            continue
        # Delivery time belongs to transport, not outcome identity.
        if key in unique and unique[key] != event:
            conflicts.add(key)
        unique[key] = event
    events = sorted((e for k, e in unique.items() if k not in conflicts), key=lambda e: e["event_id"])
    markets = {}
    for market in ("KR", "US"):
        selected = [e for e in events if e.get("market") == market]
        enrolled = {e["anchor_id"]: e for e in selected if e["status"] == "ENROLLED"}
        excluded = {e["anchor_id"]: e for e in selected if e["status"] == "EXCLUDED" and e["anchor_id"] not in enrolled}
        missing_registration = {a: e for a, e in expected[market].items() if a not in enrolled and a not in excluded}
        orphan = sorted({e["anchor_id"] for e in selected if e["anchor_id"] not in enrolled and e["status"] != "EXCLUDED"})
        complete = {}
        primary_events = [e for e in selected if e.get("measurement") == "BASELINE20_COMMON_ENDPOINT"]
        primary_completed, primary_conflicts = {}, set()
        for event in primary_events:
            if event["status"] != "COMPLETE":
                continue
            key = (event["anchor_id"], event["measurement"])
            if key in primary_completed:
                if any(primary_completed[key].get(field) != event.get(field) for field in ECONOMIC_FIELDS):
                    primary_conflicts.add(key)
                continue  # Stable event-id ordering chooses one exact duplicate receipt.
            primary_completed[key] = event
        outcome_conflicts = []
        for event in selected:
            if event["status"] != "COMPLETE" or event.get("measurement") == "BASELINE20_COMMON_ENDPOINT":
                continue
            key = (event["anchor_id"], event["horizon"])
            if key in complete and any(complete[key].get(k) != event.get(k) for k in ECONOMIC_FIELDS):
                outcome_conflicts.append(list(key))
            complete[key] = event
        revised = {e["anchor_id"] for e in selected if e["status"] == "BASIS_CHANGED"}
        invalid = {tuple(k) for k in outcome_conflicts}
        usable = [e for key, e in complete.items() if key not in invalid and e["anchor_id"] not in revised and e["anchor_id"] in enrolled]
        strata = defaultdict(list)
        for event in usable:
            strata[(event["anchor_kind"], str(event["horizon"]), event.get("trigger", "UNKNOWN"), event.get("regime", "UNKNOWN"),
                    event.get("policy_version", "UNKNOWN"), event.get("data_contract", "UNKNOWN"), event.get("enrollment", "UNKNOWN"))].append(event)
        groups = [{"anchor_kind": key[0], "horizon": int(key[1]), "trigger": key[2], "regime": key[3],
                   "policy_version": key[4], "data_contract": key[5], "enrollment": key[6],
                   "hypothetical_cost_sensitivity": {str(bps): distribution([e["gross_return"] - bps / 10000 for e in data]) for bps in (0, 10, 25, 50)},
                   **{field: distribution([e[field] for e in data]) for field in ("gross_return", "relative_return", "mfe", "mae")}}
                  for key, data in sorted(strata.items())]
        paired = defaultdict(dict)
        for event in usable:
            paired[(event["watch_ref"], event["endpoint_date"], event.get("policy_version", "UNKNOWN"),
                    event.get("data_contract", "UNKNOWN"), event.get("enrollment", "UNKNOWN"))][event["anchor_kind"]] = event
        pairs = [{"watch_ref": key[0], "endpoint_date": key[1],
                  "policy_version": key[2], "data_contract": key[3], "enrollment": key[4],
                  "ready_minus_baseline_gross": pair["FIRST_READY"]["gross_return"] - pair["BASELINE"]["gross_return"],
                  "baseline_event_id": pair["BASELINE"]["event_id"], "ready_event_id": pair["FIRST_READY"]["event_id"]}
                 for key, pair in sorted(paired.items()) if set(pair) == {"BASELINE", "FIRST_READY"}]
        primary_pairs = []
        for key, ready in sorted(primary_completed.items()):
            if key in primary_conflicts or ready["anchor_id"] in revised or ready["anchor_id"] not in enrolled:
                continue
            candidates = [e for e in usable if e["anchor_kind"] == "BASELINE" and e["horizon"] == 20
                          and e["anchor_id"] == ready.get("baseline_anchor_id")
                          and all(e.get(k) == ready.get(k) for k in ("watch_ref", "market", "policy_version", "data_contract", "enrollment", "benchmark", "endpoint_date"))]
            if len(candidates) != 1:
                continue
            baseline = candidates[0]
            primary_pairs.append({"watch_ref": ready["watch_ref"], "endpoint_date": ready["endpoint_date"],
                                  "policy_version": ready.get("policy_version"), "enrollment": ready.get("enrollment"),
                                  "baseline_event_id": baseline["event_id"], "ready_event_id": ready["event_id"],
                                  "baseline_reference_date": baseline["reference_date"], "ready_reference_date": ready["reference_date"],
                                  "ready_holding_bars": ready["actual_holding_bars"],
                                  "ready_minus_baseline_gross": ready["gross_return"] - baseline["gross_return"],
                                  "ready_minus_baseline_relative": ready["relative_return"] - baseline["relative_return"]})
        ready_watches = {e["watch_ref"] for e in enrolled.values() if e["anchor_kind"] == "FIRST_READY"}
        primary_eligible = {a for source in (expected[market], enrolled, excluded) for a, event in source.items()
                            if event["anchor_kind"] == "FIRST_READY"}
        primary_statuses = Counter()
        for anchor_id in primary_eligible:
            anchor_events = [e for e in primary_events if e["anchor_id"] == anchor_id]
            if any(key[0] == anchor_id for key in primary_conflicts):
                status = "CONFLICT"
            elif anchor_id in revised:
                status = "BASIS_CHANGED"
            elif anchor_id in excluded:
                status = "EXCLUDED"
            elif any(e["status"] == "COMPLETE" for e in anchor_events):
                status = "COMPLETE"
            elif anchor_events:
                status = max(anchor_events, key=lambda e: (e.get("asof") or "", e.get("captured_at") or "", e["event_id"]))["status"]
            else:
                status = "UNKNOWN"
            primary_statuses[status] += 1
        mature_baselines = [e for e in usable if e["anchor_kind"] == "BASELINE" and e["horizon"] == 20]
        coverage = []
        for horizon in (1, 3, 5, 10, 20):
            for kind in ("BASELINE", "FIRST_READY"):
                anchors = {a for a, e in enrolled.items() if e["anchor_kind"] == kind}
                statuses = Counter()
                for anchor in anchors:
                    data = [e for e in selected if e["anchor_id"] == anchor and e.get("horizon") in (None, horizon) and e["status"] != "ENROLLED" and not e.get("measurement")]
                    if anchor in revised:
                        status = "BASIS_CHANGED"
                    elif (anchor, horizon) in invalid:
                        status = "CONFLICT"
                    elif any(e["anchor_id"] == anchor and e["horizon"] == horizon for e in usable):
                        status = "COMPLETE"
                    elif data:
                        status = max(data, key=lambda e: (e.get("asof") or "", e.get("captured_at") or "", e["event_id"]))["status"]
                    else:
                        status = "PENDING"
                    statuses[status] += 1
                coverage.append({"anchor_kind": kind, "horizon": horizon, "eligible_anchors": len(anchors), "statuses": dict(sorted(statuses.items()))})
        markets[market] = {"currency": "KRW" if market == "KR" else "USD", "enrolled_anchors": len(enrolled),
                           "registration_coverage": {"total_anchors": len(enrolled) + len(excluded) + len(missing_registration),
                                                     "registered": len(enrolled), "excluded": len(excluded),
                                                     "missing_registration": len(missing_registration),
                                                     "missing_anchor_ids": sorted(missing_registration),
                                                     "excluded_anchor_ids": sorted(excluded),
                                                     "expected_anchor_ids": sorted(expected[market]),
                                                     "legacy_outside_contract_lifetimes": len(legacy[market] - {e["watch_ref"] for e in expected[market].values()})},
                           "unavailable_batch_refs": sorted(str(b) for b in unavailable[market]),
                           "unique_lifetimes": len({e["watch_ref"] for e in enrolled.values()}),
                           "mature_20_session_lifetimes": len({e["watch_ref"] for e in mature_baselines}),
                           "observed_market_days": len({d for e in selected if (d := observation_date(e, market))}),
                           "completed_price_dates": sorted({e["asof"] for e in selected if e.get("asof")}),
                           "missing_observation_date_events": sum(observation_date(e, market) is None for e in selected),
                           "unique_status_anchors": {s: len({e["anchor_id"] for e in selected if e["status"] == s}) for s in sorted({e["status"] for e in selected})},
                           "late_enrolled_anchors": sum(e.get("enrollment") == "LATE_ENROLLMENT" for e in enrolled.values()),
                           "orphan_anchors": orphan, "conflicting_outcomes": sorted(outcome_conflicts),
                           "basis_changed_anchors": sorted(revised), "horizon_coverage": coverage,
                           "strata": groups, "common_endpoint_pairs": pairs,
                           "primary_baseline20_pairs": sorted(primary_pairs, key=lambda p: (p["watch_ref"], p["ready_event_id"])),
                           "primary_baseline20_conflicts": [list(key) for key in sorted(primary_conflicts)],
                           "primary_baseline20_coverage": {"eligible_first_ready_anchors": len(primary_eligible),
                                                           "statuses": dict(sorted(primary_statuses.items()))},
                           "primary_baseline20_status_anchors": {s: len({e["anchor_id"] for e in primary_events if e["status"] == s}) for s in sorted({e["status"] for e in primary_events})},
                           "positive_20_session_baseline_without_ready": sum(e["gross_return"] > 0 and e["watch_ref"] not in ready_watches for e in mature_baselines),
                           "coverage_note": "No-READY winners are proxies, not proven missed executable BUYs. Status counts may overlap over time."}
    packet = {"contract_version": VERSION, "verdict": "CONTINUE_CAPTURE", "markets": markets,
              "conflicting_event_ids": sorted(conflicts), "unique_events": len(events),
              "review_gate": {"minimum_distinct_market_days_per_market": 20, "minimum_mature_lifetimes_per_market": 30,
                              "target_weeks": [8, 12], "automatic_promotion": False},
              "limitations": ["PRICE_PATH_PROXY_NOT_FILL", "No portfolio returns, causality or full micro-split replay",
                              "Common endpoint pairs only; different exposure durations remain", "Missing data is not zero return"],
              "source_event_ids": sorted({e["event_id"] for e in events} | ancillary_ids)}
    packet["packet_id"] = identity(packet)
    return packet


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path)
    args = parser.parse_args()
    rows = []
    for path in args.inputs:
        with path.open() as file:
            rows.extend(json.loads(line) for line in file if line.strip())
    print(json.dumps(build_packet(rows), ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
