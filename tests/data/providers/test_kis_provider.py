from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from prism_core.data import (
    CorporateActionRepository,
    CorporateActionType,
    DataQualityStatus,
    ListingStatus,
    SecurityId,
    SecurityMasterRepository,
)
from prism_core.data.providers.kis import (
    KISInstrument,
    KISMarketDataProvider,
    ProviderEventKind,
    ProviderPayload,
    ProviderTimeoutError,
)
from prism_core.storage import DatabaseKind, migrate_database, open_database


KST = ZoneInfo("Asia/Seoul")
SECURITY_ID = SecurityId(value=UUID("00000000-0000-0000-0000-000000000008"))
SECOND_SECURITY_ID = SecurityId(value=UUID("00000000-0000-0000-0000-000000000009"))
AS_OF = datetime(2026, 7, 24, 18, 0, tzinfo=KST)
INGESTED = datetime(2026, 7, 24, 18, 1, tzinfo=KST)


def test_raw_payload_hash_is_immutable_after_transport_returns() -> None:
    raw = {"prices": []}
    envelope = ProviderPayload(
        provider="KIS",
        source_record_id="kis:empty",
        revision=0,
        observed_at=datetime(2026, 7, 24, 15, 30, tzinfo=KST),
        available_at=datetime(2026, 7, 24, 15, 31, tzinfo=KST),
        payload=raw,
    )
    source_hash = envelope.source_hash

    raw["prices"].append({"close": "fabricated-later"})

    assert envelope.source_hash == source_hash
    assert envelope.payload == {"prices": []}


def test_duplicate_provider_symbol_configuration_is_rejected() -> None:
    with pytest.raises(ValueError, match="provider symbols must be unique"):
        KISMarketDataProvider(
            transport=FixtureTransport(),
            instruments=(
                KISInstrument(
                    security_id=SECURITY_ID,
                    kis_symbol="005930",
                    provider_symbols={"KRX": "005930"},
                ),
                KISInstrument(
                    security_id=SECOND_SECURITY_ID,
                    kis_symbol="000660",
                    provider_symbols={"KRX": "005930"},
                ),
            ),
            clock=lambda: INGESTED,
        )


class FixtureTransport:
    async def fetch(self, provider: str, *, as_of_date: datetime) -> ProviderPayload:
        assert provider == "KIS"
        assert as_of_date == AS_OF
        return ProviderPayload(
            provider="KIS",
            source_record_id="kis:daily:005930:2026-07-24",
            revision=0,
            observed_at=datetime(2026, 7, 24, 15, 30, tzinfo=KST),
            available_at=datetime(2026, 7, 24, 15, 31, tzinfo=KST),
            payload={
                "prices": [
                    {
                        "provider_symbol": "005930",
                        "trade_date": "2026-07-24",
                        "open": "70000",
                        "high": "71500",
                        "low": "69500",
                        "close": "71000",
                        "volume": "12345678",
                    }
                ]
            },
        )


@pytest.mark.asyncio
async def test_kis_primary_normalizes_raw_daily_bar_with_pit_metadata() -> None:
    provider = KISMarketDataProvider(
        transport=FixtureTransport(),
        instruments=(KISInstrument(security_id=SECURITY_ID, kis_symbol="005930"),),
        clock=lambda: INGESTED,
    )

    result = await provider.fetch_result(
        security_ids=(SECURITY_ID,),
        as_of_date=AS_OF,
    )

    assert result.snapshot.quality is DataQualityStatus.FRESH
    assert len(result.snapshot.price_bars) == 1
    bar = result.snapshot.price_bars[0]
    assert bar.provider == "KIS"
    assert bar.provider_symbol == "005930"
    assert bar.raw_close == Decimal("71000")
    assert bar.currency == "KRW"
    assert bar.bar_start == datetime(2026, 7, 24, 0, 0, tzinfo=KST)
    assert bar.bar_end == datetime(2026, 7, 24, 15, 30, tzinfo=KST)
    assert bar.timing.observed_at == datetime(2026, 7, 24, 15, 30, tzinfo=KST)
    assert bar.timing.available_at == datetime(2026, 7, 24, 15, 31, tzinfo=KST)
    assert bar.timing.ingested_at == INGESTED
    assert bar.timing.as_of_date == AS_OF
    assert bar.source_hash == result.raw_payloads[0].source_hash
    assert len(bar.source_hash) == 64
    assert result.events == ()


