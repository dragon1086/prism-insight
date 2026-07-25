"""Dormant point-in-time contracts for Korean official evidence sources.

This module deliberately contains no concrete HTTP client. KRX, KIND, and DART
have separate capabilities, terms, credentials, and approval boundaries; live
network access must be supplied by a separately approved transport.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Callable, Protocol

from prism_core.data.contracts import DataQualityStatus


class KROfficialSource(str, Enum):
    KRX = "KRX"
    KIND = "KIND"
    DART = "DART"


class KROfficialCapability(str, Enum):
    KRX_MARKET_REFERENCE = "KRX_MARKET_REFERENCE"
    KIND_LISTED_COMPANY_DISCLOSURE = "KIND_LISTED_COMPANY_DISCLOSURE"
    DART_FILING = "DART_FILING"


_SOURCE_CAPABILITY = {
    KROfficialSource.KRX: KROfficialCapability.KRX_MARKET_REFERENCE,
    KROfficialSource.KIND: KROfficialCapability.KIND_LISTED_COMPANY_DISCLOSURE,
    KROfficialSource.DART: KROfficialCapability.DART_FILING,
}
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SAFE_ENDPOINT = re.compile(r"^[a-z0-9.-]+(?::[0-9]+)?/[A-Za-z0-9._~/-]+$")


def _is_safe_endpoint(endpoint: str) -> bool:
    if not _SAFE_ENDPOINT.fullmatch(endpoint) or any(
        marker in endpoint for marker in ("?", "#", "@")
    ):
        return False
    path = endpoint.split("/", 1)[1]
    return all(segment not in {".", ".."} for segment in path.split("/"))


class KROfficialError(RuntimeError):
    """Sanitized KR official adapter failure."""


class KROfficialTransportMode(str, Enum):
    FIXTURE = "FIXTURE"
    LIVE = "LIVE"


@dataclass(frozen=True)
class KROfficialRequest:
    source: KROfficialSource
    capability: KROfficialCapability
    endpoint: str
    provider_symbol: str
    correlation_id: str

    def __post_init__(self) -> None:
        if _SOURCE_CAPABILITY.get(self.source) is not self.capability:
            raise ValueError("source and capability do not match")
        if not _is_safe_endpoint(self.endpoint):
            raise ValueError("endpoint must be a sanitized exact host/path identity")
        for name, value in (
            ("provider_symbol", self.provider_symbol),
            ("correlation_id", self.correlation_id),
        ):
            if not _SAFE_ID.fullmatch(value):
                raise ValueError(f"{name} must be a sanitized non-empty identifier")


@dataclass(frozen=True)
class KROfficialSourceApproval:
    """Durable source-specific authorization metadata for a bounded live call."""

    approval_id: str
    manifest_hash: str
    source: KROfficialSource
    capability: KROfficialCapability
    endpoint: str
    terms_id: str
    license_id: str
    credential_scope: str
    max_calls: int
    max_cost_krw: int
    approved_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        KROfficialRequest(
            source=self.source,
            capability=self.capability,
            endpoint=self.endpoint,
            provider_symbol="approval-scope",
            correlation_id="approval-validation",
        )
        for name, value in (
            ("approval_id", self.approval_id),
            ("terms_id", self.terms_id),
            ("license_id", self.license_id),
            ("credential_scope", self.credential_scope),
        ):
            if not _SAFE_ID.fullmatch(value):
                raise ValueError(f"{name} must be a sanitized non-empty identifier")
        if not re.fullmatch(r"[0-9a-f]{64}", self.manifest_hash):
            raise ValueError("manifest_hash must be a lowercase SHA-256 digest")
        if type(self.max_calls) is not int or self.max_calls < 1:
            raise ValueError("max_calls must be positive")
        if type(self.max_cost_krw) is not int or self.max_cost_krw < 0:
            raise ValueError("max_cost_krw must be non-negative")
        for name, value in (
            ("approved_at", self.approved_at),
            ("expires_at", self.expires_at),
        ):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware")
        if self.expires_at <= self.approved_at:
            raise ValueError("expires_at must be after approved_at")


@dataclass(frozen=True, init=False)
class KROfficialEvidenceEnvelope:
    """Immutable raw official evidence with complete PIT and legal provenance."""

    source: KROfficialSource
    capability: KROfficialCapability
    endpoint: str
    source_record_id: str
    provider_symbol: str
    observed_at: datetime
    available_at: datetime
    ingested_at: datetime
    as_of_date: datetime
    release_id: str
    revision: int
    vintage: str
    terms_id: str
    license_id: str
    quality: DataQualityStatus
    correlation_id: str
    fact_key: str | None
    fact_hash: str | None
    _raw_payload: bytes = field(repr=False)
    _raw_payload_hash: str = field(repr=False)

    def __init__(
        self,
        *,
        source: KROfficialSource,
        capability: KROfficialCapability,
        endpoint: str,
        source_record_id: str,
        provider_symbol: str,
        observed_at: datetime,
        available_at: datetime,
        ingested_at: datetime,
        as_of_date: datetime,
        release_id: str,
        revision: int,
        vintage: str,
        terms_id: str,
        license_id: str,
        quality: DataQualityStatus,
        correlation_id: str,
        fact_key: str | None = None,
        fact_hash: str | None = None,
        raw_payload: bytes,
    ) -> None:
        if _SOURCE_CAPABILITY.get(source) is not capability:
            raise ValueError("source and capability do not match")
        if not _is_safe_endpoint(endpoint):
            raise ValueError("endpoint must be a sanitized exact host/path identity")
        for name, value in (
            ("source_record_id", source_record_id),
            ("provider_symbol", provider_symbol),
            ("release_id", release_id),
            ("vintage", vintage),
            ("terms_id", terms_id),
            ("license_id", license_id),
            ("correlation_id", correlation_id),
        ):
            if not _SAFE_ID.fullmatch(value):
                raise ValueError(f"{name} must be a sanitized non-empty identifier")
        if type(revision) is not int or revision < 0:
            raise ValueError("revision must be non-negative")
        if not isinstance(quality, DataQualityStatus):
            raise ValueError("quality must be a DataQualityStatus")
        if (fact_key is None) != (fact_hash is None):
            raise ValueError("fact_key and fact_hash must be supplied together")
        if fact_key is not None and not _SAFE_ID.fullmatch(fact_key):
            raise ValueError("fact_key must be a sanitized identifier")
        if fact_hash is not None and not re.fullmatch(r"[0-9a-f]{64}", fact_hash):
            raise ValueError("fact_hash must be a lowercase SHA-256 digest")
        for name, value in (
            ("observed_at", observed_at),
            ("available_at", available_at),
            ("ingested_at", ingested_at),
            ("as_of_date", as_of_date),
        ):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware")
        if observed_at > available_at:
            raise ValueError("observed_at must be at or before available_at")
        if available_at > ingested_at:
            raise ValueError("available_at must be at or before ingested_at")
        if available_at > as_of_date:
            raise ValueError("available_at must be at or before as_of_date")
        if not raw_payload:
            raise ValueError("raw_payload must not be empty")
        payload = bytes(raw_payload)
        values = {
            "source": source,
            "capability": capability,
            "endpoint": endpoint,
            "source_record_id": source_record_id,
            "provider_symbol": provider_symbol,
            "observed_at": observed_at,
            "available_at": available_at,
            "ingested_at": ingested_at,
            "as_of_date": as_of_date,
            "release_id": release_id,
            "revision": revision,
            "vintage": vintage,
            "terms_id": terms_id,
            "license_id": license_id,
            "quality": quality,
            "correlation_id": correlation_id,
            "fact_key": fact_key,
            "fact_hash": fact_hash,
        }
        for field_name, field_value in values.items():
            object.__setattr__(self, field_name, field_value)
        object.__setattr__(self, "_raw_payload", payload)
        object.__setattr__(self, "_raw_payload_hash", hashlib.sha256(payload).hexdigest())

    @property
    def provider(self) -> str:
        return self.source.value

    @property
    def raw_payload(self) -> bytes:
        return bytes(self._raw_payload)

    @property
    def raw_payload_hash(self) -> str:
        return self._raw_payload_hash


class KROfficialTransport(Protocol):
    """Injected fixture or separately approved source-specific transport."""

    mode: KROfficialTransportMode

    async def fetch(self, request: KROfficialRequest) -> KROfficialEvidenceEnvelope: ...


@dataclass(frozen=True)
class KROfficialProviderEvent:
    source: KROfficialSource
    capability: KROfficialCapability
    quality: DataQualityStatus
    reason: str
    correlation_id: str | None
    fact_key: str | None = None
    related_sources: tuple[KROfficialSource, ...] = ()


@dataclass(frozen=True)
class KROfficialFetchResult:
    evidence: tuple[KROfficialEvidenceEnvelope, ...]
    events: tuple[KROfficialProviderEvent, ...]
    quality: DataQualityStatus
    core_evidence_usable: bool


class KROfficialProvider:
    """Collect source-separated evidence from an injected, non-broker transport."""

    def __init__(
        self,
        *,
        transport: KROfficialTransport,
        approvals: tuple[KROfficialSourceApproval, ...] = (),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if transport.mode is KROfficialTransportMode.LIVE and not approvals:
            raise KROfficialError(
                "live KR official transport requires source-specific durable approval"
            )
        self._transport = transport
        self._approvals = approvals
        self._clock = clock
        self._approval_calls: dict[KROfficialSourceApproval, int] = {}

    def _authorize_live_request(
        self, request: KROfficialRequest
    ) -> KROfficialSourceApproval:
        if self._clock is None:
            raise KROfficialError("live approval validation requires an injected clock")
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise KROfficialError("live approval clock must be timezone-aware")
        approval = next(
            (
                item
                for item in self._approvals
                if item.source is request.source
                and item.capability is request.capability
                and item.endpoint == request.endpoint
                and item.approved_at <= now < item.expires_at
            ),
            None,
        )
        if approval is None:
            raise KROfficialError("no active exact source approval matches the request")
        calls = self._approval_calls.get(approval, 0)
        if calls >= approval.max_calls:
            raise KROfficialError("live source approval call bound is exhausted")
        self._approval_calls[approval] = calls + 1
        return approval

    async def collect(
        self,
        *,
        requests: tuple[KROfficialRequest, ...],
        as_of_date: datetime,
    ) -> KROfficialFetchResult:
        if as_of_date.tzinfo is None or as_of_date.utcoffset() is None:
            raise ValueError("as_of_date must be timezone-aware")
        if not requests:
            return KROfficialFetchResult(
                evidence=(),
                events=(),
                quality=DataQualityStatus.UNAVAILABLE,
                core_evidence_usable=False,
            )
        evidence: list[KROfficialEvidenceEnvelope] = []
        events: list[KROfficialProviderEvent] = []
        for request in requests:
            approval = None
            if self._transport.mode is KROfficialTransportMode.LIVE:
                approval = self._authorize_live_request(request)
            try:
                item = await self._transport.fetch(request)
            except Exception as exc:
                del exc
                events.append(
                    KROfficialProviderEvent(
                        source=request.source,
                        capability=request.capability,
                        quality=DataQualityStatus.UNAVAILABLE,
                        reason="SOURCE_FETCH_FAILED",
                        correlation_id=request.correlation_id,
                        related_sources=(request.source,),
                    )
                )
                continue
            if approval is not None and (
                item.terms_id != approval.terms_id
                or item.license_id != approval.license_id
            ):
                raise KROfficialError(
                    "live evidence terms or license do not match durable approval"
                )
            if (
                item.source is not request.source
                or item.capability is not request.capability
                or item.endpoint != request.endpoint
                or item.provider_symbol != request.provider_symbol
                or item.correlation_id != request.correlation_id
            ):
                raise KROfficialError("transport evidence does not match its request")
            if item.as_of_date != as_of_date:
                raise KROfficialError("transport evidence has the wrong as-of boundary")
            evidence.append(item)
        by_fact: dict[str, list[KROfficialEvidenceEnvelope]] = {}
        for item in evidence:
            if item.fact_key is not None:
                by_fact.setdefault(item.fact_key, []).append(item)
        for fact_key, facts in by_fact.items():
            if len({item.fact_hash for item in facts}) > 1:
                conflict_source = facts[-1]
                events.append(
                    KROfficialProviderEvent(
                        source=conflict_source.source,
                        capability=conflict_source.capability,
                        quality=DataQualityStatus.CONFLICT,
                        reason="SOURCE_FACT_CONFLICT",
                        correlation_id=None,
                        fact_key=fact_key,
                        related_sources=tuple(dict.fromkeys(item.source for item in facts)),
                    )
                )
        usable = not events and len(evidence) == len(requests) and all(
            item.quality is DataQualityStatus.FRESH for item in evidence
        )
        quality_priority = (
            DataQualityStatus.CONFLICT,
            DataQualityStatus.UNAVAILABLE,
            DataQualityStatus.STALE,
            DataQualityStatus.PARTIAL,
        )
        observed_qualities = {
            *(item.quality for item in evidence),
            *(event.quality for event in events),
        }
        quality = next(
            (candidate for candidate in quality_priority if candidate in observed_qualities),
            DataQualityStatus.FRESH,
        )
        return KROfficialFetchResult(
            evidence=tuple(evidence),
            events=tuple(events),
            quality=quality,
            core_evidence_usable=usable,
        )
