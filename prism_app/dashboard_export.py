"""Read-only export contract for the localhost Phase 1 dashboard.

The exporter reads the authoritative research, internal-paper, and operations stores
through separate SQLite connections.  It never imports a broker/account adapter and
never exposes account-shaped fields.  Missing persisted research/OOS data is shown as
unavailable rather than fabricated.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping
from urllib.parse import quote

from prism_core.policy.dispositions import DispositionAction, FieldDisposition
from prism_core.reporting.scenario_completeness import assess_scenario

SCHEMA_VERSION = "prism_dashboard_v1"
LOCAL_BIND_HOST = "127.0.0.1"
_STRATEGIES = ("SWING_V1", "TREND_V1")
_REQUIRED_TABLES = {
    "research": frozenset(
        {
            "market_snapshots",
            "observations",
            "decision_snapshots",
            "trade_plan_proposals",
            "proposal_outcomes",
            "lesson_candidates",
        }
    ),
    "paper": frozenset(
        {"strategy_books", "positions", "nav_snapshots", "paper_orders"}
    ),
    "ops": frozenset({"job_runs", "heartbeats"}),
}


def _aware_utc(value: datetime, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{label} must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _json_object(value: str | None) -> dict[str, Any]:
    if value is None:
        return {}
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("persisted dashboard JSON must be an object")
    return parsed


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _project_hard_vetoes(
    stored: object, dispositions: tuple[FieldDisposition, ...]
) -> list[str]:
    """Recover veto identities from their authoritative persisted dispositions."""

    vetoes = set(_string_list(stored))
    vetoes.update(
        item.proposed_value
        for item in dispositions
        if item.field_path == "policy.hard_veto"
        and item.action is DispositionAction.REJECT
        and isinstance(item.proposed_value, str)
        and item.proposed_value
    )
    return sorted(vetoes)


def _project_quant_score(
    stored: Mapping[str, Any], *, score_version: str
) -> dict[str, Any]:
    """Project only the persisted deterministic score-audit contract."""

    components = stored.get("components")
    projected: dict[str, Any] = {
        "score_version": score_version,
        "total_score": stored.get("total_score"),
        "components": (
            {
                str(name): value
                for name, value in components.items()
                if isinstance(name, str) and isinstance(value, (str, int, float))
            }
            if isinstance(components, Mapping)
            else {}
        ),
    }
    for key in ("score_id", "feature_snapshot_id", "recomposed_total", "threshold_version"):
        value = stored.get(key)
        if isinstance(value, str):
            projected[key] = value
    if isinstance(stored.get("recomposition_matches"), bool):
        projected["recomposition_matches"] = stored["recomposition_matches"]

    detail_keys = {
        "name", "feature_name", "raw_value", "normalized_score", "lower_bound",
        "upper_bound", "higher_is_better", "weight", "weighted_score",
    }
    details = stored.get("component_details")
    if isinstance(details, list):
        projected["component_details"] = [
            {key: item[key] for key in detail_keys if key in item}
            for item in details
            if isinstance(item, Mapping)
        ]

    threshold_keys = {
        "name", "feature_name", "observed_value", "operator", "threshold",
        "unit", "passed", "veto",
    }
    thresholds = stored.get("thresholds")
    if isinstance(thresholds, list):
        projected["thresholds"] = [
            {key: item[key] for key in threshold_keys if key in item}
            for item in thresholds
            if isinstance(item, Mapping)
        ]
    vetoes = stored.get("threshold_vetoes")
    if isinstance(vetoes, list):
        projected["threshold_vetoes"] = [item for item in vetoes if isinstance(item, str)]
    return projected



def _project_missing_or_stale_data(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        field = item.get("field")
        status = item.get("status")
        critical = item.get("critical")
        detail = item.get("detail")
        if (
            not isinstance(field, str)
            or not field
            or status not in {"MISSING", "STALE", "CONFLICT"}
            or not isinstance(critical, bool)
            or not isinstance(detail, str)
            or not detail
        ):
            continue
        result.append(
            {
                "field": field,
                "status": status,
                "critical": critical,
                "detail": detail,
            }
        )
    return result


def _project_regime(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    probabilities = value.get("probabilities")
    allowed_regimes = {
        "strong_bull",
        "moderate_bull",
        "sideways",
        "moderate_bear",
        "strong_bear",
    }
    projected_probabilities = (
        {
            key: item
            for key, item in probabilities.items()
            if key in allowed_regimes and isinstance(item, (str, int, float))
        }
        if isinstance(probabilities, Mapping)
        else {}
    )
    confidence = value.get("confidence")
    return {
        "probabilities": projected_probabilities,
        "confidence": confidence if isinstance(confidence, (str, int, float)) else None,
        "drivers": _string_list(value.get("drivers")),
        "falsifiers": _string_list(value.get("falsifiers")),
    }


def _project_uncertainty(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    level = value.get("level")
    return {
        "level": level if isinstance(level, (str, int, float)) else None,
        "known_unknowns": _string_list(value.get("known_unknowns")),
    }


def _project_lesson_candidate(value: str | None) -> dict[str, Any]:
    candidate = _json_object(value)
    return {
        key: candidate[key]
        for key in ("condition", "tentative_action")
        if isinstance(candidate.get(key), str)
    }


def _without_actionable_price_levels(value: object) -> object:
    """Remove exact price values while preserving why a field is absent."""

    if isinstance(value, Mapping):
        projected: dict[str, object] = {}
        for key, item in value.items():
            normalized = str(key).lower()
            if "price" in normalized or normalized in {
                "entry_level", "stop_level", "target_level", "comparison_value",
                "observed_value", "lower_value", "upper_value",
            }:
                continue
            if key in {"stop_candidates", "target_candidates"}:
                projected[str(key)] = [
                    {"status": "SUPPRESSED_DUE_TO_DATA_QUALITY"}
                ]
            elif key == "field_dispositions" and isinstance(item, list):
                projected[str(key)] = [
                    {
                        child_key: _without_actionable_price_levels(child_value)
                        for child_key, child_value in disposition.items()
                        if child_key not in {"proposed_value", "resolved_value"}
                    }
                    for disposition in item
                    if isinstance(disposition, Mapping)
                ]
            else:
                projected[str(key)] = _without_actionable_price_levels(item)
        return projected
    if isinstance(value, list):
        return [_without_actionable_price_levels(item) for item in value]
    return value


def _strategy_summary(proposal: Mapping[str, Any] | None) -> dict[str, Any]:
    if proposal is None:
        return {"state": "ANALYSIS_INCOMPLETE", "complete": False}
    return {
        "state": proposal["scenario_state"],
        "complete": proposal["scenario_complete"],
        "decision": proposal["proposed_decision"],
        "quality": proposal["data_quality"],
        "quality_disposition": proposal["quality_disposition"],
        "actionable_levels_suppressed": proposal["actionable_levels_suppressed"],
    }


def _first_string(value: object) -> str | None:
    if isinstance(value, list):
        return next((item for item in value if isinstance(item, str) and item), None)
    return None


def _kr_daily_projection(
    *,
    freshness: list[dict[str, Any]],
    proposals: Mapping[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """Build the concise KR-first layer from the same persisted proposal rows."""

    kr_proposals = [
        item
        for strategy in _STRATEGIES
        for item in proposals[strategy]
        if item["market"] == "KR"
    ]
    available_dates = sorted(
        {
            item["available_at"][:10]
            for item in kr_proposals
            if isinstance(item.get("available_at"), str)
        }
    )
    current_date = available_dates[-1] if available_dates else None
    previous_date = available_dates[-2] if len(available_dates) > 1 else None
    current = [
        item
        for item in kr_proposals
        if current_date is None or item["available_at"].startswith(current_date)
    ]
    previous_ids = {
        item["security_id"]
        for item in kr_proposals
        if previous_date is not None and item["available_at"].startswith(previous_date)
    }
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    for item in current:
        grouped.setdefault(item["security_id"], {})[item["strategy_id"]] = item

    rows: list[dict[str, Any]] = []
    cards: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    next_reviews: set[str] = set()
    for security_id, strategies in sorted(grouped.items()):
        swing = strategies.get("SWING_V1")
        trend = strategies.get("TREND_V1")
        values = [item for item in (swing, trend) if item is not None]
        degraded = any(item["actionable_levels_suppressed"] for item in values)
        change = (
            "DATA_MISSING"
            if degraded or len(values) != len(_STRATEGIES)
            else "MAINTAINED" if security_id in previous_ids else "NEW"
        )
        support = next(
            (
                evidence
                for item in values
                if (evidence := _first_string(item["bull_evidence_ids"]))
            ),
            None,
        )
        counter = next(
            (
                evidence
                for item in values
                if (evidence := _first_string(item["bear_evidence_ids"]))
            ),
            None,
        )
        rows.append(
            {
                "security_id": security_id,
                "channel": "UNAVAILABLE",
                "trigger": "UNAVAILABLE",
                "swing": _strategy_summary(swing),
                "trend": _strategy_summary(trend),
                "current_state": (
                    values[0]["scenario_state"]
                    if values and len({item["scenario_state"] for item in values}) == 1
                    else "STRATEGY_SPECIFIC"
                ),
                "top_support": support,
                "top_counter_evidence": counter,
                "change": change,
            }
        )
        cards.append(
            {
                "security_id": security_id,
                "strategies": {
                    key: value
                    for key, value in (("SWING_V1", swing), ("TREND_V1", trend))
                    if value is not None
                },
            }
        )
        for item in values:
            gaps.extend(item["missing_or_stale_data"])
            review_at = item["scenario"].get("next_review_at")
            if isinstance(review_at, str) and review_at:
                next_reviews.add(review_at)

    changes = [
        {"security_id": row["security_id"], "state": row["change"]}
        for row in rows
    ]
    changes.extend(
        {"security_id": security_id, "state": "EXITED"}
        for security_id in sorted(previous_ids - set(grouped))
    )
    regimes = [
        scenario
        for item in current
        for key in ("regime", "market_judgment")
        if (scenario := item["scenario"].get(key)) is not None
    ]
    return {
        "market": "KR",
        "as_of": current_date,
        "section_order": [
            "source_quality", "market_context", "candidate_table", "strategy_cards",
            "conditional_scenarios", "data_gaps", "changes", "next_review", "audit",
        ],
        "source_quality": [item for item in freshness if item["market"] == "KR"],
        "call_evidence": {
            "status": "UNAVAILABLE",
            "reason": "call evidence is not stored in the dashboard databases",
        },
        "market_context": {
            "regime": regimes,
            "breadth": [],
            "investor_flows": [],
            "leading_groups": [],
            "weak_groups": [],
            "unavailable_reason": "aggregate breadth/flow/group context is not persisted by this database contract",
        },
        "candidate_table": rows,
        "strategy_cards": cards,
        "conditional_scenarios": [
            {
                "security_id": item["security_id"],
                "strategy_id": item["strategy_id"],
                "entry": item["scenario"].get(
                    "entry_triggers", item["scenario"].get("triggers", [])
                ),
                "avoid": item["scenario"].get("avoid_triggers", []),
                "invalidation": item["scenario"].get(
                    "failure_transition", item["scenario"].get("falsifiers", [])
                ),
                "stop_candidates": item["scenario"].get("stop_candidates", []),
                "target_candidates": item["scenario"].get("target_candidates", []),
                "reentry_candidates": item["scenario"].get("reentry_candidates", []),
                "pyramiding_candidates": item["scenario"].get("pyramiding_candidates", []),
                "exit": item["scenario"].get(
                    "failure_transition", item["scenario"].get("exit_conditions", [])
                ),
            }
            for item in current
        ],
        "data_gaps": gaps,
        "changes": changes,
        "next_review": sorted(next_reviews),
        "audit": [item["audit"] for item in current],
    }


def _tables(connection: sqlite3.Connection) -> frozenset[str]:
    return frozenset(
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_schema WHERE type = 'table'"
        )
    )


def _validate_boundary(connection: sqlite3.Connection, kind: str) -> None:
    missing = _REQUIRED_TABLES[kind] - _tables(connection)
    if missing:
        raise RuntimeError(f"{kind} dashboard store is missing tables: {sorted(missing)}")
    connection.execute("PRAGMA query_only = ON")


@contextmanager
def open_read_only_database(path: str | Path) -> Iterator[sqlite3.Connection]:
    """Open an existing SQLite file without creating, migrating, or mutating it."""

    resolved = Path(path).expanduser().resolve(strict=True)
    uri = f"file:{quote(str(resolved), safe='/')}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, isolation_level=None)
    try:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA foreign_keys = ON")
        yield connection
    finally:
        connection.close()


class DashboardExporter:
    """Build one provenance-preserving Phase 1 dashboard read model."""

    def __init__(
        self,
        research_connection: sqlite3.Connection,
        paper_connection: sqlite3.Connection,
        ops_connection: sqlite3.Connection,
    ) -> None:
        self._research = research_connection
        self._paper = paper_connection
        self._ops = ops_connection
        for connection, kind in (
            (research_connection, "research"),
            (paper_connection, "paper"),
            (ops_connection, "ops"),
        ):
            if not isinstance(connection, sqlite3.Connection):
                raise TypeError(f"{kind}_connection must be sqlite3.Connection")
            _validate_boundary(connection, kind)

    def build(self, *, as_of: datetime, generated_at: datetime | None = None) -> dict[str, Any]:
        boundary = _aware_utc(as_of, "as_of")
        generated = _aware_utc(
            generated_at or datetime.now(timezone.utc), "generated_at"
        )
        boundary_text = boundary.isoformat()
        return {
            "schema_version": SCHEMA_VERSION,
            "generated_at": generated.isoformat(),
            "as_of": boundary_text,
            "local_only": True,
            "bind_host": LOCAL_BIND_HOST,
            "research": self._research_contract(boundary_text),
            "paper": self._paper_contract(boundary_text),
            "ops": self._ops_contract(boundary_text),
        }

    def _research_contract(self, boundary: str) -> dict[str, Any]:
        proposals = {
            strategy: self._proposals(strategy, boundary) for strategy in _STRATEGIES
        }
        freshness = self._freshness(boundary)
        return {
            "data_freshness": freshness,
            "daily_leaders": self._leaders(boundary),
            "swing_v1_proposals": proposals["SWING_V1"],
            "trend_v1_proposals": proposals["TREND_V1"],
            "scenario_evidence_falsifiers": [
                {
                    "proposal_record_id": item["proposal_record_id"],
                    "strategy_id": item["strategy_id"],
                    "market": item["market"],
                    "security_id": item["security_id"],
                    "scenario": item["scenario"],
                    "bull_evidence_ids": item["bull_evidence_ids"],
                    "bear_evidence_ids": item["bear_evidence_ids"],
                    "falsifiers": item["falsifiers"],
                    "uncertainty": item["uncertainty"],
                }
                for strategy in _STRATEGIES
                for item in proposals[strategy]
                if item["scenario_complete"]
            ],
            "research_oos": {
                "status": "UNAVAILABLE",
                "reason": (
                    "No persistent experiment/OOS dashboard read contract exists; "
                    "results are not inferred from in-memory research state."
                ),
                "experiments": [],
            },
            "shadow_feedback": self._shadow_feedback(boundary),
            "kr_daily": _kr_daily_projection(
                freshness=freshness,
                proposals=proposals,
            ),
        }

    def _freshness(self, boundary: str) -> list[dict[str, Any]]:
        rows = self._research.execute(
            """
            SELECT s.market, s.snapshot_id, s.as_of_date, s.quality,
                   MAX(o.observed_at), MAX(o.available_at), MAX(o.ingested_at)
            FROM market_snapshots AS s
            LEFT JOIN observations AS o
              ON o.snapshot_id = s.snapshot_id AND o.available_at <= ?
            WHERE s.as_of_date <= ?
              AND NOT EXISTS (
                SELECT 1 FROM market_snapshots AS newer
                WHERE newer.market = s.market
                  AND newer.as_of_date <= ?
                  AND (newer.as_of_date > s.as_of_date OR
                       (newer.as_of_date = s.as_of_date AND newer.snapshot_id > s.snapshot_id))
              )
            GROUP BY s.market, s.snapshot_id, s.as_of_date, s.quality
            ORDER BY s.market
            """,
            (boundary, boundary, boundary),
        ).fetchall()
        return [
            {
                "market": row[0],
                "snapshot_id": row[1],
                "as_of": row[2],
                "quality": row[3],
                "observed_at": row[4],
                "available_at": row[5],
                "ingested_at": row[6],
            }
            for row in rows
        ]

    def _leaders(self, boundary: str) -> list[dict[str, Any]]:
        rows = self._research.execute(
            """
            SELECT s.market, o.snapshot_id, o.security_id, o.provider,
                   o.provider_symbol, o.observed_at, o.available_at, o.ingested_at,
                   s.quality, o.payload_json
            FROM observations AS o
            JOIN market_snapshots AS s USING (snapshot_id)
            WHERE o.observation_kind = 'leadership_security_state'
              AND o.available_at <= ? AND s.as_of_date <= ?
              AND NOT EXISTS (
                SELECT 1 FROM market_snapshots AS later_snapshot
                WHERE later_snapshot.market = s.market
                  AND later_snapshot.as_of_date <= ?
                  AND (later_snapshot.as_of_date > s.as_of_date OR
                       (later_snapshot.as_of_date = s.as_of_date
                        AND later_snapshot.snapshot_id > s.snapshot_id))
              )
              AND NOT EXISTS (
                SELECT 1 FROM observations AS newer
                WHERE newer.provider = o.provider
                  AND newer.source_record_id = o.source_record_id
                  AND newer.available_at <= ?
                  AND (newer.revision > o.revision OR
                       (newer.revision = o.revision AND newer.observation_id > o.observation_id))
              )
            ORDER BY s.market, o.provider_symbol, o.security_id
            """,
            (boundary, boundary, boundary, boundary),
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            payload = _json_object(row[9])
            result.append(
                {
                    "market": row[0],
                    "snapshot_id": row[1],
                    "security_id": row[2],
                    "provider": row[3],
                    "provider_symbol": row[4],
                    "observed_at": row[5],
                    "available_at": row[6],
                    "ingested_at": row[7],
                    "quality": row[8],
                    "symbol": payload.get("symbol"),
                    "name": payload.get("name"),
                    "decision_status": payload.get("decision_status"),
                    "strategies": payload.get("strategies", []),
                    "relative_strength": payload.get("relative_strength", {}),
                    "high_52_week": payload.get("high_52_week", {}),
                    "momentum": payload.get("momentum", {}),
                    "peak": payload.get("peak", {}),
                    "evidence_refs": payload.get("evidence_refs", []),
                }
            )
        return result

    def _proposals(self, strategy: str, boundary: str) -> list[dict[str, Any]]:
        rows = self._research.execute(
            """
            SELECT p.proposal_record_id, p.proposal_id, p.revision,
                   p.strategy_id, p.strategy_version, d.market, d.security_id,
                   d.data_snapshot_id, d.feature_snapshot_id,
                   d.evidence_refs_json, d.data_quality,
                   d.quality_disposition, p.proposed_decision,
                   p.parse_status, p.validation_status,
                   p.normalized_proposal_json, p.model_provider, p.model_id,
                   p.model_version, p.prompt_version, p.available_at,
                   p.content_hash, p.validator_version, p.policy_version,
                   d.quant_score_version, d.snapshot_json
            FROM trade_plan_proposals AS p
            JOIN decision_snapshots AS d USING (decision_snapshot_id)
            WHERE p.strategy_id = ?
              AND p.available_at <= ? AND p.as_of_at <= ?
              AND NOT EXISTS (
                SELECT 1 FROM trade_plan_proposals AS newer
                WHERE newer.proposal_key = p.proposal_key
                  AND newer.strategy_id = p.strategy_id
                  AND newer.strategy_version = p.strategy_version
                  AND newer.available_at <= ? AND newer.as_of_at <= ?
                  AND newer.revision > p.revision
              )
            ORDER BY p.proposal_key
            """,
            (strategy, boundary, boundary, boundary, boundary),
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            normalized = _json_object(row[15])
            snapshot_payload = _json_object(row[25])
            stored_quant_score = snapshot_payload.get("quant_score")
            if not isinstance(stored_quant_score, Mapping):
                stored_quant_score = {}
            disposition_rows = self._research.execute(
                """
                SELECT field_path, action, reason, proposed_value_json,
                       resolved_value_json, evidence_refs_json
                FROM proposal_disposition_events
                WHERE proposal_record_id = ? AND available_at <= ? AND as_of_at <= ?
                ORDER BY sequence_no
                """,
                (row[0], boundary, boundary),
            ).fetchall()
            dispositions = tuple(
                FieldDisposition(
                    field_path=item[0],
                    action=DispositionAction(item[1]),
                    reason=item[2],
                    proposed_value=None if item[3] is None else json.loads(item[3]),
                    resolved_value=None if item[4] is None else json.loads(item[4]),
                    evidence_ids=tuple(json.loads(item[5])),
                )
                for item in disposition_rows
            )
            assessment = assess_scenario(
                parse_status=row[13],
                validation_status=row[14],
                normalized_proposal_json=row[15],
                dispositions=dispositions,
                expected_identity={
                    "strategy_id": row[3],
                    "strategy_version": row[4],
                    "market": row[5],
                    "security_id": row[6],
                    "data_snapshot_id": row[7],
                    "feature_snapshot_id": row[8],
                },
            )
            scenario = dict(assessment.scenario)
            suppress_levels = (
                row[10] in {"STALE", "PARTIAL", "CONFLICT", "UNAVAILABLE"}
                or row[11] != "ACCEPT"
            )
            if suppress_levels:
                suppressed_scenario = _without_actionable_price_levels(scenario)
                if not isinstance(suppressed_scenario, Mapping):
                    raise TypeError("suppressed scenario projection must remain an object")
                scenario = dict(suppressed_scenario)
            falsifiers = scenario.get("falsifiers", [])
            status = (
                "REJECTED"
                if row[13] == "REJECTED" or row[14] == "REJECTED"
                else row[14]
            )
            result.append(
                {
                    "proposal_record_id": row[0],
                    "proposal_id": row[1],
                    "revision": row[2],
                    "strategy_id": row[3],
                    "strategy_version": row[4],
                    "market": row[5],
                    "security_id": row[6],
                    "snapshot_id": row[7],
                    "evidence_refs": json.loads(row[9]),
                    "data_quality": row[10],
                    "quality_disposition": row[11],
                    "quant_score": _project_quant_score(
                        stored_quant_score, score_version=row[24]
                    ),
                    "hard_vetoes": _project_hard_vetoes(
                        snapshot_payload.get("hard_vetoes"), dispositions
                    ),
                    "status": status,
                    "scenario_state": assessment.state.value,
                    "scenario_complete": assessment.complete,
                    "scenario_reasons": list(assessment.reasons),
                    "proposed_decision": (
                        None
                        if assessment.proposed_decision is None
                        else assessment.proposed_decision.value
                    ),
                    "scenario": scenario,
                    "bull_evidence_ids": _string_list(
                        scenario.get("bull_evidence_ids")
                    ),
                    "bear_evidence_ids": _string_list(
                        scenario.get("bear_evidence_ids")
                    ),
                    "falsifiers": falsifiers if isinstance(falsifiers, list) else [],
                    "missing_or_stale_data": _project_missing_or_stale_data(
                        normalized.get("missing_or_stale_data")
                    ),
                    "uncertainty": (
                        _project_uncertainty(normalized.get("uncertainty"))
                        if assessment.complete
                        else {}
                    ),
                    "actionable_levels_suppressed": suppress_levels,
                    "level_suppression_reason": (
                        f"{row[10]} / {row[11]}" if suppress_levels else None
                    ),
                    "model": {
                        "provider": row[16],
                        "model_id": row[17],
                        "model_version": row[18],
                        "prompt_version": row[19],
                    },
                    "available_at": row[20],
                    "audit": {
                        "proposal_record_id": row[0],
                        "proposal_id": row[1],
                        "snapshot_id": row[7],
                        "content_hash": row[21],
                        "validator_version": row[22],
                        "policy_version": row[23],
                        "dispositions": [
                            {
                                "field_path": item.field_path,
                                "action": item.action.value,
                                "reason": item.reason,
                                "evidence_ids": list(item.evidence_ids),
                                **(
                                    {}
                                    if suppress_levels
                                    else {
                                        "proposed_value": item.proposed_value,
                                        "resolved_value": item.resolved_value,
                                    }
                                ),
                            }
                            for item in dispositions
                        ],
                    },
                }
            )
        return result

    def _shadow_feedback(self, boundary: str) -> list[dict[str, Any]]:
        rows = self._research.execute(
            """
            SELECT c.lesson_id, c.strategy_id, c.strategy_version, c.revision,
                   c.status, c.candidate_json, c.observed_at, c.available_at,
                   c.ingested_at, c.as_of_at
            FROM lesson_candidates AS c
            WHERE c.available_at <= ? AND c.as_of_at <= ?
              AND NOT EXISTS (
                SELECT 1 FROM lesson_candidates AS newer
                WHERE newer.lesson_id = c.lesson_id
                  AND newer.strategy_id = c.strategy_id
                  AND newer.strategy_version = c.strategy_version
                  AND newer.available_at <= ? AND newer.as_of_at <= ?
                  AND newer.revision > c.revision
              )
            ORDER BY c.strategy_id, c.lesson_id
            """,
            (boundary, boundary, boundary, boundary),
        ).fetchall()
        return [
            {
                "lesson_id": row[0],
                "strategy_id": row[1],
                "strategy_version": row[2],
                "revision": row[3],
                "status": row[4],
                "candidate": _project_lesson_candidate(row[5]),
                "observed_at": row[6],
                "available_at": row[7],
                "ingested_at": row[8],
                "as_of": row[9],
            }
            for row in rows
        ]

    def _paper_contract(self, boundary: str) -> dict[str, Any]:
        books = self._paper.execute(
            "SELECT book_id, strategy_id, market, currency, created_at "
            "FROM strategy_books WHERE created_at <= ? ORDER BY strategy_id, market",
            (boundary,),
        ).fetchall()
        result = []
        for book in books:
            position_rows = self._paper.execute(
                """
                SELECT p.security_id, p.quantity, p.average_cost, p.as_of_at
                FROM positions AS p
                WHERE p.book_id = ? AND p.as_of_at <= ?
                  AND NOT EXISTS (
                    SELECT 1 FROM positions AS newer
                    WHERE newer.book_id = p.book_id
                      AND newer.security_id = p.security_id
                      AND newer.as_of_at <= ? AND newer.as_of_at > p.as_of_at
                  )
                ORDER BY p.security_id
                """,
                (book[0], boundary, boundary),
            ).fetchall()
            nav = self._paper.execute(
                "SELECT nav, cash, as_of_at FROM nav_snapshots "
                "WHERE book_id = ? AND as_of_at <= ? "
                "ORDER BY as_of_at DESC, nav_snapshot_id DESC LIMIT 1",
                (book[0], boundary),
            ).fetchone()
            states = self._paper.execute(
                "SELECT order_state, COUNT(*) FROM paper_orders "
                "WHERE book_id = ? AND occurred_at <= ? GROUP BY order_state "
                "ORDER BY order_state",
                (book[0], boundary),
            ).fetchall()
            result.append(
                {
                    "book_id": book[0],
                    "strategy_id": book[1],
                    "market": book[2],
                    "currency": book[3],
                    "created_at": book[4],
                    "positions": [
                        {
                            "security_id": row[0],
                            "quantity": row[1],
                            "average_cost": row[2],
                            "as_of": row[3],
                        }
                        for row in position_rows
                    ],
                    "nav": None
                    if nav is None
                    else {"value": nav[0], "cash": nav[1], "as_of": nav[2]},
                    "order_state_counts": {row[0]: row[1] for row in states},
                }
            )
        return {
            "environment": "INTERNAL_PAPER",
            "external_broker": False,
            "readiness": (
                "FOUNDATION_ONLY" if not result else "PERSISTED_LEDGER_READ_ONLY"
            ),
            "books": result,
        }

    def _ops_contract(self, boundary: str) -> dict[str, Any]:
        rows = self._ops.execute(
            """
            SELECT r.run_id, r.job_key, r.status, r.started_at, r.finished_at,
                   (SELECT MAX(h.observed_at) FROM heartbeats AS h
                    WHERE h.run_id = r.run_id) AS last_heartbeat
            FROM job_runs AS r
            WHERE r.started_at <= ?
              AND NOT EXISTS (
                SELECT 1 FROM job_runs AS newer
                WHERE newer.job_key = r.job_key AND newer.started_at <= ?
                  AND (newer.started_at > r.started_at OR
                       (newer.started_at = r.started_at AND newer.run_id > r.run_id))
              )
            ORDER BY r.job_key
            """,
            (boundary, boundary),
        ).fetchall()
        return {
            "jobs": [
                {
                    "run_id": row[0],
                    "job_key": row[1],
                    "status": row[2],
                    "started_at": row[3],
                    "finished_at": row[4],
                    "last_heartbeat": row[5],
                }
                for row in rows
            ]
        }


def export_dashboard(
    *,
    research_db: str | Path,
    paper_db: str | Path,
    ops_db: str | Path,
    output_path: str | Path,
    as_of: datetime | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Read three existing stores and atomically replace one dashboard JSON file."""

    boundary = _aware_utc(as_of or datetime.now(timezone.utc), "as_of")
    with ExitStack() as stack:
        exporter = DashboardExporter(
            stack.enter_context(open_read_only_database(research_db)),
            stack.enter_context(open_read_only_database(paper_db)),
            stack.enter_context(open_read_only_database(ops_db)),
        )
        payload = exporter.build(as_of=boundary, generated_at=generated_at)

    destination = Path(output_path).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return payload


def dashboard_export_main(
    argv: list[str] | None = None, *, default_output: str | Path
) -> int:
    """CLI shared by the legacy KR/US script names during migration."""

    parser = argparse.ArgumentParser(
        description=(
            "Export the separate Phase 1 research, internal-paper, and ops stores "
            "for the localhost-only dashboard."
        )
    )
    parser.add_argument("--research-db", required=True, type=Path)
    parser.add_argument("--paper-db", required=True, type=Path)
    parser.add_argument("--ops-db", required=True, type=Path)
    parser.add_argument("--output", type=Path, default=Path(default_output))
    parser.add_argument(
        "--as-of",
        type=datetime.fromisoformat,
        help="Timezone-aware ISO-8601 point-in-time boundary (default: current UTC)",
    )
    args = parser.parse_args(argv)
    export_dashboard(
        research_db=args.research_db,
        paper_db=args.paper_db,
        ops_db=args.ops_db,
        output_path=args.output,
        as_of=args.as_of,
    )
    return 0