@pytest.mark.asyncio
async def test_timeout_retries_are_bounded_and_observable() -> None:
    attempts = 0
    sleeps: list[float] = []

    class FlakyTransport(FixtureTransport):
        async def fetch(self, provider: str, *, as_of_date: datetime) -> ProviderPayload:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise ProviderTimeoutError("fixture timeout")
            return await super().fetch(provider, as_of_date=as_of_date)

    async def sleeper(delay: float) -> None:
        sleeps.append(delay)

    provider = KISMarketDataProvider(
        transport=FlakyTransport(),
        instruments=(KISInstrument(security_id=SECURITY_ID, kis_symbol="005930"),),
        clock=lambda: INGESTED,
        max_attempts=3,
        sleeper=sleeper,
    )

    result = await provider.fetch_result(
        security_ids=(SECURITY_ID,),
        as_of_date=AS_OF,
    )

    assert attempts == 3
    assert sleeps == [1.0, 2.0]
    assert [event.kind for event in result.events] == [
        ProviderEventKind.TIMEOUT,
        ProviderEventKind.TIMEOUT,
    ]
    assert [event.attempt for event in result.events] == [1, 2]
    assert result.snapshot.quality is DataQualityStatus.FRESH


@pytest.mark.asyncio
async def test_exhausted_primary_returns_unavailable_snapshot_without_fabrication() -> None:
    attempts = 0

    class OfflineTransport:
        async def fetch(self, provider: str, *, as_of_date: datetime) -> ProviderPayload:
            nonlocal attempts
            attempts += 1
            raise ProviderTimeoutError("do not leak this upstream detail")

    provider = KISMarketDataProvider(
        transport=OfflineTransport(),
        instruments=(KISInstrument(security_id=SECURITY_ID, kis_symbol="005930"),),
        clock=lambda: INGESTED,
        max_attempts=2,
    )

    result = await provider.fetch_result(
        security_ids=(SECURITY_ID,),
        as_of_date=AS_OF,
    )

    assert attempts == 2
    assert result.raw_payloads == ()
    assert result.snapshot.price_bars == ()
    assert result.snapshot.quality is DataQualityStatus.UNAVAILABLE
    assert result.events[-1].kind is ProviderEventKind.RETRY_EXHAUSTED
    assert "do not leak" not in " ".join(event.detail for event in result.events)


@pytest.mark.asyncio
async def test_krx_supplement_keeps_provenance_and_conflict_instead_of_overwrite() -> None:
    class ConflictingTransport(FixtureTransport):
        async def fetch(self, provider: str, *, as_of_date: datetime) -> ProviderPayload:
            if provider == "KIS":
                return await super().fetch(provider, as_of_date=as_of_date)
            return ProviderPayload(
                provider="KRX",
                source_record_id="krx:daily:005930:2026-07-24",
                revision=0,
                observed_at=datetime(2026, 7, 24, 15, 30, tzinfo=KST),
                available_at=datetime(2026, 7, 24, 16, 0, tzinfo=KST),
                payload={
                    "prices": [
                        {
                            "provider_symbol": "005930",
                            "trade_date": "2026-07-24",
                            "open": "70000",
                            "high": "71500",
                            "low": "69500",
                            "close": "70900",
                            "volume": "12345678",
                        }
                    ]
                },
            )

    provider = KISMarketDataProvider(
        transport=ConflictingTransport(),
        instruments=(
            KISInstrument(
                security_id=SECURITY_ID,
                kis_symbol="005930",
                provider_symbols={"KRX": "005930"},
            ),
        ),
        clock=lambda: INGESTED,
        supplement_providers=("KRX",),
    )

    result = await provider.fetch_result(
        security_ids=(SECURITY_ID,),
        as_of_date=AS_OF,
    )

    assert [bar.provider for bar in result.snapshot.price_bars] == ["KIS", "KRX"]
    assert result.snapshot.quality is DataQualityStatus.CONFLICT
    assert result.events[-1].kind is ProviderEventKind.CONFLICT
    assert {payload.provider for payload in result.raw_payloads} == {"KIS", "KRX"}


