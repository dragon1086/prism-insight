"""Append-only point-in-time corporate-action persistence and reconciliation."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID

from pydantic import AwareDatetime, computed_field, model_validator

from prism_core.data.contracts import (
    ContractModel,
    CorporateAction,
    CorporateActionType,
    DataQualityStatus,
    SecurityId,
)
from prism_core.data.security_master import MergeDisposition
from prism_core.storage.database import transaction


def _utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _utc_text(value: datetime, field_name: str) -> str:
    return _utc(value, field_name).isoformat()


def _decimal_text(value: Decimal | None) -> str | None:
    return format(value.normalize(), "f") if value is not None else None


class CorporateActionEvidence(ContractModel):
    """Provider evidence linked to a curated, stable economic-action identity."""

    action_id: UUID
    effective_at: AwareDatetime
    action: CorporateAction

    @model_validator(mode="after")
    def validate_effective_boundary(self) -> CorporateActionEvidence:
        if self.effective_at.date() != self.action.effective_date:
            raise ValueError(
                "effective_at local date must match the action effective_date"
            )
        return self


class CorporateActionView(ContractModel):
    """A reconciled as-of action; conflicting terms are intentionally withheld."""

    action_id: UUID
    security_id: SecurityId
    action_type: CorporateActionType | None
    effective_at: AwareDatetime | None
    effective_date: date | None
    ratio: Decimal | None
    cash_amount: Decimal | None
    currency: str | None
    quality: DataQualityStatus
    evidence_ids: tuple[str, ...]

    @computed_field
    @property
    def evidence_count(self) -> int:
        return len(self.evidence_ids)


def _evidence_id(evidence: CorporateActionEvidence) -> str:
    action = evidence.action
    payload = {
        "action_id": str(evidence.action_id),
        "security_id": str(action.security_id.value),
        "provider": action.provider,
        "provider_symbol": action.provider_symbol,
        "source_record_id": action.source_record_id,
        "source_hash": action.source_hash,
        "revision": action.revision,
        "action_type": action.action_type.value,
        "effective_date": action.effective_date.isoformat(),
        "effective_at": _utc_text(evidence.effective_at, "effective_at"),
        "ratio": _decimal_text(action.ratio),
        "cash_amount": _decimal_text(action.cash_amount),
        "currency": action.currency,
        "observed_at": _utc_text(action.timing.observed_at, "observed_at"),
        "available_at": _utc_text(action.timing.available_at, "available_at"),
        "quality": action.quality.value,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _quality(rows: list[sqlite3.Row]) -> DataQualityStatus:
    precedence = {
        DataQualityStatus.FRESH: 0,
        DataQualityStatus.PARTIAL: 1,
        DataQualityStatus.STALE: 2,
        DataQualityStatus.UNAVAILABLE: 3,
        DataQualityStatus.CONFLICT: 4,
    }
    return max(
        (DataQualityStatus(row["quality"]) for row in rows),
        key=precedence.__getitem__,
    )


def _signature(row: sqlite3.Row) -> tuple[object, ...]:
    return (
        row["action_type"],
        row["effective_date"],
        row["effective_at"],
        row["ratio"],
        row["cash_amount"],
        row["currency"],
    )


class CorporateActionRepository:
    """Research-database repository for PIT-safe corporate-action evidence."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._connection.row_factory = sqlite3.Row

    def merge(self, evidence: CorporateActionEvidence) -> MergeDisposition:
        action = evidence.action
        evidence_id = _evidence_id(evidence)
        values = (
            evidence_id,
            str(evidence.action_id),
            str(action.security_id.value),
            action.provider,
            action.provider_symbol,
            action.source_record_id,
            action.source_hash,
            action.revision,
            action.action_type.value,
            action.effective_date.isoformat(),
            _utc_text(evidence.effective_at, "effective_at"),
            _decimal_text(action.ratio),
            _decimal_text(action.cash_amount),
            action.currency,
            _utc_text(action.timing.observed_at, "observed_at"),
            _utc_text(action.timing.available_at, "available_at"),
            _utc_text(action.timing.ingested_at, "ingested_at"),
            _utc_text(action.timing.as_of_date, "as_of_date"),
            action.quality.value,
        )
        try:
            with transaction(self._connection):
                cursor = self._connection.execute(
                    """
                    INSERT OR IGNORE INTO corporate_action_events (
                        action_evidence_id, action_id, security_id, provider,
                        provider_symbol, source_record_id, source_hash, revision,
                        action_type, effective_date, effective_at, ratio,
                        cash_amount, currency, observed_at, available_at,
                        ingested_at, as_of_date, quality
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("corporate-action evidence violates integrity") from exc
        return (
            MergeDisposition.INSERTED
            if cursor.rowcount == 1
            else MergeDisposition.DUPLICATE
        )

    def actions_as_of(
        self,
        security_id: SecurityId,
        *,
        query_as_of: datetime,
    ) -> tuple[CorporateActionView, ...]:
        query_text = _utc_text(query_as_of, "query_as_of")
        available_rows = list(
            self._connection.execute(
                """
                SELECT * FROM corporate_action_events
                WHERE security_id = ? AND available_at <= ?
                ORDER BY available_at, revision, action_evidence_id
                """,
                (str(security_id.value), query_text),
            )
        )

        by_source: dict[tuple[str, str], list[sqlite3.Row]] = defaultdict(list)
        for row in available_rows:
            by_source[(row["provider"], row["source_record_id"])].append(row)

        current_rows: list[sqlite3.Row] = []
        for rows in by_source.values():
            highest_revision = max(row["revision"] for row in rows)
            current_rows.extend(
                row for row in rows if row["revision"] == highest_revision
            )

        by_action: dict[str, list[sqlite3.Row]] = defaultdict(list)
        for row in current_rows:
            by_action[row["action_id"]].append(row)

        views: list[CorporateActionView] = []
        for action_id in sorted(by_action):
            rows = sorted(
                by_action[action_id], key=lambda row: row["action_evidence_id"]
            )
            effective_rows = [
                row for row in rows if row["effective_at"] <= query_text
            ]
            if not effective_rows:
                continue
            signatures = {_signature(row) for row in rows}
            if len(signatures) != 1:
                views.append(
                    CorporateActionView(
                        action_id=UUID(action_id),
                        security_id=security_id,
                        action_type=None,
                        effective_at=None,
                        effective_date=None,
                        ratio=None,
                        cash_amount=None,
                        currency=None,
                        quality=DataQualityStatus.CONFLICT,
                        evidence_ids=tuple(
                            row["action_evidence_id"] for row in rows
                        ),
                    )
                )
                continue

            row = effective_rows[0]
            views.append(
                CorporateActionView(
                    action_id=UUID(action_id),
                    security_id=security_id,
                    action_type=CorporateActionType(row["action_type"]),
                    effective_at=datetime.fromisoformat(row["effective_at"]),
                    effective_date=date.fromisoformat(row["effective_date"]),
                    ratio=Decimal(row["ratio"]) if row["ratio"] is not None else None,
                    cash_amount=(
                        Decimal(row["cash_amount"])
                        if row["cash_amount"] is not None
                        else None
                    ),
                    currency=row["currency"],
                    quality=_quality(rows),
                    evidence_ids=tuple(row["action_evidence_id"] for row in rows),
                )
            )
        return tuple(views)
