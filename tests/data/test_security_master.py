from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest

from prism_core.data import (
    DataQualityStatus,
    ObservationTime,
    SecurityAliasEvidence,
    SecurityId,
    SecurityListingEvidence,
    SecurityMasterRepository,
    SymbolMapping,
    ListingStatus,
)
from prism_core.storage import DatabaseKind, migrate_database, open_database


UTC = timezone.utc
SECURITY_ID = SecurityId(value=UUID("00000000-0000-0000-0000-000000000001"))


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


def _timing(*, observed: str, available: str, ingested: str, as_of: str) -> ObservationTime:
    return ObservationTime(
        observed_at=_dt(observed),
        available_at=_dt(available),
        ingested_at=_dt(ingested),
        as_of_date=_dt(as_of),
    )


def _alias(
    symbol: str,
    *,
    valid_from: str,
    valid_to: str | None,
    source_record_id: str,
    revision: int = 0,
    source_hash: str | None = None,
    available: str = "2025-12-15T01:00:00",
) -> SecurityAliasEvidence:
    timing = _timing(
        observed="2025-12-15T00:00:00",
        available=available,
        ingested=max(available, "2025-12-15T01:05:00"),
        as_of="2026-02-01T00:00:00",
    )
    return SecurityAliasEvidence(
        mapping=SymbolMapping(
            security_id=SECURITY_ID,
            provider="FMP",
            provider_symbol=symbol,
            market="US",
            valid_from=_dt(valid_from),
            valid_to=_dt(valid_to) if valid_to else None,
            timing=timing,
            source_hash=source_hash or ("a" * 64 if symbol == "OLD" else "b" * 64),
        ),
        source_record_id=source_record_id,
        revision=revision,
        quality=DataQualityStatus.FRESH,
    )


def test_ticker_rename_resolves_both_symbols_to_one_stable_security_id(
    tmp_path: Path,
):
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        repository = SecurityMasterRepository(connection)
        repository.register_security(
            SECURITY_ID,
            market="US",
            created_at=_dt("2020-01-01T00:00:00"),
        )
        repository.merge_alias(
            _alias(
                "OLD",
                valid_from="2020-01-01T00:00:00",
                valid_to="2026-01-01T00:00:00",
                source_record_id="alias-old",
            )
        )
        repository.merge_alias(
            _alias(
                "NEW",
                valid_from="2026-01-01T00:00:00",
                valid_to=None,
                source_record_id="alias-new",
            )
        )

        before = repository.resolve_symbol(
            "FMP", "OLD", query_as_of=_dt("2025-12-20T00:00:00")
        )
        after = repository.resolve_symbol(
            "FMP", "NEW", query_as_of=_dt("2026-02-01T00:00:00")
        )

    assert before.security_id == SECURITY_ID
    assert after.security_id == SECURITY_ID
    assert before.quality is DataQualityStatus.FRESH
    assert after.quality is DataQualityStatus.FRESH


def test_delisted_security_is_historically_queryable_but_not_currently_listed(
    tmp_path: Path,
):
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        repository = SecurityMasterRepository(connection)
        repository.register_security(
            SECURITY_ID,
            market="US",
            created_at=_dt("2020-01-01T00:00:00"),
        )
        repository.merge_alias(
            _alias(
                "OLD",
                valid_from="2020-01-01T00:00:00",
                valid_to=None,
                source_record_id="alias-old",
            )
        )
        repository.merge_listing_status(
            SecurityListingEvidence(
                security_id=SECURITY_ID,
                provider="FMP",
                provider_symbol="OLD",
                market="US",
                status=ListingStatus.LISTED,
                effective_at=_dt("2020-01-01T14:30:00"),
                source_record_id="listing",
                source_hash="c" * 64,
                revision=0,
                timing=_timing(
                    observed="2020-01-01T14:30:00",
                    available="2020-01-01T14:30:00",
                    ingested="2020-01-01T14:31:00",
                    as_of="2020-01-01T15:00:00",
                ),
                quality=DataQualityStatus.FRESH,
            )
        )
        repository.merge_listing_status(
            SecurityListingEvidence(
                security_id=SECURITY_ID,
                provider="FMP",
                provider_symbol="OLD",
                market="US",
                status=ListingStatus.DELISTED,
                effective_at=_dt("2026-01-15T14:30:00"),
                source_record_id="delisting",
                source_hash="d" * 64,
                revision=0,
                timing=_timing(
                    observed="2026-01-15T14:30:00",
                    available="2026-01-16T12:00:00",
                    ingested="2026-01-16T12:05:00",
                    as_of="2026-01-16T13:00:00",
                ),
                quality=DataQualityStatus.FRESH,
            )
        )

        historical = repository.resolve_symbol(
            "FMP", "OLD", query_as_of=_dt("2025-12-20T00:00:00")
        )
        before_availability = repository.resolve_symbol(
            "FMP", "OLD", query_as_of=_dt("2026-01-16T11:00:00")
        )
        current = repository.resolve_symbol(
            "FMP", "OLD", query_as_of=_dt("2026-02-01T00:00:00")
        )

    assert historical.security_id == SECURITY_ID
    assert historical.listing_status is ListingStatus.LISTED
    assert before_availability.listing_status is ListingStatus.LISTED
    assert current.security_id == SECURITY_ID
    assert current.listing_status is ListingStatus.DELISTED


