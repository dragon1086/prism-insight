"""Dormant injected FMP-primary US market-data adapter."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from typing import Awaitable, Callable, Mapping, Protocol, cast
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime

from prism_core.data.contracts import (
    ContractModel,
    DataQualityStatus,
    MarketSnapshot,
    ObservationTime,
    PriceBar,
    SecurityId,
    SymbolMapping,
)
from prism_core.data.providers.fmp_models import (
    FMPApiKey,
    FMPPagination,
    FMPRequest,
    FMPResponseEnvelope,
)


class FMPTransport(Protocol):
    """Narrow read-only transport receiving the credential separately from identity."""

    async def execute(
        self,
        request: FMPRequest,
        *,
        api_key: FMPApiKey,
    ) -> FMPResponseEnvelope: ...


@dataclass(frozen=True)
class FMPInstrument:
    """Stable internal identity and its point-in-time FMP symbol interval."""

    security_id: SecurityId
    fmp_symbol: str
    valid_from: datetime
    valid_to: datetime | None = None

    def __post_init__(self) -> None:
        if not self.fmp_symbol:
            raise ValueError("fmp_symbol must not be empty")
        for field_name, value in (
            ("valid_from", self.valid_from),
            ("valid_to", self.valid_to),
        ):
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError(f"{field_name} must be timezone-aware")
        if self.valid_to is not None and self.valid_to <= self.valid_from:
            raise ValueError("valid_to must be after valid_from")

    def is_active(self, as_of_date: datetime) -> bool:
        return self.valid_from <= as_of_date and (
            self.valid_to is None or self.valid_to > as_of_date
        )


class CapabilityStatus(str, Enum):
    SUPPORTED = "SUPPORTED"
    FORBIDDEN = "FORBIDDEN"
    MALFORMED = "MALFORMED"
    TIMEOUT = "TIMEOUT"
    RATE_LIMIT = "RATE_LIMIT"


class FMPEventKind(str, Enum):
    TIMEOUT = "TIMEOUT"
    RATE_LIMIT = "RATE_LIMIT"
    FORBIDDEN = "FORBIDDEN"
    MALFORMED = "MALFORMED"
    UNAVAILABLE = "UNAVAILABLE"
    MISSING = "MISSING"
    STALE = "STALE"
    PARTIAL = "PARTIAL"
    CONFLICT = "CONFLICT"
    UNMATCHED = "UNMATCHED"
    INELIGIBLE = "INELIGIBLE"
    RETRY_EXHAUSTED = "RETRY_EXHAUSTED"
    MISSING_PAGE = "MISSING_PAGE"
    REPEATED_PAGE = "REPEATED_PAGE"
    PAGINATION_MALFORMED = "PAGINATION_MALFORMED"
    PAGE_LIMIT_EXCEEDED = "PAGE_LIMIT_EXCEEDED"
    REQUEST_BUDGET_EXHAUSTED = "REQUEST_BUDGET_EXHAUSTED"


class FMPProviderEvent(ContractModel):
    provider: str
    kind: FMPEventKind
    quality: DataQualityStatus
    attempt: int
    operation: str
    detail: str
    occurred_at: AwareDatetime


@dataclass(frozen=True)
class CapabilityProbeResult:
    status: CapabilityStatus
    response: FMPResponseEnvelope | None
    events: tuple[FMPProviderEvent, ...]


@dataclass(frozen=True)
class FMPFetchResult:
    snapshot: MarketSnapshot
    raw_payloads: tuple[FMPResponseEnvelope, ...]
    request_hashes: tuple[str, ...]
    events: tuple[FMPProviderEvent, ...]


@dataclass
class _RequestBudget:
    limit: int
    used: int = 0

    @property
    def remaining(self) -> int:
        return self.limit - self.used

    def consume(self) -> int:
        if self.remaining < 1:
            raise RuntimeError("request budget is exhausted")
        self.used += 1
        return self.used


class FMPTimeoutError(TimeoutError):
    """Retryable timeout at the injected read-only transport boundary."""


class FMPRateLimitError(RuntimeError):
    """Retryable provider throttle at the injected read-only transport boundary."""


class FMPMarketDataProvider:
    """FMP-primary normalization foundation with no concrete network transport."""

    provider_name = "fmp"
    _market_timezone = ZoneInfo("America/New_York")

    def __init__(
        self,
        *,
        transport: FMPTransport,
        api_key: FMPApiKey,
        instruments: tuple[FMPInstrument, ...],
        clock: Callable[[], datetime],
        max_attempts: int = 3,
        max_pages: int = 10,
        max_requests: int | None = None,
        sleeper: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if max_pages < 1:
            raise ValueError("max_pages must be positive")
        if max_requests is not None and max_requests < 1:
            raise ValueError("max_requests must be positive")
        by_id = {instrument.security_id.value: instrument for instrument in instruments}
        if len(by_id) != len(instruments):
            raise ValueError("security_id instruments must be unique")
        symbols = [instrument.fmp_symbol for instrument in instruments]
        if len(set(symbols)) != len(symbols):
            raise ValueError("FMP symbols must be unique")
        self._transport = transport
        self._api_key = api_key
        self._instruments = by_id
        self._clock = clock
        self._max_attempts = max_attempts
        self._max_pages = max_pages
        self._max_requests = max_requests or max_pages * max_attempts
        self._sleeper = sleeper

    @staticmethod
    def _require_aware(value: datetime, field_name: str) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{field_name} must be timezone-aware")
        return value

    def _response_exposes_secret(self, response: FMPResponseEnvelope) -> bool:
        secret = self._api_key.get_secret_value()

        def contains_secret(value: object) -> bool:
            if isinstance(value, str):
                return secret in value
            if isinstance(value, Mapping):
                return any(
                    contains_secret(key) or contains_secret(item)
                    for key, item in value.items()
                )
            if isinstance(value, (list, tuple)):
                return any(contains_secret(item) for item in value)
            return False

        return contains_secret(response.payload) or secret in response.source_record_id

    async def _execute_with_retry(
        self,
        request: FMPRequest,
        *,
        occurred_at: datetime,
        budget: _RequestBudget | None = None,
    ) -> tuple[
        FMPResponseEnvelope | None,
        tuple[FMPProviderEvent, ...],
        CapabilityStatus | None,
    ]:
        events: list[FMPProviderEvent] = []
        terminal_status: CapabilityStatus | None = None
        request_budget = budget or _RequestBudget(self._max_attempts)
        for local_attempt in range(1, self._max_attempts + 1):
            if request_budget.remaining < 1:
                events.append(
                    FMPProviderEvent(
                        provider="fmp",
                        kind=FMPEventKind.REQUEST_BUDGET_EXHAUSTED,
                        quality=DataQualityStatus.UNAVAILABLE,
                        attempt=request_budget.used,
                        operation=request.operation,
                        detail=f"aggregate request budget exhausted after {request_budget.used} calls",
                        occurred_at=occurred_at,
                    )
                )
                return None, tuple(events), terminal_status
            attempt = request_budget.consume()
            try:
                response = await self._transport.execute(request, api_key=self._api_key)
                if response.status_code != 429:
                    return response, tuple(events), None
                kind = FMPEventKind.RATE_LIMIT
                terminal_status = CapabilityStatus.RATE_LIMIT
                detail = "FMP response status 429"
            except (FMPTimeoutError, FMPRateLimitError) as exc:
                if isinstance(exc, FMPTimeoutError):
                    kind = FMPEventKind.TIMEOUT
                    terminal_status = CapabilityStatus.TIMEOUT
                else:
                    kind = FMPEventKind.RATE_LIMIT
                    terminal_status = CapabilityStatus.RATE_LIMIT
                detail = exc.__class__.__name__
            except Exception as exc:
                events.append(
                    FMPProviderEvent(
                        provider="fmp",
                        kind=FMPEventKind.MALFORMED,
                        quality=DataQualityStatus.UNAVAILABLE,
                        attempt=attempt,
                        operation=request.operation,
                        detail=f"transport failed with {exc.__class__.__name__}",
                        occurred_at=occurred_at,
                    )
                )
                return None, tuple(events), CapabilityStatus.MALFORMED
            events.append(
                FMPProviderEvent(
                    provider="fmp",
                    kind=kind,
                    quality=DataQualityStatus.UNAVAILABLE,
                    attempt=attempt,
                    operation=request.operation,
                    detail=detail,
                    occurred_at=occurred_at,
                )
            )
            if local_attempt == self._max_attempts or request_budget.remaining < 1:
                events.append(
                    FMPProviderEvent(
                        provider="fmp",
                        kind=FMPEventKind.RETRY_EXHAUSTED,
                        quality=DataQualityStatus.UNAVAILABLE,
                        attempt=attempt,
                        operation=request.operation,
                        detail=f"retry budget exhausted after {attempt} aggregate calls",
                        occurred_at=occurred_at,
                    )
                )
                return None, tuple(events), terminal_status
            if self._sleeper is not None:
                await self._sleeper(float(2 ** (local_attempt - 1)))
        raise AssertionError("unreachable retry state")

    async def probe_capability(self, *, as_of_date: datetime) -> CapabilityProbeResult:
        self._require_aware(as_of_date, "as_of_date")
        occurred_at = self._require_aware(self._clock(), "clock result")
        if occurred_at < as_of_date:
            raise ValueError("clock result must be at or after as_of_date")
        request = FMPRequest(
            operation="probe_capability",
            path="/stable/historical-price-eod/full",
            params={"limit": 1, "symbol": "AAPL"},
        )
        response, retry_events, terminal_status = await self._execute_with_retry(
            request,
            occurred_at=occurred_at,
        )
        if response is None:
            if terminal_status is None:
                raise AssertionError("retry exhaustion requires a terminal status")
            return CapabilityProbeResult(
                status=terminal_status,
                response=None,
                events=retry_events,
            )
        if self._response_exposes_secret(response):
            return CapabilityProbeResult(
                status=CapabilityStatus.MALFORMED,
                response=None,
                events=(
                    *retry_events,
                    FMPProviderEvent(
                        provider="fmp",
                        kind=FMPEventKind.MALFORMED,
                        quality=DataQualityStatus.UNAVAILABLE,
                        attempt=1,
                        operation="probe_capability",
                        detail="response rejected at the credential boundary",
                        occurred_at=occurred_at,
                    ),
                ),
            )
        payload = response.payload
        if (
            200 <= response.status_code < 300
            and isinstance(payload, Mapping)
            and payload.get("entitled") is True
        ):
            return CapabilityProbeResult(
                status=CapabilityStatus.SUPPORTED,
                response=response,
                events=retry_events,
            )
        forbidden = response.status_code in {401, 403, 404} or (
            200 <= response.status_code < 300
            and isinstance(payload, Mapping)
            and payload.get("entitled") is False
        )
        status = CapabilityStatus.FORBIDDEN if forbidden else CapabilityStatus.MALFORMED
        kind = FMPEventKind.FORBIDDEN if forbidden else FMPEventKind.MALFORMED
        detail = (
            "capability endpoint denied or does not support access"
            if forbidden
            else "capability response did not contain a recognized entitlement marker"
        )
        return CapabilityProbeResult(
            status=status,
            response=response,
            events=(*retry_events,
                FMPProviderEvent(
                    provider="fmp",
                    kind=kind,
                    quality=DataQualityStatus.UNAVAILABLE,
                    attempt=1,
                    operation="probe_capability",
                    detail=detail,
                    occurred_at=occurred_at,
                ),
            ),
        )

    async def fetch_snapshot(
        self,
        *,
        security_ids: tuple[SecurityId, ...],
        as_of_date: datetime,
    ) -> MarketSnapshot:
        return (
            await self.fetch_result(
                security_ids=security_ids,
                as_of_date=as_of_date,
            )
        ).snapshot

    async def fetch_result(
        self,
        *,
        security_ids: tuple[SecurityId, ...],
        as_of_date: datetime,
    ) -> FMPFetchResult:
        self._require_aware(as_of_date, "as_of_date")
        ingested_at = self._require_aware(self._clock(), "clock result")
        if ingested_at < as_of_date:
            raise ValueError("clock result must be at or after as_of_date")
        unknown = [item for item in security_ids if item.value not in self._instruments]
        if unknown:
            raise ValueError("every security_id must have a configured FMP instrument")
        active = tuple(
            self._instruments[item.value]
            for item in security_ids
            if self._instruments[item.value].is_active(as_of_date)
        )
        inactive = tuple(
            self._instruments[item.value]
            for item in security_ids
            if not self._instruments[item.value].is_active(as_of_date)
        )
        initial_events = [
            FMPProviderEvent(
                provider="fmp",
                kind=FMPEventKind.INELIGIBLE,
                quality=DataQualityStatus.UNAVAILABLE,
                attempt=1,
                operation="select_price_instruments",
                detail=f"{instrument.fmp_symbol} is inactive at the requested as-of time",
                occurred_at=ingested_at,
            )
            for instrument in inactive
        ]
        events = list(initial_events)
        collected: list[FMPResponseEnvelope] = []
        request_hashes: list[str] = []
        budget = _RequestBudget(self._max_requests)
        expected_page = 1
        declared_total: int | None = None
        seen_source_ids: dict[str, str] = {}
        seen_hashes: set[str] = set()
        collection_complete = bool(active)
        aggregate_rows: list[object] = []

        def page_event(
            kind: FMPEventKind,
            quality: DataQualityStatus,
            operation: str,
            detail: str,
        ) -> FMPProviderEvent:
            return FMPProviderEvent(
                provider="fmp",
                kind=kind,
                quality=quality,
                attempt=max(1, budget.used),
                operation=operation,
                detail=detail,
                occurred_at=ingested_at,
            )

        while active and collection_complete:
            request = FMPRequest(
                operation="fetch_price_page",
                path="/stable/historical-price-eod/full",
                params={
                    "page": expected_page,
                    "symbols": [item.fmp_symbol for item in active],
                },
            )
            page_response, retry_events, _ = await self._execute_with_retry(
                request,
                occurred_at=ingested_at,
                budget=budget,
            )
            events.extend(retry_events)
            if page_response is None:
                collection_complete = False
                break
            if self._response_exposes_secret(page_response):
                events.append(
                    page_event(
                        FMPEventKind.MALFORMED,
                        DataQualityStatus.UNAVAILABLE,
                        "fetch_price_page",
                        "response rejected at the credential boundary",
                    )
                )
                collection_complete = False
                break
            collected.append(page_response)
            request_hashes.append(request.request_hash)
            if not 200 <= page_response.status_code < 300:
                events.append(
                    page_event(
                        FMPEventKind.UNAVAILABLE,
                        DataQualityStatus.UNAVAILABLE,
                        "fetch_price_page",
                        f"page returned non-success status {page_response.status_code}",
                    )
                )
                collection_complete = False
                break
            payload = page_response.payload
            if not isinstance(payload, Mapping):
                events.append(
                    page_event(
                        FMPEventKind.PAGINATION_MALFORMED,
                        DataQualityStatus.UNAVAILABLE,
                        "validate_price_pagination",
                        "price payload must be an object",
                    )
                )
                collection_complete = False
                break
            try:
                pagination = FMPPagination.from_payload(payload.get("pagination"))
            except ValueError:
                events.append(
                    page_event(
                        FMPEventKind.PAGINATION_MALFORMED,
                        DataQualityStatus.UNAVAILABLE,
                        "validate_price_pagination",
                        f"page {expected_page} has malformed pagination metadata",
                    )
                )
                collection_complete = False
                break
            if pagination.total_pages > self._max_pages:
                events.append(
                    page_event(
                        FMPEventKind.PAGE_LIMIT_EXCEEDED,
                        DataQualityStatus.UNAVAILABLE,
                        "validate_price_pagination",
                        f"declared page count exceeds maximum {self._max_pages}",
                    )
                )
                collection_complete = False
                break
            if pagination.page != expected_page:
                mismatch_kind = (
                    FMPEventKind.MISSING_PAGE
                    if pagination.page > expected_page
                    else FMPEventKind.REPEATED_PAGE
                )
                events.append(
                    page_event(
                        mismatch_kind,
                        DataQualityStatus.UNAVAILABLE,
                        "validate_price_pagination",
                        f"expected page {expected_page} but received page {pagination.page}",
                    )
                )
                collection_complete = False
                break
            if not pagination.has_more and pagination.page < pagination.total_pages:
                events.append(
                    page_event(
                        FMPEventKind.MISSING_PAGE,
                        DataQualityStatus.UNAVAILABLE,
                        "validate_price_pagination",
                        "pagination terminated before the declared final page",
                    )
                )
                collection_complete = False
                break
            expected_next = pagination.page + 1 if pagination.has_more else None
            if (
                (pagination.has_more and pagination.page >= pagination.total_pages)
                or pagination.next_page != expected_next
            ):
                events.append(
                    page_event(
                        FMPEventKind.PAGINATION_MALFORMED,
                        DataQualityStatus.UNAVAILABLE,
                        "validate_price_pagination",
                        "pagination continuation metadata is inconsistent",
                    )
                )
                collection_complete = False
                break
            if declared_total is None:
                declared_total = pagination.total_pages
            elif pagination.total_pages != declared_total:
                events.append(
                    page_event(
                        FMPEventKind.CONFLICT,
                        DataQualityStatus.CONFLICT,
                        "validate_price_pagination",
                        "pagination totalPages changed during collection",
                    )
                )
                collection_complete = False
                break
            prior_hash = seen_source_ids.get(page_response.source_record_id)
            if prior_hash is not None or page_response.source_hash in seen_hashes:
                conflicting = prior_hash is not None and prior_hash != page_response.source_hash
                events.append(
                    page_event(
                        FMPEventKind.CONFLICT if conflicting else FMPEventKind.REPEATED_PAGE,
                        (
                            DataQualityStatus.CONFLICT
                            if conflicting
                            else DataQualityStatus.UNAVAILABLE
                        ),
                        "validate_price_page_identity",
                        "provider page identity was repeated or conflicted",
                    )
                )
                collection_complete = False
                break
            if (
                page_response.available_at > as_of_date
                or page_response.available_at > ingested_at
            ):
                events.append(
                    page_event(
                        FMPEventKind.MALFORMED,
                        DataQualityStatus.UNAVAILABLE,
                        "validate_price_page",
                        "payload violates the requested point-in-time boundary",
                    )
                )
                collection_complete = False
                break
            rows = payload.get("data")
            if not isinstance(rows, list):
                events.append(
                    page_event(
                        FMPEventKind.MALFORMED,
                        DataQualityStatus.UNAVAILABLE,
                        "fetch_price_page",
                        "response data must be a list",
                    )
                )
                collection_complete = False
                break
            page_index = len(collected) - 1
            for raw_row in rows:
                if isinstance(raw_row, Mapping):
                    aggregate_rows.append({**raw_row, "_fmpPageIndex": page_index})
                else:
                    aggregate_rows.append(raw_row)
            seen_source_ids[page_response.source_record_id] = page_response.source_hash
            seen_hashes.add(page_response.source_hash)
            if not pagination.has_more:
                break
            expected_page += 1

        raw_payloads = tuple(collected)
        response: FMPResponseEnvelope | None = None
        if collection_complete and collected:
            quality_order = {
                DataQualityStatus.FRESH: 0,
                DataQualityStatus.PARTIAL: 1,
                DataQualityStatus.STALE: 2,
                DataQualityStatus.CONFLICT: 3,
                DataQualityStatus.UNAVAILABLE: 4,
            }
            aggregate_quality = max(
                (item.quality for item in collected), key=quality_order.__getitem__
            )
            response = FMPResponseEnvelope(
                status_code=200,
                source_record_id="fmp:prices:pages:"
                + hashlib.sha256(
                    "|".join(item.source_record_id for item in collected).encode()
                ).hexdigest(),
                revision=max(item.revision for item in collected),
                observed_at=max(item.observed_at for item in collected),
                available_at=max(item.available_at for item in collected),
                payload={"data": aggregate_rows},
                quality=aggregate_quality,
            )
        bars: list[PriceBar] = []
        mappings: list[SymbolMapping] = []
        quality = DataQualityStatus.UNAVAILABLE
        if response is not None and 200 <= response.status_code < 300:
            if response.quality is not DataQualityStatus.FRESH:
                quality_kinds = {
                    DataQualityStatus.STALE: FMPEventKind.STALE,
                    DataQualityStatus.PARTIAL: FMPEventKind.PARTIAL,
                    DataQualityStatus.UNAVAILABLE: FMPEventKind.UNAVAILABLE,
                    DataQualityStatus.CONFLICT: FMPEventKind.CONFLICT,
                }
                events.append(
                    FMPProviderEvent(
                        provider="fmp",
                        kind=quality_kinds[response.quality],
                        quality=response.quality,
                        attempt=1,
                        operation="normalize_price_page",
                        detail=f"provider labelled payload {response.quality.value}",
                        occurred_at=ingested_at,
                    )
                )
            pit_valid = (
                response.available_at <= as_of_date
                and response.available_at <= ingested_at
            )
            if not pit_valid:
                events.append(
                    FMPProviderEvent(
                        provider="fmp",
                        kind=FMPEventKind.MALFORMED,
                        quality=DataQualityStatus.UNAVAILABLE,
                        attempt=1,
                        operation="validate_price_page",
                        detail="payload violates the requested point-in-time boundary",
                        occurred_at=ingested_at,
                    )
                )
            can_normalize = (
                pit_valid and response.quality is not DataQualityStatus.UNAVAILABLE
            )
            payload = response.payload if can_normalize else None
            rows = payload.get("data") if isinstance(payload, Mapping) else None
            if isinstance(rows, list):
                by_symbol = {instrument.fmp_symbol: instrument for instrument in active}
                for raw_row in rows:
                    if not isinstance(raw_row, Mapping):
                        events.append(
                            FMPProviderEvent(
                                provider="fmp",
                                kind=FMPEventKind.MALFORMED,
                                quality=DataQualityStatus.PARTIAL,
                                attempt=1,
                                operation="normalize_price_bar",
                                detail="price row must be an object",
                                occurred_at=ingested_at,
                            )
                        )
                        continue
                    row = cast(Mapping[str, object], raw_row)
                    page_index = row.get("_fmpPageIndex")
                    if type(page_index) is not int or not 0 <= page_index < len(raw_payloads):
                        events.append(
                            FMPProviderEvent(
                                provider="fmp",
                                kind=FMPEventKind.MALFORMED,
                                quality=DataQualityStatus.UNAVAILABLE,
                                attempt=1,
                                operation="normalize_price_bar",
                                detail="price row lost its page provenance",
                                occurred_at=ingested_at,
                            )
                        )
                        continue
                    page_source = raw_payloads[page_index]
                    timing = ObservationTime(
                        observed_at=page_source.observed_at,
                        available_at=page_source.available_at,
                        ingested_at=ingested_at,
                        as_of_date=as_of_date,
                    )
                    symbol = str(row.get("symbol", ""))
                    instrument = by_symbol.get(symbol)
                    if instrument is None:
                        events.append(
                            FMPProviderEvent(
                                provider="fmp",
                                kind=FMPEventKind.UNMATCHED,
                                quality=DataQualityStatus.PARTIAL,
                                attempt=1,
                                operation="normalize_price_bar",
                                detail=f"unmatched provider symbol: {symbol or '<empty>'}",
                                occurred_at=ingested_at,
                            )
                        )
                        continue
                    required_core = {
                        "date",
                        "rawOpen",
                        "rawHigh",
                        "rawLow",
                        "rawClose",
                        "rawVolume",
                    }
                    missing = sorted(required_core - row.keys())
                    if missing:
                        events.append(
                            FMPProviderEvent(
                                provider="fmp",
                                kind=FMPEventKind.PARTIAL,
                                quality=DataQualityStatus.PARTIAL,
                                attempt=1,
                                operation="normalize_price_bar",
                                detail=f"{symbol} missing fields: {','.join(missing)}",
                                occurred_at=ingested_at,
                            )
                        )
                        continue
                    adjusted_names = (
                        "adjustedOpen",
                        "adjustedHigh",
                        "adjustedLow",
                        "adjustedClose",
                        "adjustedVolume",
                    )
                    adjusted_present = tuple(
                        row.get(name) is not None for name in adjusted_names
                    )
                    if any(adjusted_present) != all(adjusted_present) or (
                        any(adjusted_present) and row.get("adjustmentAsOf") is None
                    ):
                        events.append(
                            FMPProviderEvent(
                                provider="fmp",
                                kind=FMPEventKind.MALFORMED,
                                quality=DataQualityStatus.UNAVAILABLE,
                                attempt=1,
                                operation="normalize_price_bar",
                                detail=f"{symbol} adjusted OHLCV is incomplete",
                                occurred_at=ingested_at,
                            )
                        )
                        continue
                    has_adjusted = all(adjusted_present)
                    try:
                        trading_date = date.fromisoformat(str(row["date"]))
                        if trading_date > as_of_date.astimezone(self._market_timezone).date():
                            raise ValueError("trading date exceeds requested as-of date")
                        bar = PriceBar(
                            security_id=instrument.security_id,
                            provider="fmp",
                            provider_symbol=symbol,
                            source_record_id=(
                                f"{page_source.source_record_id}:price:{symbol}:"
                                f"{trading_date.isoformat()}"
                            ),
                            source_hash=page_source.source_hash,
                            revision=page_source.revision,
                            timing=timing,
                            quality=page_source.quality,
                            bar_start=datetime.combine(
                                trading_date,
                                time.min,
                                tzinfo=self._market_timezone,
                            ),
                            bar_end=datetime.combine(
                                trading_date,
                                time(16, 0),
                                tzinfo=self._market_timezone,
                            ),
                            interval="1d",
                            currency="USD",
                            raw_open=Decimal(str(row["rawOpen"])),
                            raw_high=Decimal(str(row["rawHigh"])),
                            raw_low=Decimal(str(row["rawLow"])),
                            raw_close=Decimal(str(row["rawClose"])),
                            raw_volume=Decimal(str(row["rawVolume"])),
                            adjusted_open=(
                                Decimal(str(row["adjustedOpen"]))
                                if has_adjusted
                                else None
                            ),
                            adjusted_high=(
                                Decimal(str(row["adjustedHigh"]))
                                if has_adjusted
                                else None
                            ),
                            adjusted_low=(
                                Decimal(str(row["adjustedLow"]))
                                if has_adjusted
                                else None
                            ),
                            adjusted_close=(
                                Decimal(str(row["adjustedClose"]))
                                if has_adjusted
                                else None
                            ),
                            adjusted_volume=(
                                Decimal(str(row["adjustedVolume"]))
                                if has_adjusted
                                else None
                            ),
                            adjustment_as_of=(
                                datetime.fromisoformat(str(row["adjustmentAsOf"]))
                                if has_adjusted
                                else None
                            ),
                        )
                    except (ArithmeticError, TypeError, ValueError):
                        events.append(
                            FMPProviderEvent(
                                provider="fmp",
                                kind=FMPEventKind.MALFORMED,
                                quality=DataQualityStatus.UNAVAILABLE,
                                attempt=1,
                                operation="normalize_price_bar",
                                detail=f"{symbol} contains an invalid core field value",
                                occurred_at=ingested_at,
                            )
                        )
                        continue
                    bars.append(bar)
                    mappings.append(
                        SymbolMapping(
                            security_id=instrument.security_id,
                            provider="fmp",
                            provider_symbol=symbol,
                            market="US",
                            valid_from=instrument.valid_from,
                            valid_to=instrument.valid_to,
                            timing=timing,
                            source_hash=page_source.source_hash,
                        )
                    )
                grouped: dict[tuple[SecurityId, datetime], list[PriceBar]] = {}
                for bar in bars:
                    grouped.setdefault((bar.security_id, bar.bar_start), []).append(bar)
                reconciled: list[PriceBar] = []
                for group in grouped.values():
                    signatures = {
                        (
                            bar.raw_open,
                            bar.raw_high,
                            bar.raw_low,
                            bar.raw_close,
                            bar.raw_volume,
                            bar.adjusted_open,
                            bar.adjusted_high,
                            bar.adjusted_low,
                            bar.adjusted_close,
                            bar.adjusted_volume,
                            bar.adjustment_as_of,
                        )
                        for bar in group
                    }
                    if len(signatures) == 1:
                        reconciled.append(group[0])
                    else:
                        events.append(
                            FMPProviderEvent(
                                provider="fmp",
                                kind=FMPEventKind.CONFLICT,
                                quality=DataQualityStatus.CONFLICT,
                                attempt=1,
                                operation="reconcile_price_bars",
                                detail=(
                                    f"conflicting rows for {group[0].provider_symbol} "
                                    f"on {group[0].bar_start.date().isoformat()}"
                                ),
                                occurred_at=ingested_at,
                            )
                        )
                bars = sorted(
                    reconciled,
                    key=lambda item: (item.bar_start, str(item.security_id.value)),
                )
                present_ids = {bar.security_id.value for bar in bars}
                mappings_by_id = {
                    mapping.security_id.value: mapping for mapping in mappings
                }
                mappings = [
                    mappings_by_id[security_id]
                    for security_id in sorted(present_ids, key=str)
                ]
                missing_ids = {
                    instrument.security_id.value for instrument in active
                } - present_ids
                if missing_ids:
                    events.append(
                        FMPProviderEvent(
                            provider="fmp",
                            kind=FMPEventKind.MISSING,
                            quality=(
                                DataQualityStatus.PARTIAL
                                if present_ids
                                else DataQualityStatus.UNAVAILABLE
                            ),
                            attempt=1,
                            operation="normalize_price_bars",
                            detail=(
                                "missing requested security_ids: "
                                + ",".join(
                                    sorted(str(item) for item in missing_ids)
                                )
                            ),
                            occurred_at=ingested_at,
                        )
                    )
                if any(event.kind is FMPEventKind.CONFLICT for event in events):
                    quality = DataQualityStatus.CONFLICT
                elif not bars:
                    quality = DataQualityStatus.UNAVAILABLE
                elif response.quality in {
                    DataQualityStatus.STALE,
                    DataQualityStatus.CONFLICT,
                }:
                    quality = response.quality
                elif response.quality in {
                    DataQualityStatus.PARTIAL,
                    DataQualityStatus.UNAVAILABLE,
                } or any(
                    event.kind
                    in {
                        FMPEventKind.INELIGIBLE,
                        FMPEventKind.MALFORMED,
                        FMPEventKind.MISSING,
                        FMPEventKind.PARTIAL,
                        FMPEventKind.UNMATCHED,
                    }
                    for event in events
                ):
                    quality = DataQualityStatus.PARTIAL
                else:
                    quality = DataQualityStatus.FRESH
            elif can_normalize:
                events.append(
                    FMPProviderEvent(
                        provider="fmp",
                        kind=FMPEventKind.MALFORMED,
                        quality=DataQualityStatus.UNAVAILABLE,
                        attempt=1,
                        operation="fetch_price_page",
                        detail="response data must be a list",
                        occurred_at=ingested_at,
                    )
                )
        if response is None and any(
            item.kind is FMPEventKind.CONFLICT for item in events
        ):
            quality = DataQualityStatus.CONFLICT
        content = {
            "market": "US",
            "as_of_date": as_of_date.isoformat(),
            "requested_security_ids": [str(item.value) for item in security_ids],
            "request_hashes": request_hashes if response is not None else [],
            "raw_payload_hashes": (
                [item.source_hash for item in raw_payloads]
                if response is not None
                else []
            ),
            "quality": quality.value,
            "symbol_mappings": [item.model_dump(mode="json") for item in mappings],
            "price_bars": [item.model_dump(mode="json") for item in bars],
        }
        content_hash = hashlib.sha256(
            json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        snapshot = MarketSnapshot(
            snapshot_id=uuid5(NAMESPACE_URL, f"prism:us:fmp-market-snapshot:{content_hash}"),
            market="US",
            as_of_date=as_of_date,
            created_at=ingested_at,
            content_hash=content_hash,
            quality=quality,
            symbol_mappings=tuple(mappings),
            price_bars=tuple(bars),
            fundamentals=(),
            corporate_actions=(),
            evidence=(),
        )
        return FMPFetchResult(
            snapshot=snapshot,
            raw_payloads=raw_payloads,
            request_hashes=tuple(request_hashes),
            events=tuple(events),
        )