@pytest.mark.asyncio
async def test_equivalent_corporate_actions_share_curated_id_and_preserve_kr_date(
    tmp_path,
) -> None:
    class ActionTransport(FixtureTransport):
        async def fetch(self, provider: str, *, as_of_date: datetime) -> ProviderPayload:
            base = await super().fetch("KIS", as_of_date=as_of_date)
            action = {
                "provider_symbol": "005930",
                "event_key": f"{provider.lower()}:native:split-1",
                "action_type": "SPLIT",
                "effective_date": "2026-07-25",
                "ratio": "2.0" if provider == "KIS" else "2.00",
            }
            return ProviderPayload(
                provider=provider,
                source_record_id=f"{provider.lower()}:action:split-1",
                revision=0,
                observed_at=base.observed_at,
                available_at=base.available_at,
                payload={
                    "prices": base.payload["prices"] if provider == "KIS" else [],
                    "corporate_actions": [action],
                },
            )

    provider = KISMarketDataProvider(
        transport=ActionTransport(),
        instruments=(
            KISInstrument(
                security_id=SECURITY_ID,
                kis_symbol="005930",
                provider_symbols={"KRX": "005930"},
            ),
        ),
        clock=lambda: INGESTED,
        supplement_providers=("KRX",),
    )

    result = await provider.fetch_result(
        security_ids=(SECURITY_ID,),
        as_of_date=AS_OF,
    )

    assert len(result.corporate_action_evidence) == 2
    first, second = result.corporate_action_evidence
    assert first.action_id == second.action_id
    assert {first.action.provider, second.action.provider} == {"KIS", "KRX"}
    assert first.action.action_type is CorporateActionType.SPLIT
    assert first.effective_at == datetime(2026, 7, 25, 0, 0, tzinfo=KST)
    assert first.action.effective_date.isoformat() == "2026-07-25"

    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        SecurityMasterRepository(connection).register_security(
            SECURITY_ID,
            market="KR",
            created_at=datetime(2020, 1, 1, tzinfo=KST),
        )
        repository = CorporateActionRepository(connection)
        for evidence in result.corporate_action_evidence:
            repository.merge(evidence)
        views = repository.actions_as_of(
            SECURITY_ID,
            query_as_of=datetime(2026, 7, 25, 0, 0, tzinfo=KST),
        )

    assert len(views) == 1
    assert views[0].quality is DataQualityStatus.FRESH
    assert views[0].ratio == Decimal("2")
    assert views[0].evidence_count == 2


@pytest.mark.asyncio
async def test_symbol_correction_and_real_rename_have_distinct_revision_semantics(
    tmp_path,
) -> None:
    class SymbolTransport(FixtureTransport):
        async def fetch(self, provider: str, *, as_of_date: datetime) -> ProviderPayload:
            base = await super().fetch(provider, as_of_date=as_of_date)
            return ProviderPayload(
                provider="KIS",
                source_record_id="kis:security-master:2026-07-24",
                revision=0,
                observed_at=base.observed_at,
                available_at=base.available_at,
                payload={
                    "prices": base.payload["prices"],
                    "symbol_events": [
                        {
                            "event_kind": "CORRECTION",
                            "instrument_symbol": "005930",
                            "provider_symbol": "005930",
                            "canonical_alias_id": "samsung-main",
                            "revision": 1,
                            "valid_from": "1975-06-11",
                        },
                        {
                            "event_kind": "RENAME",
                            "instrument_symbol": "005930",
                            "provider_symbol": "OLD930",
                            "rename_event_id": "rename-2026-1",
                            "valid_from": "1975-06-11",
                            "valid_to": "2026-07-01",
                        },
                        {
                            "event_kind": "RENAME",
                            "instrument_symbol": "005930",
                            "provider_symbol": "005930",
                            "rename_event_id": "rename-2026-1",
                            "valid_from": "2026-07-01",
                        },
                    ],
                },
            )

    provider = KISMarketDataProvider(
        transport=SymbolTransport(),
        instruments=(KISInstrument(security_id=SECURITY_ID, kis_symbol="005930"),),
        clock=lambda: INGESTED,
    )
    result = await provider.fetch_result(
        security_ids=(SECURITY_ID,),
        as_of_date=AS_OF,
    )

    correction, old_name, new_name = result.alias_evidence
    assert correction.source_record_id == "KIS:alias:samsung-main"
    assert correction.revision == 1
    assert old_name.source_record_id != new_name.source_record_id
    assert old_name.revision == new_name.revision == 0
    assert old_name.mapping.security_id == new_name.mapping.security_id == SECURITY_ID
    assert old_name.mapping.valid_to == datetime(2026, 7, 1, 0, 0, tzinfo=KST)
    assert new_name.mapping.valid_from == datetime(2026, 7, 1, 0, 0, tzinfo=KST)

    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        repository = SecurityMasterRepository(connection)
        repository.register_security(
            SECURITY_ID,
            market="KR",
            created_at=datetime(1975, 6, 11, tzinfo=KST),
        )
        for evidence in result.alias_evidence:
            repository.merge_alias(evidence)
        resolution = repository.resolve_symbol(
            "KIS",
            "005930",
            query_as_of=AS_OF,
        )

    assert resolution.security_id == SECURITY_ID


