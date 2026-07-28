"""Automatic read-only KR evidence from KIS prices, FMP fundamentals, and AgentNews."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Awaitable, Callable, Mapping, Protocol
from urllib.parse import urlparse
from uuid import NAMESPACE_URL, uuid5

from prism_app.market_snapshot_composer import KRPITEvidence
from prism_core.data.contracts import (
    DataQualityStatus,
    EvidenceItem,
    FundamentalObservation,
    MarketSnapshot,
    ObservationTime,
    SecurityId,
)
from prism_core.data.providers.agentnews_models import AgentNewsSnapshot


_FMP_ORIGIN = "https://financialmodelingprep.com"
_FMP_INCOME_PATH = "/stable/income-statement"


class LiveKREvidenceError(RuntimeError):
    """Sanitized failure at the automatic read-only evidence boundary."""


@dataclass(frozen=True)
class FMPHTTPJSONResponse:
    status_code: int
    body: bytes = field(repr=False)
    received_at: datetime

    def __post_init__(self) -> None:
        if not 100 <= self.status_code <= 599:
            raise ValueError("status_code must be a valid HTTP status")
        if self.received_at.tzinfo is None or self.received_at.utcoffset() is None:
            raise ValueError("received_at must be timezone-aware")


class FMPJSONRequester(Protocol):
    async def request(self, **kwargs: object) -> FMPHTTPJSONResponse: ...


class AioHttpFMPJSONRequester:
    """Bounded GET-only requester for one exact FMP fundamentals endpoint."""

    async def request(
        self,
        *,
        method: str,
        url: str,
        params: Mapping[str, str],
        total_timeout_seconds: float,
        max_response_bytes: int,
    ) -> FMPHTTPJSONResponse:
        parsed = urlparse(url)
        if (
            method != "GET"
            or parsed.scheme != "https"
            or parsed.hostname != "financialmodelingprep.com"
            or parsed.port not in {None, 443}
            or parsed.path != _FMP_INCOME_PATH
            or parsed.query
            or parsed.fragment
        ):
            raise LiveKREvidenceError("FMP fundamentals URL is not allowlisted")
        try:
            import aiohttp

            timeout = aiohttp.ClientTimeout(total=total_timeout_seconds)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, params=dict(params), allow_redirects=False) as response:
                    declared = response.content_length
                    if declared is not None and declared > max_response_bytes:
                        raise LiveKREvidenceError("FMP fundamentals response exceeded size limit")
                    body = await response.content.read(max_response_bytes + 1)
                    if len(body) > max_response_bytes:
                        raise LiveKREvidenceError("FMP fundamentals response exceeded size limit")
                    return FMPHTTPJSONResponse(
                        status_code=response.status,
                        body=body,
                        received_at=datetime.now(tz=timezone.utc),
                    )
        except asyncio.TimeoutError:
            raise LiveKREvidenceError("FMP fundamentals request timed out") from None
        except LiveKREvidenceError:
            raise
        except Exception:
            raise LiveKREvidenceError("FMP fundamentals request failed") from None


@dataclass(frozen=True)
class FMPIncomeEvidence:
    current: Decimal
    previous: Decimal
    current_period: str
    current_accepted_at: datetime
    current_unit: str
    previous_period: str
    previous_accepted_at: datetime
    previous_unit: str
    ingested_at: datetime
    response_hash: str


class FMPIncomeStatementClient:
    """Fetch two latest point-in-time-available annual income statements."""

    def __init__(
        self,
        *,
        requester: FMPJSONRequester | None = None,
        api_key: str,
        timeout_seconds: float = 15.0,
        max_response_bytes: int = 1_000_000,
    ) -> None:
        if not isinstance(api_key, str) or not api_key:
            raise ValueError("FMP API key is required")
        if timeout_seconds <= 0 or max_response_bytes < 1:
            raise ValueError("FMP request bounds must be positive")
        self._requester = requester or AioHttpFMPJSONRequester()
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._evidence: tuple[dict[str, object], ...] = ()

    def __repr__(self) -> str:
        return "FMPIncomeStatementClient(api_key='[REDACTED]')"

    @property
    def evidence(self) -> tuple[dict[str, object], ...]:
        return tuple(dict(item) for item in self._evidence)

    @classmethod
    def from_env(cls, **kwargs: object) -> "FMPIncomeStatementClient":
        primary = os.environ.get("FMP_API_KEY", "")
        alias = os.environ.get("FINANCIAL_MODELING_PREP_API_KEY", "")
        configured = {value for value in (primary, alias) if value}
        if len(configured) != 1:
            raise LiveKREvidenceError("FMP credential is unavailable or conflicting")
        return cls(api_key=configured.pop(), **kwargs)

    async def fetch(self, *, symbol: str, as_of: datetime) -> FMPIncomeEvidence:
        if not symbol or as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("symbol and timezone-aware as_of are required")
        response = await self._requester.request(
            method="GET",
            url=f"{_FMP_ORIGIN}{_FMP_INCOME_PATH}",
            params={"apikey": self._api_key, "symbol": symbol, "limit": "8"},
            total_timeout_seconds=self._timeout_seconds,
            max_response_bytes=self._max_response_bytes,
        )
        self._evidence = (
            {
                "provider": "FMP",
                "host": "financialmodelingprep.com",
                "endpoint": _FMP_INCOME_PATH,
                "status_code": response.status_code,
                "received_at": response.received_at.isoformat(),
            },
        )
        if not 200 <= response.status_code < 300:
            raise LiveKREvidenceError("FMP fundamentals request was rejected")
        try:
            decoded = json.loads(response.body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise LiveKREvidenceError("FMP fundamentals returned malformed JSON") from None
        if not isinstance(decoded, list):
            raise LiveKREvidenceError("FMP fundamentals returned an unexpected schema")
        available: list[tuple[datetime, str, Decimal, str]] = []
        for row in decoded:
            if not isinstance(row, Mapping) or row.get("symbol") != symbol:
                continue
            try:
                accepted = _provider_timestamp(str(row["acceptedDate"]))
                period = str(row["date"])
                value = Decimal(str(row["netIncome"]))
                unit = str(row.get("reportedCurrency") or "UNKNOWN")
            except (KeyError, InvalidOperation, TypeError, ValueError):
                continue
            if value.is_finite() and accepted <= as_of.astimezone(timezone.utc):
                available.append((accepted, period, value, unit))
        latest_revision_by_period: dict[str, tuple[datetime, str, Decimal, str]] = {}
        for item in available:
            accepted, period, _value, _unit = item
            existing = latest_revision_by_period.get(period)
            if existing is None or accepted > existing[0]:
                latest_revision_by_period[period] = item
        ordered_periods = sorted(latest_revision_by_period, reverse=True)
        if len(ordered_periods) < 2:
            raise LiveKREvidenceError("FMP fundamentals lacks two PIT-available statements")
        current = latest_revision_by_period[ordered_periods[0]]
        previous = latest_revision_by_period[ordered_periods[1]]
        return FMPIncomeEvidence(
            current=current[2],
            previous=previous[2],
            current_period=current[1],
            current_accepted_at=current[0],
            current_unit=current[3],
            previous_period=previous[1],
            previous_accepted_at=previous[0],
            previous_unit=previous[3],
            ingested_at=response.received_at,
            response_hash=hashlib.sha256(response.body).hexdigest(),
        )


def _provider_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalized_scenario_records(
    *,
    income: FMPIncomeEvidence,
    board: AgentNewsSnapshot,
    stock_id: SecurityId,
    benchmark_id: SecurityId,
    as_of: datetime,
    fundamental_quality: DataQualityStatus,
) -> tuple[tuple[FundamentalObservation, ...], tuple[EvidenceItem, ...]]:
    """Bind live supplemental reads to the immutable scenario-input contracts."""

    def fundamental(
        *, period: str, accepted_at: datetime, value: Decimal, unit: str
    ) -> FundamentalObservation:
        period_end = date.fromisoformat(period)
        observed_at = min(
            datetime.combine(period_end, time.min, tzinfo=timezone.utc),
            accepted_at,
        )
        return FundamentalObservation(
            security_id=stock_id,
            provider="FMP",
            provider_symbol="income-statement",
            source_record_id=f"fmp:income:{period}:{accepted_at.isoformat()}",
            source_hash=income.response_hash,
            revision=0,
            timing=ObservationTime(
                observed_at=observed_at,
                available_at=accepted_at,
                ingested_at=max(income.ingested_at, accepted_at),
                as_of_date=as_of,
            ),
            quality=fundamental_quality,
            metric="net_income",
            period_start=date(period_end.year, 1, 1),
            period_end=period_end,
            value=value,
            unit=unit,
        )

    fundamentals = tuple(
        sorted(
            (
                fundamental(
                    period=income.current_period,
                    accepted_at=income.current_accepted_at,
                    value=income.current,
                    unit=income.current_unit,
                ),
                fundamental(
                    period=income.previous_period,
                    accepted_at=income.previous_accepted_at,
                    value=income.previous,
                    unit=income.previous_unit,
                ),
            ),
            key=lambda item: item.period_end,
        )
    )

    def evidence(
        *,
        kind: str,
        title: str,
        provider: str,
        provider_symbol: str,
        source_record_id: str,
        source_url: str,
        source_hash: str,
        observed_at: datetime,
        available_at: datetime,
        ingested_at: datetime,
        security_id: SecurityId,
        quality: DataQualityStatus,
    ) -> EvidenceItem:
        identity = f"{provider}:{kind}:{source_record_id}:{source_hash}"
        return EvidenceItem.model_validate(
            {
                "security_id": security_id,
                "provider": provider,
                "provider_symbol": provider_symbol,
                "source_record_id": source_record_id,
                "source_hash": source_hash,
                "revision": 0,
                "timing": ObservationTime(
                    observed_at=observed_at,
                    available_at=available_at,
                    ingested_at=max(ingested_at, available_at),
                    as_of_date=as_of,
                ),
                "quality": quality,
                "evidence_id": uuid5(NAMESPACE_URL, f"prism-evidence:{identity}"),
                "kind": kind,
                "title": title,
                "source_url": source_url,
                "content_hash": source_hash,
            }
        )

    current_period_end = datetime.combine(
        date.fromisoformat(income.current_period), time.min, tzinfo=timezone.utc
    )
    current_observed_at = min(current_period_end, income.current_accepted_at)
    board_observed_at = board.source_updated_at or board.fetched_at
    review_title = f"PRISM next review at {as_of.isoformat()}"
    review_hash = hashlib.sha256(review_title.encode("utf-8")).hexdigest()
    items = (
        evidence(
            kind="company_filing",
            title=f"FMP annual income statement {income.current_period}",
            provider="FMP",
            provider_symbol="income-statement",
            source_record_id=f"fmp:income:{income.current_period}",
            source_url=f"{_FMP_ORIGIN}{_FMP_INCOME_PATH}",
            source_hash=income.response_hash,
            observed_at=current_observed_at,
            available_at=income.current_accepted_at,
            ingested_at=income.ingested_at,
            security_id=stock_id,
            quality=fundamental_quality,
        ),
        evidence(
            kind="earnings_event",
            title=f"Latest PIT-available annual earnings {income.current_period}",
            provider="FMP",
            provider_symbol="income-statement",
            source_record_id=f"fmp:earnings-event:{income.current_period}",
            source_url=f"{_FMP_ORIGIN}{_FMP_INCOME_PATH}",
            source_hash=income.response_hash,
            observed_at=current_observed_at,
            available_at=income.current_accepted_at,
            ingested_at=income.ingested_at,
            security_id=stock_id,
            quality=fundamental_quality,
        ),
        evidence(
            kind="market_context",
            title="AgentNews Korean market context",
            provider="AgentNews",
            provider_symbol="finance-ko.md",
            source_record_id=f"agentnews:kr:{board.content_hash}",
            source_url=board.url,
            source_hash=board.content_hash,
            observed_at=board_observed_at,
            available_at=board_observed_at,
            ingested_at=board.fetched_at,
            security_id=benchmark_id,
            quality=board.quality,
        ),
        evidence(
            kind="next_review",
            title=review_title,
            provider="PRISM",
            provider_symbol="phase1-shadow",
            source_record_id=f"prism:next-review:{as_of.isoformat()}",
            source_url="http://localhost/prism/phase1-shadow",
            source_hash=review_hash,
            observed_at=as_of,
            available_at=as_of,
            ingested_at=as_of,
            security_id=stock_id,
            quality=DataQualityStatus.FRESH,
        ),
    )
    return fundamentals, items


def _clamp_score(value: Decimal) -> Decimal:
    return max(Decimal("0"), min(Decimal("100"), value.quantize(Decimal("0.01"))))


def _return(closes: list[Decimal], sessions: int) -> Decimal:
    if len(closes) <= sessions or closes[-sessions - 1] == 0:
        raise LiveKREvidenceError("KIS price history is insufficient for evidence")
    return closes[-1] / closes[-sessions - 1] - Decimal("1")


def _business_days_between(start: datetime, end: datetime) -> int:
    cursor = start.date()
    finish = end.date()
    count = 0
    while cursor < finish:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:
            count += 1
    return count


def agentnews_hard_vetoes(board: AgentNewsSnapshot) -> tuple[str, ...]:
    """Keep stale public context analyzable while blocking fresh-entry authority."""
    return () if board.quality.value == "FRESH" else ("STALE_AGENTNEWS_CONTEXT",)


class IncomeStatementFetcher(Protocol):
    async def fetch(self, *, symbol: str, as_of: datetime) -> FMPIncomeEvidence: ...


class LiveKREvidenceProvider:
    """Derive the six strategy observations without a user-authored evidence file."""

    def __init__(
        self,
        *,
        agentnews_fetcher: Callable[[], AgentNewsSnapshot | Awaitable[AgentNewsSnapshot]],
        fundamentals_client: IncomeStatementFetcher,
        fmp_symbol: str,
        price_provider: str = "KIS",
        market_label: str = "kr",
    ) -> None:
        if not fmp_symbol or not price_provider or market_label not in {"kr", "us"}:
            raise ValueError("fmp_symbol, price_provider, and supported market_label are required")
        self._agentnews_fetcher = agentnews_fetcher
        self._fundamentals = fundamentals_client
        self._fmp_symbol = fmp_symbol
        self._price_provider = price_provider
        self._market_label = market_label

    async def build(
        self,
        *,
        snapshot: MarketSnapshot,
        stock_id: SecurityId,
        benchmark_id: SecurityId,
        as_of: datetime,
    ) -> KRPITEvidence:
        board_result = self._agentnews_fetcher()
        board = await board_result if inspect.isawaitable(board_result) else board_result
        if not isinstance(board, AgentNewsSnapshot):
            raise LiveKREvidenceError("AgentNews fetcher returned an invalid snapshot")
        income = await self._fundamentals.fetch(symbol=self._fmp_symbol, as_of=as_of)

        by_security: dict[SecurityId, list[object]] = {stock_id: [], benchmark_id: []}
        for bar in snapshot.price_bars:
            if bar.security_id in by_security and bar.provider == self._price_provider:
                by_security[bar.security_id].append(bar)
        for bars in by_security.values():
            bars.sort(key=lambda item: item.bar_end)  # type: ignore[attr-defined]
        stock_bars = by_security[stock_id]
        benchmark_bars = by_security[benchmark_id]
        stock_closes = [bar.raw_close for bar in stock_bars]  # type: ignore[attr-defined]
        benchmark_closes = [bar.raw_close for bar in benchmark_bars]  # type: ignore[attr-defined]
        stock_20 = _return(stock_closes, 20)
        benchmark_20 = _return(benchmark_closes, 20)
        benchmark_5 = _return(benchmark_closes, 5)
        latest_session = stock_bars[-1].bar_end.date()  # type: ignore[attr-defined]
        if latest_session != benchmark_bars[-1].bar_end.date():  # type: ignore[attr-defined]
            raise LiveKREvidenceError("KIS stock and benchmark sessions are not aligned")

        board_time = board.source_updated_at or board.fetched_at
        relative_score = _clamp_score(
            Decimal("50") + (stock_20 - benchmark_20) * Decimal("500")
        )
        swing_regime = _clamp_score(Decimal("50") + benchmark_5 * Decimal("500"))
        trend_regime = _clamp_score(Decimal("50") + benchmark_20 * Decimal("500"))
        catalyst_recency = (
            Decimal(_business_days_between(board.source_updated_at, as_of))
            if board.source_updated_at is not None
            else Decimal("999")
        )
        latest_price_timing = max(
            (bar.timing for bar in (*stock_bars, *benchmark_bars)),  # type: ignore[attr-defined]
            key=lambda timing: timing.ingested_at,
        )
        observed_at = max(
            latest_price_timing.observed_at,
            board_time,
            income.current_accepted_at,
            income.previous_accepted_at,
        )
        available_at = max(
            latest_price_timing.available_at,
            board_time,
            income.current_accepted_at,
            income.previous_accepted_at,
        )
        ingested_at = max(
            latest_price_timing.ingested_at,
            board.fetched_at,
            income.ingested_at,
            available_at,
        )
        stock_symbol = next(
            bar.provider_symbol for bar in reversed(stock_bars)  # type: ignore[attr-defined]
        )
        benchmark_symbol = next(
            bar.provider_symbol for bar in reversed(benchmark_bars)  # type: ignore[attr-defined]
        )
        evidence_payload = {
            f"agentnews:{self._market_label}:{board.content_hash}": {
                "source": board.url,
                "source_updated_at": None if board.source_updated_at is None else board.source_updated_at.isoformat(),
                "fetched_at": board.fetched_at.isoformat(),
                "catalyst_recency_basis": (
                    "SOURCE_UPDATED_AT"
                    if board.source_updated_at is not None
                    else "UNKNOWN_NO_SOURCE_TIMESTAMP"
                ),
                "quality": board.quality.value,
                "trust": board.trust.value,
                "content_hash": board.content_hash,
                "content": board.raw_body.decode("utf-8"),
            },
            f"fmp:income:{self._fmp_symbol}:{income.current_period}": {
                "source": "FMP stable/income-statement",
                "symbol": self._fmp_symbol,
                "current_period": income.current_period,
                "current_net_income": str(income.current),
                "current_unit": income.current_unit,
                "current_accepted_at": income.current_accepted_at.isoformat(),
                "previous_period": income.previous_period,
                "previous_net_income": str(income.previous),
                "previous_unit": income.previous_unit,
                "previous_accepted_at": income.previous_accepted_at.isoformat(),
                "response_hash": income.response_hash,
                "provider_timestamp_assumption": "UTC_WHEN_OFFSET_ABSENT",
            },
            f"{self._price_evidence_prefix}:{stock_symbol}:{benchmark_symbol}:{latest_session.isoformat()}": {
                "source": f"{self._price_provider} daily market data",
                "stock_symbol": stock_symbol,
                "benchmark_symbol": benchmark_symbol,
                "latest_completed_session": latest_session.isoformat(),
                "stock_return_20": str(stock_20),
                "benchmark_return_20": str(benchmark_20),
            },
        }
        hard_vetoes = list(agentnews_hard_vetoes(board))
        try:
            fundamental_age_days = (
                as_of.date() - date.fromisoformat(income.current_period)
            ).days
        except ValueError:
            fundamental_age_days = 10_000
        if fundamental_age_days > 550:
            hard_vetoes.append("STALE_FMP_FUNDAMENTALS")
        fundamental_quality = (
            DataQualityStatus.STALE
            if fundamental_age_days > 550
            else DataQualityStatus.FRESH
        )
        fundamentals, evidence_items = _normalized_scenario_records(
            income=income,
            board=board,
            stock_id=stock_id,
            benchmark_id=benchmark_id,
            as_of=as_of,
            fundamental_quality=fundamental_quality,
        )
        for item in evidence_items:
            evidence_payload[str(item.evidence_id)] = {
                "source": str(item.source_url),
                "kind": item.kind,
                "title": item.title,
                "provider": item.provider,
                "source_record_id": item.source_record_id,
                "available_at": item.timing.available_at.isoformat(),
                "content_hash": item.content_hash,
            }
        return KRPITEvidence(
            observed_at=observed_at,
            available_at=available_at,
            ingested_at=ingested_at,
            observations={
                "catalyst_recency_sessions": catalyst_recency,
                "regime_swing_compatibility": swing_regime,
                "earnings_current": income.current,
                "earnings_previous": income.previous,
                "industry_leadership": relative_score,
                "regime_trend_compatibility": trend_regime,
            },
            evidence_payload=evidence_payload,
            field_quality={
                "evidence": board.quality,
                "fundamental": fundamental_quality,
                "regime": snapshot.quality,
            },
            hard_vetoes=tuple(sorted(set(hard_vetoes))),
            fundamentals=fundamentals,
            evidence_items=evidence_items,
        )

    @property
    def _price_evidence_prefix(self) -> str:
        return "kis:relative-strength" if self._market_label == "kr" else "fmp:price"


class LiveUSEvidenceProvider(LiveKREvidenceProvider):
    """US evidence adapter using FMP prices/fundamentals and AgentNews US."""

    def __init__(
        self,
        *,
        agentnews_fetcher: Callable[[], AgentNewsSnapshot | Awaitable[AgentNewsSnapshot]],
        fundamentals_client: IncomeStatementFetcher,
        fmp_symbol: str,
    ) -> None:
        super().__init__(
            agentnews_fetcher=agentnews_fetcher,
            fundamentals_client=fundamentals_client,
            fmp_symbol=fmp_symbol,
            price_provider="fmp",
            market_label="us",
        )
