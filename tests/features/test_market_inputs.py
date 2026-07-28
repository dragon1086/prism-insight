from datetime import datetime, time, timedelta
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from prism_core.data import DataQualityStatus, SecurityId
from prism_core.data.providers.kis import (
    KISInstrument,
    KISMarketDataProvider,
    ProviderPayload,
)
from prism_core.data.quality import QualityDisposition
from prism_core.features.market_inputs import build_feature_computation_input
from prism_core.features.service import NumericObservation, PriceBasis, QuantFeatureService
from prism_core.strategies.contracts import Market, StrategyId
from prism_core.strategies.registry import DEFAULT_STRATEGY_REGISTRY


KST = ZoneInfo("Asia/Seoul")
AS_OF = datetime(2026, 7, 24, 18, 0, tzinfo=KST)
INGESTED = datetime(2026, 7, 24, 18, 1, tzinfo=KST)
STOCK_ID = SecurityId(value=UUID("00000000-0000-0000-0000-000000000081"))
BENCHMARK_ID = SecurityId(value=UUID("00000000-0000-0000-0000-000000000082"))


def _sessions(count: int) -> list[datetime]:
    values: list[datetime] = []
    cursor = AS_OF
    while len(values) < count:
        if cursor.weekday() < 5:
            values.append(cursor)
        cursor -= timedelta(days=1)
    return list(reversed(values))


class HistoricalTransport:
    async def fetch(self, provider: str, *, as_of_date: datetime) -> ProviderPayload:
        rows = []
        for index, session in enumerate(_sessions(25)):
            for symbol, base in (("005930", 70000), ("069500", 40000)):
                close = base + index * (500 if symbol == "005930" else 100)
                rows.append(
                    {
                        "provider_symbol": symbol,
                        "trade_date": session.date().isoformat(),
                        "open": str(close - 100),
                        "high": str(close + 200),
                        "low": str(close - 200),
                        "close": str(close),
                        "volume": str(10_000_000 + index * 100_000),
                    }
                )
        return ProviderPayload(
            provider="KIS",
            source_record_id="kis:historical:fixture",
            revision=0,
            observed_at=datetime.combine(AS_OF.date(), time(15, 30), tzinfo=KST),
            available_at=datetime.combine(AS_OF.date(), time(15, 31), tzinfo=KST),
            payload={"prices": rows},
        )


@pytest.mark.asyncio
async def test_market_snapshot_adapts_to_real_feature_service_without_fabrication() -> None:
    provider = KISMarketDataProvider(
        transport=HistoricalTransport(),
        instruments=(
            KISInstrument(security_id=STOCK_ID, kis_symbol="005930"),
            KISInstrument(security_id=BENCHMARK_ID, kis_symbol="069500"),
        ),
        clock=lambda: INGESTED,
    )
    snapshot = await provider.fetch_snapshot(
        security_ids=(STOCK_ID, BENCHMARK_ID), as_of_date=AS_OF
    )
    observations = (
        NumericObservation(
            name="catalyst_recency_sessions", value=Decimal("2"), available_at=AS_OF
        ),
        NumericObservation(
            name="regime_swing_compatibility", value=Decimal("70"), available_at=AS_OF
        ),
    )

    inputs = build_feature_computation_input(
        snapshot=snapshot,
        market=Market.KR,
        security_id=STOCK_ID,
        benchmark_security_id=BENCHMARK_ID,
        price_basis=PriceBasis.RAW,
        observations=observations,
        field_quality={
            "calendar": DataQualityStatus.FRESH,
            "evidence": DataQualityStatus.FRESH,
            "fundamental": DataQualityStatus.FRESH,
            "price": DataQualityStatus.FRESH,
            "regime": DataQualityStatus.FRESH,
        },
    )
    feature = QuantFeatureService(feature_version="features.v1").compute(
        DEFAULT_STRATEGY_REGISTRY.get(StrategyId.SWING_V1), inputs
    )

    assert len(inputs.prices) == len(inputs.benchmark_points) == 25
    assert inputs.prices[0].observed_at == AS_OF.replace(hour=15, minute=30)
    assert inputs.quality_decision.disposition is QualityDisposition.ACCEPT
    assert feature.data_snapshot_id == snapshot.snapshot_id
    assert feature.data_quality_status is DataQualityStatus.FRESH
    assert "swing_v1.relative_strength_20d" in {item.name for item in feature.values}


@pytest.mark.asyncio
async def test_snapshot_quality_cannot_be_overridden_to_fresh() -> None:
    provider = KISMarketDataProvider(
        transport=HistoricalTransport(),
        instruments=(
            KISInstrument(security_id=STOCK_ID, kis_symbol="005930"),
            KISInstrument(security_id=BENCHMARK_ID, kis_symbol="069500"),
        ),
        clock=lambda: INGESTED,
    )
    snapshot = await provider.fetch_snapshot(
        security_ids=(STOCK_ID, BENCHMARK_ID), as_of_date=AS_OF
    )
    stale = snapshot.model_copy(update={"quality": DataQualityStatus.STALE})

    with pytest.raises(ValueError, match="cannot override snapshot quality"):
        build_feature_computation_input(
            snapshot=stale,
            market=Market.KR,
            security_id=STOCK_ID,
            benchmark_security_id=BENCHMARK_ID,
            price_basis=PriceBasis.RAW,
            observations=(),
            field_quality={
                "calendar": DataQualityStatus.FRESH,
                "evidence": DataQualityStatus.FRESH,
                "price": DataQualityStatus.FRESH,
                "regime": DataQualityStatus.FRESH,
            },
        )