@pytest.mark.asyncio
async def test_stale_primary_is_explicit_and_never_relabelled_fresh() -> None:
    class StaleTransport(FixtureTransport):
        async def fetch(self, provider: str, *, as_of_date: datetime) -> ProviderPayload:
            payload = await super().fetch(provider, as_of_date=as_of_date)
            return ProviderPayload(
                provider=payload.provider,
                source_record_id=payload.source_record_id,
                revision=payload.revision,
                observed_at=payload.observed_at,
                available_at=payload.available_at,
                payload=payload.payload,
                quality=DataQualityStatus.STALE,
            )

    provider = KISMarketDataProvider(
        transport=StaleTransport(),
        instruments=(KISInstrument(security_id=SECURITY_ID, kis_symbol="005930"),),
        clock=lambda: INGESTED,
    )
    result = await provider.fetch_result(
        security_ids=(SECURITY_ID,),
        as_of_date=AS_OF,
    )

    assert result.snapshot.quality is DataQualityStatus.STALE
    assert result.snapshot.price_bars[0].quality is DataQualityStatus.STALE
    assert result.events[-1].kind is ProviderEventKind.STALE


@pytest.mark.asyncio
async def test_missing_requested_primary_symbol_is_partial_not_fabricated() -> None:
    provider = KISMarketDataProvider(
        transport=FixtureTransport(),
        instruments=(
            KISInstrument(security_id=SECURITY_ID, kis_symbol="005930"),
            KISInstrument(security_id=SECOND_SECURITY_ID, kis_symbol="000660"),
        ),
        clock=lambda: INGESTED,
    )

    result = await provider.fetch_result(
        security_ids=(SECURITY_ID, SECOND_SECURITY_ID),
        as_of_date=AS_OF,
    )

    assert len(result.snapshot.price_bars) == 1
    assert result.snapshot.price_bars[0].security_id == SECURITY_ID
    assert result.snapshot.quality is DataQualityStatus.PARTIAL
    assert result.events[-1].kind is ProviderEventKind.MISSING
    assert str(SECOND_SECURITY_ID.value) in result.events[-1].detail


@pytest.mark.asyncio
async def test_partial_price_row_is_rejected_with_explicit_event() -> None:
    class PartialTransport(FixtureTransport):
        async def fetch(self, provider: str, *, as_of_date: datetime) -> ProviderPayload:
            payload = await super().fetch(provider, as_of_date=as_of_date)
            row = dict(payload.payload["prices"][0])
            del row["close"]
            return ProviderPayload(
                provider=payload.provider,
                source_record_id=payload.source_record_id,
                revision=payload.revision,
                observed_at=payload.observed_at,
                available_at=payload.available_at,
                payload={"prices": [row]},
            )

    provider = KISMarketDataProvider(
        transport=PartialTransport(),
        instruments=(KISInstrument(security_id=SECURITY_ID, kis_symbol="005930"),),
        clock=lambda: INGESTED,
    )
    result = await provider.fetch_result(
        security_ids=(SECURITY_ID,),
        as_of_date=AS_OF,
    )

    assert result.snapshot.price_bars == ()
    assert result.snapshot.quality is DataQualityStatus.UNAVAILABLE
    assert any(event.kind is ProviderEventKind.PARTIAL for event in result.events)
    assert any(event.kind is ProviderEventKind.MISSING for event in result.events)


