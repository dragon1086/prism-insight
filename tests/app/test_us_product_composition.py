from __future__ import annotations

from dataclasses import replace
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from cores.llm.fakes import FakeLLMBackend
from cores.llm.ports import LLMResult
from prism_app.market_snapshot_composer import USPITEvidence, USProductSnapshotComposer
from prism_app.product_composition import ProductRunConfig, run_us_shadow_product
from prism_core.data import (
    DataQualityStatus,
    EvidenceItem,
    ObservationTime,
    SecurityId,
)
from prism_core.data.providers.fmp import FMPInstrument, FMPMarketDataProvider
from prism_core.data.providers.fmp_models import FMPApiKey, FMPRequest, FMPResponseEnvelope
from prism_core.runtime.settings import ProductMode, RuntimeSettings
from prism_core.strategies.contracts import StrategyId


UTC = timezone.utc
ET = ZoneInfo("America/New_York")
AS_OF = datetime(2026, 7, 24, 21, 0, tzinfo=UTC)
STOCK_ID = SecurityId(value=UUID("00000000-0000-0000-0000-000000000181"))
BENCHMARK_ID = SecurityId(value=UUID("00000000-0000-0000-0000-000000000182"))


def _sessions(count: int):
    result = []
    cursor = AS_OF.astimezone(ET).date()
    while len(result) < count:
        if cursor.weekday() < 5:
            result.append(cursor)
        cursor -= timedelta(days=1)
    return list(reversed(result))


class USPriceTransport:
    async def execute(
        self, request: FMPRequest, *, api_key: FMPApiKey
    ) -> FMPResponseEnvelope:
        rows = []
        for index, session in enumerate(_sessions(220)):
            for symbol, base, step in (("AAPL", 150, 1), ("SPY", 500, 1)):
                close = Decimal(base) + Decimal(index * step) / Decimal("10")
                rows.append(
                    {
                        "symbol": symbol,
                        "date": session.isoformat(),
                        "rawOpen": str(close - Decimal("0.5")),
                        "rawHigh": str(close + Decimal("1")),
                        "rawLow": str(close - Decimal("1")),
                        "rawClose": str(close),
                        "rawVolume": str(1_000_000 + index * 1_000),
                    }
                )
        observed = datetime.combine(AS_OF.astimezone(ET).date(), time(16), tzinfo=ET)
        return FMPResponseEnvelope(
            status_code=200,
            source_record_id="fmp:us-pair:fixture",
            revision=0,
            observed_at=observed,
            available_at=observed + timedelta(minutes=1),
            payload={
                "data": rows,
                "pagination": {
                    "page": 1,
                    "totalPages": 1,
                    "hasMore": False,
                    "nextPage": None,
                },
            },
            quality=DataQualityStatus.FRESH,
        )


def _provider() -> FMPMarketDataProvider:
    return FMPMarketDataProvider(
        transport=USPriceTransport(),
        api_key=FMPApiKey("fixture-key"),
        instruments=(
            FMPInstrument(
                security_id=STOCK_ID,
                fmp_symbol="AAPL",
                valid_from=datetime(1980, 12, 12, tzinfo=UTC),
            ),
            FMPInstrument(
                security_id=BENCHMARK_ID,
                fmp_symbol="SPY",
                valid_from=datetime(1993, 1, 29, tzinfo=UTC),
            ),
        ),
        clock=lambda: AS_OF + timedelta(minutes=1),
        max_attempts=1,
        max_pages=1,
        max_requests=1,
    )


def _evidence() -> USPITEvidence:
    return USPITEvidence(
        observed_at=AS_OF - timedelta(minutes=2),
        available_at=AS_OF - timedelta(minutes=1),
        ingested_at=AS_OF,
        observations={
            "catalyst_recency_sessions": Decimal("1"),
            "regime_swing_compatibility": Decimal("65"),
            "earnings_current": Decimal("10"),
            "earnings_previous": Decimal("8"),
            "industry_leadership": Decimal("72"),
            "regime_trend_compatibility": Decimal("68"),
        },
        evidence_payload={
            "agentnews:us:fixture": {
                "source": "AgentNews US",
                "available_at": (AS_OF - timedelta(minutes=1)).isoformat(),
            },
            "fmp:income:AAPL:2025-09-27": {
                "source": "FMP stable/income-statement",
                "available_at": (AS_OF - timedelta(minutes=1)).isoformat(),
            },
            "fmp:price:AAPL:SPY:2026-07-24": {
                "source": "FMP daily market data",
                "latest_completed_session": "2026-07-24",
            },
        },
    )


