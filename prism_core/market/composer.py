"""Read-only KIS and AgentNews composition for one shared KR market context."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time
from decimal import Context, Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
from typing import Callable, Mapping, Protocol, cast
from zoneinfo import ZoneInfo

import prism_core.market.kr_group_leadership as kr_group_leadership
import prism_core.market.kr_metrics as kr_metrics
from prism_core.data.contracts import DataQualityStatus
from prism_core.data.exchange_calendar import (
    ExchangeCalendarUnavailableError,
    ExchangeMarket,
    is_exchange_session,
    latest_completed_session as resolve_latest_completed_session,
)
from prism_core.data.providers.agentnews import AgentNewsFetchResult
from prism_core.data.providers.agentnews_models import AgentNewsBoard
from prism_core.data.providers.kis import ProviderPayload
from prism_core.market.context import (
    DeterministicMetric,
    GroupLeadershipState,
    KRMarketContext,
    MarketContextTiming,
    RegimeAssessment,
    SessionState,
    SourceClock,
    SourceRole,
    SupplementalEvidence,
    derive_context_quality,
)


_MIN_GROUP_TURNOVER_KRW = Decimal("100000000")
_DECIMAL_CONTEXT = Context(prec=28, rounding=ROUND_HALF_EVEN)


class KISMarketContextTransport(Protocol):
    """Narrow KIS quotation-only port with no account or order capability."""

    async def fetch_volume_rank(self) -> ProviderPayload: ...


class KRXMarketContextProvider(Protocol):
    """Official KRX index, equity-universe breadth, and flow boundary."""

    async def fetch_market_context(self, *, as_of: datetime) -> ProviderPayload: ...


class AgentNewsKRContextProvider(Protocol):
    """Public read-only AgentNews port."""

    async def fetch_result(self, board: AgentNewsBoard) -> AgentNewsFetchResult: ...


class KRMarketContextComposer:
    """Compose one immutable context for sharing across a complete KR batch."""

    def __init__(
        self,
        *,
        kis_transport: KISMarketContextTransport,
        krx_provider: KRXMarketContextProvider | None = None,
        agentnews_provider: AgentNewsKRContextProvider,
        clock: Callable[[], datetime],
    ) -> None:
        self._kis_transport = kis_transport
        self._krx_provider = krx_provider
        self._agentnews_provider = agentnews_provider
        self._clock = clock

    async def _fetch_krx_optional(self, *, as_of: datetime) -> ProviderPayload | None:
        if self._krx_provider is None:
            return None
        try:
            return await self._krx_provider.fetch_market_context(as_of=as_of)
        except Exception:
            return None

    async def compose(self) -> KRMarketContext:
        request_as_of = self._clock()
        if request_as_of.tzinfo is None or request_as_of.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")
        requests = [
            self._kis_transport.fetch_volume_rank(),
            self._agentnews_provider.fetch_result(AgentNewsBoard.KR),
        ]
        requests.append(self._fetch_krx_optional(as_of=request_as_of))
        results = await asyncio.gather(*requests, return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                raise result
        kis_payload = results[0]
        agentnews_result = results[1]
        krx_payload = results[2]
        if not isinstance(kis_payload, ProviderPayload):
            raise TypeError("KIS market context transport returned an invalid payload")
        if not isinstance(agentnews_result, AgentNewsFetchResult):
            raise TypeError("AgentNews context provider returned an invalid result")
        if krx_payload is not None and not isinstance(krx_payload, ProviderPayload):
            raise TypeError("KRX market context provider returned an invalid payload")
        if kis_payload.provider != "KIS":
            raise ValueError("KR market context requires the KIS primary provider")
        if krx_payload is not None and krx_payload.provider != "KRX":
            raise ValueError("KR market context official provider must be KRX")
        as_of = self._clock()
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")
        agentnews = agentnews_result.snapshot
        if (
            kis_payload.available_at > as_of
            or agentnews.fetched_at > as_of
            or (krx_payload is not None and krx_payload.available_at > as_of)
        ):
            raise ValueError("provider evidence cannot be available after context as_of")
        agentnews_decision_clocks = (
            agentnews.freshness_evaluated_at,
            *(attempt.fetched_at for attempt in agentnews_result.attempts),
        )
        if any(
            clock.tzinfo is None
            or clock.utcoffset() is None
            or clock > as_of
            for clock in agentnews_decision_clocks
        ):
            raise ValueError("AgentNews decision clock cannot follow context as_of")
        if agentnews_result.used_last_known_good != (
            agentnews.fallback_reason is not None
        ):
            raise ValueError("AgentNews fallback provenance is inconsistent")

        ranking_session = kis_payload.payload.get("ranking_session")
        if not isinstance(ranking_session, Mapping):
            raise ValueError("KIS volume rank omitted ranking_session provenance")
        session_date_raw = ranking_session.get("latest_completed_session")
        session_state_raw = ranking_session.get("state")
        if not isinstance(session_date_raw, str) or not isinstance(
            session_state_raw, str
        ):
            raise ValueError("KIS ranking_session provenance is malformed")
        try:
            session_date = datetime.fromisoformat(session_date_raw).date()
        except ValueError:
            raise ValueError("KIS latest completed session is invalid") from None
        session_state = resolve_session_state(
            provider_state=session_state_raw,
            observed_at=kis_payload.observed_at,
            latest_completed_session=session_date,
        )

        kis_evidence_id = kis_payload.source_record_id
        agentnews_evidence_id = agentnews.source_record_id
        source_clock_items = [
            SourceClock(
                source="AgentNews",
                role=SourceRole.SUPPLEMENTAL,
                observed_at=agentnews.source_updated_at or agentnews.fetched_at,
                available_at=agentnews.fetched_at,
                ingested_at=as_of,
                quality=agentnews.quality,
                evidence_ids=(agentnews_evidence_id,),
            ),
            SourceClock(
                source="KIS",
                role=SourceRole.PRIMARY,
                observed_at=kis_payload.observed_at,
                available_at=kis_payload.available_at,
                ingested_at=as_of,
                quality=kis_payload.quality,
                evidence_ids=(kis_evidence_id,),
            ),
        ]
        index_state: tuple[DeterministicMetric, ...] = ()
        breadth: tuple[DeterministicMetric, ...] = ()
        investor_flows: tuple[DeterministicMetric, ...] = ()
        group_leadership = ()
        group_state = GroupLeadershipState(
            session_date=session_date,
            quality=DataQualityStatus.UNAVAILABLE,
            missing_fields=("group_leadership",),
            reason_codes=("KRX_GROUP_LEADERSHIP_UNAVAILABLE",),
            source_evidence_ids=(kis_evidence_id,),
        )
        group_conflicts: tuple[str, ...] = ()
        regime = RegimeAssessment.unknown(
            missing_features=(
                "kospi_close",
                "kospi_ma20",
                "kospi_return_10d_pct",
            )
        )
        optional_missing_sources = ["DART", "KIND"]
        if krx_payload is None:
            optional_missing_sources.append("KRX")
        core_missing = {
            "index_state",
            "breadth",
            "group_leadership",
            "regime_features",
        }
        evidence_ids = [agentnews_evidence_id, kis_evidence_id]
        if krx_payload is not None:
            krx_session_raw = krx_payload.payload.get("session_date")
            if not isinstance(krx_session_raw, str):
                raise ValueError("KRX completed session provenance is missing")
            try:
                krx_session_date = date.fromisoformat(krx_session_raw)
            except ValueError:
                raise ValueError("KRX completed session provenance is invalid") from None
            if krx_session_date != session_date:
                raise ValueError("KIS and KRX completed sessions do not match")
            krx_evidence_id = krx_payload.source_record_id
            source_clock_items.append(
                SourceClock(
                    source="KRX",
                    role=SourceRole.OFFICIAL,
                    observed_at=krx_payload.observed_at,
                    available_at=krx_payload.available_at,
                    ingested_at=as_of,
                    quality=krx_payload.quality,
                    evidence_ids=(krx_evidence_id,),
                )
            )
            evidence_ids.append(krx_evidence_id)
            computed = _compute_krx_metrics(
                krx_payload,
                evidence_id=krx_evidence_id,
                session_date=session_date,
            )
            index_state = computed.index_state
            breadth = computed.breadth
            investor_flows = computed.investor_flows
            regime = computed.regime
            core_missing.clear()
            if "group_taxonomy" in krx_payload.payload:
                try:
                    group_computation = _compute_group_leadership(
                        krx_payload=krx_payload,
                        kis_payload=kis_payload,
                        session_date=session_date,
                        krx_evidence_id=krx_evidence_id,
                        kis_evidence_id=kis_evidence_id,
                    )
                except (InvalidOperation, KeyError, TypeError, ValueError):
                    group_state = GroupLeadershipState(
                        session_date=session_date,
                        quality=DataQualityStatus.UNAVAILABLE,
                        missing_fields=("group_leadership",),
                        reason_codes=("KRX_GROUP_LEADERSHIP_INVALID",),
                        source_evidence_ids=(krx_evidence_id,),
                    )
                    core_missing.add("group_leadership")
                else:
                    group_leadership = group_computation.ranked_groups
                    group_conflicts = group_computation.conflicts
                    group_state = GroupLeadershipState(
                        session_date=group_computation.session_date,
                        taxonomy_version=group_computation.taxonomy_version,
                        quality=group_computation.quality,
                        conflicts=group_computation.conflicts,
                        missing_fields=group_computation.missing_fields,
                        reason_codes=group_computation.reason_codes,
                        excluded_groups=group_computation.excluded_groups,
                        source_evidence_ids=group_computation.source_evidence_ids,
                    )
                    if not (
                        group_state.quality is DataQualityStatus.FRESH
                        and group_leadership
                    ):
                        core_missing.add("group_leadership")
            else:
                availability = krx_payload.payload.get("group_leadership_availability")
                reason_codes = ("KRX_GROUP_LEADERSHIP_UNAVAILABLE",)
                if isinstance(availability, Mapping):
                    raw_reasons = availability.get("reason_codes")
                    if isinstance(raw_reasons, list) and all(
                        isinstance(item, str) and item for item in raw_reasons
                    ):
                        reason_codes = tuple(sorted(set(raw_reasons)))
                group_state = GroupLeadershipState(
                    session_date=session_date,
                    quality=DataQualityStatus.UNAVAILABLE,
                    missing_fields=("group_leadership",),
                    reason_codes=reason_codes,
                    source_evidence_ids=(krx_evidence_id,),
                )
                core_missing.add("group_leadership")
            if "breadth" in computed.missing_fields:
                core_missing.add("breadth")
            if any(
                name in computed.missing_fields
                for name in (
                    "kospi_close",
                    "kospi_ma20",
                    "kospi_return_10d_pct",
                )
            ):
                core_missing.update(("index_state", "regime_features"))
            missing_flow_markets = krx_payload.payload.get(
                "investor_flow_missing_markets", ()
            )
            if not isinstance(missing_flow_markets, (list, tuple)) or any(
                market not in {"KOSPI", "KOSDAQ"}
                for market in missing_flow_markets
            ):
                raise ValueError("KRX missing investor-flow markets are malformed")
            optional_missing_sources.extend(
                f"KRX_INVESTOR_FLOWS_{market}"
                for market in sorted(set(missing_flow_markets))
            )
            if not investor_flows:
                optional_missing_sources.append("KRX_INVESTOR_FLOWS")
        source_clocks = tuple(sorted(source_clock_items, key=lambda item: item.source))
        missing_fields = tuple(sorted(core_missing))
        conflict_items = set(group_conflicts)
        if kis_payload.quality is DataQualityStatus.CONFLICT:
            conflict_items.add("KIS_PRIMARY_QUALITY_CONFLICT")
        if krx_payload is not None and krx_payload.quality is DataQualityStatus.CONFLICT:
            conflict_items.add("KRX_OFFICIAL_QUALITY_CONFLICT")
        conflicts = tuple(sorted(conflict_items))
        primary_source_clocks = tuple(
            source
            for source in source_clocks
            if source.role in {SourceRole.PRIMARY, SourceRole.OFFICIAL}
        )
        quality = derive_context_quality(
            # AgentNews is supplemental and visibly quality-scored below; it
            # cannot redefine deterministic KIS market-data quality.
            source_clocks=primary_source_clocks,
            conflicts=conflicts,
            missing_fields=missing_fields,
        )
        with localcontext(_DECIMAL_CONTEXT):
            return KRMarketContext(
                timing=MarketContextTiming(
                    session_date=session_date,
                    session_state=session_state,
                    as_of=as_of,
                    ingested_at=as_of,
                ),
                source_clocks=source_clocks,
                index_state=index_state,
                breadth=breadth,
                investor_flows=investor_flows,
                macro_indicators=(),
                group_leadership=group_leadership,
                group_leadership_state=group_state,
                regime=regime,
                supplemental_evidence=(
                    SupplementalEvidence(
                        evidence_id=agentnews_evidence_id,
                        provider="AgentNews",
                        role=agentnews.role.value,
                        trust=agentnews.trust.value,
                        executable=agentnews.executable,
                        quality=agentnews.quality,
                    ),
                ),
                evidence_ids=tuple(sorted(evidence_ids)),
                quality=quality,
                conflicts=conflicts,
                missing_fields=missing_fields,
                optional_missing_sources=tuple(sorted(optional_missing_sources)),
            )


def resolve_session_state(
    *,
    provider_state: str,
    observed_at: datetime,
    latest_completed_session: date,
) -> SessionState:
    """Resolve mutable state only when the KRX calendar confirms a session."""
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("KIS observed_at must be timezone-aware")
    market_observed_at = observed_at.astimezone(ZoneInfo("Asia/Seoul"))
    market_date = market_observed_at.date()
    if latest_completed_session > market_date:
        raise ValueError("latest completed session cannot follow KIS observation")
    if provider_state == "COMPLETE_CURRENT_SESSION":
        try:
            official_completed_session = resolve_latest_completed_session(
                ExchangeMarket.KRX, observed_at
            )
        except ExchangeCalendarUnavailableError:
            return SessionState.UNKNOWN
        if (
            official_completed_session == market_date
            and latest_completed_session == official_completed_session
        ):
            return SessionState.COMPLETE
        return SessionState.UNKNOWN
    if provider_state != "UNVERIFIED_MUTABLE_SNAPSHOT":
        return SessionState.UNKNOWN
    try:
        observed_on_session = is_exchange_session(ExchangeMarket.KRX, market_date)
    except ExchangeCalendarUnavailableError:
        return SessionState.UNKNOWN
    if not observed_on_session:
        return SessionState.PRIOR_CLOSE
    market_time = market_observed_at.timetz()
    if time(9, 0, tzinfo=market_time.tzinfo) <= market_time < time(
        15, 31, tzinfo=market_time.tzinfo
    ):
        return SessionState.INTRADAY
    return SessionState.PRIOR_CLOSE


def _compute_krx_metrics(
    payload: ProviderPayload, *, evidence_id: str, session_date: date
) -> kr_metrics.KRComputedMetrics:
    rows = payload.payload.get("index_history")
    if not isinstance(rows, list) or len(rows) < 20:
        raise ValueError("KRX index history requires at least 20 completed sessions")
    normalized: list[tuple[date, Decimal | int | str]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("KRX index history contains a malformed row")
        try:
            trade_date = date.fromisoformat(str(row.get("trade_date", "")))
        except ValueError:
            raise ValueError("KRX index history contains an invalid trade date") from None
        close = row.get("close")
        if not isinstance(close, (Decimal, int, str)):
            raise ValueError("KRX index close is not numeric")
        normalized.append((trade_date, close))
    breadth = payload.payload.get("equity_breadth")
    if not isinstance(breadth, Mapping) or breadth.get("universe") != "KOSPI_KOSDAQ_EQUITIES":
        raise ValueError("KRX breadth must use the KOSPI/KOSDAQ equity universe")
    breadth_counts = {
        name: breadth.get(name)
        for name in (
            "advance_count",
            "decline_count",
            "eligible_equity_count",
            "excluded_non_equity_count",
            "unclassified_equity_count",
            "unchanged_count",
        )
    }
    flows = payload.payload.get("investor_flows")
    if flows is not None and not isinstance(flows, Mapping):
        raise ValueError("KRX investor flows must be an object when present")
    return kr_metrics.compute_kr_market_metrics(
        session_date=session_date,
        index_history=normalized,
        breadth_counts=cast(Mapping[str, Decimal | int | str], breadth_counts),
        investor_flows=cast(Mapping[str, Decimal | int | str] | None, flows),
        source_quality=payload.quality,
        evidence_id=evidence_id,
    )


def _combined_source_quality(
    kis_quality: DataQualityStatus, krx_quality: DataQualityStatus
) -> DataQualityStatus:
    for status in (
        DataQualityStatus.CONFLICT,
        DataQualityStatus.UNAVAILABLE,
        DataQualityStatus.STALE,
        DataQualityStatus.PARTIAL,
    ):
        if status in {kis_quality, krx_quality}:
            return status
    return DataQualityStatus.FRESH


def _compute_group_leadership(
    *,
    krx_payload: ProviderPayload,
    kis_payload: ProviderPayload,
    session_date: date,
    krx_evidence_id: str,
    kis_evidence_id: str,
) -> kr_group_leadership.KRGroupLeadershipComputation:
    taxonomy = krx_payload.payload.get("group_taxonomy")
    rows = krx_payload.payload.get("group_observations")
    index_rows = krx_payload.payload.get("index_history")
    if not isinstance(taxonomy, Mapping):
        raise ValueError("KRX group taxonomy must be an object")
    version = taxonomy.get("version")
    assignments_raw = taxonomy.get("assignments")
    if not isinstance(version, str) or not isinstance(assignments_raw, list):
        raise ValueError("KRX group taxonomy is malformed")
    if not isinstance(rows, list) or not isinstance(index_rows, list) or len(index_rows) < 2:
        raise ValueError("KRX group observations require price and benchmark history")
    assignments = tuple(
        kr_group_leadership.GroupTaxonomyAssignment.model_validate(item)
        for item in assignments_raw
    )
    krx_observations = tuple(
        kr_group_leadership.GroupSecurityObservation(
            provider_symbol=str(item["provider_symbol"]),
            current_close=Decimal(str(item["current_close"])),
            previous_close=Decimal(str(item["previous_close"])),
            turnover_krw=Decimal(str(item["turnover_krw"])),
            evidence_ids=(krx_evidence_id,),
        )
        for item in rows
        if isinstance(item, Mapping)
    )
    previous_index_close = Decimal(str(index_rows[-2]["close"]))
    current_index_close = Decimal(str(index_rows[-1]["close"]))
    if previous_index_close <= 0:
        raise ValueError("KRX benchmark previous close must be positive")
    with localcontext(_DECIMAL_CONTEXT):
        benchmark_return_pct = (
            current_index_close / previous_index_close - Decimal(1)
        ) * Decimal(100)
    rank_rows = kis_payload.payload.get("volume_rank", ())
    if not isinstance(rank_rows, (list, tuple)):
        raise ValueError("KIS volume rank must be a collection")
    kis_observations = []
    for item in rank_rows:
        if not isinstance(item, Mapping):
            continue
        symbol = item.get("mksc_shrn_iscd")
        return_pct = item.get("prdy_ctrt")
        turnover = item.get("acml_tr_pbmn")
        if not symbol or (return_pct is None and turnover is None):
            continue
        if return_pct is None or turnover is None:
            raise ValueError("KIS group numeric evidence is incomplete")
        kis_observations.append(
            kr_group_leadership.KISGroupSecurityObservation(
                provider_symbol=str(symbol),
                return_pct=Decimal(str(return_pct)),
                turnover_krw=Decimal(str(turnover)),
                evidence_ids=(kis_evidence_id,),
            )
        )
    return kr_group_leadership.compute_kr_group_leadership(
        session_date=session_date,
        taxonomy_version=version,
        taxonomy_assignments=assignments,
        krx_observations=krx_observations,
        kis_observations=tuple(kis_observations),
        benchmark_return_pct=benchmark_return_pct,
        source_quality=_combined_source_quality(
            kis_payload.quality, krx_payload.quality
        ),
        source_evidence_ids=(krx_evidence_id, kis_evidence_id),
        min_group_turnover_krw=_MIN_GROUP_TURNOVER_KRW,
    )
