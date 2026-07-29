from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import pytest

from prism_app.stockeasy_snapshot_import import (
    StockEasyImportOutcome,
    StockEasyPermissionRecord,
    StockEasySnapshotImporter,
    canonical_stockeasy_content_hash,
)
from prism_core.data.contracts import DataQualityStatus
from prism_core.data.contracts.leadership_supplement import (
    LeadershipCaptureMethod,
    LeadershipSection,
)


UTC = timezone.utc
AS_OF = datetime(2026, 7, 29, 7, 0, tzinfo=UTC)
IMAGE = b"sanitized-fixture-image"
IMAGE_HASH = hashlib.sha256(IMAGE).hexdigest()


def _permission(*, approved: bool = True) -> StockEasyPermissionRecord:
    return StockEasyPermissionRecord(
        record_id="permission:2026-07-29",
        terms_verified=approved,
        permission_verified=approved,
        verified_at=AS_OF - timedelta(days=1),
        terms_record_id="terms:2026-07-28" if approved else None,
        permission_basis_id="user-approval:2026-07-28" if approved else None,
        approved_source_scope_ids=("generic-security-leadership",) if approved else (),
        approved_sections=(LeadershipSection.SECURITY_LEADERSHIP,),
        approved_methods=(LeadershipCaptureMethod.APPROVED_EXPORT,),
    )


def _payload() -> dict[str, Any]:
    payload = {
        "schema_version": "stockeasy_sanitized_snapshot_v1",
        "generic_section": "SECURITY_LEADERSHIP",
        "source_scope_id": "generic-security-leadership",
        "capture_method": "APPROVED_EXPORT",
        "captured_at": (AS_OF - timedelta(minutes=5)).isoformat(),
        "exported_at": (AS_OF - timedelta(minutes=4)).isoformat(),
        "as_of": AS_OF.isoformat(),
        "source_snapshot_id": "approved-export:2026-07-29",
        "content_sha256": "0" * 64,
        "image_sha256": IMAGE_HASH,
        "observations": [
            {
                "kind": "RELATIVE_STRENGTH_RANK",
                "scope": "SECURITY",
                "provider_symbol": "005930",
                "group_id": None,
                "value": "7",
                "unit": "rank",
            }
        ],
        "candidates": [
            {
                "provider_symbol": "005930",
                "display_name": "삼성전자",
                "trigger_id": "supplemental_relative_strength",
                "observation_indexes": [0],
            }
        ],
    }
    payload["content_sha256"] = canonical_stockeasy_content_hash(payload)
    return payload


def test_approved_sanitized_export_imports_and_deletes_temporary_image(tmp_path: Path) -> None:
    temporary_image = tmp_path / "capture.png"
    temporary_image.write_bytes(IMAGE)
    importer = StockEasySnapshotImporter(permission=_permission(), max_age=timedelta(hours=24))

    result = importer.import_snapshot(
        _payload(), imported_at=AS_OF, temporary_image_path=temporary_image
    )

    assert result.outcome is StockEasyImportOutcome.IMPORTED
    assert result.snapshot is not None
    assert result.snapshot.quality is DataQualityStatus.FRESH
    assert result.snapshot.image_hash == IMAGE_HASH
    assert result.snapshot.source_scope_id == "generic-security-leadership"
    assert result.snapshot.source_snapshot_id == "approved-export:2026-07-29"
    assert result.snapshot.candidate_nominations[0].provider_symbol == "005930"
    assert result.snapshot.candidate_nominations[0].security_id.value == uuid5(
        NAMESPACE_URL, "prism:kr:005930"
    )
    assert not temporary_image.exists()
    assert result.temporary_image_deleted is True


def test_prohibited_nested_fields_are_rejected_without_retaining_temporary_image(
    tmp_path: Path,
) -> None:
    temporary_image = tmp_path / "capture.png"
    temporary_image.write_bytes(IMAGE)
    payload = _payload()
    payload["observations"][0]["metadata"] = {"session_cookie": "must-not-echo"}

    result = StockEasySnapshotImporter(
        permission=_permission(), max_age=timedelta(hours=24)
    ).import_snapshot(
        payload, imported_at=AS_OF, temporary_image_path=temporary_image
    )

    assert result.outcome is StockEasyImportOutcome.REJECTED
    assert result.error_code == "PROHIBITED_FIELD"
    assert result.temporary_image_deleted is True
    assert not temporary_image.exists()
    assert "must-not-echo" not in result.model_dump_json()


