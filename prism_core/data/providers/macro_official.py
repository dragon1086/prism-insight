"""Dormant point-in-time contracts for official macroeconomic evidence.

This module deliberately contains no concrete HTTP client. FRED current-series,
ALFRED vintage, and ECOS evidence remain separate and require a source-specific,
durably approved transport before any live network access.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Callable, Protocol

from prism_core.data.contracts import DataQualityStatus


class MacroOfficialSource(str, Enum):
    FRED = "FRED"
    ALFRED = "ALFRED"
    ECOS = "ECOS"


class MacroOfficialCapability(str, Enum):
    FRED_SERIES_CURRENT = "FRED_SERIES_CURRENT"
    ALFRED_SERIES_VINTAGE = "ALFRED_SERIES_VINTAGE"
    ECOS_STATISTIC_SEARCH = "ECOS_STATISTIC_SEARCH"


class MacroOfficialError(RuntimeError):
    """Sanitized official-macro adapter failure."""


class MacroOfficialTransportMode(str, Enum):
    FIXTURE = "FIXTURE"
    LIVE = "LIVE"


_SOURCE_CAPABILITY = {
    MacroOfficialSource.FRED: MacroOfficialCapability.FRED_SERIES_CURRENT,
    MacroOfficialSource.ALFRED: MacroOfficialCapability.ALFRED_SERIES_VINTAGE,
    MacroOfficialSource.ECOS: MacroOfficialCapability.ECOS_STATISTIC_SEARCH,
}
_ENDPOINT = {
    MacroOfficialCapability.FRED_SERIES_CURRENT: (
        "api.stlouisfed.org/fred/series/observations"
    ),
    MacroOfficialCapability.ALFRED_SERIES_VINTAGE: (
        "api.stlouisfed.org/fred/series/observations"
    ),
    MacroOfficialCapability.ECOS_STATISTIC_SEARCH: "ecos.bok.or.kr/api/StatisticSearch",
}
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


@dataclass(frozen=True)
class MacroOfficialRequest:
    source: MacroOfficialSource
    capability: MacroOfficialCapability
    endpoint: str
    series_id: str
    correlation_id: str
    vintage_date: date | None = None

    def __post_init__(self) -> None:
        if _SOURCE_CAPABILITY.get(self.source) is not self.capability:
            raise ValueError("source and capability do not match")
        if _ENDPOINT[self.capability] != self.endpoint:
            raise ValueError("endpoint must match the declared macro capability")
        for name, value in (
            ("series_id", self.series_id),
            ("correlation_id", self.correlation_id),
        ):
            if not _SAFE_ID.fullmatch(value):
                raise ValueError(f"{name} must be a sanitized non-empty identifier")
        if self.source is MacroOfficialSource.ALFRED:
            if type(self.vintage_date) is not date:
                raise ValueError("vintage_date is required for ALFRED")
        elif self.vintage_date is not None:
            raise ValueError("vintage_date is only valid for ALFRED")


@dataclass(frozen=True)
class MacroOfficialSourceApproval:
    """Durable exact-scope authorization metadata for one macro source."""

    approval_id: str
    manifest_hash: str
    source: MacroOfficialSource
    capability: MacroOfficialCapability
    endpoint: str
    terms_id: str
    license_id: str
    credential_scope: str
    max_calls: int
    minimum_request_interval_ms: int
    max_cost_usd_cents: int
    approved_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        if _SOURCE_CAPABILITY.get(self.source) is not self.capability:
            raise ValueError("source and capability do not match")
        if _ENDPOINT[self.capability] != self.endpoint:
            raise ValueError("endpoint must match the declared macro capability")
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
        if (
            type(self.minimum_request_interval_ms) is not int
            or self.minimum_request_interval_ms < 1
        ):
            raise ValueError("minimum_request_interval_ms must be positive")
        if type(self.max_cost_usd_cents) is not int or self.max_cost_usd_cents < 0:
            raise ValueError("max_cost_usd_cents must be non-negative")
        for name, value in (
            ("approved_at", self.approved_at),
            ("expires_at", self.expires_at),
        ):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware")
        if self.expires_at <= self.approved_at:
            raise ValueError("expires_at must be after approved_at")


@dataclass(frozen=True, init=False)
class MacroOfficialEvidenceEnvelope:
    """Immutable source-separated macro evidence with explicit availability."""

    source: MacroOfficialSource
    capability: MacroOfficialCapability
    endpoint: str
    source_record_id: str
    series_id: str
    observation_period: str
    observed_at: datetime
    available_at: datetime
    ingested_at: datetime
    as_of_date: datetime
    release_id: str
    revision: int
    vintage_date: date
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
        source: MacroOfficialSource,
        capability: MacroOfficialCapability,
        endpoint: str,
        source_record_id: str,
        series_id: str,
        observation_period: str,
        observed_at: datetime,
        available_at: datetime,
        ingested_at: datetime,
        as_of_date: datetime,
        release_id: str,
        revision: int,
        vintage_date: date,
        terms_id: str,
        license_id: str,
        quality: DataQualityStatus,
        correlation_id: str,
        raw_payload: bytes,
        fact_key: str | None = None,
        fact_hash: str | None = None,
    ) -> None:
        if _SOURCE_CAPABILITY.get(source) is not capability:
            raise ValueError("source and capability do not match")
        if _ENDPOINT[capability] != endpoint:
            raise ValueError("endpoint must match the declared macro capability")
        for name, value in (
            ("source_record_id", source_record_id),
            ("series_id", series_id),
            ("observation_period", observation_period),
            ("release_id", release_id),
            ("terms_id", terms_id),
            ("license_id", license_id),
            ("correlation_id", correlation_id),
        ):
            if not _SAFE_ID.fullmatch(value):
                raise ValueError(f"{name} must be a sanitized non-empty identifier")
        if type(revision) is not int or revision < 0:
            raise ValueError("revision must be non-negative")
        if type(vintage_date) is not date:
            raise ValueError("vintage_date must be a date")
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
        if vintage_date > as_of_date.date():
            raise ValueError("vintage_date cannot exceed as_of_date")
        if not raw_payload:
            raise ValueError("raw_payload must not be empty")

        payload = bytes(raw_payload)
        values = {
            "source": source,
            "capability": capability,
            "endpoint": endpoint,
            "source_record_id": source_record_id,
            "series_id": series_id,
            "observation_period": observation_period,
            "observed_at": observed_at,
            "available_at": available_at,
            "ingested_at": ingested_at,
            "as_of_date": as_of_date,
            "release_id": release_id,
            "revision": revision,
            "vintage_date": vintage_date,
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


class MacroOfficialTransport(Protocol):
    """Injected fixture or separately approved official-macro transport."""

    mode: MacroOfficialTransportMode

    async def fetch(
        self,
        request: MacroOfficialRequest,
        *,
        approval: MacroOfficialSourceApproval | None,
    ) -> MacroOfficialEvidenceEnvelope: ...


@dataclass(frozen=True)
class MacroOfficialProviderEvent:
    source: MacroOfficialSource
    capability: MacroOfficialCapability
    quality: DataQualityStatus
    reason: str
    correlation_id: str | None
    fact_key: str | None = None
    related_sources: tuple[MacroOfficialSource, ...] = ()


@dataclass(frozen=True)
class MacroOfficialFetchResult:
    evidence: tuple[MacroOfficialEvidenceEnvelope, ...]
    events: tuple[MacroOfficialProviderEvent, ...]
    quality: DataQualityStatus
    core_evidence_usable: bool


class MacroOfficialProvider:
    """Collect source-separated official macro evidence without runtime wiring."""

    def __init__(
        self,
        *,
        transport: MacroOfficialTransport,
        approvals: tuple[MacroOfficialSourceApproval, ...] = (),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if transport.mode is MacroOfficialTransportMode.LIVE and not approvals:
            raise MacroOfficialError(
                "live official-macro transport requires source-specific durable approval"
            )
        self._transport = transport
        self._approvals = approvals
        self._clock = clock
        self._approval_calls: dict[MacroOfficialSourceApproval, int] = {}
        self._approval_last_request_at: dict[MacroOfficialSourceApproval, datetime] = {}

    def _authorize_live_request(
        self, request: MacroOfficialRequest
    ) -> MacroOfficialSourceApproval:
        if self._clock is None:
            raise MacroOfficialError("live approval validation requires an injected clock")
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise MacroOfficialError("live approval clock must be timezone-aware")
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
            raise MacroOfficialError("no active exact macro approval matches the request")
        calls = self._approval_calls.get(approval, 0)
        if calls >= approval.max_calls:
            raise MacroOfficialError("live macro approval call bound is exhausted")
        previous = self._approval_last_request_at.get(approval)
        if previous is not None:
            elapsed_ms = (now - previous).total_seconds() * 1000
            if elapsed_ms < approval.minimum_request_interval_ms:
                raise MacroOfficialError("live macro approval rate bound is not satisfied")
        # Count attempts before I/O so failures cannot create an unbounded retry loop.
        self._approval_calls[approval] = calls + 1
        self._approval_last_request_at[approval] = now
        return approval

    async def collect(
        self,
        *,
        requests: tuple[MacroOfficialRequest, ...],
        as_of_date: datetime,
    ) -> MacroOfficialFetchResult:
        if as_of_date.tzinfo is None or as_of_date.utcoffset() is None:
            raise ValueError("as_of_date must be timezone-aware")
        if not requests:
            return MacroOfficialFetchResult(
                evidence=(),
                events=(),
                quality=DataQualityStatus.UNAVAILABLE,
                core_evidence_usable=False,
            )

        evidence: list[MacroOfficialEvidenceEnvelope] = []
        events: list[MacroOfficialProviderEvent] = []
        for request in requests:
            approval = None
            if self._transport.mode is MacroOfficialTransportMode.LIVE:
                approval = self._authorize_live_request(request)
            try:
                item = await self._transport.fetch(request, approval=approval)
            except Exception as exc:
                del exc
                events.append(
                    MacroOfficialProviderEvent(
                        source=request.source,
                        capability=request.capability,
                        quality=DataQualityStatus.UNAVAILABLE,
                        reason="MACRO_FETCH_FAILED",
                        correlation_id=request.correlation_id,
                        related_sources=(request.source,),
                    )
                )
                continue
            if not isinstance(item, MacroOfficialEvidenceEnvelope):
                raise MacroOfficialError("macro transport returned an invalid evidence type")
            if approval is not None and (
                item.terms_id != approval.terms_id
                or item.license_id != approval.license_id
            ):
                raise MacroOfficialError(
                    "live macro evidence terms or license do not match durable approval"
                )
            if (
                item.source is not request.source
                or item.capability is not request.capability
                or item.endpoint != request.endpoint
                or item.series_id != request.series_id
                or item.correlation_id != request.correlation_id
            ):
                raise MacroOfficialError("transport evidence does not match its request")
            if (
                request.source is MacroOfficialSource.ALFRED
                and item.vintage_date != request.vintage_date
            ):
                raise MacroOfficialError("ALFRED evidence has the wrong requested vintage")
            if item.as_of_date != as_of_date:
                raise MacroOfficialError("transport evidence has the wrong as-of boundary")
            evidence.append(item)

        by_fact: dict[str, list[MacroOfficialEvidenceEnvelope]] = {}
        for item in evidence:
            if item.fact_key is not None:
                by_fact.setdefault(item.fact_key, []).append(item)
        for fact_key, facts in by_fact.items():
            if len({item.fact_hash for item in facts}) > 1:
                related = tuple(dict.fromkeys(item.source for item in facts))
                events.append(
                    MacroOfficialProviderEvent(
                        source=facts[-1].source,
                        capability=facts[-1].capability,
                        quality=DataQualityStatus.CONFLICT,
                        reason="MACRO_FACT_CONFLICT",
                        correlation_id=None,
                        fact_key=fact_key,
                        related_sources=related,
                    )
                )

        observed_qualities = {
            *(item.quality for item in evidence),
            *(event.quality for event in events),
        }
        priority = (
            DataQualityStatus.CONFLICT,
            DataQualityStatus.UNAVAILABLE,
            DataQualityStatus.STALE,
            DataQualityStatus.PARTIAL,
        )
        quality = next(
            (candidate for candidate in priority if candidate in observed_qualities),
            DataQualityStatus.FRESH,
        )
        usable = not events and len(evidence) == len(requests) and all(
            item.quality is DataQualityStatus.FRESH for item in evidence
        )
        return MacroOfficialFetchResult(
            evidence=tuple(evidence),
            events=tuple(events),
            quality=quality,
            core_evidence_usable=usable,
        )