def _coverage_item(security_id: SecurityId, symbol: str, suffix: int) -> EvidenceItem:
    return EvidenceItem.model_validate(
        {
            "security_id": security_id,
            "provider": "FMP",
            "provider_symbol": symbol,
            "source_record_id": f"fmp:split-coverage:{symbol}:fixture",
            "source_hash": "c" * 64,
            "revision": 0,
            "timing": ObservationTime(
                observed_at=AS_OF - timedelta(hours=1),
                available_at=AS_OF,
                ingested_at=AS_OF,
                as_of_date=AS_OF,
            ),
            "quality": DataQualityStatus.FRESH,
            "evidence_id": UUID(f"00000000-0000-0000-0000-{suffix:012d}"),
            "kind": "corporate_action_coverage",
            "title": f"FMP split coverage {symbol}",
            "source_url": "https://financialmodelingprep.com/stable/splits-calendar",
            "content_hash": "c" * 64,
        }
    )


class CoveredUSProvider:
    async def fetch_result(self, **kwargs):
        result = await _provider().fetch_result(**kwargs)
        return replace(
            result,
            snapshot=result.snapshot.model_copy(
                update={
                    "evidence": (
                        _coverage_item(STOCK_ID, "AAPL", 183),
                        _coverage_item(BENCHMARK_ID, "SPY", 184),
                    )
                }
            ),
        )


@pytest.mark.asyncio
async def test_us_composer_builds_fmp_strategy_inputs_and_us_leadership() -> None:
    snapshot = await USProductSnapshotComposer(
        provider=_provider(),
        stock_id=STOCK_ID,
        benchmark_id=BENCHMARK_ID,
        stock_symbol="AAPL",
        benchmark_symbol="SPY",
        evidence=_evidence(),
    ).acquire(as_of=AS_OF)

    assert snapshot.source_payload["provider"] == "FMP"
    assert snapshot.source_payload["evidence_level"] == "STATIC_PIT_OVERRIDE"
    assert set(snapshot.source_payload["source_as_of"]) == {
        "AgentNews",
        "FMP fundamentals",
        "FMP price",
    }
    assert snapshot.leadership_snapshot.market.value == "US"
    assert set(snapshot.strategy_inputs) == {StrategyId.SWING_V1, StrategyId.TREND_V1}
    assert all(
        item.feature_snapshot.market.value == "US"
        for item in snapshot.strategy_inputs.values()
    )
    assert snapshot.source_payload["corporate_action_coverage_status"] == "UNVERIFIED"
    assert snapshot.source_payload["corporate_action_coverage_scope"] == "UNVERIFIED"
    assert all(
        "price.raw_corporate_action_coverage_missing" in item.hard_vetoes
        for item in snapshot.strategy_inputs.values()
    )


@pytest.mark.asyncio
async def test_us_composer_accepts_both_symbol_split_coverage_without_veto() -> None:
    snapshot = await USProductSnapshotComposer(
        provider=CoveredUSProvider(),
        stock_id=STOCK_ID,
        benchmark_id=BENCHMARK_ID,
        stock_symbol="AAPL",
        benchmark_symbol="SPY",
        evidence=_evidence(),
    ).acquire(as_of=AS_OF)

    assert snapshot.source_payload["corporate_action_coverage_status"] == "VERIFIED"
    assert snapshot.source_payload["corporate_action_coverage_scope"] == "SPLITS_ONLY"
    assert snapshot.source_payload["corporate_action_covered_symbols"] == ["AAPL", "SPY"]
    assert all(
        item.scenario_input_pack is not None
        and "price.raw_corporate_action_coverage_missing" not in item.hard_vetoes
        for item in snapshot.strategy_inputs.values()
    )


@pytest.mark.asyncio
async def test_us_product_composition_persists_and_reads_back(tmp_path) -> None:
    composer = USProductSnapshotComposer(
        provider=_provider(),
        stock_id=STOCK_ID,
        benchmark_id=BENCHMARK_ID,
        stock_symbol="AAPL",
        benchmark_symbol="SPY",
        evidence=_evidence(),
    )
    backend = FakeLLMBackend([LLMResult(text="not-json"), LLMResult(text="not-json")])
    settings = RuntimeSettings(
        product_mode=ProductMode.SHADOW,
        research_db_path=tmp_path / "research.sqlite",
        ops_db_path=tmp_path / "ops.sqlite",
    )

    result = await run_us_shadow_product(
        composer=composer,
        backend=backend,
        settings=settings,
        config=ProductRunConfig(
            evaluated_at=AS_OF,
            run_type="daily-close",
            model_id="fixture-model",
            model_version="fixture-v1",
            code_version="test-tree",
            owner_id="fixture-us-runner",
        ),
        output_path=tmp_path / "us-shadow.md",
    )

    assert result.analysis.market.value == "US"
    assert len(backend.calls) == 2
    assert result.readback.analysis == result.analysis