def test_malformed_or_partial_input_has_sanitized_error_and_is_cleaned_up(
    tmp_path: Path,
) -> None:
    temporary_image = tmp_path / "capture.png"
    temporary_image.write_bytes(IMAGE)
    payload = _payload()
    payload["observations"][0]["unexpected"] = "private-value-must-not-echo"

    result = StockEasySnapshotImporter(
        permission=_permission(), max_age=timedelta(hours=24)
    ).import_snapshot(
        payload, imported_at=AS_OF, temporary_image_path=temporary_image
    )

    assert result.outcome is StockEasyImportOutcome.REJECTED
    assert result.error_code == "INVALID_SANITIZED_SNAPSHOT"
    assert result.temporary_image_deleted is True
    assert not temporary_image.exists()
    assert "private-value-must-not-echo" not in result.model_dump_json()


@pytest.mark.parametrize(
    "field",
    [
        "credential",
        "cookies",
        "access_token",
        "internal_endpoint",
        "account_number",
        "payment_profile",
    ],
)
def test_every_prohibited_field_family_is_rejected_without_value_echo(field: str) -> None:
    payload = _payload()
    payload[field] = "sensitive-value-must-not-echo"

    result = StockEasySnapshotImporter(
        permission=_permission(), max_age=timedelta(hours=24)
    ).import_snapshot(payload, imported_at=AS_OF)

    assert result.outcome is StockEasyImportOutcome.REJECTED
    assert result.error_code == "PROHIBITED_FIELD"
    assert "sensitive-value-must-not-echo" not in result.model_dump_json()


def test_credential_or_internal_endpoint_material_in_allowed_values_is_rejected() -> None:
    payload = _payload()
    payload["source_snapshot_id"] = "Authorization: Bearer sensitive-value-must-not-echo"
    payload["content_sha256"] = canonical_stockeasy_content_hash(payload)

    result = StockEasySnapshotImporter(
        permission=_permission(), max_age=timedelta(hours=24)
    ).import_snapshot(payload, imported_at=AS_OF)

    assert result.outcome is StockEasyImportOutcome.REJECTED
    assert result.error_code == "PROHIBITED_FIELD"
    assert "sensitive-value-must-not-echo" not in result.model_dump_json()


def test_unverified_permission_exposes_explicit_unavailable_port_only() -> None:
    permission = StockEasyPermissionRecord.unavailable(
        record_id="permission:unavailable:2026-07-29",
        verified_at=AS_OF,
    )
    result = StockEasySnapshotImporter(
        permission=permission, max_age=timedelta(hours=24)
    ).import_snapshot(_payload(), imported_at=AS_OF)

    assert permission.approved_sections == ()
    assert permission.approved_methods == ()
    assert result.outcome is StockEasyImportOutcome.UNAVAILABLE
    assert result.snapshot is None
    assert result.error_code == "APPROVED_UI_EXPORT_NOT_CONFIRMED"


def test_permission_record_rejects_claimed_scope_without_terms_and_permission_proof() -> None:
    with pytest.raises(ValueError):
        StockEasyPermissionRecord(
            record_id="permission:invalid",
            terms_verified=False,
            permission_verified=False,
            verified_at=AS_OF,
            terms_record_id=None,
            permission_basis_id=None,
            approved_source_scope_ids=("generic-security-leadership",),
            approved_sections=(LeadershipSection.SECURITY_LEADERSHIP,),
            approved_methods=(LeadershipCaptureMethod.APPROVED_EXPORT,),
        )


def test_unapproved_exact_source_scope_is_rejected() -> None:
    payload = _payload()
    payload["source_scope_id"] = "different-generic-section"
    payload["content_sha256"] = canonical_stockeasy_content_hash(payload)

    result = StockEasySnapshotImporter(
        permission=_permission(), max_age=timedelta(hours=24)
    ).import_snapshot(payload, imported_at=AS_OF)

    assert result.outcome is StockEasyImportOutcome.REJECTED
    assert result.error_code == "SCOPE_NOT_APPROVED"