@pytest.mark.asyncio
async def test_unavailable_supplement_degrades_fresh_kis_to_partial_only() -> None:
    class SupplementOfflineTransport(FixtureTransport):
        async def fetch(self, provider: str, *, as_of_date: datetime) -> ProviderPayload:
            if provider == "KRX":
                raise ProviderTimeoutError("offline")
            return await super().fetch(provider, as_of_date=as_of_date)

    provider = KISMarketDataProvider(
        transport=SupplementOfflineTransport(),
        instruments=(
            KISInstrument(
                security_id=SECURITY_ID,
                kis_symbol="005930",
                provider_symbols={"KRX": "005930"},
            ),
        ),
        clock=lambda: INGESTED,
        supplement_providers=("KRX",),
        max_attempts=2,
    )
    result = await provider.fetch_result(
        security_ids=(SECURITY_ID,),
        as_of_date=AS_OF,
    )

    assert result.snapshot.price_bars[0].provider == "KIS"
    assert result.snapshot.quality is DataQualityStatus.PARTIAL
    assert result.events[-1].provider == "KRX"
    assert result.events[-1].quality is DataQualityStatus.UNAVAILABLE


@pytest.mark.asyncio
async def test_stale_supplement_degrades_fresh_kis_to_partial_only() -> None:
    class StaleSupplementTransport(FixtureTransport):
        async def fetch(self, provider: str, *, as_of_date: datetime) -> ProviderPayload:
            payload = await super().fetch("KIS", as_of_date=as_of_date)
            if provider == "KIS":
                return payload
            return ProviderPayload(
                provider="KRX",
                source_record_id="krx:daily:005930:2026-07-24",
                revision=0,
                observed_at=payload.observed_at,
                available_at=payload.available_at,
                payload={"prices": []},
                quality=DataQualityStatus.STALE,
            )

    provider = KISMarketDataProvider(
        transport=StaleSupplementTransport(),
        instruments=(
            KISInstrument(
                security_id=SECURITY_ID,
                kis_symbol="005930",
                provider_symbols={"KRX": "005930"},
            ),
        ),
        clock=lambda: INGESTED,
        supplement_providers=("KRX",),
    )

    result = await provider.fetch_result(
        security_ids=(SECURITY_ID,),
        as_of_date=AS_OF,
    )

    assert result.snapshot.quality is DataQualityStatus.PARTIAL
    assert any(event.kind is ProviderEventKind.STALE for event in result.events)


@pytest.mark.asyncio
async def test_official_listing_event_keeps_provider_and_kr_effective_instant(
    tmp_path,
) -> None:
    class ListingTransport(FixtureTransport):
        async def fetch(self, provider: str, *, as_of_date: datetime) -> ProviderPayload:
            base = await super().fetch("KIS", as_of_date=as_of_date)
            return ProviderPayload(
                provider=provider,
                source_record_id=f"{provider.lower()}:listing:2026-07-24",
                revision=2,
                observed_at=base.observed_at,
                available_at=base.available_at,
                payload={
                    "prices": base.payload["prices"] if provider == "KIS" else [],
                    "listing_events": []
                    if provider == "KIS"
                    else [
                        {
                            "instrument_symbol": "005930",
                            "provider_symbol": "005930",
                            "event_key": "listing-status-2026-07-25",
                            "status": "DELISTED",
                            "effective_date": "2026-07-25",
                        }
                    ],
                },
            )

    provider = KISMarketDataProvider(
        transport=ListingTransport(),
        instruments=(
            KISInstrument(
                security_id=SECURITY_ID,
                kis_symbol="005930",
                provider_symbols={"KRX": "005930"},
            ),
        ),
        clock=lambda: INGESTED,
        supplement_providers=("KRX",),
    )
    result = await provider.fetch_result(
        security_ids=(SECURITY_ID,),
        as_of_date=AS_OF,
    )

    assert len(result.listing_evidence) == 1
    evidence = result.listing_evidence[0]
    assert evidence.provider == "KRX"
    assert evidence.status is ListingStatus.DELISTED
    assert evidence.effective_at == datetime(2026, 7, 25, 0, 0, tzinfo=KST)
    assert evidence.source_record_id == "KRX:listing:listing-status-2026-07-25"
    assert evidence.revision == 2

    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        repository = SecurityMasterRepository(connection)
        repository.register_security(
            SECURITY_ID,
            market="KR",
            created_at=datetime(1975, 6, 11, tzinfo=KST),
        )
        repository.merge_listing_status(evidence)


