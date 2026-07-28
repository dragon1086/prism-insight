"""Dormant injected FMP-primary US market-data adapter."""

from __future__ import annotations

import hashlib
import json
import math
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
    CorporateAction,
    CorporateActionType,
    DataQualityStatus,
    MarketSnapshot,
    ObservationTime,
    PriceBar,
    SecurityId,
    SymbolMapping,
)
from prism_core.data.corporate_actions import CorporateActionEvidence
from prism_core.data.providers.fmp_models import (
    FMPApiKey,
    FMPCorporateActionRequest,
    FMPCorporateActionResponseEnvelope,
    FMPFallbackRequest,
    FMPFallbackResponseEnvelope,
    FMPPagination,
    FMPRequest,
    FMPResponseEnvelope,
)


FMP_RAW_EOD_PATH = "/stable/historical-price-eod/non-split-adjusted"


class FMPTransport(Protocol):
    """Narrow read-only transport receiving the credential separately from identity."""

    async def execute(
        self,
        request: FMPRequest,
        *,
        api_key: FMPApiKey,
    ) -> FMPResponseEnvelope: ...


class FMPFallbackTransport(Protocol):
    """Credential-free injected fallback available only by explicit policy."""

    async def execute(
        self, request: FMPFallbackRequest
    ) -> FMPFallbackResponseEnvelope: ...


class FMPCorporateActionTransport(Protocol):
    """Injected FMP action evidence boundary; concrete HTTP is intentionally absent."""

    async def execute(
        self,
        request: FMPCorporateActionRequest,
        *,
        api_key: FMPApiKey,
    ) -> FMPCorporateActionResponseEnvelope: ...


class SECOfficialEvidenceTransport(Protocol):
    """Injected credential-free SEC official-evidence boundary."""

    async def execute(
        self, request: FMPCorporateActionRequest
    ) -> FMPCorporateActionResponseEnvelope: ...


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
    FALLBACK_DISABLED = "FALLBACK_DISABLED"
    FALLBACK_MISSING_TRANSPORT = "FALLBACK_MISSING_TRANSPORT"
    FALLBACK_INELIGIBLE = "FALLBACK_INELIGIBLE"
    FALLBACK_SELECTED = "FALLBACK_SELECTED"
    FALLBACK_RETRY = "FALLBACK_RETRY"
    FALLBACK_REJECTED = "FALLBACK_REJECTED"
    FALLBACK_STALE = "FALLBACK_STALE"
    FALLBACK_PARTIAL = "FALLBACK_PARTIAL"
    FALLBACK_CONFLICT = "FALLBACK_CONFLICT"
    FALLBACK_UNAVAILABLE = "FALLBACK_UNAVAILABLE"
    FALLBACK_MALFORMED = "FALLBACK_MALFORMED"
    FALLBACK_TIMEOUT = "FALLBACK_TIMEOUT"
    FALLBACK_RATE_LIMIT = "FALLBACK_RATE_LIMIT"
    FALLBACK_BUDGET_EXHAUSTED = "FALLBACK_BUDGET_EXHAUSTED"
    UNSUPPORTED_ACTION = "UNSUPPORTED_ACTION"
    MISSING_FIELD = "MISSING_FIELD"
    DUPLICATE_ACTION = "DUPLICATE_ACTION"
    WITHHELD_FUTURE = "WITHHELD_FUTURE"
    SOURCE_REVISION_DIVERGENCE = "SOURCE_REVISION_DIVERGENCE"
    PROVIDER_CONFLICT = "PROVIDER_CONFLICT"


class FMPFallbackMode(str, Enum):
    """Explicit policy switch; fallback is never inferred from environment state."""

    DISABLED = "DISABLED"
    RESEARCH_FIXTURE = "RESEARCH_FIXTURE"


class FMPFallbackReason(str, Enum):
    """Machine-branchable primary condition that invoked explicit fallback."""

    STALE = "STALE"
    PARTIAL = "PARTIAL"
    MISSING = "MISSING"
    UNAVAILABLE = "UNAVAILABLE"
    TIMEOUT = "TIMEOUT"
    RATE_LIMIT = "RATE_LIMIT"


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
    primary_quality: DataQualityStatus
    fallback_mode: FMPFallbackMode
    selected_provider: str | None
    fallback_raw_payloads: tuple[FMPFallbackResponseEnvelope, ...]
    fallback_request_hashes: tuple[str, ...]
    fallback_reason: FMPFallbackReason | None