def test_old_export_is_preserved_as_stale_supplemental_evidence() -> None:
    payload = _payload()
    imported_at = AS_OF + timedelta(hours=25)

    result = StockEasySnapshotImporter(
        permission=_permission(), max_age=timedelta(hours=24)
    ).import_snapshot(payload, imported_at=imported_at)

    assert result.outcome is StockEasyImportOutcome.REJECTED
    assert result.error_code == "IMAGE_NOT_PROVIDED"

    payload["image_sha256"] = None
    payload["content_sha256"] = canonical_stockeasy_content_hash(payload)
    result = StockEasySnapshotImporter(
        permission=_permission(), max_age=timedelta(hours=24)
    ).import_snapshot(payload, imported_at=imported_at)
    assert result.outcome is StockEasyImportOutcome.IMPORTED
    assert result.snapshot is not None
    assert result.snapshot.quality is DataQualityStatus.STALE
    assert result.snapshot.issues == ("SUPPLEMENTAL_SNAPSHOT_STALE",)


def test_image_hash_mismatch_rejects_and_deletes_temporary_image(tmp_path: Path) -> None:
    temporary_image = tmp_path / "capture.png"
    temporary_image.write_bytes(b"different-image")

    result = StockEasySnapshotImporter(
        permission=_permission(), max_age=timedelta(hours=24)
    ).import_snapshot(
        _payload(), imported_at=AS_OF, temporary_image_path=temporary_image
    )

    assert result.outcome is StockEasyImportOutcome.REJECTED
    assert result.error_code == "IMAGE_HASH_MISMATCH"
    assert result.temporary_image_deleted is True
    assert not temporary_image.exists()


def test_content_hash_mismatch_is_rejected_before_snapshot_identity_is_trusted() -> None:
    payload = _payload()
    payload["content_sha256"] = "f" * 64

    result = StockEasySnapshotImporter(
        permission=_permission(), max_age=timedelta(hours=24)
    ).import_snapshot(payload, imported_at=AS_OF)

    assert result.outcome is StockEasyImportOutcome.REJECTED
    assert result.error_code == "CONTENT_HASH_MISMATCH"
    assert result.snapshot is None


def test_excessive_raw_nesting_is_bounded_sanitized_and_cleaned_up(tmp_path: Path) -> None:
    temporary_image = tmp_path / "capture.png"
    temporary_image.write_bytes(IMAGE)
    payload = _payload()
    nested: dict[str, Any] = {}
    cursor = nested
    for _ in range(1100):
        child: dict[str, Any] = {}
        cursor["nested"] = child
        cursor = child
    payload["unexpected_container"] = nested

    result = StockEasySnapshotImporter(
        permission=_permission(), max_age=timedelta(hours=24)
    ).import_snapshot(
        payload, imported_at=AS_OF, temporary_image_path=temporary_image
    )

    assert result.outcome is StockEasyImportOutcome.REJECTED
    assert result.error_code == "INVALID_SANITIZED_SNAPSHOT"
    assert result.temporary_image_deleted is True
    assert not temporary_image.exists()


def test_candidate_cannot_bind_another_securitys_observation() -> None:
    payload = _payload()
    payload["candidates"][0]["provider_symbol"] = "000660"
    payload["content_sha256"] = canonical_stockeasy_content_hash(payload)

    result = StockEasySnapshotImporter(
        permission=_permission(), max_age=timedelta(hours=24)
    ).import_snapshot(payload, imported_at=AS_OF)

    assert result.outcome is StockEasyImportOutcome.REJECTED
    assert result.error_code == "INVALID_SANITIZED_SNAPSHOT"


def test_symlink_image_is_rejected_without_deleting_or_hashing_target(tmp_path: Path) -> None:
    target = tmp_path / "private-source.bin"
    target.write_bytes(IMAGE)
    temporary_link = tmp_path / "capture.png"
    temporary_link.symlink_to(target)

    result = StockEasySnapshotImporter(
        permission=_permission(), max_age=timedelta(hours=24)
    ).import_snapshot(
        _payload(), imported_at=AS_OF, temporary_image_path=temporary_link
    )

    assert result.outcome is StockEasyImportOutcome.REJECTED
    assert result.error_code == "INVALID_SANITIZED_SNAPSHOT"
    assert not temporary_link.exists()
    assert target.read_bytes() == IMAGE