def test_alias_revision_supersedes_only_after_the_revision_is_available(tmp_path: Path):
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        repository = SecurityMasterRepository(connection)
        repository.register_security(
            SECURITY_ID,
            market="US",
            created_at=_dt("2020-01-01T00:00:00"),
        )
        repository.merge_alias(
            _alias(
                "OLD",
                valid_from="2020-01-01T00:00:00",
                valid_to=None,
                source_record_id="alias-old",
                revision=0,
            )
        )
        repository.merge_alias(
            _alias(
                "OLD",
                valid_from="2020-01-01T00:00:00",
                valid_to="2026-01-01T00:00:00",
                source_record_id="alias-old",
                revision=1,
                source_hash="e" * 64,
                available="2026-01-02T00:00:00",
            )
        )

        before_revision = repository.resolve_symbol(
            "FMP", "OLD", query_as_of=_dt("2026-01-01T12:00:00")
        )
        after_revision = repository.resolve_symbol(
            "FMP", "OLD", query_as_of=_dt("2026-01-03T00:00:00")
        )

    assert before_revision.security_id == SECURITY_ID
    assert after_revision.security_id is None
    assert after_revision.quality is DataQualityStatus.UNAVAILABLE


def test_alias_revision_can_correct_provider_symbol_without_leaving_old_alias_active(
    tmp_path: Path,
):
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        repository = SecurityMasterRepository(connection)
        repository.register_security(
            SECURITY_ID,
            market="US",
            created_at=_dt("2020-01-01T00:00:00"),
        )
        repository.merge_alias(
            _alias(
                "OLD",
                valid_from="2020-01-01T00:00:00",
                valid_to=None,
                source_record_id="provider-alias-1",
                revision=0,
            )
        )
        repository.merge_alias(
            _alias(
                "NEW",
                valid_from="2020-01-01T00:00:00",
                valid_to=None,
                source_record_id="provider-alias-1",
                revision=1,
                available="2026-01-02T00:00:00",
            )
        )

        before_revision = repository.resolve_symbol(
            "FMP", "OLD", query_as_of=_dt("2026-01-01T12:00:00")
        )
        old_after_revision = repository.resolve_symbol(
            "FMP", "OLD", query_as_of=_dt("2026-01-03T00:00:00")
        )
        new_after_revision = repository.resolve_symbol(
            "FMP", "NEW", query_as_of=_dt("2026-01-03T00:00:00")
        )

    assert before_revision.security_id == SECURITY_ID
    assert old_after_revision.security_id is None
    assert old_after_revision.quality is DataQualityStatus.UNAVAILABLE
    assert new_after_revision.security_id == SECURITY_ID


def test_alias_market_must_match_the_registered_security_market(tmp_path: Path):
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        repository = SecurityMasterRepository(connection)
        repository.register_security(
            SECURITY_ID,
            market="KR",
            created_at=_dt("2020-01-01T00:00:00"),
        )

        with pytest.raises(ValueError, match="market does not match"):
            repository.merge_alias(
                _alias(
                    "OLD",
                    valid_from="2020-01-01T00:00:00",
                    valid_to=None,
                    source_record_id="wrong-market",
                )
            )