@pytest.mark.asyncio
async def test_malformed_supplement_is_observable_without_discarding_kis_primary() -> None:
    class MalformedSupplementTransport(FixtureTransport):
        async def fetch(self, provider: str, *, as_of_date: datetime) -> ProviderPayload:
            if provider == "KIS":
                return await super().fetch(provider, as_of_date=as_of_date)
            return ProviderPayload(
                provider="KRX",
                source_record_id="krx:daily:malformed",
                revision=0,
                observed_at=datetime(2026, 7, 24, 15, 30, tzinfo=KST),
                available_at=datetime(2026, 7, 24, 16, 0, tzinfo=KST),
                payload={
                    "prices": [
                        {
                            "provider_symbol": "005930",
                            "trade_date": "bad",
                            "open": "70000",
                            "high": "71500",
                            "low": "69500",
                            "close": "71000",
                            "volume": "12345678",
                        }
                    ]
                },
            )

    provider = KISMarketDataProvider(
        transport=MalformedSupplementTransport(),
        instruments=(
            KISInstrument(
                security_id=SECURITY_ID,
                kis_symbol="005930",
                provider_symbols={"KRX": "005930"},
            ),
        ),
        clock=lambda: INGESTED,
        supplement_providers=("KRX",),
    )

    result = await provider.fetch_result(
        security_ids=(SECURITY_ID,),
        as_of_date=AS_OF,
    )

    assert [bar.provider for bar in result.snapshot.price_bars] == ["KIS"]
    assert result.snapshot.quality is DataQualityStatus.PARTIAL
    assert {payload.provider for payload in result.raw_payloads} == {"KIS", "KRX"}
    assert any(event.kind is ProviderEventKind.MALFORMED for event in result.events)


@pytest.mark.asyncio
async def test_semantically_invalid_supplement_does_not_discard_kis_primary() -> None:
    class InvalidOhlcSupplementTransport(FixtureTransport):
        async def fetch(self, provider: str, *, as_of_date: datetime) -> ProviderPayload:
            if provider == "KIS":
                return await super().fetch(provider, as_of_date=as_of_date)
            return ProviderPayload(
                provider="KRX",
                source_record_id="krx:daily:invalid-ohlc",
                revision=0,
                observed_at=datetime(2026, 7, 24, 15, 30, tzinfo=KST),
                available_at=datetime(2026, 7, 24, 16, 0, tzinfo=KST),
                payload={
                    "prices": [
                        {
                            "provider_symbol": "005930",
                            "trade_date": "2026-07-24",
                            "open": "70000",
                            "high": "69000",
                            "low": "69500",
                            "close": "71000",
                            "volume": "12345678",
                        }
                    ]
                },
            )

    provider = KISMarketDataProvider(
        transport=InvalidOhlcSupplementTransport(),
        instruments=(
            KISInstrument(
                security_id=SECURITY_ID,
                kis_symbol="005930",
                provider_symbols={"KRX": "005930"},
            ),
        ),
        clock=lambda: INGESTED,
        supplement_providers=("KRX",),
    )

    result = await provider.fetch_result(
        security_ids=(SECURITY_ID,),
        as_of_date=AS_OF,
    )

    assert [bar.provider for bar in result.snapshot.price_bars] == ["KIS"]
    assert result.snapshot.quality is DataQualityStatus.PARTIAL
    assert any(event.kind is ProviderEventKind.MALFORMED for event in result.events)


