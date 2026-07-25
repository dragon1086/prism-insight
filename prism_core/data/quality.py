"""Deterministic, fail-closed policy for point-in-time data quality."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Mapping, Protocol
from uuid import UUID

from prism_core.data.contracts import DataQualityStatus


PROPOSAL_CORE_FIELDS = frozenset({"price", "regime", "calendar", "evidence"})
logger = logging.getLogger(__name__)


class QualityDisposition(str, Enum):
    """Policy outcome; deliberately separate from observed quality status."""

    ACCEPT = "ACCEPT"
    REPORT_ONLY = "REPORT_ONLY"
    REJECT = "REJECT"


@dataclass(frozen=True)
class QualityDecision:
    """Auditable policy result for one quality-gate evaluation."""

    disposition: QualityDisposition
    reasons: tuple[str, ...]
    missing_fields: tuple[str, ...]
    stale_fields: tuple[str, ...]


@dataclass(frozen=True)
class QualitySkipRecord:
    """Immutable record handed to append-only audit storage when work is skipped."""

    request_id: str
    snapshot_id: UUID
    evaluated_at: datetime
    disposition: QualityDisposition
    reasons: tuple[str, ...]
    missing_fields: tuple[str, ...]
    stale_fields: tuple[str, ...]
    skipped_action: str = "NEW_PROPOSAL"

    def __post_init__(self) -> None:
        if not self.request_id.strip():
            raise ValueError("request_id must not be empty")
        if self.evaluated_at.tzinfo is None:
            raise ValueError("evaluated_at must be timezone-aware")
        if self.disposition is QualityDisposition.ACCEPT:
            raise ValueError("ACCEPT decisions do not create skip records")
        if not self.reasons:
            raise ValueError("skip records require at least one reason")
        if self.skipped_action != "NEW_PROPOSAL":
            raise ValueError("the quality gate may only skip NEW_PROPOSAL")


class QualitySkipRecorder(Protocol):
    """Append-only persistence boundary supplied by a later storage/application slice."""

    def append(self, record: QualitySkipRecord) -> None: ...


class DataQualityGate:
    """Classify named observations without silently relaxing core requirements."""

    def __init__(
        self,
        *,
        core_fields: set[str] | frozenset[str] | None = None,
        report_only_fields: set[str] | frozenset[str] = frozenset(),
    ) -> None:
        self._core_fields = (
            PROPOSAL_CORE_FIELDS if core_fields is None else frozenset(core_fields)
        )
        self._report_only_fields = frozenset(report_only_fields)
        if not self._core_fields:
            raise ValueError("at least one core field is required")
        overlap = self._core_fields & self._report_only_fields
        if overlap:
            raise ValueError(
                "fields cannot be both core and report-only: "
                + ", ".join(sorted(overlap))
            )

    def evaluate(
        self, field_quality: Mapping[str, DataQualityStatus]
    ) -> QualityDecision:
        """Return a policy result; malformed input becomes REJECT, never ACCEPT."""
        try:
            return self._evaluate(field_quality)
        except Exception as exc:  # noqa: BLE001 - fail closed at the policy boundary
            logger.warning(
                "quality gate rejected malformed input (%s)", type(exc).__name__
            )
            return QualityDecision(
                disposition=QualityDisposition.REJECT,
                reasons=(f"quality_gate_error:{type(exc).__name__}",),
                missing_fields=(),
                stale_fields=(),
            )

    def _evaluate(
        self, field_quality: Mapping[str, DataQualityStatus]
    ) -> QualityDecision:
        if not isinstance(field_quality, Mapping):
            raise TypeError("field_quality must be a mapping")
        if any(
            not isinstance(field, str) or not field.strip()
            for field in field_quality
        ):
            raise TypeError("quality field names must be non-empty strings")
        if any(
            not isinstance(status, DataQualityStatus)
            for status in field_quality.values()
        ):
            raise TypeError("quality values must be DataQualityStatus members")

        absent = set(self._core_fields - field_quality.keys())
        reasons = [f"missing_core:{field}" for field in sorted(absent)]
        stale: set[str] = set()
        missing = set(absent)
        for field in sorted(self._core_fields & field_quality.keys()):
            status = field_quality[field]
            if status is DataQualityStatus.FRESH:
                continue
            reasons.append(f"{status.value.lower()}_core:{field}")
            if status is DataQualityStatus.STALE:
                stale.add(field)
            elif status in {
                DataQualityStatus.PARTIAL,
                DataQualityStatus.UNAVAILABLE,
            }:
                missing.add(field)

        report_only = False
        for field in sorted(self._report_only_fields):
            status = field_quality.get(field)
            if status is DataQualityStatus.FRESH:
                continue
            if status is None or status is DataQualityStatus.PARTIAL:
                report_only = True
                missing.add(field)
                label = "missing" if status is None else "partial"
                reasons.append(f"{label}_report_only:{field}")
                continue
            reasons.append(f"{status.value.lower()}_report_only:{field}")
            if status is DataQualityStatus.STALE:
                stale.add(field)

        classified = self._core_fields | self._report_only_fields
        for field in sorted(field_quality.keys() - classified):
            status = field_quality[field]
            if status is DataQualityStatus.FRESH:
                continue
            reasons.append(f"{status.value.lower()}_unclassified:{field}")

        reject = any(
            not reason.startswith(("missing_report_only:", "partial_report_only:"))
            for reason in reasons
        )
        if reject:
            return QualityDecision(
                disposition=QualityDisposition.REJECT,
                reasons=tuple(reasons),
                missing_fields=tuple(sorted(missing)),
                stale_fields=tuple(sorted(stale)),
            )
        if report_only:
            return QualityDecision(
                disposition=QualityDisposition.REPORT_ONLY,
                reasons=tuple(reasons),
                missing_fields=tuple(sorted(missing)),
                stale_fields=tuple(sorted(stale)),
            )
        return QualityDecision(
            disposition=QualityDisposition.ACCEPT,
            reasons=(),
            missing_fields=(),
            stale_fields=(),
        )

    def evaluate_and_record(
        self,
        field_quality: Mapping[str, DataQualityStatus],
        *,
        recorder: QualitySkipRecorder,
        request_id: str,
        snapshot_id: UUID,
        evaluated_at: datetime,
    ) -> QualityDecision:
        """Evaluate and append every non-ACCEPT result to the supplied audit sink.

        Recorder failures intentionally propagate so callers cannot continue as if
        a required skip were durably audited.
        """
        decision = self.evaluate(field_quality)
        if decision.disposition is not QualityDisposition.ACCEPT:
            recorder.append(
                QualitySkipRecord(
                    request_id=request_id,
                    snapshot_id=snapshot_id,
                    evaluated_at=evaluated_at,
                    disposition=decision.disposition,
                    reasons=decision.reasons,
                    missing_fields=decision.missing_fields,
                    stale_fields=decision.stale_fields,
                )
            )
        return decision
