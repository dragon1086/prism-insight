"""Permission-gated import of bounded sanitized StockEasy UI/export snapshots."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, Mapping
from uuid import NAMESPACE_URL, uuid5

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, model_validator

from prism_core.data.contracts import ContractModel, DataQualityStatus, ObservationTime, SecurityId
from prism_core.data.contracts.leadership_supplement import (
    LeadershipCandidateNomination,
    LeadershipCaptureMethod,
    LeadershipObservation,
    LeadershipObservationKind,
    LeadershipScope,
    LeadershipSection,
    LeadershipSupplementSnapshot,
)


_PROHIBITED_KEY_PARTS = frozenset(
    {
        "account",
        "authorization",
        "balance",
        "cookie",
        "credential",
        "endpoint",
        "internal_api",
        "password",
        "payment",
        "profile",
        "secret",
        "session",
        "token",
    }
)
_MAX_IMAGE_BYTES = 10 * 1024 * 1024
_MAX_RAW_DEPTH = 16
_MAX_RAW_NODES = 20_000
_MAX_COLLECTION_ITEMS = 5_000
_PROHIBITED_VALUE_MARKERS = (
    "authorization:",
    "bearer ",
    "cookie:",
    "token=",
    "api_key=",
    "password=",
    "credential=",
    "account=",
    "payment=",
    "profile=",
    "http://",
    "https://",
    "/api/",
)
_JWT_PATTERN = re.compile(r"\b[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\b")


class StockEasyImportOutcome(str, Enum):
    IMPORTED = "IMPORTED"
    UNAVAILABLE = "UNAVAILABLE"
    REJECTED = "REJECTED"


class StockEasyPermissionRecord(ContractModel):
    record_id: Annotated[str, Field(min_length=1)]
    terms_verified: bool
    permission_verified: bool
    verified_at: AwareDatetime
    terms_record_id: Annotated[str, Field(min_length=1)] | None
    permission_basis_id: Annotated[str, Field(min_length=1)] | None
    approved_source_scope_ids: tuple[Annotated[str, Field(min_length=1)], ...]
    approved_sections: tuple[LeadershipSection, ...]
    approved_methods: tuple[LeadershipCaptureMethod, ...]

    @model_validator(mode="after")
    def validate_permission_evidence(self) -> "StockEasyPermissionRecord":
        scoped = bool(
            self.terms_record_id
            or self.permission_basis_id
            or self.approved_source_scope_ids
            or self.approved_sections
            or self.approved_methods
        )
        if self.approved:
            if not all(
                (
                    self.terms_record_id,
                    self.permission_basis_id,
                    self.approved_source_scope_ids,
                    self.approved_sections,
                    self.approved_methods,
                )
            ):
                raise ValueError("approved import requires exact permission evidence and scope")
        elif scoped:
            raise ValueError("unverified permission cannot claim an approved scope")
        return self

    @property
    def approved(self) -> bool:
        return self.terms_verified and self.permission_verified

    @classmethod
    def unavailable(
        cls, *, record_id: str, verified_at: datetime
    ) -> "StockEasyPermissionRecord":
        """Record an honest capability block without inventing approved scope."""

        return cls(
            record_id=record_id,
            terms_verified=False,
            permission_verified=False,
            verified_at=verified_at,
            terms_record_id=None,
            permission_basis_id=None,
            approved_source_scope_ids=(),
            approved_sections=(),
            approved_methods=(),
        )


class StockEasyImportResult(ContractModel):
    outcome: StockEasyImportOutcome
    snapshot: LeadershipSupplementSnapshot | None = None
    error_code: str | None = None
    temporary_image_deleted: bool

    @model_validator(mode="after")
    def validate_result(self) -> "StockEasyImportResult":
        if (self.outcome is StockEasyImportOutcome.IMPORTED) != (self.snapshot is not None):
            raise ValueError("only imported results may contain a snapshot")
        if self.outcome is not StockEasyImportOutcome.IMPORTED and not self.error_code:
            raise ValueError("non-imported results require a sanitized error code")
        return self


class _WireObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: LeadershipObservationKind
    scope: LeadershipScope
    provider_symbol: str | None = Field(default=None, pattern=r"^\d{6}$")
    group_id: str | None = Field(default=None, min_length=1)
    value: str | int | float
    unit: str = Field(min_length=1)


class _WireCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_symbol: str = Field(pattern=r"^\d{6}$")
    display_name: str = Field(min_length=1)
    trigger_id: str = Field(min_length=1)
    observation_indexes: tuple[int, ...] = Field(min_length=1)


class _WireSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    generic_section: LeadershipSection
    source_scope_id: str = Field(min_length=1)
    capture_method: LeadershipCaptureMethod
    captured_at: AwareDatetime
    exported_at: AwareDatetime
    as_of: AwareDatetime
    source_snapshot_id: str = Field(min_length=1)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    image_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    observations: tuple[_WireObservation, ...] = Field(max_length=_MAX_COLLECTION_ITEMS)
    candidates: tuple[_WireCandidate, ...] = Field(max_length=_MAX_COLLECTION_ITEMS)

    @model_validator(mode="after")
    def validate_wire(self) -> "_WireSnapshot":
        if self.schema_version != "stockeasy_sanitized_snapshot_v1":
            raise ValueError("unsupported snapshot schema")
        if not (self.captured_at <= self.exported_at <= self.as_of):
            raise ValueError("snapshot clocks violate the point-in-time boundary")
        if any(
            index < 0 or index >= len(self.observations)
            for candidate in self.candidates
            for index in candidate.observation_indexes
        ):
            raise ValueError("candidate observation index is out of bounds")
        for candidate in self.candidates:
            for index in candidate.observation_indexes:
                observation = self.observations[index]
                if (
                    observation.scope is LeadershipScope.SECURITY
                    and observation.provider_symbol != candidate.provider_symbol
                ):
                    raise ValueError("candidate evidence belongs to another security")
        return self


class StockEasySnapshotImporter:
    """No network/browser capability: imports only an already-sanitized snapshot."""

    def __init__(self, *, permission: StockEasyPermissionRecord, max_age: timedelta) -> None:
        if max_age <= timedelta(0):
            raise ValueError("max_age must be positive")
        self._permission = permission
        self._max_age = max_age

    def unavailable(self, error_code: str = "APPROVED_UI_EXPORT_NOT_CONFIRMED") -> StockEasyImportResult:
        return StockEasyImportResult(
            outcome=StockEasyImportOutcome.UNAVAILABLE,
            error_code=error_code,
            temporary_image_deleted=False,
        )

    def import_snapshot(
        self,
        payload: Mapping[str, object],
        *,
        imported_at: datetime,
        temporary_image_path: Path | None = None,
    ) -> StockEasyImportResult:
        deleted = False
        try:
            if not self._permission.approved:
                deleted = _delete_temporary_image(temporary_image_path)
                return StockEasyImportResult(
                    outcome=StockEasyImportOutcome.UNAVAILABLE,
                    error_code="APPROVED_UI_EXPORT_NOT_CONFIRMED",
                    temporary_image_deleted=deleted,
                )
            if _contains_prohibited_key(payload):
                deleted = _delete_temporary_image(temporary_image_path)
                return StockEasyImportResult(
                    outcome=StockEasyImportOutcome.REJECTED,
                    error_code="PROHIBITED_FIELD",
                    temporary_image_deleted=deleted,
                )
            wire = _WireSnapshot.model_validate(payload)
            if (
                wire.generic_section not in self._permission.approved_sections
                or wire.capture_method not in self._permission.approved_methods
                or wire.source_scope_id not in self._permission.approved_source_scope_ids
            ):
                deleted = _delete_temporary_image(temporary_image_path)
                return StockEasyImportResult(
                    outcome=StockEasyImportOutcome.REJECTED,
                    error_code="SCOPE_NOT_APPROVED",
                    temporary_image_deleted=deleted,
                )
            if imported_at.tzinfo is None or imported_at.utcoffset() is None or wire.as_of > imported_at:
                deleted = _delete_temporary_image(temporary_image_path)
                return StockEasyImportResult(
                    outcome=StockEasyImportOutcome.REJECTED,
                    error_code="INVALID_IMPORT_CLOCK",
                    temporary_image_deleted=deleted,
                )
            if wire.content_sha256 != canonical_stockeasy_content_hash(payload):
                deleted = _delete_temporary_image(temporary_image_path)
                return StockEasyImportResult(
                    outcome=StockEasyImportOutcome.REJECTED,
                    error_code="CONTENT_HASH_MISMATCH",
                    temporary_image_deleted=deleted,
                )
            if temporary_image_path is not None:
                image_hash = _hash_bounded_file(temporary_image_path)
                if wire.image_sha256 != image_hash:
                    deleted = _delete_temporary_image(temporary_image_path)
                    return StockEasyImportResult(
                        outcome=StockEasyImportOutcome.REJECTED,
                        error_code="IMAGE_HASH_MISMATCH",
                        temporary_image_deleted=deleted,
                    )
            elif wire.image_sha256 is not None:
                return StockEasyImportResult(
                    outcome=StockEasyImportOutcome.REJECTED,
                    error_code="IMAGE_NOT_PROVIDED",
                    temporary_image_deleted=False,
                )

            quality = (
                DataQualityStatus.STALE
                if imported_at - wire.exported_at > self._max_age
                else DataQualityStatus.FRESH
            )
            issues = ("SUPPLEMENTAL_SNAPSHOT_STALE",) if quality is DataQualityStatus.STALE else ()
            observations = _observations(wire)
            snapshot = LeadershipSupplementSnapshot(
                snapshot_id=f"leadership:{wire.content_sha256}",
                provider="STOCKEASY_SANITIZED_UI_EXPORT",
                section=wire.generic_section,
                source_scope_id=wire.source_scope_id,
                source_snapshot_id=wire.source_snapshot_id,
                capture_method=wire.capture_method,
                permission_record_id=self._permission.record_id,
                timing=ObservationTime(
                    observed_at=wire.captured_at,
                    available_at=wire.exported_at,
                    ingested_at=imported_at,
                    as_of_date=wire.as_of,
                ),
                content_hash=wire.content_sha256,
                image_hash=wire.image_sha256,
                quality=quality,
                observations=observations,
                candidate_nominations=_candidates(wire, observations),
                issues=issues,
            )
            if temporary_image_path is not None:
                temporary_image_path.unlink()
                deleted = not temporary_image_path.exists()
                if not deleted:
                    return StockEasyImportResult(
                        outcome=StockEasyImportOutcome.REJECTED,
                        error_code="TEMPORARY_IMAGE_DELETE_FAILED",
                        temporary_image_deleted=False,
                    )
            return StockEasyImportResult(
                outcome=StockEasyImportOutcome.IMPORTED,
                snapshot=snapshot,
                temporary_image_deleted=deleted,
            )
        except (OSError, TypeError, ValidationError, ValueError):
            deleted = _delete_temporary_image(temporary_image_path)
            return StockEasyImportResult(
                outcome=StockEasyImportOutcome.REJECTED,
                error_code="INVALID_SANITIZED_SNAPSHOT",
                temporary_image_deleted=deleted,
            )


def canonical_stockeasy_content_hash(payload: Mapping[str, object]) -> str:
    """Hash the complete sanitized wire payload except its self-referential hash."""

    canonical_payload = {
        str(key): value for key, value in payload.items() if key != "content_sha256"
    }
    encoded = json.dumps(
        canonical_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _contains_prohibited_key(value: object) -> bool:
    stack: list[tuple[object, int]] = [(value, 0)]
    seen_container_ids: set[int] = set()
    visited = 0
    while stack:
        current, depth = stack.pop()
        visited += 1
        if depth > _MAX_RAW_DEPTH or visited > _MAX_RAW_NODES:
            raise ValueError("sanitized snapshot exceeds structural bounds")
        if isinstance(current, Mapping):
            identity = id(current)
            if identity in seen_container_ids:
                raise ValueError("sanitized snapshot contains a container cycle")
            seen_container_ids.add(identity)
            for key, nested in current.items():
                normalized = str(key).strip().lower().replace("-", "_")
                if any(part in normalized for part in _PROHIBITED_KEY_PARTS):
                    return True
                stack.append((nested, depth + 1))
        elif isinstance(current, (list, tuple)):
            identity = id(current)
            if identity in seen_container_ids:
                raise ValueError("sanitized snapshot contains a container cycle")
            seen_container_ids.add(identity)
            stack.extend((item, depth + 1) for item in current)
        elif isinstance(current, str):
            normalized_value = current.strip().casefold()
            if any(marker in normalized_value for marker in _PROHIBITED_VALUE_MARKERS):
                return True
            if _JWT_PATTERN.search(current):
                return True
    return False


def _hash_bounded_file(path: Path) -> str:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_IMAGE_BYTES:
        raise ValueError("temporary image is unavailable or exceeds the size bound")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _delete_temporary_image(path: Path | None) -> bool:
    if path is None:
        return False
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return False
    return not path.exists()


def _security_id(symbol: str) -> SecurityId:
    return SecurityId(value=uuid5(NAMESPACE_URL, f"prism:kr:{symbol}"))


def _observations(wire: _WireSnapshot) -> tuple[LeadershipObservation, ...]:
    result = []
    for index, item in enumerate(wire.observations):
        symbol = item.provider_symbol
        canonical = json.dumps(item.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        result.append(
            LeadershipObservation(
                observation_id=f"observation:{digest}",
                kind=item.kind,
                scope=item.scope,
                security_id=_security_id(symbol) if symbol is not None else None,
                provider_symbol=symbol,
                group_id=item.group_id,
                value=str(item.value),
                unit=item.unit,
                evidence_id=f"evidence:{digest}",
            )
        )
    return tuple(result)


def _candidates(
    wire: _WireSnapshot, observations: tuple[LeadershipObservation, ...]
) -> tuple[LeadershipCandidateNomination, ...]:
    return tuple(
        LeadershipCandidateNomination(
            security_id=_security_id(item.provider_symbol),
            provider_symbol=item.provider_symbol,
            display_name=item.display_name,
            trigger_id=item.trigger_id,
            evidence_ids=tuple(observations[index].evidence_id for index in item.observation_indexes),
        )
        for item in wire.candidates
    )
