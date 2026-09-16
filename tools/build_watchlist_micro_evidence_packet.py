"""Exact-ID diagnostic evidence from sanitized JSONL; no orders or PnL inference."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CONTRACT_VERSION = "watchlist-micro-evidence-v1"
WATCH = "watchlist.shadow_evaluated"
SEED = "watchlist.shadow_seeded"
READY = "watchlist.shadow_ready"
UNAVAILABLE = "watchlist.shadow_unavailable"
MICRO = "micro_split.shadow_evaluated"
LINK = "watchlist_micro_split.shadow_linked"
COMPLETE = "micro_split.shadow_batch_completed"
TYPES = {WATCH, SEED, READY, UNAVAILABLE, MICRO, LINK, COMPLETE}


def _time(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def _session(attrs):
    try:
        date = str(attrs.get("trade_date", ""))
        if len(date) != 8 or attrs.get("trigger_mode") not in {"morning", "afternoon"}:
            return None
        datetime.strptime(date, "%Y%m%d").replace(tzinfo=timezone.utc)
        return date, attrs["trigger_mode"]
    except ValueError:
        return None


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _attrs(event: dict) -> dict:
    value = event.get("attributes")
    return value if isinstance(value, dict) else {}


def build_watchlist_micro_evidence_packet(events: Iterable[dict], *, input_available=True, market="US") -> dict:
    """Reject conflicting duplicate IDs and orphan links, independent of input order."""
    if market not in {"KR", "US"}:
        raise ValueError("market must be KR or US")
    variants: dict[str, dict[str, dict]] = {}
    copies = Counter()
    malformed = 0
    for event in events:
        if (not isinstance(event, dict) or event.get("event_type") not in TYPES
                or event.get("market") != market):
            continue
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            malformed += 1
            continue
        # Envelope timestamps can differ on retry; compare economic attributes instead.
        semantic = {k: event.get(k) for k in ("event_type", "market", "ticker", "attributes")}
        key = _canonical(semantic)
        group = variants.setdefault(event_id, {})
        # Earliest original capture wins semantic retries, deterministically.
        if key not in group or str(event.get("timestamp", "")) < str(group[key].get("timestamp", "")):
            group[key] = event
        copies[event_id] += 1
    conflicts = {key for key, values in variants.items() if len(values) != 1}
    index = {key: next(iter(values.values())) for key, values in variants.items() if key not in conflicts}
    by_type = {kind: [e for _, e in sorted(index.items()) if e["event_type"] == kind] for kind in TYPES}
    reasons = Counter()
    valid = []
    for link in by_type[LINK]:
        a = _attrs(link)
        refs = [a.get(k) for k in ("micro_event_id", "seed_event_id", "ready_event_id", "ready_observation_event_id")]
        if not all(isinstance(ref, str) and ref in index for ref in refs):
            reasons["ORPHAN_OR_CONFLICTING_REFERENCE"] += 1
            continue
        micro, seed, ready, observed = (index[ref] for ref in refs)
        m, s, r, o = map(_attrs, (micro, seed, ready, observed))
        expected = {"market": market, "ticker": link.get("ticker")}
        identity_ok = bool(expected["ticker"]) and all(
            all(event.get(k) == v for k, v in expected.items())
            for event in (link, micro, seed, ready, observed)
        )
        batch = a.get("batch_ref")
        watch = a.get("watch_ref")
        if not identity_ok or not batch or not watch or any(x.get("watch_ref") != watch for x in (s, r, o)):
            reasons["IDENTITY_MISMATCH"] += 1
            continue
        if m.get("batch_ref") != batch or o.get("batch_ref") != batch:
            reasons["BATCH_MISMATCH"] += 1
            continue
        if (micro.get("event_type") != MICRO or seed.get("event_type") != SEED
                or ready.get("event_type") != READY or observed.get("event_type") != WATCH
                or r.get("status") != "READY" or o.get("status") != "READY"
                or any(x.get("mode") != "SHADOW" for x in (a, m, s, r, o))
                or m.get("reason_code") != "ENTRY_ELIGIBLE_SCOUT"
                or m.get("previous_target_pct") != 0 or m.get("target_pct") != 10
                or a.get("link_schema_version") != 1 or m.get("shadow_schema_version") != 2):
            reasons["INVALID_EVENT_CONTRACT"] += 1
            continue
        times = [_time(e.get("timestamp")) for e in (seed, ready, observed, micro, link)]
        asof = _time(o.get("asof"))
        if (any(t is None for t in times) or times != sorted(times)
                or asof is None or asof > times[2]):
            reasons["TEMPORAL_MISMATCH"] += 1
            continue
        superseded = any(
            _attrs(e).get("watch_ref") == watch
            and _attrs(e).get("status") in {"EXPIRED", "INVALIDATED", "MISSING", "WATCHING"}
            and _time(e.get("timestamp")) is not None
            and times[2] <= _time(e["timestamp"]) <= times[3]
            for e in by_type[WATCH]
        )
        if superseded:
            reasons["SUPERSEDED_READY"] += 1
            continue
        if (not a.get("watch_policy_version") or not a.get("micro_policy_version")
                or any(x.get("policy_version") != a["watch_policy_version"] for x in (s, r, o))
                or m.get("policy_version") != a["micro_policy_version"]):
            reasons["POLICY_MISMATCH"] += 1
            continue
        if (not a.get("source_decision_ref") or not a.get("execution_profile_ref")
                or m.get("decision_ref") != a["source_decision_ref"]
                or m.get("execution_profile_ref") != a["execution_profile_ref"]
                or not a.get("observation_price_ref")
                or o.get("observation_price_ref") != a["observation_price_ref"]
                or o.get("seed_event_id") != a["seed_event_id"]
                or o.get("ready_event_id") != a["ready_event_id"]):
            reasons["PROVENANCE_MISMATCH"] += 1
            continue
        quantity = m.get("projected_whole_share_quantity")
        projected = m.get("projection_status") == "PROJECTED" and type(quantity) is int and quantity >= 0
        baseline_fraction = m.get("baseline_position_fraction")
        if type(baseline_fraction) not in (int, float) or not 0 < baseline_fraction <= 1:
            baseline_fraction = None
        valid.append({
            "link_event_id": link["event_id"], "batch_ref": batch, "watch_ref": watch,
            "micro_event_id": micro["event_id"], "source_decision_ref": a["source_decision_ref"],
            "trade_date": m.get("trade_date"), "trigger_mode": m.get("trigger_mode"),
            "execution_profile_ref": a["execution_profile_ref"],
            "watch_policy_version": a["watch_policy_version"], "micro_policy_version": a["micro_policy_version"],
            "projection_status": "PROJECTED" if projected else "INPUT_UNAVAILABLE",
            "projected_whole_share_quantity": quantity if projected else None,
            "entry_boundary": (
                "LEGACY_ELIGIBLE_PRE_REFRESH" if market == "US"
                else m.get("entry_boundary", "UNKNOWN")
            ),
            "baseline_position_fraction": baseline_fraction,
            "baseline_sizing_status": (
                "CAPTURED" if baseline_fraction is not None else "UNKNOWN"
            ),
            "broker_approved": False, "confirmed_fill": False,
        })
    # One actual source event and one watcher batch observation per execution profile.
    unique = {}
    for row in valid:
        key = (row["micro_event_id"], row["watch_ref"])
        if key in unique:
            reasons["DUPLICATE_LINK"] += 1
        else:
            unique[key] = row
    valid = sorted(unique.values(), key=lambda row: row["link_event_id"])
    batches = {(row["batch_ref"], _session(row)) for row in valid if _session(row)}
    sessions = set()
    for event in by_type[COMPLETE]:
        a = _attrs(event)
        if (event.get("market") == market and (a.get("batch_ref"), _session(a)) in batches
                and a.get("status") == "COMPLETED" and a.get("completion_scope") == "analysis_and_tracking"
                and _session(a)):
            sessions.add(_session(a))
    decisions = {row["source_decision_ref"] for row in valid}
    unavailable = sum(row["projection_status"] != "PROJECTED" for row in valid)
    insufficiency = []
    if not input_available:
        insufficiency.append("INPUT_UNAVAILABLE")
    if len(sessions) < 20:
        insufficiency.append("FEWER_THAN_20_COMPLETED_JOINT_SESSIONS")
    if len(decisions) < 30:
        insufficiency.append("FEWER_THAN_30_UNIQUE_JOINT_DECISIONS")
    watch_failures = len(by_type[UNAVAILABLE]) + sum(_attrs(e).get("status") == "MISSING" for e in by_type[WATCH])
    if conflicts or malformed or reasons or unavailable or watch_failures:
        insufficiency.append("DATA_QUALITY_FAILURE")
    packet = {
        "packet_schema_version": 1, "analysis_contract_version": CONTRACT_VERSION,
        "scope": f"{market}_INITIAL_0_TO_10_DIAGNOSTIC_ONLY",
        "input_status": "AVAILABLE" if input_available else "INPUT_UNAVAILABLE",
        "counts": {
            "watch_ready_observations": sum(_attrs(e).get("status") == "READY" for e in by_type[WATCH]),
            "watch_ready_lifetimes": len({_attrs(e).get("watch_ref") for e in by_type[WATCH] if _attrs(e).get("status") == "READY"}),
            "original_micro_events": len(by_type[MICRO]), "natural_pipeline_joint_links": len(valid),
            "unique_joint_decisions": len(decisions), "completed_joint_sessions": len(sessions),
            "input_unavailable_projections": unavailable,
            "watch_data_failures": watch_failures,
            "zero_quantity_projections": sum(row["projected_whole_share_quantity"] == 0 for row in valid),
            "duplicate_events": sum(n - 1 for n in copies.values()), "conflicting_event_ids": len(conflicts),
            "malformed_events": malformed,
        },
        "rejected_links": dict(sorted(reasons.items())), "joint_observations": valid,
        "readiness": {"verdict": "CONTINUE_CAPTURE", "diagnostic_minimum_met": not insufficiency,
                      "insufficiency_reasons": insufficiency, "auto_promotion": False},
        "claims": {"confirmed_fills": False, "pnl_evaluated": False, "full_stage_simulation": False},
    }
    packet["packet_id"] = hashlib.sha256(_canonical(packet).encode()).hexdigest()
    return packet


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--market", choices=("KR", "US"), default="US")
    args = parser.parse_args()
    events = []
    available = args.input.is_file()
    if available:
        try:
            for line in args.input.read_text().splitlines():
                if line.strip():
                    events.append(json.loads(line))
        except (OSError, UnicodeError, json.JSONDecodeError):
            events = []
            available = False
    packet = build_watchlist_micro_evidence_packet(events, input_available=available, market=args.market)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(packet, indent=2, ensure_ascii=False) + "\n")
    print(f"packet_id={packet['packet_id']} verdict=CONTINUE_CAPTURE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
