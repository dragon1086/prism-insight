"""Point-in-time DART filing adapter with an injected, read-only transport.

The module does not assume that DART credentials or terms approval exist.  Live
HTTP capability must be supplied explicitly; ``UnavailableDARTAdapter`` is the
honest default when that capability has not been established.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Mapping, Protocol
from uuid import NAMESPACE_URL, uuid5

from prism_core.data.contracts import (
    DataQualityStatus,
    EvidenceItem,
    FundamentalObservation,
    ObservationTime,
    SecurityId,
)


_DART_VIEWER = "https://dart.fss.or.kr/dsaf001/main.do"
_RECEIPT = re.compile(r"^[0-9]{14}$")
_STOCK_CODE = re.compile(r"^[0-9]{6}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class DARTTransportError(RuntimeError):
    """Sanitized transport failure raised by an approved DART transport."""


@dataclass(frozen=True)
class DARTFilingRecord:
    """One official filing fact whose availability is the acceptance timestamp."""

    stock_code: str
    receipt_no: str
    report_name: str
    metric: str
    period_start: date
    period_end: date
    value: Decimal
    unit: str
    accepted_at: datetime
    ingested_at: datetime
    source_hash: str
    quality: DataQualityStatus = DataQualityStatus.FRESH
    revision: int = 0
    risk_flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not _STOCK_CODE.fullmatch(self.stock_code):
            raise ValueError("stock_code must be a six-digit KR provider symbol")
        if not _RECEIPT.fullmatch(self.receipt_no):
            raise ValueError("receipt_no must be a DART receipt identity")
        if not self.report_name.strip() or not self.metric.strip() or not self.unit.strip():
            raise ValueError("filing labels must be non-empty")
        if self.period_start > self.period_end:
            raise ValueError("filing period is reversed")
        if not isinstance(self.value, Decimal) or not self.value.is_finite():
            raise ValueError("filing value must be a finite Decimal")
        for name, value in (
            ("accepted_at", self.accepted_at),
            ("ingested_at", self.ingested_at),
        ):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware")
        if self.accepted_at > self.ingested_at:
            raise ValueError("acceptance cannot follow ingestion")
        if not _SHA256.fullmatch(self.source_hash):
            raise ValueError("source_hash must be a lowercase SHA-256 digest")
        if type(self.revision) is not int or self.revision < 0:
            raise ValueError("revision must be non-negative")
        if not isinstance(self.quality, DataQualityStatus):
            raise TypeError("quality must be DataQualityStatus")
        if any(not isinstance(item, str) or not item.strip() for item in self.risk_flags):
            raise ValueError("risk flags must be non-empty strings")


class DARTTransport(Protocol):
    """Approved transport port; account and broker operations are absent."""

    async def fetch_filings(
        self, *, stock_code: str, as_of: datetime
    ) -> tuple[DARTFilingRecord, ...]: ...


@dataclass(frozen=True)
class DARTFetchResult:
    fundamentals: tuple[FundamentalObservation, ...]
    evidence_items: tuple[EvidenceItem, ...]
    quality: DataQualityStatus
    risk_flags: tuple[str, ...]
    issues: tuple[str, ...]
    call_evidence: tuple[Mapping[str, str], ...]


class DARTProvider:
    """Normalize injected official records without backdating filing availability."""

    def __init__(self, *, transport: DARTTransport, max_attempts: int = 2) -> None:
        if type(max_attempts) is not int or max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self._transport = transport
        self._max_attempts = max_attempts

    async def fetch(
        self, *, stock_code: str, security_id: SecurityId, as_of: datetime
    ) -> DARTFetchResult:
        if not _STOCK_CODE.fullmatch(stock_code):
            raise ValueError("stock_code must be a six-digit KR provider symbol")
        if not isinstance(security_id, SecurityId):
            raise TypeError("security_id must be SecurityId")
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        records: tuple[DARTFilingRecord, ...] | None = None
        for _attempt in range(1, self._max_attempts + 1):
            try:
                records = await self._transport.fetch_filings(
                    stock_code=stock_code, as_of=as_of
                )
                break
            except Exception:
                records = None
        if records is None:
            return DARTFetchResult(
                fundamentals=(),
                evidence_items=(),
                quality=DataQualityStatus.UNAVAILABLE,
                risk_flags=(),
                issues=("DART_FETCH_FAILED",),
                call_evidence=(),
            )
        selected = tuple(
            record
            for record in records
            if isinstance(record, DARTFilingRecord)
            and record.stock_code == stock_code
            and record.accepted_at <= as_of
        )
        if not selected:
            return DARTFetchResult(
                fundamentals=(),
                evidence_items=(),
                quality=DataQualityStatus.UNAVAILABLE,
                risk_flags=(),
                issues=("NO_PIT_AVAILABLE_DART_FUNDAMENTALS",),
                call_evidence=(
                    {"provider": "DART", "status": "SUCCESS", "schema": "NO_PIT_RECORDS"},
                ),
            )
        fundamentals = tuple(self._fundamental(item, security_id, as_of) for item in selected)
        evidence = tuple(self._evidence(item, security_id, as_of) for item in selected)
        quality = next(
            (
                candidate
                for candidate in (
                    DataQualityStatus.CONFLICT,
                    DataQualityStatus.UNAVAILABLE,
                    DataQualityStatus.STALE,
                    DataQualityStatus.PARTIAL,
                )
                if candidate in {item.quality for item in selected}
            ),
            DataQualityStatus.FRESH,
        )
        return DARTFetchResult(
            fundamentals=fundamentals,
            evidence_items=evidence,
            quality=quality,
            risk_flags=tuple(sorted({flag for item in selected for flag in item.risk_flags})),
            issues=(),
            call_evidence=(
                {"provider": "DART", "status": "SUCCESS", "schema": "DART_FILING_V1"},
            ),
        )

    @staticmethod
    def _timing(record: DARTFilingRecord, as_of: datetime) -> ObservationTime:
        observed = min(
            datetime.combine(record.period_end, time.min, tzinfo=timezone.utc),
            record.accepted_at,
        )
        return ObservationTime(
            observed_at=observed,
            available_at=record.accepted_at,
            ingested_at=record.ingested_at,
            as_of_date=as_of,
        )

    @classmethod
    def _fundamental(
        cls, record: DARTFilingRecord, security_id: SecurityId, as_of: datetime
    ) -> FundamentalObservation:
        return FundamentalObservation(
            security_id=security_id,
            provider="DART",
            provider_symbol=record.stock_code,
            source_record_id=record.receipt_no,
            source_hash=record.source_hash,
            revision=record.revision,
            timing=cls._timing(record, as_of),
            quality=record.quality,
            metric=record.metric,
            period_start=record.period_start,
            period_end=record.period_end,
            value=record.value,
            unit=record.unit,
        )

    @classmethod
    def _evidence(
        cls, record: DARTFilingRecord, security_id: SecurityId, as_of: datetime
    ) -> EvidenceItem:
        identity = f"dart:{record.receipt_no}:{record.source_hash}"
        return EvidenceItem.model_validate(
            {
                "security_id": security_id,
                "provider": "DART",
                "provider_symbol": record.stock_code,
                "source_record_id": record.receipt_no,
                "source_hash": record.source_hash,
                "revision": record.revision,
                "timing": cls._timing(record, as_of),
                "quality": record.quality,
                "evidence_id": uuid5(NAMESPACE_URL, identity),
                "kind": "company_filing",
                "title": record.report_name,
                "source_url": f"{_DART_VIEWER}?rcpNo={record.receipt_no}",
                "content_hash": record.source_hash,
            }
        )


class UnavailableDARTAdapter:
    """Explicit adapter used when DART terms/credential capability is not approved."""

    async def fetch(
        self, *, stock_code: str, security_id: SecurityId, as_of: datetime
    ) -> DARTFetchResult:
        del stock_code, security_id, as_of
        return DARTFetchResult(
            fundamentals=(),
            evidence_items=(),
            quality=DataQualityStatus.UNAVAILABLE,
            risk_flags=(),
            issues=("DART_CAPABILITY_UNAVAILABLE",),
            call_evidence=(),
        )