@pytest.mark.asyncio
async def test_invalid_supplement_action_is_malformed_without_discarding_primary() -> None:
    class InvalidActionTransport(FixtureTransport):
        async def fetch(self, provider: str, *, as_of_date: datetime) -> ProviderPayload:
            if provider == "KIS":
                return await super().fetch(provider, as_of_date=as_of_date)
            return ProviderPayload(
                provider="KRX",
                source_record_id="krx:action:invalid-split",
                revision=0,
                observed_at=datetime(2026, 7, 24, 15, 30, tzinfo=KST),
                available_at=datetime(2026, 7, 24, 16, 0, tzinfo=KST),
                payload={
                    "prices": [],
                    "corporate_actions": [
                        {
                            "provider_symbol": "005930",
                            "event_key": "krx:split:invalid",
                            "action_type": "SPLIT",
                            "effective_date": "2026-07-25",
                        }
                    ],
                },
            )

    provider = KISMarketDataProvider(
        transport=InvalidActionTransport(),
        instruments=(
            KISInstrument(
                security_id=SECURITY_ID,
                kis_symbol="005930",
                provider_symbols={"KRX": "005930"},
            ),
        ),
        clock=lambda: INGESTED,
        supplement_providers=("KRX",),
    )

    result = await provider.fetch_result(
        security_ids=(SECURITY_ID,),
        as_of_date=AS_OF,
    )

    assert [bar.provider for bar in result.snapshot.price_bars] == ["KIS"]
    assert result.snapshot.quality is DataQualityStatus.PARTIAL
    assert result.corporate_action_evidence == ()
    assert any(event.kind is ProviderEventKind.MALFORMED for event in result.events)


@pytest.mark.asyncio
async def test_unmatched_supplement_action_is_observable() -> None:
    class UnmatchedActionTransport(FixtureTransport):
        async def fetch(self, provider: str, *, as_of_date: datetime) -> ProviderPayload:
            if provider == "KIS":
                return await super().fetch(provider, as_of_date=as_of_date)
            return ProviderPayload(
                provider="KRX",
                source_record_id="krx:action:unmatched",
                revision=0,
                observed_at=datetime(2026, 7, 24, 15, 30, tzinfo=KST),
                available_at=datetime(2026, 7, 24, 16, 0, tzinfo=KST),
                payload={
                    "prices": [],
                    "corporate_actions": [
                        {
                            "provider_symbol": "999999",
                            "event_key": "krx:split:unmatched",
                            "action_type": "SPLIT",
                            "effective_date": "2026-07-25",
                            "ratio": "2",
                        }
                    ],
                },
            )

    provider = KISMarketDataProvider(
        transport=UnmatchedActionTransport(),
        instruments=(
            KISInstrument(
                security_id=SECURITY_ID,
                kis_symbol="005930",
                provider_symbols={"KRX": "005930"},
            ),
        ),
        clock=lambda: INGESTED,
        supplement_providers=("KRX",),
    )

    result = await provider.fetch_result(
        security_ids=(SECURITY_ID,),
        as_of_date=AS_OF,
    )

    assert result.corporate_action_evidence == ()
    assert result.snapshot.quality is DataQualityStatus.PARTIAL
    assert any(event.kind is ProviderEventKind.UNMATCHED for event in result.events)


@pytest.mark.asyncio
async def test_supplement_prices_are_not_promoted_when_kis_primary_is_unavailable() -> None:
    class PrimaryOfflineTransport:
        async def fetch(self, provider: str, *, as_of_date: datetime) -> ProviderPayload:
            if provider == "KIS":
                raise ProviderTimeoutError("offline")
            return ProviderPayload(
                provider="KRX",
                source_record_id="krx:daily:005930:2026-07-24",
                revision=0,
                observed_at=datetime(2026, 7, 24, 15, 30, tzinfo=KST),
                available_at=datetime(2026, 7, 24, 16, 0, tzinfo=KST),
                payload={
                    "prices": [
                        {
                            "provider_symbol": "005930",
                            "trade_date": "2026-07-24",
                            "open": "70000",
                            "high": "71500",
                            "low": "69500",
                            "close": "71000",
                            "volume": "12345678",
                        }
                    ]
                },
            )

    provider = KISMarketDataProvider(
        transport=PrimaryOfflineTransport(),
        instruments=(
            KISInstrument(
                security_id=SECURITY_ID,
                kis_symbol="005930",
                provider_symbols={"KRX": "005930"},
            ),
        ),
        clock=lambda: INGESTED,
        supplement_providers=("KRX",),
        max_attempts=1,
    )

    result = await provider.fetch_result(
        security_ids=(SECURITY_ID,),
        as_of_date=AS_OF,
    )

    assert result.snapshot.quality is DataQualityStatus.UNAVAILABLE
    assert result.snapshot.price_bars == ()
    assert [payload.provider for payload in result.raw_payloads] == ["KRX"]


