from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from typing import Mapping, cast
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from prism_core.data import DataQualityStatus, SecurityId
from prism_core.data.providers.kis import KISInstrument, KISMarketDataProvider
from prism_core.data.providers.kis_http import (
    DAILY_PRICE_PATH,
    TOKEN_PATH,
    KISHTTPTransport,
    KISMarketDataCredentials,
)


KST = ZoneInfo("Asia/Seoul")
SECURITY_ID = SecurityId(value=UUID("00000000-0000-0000-0000-000000000081"))

pytestmark = [
    pytest.mark.live_kis,
    pytest.mark.skipif(
        os.environ.get("PRISM_RUN_KIS_LIVE") != "1",
        reason="set PRISM_RUN_KIS_LIVE=1 for the authorized KIS market-data smoke",
    ),
]


@pytest.mark.asyncio
async def test_live_kis_daily_quote_normalizes_without_account_or_broker_effect() -> None:
    credentials = KISMarketDataCredentials.from_env()
    as_of = datetime.now(tz=KST)
    if as_of.weekday() < 5 and (
        as_of.hour < 15 or (as_of.hour == 15 and as_of.minute < 31)
    ):
        pytest.skip("KIS daily smoke requires the current KRX daily bar to be complete")

    transport = KISHTTPTransport(
        credentials=credentials,
        symbols=("005930",),
        timeout_seconds=10.0,
        max_response_bytes=1_000_000,
        min_request_interval_seconds=0.1,
    )
    provider = KISMarketDataProvider(
        transport=transport,
        instruments=(KISInstrument(security_id=SECURITY_ID, kis_symbol="005930"),),
        clock=lambda: datetime.now(tz=KST),
        max_attempts=2,
    )

    result = await provider.fetch_result(
        security_ids=(SECURITY_ID,),
        as_of_date=as_of,
    )

    payload = result.raw_payloads[0]
    transport_evidence = cast(
        list[Mapping[str, object]], payload.payload["transport_evidence"]
    )
    assert [item["endpoint"] for item in transport_evidence] == [
        TOKEN_PATH,
        DAILY_PRICE_PATH,
    ]
    assert all(item["status_code"] == 200 for item in transport_evidence)
    assert result.events == ()
    assert result.snapshot.quality is DataQualityStatus.FRESH
    assert len(result.raw_payloads) == 1
    assert len(result.snapshot.price_bars) == 1
    bar = result.snapshot.price_bars[0]
    price_rows = cast(list[Mapping[str, object]], payload.payload["prices"])
    assert set(payload.payload) == {"prices", "transport_evidence"}
    assert set(price_rows[0]) == {
        "provider_symbol",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "volume",
    }
    assert bar.provider == "KIS"
    assert bar.provider_symbol == "005930"
    expected_session = as_of.astimezone(KST).date()
    while expected_session.weekday() >= 5:
        expected_session -= timedelta(days=1)
    assert bar.bar_start.astimezone(KST).date() == expected_session
    assert bar.timing.observed_at < bar.timing.available_at
    assert bar.timing.available_at <= bar.timing.as_of_date <= bar.timing.ingested_at
    assert bar.source_hash == transport_evidence[-1]["raw_payload_hash"]
    assert len(bar.source_hash) == 64

    if os.environ.get("PRISM_KIS_SMOKE_EVIDENCE") == "1":
        print(
            json.dumps(
                {
                    "endpoints": transport_evidence,
                    "provider": bar.provider,
                    "provider_symbol": bar.provider_symbol,
                    "schema_fields": sorted(price_rows[0]),
                    "quality": result.snapshot.quality.value,
                    "observed_at": bar.timing.observed_at.isoformat(),
                    "available_at": bar.timing.available_at.isoformat(),
                    "ingested_at": bar.timing.ingested_at.isoformat(),
                    "as_of_date": bar.timing.as_of_date.isoformat(),
                    "source_hash": bar.source_hash,
                },
                sort_keys=True,
            )
        )
