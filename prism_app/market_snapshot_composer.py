"""Compose KIS market data and explicit user-supplied PIT evidence into SHADOW inputs."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Mapping, Protocol
from uuid import NAMESPACE_URL, uuid5

from prism_app.daily_pipeline import PipelineSnapshot, StrategyEvaluationInput
from prism_core.data.contracts import (
    DataQualityStatus,
    EvidenceItem,
    FundamentalObservation,
    MarketSnapshot,
    ObservationTime,
    SecurityId,
    SymbolMapping,
)
from prism_core.data.quality import DataQualityGate, QualityDisposition
from prism_core.feedback.repository import canonical_json
from prism_core.features.market_inputs import build_feature_computation_input
from prism_core.features.service import NumericObservation, PriceBasis, QuantFeatureService
from prism_core.reporting.leadership_tracking import (
    ConfirmationState,
    Market as LeadershipMarket,
    MarketRegime,
    MarketStage,
    MarketTrackingSnapshot,
)
from prism_core.strategies.contracts import Market, StrategyId
from prism_core.strategies.quant_score import (
    QuantScorePolicy,
    QuantScoreRule,
    QuantScoreService,
)
from prism_core.strategies.registry import DEFAULT_STRATEGY_REGISTRY
from prism_core.strategies.scenario_inputs import (
    ScenarioPriceBasis,
    build_scenario_input_pack,
)


_REQUIRED_OBSERVATIONS = frozenset(
    {
        "catalyst_recency_sessions",
        "regime_swing_compatibility",
        "earnings_current",
        "earnings_previous",
        "industry_leadership",
        "regime_trend_compatibility",
    }
)


class KISFetchProvider(Protocol):
    async def fetch_result(
        self, *, security_ids: tuple[SecurityId, ...], as_of_date: datetime
    ) -> Any: ...


class KREvidenceProvider(Protocol):
    async def build(
        self,
        *,
        snapshot: Any,
        stock_id: SecurityId,
        benchmark_id: SecurityId,
        as_of: datetime,
    ) -> "KRPITEvidence": ...


@dataclass(frozen=True)
class KRPITEvidence:
    """Explicit non-price observations; never inferred from a price transport."""

    observed_at: datetime
    available_at: datetime
    ingested_at: datetime
    observations: Mapping[str, Decimal]
    evidence_payload: Mapping[str, Any]
    field_quality: Mapping[str, DataQualityStatus] = field(
        default_factory=lambda: {
            "evidence": DataQualityStatus.FRESH,
            "fundamental": DataQualityStatus.FRESH,
            "regime": DataQualityStatus.FRESH,
        }
    )
    hard_vetoes: tuple[str, ...] = ()
    fundamentals: tuple[FundamentalObservation, ...] = ()
    evidence_items: tuple[EvidenceItem, ...] = ()

    def __post_init__(self) -> None:
        for label, value in (
            ("observed_at", self.observed_at),
            ("available_at", self.available_at),
            ("ingested_at", self.ingested_at),
        ):
            if not isinstance(value, datetime) or value.tzinfo is None:
                raise ValueError(f"{label} must be timezone-aware")
        if not self.observed_at <= self.available_at <= self.ingested_at:
            raise ValueError("evidence timing must be observed <= available <= ingested")
        missing = sorted(_REQUIRED_OBSERVATIONS - set(self.observations))
        if missing:
            raise ValueError("missing required observations: " + ", ".join(missing))
        if set(self.observations) != _REQUIRED_OBSERVATIONS:
            raise ValueError("unsupported observations are not accepted")
        if any(
            not isinstance(value, Decimal) or not value.is_finite()
            for value in self.observations.values()
        ):
            raise ValueError("observations must be finite Decimal values")
        if not self.evidence_payload or any(
            not isinstance(key, str) or not key.strip()
            for key in self.evidence_payload
        ):
            raise ValueError("evidence_payload requires stable non-empty identities")
        canonical_json(dict(self.evidence_payload))
        expected_quality = {"evidence", "fundamental", "regime"}
        if set(self.field_quality) != expected_quality or any(
            not isinstance(status, DataQualityStatus)
            for status in self.field_quality.values()
        ):
            raise ValueError(
                "field_quality must contain evidence, fundamental, and regime statuses"
            )
        object.__setattr__(self, "field_quality", dict(self.field_quality))
        if any(not isinstance(item, FundamentalObservation) for item in self.fundamentals):
            raise TypeError("fundamentals must contain FundamentalObservation records")
        if any(not isinstance(item, EvidenceItem) for item in self.evidence_items):
            raise TypeError("evidence_items must contain EvidenceItem records")


class KRProductSnapshotComposer:
    """Fetch one KIS snapshot and derive both strategy inputs without broker imports."""

    def __init__(
        self,
        *,
        provider: KISFetchProvider,
        stock_id: SecurityId,
        benchmark_id: SecurityId,
        stock_symbol: str,
        benchmark_symbol: str,
        evidence: KRPITEvidence | None = None,
        evidence_provider: KREvidenceProvider | None = None,
        max_evidence_age: timedelta = timedelta(days=1),
        market: Market = Market.KR,
        provider_name: str = "KIS",
        live_evidence_level: str = "LIVE_KIS_FMP_AGENTNEWS_PIT",
        price_adjustment_semantics: str = "RAW_UNADJUSTED",
        incomplete_session_filter: str = "SCENARIO_PACK",
        corporate_action_coverage_scope: str = "UNVERIFIED",
    ) -> None:
        if stock_id == benchmark_id:
            raise ValueError("stock and benchmark identities must differ")
        if not stock_symbol or not benchmark_symbol:
            raise ValueError("provider symbols must be non-empty")
        if max_evidence_age <= timedelta(0):
            raise ValueError("max_evidence_age must be positive")
        if (evidence is None) == (evidence_provider is None):
            raise ValueError("provide exactly one static evidence or evidence_provider")
        if not isinstance(market, Market):
            raise TypeError("market must be a Market")
        if not all(
            (
                provider_name,
                live_evidence_level,
                price_adjustment_semantics,
                incomplete_session_filter,
                corporate_action_coverage_scope,
            )
        ):
            raise ValueError("source provenance labels must be non-empty")
        self._provider = provider
        self._stock_id = stock_id
        self._benchmark_id = benchmark_id
        self._stock_symbol = stock_symbol
        self._benchmark_symbol = benchmark_symbol
        self._evidence = evidence
        self._evidence_provider = evidence_provider
        self._max_evidence_age = max_evidence_age
        self._market = market
        self._provider_name = provider_name
        self._live_evidence_level = live_evidence_level
        self._price_adjustment_semantics = price_adjustment_semantics
        self._incomplete_session_filter = incomplete_session_filter
        self._corporate_action_coverage_scope = corporate_action_coverage_scope

    def validate_as_of(self, as_of: datetime) -> None:
        if not isinstance(as_of, datetime) or as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware")
        if self._evidence is not None:
            self._validate_evidence(self._evidence, as_of)

    def _validate_evidence(
        self, evidence: KRPITEvidence, as_of: datetime, *, enforce_age: bool = True
    ) -> None:
        if evidence.available_at > as_of:
            raise ValueError("PIT evidence was available after evaluation")
        if enforce_age and as_of - evidence.available_at > self._max_evidence_age:
            raise ValueError("PIT evidence is stale for evaluation")

    async def acquire(self, *, as_of: datetime) -> PipelineSnapshot:
        self.validate_as_of(as_of)
        result = await self._provider.fetch_result(
            security_ids=(self._stock_id, self._benchmark_id),
            as_of_date=as_of,
        )
        provider_snapshot = result.snapshot
        snapshot = provider_snapshot
        evidence = self._evidence
        if evidence is None:
            assert self._evidence_provider is not None
            evidence = await self._evidence_provider.build(
                snapshot=snapshot,
                stock_id=self._stock_id,
                benchmark_id=self._benchmark_id,
                as_of=as_of,
            )
        self._validate_evidence(evidence, as_of)
        snapshot = _enrich_market_snapshot(
            snapshot,
            evidence,
            security_ids=(self._stock_id, self._benchmark_id),
        )
        field_quality = {
            "calendar": DataQualityStatus.FRESH,
            **evidence.field_quality,
            "price": snapshot.quality,
        }
        observations = tuple(
            NumericObservation(
                name=name,
                value=value,
                available_at=evidence.available_at,
            )
            for name, value in sorted(evidence.observations.items())
        )
        strategy_inputs: dict[StrategyId, StrategyEvaluationInput] = {}
        timing = ObservationTime(
            observed_at=evidence.observed_at,
            available_at=evidence.available_at,
            ingested_at=max((
                evidence.ingested_at,
                *(bar.timing.ingested_at for bar in snapshot.price_bars),
            )),
            as_of_date=as_of,
        )
        evidence_ids = frozenset(evidence.evidence_payload)
        corporate_action_coverage_items = tuple(
            item
            for item in snapshot.evidence
            if item.kind == "corporate_action_coverage"
            and item.security_id.value
            in {self._stock_id.value, self._benchmark_id.value}
            and item.quality is DataQualityStatus.FRESH
            and item.timing.available_at <= as_of
        )
        covered_corporate_action_ids = {
            item.security_id.value for item in corporate_action_coverage_items
        }
        corporate_action_coverage_verified = covered_corporate_action_ids == {
            self._stock_id.value,
            self._benchmark_id.value,
        }
        coverage_vetoes = (
            ("price.raw_corporate_action_coverage_missing",)
            if self._market is Market.US and not corporate_action_coverage_verified
            else ()
        )
        quality_decision = DataQualityGate().evaluate(field_quality)
        scenario_input_pack = None
        if quality_decision.disposition is QualityDisposition.ACCEPT:
            feature_inputs = build_feature_computation_input(
                snapshot=snapshot,
                market=self._market,
                security_id=self._stock_id,
                benchmark_security_id=self._benchmark_id,
                price_basis=PriceBasis.RAW,
                observations=observations,
                field_quality=field_quality,
            )
            feature_service = QuantFeatureService(feature_version="phase1.features.v1")
            score_service = QuantScoreService()
            features = {}
            scores = {}
            for strategy_id in (StrategyId.SWING_V1, StrategyId.TREND_V1):
                strategy = DEFAULT_STRATEGY_REGISTRY.get(strategy_id)
                feature = feature_service.compute(strategy, feature_inputs)
                score = score_service.score(strategy, feature, _score_policy(strategy_id))
                features[strategy_id] = feature
                scores[strategy_id] = score
            scenario_input_pack = build_scenario_input_pack(
                snapshot=snapshot,
                market=self._market,
                security_id=self._stock_id,
                benchmark_security_id=self._benchmark_id,
                price_basis=ScenarioPriceBasis.RAW,
                feature_snapshots=features,
            )
            for strategy_id in (StrategyId.SWING_V1, StrategyId.TREND_V1):
                strategy_inputs[strategy_id] = StrategyEvaluationInput(
                    feature_snapshot=features[strategy_id],
                    quant_score=scores[strategy_id],
                    available_evidence_ids=evidence_ids,
                    evidence_payload=dict(evidence.evidence_payload),
                    timing=timing,
                    hard_vetoes=tuple(
                        sorted(
                            {
                                *evidence.hard_vetoes,
                                *scenario_input_pack.entry_vetoes,
                                *coverage_vetoes,
                            }
                        )
                    ),
                    scenario_input_pack=scenario_input_pack,
                )

        evidence_hash = hashlib.sha256(
            canonical_json(
                {
                    "observations": dict(evidence.observations),
                    "evidence_payload": dict(evidence.evidence_payload),
                    "observed_at": evidence.observed_at,
                    "available_at": evidence.available_at,
                    "ingested_at": evidence.ingested_at,
                }
            ).encode("utf-8")
        ).hexdigest()
        source_payload = {
            "provider": self._provider_name,
            "provider_snapshot_id": str(provider_snapshot.snapshot_id),
            "evidence_hash": evidence_hash,
            "evidence_level": (
                self._live_evidence_level
                if self._evidence_provider is not None
                else "STATIC_PIT_OVERRIDE"
            ),
            "stock_symbol": self._stock_symbol,
            "benchmark_symbol": self._benchmark_symbol,
            "collected_at": timing.ingested_at.isoformat(),
            "observed_at": evidence.observed_at.isoformat(),
            "available_at": evidence.available_at.isoformat(),
            "latest_completed_session": (
                max(bar.bar_end.date() for bar in snapshot.price_bars)
                if scenario_input_pack is None
                else scenario_input_pack.provenance.latest_completed_session
            ).isoformat(),
            "price_basis": ScenarioPriceBasis.RAW.value,
            "price_adjustment_semantics": self._price_adjustment_semantics,
            "corporate_action_count": len(snapshot.corporate_actions),
            "corporate_action_coverage_status": (
                "VERIFIED"
                if corporate_action_coverage_verified
                else "UNVERIFIED"
            ),
            "corporate_action_coverage_scope": (
                self._corporate_action_coverage_scope
                if corporate_action_coverage_verified
                else "UNVERIFIED"
            ),
            "corporate_action_covered_symbols": sorted(
                item.provider_symbol for item in corporate_action_coverage_items
            ),
            "excluded_incomplete_sessions": (
                []
                if scenario_input_pack is None
                else [
                    item.isoformat()
                    for item in scenario_input_pack.provenance.excluded_incomplete_sessions
                ]
            ),
            "incomplete_session_filter": self._incomplete_session_filter,
            "source_as_of": _source_as_of_disclosures(
                evidence=evidence,
                price_quality=snapshot.quality,
                market=self._market,
                price_provider=self._provider_name,
            ),
            "broker_called": False,
        }
        return PipelineSnapshot(
            data_snapshot_id=snapshot.snapshot_id,
            field_quality=field_quality,
            leadership_snapshot=_leadership_snapshot(
                as_of=as_of,
                ingested_at=timing.ingested_at,
                source_payload=source_payload,
                evidence_ids=tuple(sorted(evidence_ids)),
                regime_score=evidence.observations[
                    "regime_trend_compatibility"
                ],
                quality=snapshot.quality,
                quality_reasons=quality_decision.reasons,
                observed_at=evidence.observed_at,
                available_at=evidence.available_at,
                market=self._market,
                price_provider=self._provider_name,
            ),
            source_payload=source_payload,
            strategy_inputs=strategy_inputs,
        )


def _enrich_market_snapshot(
    snapshot: MarketSnapshot,
    evidence: KRPITEvidence,
    *,
    security_ids: tuple[SecurityId, SecurityId],
) -> MarketSnapshot:
    mappings = list(snapshot.symbol_mappings)
    mapped_ids = tuple(item.security_id for item in mappings)
    for security_id in security_ids:
        if security_id in mapped_ids:
            continue
        bars = sorted(
            (item for item in snapshot.price_bars if item.security_id == security_id),
            key=lambda item: item.bar_end,
        )
        if not bars:
            continue
        latest = bars[-1]
        mappings.append(
            SymbolMapping(
                security_id=security_id,
                provider=latest.provider,
                provider_symbol=latest.provider_symbol,
                market=snapshot.market,
                valid_from=bars[0].bar_start,
                valid_to=None,
                timing=ObservationTime(
                    observed_at=latest.timing.observed_at,
                    available_at=latest.timing.available_at,
                    ingested_at=latest.timing.ingested_at,
                    as_of_date=snapshot.as_of_date,
                ),
                source_hash=latest.source_hash,
            )
        )
    if (
        not evidence.fundamentals
        and not evidence.evidence_items
        and tuple(mappings) == snapshot.symbol_mappings
    ):
        return snapshot
    identity_payload = {
        "provider_snapshot_id": str(snapshot.snapshot_id),
        "provider_content_hash": snapshot.content_hash,
        "symbol_mappings": [item.model_dump(mode="json") for item in mappings],
        "fundamentals": [
            item.model_dump(mode="json") for item in evidence.fundamentals
        ],
        "evidence": [item.model_dump(mode="json") for item in evidence.evidence_items],
    }
    content_hash = hashlib.sha256(
        canonical_json(identity_payload).encode("utf-8")
    ).hexdigest()
    return MarketSnapshot(
        snapshot_id=uuid5(NAMESPACE_URL, f"prism-product-snapshot:{content_hash}"),
        market=snapshot.market,
        as_of_date=snapshot.as_of_date,
        created_at=max(snapshot.created_at, evidence.ingested_at),
        content_hash=content_hash,
        quality=snapshot.quality,
        symbol_mappings=tuple(mappings),
        price_bars=snapshot.price_bars,
        fundamentals=(*snapshot.fundamentals, *evidence.fundamentals),
        corporate_actions=snapshot.corporate_actions,
        evidence=(*snapshot.evidence, *evidence.evidence_items),
    )


def _source_as_of_disclosures(
    *,
    evidence: KRPITEvidence,
    price_quality: DataQualityStatus,
    market: Market,
    price_provider: str,
) -> dict[str, dict[str, str]]:
    disclosures: dict[str, dict[str, str]] = {}
    for evidence_id, raw in sorted(evidence.evidence_payload.items()):
        payload = raw if isinstance(raw, Mapping) else {}
        if evidence_id.startswith(("kis:", "fmp:price:")):
            label = "FMP price" if price_provider == "FMP" else price_provider
            default_source = f"{price_provider} daily market data"
            quality = price_quality.value
        elif evidence_id.startswith("fmp:"):
            label = "FMP fundamentals" if price_provider == "FMP" else "FMP"
            default_source = "FMP supplemental fundamentals"
            quality = "PIT_AVAILABLE"
        elif evidence_id.startswith("agentnews:"):
            label = "AgentNews"
            default_source = f"AgentNews {market.value}"
            quality = str(payload.get("quality", "UNAVAILABLE"))
        else:
            label = evidence_id
            default_source = "PIT evidence"
            quality = "PIT_AVAILABLE"
        as_of = next(
            (
                str(payload[name])
                for name in (
                    "source_updated_at",
                    "current_accepted_at",
                    "latest_completed_session",
                    "fetched_at",
                    "available_at",
                )
                if payload.get(name) is not None
            ),
            evidence.available_at.isoformat(),
        )
        disclosures[label] = {
            "source": str(payload.get("source", default_source)),
            "as_of": as_of,
            "quality": quality,
        }
    return disclosures


def _score_policy(strategy_id: StrategyId) -> QuantScorePolicy:
    if strategy_id is StrategyId.SWING_V1:
        rules = (
            QuantScoreRule(
                "swing_v1.price_momentum_5d",
                Decimal("-0.10"),
                Decimal("0.10"),
                Decimal("0.6"),
            ),
            QuantScoreRule(
                "swing_v1.regime_compatibility",
                Decimal("0"),
                Decimal("100"),
                Decimal("0.4"),
            ),
        )
        version = "swing-score.shadow.v1"
    else:
        rules = (
            QuantScoreRule(
                "trend_v1.regime_compatibility",
                Decimal("0"),
                Decimal("100"),
                Decimal("1"),
            ),
        )
        version = "trend-score.shadow.v1"
    return QuantScorePolicy(strategy_id=strategy_id, score_version=version, rules=rules)


def _leadership_snapshot(
    *,
    as_of: datetime,
    ingested_at: datetime,
    source_payload: Mapping[str, Any],
    evidence_ids: tuple[str, ...],
    regime_score: Decimal,
    quality: DataQualityStatus,
    quality_reasons: tuple[str, ...],
    observed_at: datetime,
    available_at: datetime,
    market: Market,
    price_provider: str,
) -> MarketTrackingSnapshot:
    source_hash = hashlib.sha256(
        canonical_json(dict(source_payload)).encode("utf-8")
    ).hexdigest()
    if regime_score >= Decimal("65"):
        regime = MarketRegime.MODERATE_BULL
    elif regime_score <= Decimal("35"):
        regime = MarketRegime.MODERATE_BEAR
    else:
        regime = MarketRegime.SIDEWAYS
    return MarketTrackingSnapshot.model_validate(
        {
            "schema_version": "market_tracking_v1",
            "run_id": f"phase1-{market.value.lower()}-{source_payload['provider_snapshot_id']}",
            "revision": 0,
            "market": LeadershipMarket(market.value),
            "kst_slot": 19 if market is Market.KR else 7,
            "stage": MarketStage.CLOSE,
            "confirmation_state": ConfirmationState.CONFIRMED,
            "as_of": as_of,
            "observed_at": observed_at,
            "available_at": available_at,
            "ingested_at": max(as_of, ingested_at),
            "source": {
                "report_path": f"phase1/{market.value.lower()}-live-shadow.md",
                "report_sha256": source_hash,
                "source_urls": (),
                "evidence_refs": evidence_ids,
            },
            "quality": quality,
            "quality_reasons": quality_reasons,
            "core_evidence_usable": quality is DataQualityStatus.FRESH,
            "leader_universe_complete": False,
            "market_state": {
                "regime": regime,
                "summary": (
                    f"{price_provider} 가격과 명시적 PIT 증거로 구성한 "
                    "SHADOW 시장 상태입니다."
                ),
                "evidence_refs": evidence_ids,
            },
            "events": (),
            "leaders": (),
        }
    )


USPITEvidence = KRPITEvidence


class USProductSnapshotComposer(KRProductSnapshotComposer):
    """Compose FMP-primary US prices and US evidence through the shared contract."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(
            **kwargs,
            market=Market.US,
            provider_name="FMP",
            live_evidence_level="LIVE_FMP_AGENTNEWS_PIT",
            price_adjustment_semantics="FMP_NON_SPLIT_ADJUSTED_ENDPOINT",
            incomplete_session_filter="FMP_HTTP_PRE_NORMALIZATION",
            corporate_action_coverage_scope="SPLITS_ONLY",
        )
