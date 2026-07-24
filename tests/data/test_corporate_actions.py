from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from prism_core.data import (
    CorporateAction,
    CorporateActionEvidence,
    CorporateActionRepository,
    CorporateActionType,
    DataQualityStatus,
    MergeDisposition,
    ObservationTime,
    SecurityId,
    SecurityMasterRepository,
)
from prism_core.storage import DatabaseKind, migrate_database, open_database


UTC = timezone.utc
SECURITY_ID = SecurityId(value=UUID("00000000-0000-0000-0000-000000000001"))
ACTION_ID = UUID("10000000-0000-0000-0000-000000000001")


def _dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _split(
    *,
    ratio: str = "2",
    provider: str = "FMP",
    source_record_id: str = "split-1",
    source_hash: str = "a" * 64,
    revision: int = 0,
    available_at: str = "2026-01-09T12:00:00",
    effective_at: str = "2026-01-10T14:30:00",
) -> CorporateActionEvidence:
    timing = ObservationTime(
        observed_at=_dt("2026-01-09T11:00:00"),
        available_at=_dt(available_at),
        ingested_at=max(_dt(available_at), _dt("2026-01-09T12:05:00")),
        as_of_date=max(_dt(available_at), _dt("2026-01-20T00:00:00")),
    )
    return CorporateActionEvidence(
        action_id=ACTION_ID,
        effective_at=_dt(effective_at),
        action=CorporateAction(
            security_id=SECURITY_ID,
            provider=provider,
            provider_symbol="XYZ",
            source_record_id=source_record_id,
            source_hash=source_hash,
            revision=revision,
            timing=timing,
            quality=DataQualityStatus.FRESH,
            action_type=CorporateActionType.SPLIT,
            effective_date=_dt(effective_at).date(),
            ratio=Decimal(ratio),
        ),
    )


def test_split_is_hidden_until_both_effective_and_available_boundaries(
    tmp_path: Path,
):
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        SecurityMasterRepository(connection).register_security(
            SECURITY_ID,
            market="US",
            created_at=_dt("2020-01-01T00:00:00"),
        )
        repository = CorporateActionRepository(connection)
        repository.merge(_split())

        before_effective = repository.actions_as_of(
            SECURITY_ID, query_as_of=_dt("2026-01-10T14:29:59")
        )
        after_effective = repository.actions_as_of(
            SECURITY_ID, query_as_of=_dt("2026-01-10T14:30:00")
        )

        late_repository = CorporateActionRepository(connection)
        late_repository.merge(
            _split(
                source_record_id="late-split",
                source_hash="b" * 64,
                available_at="2026-01-11T12:00:00",
            )
        )
        before_availability = late_repository.actions_as_of(
            SECURITY_ID, query_as_of=_dt("2026-01-11T11:59:59")
        )

    assert before_effective == ()
    assert len(after_effective) == 1
    assert after_effective[0].ratio == Decimal("2")
    assert len(before_availability) == 1
    assert before_availability[0].evidence_count == 1


def test_duplicate_provider_event_merges_idempotently(tmp_path: Path):
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        SecurityMasterRepository(connection).register_security(
            SECURITY_ID,
            market="US",
            created_at=_dt("2020-01-01T00:00:00"),
        )
        repository = CorporateActionRepository(connection)
        evidence = _split()

        assert repository.merge(evidence) is MergeDisposition.INSERTED
        assert repository.merge(evidence) is MergeDisposition.DUPLICATE
        assert connection.execute(
            "SELECT COUNT(*) FROM corporate_action_events"
        ).fetchone()[0] == 1


def test_conflicting_provider_terms_return_conflict_without_silent_choice(
    tmp_path: Path,
):
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        SecurityMasterRepository(connection).register_security(
            SECURITY_ID,
            market="US",
            created_at=_dt("2020-01-01T00:00:00"),
        )
        repository = CorporateActionRepository(connection)
        repository.merge(_split(ratio="2"))
        repository.merge(
            _split(
                ratio="3",
                provider="SEC",
                source_record_id="sec-split-1",
                source_hash="c" * 64,
            )
        )

        actions = repository.actions_as_of(
            SECURITY_ID, query_as_of=_dt("2026-01-20T00:00:00")
        )

    assert len(actions) == 1
    assert actions[0].quality is DataQualityStatus.CONFLICT
    assert actions[0].ratio is None
    assert actions[0].evidence_count == 2


def test_available_effective_date_disagreement_blocks_early_adjustment(
    tmp_path: Path,
):
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        SecurityMasterRepository(connection).register_security(
            SECURITY_ID,
            market="US",
            created_at=_dt("2020-01-01T00:00:00"),
        )
        repository = CorporateActionRepository(connection)
        repository.merge(_split())
        repository.merge(
            _split(
                provider="SEC",
                source_record_id="sec-split-1",
                source_hash="c" * 64,
                effective_at="2026-01-12T14:30:00",
            )
        )

        actions = repository.actions_as_of(
            SECURITY_ID, query_as_of=_dt("2026-01-11T00:00:00")
        )

    assert len(actions) == 1
    assert actions[0].quality is DataQualityStatus.CONFLICT
    assert actions[0].effective_at is None
    assert actions[0].ratio is None
    assert actions[0].evidence_count == 2


