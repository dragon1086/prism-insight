"""Build a deterministic, secret-minimized 초분할 evidence packet.

The tool reads local sanitized observability JSONL and an optional local KIS
configuration.  It performs no network, database, broker, or trading calls.
Actual SHADOW observations and candidate replay projections remain separate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from prism_core.micro_split import DEFAULT_POLICY, project_execution_on_advance

PACKET_SCHEMA_VERSION = 1
ANALYSIS_CONTRACT_VERSION = "micro-split-evidence-v1"
MIN_OBSERVED_DATES = 20
MIN_OBSERVED_DECISIONS = 30
BASE_STAGES = DEFAULT_POLICY.base_steps_pct
_EVENT_TYPES = {"candidate.evaluated", "micro_split.shadow_evaluated"}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _parse_time(value: Any) -> datetime | None:
    normalized = _text(value)
    if normalized is None:
        return None
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _canonical(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _ref(*parts: Any) -> str:
    raw = "|".join(str(part or "") for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _stage_quantities(unit_amount: Any, price: Any) -> tuple[dict[str, int], int | None]:
    quantities: dict[str, int] = {}
    try:
        for target_pct in BASE_STAGES:
            projection = project_execution_on_advance(
                unit_amount=unit_amount,
                previous_target_pct=0,
                target_pct=target_pct,
                execution_price=price,
                confirmed_strategy_quantity=0,
            )
            quantities[str(target_pct)] = projection.desired_quantity
    except (TypeError, ValueError):
        return {}, None
    first_executable = next(
        (
            target_pct
            for target_pct in BASE_STAGES
            if quantities[str(target_pct)] > 0
        ),
        None,
    )
    return quantities, first_executable


def load_replay_profiles(config_path: str | Path) -> list[dict[str, Any]]:
    """Load unique USD sizing profiles while returning no account or secret fields."""
    path = Path(config_path)
    raw = path.read_bytes()
    config = yaml.safe_load(raw) or {}
    if not isinstance(config, Mapping):
        return []
    default = _number(config.get("default_unit_amount_usd"))
    amounts = set()
    accounts = config.get("accounts")
    if isinstance(accounts, list):
        for account in accounts:
            if not isinstance(account, Mapping) or account.get("enabled", True) is False:
                continue
            account_market = str(account.get("market") or "all").strip().lower()
            if account_market not in {"us", "all", "both"}:
                continue
            amount = _number(account.get("buy_amount_usd")) or default
            if amount is not None:
                amounts.add(amount)
    elif default is not None:
        amounts.add(default)
    sizing_fingerprint = hashlib.sha256(
        json.dumps(sorted(amounts), separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return [
        {
            "profile_ref": _ref(
                "micro-split-replay-profile",
                sizing_fingerprint,
                amount,
            ),
            "unit_amount": amount,
        }
        for amount in sorted(amounts)
    ]


def _deduplicate(
    raw_events: Iterable[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], int, int]:
    by_id: dict[str, dict[str, Any]] = {}
    duplicate_count = 0
    raw_supported = 0
    for raw in raw_events:
        event = dict(raw)
        if event.get("event_type") not in _EVENT_TYPES:
            continue
        raw_supported += 1
        event_id = _text(event.get("event_id"))
        if event_id is None:
            continue
        existing = by_id.get(event_id)
        if existing is not None:
            duplicate_count += 1
            if (str(event.get("timestamp") or ""), _canonical(event)) <= (
                str(existing.get("timestamp") or ""),
                _canonical(existing),
            ):
                continue
        by_id[event_id] = event
    events = sorted(
        by_id.values(),
        key=lambda event: (
            str(event.get("timestamp") or ""),
            str(event.get("event_id") or ""),
        ),
    )
    return events, raw_supported, duplicate_count


def _observed_row(event: Mapping[str, Any]) -> dict[str, Any]:
    attributes = _mapping(event.get("attributes"))
    quantities = _mapping(attributes.get("base_stage_projection_quantities"))
    safe_quantities = {}
    for stage in BASE_STAGES:
        key = str(stage)
        if key not in quantities:
            continue
        value = quantities[key]
        if value == 0 or _number(value) is not None:
            safe_quantities[key] = int(value)
    return {
        "event_ref": _ref("event", event.get("event_id")),
        "decision_ref": _text(attributes.get("decision_ref"))
        or _ref("decision", event.get("decision_id")),
        "symbol_ref": _ref("symbol", event.get("market"), event.get("ticker")),
        "timestamp": _text(event.get("timestamp")),
        "schema_version": int(attributes.get("shadow_schema_version") or 1),
        "mode": _text(attributes.get("mode")),
        "policy_version": _text(attributes.get("policy_version")),
        "regime": _text(attributes.get("regime")),
        "execution_profile_ref": _text(attributes.get("execution_profile_ref")),
        "unit_snapshot_ref": _text(attributes.get("unit_amount_snapshot_ref")),
        "projection_status": _text(attributes.get("projection_status")),
        "target_pct": attributes.get("target_pct"),
        "projected_whole_share_quantity": attributes.get(
            "projected_whole_share_quantity"
        ),
        "base_stage_projection_quantities": safe_quantities,
        "first_executable_target_pct": attributes.get(
            "first_executable_target_pct"
        ),
        "internal_target_independent_of_execution": bool(
            attributes.get("internal_target_independent_of_execution")
        ),
    }


def build_micro_split_evidence_packet(
    raw_events: Iterable[Mapping[str, Any]],
    *,
    market: str = "US",
    replay_profiles: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Return a deterministic packet with observed and replay cohorts separated."""
    normalized_market = str(market or "US").upper()
    raw_list = list(raw_events)
    events, _, _ = _deduplicate(raw_list)
    market_events = [
        event
        for event in events
        if str(event.get("market") or "").upper() == normalized_market
    ]
    raw_micro_count = sum(
        event.get("event_type") == "micro_split.shadow_evaluated"
        and str(event.get("market") or "").upper() == normalized_market
        for event in raw_list
    )
    observed_events = [
        event
        for event in market_events
        if event.get("event_type") == "micro_split.shadow_evaluated"
    ]
    micro_duplicate_count = raw_micro_count - len(observed_events)
    raw_candidate_count = sum(
        event.get("event_type") == "candidate.evaluated"
        and str(event.get("market") or "").upper() == normalized_market
        for event in raw_list
    )
    distinct_candidate_event_count = sum(
        event.get("event_type") == "candidate.evaluated" for event in market_events
    )
    candidate_duplicate_count = raw_candidate_count - distinct_candidate_event_count
    observed_rows = [_observed_row(event) for event in observed_events]
    observed_dates = {
        timestamp.date().isoformat()
        for row in observed_rows
        if (timestamp := _parse_time(row.get("timestamp"))) is not None
    }
    v2_rows = [
        row
        for row in observed_rows
        if row["schema_version"] >= 2
        and set(row["base_stage_projection_quantities"])
        == {str(stage) for stage in BASE_STAGES}
    ]
    first_stage_distribution = Counter(
        str(row.get("first_executable_target_pct") or "NONE")
        for row in v2_rows
    )

    candidates_by_decision: dict[str, dict[str, Any]] = {}
    for event in market_events:
        if event.get("event_type") != "candidate.evaluated":
            continue
        attributes = _mapping(event.get("attributes"))
        decision_context = _mapping(attributes.get("decision_context"))
        if bool(decision_context.get("is_add")):
            continue
        identity = _text(event.get("decision_id")) or str(event.get("event_id"))
        existing = candidates_by_decision.get(identity)
        if existing is None or (
            str(event.get("timestamp") or ""), _canonical(event)
        ) > (str(existing.get("timestamp") or ""), _canonical(existing)):
            candidates_by_decision[identity] = event
    dated_candidates = [
        event
        for event in candidates_by_decision.values()
        if _parse_time(event.get("timestamp")) is not None
    ]
    replay_dates = sorted(
        {_parse_time(event.get("timestamp")).date().isoformat() for event in dated_candidates}
    )[-60:]
    replay_date_set = set(replay_dates)
    replay_candidates = [
        event
        for event in dated_candidates
        if _parse_time(event.get("timestamp")).date().isoformat() in replay_date_set
    ]
    profiles = [
        {
            "profile_ref": _text(profile.get("profile_ref")),
            "unit_amount": _number(profile.get("unit_amount")),
        }
        for profile in replay_profiles
        if _text(profile.get("profile_ref"))
        and _number(profile.get("unit_amount")) is not None
    ]
    replay_rows = []
    candidates_with_price = 0
    for event in sorted(
        replay_candidates,
        key=lambda item: (str(item.get("timestamp") or ""), str(item.get("event_id") or "")),
    ):
        attributes = _mapping(event.get("attributes"))
        decision_context = _mapping(attributes.get("decision_context"))
        price = _number(decision_context.get("price"))
        if price is None:
            continue
        candidates_with_price += 1
        for profile in profiles:
            quantities, first_executable = _stage_quantities(
                profile["unit_amount"], price
            )
            if not quantities:
                continue
            replay_rows.append(
                {
                    "decision_ref": _ref("decision", event.get("decision_id")),
                    "symbol_ref": _ref(
                        "symbol", event.get("market"), event.get("ticker")
                    ),
                    "timestamp": _text(event.get("timestamp")),
                    "profile_ref": profile["profile_ref"],
                    "selected_for_entry": bool(
                        decision_context.get("selected_for_entry")
                    ),
                    "base_stage_projection_quantities": quantities,
                    "first_executable_target_pct": first_executable,
                    "ingestion_mode": _text(attributes.get("ingestion_mode"))
                    or "live",
                }
            )

    observed_decisions = len(
        {row["decision_ref"] for row in observed_rows if row.get("decision_ref")}
    )
    v2_coverage = len(v2_rows) / len(observed_rows) if observed_rows else 0.0
    reasons = []
    for code, failed, observed, minimum in (
        (
            "OBSERVED_DATES_LT_20",
            len(observed_dates) < MIN_OBSERVED_DATES,
            len(observed_dates),
            MIN_OBSERVED_DATES,
        ),
        (
            "OBSERVED_DECISIONS_LT_30",
            observed_decisions < MIN_OBSERVED_DECISIONS,
            observed_decisions,
            MIN_OBSERVED_DECISIONS,
        ),
        (
            "SCHEMA_V2_COVERAGE_LT_100_PCT",
            v2_coverage < 1.0,
            round(v2_coverage, 4),
            1.0,
        ),
        (
            "CANDIDATE_REPLAY_UNAVAILABLE",
            not replay_rows,
            len(replay_rows),
            1,
        ),
    ):
        if failed:
            reasons.append({"code": code, "observed": observed, "minimum": minimum})
    if micro_duplicate_count:
        reasons.append(
            {
                "code": "DUPLICATE_EVENT_IDS_PRESENT",
                "observed": micro_duplicate_count,
                "maximum": 0,
            }
        )

    payload = {
        "packet_schema_version": PACKET_SCHEMA_VERSION,
        "analysis_contract_version": ANALYSIS_CONTRACT_VERSION,
        "market": normalized_market,
        "as_of": max(
            (str(event.get("timestamp") or "") for event in market_events),
            default=None,
        ),
        "policy": {
            "policy_version": DEFAULT_POLICY.policy_version,
            "base_steps_pct": list(BASE_STAGES),
            "trading_impact": "none",
        },
        "observed_shadow": {
            "raw_event_count": raw_micro_count,
            "distinct_event_count": len(observed_rows),
            "duplicate_event_id_count": micro_duplicate_count,
            "decision_count": observed_decisions,
            "decision_date_count": len(observed_dates),
            "schema_v2_count": len(v2_rows),
            "schema_v2_coverage_rate": round(v2_coverage, 4),
            "first_executable_target_pct_distribution": dict(
                sorted(first_stage_distribution.items())
            ),
            "rows": observed_rows,
        },
        "candidate_replay": {
            "kind": "counterfactual_projection_only",
            "actual_observation": False,
            "decision_date_count": len(replay_dates),
            "duplicate_event_id_count": candidate_duplicate_count,
            "candidate_count": len(replay_candidates),
            "candidate_price_coverage_rate": round(
                candidates_with_price / len(replay_candidates), 4
            )
            if replay_candidates
            else 0.0,
            "profile_count": len(profiles),
            "projected_row_count": len(replay_rows),
            "rows": replay_rows,
        },
        "security": {
            "packet_sanitized": True,
            "raw_identifiers_exported": False,
            "raw_sizing_values_exported": False,
        },
        "readiness": {
            "data_sufficient": not reasons,
            "verdict": "PREREGISTER_REPLAY" if not reasons else "CONTINUE_CAPTURE",
            "reasons": reasons,
            "automatic_shadow_forbidden": True,
            "automatic_live_forbidden": True,
        },
    }
    packet_id = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()[:24]
    return {"packet_id": packet_id, **payload}


def _load_jsonl(paths: Iterable[str | Path]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    events = []
    diagnostics = Counter()
    for raw_path in paths:
        diagnostics["input_file_count"] += 1
        path = Path(raw_path)
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                diagnostics["input_line_count"] += 1
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    diagnostics["invalid_json_line_count"] += 1
                    continue
                if not isinstance(value, Mapping):
                    diagnostics["non_object_line_count"] += 1
                    continue
                events.append(dict(value))
    return events, dict(diagnostics)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", action="append", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--market", default="US")
    parser.add_argument("--replay-config")
    args = parser.parse_args()

    events, input_diagnostics = _load_jsonl(args.input)
    profiles = load_replay_profiles(args.replay_config) if args.replay_config else []
    packet = build_micro_split_evidence_packet(
        events,
        market=args.market,
        replay_profiles=profiles,
    )
    packet["input_diagnostics"] = input_diagnostics
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        "micro-split evidence packet "
        f"{packet['packet_id']} observed={packet['observed_shadow']['distinct_event_count']} "
        f"replay={packet['candidate_replay']['projected_row_count']} "
        f"verdict={packet['readiness']['verdict']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
