"""Read-only KIS and AgentNews composition for one shared KR market context."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Callable, Mapping, Protocol
from zoneinfo import ZoneInfo

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
    KRMarketContext,
    MarketContextTiming,
    RegimeAssessment,
    SessionState,
    SourceClock,
    SourceRole,
    SupplementalEvidence,
    derive_context_quality,
)


class KISMarketContextTransport(Protocol):
    """Narrow KIS quotation-only port with no account or order capability."""

    async def fetch_volume_rank(self) -> ProviderPayload: ...


class AgentNewsKRContextProvider(Protocol):
    """Public read-only AgentNews port."""

    async def fetch_result(self, board: AgentNewsBoard) -> AgentNewsFetchResult: ...


class KRMarketContextComposer:
    """Compose one immutable context for sharing across a complete KR batch."""

    def __init__(
        self,
        *,
        kis_transport: KISMarketContextTransport,
        agentnews_provider: AgentNewsKRContextProvider,
        clock: Callable[[], datetime],
    ) -> None:
        self._kis_transport = kis_transport
        self._agentnews_provider = agentnews_provider
        self._clock = clock

    async def compose(self) -> KRMarketContext:
        kis_payload, agentnews_result = await asyncio.gather(
            self._kis_transport.fetch_volume_rank(),
            self._agentnews_provider.fetch_result(AgentNewsBoard.KR),
        )
        if kis_payload.provider != "KIS":
            raise ValueError("KR market context requires the KIS primary provider")
        as_of = self._clock()
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")
        agentnews = agentnews_result.snapshot
        if kis_payload.available_at > as_of or agentnews.fetched_at > as_of:
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
        source_clocks = (
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
        )
        breadth, breadth_missing = _breadth_metrics(
            kis_payload, evidence_id=kis_evidence_id
        )
        missing_fields = tuple(
            sorted(
                {
                    "group_leadership",
                    "index_state",
                    "investor_flows",
                    "macro_indicators",
                    "regime_features",
                    *breadth_missing,
                }
            )
        )
        conflicts = (
            ("KIS_PRIMARY_QUALITY_CONFLICT",)
            if kis_payload.quality is DataQualityStatus.CONFLICT
            else ()
        )
        primary_source_clocks = tuple(
            source
            for source in source_clocks
            if source.role is SourceRole.PRIMARY
        )
        quality = derive_context_quality(
            # AgentNews is supplemental and visibly quality-scored below; it
            # cannot redefine deterministic KIS market-data quality.
            source_clocks=primary_source_clocks,
            conflicts=conflicts,
            missing_fields=missing_fields,
        )
        evidence_ids = tuple(sorted((agentnews_evidence_id, kis_evidence_id)))
        return KRMarketContext(
            timing=MarketContextTiming(
                session_date=session_date,
                session_state=session_state,
                as_of=as_of,
                ingested_at=as_of,
            ),
            source_clocks=source_clocks,
            index_state=(),
            breadth=breadth,
            investor_flows=(),
            macro_indicators=(),
            group_leadership=(),
            regime=RegimeAssessment.unknown(
                missing_features=(
                    "kospi_close",
                    "kospi_ma20",
                    "kospi_return_10d_pct",
                )
            ),
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
            evidence_ids=evidence_ids,
            quality=quality,
            conflicts=conflicts,
            missing_fields=missing_fields,
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


def _breadth_metrics(
    payload: ProviderPayload, *, evidence_id: str
) -> tuple[tuple[DeterministicMetric, ...], tuple[str, ...]]:
    rows = payload.payload.get("volume_rank")
    if not isinstance(rows, list):
        raise ValueError("KIS volume rank omitted the row list")
    changes: list[Decimal] = []
    malformed_change = False
    for row in rows:
        if not isinstance(row, Mapping):
            malformed_change = True
            continue
        try:
            change = Decimal(str(row.get("prdy_ctrt", "")))
        except (InvalidOperation, ValueError):
            malformed_change = True
            continue
        if not change.is_finite():
            malformed_change = True
            continue
        changes.append(change)
    values = {
        "volume_rank_advance_count": sum(change > 0 for change in changes),
        "volume_rank_decline_count": sum(change < 0 for change in changes),
        "volume_rank_returned_security_count": len(rows),
        "volume_rank_unchanged_count": sum(change == 0 for change in changes),
    }
    metrics = tuple(
        DeterministicMetric(
            name=name,
            value=Decimal(value),
            unit="count",
            source="KIS",
            evidence_ids=(evidence_id,),
        )
        for name, value in sorted(values.items())
    )
    missing = (
        ("breadth.volume_rank",)
        if not rows
        else (("breadth.advance_decline",) if malformed_change else ())
    )
    return metrics, missing