def test_same_revision_divergent_listing_evidence_is_conflict(tmp_path: Path):
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        repository = SecurityMasterRepository(connection)
        repository.register_security(
            SECURITY_ID,
            market="US",
            created_at=_dt("2020-01-01T00:00:00"),
        )
        repository.merge_alias(
            _alias(
                "OLD",
                valid_from="2020-01-01T00:00:00",
                valid_to=None,
                source_record_id="alias-old",
            )
        )
        common = {
            "security_id": SECURITY_ID,
            "provider": "FMP",
            "provider_symbol": "OLD",
            "market": "US",
            "effective_at": _dt("2026-01-15T14:30:00"),
            "source_record_id": "status-1",
            "revision": 0,
            "timing": _timing(
                observed="2026-01-15T14:30:00",
                available="2026-01-15T15:00:00",
                ingested="2026-01-15T15:05:00",
                as_of="2026-01-16T00:00:00",
            ),
            "quality": DataQualityStatus.FRESH,
        }
        repository.merge_listing_status(
            SecurityListingEvidence(
                **common,
                status=ListingStatus.LISTED,
                source_hash="f" * 64,
            )
        )
        repository.merge_listing_status(
            SecurityListingEvidence(
                **common,
                status=ListingStatus.DELISTED,
                source_hash="0" * 64,
            )
        )

        result = repository.resolve_symbol(
            "FMP", "OLD", query_as_of=_dt("2026-01-16T00:00:00")
        )

    assert result.security_id == SECURITY_ID
    assert result.listing_status is None
    assert result.quality is DataQualityStatus.CONFLICT


def test_latest_status_per_provider_is_reconciled_without_silent_provider_choice(
    tmp_path: Path,
):
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        repository = SecurityMasterRepository(connection)
        repository.register_security(
            SECURITY_ID,
            market="US",
            created_at=_dt("2020-01-01T00:00:00"),
        )
        repository.merge_alias(
            _alias(
                "OLD",
                valid_from="2020-01-01T00:00:00",
                valid_to=None,
                source_record_id="alias-old",
            )
        )
        for provider, status, effective_at, source_record_id, source_hash in (
            (
                "FMP",
                ListingStatus.LISTED,
                "2020-01-01T14:30:00",
                "fmp-listing",
                "1" * 64,
            ),
            (
                "SEC",
                ListingStatus.DELISTED,
                "2026-01-15T14:30:00",
                "sec-delisting",
                "2" * 64,
            ),
        ):
            repository.merge_listing_status(
                SecurityListingEvidence(
                    security_id=SECURITY_ID,
                    provider=provider,
                    provider_symbol="OLD",
                    market="US",
                    status=status,
                    effective_at=_dt(effective_at),
                    source_record_id=source_record_id,
                    source_hash=source_hash,
                    revision=0,
                    timing=_timing(
                        observed=effective_at,
                        available=effective_at,
                        ingested=effective_at,
                        as_of="2026-01-16T00:00:00",
                    ),
                    quality=DataQualityStatus.FRESH,
                )
            )

        result = repository.resolve_symbol(
            "FMP", "OLD", query_as_of=_dt("2026-01-16T00:00:00")
        )

    assert result.security_id == SECURITY_ID
    assert result.listing_status is None
    assert result.quality is DataQualityStatus.CONFLICT


def test_listing_revision_is_selected_before_its_corrected_effective_boundary(
    tmp_path: Path,
):
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        repository = SecurityMasterRepository(connection)
        repository.register_security(
            SECURITY_ID,
            market="US",
            created_at=_dt("2020-01-01T00:00:00"),
        )
        repository.merge_alias(
            _alias(
                "OLD",
                valid_from="2020-01-01T00:00:00",
                valid_to=None,
                source_record_id="alias-old",
            )
        )
        for status, effective_at, source_record_id, revision, available, source_hash in (
            (
                ListingStatus.LISTED,
                "2020-01-01T14:30:00",
                "listing",
                0,
                "2020-01-01T14:30:00",
                "3" * 64,
            ),
            (
                ListingStatus.DELISTED,
                "2026-01-15T14:30:00",
                "delisting",
                0,
                "2026-01-15T15:00:00",
                "4" * 64,
            ),
            (
                ListingStatus.DELISTED,
                "2026-01-20T14:30:00",
                "delisting",
                1,
                "2026-01-16T12:00:00",
                "5" * 64,
            ),
        ):
            repository.merge_listing_status(
                SecurityListingEvidence(
                    security_id=SECURITY_ID,
                    provider="FMP",
                    provider_symbol="OLD",
                    market="US",
                    status=status,
                    effective_at=_dt(effective_at),
                    source_record_id=source_record_id,
                    source_hash=source_hash,
                    revision=revision,
                    timing=_timing(
                        observed=min(effective_at, available),
                        available=available,
                        ingested=available,
                        as_of="2026-01-21T00:00:00",
                    ),
                    quality=DataQualityStatus.FRESH,
                )
            )

        before_corrected_effective = repository.resolve_symbol(
            "FMP", "OLD", query_as_of=_dt("2026-01-17T00:00:00")
        )
        after_corrected_effective = repository.resolve_symbol(
            "FMP", "OLD", query_as_of=_dt("2026-01-21T00:00:00")
        )

    assert before_corrected_effective.listing_status is ListingStatus.LISTED
    assert after_corrected_effective.listing_status is ListingStatus.DELISTED
