"""Append-only point-in-time security identity persistence."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from enum import Enum
from uuid import UUID

from pydantic import Field

from prism_core.data.contracts import (
    ContractModel,
    DataQualityStatus,
    NonEmptyStr,
    ObservationTime,
    Sha256,
    SecurityId,
    SymbolMapping,
)
from prism_core.storage.database import transaction


class MergeDisposition(str, Enum):
    INSERTED = "INSERTED"
    DUPLICATE = "DUPLICATE"


class ListingStatus(str, Enum):
    LISTED = "LISTED"
    DELISTED = "DELISTED"


class SecurityAliasEvidence(ContractModel):
    """One provider assertion about a symbol validity interval."""

    mapping: SymbolMapping
    source_record_id: str = Field(min_length=1)
    revision: int = Field(ge=0)
    quality: DataQualityStatus


class SecurityListingEvidence(ContractModel):
    """One provider assertion about listing state from an effective instant."""

    security_id: SecurityId
    provider: NonEmptyStr
    provider_symbol: NonEmptyStr
    market: NonEmptyStr
    status: ListingStatus
    effective_at: datetime
    source_record_id: NonEmptyStr
    source_hash: Sha256
    revision: int = Field(ge=0)
    timing: ObservationTime
    quality: DataQualityStatus

    def model_post_init(self, __context: object) -> None:
        _require_aware(self.effective_at, "effective_at")


class SecurityResolution(ContractModel):
    """Deterministic result of a point-in-time provider-symbol lookup."""

    security_id: SecurityId | None
    listing_status: ListingStatus | None = None
    quality: DataQualityStatus
    evidence_ids: tuple[str, ...]


def _require_aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _utc_text(value: datetime, field_name: str) -> str:
    return _require_aware(value, field_name).isoformat()


def _alias_evidence_id(evidence: SecurityAliasEvidence) -> str:
    mapping = evidence.mapping
    payload = {
        "security_id": str(mapping.security_id.value),
        "provider": mapping.provider,
        "provider_symbol": mapping.provider_symbol,
        "market": mapping.market,
        "valid_from": _utc_text(mapping.valid_from, "valid_from"),
        "valid_to": (
            _utc_text(mapping.valid_to, "valid_to")
            if mapping.valid_to is not None
            else None
        ),
        "source_record_id": evidence.source_record_id,
        "source_hash": mapping.source_hash,
        "revision": evidence.revision,
        "observed_at": _utc_text(mapping.timing.observed_at, "observed_at"),
        "available_at": _utc_text(mapping.timing.available_at, "available_at"),
        "quality": evidence.quality.value,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _listing_evidence_id(evidence: SecurityListingEvidence) -> str:
    payload = {
        "security_id": str(evidence.security_id.value),
        "provider": evidence.provider,
        "provider_symbol": evidence.provider_symbol,
        "market": evidence.market,
        "status": evidence.status.value,
        "effective_at": _utc_text(evidence.effective_at, "effective_at"),
        "source_record_id": evidence.source_record_id,
        "source_hash": evidence.source_hash,
        "revision": evidence.revision,
        "observed_at": _utc_text(evidence.timing.observed_at, "observed_at"),
        "available_at": _utc_text(evidence.timing.available_at, "available_at"),
        "quality": evidence.quality.value,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _combined_quality(rows: list[sqlite3.Row]) -> DataQualityStatus:
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


class SecurityMasterRepository:
    """Research-database repository for stable identities and alias evidence."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._connection.row_factory = sqlite3.Row

    def _require_registered_market(self, security_id: SecurityId, market: str) -> None:
        row = self._connection.execute(
            "SELECT market FROM securities WHERE security_id = ?",
            (str(security_id.value),),
        ).fetchone()
        if row is None:
            raise ValueError("security_id is not registered")
        if row["market"] != market:
            raise ValueError("evidence market does not match registered security market")

    def register_security(
        self,
        security_id: SecurityId,
        *,
        market: str,
        created_at: datetime,
    ) -> MergeDisposition:
        if not market:
            raise ValueError("market must not be empty")
        created_at_text = _utc_text(created_at, "created_at")
        existing = self._connection.execute(
            "SELECT market, created_at FROM securities WHERE security_id = ?",
            (str(security_id.value),),
        ).fetchone()
        if existing is not None:
            if (existing["market"], existing["created_at"]) != (
                market,
                created_at_text,
            ):
                raise ValueError("security_id is already registered with different data")
            return MergeDisposition.DUPLICATE
        with transaction(self._connection):
            self._connection.execute(
                "INSERT INTO securities (security_id, market, created_at) VALUES (?, ?, ?)",
                (str(security_id.value), market, created_at_text),
            )
        return MergeDisposition.INSERTED

    def merge_alias(self, evidence: SecurityAliasEvidence) -> MergeDisposition:
        mapping = evidence.mapping
        self._require_registered_market(mapping.security_id, mapping.market)
        evidence_id = _alias_evidence_id(evidence)
        values = (
            evidence_id,
            str(mapping.security_id.value),
            mapping.market,
            mapping.provider,
            mapping.provider_symbol,
            _utc_text(mapping.valid_from, "valid_from"),
            _utc_text(mapping.valid_to, "valid_to") if mapping.valid_to else None,
            evidence.source_record_id,
            mapping.source_hash,
            evidence.revision,
            _utc_text(mapping.timing.observed_at, "observed_at"),
            _utc_text(mapping.timing.available_at, "available_at"),
            _utc_text(mapping.timing.ingested_at, "ingested_at"),
            _utc_text(mapping.timing.as_of_date, "as_of_date"),
            evidence.quality.value,
        )
        try:
            with transaction(self._connection):
                cursor = self._connection.execute(
                    """
                    INSERT OR IGNORE INTO security_alias_events (
                        alias_evidence_id, security_id, market, provider,
                        provider_symbol, valid_from, valid_to, source_record_id,
                        source_hash, revision, observed_at, available_at,
                        ingested_at, as_of_date, quality
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("alias evidence violates security-master integrity") from exc
        return (
            MergeDisposition.INSERTED
            if cursor.rowcount == 1
            else MergeDisposition.DUPLICATE
        )

    def merge_listing_status(
        self, evidence: SecurityListingEvidence
    ) -> MergeDisposition:
        self._require_registered_market(evidence.security_id, evidence.market)
        evidence_id = _listing_evidence_id(evidence)
        values = (
            evidence_id,
            str(evidence.security_id.value),
            evidence.market,
            evidence.provider,
            evidence.provider_symbol,
            evidence.status.value,
            _utc_text(evidence.effective_at, "effective_at"),
            evidence.source_record_id,
            evidence.source_hash,
            evidence.revision,
            _utc_text(evidence.timing.observed_at, "observed_at"),
            _utc_text(evidence.timing.available_at, "available_at"),
            _utc_text(evidence.timing.ingested_at, "ingested_at"),
            _utc_text(evidence.timing.as_of_date, "as_of_date"),
            evidence.quality.value,
        )
        try:
            with transaction(self._connection):
                cursor = self._connection.execute(
                    """
                    INSERT OR IGNORE INTO security_listing_events (
                        listing_evidence_id, security_id, market, provider,
                        provider_symbol, status, effective_at, source_record_id,
                        source_hash, revision, observed_at, available_at,
                        ingested_at, as_of_date, quality
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("listing evidence violates security-master integrity") from exc
        return (
            MergeDisposition.INSERTED
            if cursor.rowcount == 1
            else MergeDisposition.DUPLICATE
        )

    def resolve_symbol(
        self,
        provider: str,
        provider_symbol: str,
        *,
        query_as_of: datetime,
    ) -> SecurityResolution:
        if not provider or not provider_symbol:
            raise ValueError("provider and provider_symbol must not be empty")
        query_text = _utc_text(query_as_of, "query_as_of")
        available_rows = list(
            self._connection.execute(
                """
                SELECT * FROM security_alias_events
                WHERE provider = ? AND available_at <= ?
                ORDER BY available_at, revision, alias_evidence_id
                """,
                (provider, query_text),
            )
        )
        by_source: dict[tuple[str, str], list[sqlite3.Row]] = {}
        for row in available_rows:
            by_source.setdefault(
                (row["provider"], row["source_record_id"]), []
            ).append(row)
        current_assertions: list[sqlite3.Row] = []
        for source_rows in by_source.values():
            highest_revision = max(row["revision"] for row in source_rows)
            current_assertions.extend(
                row for row in source_rows if row["revision"] == highest_revision
            )
        rows = [
            row
            for row in current_assertions
            if row["provider_symbol"] == provider_symbol
            and row["valid_from"] <= query_text
            and (row["valid_to"] is None or row["valid_to"] > query_text)
        ]
        rows.sort(key=lambda row: row["alias_evidence_id"])
        if not rows:
            return SecurityResolution(
                security_id=None,
                quality=DataQualityStatus.UNAVAILABLE,
                evidence_ids=(),
            )

        security_ids = {row["security_id"] for row in rows}
        quality = _combined_quality(rows)
        if len(security_ids) != 1:
            quality = DataQualityStatus.CONFLICT
            security_id = None
        else:
            security_id = SecurityId(value=UUID(next(iter(security_ids))))

        listing_status = None
        listing_rows: list[sqlite3.Row] = []
        if security_id is not None:
            candidates = list(
                self._connection.execute(
                    """
                    SELECT * FROM security_listing_events
                    WHERE security_id = ? AND available_at <= ?
                    ORDER BY effective_at, available_at, revision, listing_evidence_id
                    """,
                    (str(security_id.value), query_text),
                )
            )
            by_listing_source: dict[tuple[str, str], list[sqlite3.Row]] = {}
            for candidate in candidates:
                key = (candidate["provider"], candidate["source_record_id"])
                by_listing_source.setdefault(key, []).append(candidate)
            current_listing_rows: list[sqlite3.Row] = []
            for source_rows in by_listing_source.values():
                highest_revision = max(row["revision"] for row in source_rows)
                current_listing_rows.extend(
                    row for row in source_rows if row["revision"] == highest_revision
                )
            current_listing_rows = [
                row
                for row in current_listing_rows
                if row["effective_at"] <= query_text
            ]
            if current_listing_rows:
                by_provider: dict[str, list[sqlite3.Row]] = {}
                for row in current_listing_rows:
                    by_provider.setdefault(row["provider"], []).append(row)
                for provider_rows in by_provider.values():
                    latest_effective = max(
                        row["effective_at"] for row in provider_rows
                    )
                    listing_rows.extend(
                        row
                        for row in provider_rows
                        if row["effective_at"] == latest_effective
                    )
                listing_rows.sort(key=lambda row: row["listing_evidence_id"])
                statuses = {row["status"] for row in listing_rows}
                if len(statuses) == 1:
                    listing_status = ListingStatus(next(iter(statuses)))
                else:
                    quality = DataQualityStatus.CONFLICT
                if quality is not DataQualityStatus.CONFLICT:
                    quality = _combined_quality([*rows, *listing_rows])
        return SecurityResolution(
            security_id=security_id,
            listing_status=listing_status,
            quality=quality,
            evidence_ids=(
                *(row["alias_evidence_id"] for row in rows),
                *(row["listing_evidence_id"] for row in listing_rows),
            ),
        )