@pytest.mark.asyncio
async def test_negative_alias_revision_is_malformed_without_discarding_primary() -> None:
    class InvalidAliasTransport(FixtureTransport):
        async def fetch(self, provider: str, *, as_of_date: datetime) -> ProviderPayload:
            if provider == "KIS":
                return await super().fetch(provider, as_of_date=as_of_date)
            return ProviderPayload(
                provider="KRX",
                source_record_id="krx:alias:invalid-revision",
                revision=0,
                observed_at=datetime(2026, 7, 24, 15, 30, tzinfo=KST),
                available_at=datetime(2026, 7, 24, 16, 0, tzinfo=KST),
                payload={
                    "prices": [],
                    "symbol_events": [
                        {
                            "event_kind": "CORRECTION",
                            "instrument_symbol": "005930",
                            "provider_symbol": "005930",
                            "canonical_alias_id": "samsung-main",
                            "revision": -1,
                            "valid_from": "1975-06-11",
                        }
                    ],
                },
            )

    provider = KISMarketDataProvider(
        transport=InvalidAliasTransport(),
        instruments=(
            KISInstrument(
                security_id=SECURITY_ID,
                kis_symbol="005930",
                provider_symbols={"KRX": "005930"},
            ),
        ),
        clock=lambda: INGESTED,
        supplement_providers=("KRX",),
    )

    result = await provider.fetch_result(
        security_ids=(SECURITY_ID,),
        as_of_date=AS_OF,
    )

    assert [bar.provider for bar in result.snapshot.price_bars] == ["KIS"]
    assert result.snapshot.quality is DataQualityStatus.PARTIAL
    assert result.alias_evidence == ()
    assert any(event.kind is ProviderEventKind.MALFORMED for event in result.events)


@pytest.mark.asyncio
async def test_supplement_price_is_not_promoted_for_symbol_missing_from_kis() -> None:
    class PerSymbolMissingTransport(FixtureTransport):
        async def fetch(self, provider: str, *, as_of_date: datetime) -> ProviderPayload:
            if provider == "KIS":
                return await super().fetch(provider, as_of_date=as_of_date)
            return ProviderPayload(
                provider="KRX",
                source_record_id="krx:daily:000660:2026-07-24",
                revision=0,
                observed_at=datetime(2026, 7, 24, 15, 30, tzinfo=KST),
                available_at=datetime(2026, 7, 24, 16, 0, tzinfo=KST),
                payload={
                    "prices": [
                        {
                            "provider_symbol": "000660",
                            "trade_date": "2026-07-24",
                            "open": "200000",
                            "high": "205000",
                            "low": "198000",
                            "close": "204000",
                            "volume": "1234567",
                        }
                    ]
                },
            )

    provider = KISMarketDataProvider(
        transport=PerSymbolMissingTransport(),
        instruments=(
            KISInstrument(security_id=SECURITY_ID, kis_symbol="005930"),
            KISInstrument(
                security_id=SECOND_SECURITY_ID,
                kis_symbol="000660",
                provider_symbols={"KRX": "000660"},
            ),
        ),
        clock=lambda: INGESTED,
        supplement_providers=("KRX",),
    )

    result = await provider.fetch_result(
        security_ids=(SECURITY_ID, SECOND_SECURITY_ID),
        as_of_date=AS_OF,
    )

    assert [(bar.security_id, bar.provider) for bar in result.snapshot.price_bars] == [
        (SECURITY_ID, "KIS")
    ]
    assert result.snapshot.quality is DataQualityStatus.PARTIAL
    assert any(event.kind is ProviderEventKind.MISSING for event in result.events)
