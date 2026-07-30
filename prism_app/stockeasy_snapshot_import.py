"""Permission-gated import of bounded sanitized StockEasy UI/export snapshots."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
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
_MAX_SNAPSHOT_BYTES = 1 * 1024 * 1024
_MAX_PERMISSION_BYTES = 64 * 1024
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
_REQUIRED_PRODUCT_SECTIONS = {
    "SECURITIES": (LeadershipSection.SECURITY_LEADERSHIP,),
    "MARKET_OVERVIEW": (
        LeadershipSection.MARKET_BREADTH,
        LeadershipSection.INVESTOR_FLOWS,
    ),
    "LEADING_SECURITIES": (LeadershipSection.SECURITY_LEADERSHIP,),
    "LEADING_SECTORS": (LeadershipSection.LEADING_GROUPS,),
}
_UNAVAILABLE_PREREQUISITE = (
    "Confirm current terms and user authorization for one visible read-only UI capture "
    "or official export, record the exact approved sections and method, then provide a "
    "bounded sanitized stockeasy_sanitized_snapshot_v1 JSON file."
)


class StockEasyImportOutcome(str, Enum):
    IMPORTED = "IMPORTED"
    UNAVAILABLE = "UNAVAILABLE"
    REJECTED = "REJECTED"


def unavailable_stockeasy_capability(
    *,
    checked_at: datetime,
    snapshot_argument_supplied: bool,
) -> dict[str, object]:
    """Project an honest fail-soft result without touching a UI or supplied file."""

    if checked_at.tzinfo is None or checked_at.utcoffset() is None:
        raise ValueError("checked_at must be timezone-aware")
    return {
        "status": "STOCKEASY_UNAVAILABLE",
        "site_status": "SITE_STATUS_UNKNOWN",
        "site_status_as_of": None,
        "site_status_basis": None,
        "site_currently_verified": False,
        "ingestion_status": "NOT_INGESTED",
        "reason": "APPROVED_UI_EXPORT_NOT_CONFIRMED",
        "source": "STOCKEASY_APPROVED_UI_EXPORT",
        "capability_checked_at": checked_at.isoformat(),
        "collection_attempted": False,
        "collection_attempted_at": None,
        "capture_method": None,
        "observed_at": None,
        "available_at": None,
        "ingested_at": None,
        "content_hash": None,
        "image_hash": None,
        "snapshot_argument_supplied": snapshot_argument_supplied,
        "requirements": [
            {
                "requirement": requirement,
                "status": "UNAVAILABLE",
                "evidence_gap": "APPROVED_SECTION_NOT_OBSERVED",
                "contract_sections": [item.value for item in contract_sections],
            }
            for requirement, contract_sections in _REQUIRED_PRODUCT_SECTIONS.items()
        ],
        "prerequisite_code": "APPROVED_VISIBLE_UI_OR_OFFICIAL_EXPORT_REQUIRED",
        "prerequisite": _UNAVAILABLE_PREREQUISITE,
        "price_authority": "KIS_KRX",
        "entry_signal_authority": False,
        "fail_soft": True,
    }


def stockeasy_capability_projection(
    result: "StockEasyImportResult",
    *,
    checked_at: datetime,
    snapshot_argument_supplied: bool,
) -> dict[str, object]:
    """Project actual bounded evidence separately from site and ingestion state."""

    if result.outcome is StockEasyImportOutcome.UNAVAILABLE:
        return unavailable_stockeasy_capability(
            checked_at=checked_at,
            snapshot_argument_supplied=snapshot_argument_supplied,
        )
    if result.outcome is StockEasyImportOutcome.REJECTED:
        return {
            "status": "STOCKEASY_REJECTED",
            "site_status": "SITE_STATUS_UNKNOWN",
            "site_status_as_of": None,
            "site_status_basis": None,
            "site_currently_verified": False,
            "ingestion_status": "REJECTED",
            "reason": result.error_code,
            "source": "STOCKEASY_APPROVED_UI_EXPORT",
            "capability_checked_at": checked_at.isoformat(),
            "snapshot_argument_supplied": snapshot_argument_supplied,
            "requirements": [],
            "observations": [],
            "price_authority": "KIS_KRX",
            "entry_signal_authority": False,
            "authority_crosscheck_status": "NOT_PERFORMED",
            "supplemental_numeric_values_used_for_strategy": False,
            "fail_soft": True,
        }

    snapshot = result.snapshot
    if snapshot is None:  # Defensive: the result contract already forbids this state.
        raise ValueError("imported StockEasy result omitted its snapshot")
    kinds = {item.kind for item in snapshot.observations}
    requirements = [
        _imported_requirement(
            "SECURITIES",
            (LeadershipSection.SECURITY_LEADERSHIP,),
            sum(item.scope is LeadershipScope.SECURITY for item in snapshot.observations),
        ),
        _imported_requirement(
            "MARKET_OVERVIEW",
            (LeadershipSection.MARKET_BREADTH, LeadershipSection.INVESTOR_FLOWS),
            int(
                LeadershipObservationKind.MARKET_BREADTH in kinds
                and LeadershipObservationKind.INVESTOR_FLOW in kinds
            ),
        ),
        _imported_requirement(
            "LEADING_SECURITIES",
            (LeadershipSection.SECURITY_LEADERSHIP,),
            len(snapshot.candidate_nominations),
        ),
        _imported_requirement(
            "LEADING_SECTORS",
            (LeadershipSection.LEADING_GROUPS,),
            sum(
                item.kind is LeadershipObservationKind.LEADING_GROUP
                for item in snapshot.observations
            ),
        ),
    ]
    complete = all(item["status"] == "IMPORTED" for item in requirements)
    temporary_capture_used = snapshot.image_hash is not None
    stale = snapshot.quality is DataQualityStatus.STALE
    return {
        "status": (
            "CONNECTED_STALE"
            if stale
            else "CONNECTED"
            if complete
            else "CONNECTED_PARTIAL"
        ),
        "site_status": "SITE_AVAILABLE",
        "site_status_as_of": snapshot.timing.observed_at.isoformat(),
        "site_status_basis": "OPERATOR_ATTESTED_VISIBLE_UI_SNAPSHOT",
        "site_currently_verified": False,
        "ingestion_status": "IMPORTED",
        "reason": None,
        "source": snapshot.provider,
        "capability_checked_at": checked_at.isoformat(),
        "collection_attempted": True,
        "collection_attempted_at": snapshot.timing.observed_at.isoformat(),
        "capture_method": snapshot.capture_method.value,
        "observed_at": snapshot.timing.observed_at.isoformat(),
        "available_at": snapshot.timing.available_at.isoformat(),
        "ingested_at": snapshot.timing.ingested_at.isoformat(),
        "content_hash": snapshot.content_hash,
        "image_hash": snapshot.image_hash,
        "snapshot_argument_supplied": snapshot_argument_supplied,
        "permission_record_id": snapshot.permission_record_id,
        "source_scope_id": snapshot.source_scope_id,
        "source_snapshot_id": snapshot.source_snapshot_id,
        "quality": snapshot.quality.value,
        "snapshot_stale": stale,
        "issues": list(snapshot.issues),
        "requirements": requirements,
        "observations": [
            {
                "kind": item.kind.value,
                "scope": item.scope.value,
                "provider_symbol": item.provider_symbol,
                "group_id": item.group_id,
                "value": str(item.value),
                "unit": item.unit,
                "evidence_id": item.evidence_id,
            }
            for item in snapshot.observations
        ],
        "temporary_capture_used": temporary_capture_used,
        "temporary_capture_deleted": result.temporary_image_deleted,
        "temporary_capture_deletion_verified": (
            not temporary_capture_used or result.temporary_image_deleted
        ),
        "price_authority": "KIS_KRX",
        "entry_signal_authority": False,
        "authority_crosscheck_status": "NOT_PERFORMED",
        "supplemental_numeric_values_used_for_strategy": False,
        "fail_soft": True,
    }


def _imported_requirement(
    requirement: str,
    contract_sections: tuple[LeadershipSection, ...],
    evidence_count: int,
) -> dict[str, object]:
    return {
        "requirement": requirement,
        "status": "IMPORTED" if evidence_count else "NOT_INGESTED",
        "evidence_count": evidence_count,
        "contract_sections": [item.value for item in contract_sections],
    }


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

    @model_validator(mode="after")
    def validate_kind_scope(self) -> "_WireObservation":
        market_only = {
            LeadershipObservationKind.MARKET_BREADTH,
            LeadershipObservationKind.INVESTOR_FLOW,
        }
        security_only = {
            LeadershipObservationKind.RELATIVE_STRENGTH_RANK,
            LeadershipObservationKind.TURNOVER_RANK,
            LeadershipObservationKind.TURNOVER_VALUE,
            LeadershipObservationKind.SESSION_RETURN,
            LeadershipObservationKind.MOMENTUM_RANK,
            LeadershipObservationKind.PEAK_STATE,
        }
        if self.kind in market_only and self.scope is not LeadershipScope.MARKET:
            raise ValueError("market observation kind requires market scope")
        if (
            self.kind is LeadershipObservationKind.LEADING_GROUP
            and self.scope is not LeadershipScope.GROUP
        ):
            raise ValueError("leading-group observation requires group scope")
        if self.kind in security_only and self.scope is not LeadershipScope.SECURITY:
            raise ValueError("security observation kind requires security scope")
        if (
            self.kind is LeadershipObservationKind.HIGH_52_WEEK_STATE
            and self.scope is LeadershipScope.GROUP
        ):
            raise ValueError("52-week-high state cannot use group scope")
        return self


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
                or any(
                    _section_for_observation(item)
                    not in self._permission.approved_sections
                    for item in wire.observations
                )
                or (
                    bool(wire.candidates)
                    and LeadershipSection.SECURITY_LEADERSHIP
                    not in self._permission.approved_sections
                )
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


def load_stockeasy_import(
    *,
    snapshot_path: Path,
    permission_path: Path,
    imported_at: datetime,
    max_age: timedelta,
) -> StockEasyImportResult:
    """Load two bounded local contracts and run the permission-gated importer."""

    try:
        permission_bytes = _read_bounded_regular_file(
            permission_path,
            max_bytes=_MAX_PERMISSION_BYTES,
        )
        snapshot_bytes = _read_bounded_regular_file(
            snapshot_path,
            max_bytes=_MAX_SNAPSHOT_BYTES,
        )
        permission_payload = json.loads(permission_bytes)
        if not isinstance(permission_payload, Mapping):
            raise ValueError("permission record must be a JSON object")
        if _contains_prohibited_key(permission_payload):
            return StockEasyImportResult(
                outcome=StockEasyImportOutcome.REJECTED,
                error_code="PROHIBITED_FIELD",
                temporary_image_deleted=False,
            )
        permission = StockEasyPermissionRecord.model_validate_json(permission_bytes)
        payload = json.loads(snapshot_bytes)
        if not isinstance(payload, Mapping):
            raise ValueError("sanitized snapshot must be a JSON object")
        return StockEasySnapshotImporter(
            permission=permission,
            max_age=max_age,
        ).import_snapshot(payload, imported_at=imported_at)
    except (OSError, TypeError, ValidationError, ValueError):
        return StockEasyImportResult(
            outcome=StockEasyImportOutcome.REJECTED,
            error_code="INVALID_IMPORT_INPUT",
            temporary_image_deleted=False,
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


def _read_bounded_regular_file(path: Path, *, max_bytes: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("import input must be a regular file")
        if metadata.st_size <= 0 or metadata.st_size > max_bytes:
            raise ValueError("import input exceeds the size boundary")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            payload = stream.read(max_bytes + 1)
        if len(payload) != metadata.st_size or len(payload) > max_bytes:
            raise ValueError("import input changed or exceeded the size boundary")
        return payload
    finally:
        os.close(descriptor)


def _section_for_observation(item: _WireObservation) -> LeadershipSection:
    if item.kind is LeadershipObservationKind.MARKET_BREADTH:
        return LeadershipSection.MARKET_BREADTH
    if item.kind is LeadershipObservationKind.INVESTOR_FLOW:
        return LeadershipSection.INVESTOR_FLOWS
    if item.kind is LeadershipObservationKind.LEADING_GROUP:
        return LeadershipSection.LEADING_GROUPS
    if (
        item.kind is LeadershipObservationKind.HIGH_52_WEEK_STATE
        and item.scope is LeadershipScope.MARKET
    ):
        return LeadershipSection.MARKET_BREADTH
    return LeadershipSection.SECURITY_LEADERSHIP


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
    return hashlib.sha256(
        _read_bounded_regular_file(path, max_bytes=_MAX_IMAGE_BYTES)
    ).hexdigest()


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