def test_future_revision_does_not_mask_the_revision_known_at_query_time(
    tmp_path: Path,
):
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        SecurityMasterRepository(connection).register_security(
            SECURITY_ID,
            market="US",
            created_at=_dt("2020-01-01T00:00:00"),
        )
        repository = CorporateActionRepository(connection)
        repository.merge(_split(ratio="2", revision=0))
        repository.merge(
            _split(
                ratio="3",
                source_hash="d" * 64,
                revision=1,
                available_at="2026-01-12T12:00:00",
            )
        )

        before_revision = repository.actions_as_of(
            SECURITY_ID, query_as_of=_dt("2026-01-11T00:00:00")
        )
        after_revision = repository.actions_as_of(
            SECURITY_ID, query_as_of=_dt("2026-01-13T00:00:00")
        )

    assert before_revision[0].ratio == Decimal("2")
    assert after_revision[0].ratio == Decimal("3")


def test_cash_dividend_uses_the_same_effective_instant_boundary(tmp_path: Path):
    timing = ObservationTime(
        observed_at=_dt("2026-02-01T00:00:00"),
        available_at=_dt("2026-02-01T01:00:00"),
        ingested_at=_dt("2026-02-01T01:05:00"),
        as_of_date=_dt("2026-02-10T00:00:00"),
    )
    dividend = CorporateActionEvidence(
        action_id=UUID("10000000-0000-0000-0000-000000000002"),
        effective_at=_dt("2026-02-05T14:30:00"),
        action=CorporateAction(
            security_id=SECURITY_ID,
            provider="FMP",
            provider_symbol="XYZ",
            source_record_id="dividend-1",
            source_hash="e" * 64,
            revision=0,
            timing=timing,
            quality=DataQualityStatus.FRESH,
            action_type=CorporateActionType.CASH_DIVIDEND,
            effective_date=date(2026, 2, 5),
            cash_amount=Decimal("0.25"),
            currency="USD",
        ),
    )

    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        SecurityMasterRepository(connection).register_security(
            SECURITY_ID,
            market="US",
            created_at=_dt("2020-01-01T00:00:00"),
        )
        repository = CorporateActionRepository(connection)
        repository.merge(dividend)

        assert repository.actions_as_of(
            SECURITY_ID, query_as_of=_dt("2026-02-05T14:29:59")
        ) == ()
        available = repository.actions_as_of(
            SECURITY_ID, query_as_of=_dt("2026-02-05T14:30:00")
        )

    assert available[0].cash_amount == Decimal("0.25")
    assert available[0].currency == "USD"


def test_reingestion_with_new_ingested_at_is_idempotent(tmp_path: Path):
    first = _split()
    later_timing = first.action.timing.model_copy(
        update={
            "ingested_at": _dt("2026-01-09T13:00:00"),
            "as_of_date": _dt("2026-01-21T00:00:00"),
        }
    )
    reingested = first.model_copy(
        update={"action": first.action.model_copy(update={"timing": later_timing})}
    )

    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        SecurityMasterRepository(connection).register_security(
            SECURITY_ID,
            market="US",
            created_at=_dt("2020-01-01T00:00:00"),
        )
        repository = CorporateActionRepository(connection)

        assert repository.merge(first) is MergeDisposition.INSERTED
        assert repository.merge(reingested) is MergeDisposition.DUPLICATE
        assert connection.execute(
            "SELECT COUNT(*) FROM corporate_action_events"
        ).fetchone()[0] == 1


def test_equivalent_provider_evidence_coalesces_under_one_curated_action_id(
    tmp_path: Path,
):
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        SecurityMasterRepository(connection).register_security(
            SECURITY_ID,
            market="US",
            created_at=_dt("2020-01-01T00:00:00"),
        )
        repository = CorporateActionRepository(connection)
        repository.merge(_split(ratio="2.0"))
        repository.merge(
            _split(
                ratio="2.00",
                provider="SEC",
                source_record_id="sec-split-1",
                source_hash="f" * 64,
            )
        )

        actions = repository.actions_as_of(
            SECURITY_ID, query_as_of=_dt("2026-01-20T00:00:00")
        )

    assert len(actions) == 1
    assert actions[0].action_id == ACTION_ID
    assert actions[0].quality is DataQualityStatus.FRESH
    assert actions[0].ratio == Decimal("2")
    assert actions[0].evidence_count == 2


def test_market_local_effective_date_is_preserved_across_utc_boundary(
    tmp_path: Path,
):
    effective_at = datetime.fromisoformat("2026-01-10T00:00:00+09:00")
    evidence = _split(effective_at="2026-01-10T00:00:00+09:00")

    assert evidence.action.effective_date == date(2026, 1, 10)
    assert evidence.effective_at == effective_at

    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        SecurityMasterRepository(connection).register_security(
            SECURITY_ID,
            market="US",
            created_at=_dt("2020-01-01T00:00:00"),
        )
        repository = CorporateActionRepository(connection)
        repository.merge(evidence)

        assert repository.actions_as_of(
            SECURITY_ID,
            query_as_of=datetime.fromisoformat("2026-01-09T14:59:59+00:00"),
        ) == ()
        available = repository.actions_as_of(
            SECURITY_ID,
            query_as_of=datetime.fromisoformat("2026-01-09T15:00:00+00:00"),
        )

    assert available[0].effective_date == date(2026, 1, 10)
