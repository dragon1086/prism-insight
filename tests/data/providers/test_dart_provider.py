from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from prism_core.data import DataQualityStatus, SecurityId
from prism_core.data.providers.dart import (
    DARTFilingRecord,
    DARTProvider,
    DARTTransportError,
    UnavailableDARTAdapter,
)


AS_OF = datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc)
ACCEPTED = datetime(2026, 3, 20, 1, 23, 45, tzinfo=timezone.utc)
STOCK_ID = SecurityId(value=UUID("00000000-0000-0000-0000-000000000091"))


class Transport:
    def __init__(self, records: tuple[DARTFilingRecord, ...]) -> None:
        self.records = records
        self.calls: list[tuple[str, datetime]] = []

    async def fetch_filings(
        self, *, stock_code: str, as_of: datetime
    ) -> tuple[DARTFilingRecord, ...]:
        self.calls.append((stock_code, as_of))
        return self.records


def record(**changes: object) -> DARTFilingRecord:
    values: dict[str, object] = {
        "stock_code": "214450",
        "receipt_no": "20260320000123",
        "report_name": "사업보고서",
        "metric": "net_income",
        "period_start": date(2025, 1, 1),
        "period_end": date(2025, 12, 31),
        "value": Decimal("12500000000"),
        "unit": "KRW",
        "accepted_at": ACCEPTED,
        "ingested_at": AS_OF,
        "source_hash": "a" * 64,
        "quality": DataQualityStatus.FRESH,
    }
    values.update(changes)
    return DARTFilingRecord(**values)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_dart_preserves_official_acceptance_clock_and_identity() -> None:
    transport = Transport((record(),))

    result = await DARTProvider(transport=transport).fetch(
        stock_code="214450", security_id=STOCK_ID, as_of=AS_OF
    )

    assert result.quality is DataQualityStatus.FRESH
    assert result.fundamentals[0].timing.available_at == ACCEPTED
    assert result.fundamentals[0].timing.as_of_date == AS_OF
    assert result.fundamentals[0].security_id == STOCK_ID
    assert result.fundamentals[0].provider == "DART"
    assert result.evidence_items[0].source_record_id == "20260320000123"
    assert result.call_evidence[0]["status"] == "SUCCESS"
    assert transport.calls == [("214450", AS_OF)]


@pytest.mark.asyncio
async def test_dart_filters_future_records_and_retries_sanitized_failures() -> None:
    future = record(
        accepted_at=AS_OF.replace(hour=9),
        ingested_at=AS_OF.replace(hour=9, minute=1),
        receipt_no="20260729000999",
    )
    result = await DARTProvider(transport=Transport((future,))).fetch(
        stock_code="214450", security_id=STOCK_ID, as_of=AS_OF
    )
    assert result.fundamentals == ()
    assert result.quality is DataQualityStatus.UNAVAILABLE
    assert result.issues == ("NO_PIT_AVAILABLE_DART_FUNDAMENTALS",)

    class Broken:
        calls = 0

        async def fetch_filings(self, **_: object) -> tuple[DARTFilingRecord, ...]:
            self.calls += 1
            raise DARTTransportError("secret-bearing upstream detail")

    broken = Broken()
    failed = await DARTProvider(transport=broken, max_attempts=2).fetch(
        stock_code="214450", security_id=STOCK_ID, as_of=AS_OF
    )
    assert broken.calls == 2
    assert failed.quality is DataQualityStatus.UNAVAILABLE
    assert failed.issues == ("DART_FETCH_FAILED",)
    assert "secret" not in repr(failed)


@pytest.mark.asyncio
async def test_unavailable_dart_adapter_is_explicit_and_makes_no_claim_of_coverage() -> None:
    result = await UnavailableDARTAdapter().fetch(
        stock_code="214450", security_id=STOCK_ID, as_of=AS_OF
    )

    assert result.fundamentals == ()
    assert result.evidence_items == ()
    assert result.quality is DataQualityStatus.UNAVAILABLE
    assert result.issues == ("DART_CAPABILITY_UNAVAILABLE",)
    assert result.call_evidence == ()
