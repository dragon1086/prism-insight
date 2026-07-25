"""Dormant point-in-time evidence contracts for SEC EDGAR.

This module deliberately contains no concrete HTTP client. Live SEC access must
be supplied by a separately approved transport that satisfies SEC identification,
rate, endpoint, terms, and validity obligations.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Callable, Protocol

from prism_core.data.contracts import DataQualityStatus


class SECEdgarCapability(str, Enum):
    COMPANY_SUBMISSIONS = "COMPANY_SUBMISSIONS"
    COMPANY_FACTS = "COMPANY_FACTS"
    FILING_DOCUMENT = "FILING_DOCUMENT"


class SECEdgarError(RuntimeError):
    """Sanitized SEC EDGAR adapter failure."""


class SECEdgarTransportMode(str, Enum):
    FIXTURE = "FIXTURE"
    LIVE = "LIVE"


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SAFE_ENDPOINT = re.compile(r"^(?:data|www)\.sec\.gov/[A-Za-z0-9._~/-]+$")
_CIK = re.compile(r"^[0-9]{10}$")
_ACCESSION = re.compile(r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$")


def _is_safe_endpoint(endpoint: str) -> bool:
    if not _SAFE_ENDPOINT.fullmatch(endpoint) or any(
        marker in endpoint for marker in ("?", "#", "@", "%", ";")
    ):
        return False
    path = endpoint.split("/", 1)[1]
    return all(segment not in {".", ".."} for segment in path.split("/"))


def _endpoint_matches_identity(
    capability: SECEdgarCapability,
    endpoint: str,
    cik: str,
    accession_number: str,
) -> bool:
    expected = {
        SECEdgarCapability.COMPANY_SUBMISSIONS: (
            f"data.sec.gov/submissions/CIK{cik}.json"
        ),
        SECEdgarCapability.COMPANY_FACTS: (
            f"data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
        ),
    }
    if capability in expected:
        return endpoint == expected[capability]
    accession_path = accession_number.replace("-", "")
    archive_prefix = f"www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_path}/"
    document_name = endpoint[len(archive_prefix) :] if endpoint.startswith(archive_prefix) else ""
    return bool(document_name) and "/" not in document_name


@dataclass(frozen=True)
class SECEdgarRequest:
    capability: SECEdgarCapability
    endpoint: str
    cik: str
    provider_symbol: str
    accession_number: str
    correlation_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.capability, SECEdgarCapability):
            raise ValueError("capability must be a SECEdgarCapability")
        if not _is_safe_endpoint(self.endpoint):
            raise ValueError("endpoint must be a sanitized exact SEC host/path identity")
        if not _CIK.fullmatch(self.cik):
            raise ValueError("cik must contain exactly ten digits")
        if not _ACCESSION.fullmatch(self.accession_number):
            raise ValueError("accession_number must use the SEC accession format")
        if not _endpoint_matches_identity(
            self.capability, self.endpoint, self.cik, self.accession_number
        ):
            raise ValueError("endpoint must match the declared capability, CIK, and accession")
        for name, value in (
            ("provider_symbol", self.provider_symbol),
            ("correlation_id", self.correlation_id),
        ):
            if not _SAFE_ID.fullmatch(value):
                raise ValueError(f"{name} must be a sanitized non-empty identifier")


@dataclass(frozen=True)
class SECEdgarSourceApproval:
    """Durable exact-scope authorization metadata for a bounded SEC call."""

    approval_id: str
    manifest_hash: str
    capability: SECEdgarCapability
    endpoint: str
    terms_id: str
    license_id: str
    sec_identity_id: str
    credential_scope: str
    max_calls: int
    minimum_request_interval_ms: int
    max_cost_usd_cents: int
    approved_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.capability, SECEdgarCapability):
            raise ValueError("capability must be a SECEdgarCapability")
        if not _is_safe_endpoint(self.endpoint):
            raise ValueError("endpoint must be a sanitized exact SEC host/path identity")
        for name, value in (
            ("approval_id", self.approval_id),
            ("terms_id", self.terms_id),
            ("license_id", self.license_id),
            ("sec_identity_id", self.sec_identity_id),
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
        if type(self.max_cost_usd_cents) is not int or self.max_cost_usd_cents != 0:
            raise ValueError("SEC public-source cost bound must be zero in this foundation")
        for name, value in (
            ("approved_at", self.approved_at),
            ("expires_at", self.expires_at),
        ):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware")
        if self.expires_at <= self.approved_at:
            raise ValueError("expires_at must be after approved_at")


@dataclass(frozen=True, init=False)
class SECEdgarEvidenceEnvelope:
    """Immutable official filing evidence with complete PIT provenance."""

    capability: SECEdgarCapability
    endpoint: str
    source_record_id: str
    cik: str
    provider_symbol: str
    accession_number: str
    form_type: str
    filing_date: date
    period_of_report: date | None
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
        capability: SECEdgarCapability,
        endpoint: str,
        source_record_id: str,
        cik: str,
        provider_symbol: str,
        accession_number: str,
        form_type: str,
        filing_date: date,
        period_of_report: date | None,
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
        raw_payload: bytes,
        fact_key: str | None = None,
        fact_hash: str | None = None,
    ) -> None:
        if not isinstance(capability, SECEdgarCapability):
            raise ValueError("capability must be a SECEdgarCapability")
        if not _is_safe_endpoint(endpoint):
            raise ValueError("endpoint must be a sanitized exact SEC host/path identity")
        for name, value in (
            ("source_record_id", source_record_id),
            ("provider_symbol", provider_symbol),
            ("release_id", release_id),
            ("vintage", vintage),
            ("terms_id", terms_id),
            ("license_id", license_id),
            ("correlation_id", correlation_id),
            ("form_type", form_type),
        ):
            if not _SAFE_ID.fullmatch(value):
                raise ValueError(f"{name} must be a sanitized non-empty identifier")
        if not _CIK.fullmatch(cik):
            raise ValueError("cik must contain exactly ten digits")
        if not _ACCESSION.fullmatch(accession_number):
            raise ValueError("accession_number must use the SEC accession format")
        if not _endpoint_matches_identity(capability, endpoint, cik, accession_number):
            raise ValueError("endpoint must match the declared capability, CIK, and accession")
        if type(filing_date) is not date:
            raise ValueError("filing_date must be a date")
        if period_of_report is not None and type(period_of_report) is not date:
            raise ValueError("period_of_report must be a date or None")
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
            "capability": capability,
            "endpoint": endpoint,
            "source_record_id": source_record_id,
            "cik": cik,
            "provider_symbol": provider_symbol,
            "accession_number": accession_number,
            "form_type": form_type,
            "filing_date": filing_date,
            "period_of_report": period_of_report,
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
        return "SEC_EDGAR"

    @property
    def raw_payload(self) -> bytes:
        return bytes(self._raw_payload)

    @property
    def raw_payload_hash(self) -> str:
        return self._raw_payload_hash


class SECEdgarTransport(Protocol):
    """Injected fixture or separately approved SEC transport."""

    mode: SECEdgarTransportMode

    async def fetch(
        self,
        request: SECEdgarRequest,
        *,
        approval: SECEdgarSourceApproval | None,
    ) -> SECEdgarEvidenceEnvelope: ...


@dataclass(frozen=True)
class SECEdgarProviderEvent:
    capability: SECEdgarCapability
    quality: DataQualityStatus
    reason: str
    correlation_id: str | None
    fact_key: str | None = None
    related_providers: tuple[str, ...] = ("SEC_EDGAR",)


@dataclass(frozen=True)
class SecondaryEvidenceReference:
    """Hash-only reference to non-SEC-EDGAR evidence; payloads remain separate."""

    provider: str
    source_record_id: str
    fact_key: str
    fact_hash: str

    def __post_init__(self) -> None:
        if self.provider == "SEC_EDGAR" or not _SAFE_ID.fullmatch(self.provider):
            raise ValueError("secondary provider must be separate from SEC_EDGAR")
        for name, value in (
            ("source_record_id", self.source_record_id),
            ("fact_key", self.fact_key),
        ):
            if not _SAFE_ID.fullmatch(value):
                raise ValueError(f"{name} must be a sanitized non-empty identifier")
        if not re.fullmatch(r"[0-9a-f]{64}", self.fact_hash):
            raise ValueError("fact_hash must be a lowercase SHA-256 digest")


@dataclass(frozen=True)
class SECEdgarFetchResult:
    evidence: tuple[SECEdgarEvidenceEnvelope, ...]
    secondary_references: tuple[SecondaryEvidenceReference, ...]
    events: tuple[SECEdgarProviderEvent, ...]
    quality: DataQualityStatus
    core_evidence_usable: bool


class SECEdgarProvider:
    """Collect immutable official filing evidence without runtime wiring."""

    def __init__(
        self,
        *,
        transport: SECEdgarTransport,
        approvals: tuple[SECEdgarSourceApproval, ...] = (),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if transport.mode is SECEdgarTransportMode.LIVE and not approvals:
            raise SECEdgarError(
                "live SEC EDGAR transport requires source-specific durable approval"
            )
        self._transport = transport
        self._approvals = approvals
        self._clock = clock
        self._approval_calls: dict[SECEdgarSourceApproval, int] = {}
        self._approval_last_request_at: dict[SECEdgarSourceApproval, datetime] = {}

    def _authorize_live_request(
        self, request: SECEdgarRequest
    ) -> SECEdgarSourceApproval:
        if self._clock is None:
            raise SECEdgarError("live approval validation requires an injected clock")
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise SECEdgarError("live approval clock must be timezone-aware")
        approval = next(
            (
                item
                for item in self._approvals
                if item.capability is request.capability
                and item.endpoint == request.endpoint
                and item.approved_at <= now < item.expires_at
            ),
            None,
        )
        if approval is None:
            raise SECEdgarError("no active exact SEC approval matches the request")
        calls = self._approval_calls.get(approval, 0)
        if calls >= approval.max_calls:
            raise SECEdgarError("live SEC approval call bound is exhausted")
        previous = self._approval_last_request_at.get(approval)
        if previous is not None:
            elapsed_ms = (now - previous).total_seconds() * 1000
            if elapsed_ms < approval.minimum_request_interval_ms:
                raise SECEdgarError("live SEC approval rate bound is not satisfied")
        self._approval_calls[approval] = calls + 1
        self._approval_last_request_at[approval] = now
        return approval

    async def collect(
        self,
        *,
        requests: tuple[SECEdgarRequest, ...],
        as_of_date: datetime,
        secondary_references: tuple[SecondaryEvidenceReference, ...] = (),
    ) -> SECEdgarFetchResult:
        if as_of_date.tzinfo is None or as_of_date.utcoffset() is None:
            raise ValueError("as_of_date must be timezone-aware")
        if not requests:
            return SECEdgarFetchResult(
                evidence=(),
                secondary_references=secondary_references,
                events=(),
                quality=DataQualityStatus.UNAVAILABLE,
                core_evidence_usable=False,
            )
        evidence: list[SECEdgarEvidenceEnvelope] = []
        events: list[SECEdgarProviderEvent] = []
        for request in requests:
            approval = None
            if self._transport.mode is SECEdgarTransportMode.LIVE:
                approval = self._authorize_live_request(request)
            try:
                item = await self._transport.fetch(request, approval=approval)
            except Exception as exc:
                del exc
                events.append(
                    SECEdgarProviderEvent(
                        capability=request.capability,
                        quality=DataQualityStatus.UNAVAILABLE,
                        reason="SEC_FETCH_FAILED",
                        correlation_id=request.correlation_id,
                    )
                )
                continue
            if not isinstance(item, SECEdgarEvidenceEnvelope):
                raise SECEdgarError("SEC transport returned an invalid evidence type")
            if approval is not None and (
                item.terms_id != approval.terms_id
                or item.license_id != approval.license_id
            ):
                raise SECEdgarError(
                    "live SEC evidence terms or license do not match durable approval"
                )
            if (
                item.capability is not request.capability
                or item.endpoint != request.endpoint
                or item.cik != request.cik
                or item.provider_symbol != request.provider_symbol
                or item.accession_number != request.accession_number
                or item.correlation_id != request.correlation_id
            ):
                raise SECEdgarError("transport evidence does not match its request")
            if item.as_of_date != as_of_date:
                raise SECEdgarError("transport evidence has the wrong as-of boundary")
            evidence.append(item)

        official_fact_groups: dict[str, list[SECEdgarEvidenceEnvelope]] = {}
        for item in evidence:
            if item.fact_key is not None and item.fact_hash is not None:
                official_fact_groups.setdefault(item.fact_key, []).append(item)
        for fact_key, facts in official_fact_groups.items():
            if len({item.fact_hash for item in facts}) > 1:
                events.append(
                    SECEdgarProviderEvent(
                        capability=facts[-1].capability,
                        quality=DataQualityStatus.CONFLICT,
                        reason="SEC_OFFICIAL_FACT_CONFLICT",
                        correlation_id=None,
                        fact_key=fact_key,
                    )
                )

        official_by_fact = {
            item.fact_key: item
            for item in evidence
            if item.fact_key is not None and item.fact_hash is not None
        }
        for reference in secondary_references:
            official = official_by_fact.get(reference.fact_key)
            if official is not None and official.fact_hash != reference.fact_hash:
                events.append(
                    SECEdgarProviderEvent(
                        capability=official.capability,
                        quality=DataQualityStatus.CONFLICT,
                        reason="OFFICIAL_SECONDARY_CONFLICT",
                        correlation_id=None,
                        fact_key=reference.fact_key,
                        related_providers=("SEC_EDGAR", reference.provider),
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
        return SECEdgarFetchResult(
            evidence=tuple(evidence),
            secondary_references=secondary_references,
            events=tuple(events),
            quality=quality,
            core_evidence_usable=usable,
        )
