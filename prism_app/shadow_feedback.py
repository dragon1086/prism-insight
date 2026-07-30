"""Initial append-only SHADOW process and future-outcome obligations."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

from prism_app.daily_pipeline import PersistedDailyAnalysis, PipelineSnapshot
from prism_app.outcome_tracker import OutcomeTracker
from prism_core.data.contracts import DataQualityStatus, ObservationTime
from prism_core.data.exchange_calendar import ExchangeMarket, is_exchange_session
from prism_core.data.quality import QualityDisposition
from prism_core.feedback.outcomes import outcome_horizons_for
from prism_core.persistence.feedback_cycle import (
    PendingMarketOutcomeInput,
    ProcessQuality,
    ProcessQualityRecord,
)
from prism_core.strategies.contracts import Market, StrategyId


def record_initial_shadow_feedback(
    connection: sqlite3.Connection,
    *,
    snapshot: PipelineSnapshot,
    analysis: PersistedDailyAnalysis,
    config_version: str,
    code_version: str,
    schema_version: str,
) -> None:
    """Persist same-day PROCESS and not-yet-measurable horizon obligations only."""

    tracker = OutcomeTracker(connection)
    timing = ObservationTime(
        observed_at=analysis.evaluated_at,
        available_at=analysis.evaluated_at,
        ingested_at=analysis.evaluated_at,
        as_of_date=analysis.evaluated_at,
    )
    prepared = []
    for item in analysis.strategies:
        if not item.evidence_refs:
            raise RuntimeError(
                "SHADOW process feedback requires at least one bound evidence id"
            )
        strategy_input = snapshot.strategy_inputs[item.strategy_id]
        feature = strategy_input.feature_snapshot
        proposal_record_id = item.output_payload.get("proposal_record_id")
        if not isinstance(proposal_record_id, str) or not proposal_record_id.strip():
            raise RuntimeError("strategy analysis is missing its exact proposal identity")
        proposal = connection.execute(
            """
            SELECT p.parse_status
            FROM trade_plan_proposals AS p
            JOIN decision_snapshots AS d
              ON d.decision_snapshot_id = p.decision_snapshot_id
            WHERE p.proposal_record_id = ? AND d.feature_snapshot_id = ?
              AND p.strategy_id = ? AND p.strategy_version = ?
            """,
            (
                proposal_record_id,
                str(feature.feature_snapshot_id),
                item.strategy_id.value,
                item.strategy_version.value,
            ),
        ).fetchone()
        if proposal is None:
            raise RuntimeError(
                "exact persisted strategy proposal is missing for SHADOW feedback"
            )
        prepared.append((item, feature, proposal_record_id, proposal[0]))

    for item, feature, proposal_record_id, parse_status in prepared:
        process_id = str(
            uuid5(NAMESPACE_URL, f"prism-process:{proposal_record_id}:0")
        )
        tracker.record_process_quality(
            ProcessQualityRecord(
                process_event_id=process_id,
                proposal_record_id=proposal_record_id,
                market=analysis.market,
                security_id=str(feature.security_id.value),
                strategy_id=item.strategy_id,
                strategy_version=item.strategy_version,
                data_quality=_data_process_quality(
                    feature.data_quality_status,
                    feature.quality_disposition,
                ),
                evidence_quality=ProcessQuality.PASS,
                predicate_quality=(
                    ProcessQuality.PASS
                    if parse_status == "PARSED"
                    else ProcessQuality.FAIL
                ),
                validator_quality=ProcessQuality.PASS,
                evidence_ids=item.evidence_refs,
                timing=timing,
            )
        )
        for horizon in sorted(outcome_horizons_for(item.strategy_id)):
            tracker.record_pending_market_outcome(
                PendingMarketOutcomeInput(
                    outcome_event_id=str(
                        uuid5(
                            NAMESPACE_URL,
                            f"prism-pending:{proposal_record_id}:{horizon}:0",
                        )
                    ),
                    proposal_record_id=proposal_record_id,
                    strategy_id=item.strategy_id,
                    strategy_version=item.strategy_version,
                    required_session_count=horizon,
                    completed_sessions=(),
                    next_eligible_session=_horizon_session(
                        analysis.market,
                        analysis.evaluated_at.date(),
                        horizon,
                    ),
                    timing=timing,
                    config_version=config_version,
                    code_version=code_version,
                    schema_version=schema_version,
                )
            )

    for item, _feature, proposal_record_id, _parse_status in prepared:
        process_count = connection.execute(
            "SELECT count(*) FROM process_quality_outcomes "
            "WHERE proposal_record_id = ? AND revision = 0",
            (proposal_record_id,),
        ).fetchone()[0]
        pending_horizons = {
            row[0]
            for row in connection.execute(
                "SELECT horizon_sessions FROM proposal_outcomes "
                "WHERE proposal_record_id = ? AND revision = 0 "
                "AND outcome_state = 'UNKNOWN'",
                (proposal_record_id,),
            )
        }
        if process_count != 1 or pending_horizons != outcome_horizons_for(
            item.strategy_id
        ):
            raise RuntimeError(
                "SHADOW feedback obligations are incomplete; replay reconciliation required"
            )


def _data_process_quality(
    status: DataQualityStatus,
    disposition: QualityDisposition,
) -> ProcessQuality:
    if status is DataQualityStatus.FRESH and disposition is QualityDisposition.ACCEPT:
        return ProcessQuality.PASS
    if disposition is QualityDisposition.REJECT:
        return ProcessQuality.FAIL
    return ProcessQuality.WARN


def _horizon_session(market: Market, decision_session: date, count: int) -> date:
    exchange = ExchangeMarket.KRX if market is Market.KR else ExchangeMarket.NYSE
    cursor = decision_session
    found = 0
    while found < count:
        cursor += timedelta(days=1)
        if is_exchange_session(exchange, cursor):
            found += 1
    return cursor
