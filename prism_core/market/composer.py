"""Read-only KIS and AgentNews composition for one shared KR market context."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time
from decimal import Context, Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
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
    RegimeFeature,
    SessionState,
    SourceClock,
    SourceRole,
    SupplementalEvidence,
    classify_kr_regime,
    derive_context_quality,
)


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
        core_missing = {"index_state", "breadth", "regime_features"}
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
            index_state, regime = _index_metrics(
                krx_payload, evidence_id=krx_evidence_id, session_date=session_date
            )
            breadth = _authoritative_breadth_metrics(
                krx_payload, evidence_id=krx_evidence_id
            )
            investor_flows = _investor_flow_metrics(
                krx_payload, evidence_id=krx_evidence_id
            )
            core_missing.clear()
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
        conflicts = (
            ("KIS_PRIMARY_QUALITY_CONFLICT",)
            if kis_payload.quality is DataQualityStatus.CONFLICT
            else ()
        )
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
            group_leadership=(),
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


def _metric_tuple(
    values: Mapping[str, Decimal], *, unit: str, source: str, evidence_id: str
) -> tuple[DeterministicMetric, ...]:
    return tuple(
        DeterministicMetric(
            name=name,
            value=value,
            unit=unit,
            source=source,
            evidence_ids=(evidence_id,),
        )
        for name, value in sorted(values.items())
    )


def _finite_decimal(value: object, *, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"KRX {field} is not numeric") from None
    if not result.is_finite():
        raise ValueError(f"KRX {field} is not finite")
    return result


def _index_metrics(
    payload: ProviderPayload, *, evidence_id: str, session_date: date
) -> tuple[tuple[DeterministicMetric, ...], RegimeAssessment]:
    rows = payload.payload.get("index_history")
    if not isinstance(rows, list) or len(rows) < 20:
        raise ValueError("KRX index history requires at least 20 completed sessions")
    normalized: list[tuple[date, Decimal]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("KRX index history contains a malformed row")
        try:
            trade_date = date.fromisoformat(str(row.get("trade_date", "")))
        except ValueError:
            raise ValueError("KRX index history contains an invalid trade date") from None
        normalized.append((trade_date, _finite_decimal(row.get("close"), field="index close")))
    normalized.sort(key=lambda item: item[0])
    if len({item[0] for item in normalized}) != len(normalized):
        raise ValueError("KRX index history contains duplicate sessions")
    if normalized[-1][0] != session_date:
        raise ValueError("KRX index history does not match the completed KIS session")
    closes = [item[1] for item in normalized]
    if closes[-11] == 0:
        raise ValueError("KRX 10-session return denominator cannot be zero")
    with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
        values = {
            "kospi_close": +closes[-1],
            "kospi_ma20": sum(closes[-20:], start=Decimal(0)) / Decimal(20),
            "kospi_return_10d_pct": (
                closes[-1] / closes[-11] - Decimal(1)
            )
            * Decimal(100),
        }
    metrics = tuple(
        DeterministicMetric(
            name=name,
            value=value,
            unit="percent" if name.endswith("_pct") else "index_points",
            source="KRX",
            evidence_ids=(evidence_id,),
        )
        for name, value in sorted(values.items())
    )
    features = tuple(
        RegimeFeature(
            name=metric.name,
            value=metric.value,
            unit="percent" if metric.name.endswith("_pct") else "index_points",
            evidence_ids=metric.evidence_ids,
        )
        for metric in metrics
    )
    return metrics, classify_kr_regime(features)


def _authoritative_breadth_metrics(
    payload: ProviderPayload, *, evidence_id: str
) -> tuple[DeterministicMetric, ...]:
    raw = payload.payload.get("equity_breadth")
    if not isinstance(raw, Mapping) or raw.get("universe") != "KOSPI_KOSDAQ_EQUITIES":
        raise ValueError("KRX breadth must use the KOSPI/KOSDAQ equity universe")
    values = {
        name: _finite_decimal(raw.get(name), field=f"breadth {name}")
        for name in (
            "advance_count",
            "decline_count",
            "eligible_equity_count",
            "excluded_non_equity_count",
            "unclassified_equity_count",
            "unchanged_count",
        )
    }
    if any(value < 0 or value != value.to_integral_value() for value in values.values()):
        raise ValueError("KRX breadth counts must be non-negative integers")
    if values["advance_count"] + values["decline_count"] + values["unchanged_count"] != values["eligible_equity_count"]:
        raise ValueError("KRX breadth counts must cover the complete eligible equity universe")
    return _metric_tuple(values, unit="count", source="KRX", evidence_id=evidence_id)


def _investor_flow_metrics(
    payload: ProviderPayload, *, evidence_id: str
) -> tuple[DeterministicMetric, ...]:
    raw = payload.payload.get("investor_flows")
    if raw is None:
        return ()
    if not isinstance(raw, Mapping):
        raise ValueError("KRX investor flows must be an object when present")
    values = {
        str(name): _finite_decimal(value, field=f"investor flow {name}")
        for name, value in raw.items()
    }
    return _metric_tuple(values, unit="KRW", source="KRX", evidence_id=evidence_id)