@dataclass(frozen=True)
class FMPCorporateActionFetchResult:
    corporate_action_evidence: tuple[CorporateActionEvidence, ...]
    raw_payloads: tuple[FMPCorporateActionResponseEnvelope, ...]
    request_hashes: tuple[str, ...]
    events: tuple[FMPProviderEvent, ...]
    quality: DataQualityStatus


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

    def __init__(
        self,
        message: str,
        *,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        if retry_after_seconds is not None and (
            not math.isfinite(retry_after_seconds) or retry_after_seconds < 0
        ):
            raise ValueError("retry_after_seconds must be finite and non-negative")
        self.retry_after_seconds = retry_after_seconds


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
        fallback_mode: FMPFallbackMode = FMPFallbackMode.DISABLED,
        fallback_transport: FMPFallbackTransport | None = None,
        fallback_provider: str | None = None,
        fallback_max_attempts: int = 1,
        fmp_corporate_action_transport: FMPCorporateActionTransport | None = None,
        sec_official_evidence_transport: SECOfficialEvidenceTransport | None = None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if max_pages < 1:
            raise ValueError("max_pages must be positive")
        if max_requests is not None and max_requests < 1:
            raise ValueError("max_requests must be positive")
        if fallback_max_attempts < 1:
            raise ValueError("fallback_max_attempts must be positive")
        if fallback_provider is not None and (
            not fallback_provider or fallback_provider.lower() == "fmp"
        ):
            raise ValueError("fallback_provider must be non-empty and must not be fmp")
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
        fallback_configured = (
            fallback_mode is FMPFallbackMode.RESEARCH_FIXTURE
            and fallback_transport is not None
            and fallback_provider is not None
        )
        fallback_allowance = fallback_max_attempts if fallback_configured else 0
        if max_requests is not None and max_requests <= fallback_allowance:
            raise ValueError("max_requests must leave at least one primary request")
        self._max_requests = max_requests or max_pages * max_attempts + fallback_allowance
        self._sleeper = sleeper
        self._fallback_mode = fallback_mode
        self._fallback_transport = fallback_transport
        self._fallback_provider = fallback_provider
        self._fallback_max_attempts = fallback_max_attempts
        self._fmp_corporate_action_transport = fmp_corporate_action_transport
        self._sec_official_evidence_transport = sec_official_evidence_transport

    @staticmethod
    def _require_aware(value: datetime, field_name: str) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{field_name} must be timezone-aware")
        return value

    def _response_exposes_secret(
        self,
        response: (
            FMPResponseEnvelope
            | FMPFallbackResponseEnvelope
            | FMPCorporateActionResponseEnvelope
        ),
    ) -> bool:
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

    async def fetch_corporate_actions(
        self,
        *,
        security_ids: tuple[SecurityId, ...],
        as_of_date: datetime,
    ) -> FMPCorporateActionFetchResult:
        """Normalize injected FMP and SEC evidence without applying adjustments."""

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
        by_symbol = {item.fmp_symbol: item for item in active}
        events: list[FMPProviderEvent] = []
        raw_payloads: list[FMPCorporateActionResponseEnvelope] = []
        request_hashes: list[str] = []
        evidence: list[CorporateActionEvidence] = []

        def make_event(
            provider: str,
            kind: FMPEventKind,
            quality: DataQualityStatus,
            detail: str,
        ) -> FMPProviderEvent:
            return FMPProviderEvent(
                provider=provider,
                kind=kind,
                quality=quality,
                attempt=1,
                operation="fetch_corporate_actions",
                detail=detail,
                occurred_at=ingested_at,
            )

        configured: tuple[
            tuple[str, FMPCorporateActionTransport | SECOfficialEvidenceTransport | None],
            ...,
        ] = (
            ("fmp", self._fmp_corporate_action_transport),
            ("sec", self._sec_official_evidence_transport),
        )
        for provider_name, transport in configured:
            if transport is None:
                continue
            request = FMPCorporateActionRequest(
                provider=provider_name,
                operation="fetch_corporate_actions",
                params={
                    "as_of_date": as_of_date.isoformat(),
                    "symbols": [item.fmp_symbol for item in active],
                },
            )
            request_hashes.append(request.request_hash)
            try:
                if provider_name == "fmp":
                    response = await cast(FMPCorporateActionTransport, transport).execute(
                        request, api_key=self._api_key
                    )
                else:
                    response = await cast(SECOfficialEvidenceTransport, transport).execute(
                        request
                    )
            except Exception as exc:
                events.append(
                    make_event(
                        provider_name,
                        FMPEventKind.UNAVAILABLE,
                        DataQualityStatus.UNAVAILABLE,
                        f"transport failed with {exc.__class__.__name__}",
                    )
                )
                continue
            if response.provider != provider_name:
                events.append(
                    make_event(
                        provider_name,
                        FMPEventKind.MALFORMED,
                        DataQualityStatus.UNAVAILABLE,
                        "response provider does not match the requested provenance",
                    )
                )
                continue
            if self._response_exposes_secret(response):
                events.append(
                    make_event(
                        provider_name,
                        FMPEventKind.MALFORMED,
                        DataQualityStatus.UNAVAILABLE,
                        "response rejected at the credential boundary",
                    )
                )
                continue
            if response.available_at > as_of_date or response.available_at > ingested_at:
                events.append(
                    make_event(
                        provider_name,
                        FMPEventKind.WITHHELD_FUTURE,
                        DataQualityStatus.UNAVAILABLE,
                        "evidence was unavailable at the requested point-in-time boundary",
                    )
                )
                continue
            raw_payloads.append(response)
            if response.quality is DataQualityStatus.UNAVAILABLE:
                events.append(
                    make_event(
                        provider_name,
                        FMPEventKind.UNAVAILABLE,
                        response.quality,
                        "provider marked corporate-action evidence unavailable",
                    )
                )
                continue
            if response.quality is not DataQualityStatus.FRESH:
                quality_kinds = {
                    DataQualityStatus.STALE: FMPEventKind.STALE,
                    DataQualityStatus.PARTIAL: FMPEventKind.PARTIAL,
                    DataQualityStatus.CONFLICT: FMPEventKind.CONFLICT,
                }
                events.append(
                    make_event(
                        provider_name,
                        quality_kinds[response.quality],
                        response.quality,
                        f"provider labelled action evidence {response.quality.value}",
                    )
                )
                if response.quality is DataQualityStatus.CONFLICT:
                    continue
            payload = response.payload
            rows = payload.get("data") if isinstance(payload, Mapping) else None
            if not isinstance(rows, list):
                events.append(
                    make_event(
                        provider_name,
                        FMPEventKind.MALFORMED,
                        DataQualityStatus.UNAVAILABLE,
                        "corporate-action data must be a list",
                    )
                )
                continue
            for raw_row in rows:
                if not isinstance(raw_row, Mapping):
                    events.append(
                        make_event(
                            provider_name,
                            FMPEventKind.MALFORMED,
                            DataQualityStatus.PARTIAL,
                            "corporate-action row must be an object",
                        )
                    )
                    continue
                row = cast(Mapping[str, object], raw_row)
                required = {
                    "providerSymbol",
                    "correlationKey",
                    "actionType",
                    "effectiveDate",
                }
                missing = sorted(required - row.keys())
                if missing:
                    events.append(
                        make_event(
                            provider_name,
                            FMPEventKind.MISSING_FIELD,
                            DataQualityStatus.PARTIAL,
                            "corporate-action row is missing required fields: "
                            + ",".join(missing),
                        )
                    )
                    continue
                symbol = str(row["providerSymbol"])
                instrument = by_symbol.get(symbol)
                if instrument is None:
                    events.append(
                        make_event(
                            provider_name,
                            FMPEventKind.UNMATCHED,
                            DataQualityStatus.PARTIAL,
                            f"unmatched provider symbol: {symbol or '<empty>'}",
                        )
                    )
                    continue
                try:
                    action_type = CorporateActionType(str(row["actionType"]))
                except ValueError:
                    events.append(
                        make_event(
                            provider_name,
                            FMPEventKind.UNSUPPORTED_ACTION,
                            DataQualityStatus.PARTIAL,
                            "corporate-action type is unsupported",
                        )
                    )
                    continue
                if action_type not in {
                    CorporateActionType.SPLIT,
                    CorporateActionType.CASH_DIVIDEND,
                }:
                    events.append(
                        make_event(
                            provider_name,
                            FMPEventKind.UNSUPPORTED_ACTION,
                            DataQualityStatus.PARTIAL,
                            "corporate-action type is outside this bounded slice",
                        )
                    )
                    continue
                term_fields = (
                    ("numerator", "denominator")
                    if action_type is CorporateActionType.SPLIT
                    else ("cashAmount", "currency")
                )
                missing_terms = [name for name in term_fields if row.get(name) is None]
                if missing_terms:
                    events.append(
                        make_event(
                            provider_name,
                            FMPEventKind.MISSING_FIELD,
                            DataQualityStatus.PARTIAL,
                            "corporate-action row is missing term fields: "
                            + ",".join(missing_terms),
                        )
                    )
                    continue
                try:
                    effective_date = date.fromisoformat(str(row["effectiveDate"]))
                    correlation_key = str(row["correlationKey"])
                    if not correlation_key:
                        raise ValueError("correlation key is empty")
                    ratio: Decimal | None = None
                    cash_amount: Decimal | None = None
                    currency: str | None = None
                    if action_type is CorporateActionType.SPLIT:
                        numerator = Decimal(str(row["numerator"]))
                        denominator = Decimal(str(row["denominator"]))
                        if numerator <= 0 or denominator <= 0:
                            raise ValueError("split terms must be positive")
                        ratio = numerator / denominator
                    else:
                        cash_amount = Decimal(str(row["cashAmount"]))
                        currency = str(row["currency"])
                    timing = ObservationTime(
                        observed_at=response.observed_at,
                        available_at=response.available_at,
                        ingested_at=ingested_at,
                        as_of_date=as_of_date,
                    )
                    action = CorporateAction(
                        security_id=instrument.security_id,
                        provider=provider_name,
                        provider_symbol=symbol,
                        source_record_id=(
                            f"{response.source_record_id}:action:{correlation_key}"
                        ),
                        source_hash=response.source_hash,
                        revision=response.revision,
                        timing=timing,
                        quality=response.quality,
                        action_type=action_type,
                        effective_date=effective_date,
                        ratio=ratio,
                        cash_amount=cash_amount,
                        currency=currency,
                    )
                    evidence.append(
                        CorporateActionEvidence(
                            action_id=uuid5(
                                NAMESPACE_URL,
                                "prism:us:corporate-action:"
                                f"{instrument.security_id.value}:"
                                f"{action_type.value}:{correlation_key}",
                            ),
                            effective_at=datetime.combine(
                                effective_date,
                                time.min,
                                tzinfo=self._market_timezone,
                            ),
                            action=action,
                        )
                    )
                except (ArithmeticError, KeyError, TypeError, ValueError):
                    events.append(
                        make_event(
                            provider_name,
                            FMPEventKind.MALFORMED,
                            DataQualityStatus.PARTIAL,
                            "corporate-action row has invalid or incomplete terms",
                        )
                    )

        deduplicated: list[CorporateActionEvidence] = []
        seen_source_revisions: dict[tuple[str, str, int], CorporateActionEvidence] = {}
        source_revision_diverged = False
        for item in evidence:
            key = (
                item.action.provider,
                item.action.source_record_id,
                item.action.revision,
            )
            prior = seen_source_revisions.get(key)
            if prior is None:
                seen_source_revisions[key] = item
                deduplicated.append(item)
            elif prior == item:
                events.append(
                    make_event(
                        item.action.provider,
                        FMPEventKind.DUPLICATE_ACTION,
                        item.action.quality,
                        "exact corporate-action evidence replay was deduplicated",
                    )
                )
            else:
                source_revision_diverged = True
        if source_revision_diverged:
            events.append(
                make_event(
                    "fmp_sec",
                    FMPEventKind.SOURCE_REVISION_DIVERGENCE,
                    DataQualityStatus.CONFLICT,
                    "one provider source revision contained divergent action terms",
                )
            )
            evidence = []
        else:
            evidence = deduplicated

        signatures_by_id: dict[object, set[tuple[object, ...]]] = {}
        for item in evidence:
            action = item.action
            signatures_by_id.setdefault(item.action_id, set()).add(
                (
                    action.action_type,
                    action.effective_date,
                    item.effective_at,
                    action.ratio,
                    action.cash_amount,
                    action.currency,
                )
            )
        for action_id, signatures in signatures_by_id.items():
            if len(signatures) > 1:
                events.append(
                    make_event(
                        "fmp_sec",
                        FMPEventKind.PROVIDER_CONFLICT,
                        DataQualityStatus.CONFLICT,
                        f"providers disagree for correlated action {action_id}",
                    )
                )

        evidence.sort(
            key=lambda item: (
                item.effective_at,
                str(item.action_id),
                item.action.provider,
                item.action.source_record_id,
                item.action.revision,
            )
        )
        precedence = {
            DataQualityStatus.FRESH: 0,
            DataQualityStatus.PARTIAL: 1,
            DataQualityStatus.STALE: 2,
            DataQualityStatus.UNAVAILABLE: 3,
            DataQualityStatus.CONFLICT: 4,
        }
        qualities = [item.action.quality for item in evidence]
        qualities.extend(item.quality for item in events)
        quality = (
            max(qualities, key=precedence.__getitem__)
            if qualities
            else DataQualityStatus.UNAVAILABLE
        )
        return FMPCorporateActionFetchResult(
            corporate_action_evidence=tuple(evidence),
            raw_payloads=tuple(raw_payloads),
            request_hashes=tuple(request_hashes),
            events=tuple(events),
            quality=quality,
        )

    async def _fetch_fallback(
        self,
        *,
        active: tuple[FMPInstrument, ...],
        as_of_date: datetime,
        ingested_at: datetime,
        budget: _RequestBudget,
        primary_source_record_ids: frozenset[str],
    ) -> tuple[
        tuple[PriceBar, ...],
        tuple[SymbolMapping, ...],
        tuple[FMPFallbackResponseEnvelope, ...],
        tuple[str, ...],
        tuple[FMPProviderEvent, ...],
    ]:
        provider = self._fallback_provider
        transport = self._fallback_transport
        if provider is None or transport is None:
            return (), (), (), (), (
                FMPProviderEvent(
                    provider=provider or "fallback",
                    kind=FMPEventKind.FALLBACK_MISSING_TRANSPORT,
                    quality=DataQualityStatus.UNAVAILABLE,
                    attempt=max(1, budget.used),
                    operation="fetch_fallback_prices",
                    detail="fallback mode requires an injected transport and provider identity",
                    occurred_at=ingested_at,
                ),
            )
        request = FMPFallbackRequest(
            provider=provider,
            operation="fetch_fallback_prices",
            params={
                "as_of_date": as_of_date.isoformat(),
                "symbols": [item.fmp_symbol for item in active],
            },
        )
        retry_events: list[FMPProviderEvent] = []
        response: FMPFallbackResponseEnvelope | None = None
        attempt = max(1, budget.used)
        for local_attempt in range(1, self._fallback_max_attempts + 1):
            if budget.remaining < 1:
                return (), (), (), (request.request_hash,), (
                    *retry_events,
                    FMPProviderEvent(
                        provider=provider,
                        kind=FMPEventKind.FALLBACK_BUDGET_EXHAUSTED,
                        quality=DataQualityStatus.UNAVAILABLE,
                        attempt=max(1, budget.used),
                        operation=request.operation,
                        detail="aggregate request budget has no fallback allowance",
                        occurred_at=ingested_at,
                    ),
                )
            attempt = budget.consume()
            retry_detail: str | None = None
            retry_kind = FMPEventKind.FALLBACK_RETRY
            try:
                candidate = await transport.execute(request)
                if candidate.status_code != 429:
                    response = candidate
                    break
                retry_detail = "fallback response status 429"
                retry_kind = FMPEventKind.FALLBACK_RATE_LIMIT
            except (FMPTimeoutError, FMPRateLimitError) as exc:
                retry_detail = exc.__class__.__name__
                retry_kind = (
                    FMPEventKind.FALLBACK_TIMEOUT
                    if isinstance(exc, FMPTimeoutError)
                    else FMPEventKind.FALLBACK_RATE_LIMIT
                )
            except Exception as exc:
                return (), (), (), (request.request_hash,), (
                    *retry_events,
                    FMPProviderEvent(
                        provider=provider,
                        kind=FMPEventKind.FALLBACK_REJECTED,
                        quality=DataQualityStatus.UNAVAILABLE,
                        attempt=attempt,
                        operation=request.operation,
                        detail=f"fallback failed with {exc.__class__.__name__}",
                        occurred_at=ingested_at,
                    ),
                )
            terminal = (
                local_attempt == self._fallback_max_attempts or budget.remaining < 1
            )
            retry_events.append(
                FMPProviderEvent(
                    provider=provider,
                    kind=retry_kind,
                    quality=DataQualityStatus.UNAVAILABLE,
                    attempt=attempt,
                    operation=request.operation,
                    detail=f"fallback retryable failure: {retry_detail}",
                    occurred_at=ingested_at,
                )
            )
            if terminal:
                return (), (), (), (request.request_hash,), tuple(retry_events)
            if self._sleeper is not None:
                await self._sleeper(float(2 ** (local_attempt - 1)))
        if response is None:
            raise AssertionError("fallback retry loop ended without a response")
        if self._response_exposes_secret(response):
            return (), (), (), (request.request_hash,), (
                *retry_events,
                FMPProviderEvent(
                    provider=provider,
                    kind=FMPEventKind.FALLBACK_MALFORMED,
                    quality=DataQualityStatus.UNAVAILABLE,
                    attempt=attempt,
                    operation=request.operation,
                    detail="fallback response was rejected because it exposed secret material",
                    occurred_at=ingested_at,
                ),
            )
        if response.source_record_id in primary_source_record_ids:
            return (), (), (), (request.request_hash,), (
                *retry_events,
                FMPProviderEvent(
                    provider=provider,
                    kind=FMPEventKind.FALLBACK_MALFORMED,
                    quality=DataQualityStatus.UNAVAILABLE,
                    attempt=attempt,
                    operation=request.operation,
                    detail="fallback source identity collides with retained primary evidence",
                    occurred_at=ingested_at,
                ),
            )
        retained = (response,)
        invalid = (
            response.provider != provider
            or not 200 <= response.status_code < 300
            or response.quality is not DataQualityStatus.FRESH
            or response.available_at > as_of_date
            or response.available_at > ingested_at
        )
        payload = response.payload
        rows = payload.get("data") if isinstance(payload, Mapping) else None
        if invalid or not isinstance(rows, list):
            quality_kinds = {
                DataQualityStatus.STALE: FMPEventKind.FALLBACK_STALE,
                DataQualityStatus.PARTIAL: FMPEventKind.FALLBACK_PARTIAL,
                DataQualityStatus.CONFLICT: FMPEventKind.FALLBACK_CONFLICT,
                DataQualityStatus.UNAVAILABLE: FMPEventKind.FALLBACK_UNAVAILABLE,
            }
            rejected_kind = quality_kinds.get(response.quality)
            if rejected_kind is None:
                rejected_kind = (
                    FMPEventKind.FALLBACK_UNAVAILABLE
                    if not 200 <= response.status_code < 300
                    else FMPEventKind.FALLBACK_MALFORMED
                )
            return (), (), retained, (request.request_hash,), (
                *retry_events,
                FMPProviderEvent(
                    provider=provider,
                    kind=rejected_kind,
                    quality=response.quality,
                    attempt=attempt,
                    operation=request.operation,
                    detail="fallback response failed provider, quality, status, PIT, or schema checks",
                    occurred_at=ingested_at,
                ),
            )
        by_symbol = {item.fmp_symbol: item for item in active}
        bars_by_identity: dict[tuple[str, date], PriceBar] = {}
        present: set[str] = set()
        try:
            for raw_row in rows:
                if not isinstance(raw_row, Mapping):
                    raise ValueError("fallback row must be an object")
                row = cast(Mapping[str, object], raw_row)
                symbol = str(row.get("symbol", ""))
                instrument = by_symbol.get(symbol)
                if instrument is None:
                    raise ValueError("fallback returned an unmatched symbol")
                required_core = {
                    "date",
                    "rawOpen",
                    "rawHigh",
                    "rawLow",
                    "rawClose",
                    "rawVolume",
                }
                if required_core - row.keys():
                    raise ValueError("fallback row is missing a core field")
                adjusted_names = (
                    "adjustedOpen",
                    "adjustedHigh",
                    "adjustedLow",
                    "adjustedClose",
                    "adjustedVolume",
                )
                adjusted_present = tuple(row.get(name) is not None for name in adjusted_names)
                if any(adjusted_present) != all(adjusted_present) or (
                    any(adjusted_present) and row.get("adjustmentAsOf") is None
                ):
                    raise ValueError("fallback adjusted OHLCV is incomplete")
                has_adjusted = all(adjusted_present)
                trading_date = date.fromisoformat(str(row["date"]))
                if trading_date > as_of_date.astimezone(self._market_timezone).date():
                    raise ValueError("fallback trading date exceeds requested as-of date")
                adjustment_as_of = (
                    datetime.fromisoformat(str(row["adjustmentAsOf"]))
                    if has_adjusted
                    else None
                )
                if adjustment_as_of is not None and (
                    adjustment_as_of.tzinfo is None
                    or adjustment_as_of.utcoffset() is None
                    or adjustment_as_of > as_of_date
                ):
                    raise ValueError("fallback adjustment vintage violates PIT")
                timing = ObservationTime(
                    observed_at=response.observed_at,
                    available_at=response.available_at,
                    ingested_at=ingested_at,
                    as_of_date=as_of_date,
                )
                bar = PriceBar(
                    security_id=instrument.security_id,
                    provider=provider,
                    provider_symbol=symbol,
                    source_record_id=(
                        f"{response.source_record_id}:price:{symbol}:{trading_date.isoformat()}"
                    ),
                    source_hash=response.source_hash,
                    revision=response.revision,
                    timing=timing,
                    quality=DataQualityStatus.FRESH,
                    bar_start=datetime.combine(
                        trading_date, time.min, tzinfo=self._market_timezone
                    ),
                    bar_end=datetime.combine(
                        trading_date, time(16, 0), tzinfo=self._market_timezone
                    ),
                    interval="1d",
                    currency="USD",
                    raw_open=Decimal(str(row["rawOpen"])),
                    raw_high=Decimal(str(row["rawHigh"])),
                    raw_low=Decimal(str(row["rawLow"])),
                    raw_close=Decimal(str(row["rawClose"])),
                    raw_volume=Decimal(str(row["rawVolume"])),
                    adjusted_open=Decimal(str(row["adjustedOpen"])) if has_adjusted else None,
                    adjusted_high=Decimal(str(row["adjustedHigh"])) if has_adjusted else None,
                    adjusted_low=Decimal(str(row["adjustedLow"])) if has_adjusted else None,
                    adjusted_close=Decimal(str(row["adjustedClose"])) if has_adjusted else None,
                    adjusted_volume=Decimal(str(row["adjustedVolume"])) if has_adjusted else None,
                    adjustment_as_of=adjustment_as_of,
                )
                identity = (symbol, trading_date)
                prior = bars_by_identity.get(identity)
                if prior is not None and prior != bar:
                    raise ValueError("fallback contains conflicting price rows")
                bars_by_identity[identity] = bar
                present.add(symbol)
        except (ArithmeticError, TypeError, ValueError):
            return (), (), retained, (request.request_hash,), (
                *retry_events,
                FMPProviderEvent(
                    provider=provider,
                    kind=FMPEventKind.FALLBACK_MALFORMED,
                    quality=DataQualityStatus.UNAVAILABLE,
                    attempt=attempt,
                    operation=request.operation,
                    detail="fallback payload is incomplete, malformed, conflicting, or violates PIT",
                    occurred_at=ingested_at,
                ),
            )
        if present != set(by_symbol):
            return (), (), retained, (request.request_hash,), (
                *retry_events,
                FMPProviderEvent(
                    provider=provider,
                    kind=FMPEventKind.FALLBACK_PARTIAL,
                    quality=DataQualityStatus.PARTIAL,
                    attempt=attempt,
                    operation=request.operation,
                    detail="fallback did not contain every requested active security",
                    occurred_at=ingested_at,
                ),
            )
        bars = tuple(
            sorted(
                bars_by_identity.values(),
                key=lambda item: (item.bar_start, str(item.security_id.value)),
            )
        )
        mappings = tuple(
            SymbolMapping(
                security_id=item.security_id,
                provider=provider,
                provider_symbol=item.fmp_symbol,
                market="US",
                valid_from=item.valid_from,
                valid_to=item.valid_to,
                timing=ObservationTime(
                    observed_at=response.observed_at,
                    available_at=response.available_at,
                    ingested_at=ingested_at,
                    as_of_date=as_of_date,
                ),
                source_hash=response.source_hash,
            )
            for item in sorted(active, key=lambda item: str(item.security_id.value))
        )
        return bars, mappings, retained, (request.request_hash,), (
            *retry_events,
            FMPProviderEvent(
                provider=provider,
                kind=FMPEventKind.FALLBACK_SELECTED,
                quality=DataQualityStatus.FRESH,
                attempt=attempt,
                operation=request.operation,
                detail="complete fresh fallback evidence selected under explicit research policy",
                occurred_at=ingested_at,
            ),
        )

    async def _execute_with_retry(
        self,
        request: FMPRequest,
        *,
        occurred_at: datetime,
        budget: _RequestBudget | None = None,
        reserved_requests: int = 0,
    ) -> tuple[
        FMPResponseEnvelope | None,
        tuple[FMPProviderEvent, ...],
        CapabilityStatus | None,
    ]:
        events: list[FMPProviderEvent] = []
        terminal_status: CapabilityStatus | None = None
        request_budget = budget or _RequestBudget(self._max_attempts)
        for local_attempt in range(1, self._max_attempts + 1):
            retry_delay = float(2 ** (local_attempt - 1))
            if request_budget.remaining <= reserved_requests:
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
                    if exc.retry_after_seconds is not None:
                        retry_delay = min(exc.retry_after_seconds, 60.0)
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
            if (
                local_attempt == self._max_attempts
                or request_budget.remaining <= reserved_requests
            ):
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
                await self._sleeper(retry_delay)
        raise AssertionError("unreachable retry state")

    async def probe_capability(self, *, as_of_date: datetime) -> CapabilityProbeResult:
        self._require_aware(as_of_date, "as_of_date")
        occurred_at = self._require_aware(self._clock(), "clock result")
        if occurred_at < as_of_date:
            raise ValueError("clock result must be at or after as_of_date")
        request = FMPRequest(
            operation="probe_capability",
            path=FMP_RAW_EOD_PATH,
            params={
                "as_of_date": as_of_date.isoformat(),
                "limit": 1,
                "symbol": "AAPL",
            },
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
                path=FMP_RAW_EOD_PATH,
                params={
                    "as_of_date": as_of_date.isoformat(),
                    "page": expected_page,
                    "symbols": [item.fmp_symbol for item in active],
                },
            )
            request_hashes.append(request.request_hash)
            page_response, retry_events, _ = await self._execute_with_retry(
                request,
                occurred_at=ingested_at,
                budget=budget,
                reserved_requests=(
                    self._fallback_max_attempts
                    if self._fallback_mode is FMPFallbackMode.RESEARCH_FIXTURE
                    and self._fallback_transport is not None
                    and self._fallback_provider is not None
                    else 0
                ),
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

        # Ingestion is receipt-side provenance: sample it only after the bounded
        # primary transport sequence has completed, never before the HTTP call.
        post_response_ingested_at = self._require_aware(
            self._clock(), "clock result"
        )
        if post_response_ingested_at < ingested_at:
            raise ValueError("post-response clock result must not move backwards")
        ingested_at = post_response_ingested_at
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
        primary_quality = quality
        selected_provider: str | None = "fmp" if quality is DataQualityStatus.FRESH else None
        fallback_raw_payloads: tuple[FMPFallbackResponseEnvelope, ...] = ()
        fallback_request_hashes: tuple[str, ...] = ()
        fallback_reason: FMPFallbackReason | None = None
        if quality is not DataQualityStatus.FRESH:
            bars = []
            mappings = []
            if self._fallback_mode is FMPFallbackMode.DISABLED:
                events.append(
                    FMPProviderEvent(
                        provider="fmp",
                        kind=FMPEventKind.FALLBACK_DISABLED,
                        quality=quality,
                        attempt=max(1, budget.used),
                        operation="apply_fallback_policy",
                        detail="fallback policy is disabled",
                        occurred_at=ingested_at,
                    )
                )
            elif any(
                event.kind
                in {
                    FMPEventKind.CONFLICT,
                    FMPEventKind.MALFORMED,
                    FMPEventKind.PAGINATION_MALFORMED,
                    FMPEventKind.INELIGIBLE,
                }
                for event in events
            ):
                events.append(
                    FMPProviderEvent(
                        provider="fmp",
                        kind=FMPEventKind.FALLBACK_INELIGIBLE,
                        quality=quality,
                        attempt=max(1, budget.used),
                        operation="apply_fallback_policy",
                        detail="conflicting, malformed, or ineligible primary evidence blocks fallback selection",
                        occurred_at=ingested_at,
                    )
                )
            else:
                event_kinds = {event.kind for event in events}
                if primary_quality is DataQualityStatus.STALE:
                    fallback_reason = FMPFallbackReason.STALE
                elif response is not None and FMPEventKind.MISSING in event_kinds:
                    fallback_reason = FMPFallbackReason.MISSING
                elif primary_quality is DataQualityStatus.PARTIAL:
                    fallback_reason = FMPFallbackReason.PARTIAL
                elif FMPEventKind.RATE_LIMIT in event_kinds:
                    fallback_reason = FMPFallbackReason.RATE_LIMIT
                elif FMPEventKind.TIMEOUT in event_kinds:
                    fallback_reason = FMPFallbackReason.TIMEOUT
                else:
                    fallback_reason = FMPFallbackReason.UNAVAILABLE
                (
                    fallback_bars,
                    fallback_mappings,
                    fallback_raw_payloads,
                    fallback_request_hashes,
                    fallback_events,
                ) = await self._fetch_fallback(
                    active=active,
                    as_of_date=as_of_date,
                    ingested_at=ingested_at,
                    budget=budget,
                    primary_source_record_ids=frozenset(
                        item.source_record_id for item in raw_payloads
                    ),
                )
                events.extend(fallback_events)
                if fallback_bars and fallback_mappings:
                    bars = list(fallback_bars)
                    mappings = list(fallback_mappings)
                    quality = DataQualityStatus.FRESH
                    selected_provider = self._fallback_provider
        content = {
            "market": "US",
            "as_of_date": as_of_date.isoformat(),
            "fallback_mode": self._fallback_mode.value,
            "primary_quality": primary_quality.value,
            "selected_provider": selected_provider,
            "requested_security_ids": [str(item.value) for item in security_ids],
            "request_hashes": request_hashes,
            "raw_payload_hashes": [item.source_hash for item in raw_payloads],
            "fallback_request_hashes": fallback_request_hashes,
            "fallback_raw_payload_hashes": [
                item.source_hash for item in fallback_raw_payloads
            ],
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
            primary_quality=primary_quality,
            fallback_mode=self._fallback_mode,
            selected_provider=selected_provider,
            fallback_raw_payloads=fallback_raw_payloads,
            fallback_request_hashes=fallback_request_hashes,
            fallback_reason=fallback_reason,
        )
