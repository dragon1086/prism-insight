"""Thin, dependency-injected daily research pipeline for Phase 1."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Mapping, Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from prism_app.report_service import PublicationResult, ReportService
from prism_core.data.contracts import DataQualityStatus, ObservationTime
from prism_core.data.quality import (
    DataQualityGate,
    QualityDecision,
    QualityDisposition,
    QualitySkipRecord,
)
from prism_core.feedback.repository import canonical_json
from prism_core.reporting.leadership_tracking import (
    LeadershipRepository,
    MarketTrackingSnapshot,
)
from prism_core.runtime.settings import PHASE1_MODES, RuntimeSettings
from prism_core.storage.database import transaction
from prism_core.strategies.contracts import (
    FeatureSnapshot,
    Market,
    QuantScoreBreakdown,
    StrategyDefinition,
    StrategyId,
    StrategyVersion,
)
from prism_core.strategies.registry import DEFAULT_STRATEGY_REGISTRY, StrategyRegistry
from prism_core.strategies.scenario_inputs import ScenarioInputPack, ScenarioInputStatus


_RUN_TYPE = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")


@dataclass(frozen=True)
class ApplicationCapabilities:
    """Task-scoped effects; all are disabled unless explicitly injected."""

    publication_enabled: bool = False
    shadow_evaluation_enabled: bool = False

    def __post_init__(self) -> None:
        if type(self.publication_enabled) is not bool:
            raise TypeError("publication_enabled must be boolean")
        if type(self.shadow_evaluation_enabled) is not bool:
            raise TypeError("shadow_evaluation_enabled must be boolean")


@dataclass(frozen=True)
class DailyRunRequest:
    market: Market
    as_of_date: date
    run_type: str
    evaluated_at: datetime
    invocation_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.market, Market):
            raise TypeError("market must be a strategy Market")
        if not isinstance(self.as_of_date, date) or isinstance(self.as_of_date, datetime):
            raise TypeError("as_of_date must be a date")
        if not isinstance(self.run_type, str) or not _RUN_TYPE.fullmatch(self.run_type):
            raise ValueError("run_type must be normalized lowercase text")
        if (
            not isinstance(self.evaluated_at, datetime)
            or self.evaluated_at.tzinfo is None
            or self.evaluated_at.utcoffset() is None
        ):
            raise ValueError("evaluated_at must be timezone-aware")
        if self.invocation_id is not None and (
            not isinstance(self.invocation_id, str)
            or re.fullmatch(r"[0-9a-f]{64}", self.invocation_id) is None
        ):
            raise ValueError("invocation_id must be a lowercase SHA-256 digest")

    @property
    def base_job_key(self) -> str:
        return f"daily:{self.market.value}:{self.as_of_date.isoformat()}:{self.run_type}"

    @property
    def job_key(self) -> str:
        if self.invocation_id is None:
            return self.base_job_key
        return f"{self.base_job_key}:{self.invocation_id}"


@dataclass(frozen=True)
class PipelineSnapshot:
    """Provider-composition output; the application never invents quality fields."""

    data_snapshot_id: UUID
    field_quality: Mapping[str, DataQualityStatus]
    leadership_snapshot: MarketTrackingSnapshot
    source_payload: Mapping[str, Any]
    strategy_inputs: Mapping[StrategyId, StrategyEvaluationInput] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.data_snapshot_id, UUID):
            raise TypeError("data_snapshot_id must be UUID")
        if not isinstance(self.field_quality, Mapping):
            raise TypeError("field_quality must be a mapping")
        if not isinstance(self.leadership_snapshot, MarketTrackingSnapshot):
            raise TypeError("leadership_snapshot must be MarketTrackingSnapshot")
        if not isinstance(self.source_payload, Mapping):
            raise TypeError("source_payload must be a mapping")
        if not isinstance(self.strategy_inputs, Mapping) or any(
            not isinstance(key, StrategyId)
            or not isinstance(value, StrategyEvaluationInput)
            or value.feature_snapshot.strategy_id is not key
            or value.feature_snapshot.data_snapshot_id != self.data_snapshot_id
            for key, value in self.strategy_inputs.items()
        ):
            raise TypeError("strategy_inputs must contain exact strategy input identities")


@dataclass(frozen=True)
class StrategyEvaluationInput:
    """Typed, point-in-time input for one structured strategy proposal."""

    feature_snapshot: FeatureSnapshot
    quant_score: QuantScoreBreakdown
    available_evidence_ids: frozenset[str]
    evidence_payload: Mapping[str, Any]
    timing: ObservationTime
    hard_vetoes: tuple[str, ...] = ()
    scenario_input_pack: ScenarioInputPack | None = None

    def __post_init__(self) -> None:
        feature = self.feature_snapshot
        score = self.quant_score
        if not isinstance(feature, FeatureSnapshot):
            raise TypeError("feature_snapshot must be a FeatureSnapshot")
        if not isinstance(score, QuantScoreBreakdown):
            raise TypeError("quant_score must be a QuantScoreBreakdown")
        if (
            score.feature_snapshot_id != feature.feature_snapshot_id
            or score.strategy_id is not feature.strategy_id
            or score.strategy_version != feature.strategy_version
            or score.market is not feature.market
            or score.security_id != feature.security_id
        ):
            raise ValueError("quant score must match the exact feature snapshot identity")
        if not isinstance(self.available_evidence_ids, frozenset) or any(
            not isinstance(item, str) or not item.strip()
            for item in self.available_evidence_ids
        ):
            raise TypeError("available_evidence_ids must be a frozenset of non-empty strings")
        if not isinstance(self.evidence_payload, Mapping):
            raise TypeError("evidence_payload must be a mapping")
        if set(self.evidence_payload) != set(self.available_evidence_ids):
            raise ValueError("evidence payload keys must match available evidence identities")
        if not isinstance(self.timing, ObservationTime):
            raise TypeError("timing must be ObservationTime")
        if self.timing.as_of_date != feature.as_of:
            raise ValueError("strategy input timing must match feature as_of")
        if any(not isinstance(item, str) or not item.strip() for item in self.hard_vetoes):
            raise TypeError("hard_vetoes must contain non-empty strings")
        pack = self.scenario_input_pack
        if pack is not None:
            if not isinstance(pack, ScenarioInputPack):
                raise TypeError("scenario_input_pack must be a ScenarioInputPack")
            if (
                pack.provenance.data_snapshot_id != feature.data_snapshot_id
                or pack.identity.market is not feature.market
                or pack.identity.security_id != feature.security_id
            ):
                raise ValueError("scenario input pack must match the feature snapshot")
            if pack.status is ScenarioInputStatus.COMPLETE:
                matching = tuple(
                    item
                    for item in pack.strategies
                    if item.strategy_id is feature.strategy_id
                )
                if (
                    len(matching) != 1
                    or matching[0].feature_snapshot_id != feature.feature_snapshot_id
                ):
                    raise ValueError(
                        "scenario input pack must bind the exact strategy feature"
                    )


@dataclass(frozen=True)
class StrategyEvaluationRequest:
    strategy: StrategyDefinition
    market: Market
    data_snapshot_id: UUID
    source_payload: Mapping[str, Any]
    evaluated_at: datetime
    strategy_input: StrategyEvaluationInput | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.market, Market):
            raise TypeError("market must be Market")
        if self.market not in self.strategy.supported_markets:
            raise ValueError("strategy does not support evaluation market")
        if (
            self.strategy_input is not None
            and self.strategy_input.feature_snapshot.market is not self.market
        ):
            raise ValueError("strategy input market must match evaluation market")


@dataclass(frozen=True)
class StrategyAnalysis:
    strategy_id: StrategyId
    strategy_version: StrategyVersion
    output_payload: Mapping[str, Any]
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.strategy_id, StrategyId):
            raise TypeError("strategy_id must be StrategyId")
        if not isinstance(self.strategy_version, StrategyVersion):
            raise TypeError("strategy_version must be StrategyVersion")
        if not isinstance(self.output_payload, Mapping):
            raise TypeError("output_payload must be a mapping")
        if any(not isinstance(item, str) or not item.strip() for item in self.evidence_refs):
            raise ValueError("evidence_refs must contain non-empty strings")


@dataclass(frozen=True)
class PersistedDailyAnalysis:
    job_key: str
    run_id: str
    market: Market
    as_of_date: date
    run_type: str
    evaluated_at: datetime
    data_snapshot_id: UUID
    leadership_snapshot_id: str
    leadership_report_id: str
    quality_decision: QualityDecision
    quality_skip: QualitySkipRecord | None
    source_payload: Mapping[str, Any]
    strategies: tuple[StrategyAnalysis, ...]


@dataclass(frozen=True)
class DailyRunResult:
    analysis: PersistedDailyAnalysis
    publication: PublicationResult
    idempotent_replay: bool = False


class SnapshotProvider(Protocol):
    async def acquire(self, request: DailyRunRequest) -> PipelineSnapshot: ...


class StrategyEvaluator(Protocol):
    async def evaluate(
        self, request: StrategyEvaluationRequest
    ) -> StrategyAnalysis: ...


class AppRunRepository(Protocol):
    def get(self, job_key: str) -> PersistedDailyAnalysis | None: ...

    def save(self, analysis: PersistedDailyAnalysis) -> PersistedDailyAnalysis: ...


class SQLiteAppRunRepository:
    """Use the existing ops job_runs table without inventing Task 24 lease schema.

    A UUIDv5 run_id is the canonical one-to-one identity for this analysis job key.
    Task 24 may add attempt/lease records without changing this persisted identity.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        if not isinstance(connection, sqlite3.Connection):
            raise TypeError("connection must be sqlite3.Connection")
        if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise RuntimeError("foreign-key enforcement must be enabled")
        self._connection = connection

    @staticmethod
    def run_id_for(job_key: str) -> str:
        return str(uuid5(NAMESPACE_URL, f"prism-app-analysis:{job_key}"))

    def get(self, job_key: str) -> PersistedDailyAnalysis | None:
        run_id = self.run_id_for(job_key)
        row = self._connection.execute(
            "SELECT job_key, status, payload_json FROM job_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        if row[0] != job_key or row[1] != "ANALYSIS_PERSISTED":
            raise RuntimeError("canonical application run identity contains incompatible data")
        return _analysis_from_json(row[2])

    def save(self, analysis: PersistedDailyAnalysis) -> PersistedDailyAnalysis:
        if analysis.run_id != self.run_id_for(analysis.job_key):
            raise ValueError("run_id does not match the deterministic job identity")
        payload_json = _analysis_json(analysis)
        existing = self.get(analysis.job_key)
        if existing is not None:
            if _analysis_json(existing) != payload_json:
                raise ValueError("idempotent job key has divergent analysis")
            return existing
        timestamp = analysis.evaluated_at.astimezone(timezone.utc).isoformat()
        try:
            with transaction(self._connection):
                self._connection.execute(
                    "INSERT INTO job_runs "
                    "(run_id, job_key, status, started_at, finished_at, payload_json, created_at) "
                    "VALUES (?, ?, 'ANALYSIS_PERSISTED', ?, ?, ?, ?)",
                    (
                        analysis.run_id,
                        analysis.job_key,
                        timestamp,
                        timestamp,
                        payload_json,
                        timestamp,
                    ),
                )
        except sqlite3.IntegrityError:
            existing = self.get(analysis.job_key)
            if existing is None or _analysis_json(existing) != payload_json:
                raise
            return existing
        return analysis


class DailyPipeline:
    def __init__(
        self,
        *,
        settings: RuntimeSettings,
        capabilities: ApplicationCapabilities,
        snapshot_provider: SnapshotProvider,
        quality_gate: DataQualityGate,
        strategy_evaluator: StrategyEvaluator,
        leadership_repository: LeadershipRepository,
        run_repository: AppRunRepository,
        report_service: ReportService,
        strategy_registry: StrategyRegistry = DEFAULT_STRATEGY_REGISTRY,
    ) -> None:
        if settings.product_mode not in PHASE1_MODES or settings.broker_enabled:
            raise ValueError("thin daily pipeline accepts Phase 1 no-broker settings only")
        if capabilities.publication_enabled and not report_service.can_publish:
            raise ValueError("publication capability requires an injected transport")
        self._settings = settings
        self._capabilities = capabilities
        self._snapshot_provider = snapshot_provider
        self._quality_gate = quality_gate
        self._strategy_evaluator = strategy_evaluator
        self._leadership_repository = leadership_repository
        self._run_repository = run_repository
        self._report_service = report_service
        self._strategy_registry = strategy_registry

    async def run(self, request: DailyRunRequest) -> DailyRunResult:
        existing = self._run_repository.get(request.job_key)
        if existing is not None:
            return DailyRunResult(
                analysis=existing,
                publication=PublicationResult(attempted=False, succeeded=False),
                idempotent_replay=True,
            )

        snapshot = await self._snapshot_provider.acquire(request)
        if snapshot.leadership_snapshot.market.value != request.market.value:
            raise ValueError("leadership snapshot market does not match the request")
        leadership = self._leadership_repository.ingest(snapshot.leadership_snapshot)
        quality = self._quality_gate.evaluate(snapshot.field_quality)

        quality_skip = None
        strategies: list[StrategyAnalysis] = []
        if quality.disposition is QualityDisposition.ACCEPT:
            for definition in self._strategy_registry.enabled_for(request.market):
                output = await self._strategy_evaluator.evaluate(
                    StrategyEvaluationRequest(
                        strategy=definition,
                        market=request.market,
                        data_snapshot_id=snapshot.data_snapshot_id,
                        source_payload=snapshot.source_payload,
                        evaluated_at=request.evaluated_at,
                        strategy_input=snapshot.strategy_inputs.get(definition.strategy_id),
                    )
                )
                if (
                    output.strategy_id is not definition.strategy_id
                    or output.strategy_version != definition.version
                ):
                    raise ValueError("strategy evaluator returned mismatched exact identity")
                strategies.append(output)
        else:
            quality_skip = QualitySkipRecord(
                request_id=request.job_key,
                snapshot_id=snapshot.data_snapshot_id,
                evaluated_at=request.evaluated_at,
                disposition=quality.disposition,
                reasons=quality.reasons,
                missing_fields=quality.missing_fields,
                stale_fields=quality.stale_fields,
            )

        analysis = PersistedDailyAnalysis(
            job_key=request.job_key,
            run_id=SQLiteAppRunRepository.run_id_for(request.job_key),
            market=request.market,
            as_of_date=request.as_of_date,
            run_type=request.run_type,
            evaluated_at=request.evaluated_at,
            data_snapshot_id=snapshot.data_snapshot_id,
            leadership_snapshot_id=leadership.snapshot_id,
            leadership_report_id=leadership.report_id,
            quality_decision=quality,
            quality_skip=quality_skip,
            source_payload=dict(snapshot.source_payload),
            strategies=tuple(strategies),
        )
        persisted = self._run_repository.save(analysis)
        publication = await self._report_service.publish_persisted(
            persisted,
            enabled=self._capabilities.publication_enabled,
        )
        return DailyRunResult(analysis=persisted, publication=publication)


def _analysis_json(analysis: PersistedDailyAnalysis) -> str:
    payload = {
        "job_key": analysis.job_key,
        "run_id": analysis.run_id,
        "market": analysis.market.value,
        "as_of_date": analysis.as_of_date.isoformat(),
        "run_type": analysis.run_type,
        "evaluated_at": analysis.evaluated_at,
        "data_snapshot_id": analysis.data_snapshot_id,
        "leadership_snapshot_id": analysis.leadership_snapshot_id,
        "leadership_report_id": analysis.leadership_report_id,
        "quality_decision": {
            "disposition": analysis.quality_decision.disposition.value,
            "reasons": analysis.quality_decision.reasons,
            "missing_fields": analysis.quality_decision.missing_fields,
            "stale_fields": analysis.quality_decision.stale_fields,
        },
        "quality_skip": None
        if analysis.quality_skip is None
        else {
            "request_id": analysis.quality_skip.request_id,
            "snapshot_id": analysis.quality_skip.snapshot_id,
            "evaluated_at": analysis.quality_skip.evaluated_at,
            "disposition": analysis.quality_skip.disposition.value,
            "reasons": analysis.quality_skip.reasons,
            "missing_fields": analysis.quality_skip.missing_fields,
            "stale_fields": analysis.quality_skip.stale_fields,
        },
        "source_payload": analysis.source_payload,
        "strategies": [
            {
                "strategy_id": item.strategy_id.value,
                "strategy_version": item.strategy_version.value,
                "output_payload": item.output_payload,
                "evidence_refs": item.evidence_refs,
            }
            for item in analysis.strategies
        ],
    }
    return canonical_json(payload)


def _analysis_from_json(payload_json: str) -> PersistedDailyAnalysis:
    payload = json.loads(payload_json)
    decision_payload = payload["quality_decision"]
    decision = QualityDecision(
        disposition=QualityDisposition(decision_payload["disposition"]),
        reasons=tuple(decision_payload["reasons"]),
        missing_fields=tuple(decision_payload["missing_fields"]),
        stale_fields=tuple(decision_payload["stale_fields"]),
    )
    skip_payload = payload["quality_skip"]
    skip = None
    if skip_payload is not None:
        skip = QualitySkipRecord(
            request_id=skip_payload["request_id"],
            snapshot_id=UUID(skip_payload["snapshot_id"]),
            evaluated_at=datetime.fromisoformat(skip_payload["evaluated_at"]),
            disposition=QualityDisposition(skip_payload["disposition"]),
            reasons=tuple(skip_payload["reasons"]),
            missing_fields=tuple(skip_payload["missing_fields"]),
            stale_fields=tuple(skip_payload["stale_fields"]),
        )
    return PersistedDailyAnalysis(
        job_key=payload["job_key"],
        run_id=payload["run_id"],
        market=Market(payload["market"]),
        as_of_date=date.fromisoformat(payload["as_of_date"]),
        run_type=payload["run_type"],
        evaluated_at=datetime.fromisoformat(payload["evaluated_at"]),
        data_snapshot_id=UUID(payload["data_snapshot_id"]),
        leadership_snapshot_id=payload["leadership_snapshot_id"],
        leadership_report_id=payload["leadership_report_id"],
        quality_decision=decision,
        quality_skip=skip,
        source_payload=payload["source_payload"],
        strategies=tuple(
            StrategyAnalysis(
                strategy_id=StrategyId(item["strategy_id"]),
                strategy_version=StrategyVersion(item["strategy_version"]),
                output_payload=item["output_payload"],
                evidence_refs=tuple(item["evidence_refs"]),
            )
            for item in payload["strategies"]
        ),
    )
