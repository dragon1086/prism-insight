"""Explicit opt-in live FMP market-data smoke test.

This test performs only bounded public market-data GETs. It never reads account
state and cannot place, modify, or cancel broker orders.
"""

from __future__ import annotations

import os
from datetime import datetime, time
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from prism_core.data import DataQualityStatus, SecurityId
from prism_core.data.providers.fmp import (
    CapabilityStatus,
    FMPInstrument,
    FMPMarketDataProvider,
    FMP_RAW_EOD_PATH,
)
from prism_core.data.providers.fmp_http import FMPHTTPTransport, load_fmp_api_key_from_env


pytestmark = pytest.mark.live_market_data
UTC = ZoneInfo("UTC")
NEW_YORK = ZoneInfo("America/New_York")


def _live_as_of() -> datetime:
    now = datetime.now(tz=UTC)
    market_now = now.astimezone(NEW_YORK)
    if market_now.weekday() < 5 and market_now.time() < time(16, 30):
        pytest.skip("FMP live smoke requires the US close plus availability delay")
    return now


@pytest.mark.asyncio
async def test_fmp_live_capability_and_normalized_raw_price() -> None:
    if os.environ.get("RUN_FMP_LIVE_SMOKE") != "1":
        pytest.skip("set RUN_FMP_LIVE_SMOKE=1 to enable the bounded FMP live smoke")

    api_key = load_fmp_api_key_from_env()
    as_of = _live_as_of()
    security_id = SecurityId(value=UUID("00000000-0000-0000-0000-000000000095"))
    transport = FMPHTTPTransport()
    provider = FMPMarketDataProvider(
        transport=transport,
        api_key=api_key,
        instruments=(
            FMPInstrument(
                security_id=security_id,
                fmp_symbol="AAPL",
                valid_from=datetime(1980, 12, 12, tzinfo=UTC),
            ),
        ),
        clock=lambda: datetime.now(tz=UTC),
        max_attempts=2,
    )

    capability = await provider.probe_capability(as_of_date=as_of)
    capability_evidence = transport.evidence
    assert capability.status is CapabilityStatus.SUPPORTED
    assert capability.response is not None
    assert capability.response.status_code == 200
    assert capability.response.payload == {"entitled": True}
    assert len(capability_evidence) == 1
    assert capability_evidence[0].endpoint == FMP_RAW_EOD_PATH
    assert capability_evidence[0].provider_version == "stable"
    assert capability_evidence[0].status_code == 200
    assert capability_evidence[0].latency_ms >= 0
    assert len(capability_evidence[0].raw_payload_hash) == 64

    result = await provider.fetch_result(
        security_ids=(security_id,),
        as_of_date=as_of,
    )
    price_evidence = transport.evidence
    assert result.snapshot.quality is DataQualityStatus.FRESH
    assert len(result.snapshot.price_bars) >= 1
    latest = result.snapshot.price_bars[-1]
    assert latest.security_id == security_id
    assert latest.provider == "fmp"
    assert latest.provider_symbol == "AAPL"
    assert latest.raw_close > 0
    assert latest.raw_volume >= 0
    assert latest.adjusted_close is None
    assert latest.adjustment_as_of is None
    assert latest.timing.observed_at < latest.timing.available_at <= as_of
    assert len(price_evidence) == 1
    assert price_evidence[0].endpoint == FMP_RAW_EOD_PATH
    assert price_evidence[0].status_code == 200
    assert price_evidence[0].request_correlation
    assert len(price_evidence[0].raw_payload_hash) == 64
