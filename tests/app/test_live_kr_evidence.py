from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Context, Decimal, ROUND_HALF_EVEN, ROUND_UP, localcontext
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from prism_app.live_kr_evidence import (
    FMPHTTPJSONResponse,
    FMPIncomeStatementClient,
    LiveKREvidenceProvider,
    LiveUSEvidenceProvider,
    _relative_state_score,
    _return,
    agentnews_hard_vetoes,
)
from prism_app.kr_evidence_composer import KREvidenceComposer
from prism_app.market_snapshot_composer import KRProductSnapshotComposer
from prism_core.data import DataQualityStatus, SecurityId
from prism_core.data.providers.agentnews_models import AgentNewsBoard, AgentNewsSnapshot
from prism_core.data.providers.kis import KISInstrument, KISMarketDataProvider, ProviderPayload
from prism_core.data.providers.dart import UnavailableDARTAdapter
from prism_core.data.providers.kind import UnavailableKINDAdapter
from prism_core.strategies import ScenarioInputStatus, StrategyId


KST = ZoneInfo("Asia/Seoul")
UTC = ZoneInfo("UTC")
AS_OF = datetime(2026, 7, 26, 21, 0, tzinfo=KST)
STOCK_ID = SecurityId(value=UUID("00000000-0000-0000-0000-000000000081"))
BENCHMARK_ID = SecurityId(value=UUID("00000000-0000-0000-0000-000000000082"))


class PriceTransport:
    async def fetch(self, provider: str, *, as_of_date: datetime) -> ProviderPayload:
        rows = []
        sessions = []
        cursor = datetime(2026, 7, 24, tzinfo=KST).date()
        while len(sessions) < 260:
            if cursor.weekday() < 5:
                sessions.append(cursor)
            cursor -= timedelta(days=1)
        sessions.reverse()
        for index, session in enumerate(sessions):
            rows.extend(
                [
                    {
                        "provider_symbol": "005930",
                        "trade_date": session.isoformat(),
                        "open": str(70000 + index * 100),
                        "high": str(70200 + index * 100),
                        "low": str(69900 + index * 100),
                        "close": str(70100 + index * 100),
                        "volume": str(1_000_000 + index * 10_000),
                    },
                    {
                        "provider_symbol": "069500",
                        "trade_date": session.isoformat(),
                        "open": str(30000 + index * 20),
                        "high": str(30100 + index * 20),
                        "low": str(29900 + index * 20),
                        "close": str(30050 + index * 20),
                        "volume": str(2_000_000 + index * 10_000),
                    },
                ]
            )
        return ProviderPayload(
            provider="KIS",
            source_record_id="kis:weekend:latest-completed-session",
            revision=0,
            observed_at=datetime(2026, 7, 24, 15, 30, tzinfo=KST),
            available_at=datetime(2026, 7, 24, 15, 31, tzinfo=KST),
            payload={"prices": rows},
            quality=DataQualityStatus.FRESH,
        )


class FMPRequester:
    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []

    async def request(self, **kwargs: object) -> FMPHTTPJSONResponse:
        self.requests.append(kwargs)
        return FMPHTTPJSONResponse(
            status_code=200,
            body=json.dumps(
                [
                    {
                        "symbol": "005930.KS",
                        "date": "2024-12-31",
                        "acceptedDate": "2026-05-15 09:00:00",
                        "netIncome": 27_000_000_000_000,
                    },
                    {
                        "symbol": "005930.KS",
                        "date": "2025-12-31",
                        "acceptedDate": "2025-12-30 19:00:00",
                        "netIncome": 31_000_000_000_000,
                    },
                    {
                        "symbol": "005930.KS",
                        "date": "2024-12-31",
                        "acceptedDate": "2025-01-31 09:00:00",
                        "netIncome": 26_000_000_000_000,
                    },
                ]
            ).encode(),
            received_at=AS_OF.astimezone(UTC),
        )


class StaleFMPRequester(FMPRequester):
    async def request(self, **kwargs: object) -> FMPHTTPJSONResponse:
        self.requests.append(kwargs)
        return FMPHTTPJSONResponse(
            status_code=200,
            body=json.dumps(
                [
                    {
                        "symbol": "005930.KS",
                        "date": "2023-12-31",
                        "acceptedDate": "2024-03-20 09:00:00",
                        "netIncome": 20_000_000_000_000,
                    },
                    {
                        "symbol": "005930.KS",
                        "date": "2022-12-31",
                        "acceptedDate": "2023-03-20 09:00:00",
                        "netIncome": 18_000_000_000_000,
                    },
                ]
            ).encode(),
            received_at=AS_OF.astimezone(UTC),
        )


def test_live_regime_formula_is_independent_of_ambient_decimal_context() -> None:
    closes = [Decimal("1"), Decimal("1.234567890123456789")]
    benchmark_return = Decimal(
        "0.0028465698832906347850839738115570737261599772274"
    )
    with localcontext(Context(prec=50, rounding=ROUND_HALF_EVEN)):
        expected_return = _return(closes, 1)
        expected_score = _relative_state_score(benchmark_return)

    with localcontext(Context(prec=6, rounding=ROUND_UP)):
        assert _return(closes, 1) == expected_return
        assert _relative_state_score(benchmark_return) == expected_score


@pytest.mark.asyncio
async def test_live_evidence_is_derived_from_kis_fmp_and_agentnews_without_manual_json() -> None:
    provider_clock = iter((AS_OF, AS_OF + timedelta(seconds=1)))
    market = await KISMarketDataProvider(
        transport=PriceTransport(),
        instruments=(
            KISInstrument(security_id=STOCK_ID, kis_symbol="005930"),
            KISInstrument(security_id=BENCHMARK_ID, kis_symbol="069500"),
        ),
        clock=lambda: next(provider_clock),
    ).fetch_snapshot(security_ids=(STOCK_ID, BENCHMARK_ID), as_of_date=AS_OF)
    board = AgentNewsSnapshot.from_markdown(
        board=AgentNewsBoard.KR,
        url=AgentNewsBoard.KR.url,
        raw_body=(
            b'---\nupdated: "2026-07-26T10:00:00+00:00"\n---\n'
            b'# Korea market context\nSamsung semiconductor leadership remains in focus.\n'
        ),
        fetched_at=AS_OF.astimezone(UTC),
        freshness_window=timedelta(hours=24),
    )
    requester = FMPRequester()
    fundamentals = FMPIncomeStatementClient(requester=requester, api_key="fixture-key")
    evidence_provider = LiveKREvidenceProvider(
        agentnews_fetcher=lambda: board,
        fundamentals_client=fundamentals,
        fmp_symbol="005930.KS",
    )

    evidence = await evidence_provider.build(
        snapshot=market,
        stock_id=STOCK_ID,
        benchmark_id=BENCHMARK_ID,
        as_of=AS_OF,
    )

    assert set(evidence.observations) == {
        "catalyst_recency_sessions",
        "regime_swing_compatibility",
        "earnings_current",
        "earnings_previous",
        "industry_leadership",
        "regime_trend_compatibility",
    }
    assert evidence.observations["earnings_current"] == Decimal("31000000000000")
    assert evidence.observations["earnings_previous"] == Decimal("27000000000000")
    assert evidence.observations["catalyst_recency_sessions"] == Decimal("0")
    assert evidence.observations["regime_swing_compatibility"] == Decimal("51.42")
    assert evidence.observations["regime_trend_compatibility"] == Decimal("55.74")
    assert evidence.observations["industry_leadership"] == Decimal("54.90")
    assert {
        "agentnews:kr:" + board.content_hash,
        "fmp:income:005930.KS:2025-12-31",
        "kis:relative-strength:005930:069500:2026-07-24",
    } <= set(evidence.evidence_payload)
    assert requester.requests[0]["url"] == "https://financialmodelingprep.com/stable/income-statement"
    assert requester.requests[0]["params"] == {
        "apikey": "fixture-key",
        "symbol": "005930.KS",
        "limit": "8",
    }
    assert "fixture-key" not in repr(fundamentals)
    assert fundamentals.evidence == (
        {
            "provider": "FMP",
            "host": "financialmodelingprep.com",
            "endpoint": "/stable/income-statement",
            "status_code": 200,
            "received_at": AS_OF.astimezone(UTC).isoformat(),
        },
    )
    assert evidence.available_at <= AS_OF
    assert evidence.field_quality == {
        "evidence": DataQualityStatus.FRESH,
        "fundamental": DataQualityStatus.FRESH,
        "regime": DataQualityStatus.FRESH,
    }
    assert [item.value for item in evidence.fundamentals] == [
        Decimal("27000000000000"),
        Decimal("31000000000000"),
    ]
    assert {item.kind for item in evidence.evidence_items} == {
        "company_filing",
        "earnings_event",
        "market_context",
        "next_review",
    }
    income_payload = evidence.evidence_payload["fmp:income:005930.KS:2025-12-31"]
    assert income_payload["current_net_income"] == "31000000000000"
    assert income_payload["previous_net_income"] == "27000000000000"
    assert {str(item.evidence_id) for item in evidence.evidence_items} <= set(
        evidence.evidence_payload
    )


@pytest.mark.asyncio
async def test_live_composer_builds_complete_scenario_pack_for_both_strategies() -> None:
    provider_clock = iter((AS_OF, AS_OF + timedelta(seconds=1)))
    provider = KISMarketDataProvider(
        transport=PriceTransport(),
        instruments=(
            KISInstrument(security_id=STOCK_ID, kis_symbol="005930"),
            KISInstrument(security_id=BENCHMARK_ID, kis_symbol="069500"),
        ),
        clock=lambda: next(provider_clock),
    )
    board = AgentNewsSnapshot.from_markdown(
        board=AgentNewsBoard.KR,
        url=AgentNewsBoard.KR.url,
        raw_body=(
            b'---\nupdated: "2026-07-26T10:00:00+00:00"\n---\n'
            b"# Korea market context\nSamsung semiconductor leadership remains in focus.\n"
        ),
        fetched_at=AS_OF.astimezone(UTC),
        freshness_window=timedelta(hours=24),
    )
    composer = KRProductSnapshotComposer(
        provider=provider,
        stock_id=STOCK_ID,
        benchmark_id=BENCHMARK_ID,
        stock_symbol="005930",
        benchmark_symbol="069500",
        evidence_provider=LiveKREvidenceProvider(
            agentnews_fetcher=lambda: board,
            fundamentals_client=FMPIncomeStatementClient(
                requester=FMPRequester(), api_key="fixture-key"
            ),
            fmp_symbol="005930.KS",
        ),
    )

    snapshot = await composer.acquire(as_of=AS_OF)

    assert set(snapshot.strategy_inputs) == {StrategyId.SWING_V1, StrategyId.TREND_V1}
    packs = [item.scenario_input_pack for item in snapshot.strategy_inputs.values()]
    assert all(pack is not None for pack in packs)
    assert all(pack.status is ScenarioInputStatus.COMPLETE for pack in packs if pack)
    assert all(not pack.issues for pack in packs if pack)
    assert len({pack.model_dump_json() for pack in packs if pack}) == 1


@pytest.mark.asyncio
async def test_kr_missing_fmp_continues_with_technical_market_evidence_and_report_only_veto() -> None:
    provider_clock = iter((AS_OF, AS_OF + timedelta(seconds=1)))
    market = await KISMarketDataProvider(
        transport=PriceTransport(),
        instruments=(
            KISInstrument(security_id=STOCK_ID, kis_symbol="005930"),
            KISInstrument(security_id=BENCHMARK_ID, kis_symbol="069500"),
        ),
        clock=lambda: next(provider_clock),
    ).fetch_snapshot(security_ids=(STOCK_ID, BENCHMARK_ID), as_of_date=AS_OF)
    board = AgentNewsSnapshot.from_markdown(
        board=AgentNewsBoard.KR,
        url=AgentNewsBoard.KR.url,
        raw_body=b'---\nupdated: "2026-07-26T10:00:00+00:00"\n---\n# KR context\n',
        fetched_at=AS_OF.astimezone(UTC),
        freshness_window=timedelta(hours=24),
    )
    cascade = KREvidenceComposer(
        stock_code="005930",
        security_id=STOCK_ID,
        dart=UnavailableDARTAdapter(),
        kind=UnavailableKINDAdapter(),
        fmp=None,
    )

    evidence = await LiveKREvidenceProvider(
        agentnews_fetcher=lambda: board,
        fundamentals_client=cascade,
        fmp_symbol="005930.KS",
    ).build(
        snapshot=market,
        stock_id=STOCK_ID,
        benchmark_id=BENCHMARK_ID,
        as_of=AS_OF,
    )

    assert set(evidence.observations) == {
        "catalyst_recency_sessions",
        "regime_swing_compatibility",
        "industry_leadership",
        "regime_trend_compatibility",
    }
    assert evidence.fundamentals == ()
    assert evidence.field_quality["fundamental"] is DataQualityStatus.PARTIAL
    assert "MISSING_SUPPLEMENTAL_FUNDAMENTALS" in evidence.hard_vetoes
    assert any(key.startswith("kis:relative-strength:") for key in evidence.evidence_payload)

    second_clock = iter((AS_OF, AS_OF + timedelta(seconds=1)))
    product_snapshot = await KRProductSnapshotComposer(
        provider=KISMarketDataProvider(
            transport=PriceTransport(),
            instruments=(
                KISInstrument(security_id=STOCK_ID, kis_symbol="005930"),
                KISInstrument(security_id=BENCHMARK_ID, kis_symbol="069500"),
            ),
            clock=lambda: next(second_clock),
        ),
        stock_id=STOCK_ID,
        benchmark_id=BENCHMARK_ID,
        stock_symbol="005930",
        benchmark_symbol="069500",
        evidence_provider=LiveKREvidenceProvider(
            agentnews_fetcher=lambda: board,
            fundamentals_client=KREvidenceComposer(
                stock_code="005930",
                security_id=STOCK_ID,
                dart=UnavailableDARTAdapter(),
                kind=UnavailableKINDAdapter(),
                fmp=None,
            ),
            fmp_symbol="005930.KS",
        ),
    ).acquire(as_of=AS_OF)

    assert product_snapshot.field_quality["fundamental"] is DataQualityStatus.PARTIAL
    assert product_snapshot.strategy_inputs == {}
    assert product_snapshot.source_payload["quality_disposition"] == "REPORT_ONLY"
    assert "FMP" not in product_snapshot.source_payload["evidence_level"]
    swing = product_snapshot.source_payload["report_only_features"]["SWING_V1"]
    assert swing["quality_disposition"] == "REPORT_ONLY"
    assert "swing_v1.price_momentum_5d" in {
        item["name"] for item in swing["values"]
    }
    assert "MISSING_SUPPLEMENTAL_FUNDAMENTALS" in product_snapshot.source_payload[
        "entry_vetoes"
    ]


@pytest.mark.asyncio
async def test_stale_supplemental_fundamentals_are_report_only_not_core_reject() -> None:
    provider_clock = iter((AS_OF, AS_OF + timedelta(seconds=1)))
    market = await KISMarketDataProvider(
        transport=PriceTransport(),
        instruments=(
            KISInstrument(security_id=STOCK_ID, kis_symbol="005930"),
            KISInstrument(security_id=BENCHMARK_ID, kis_symbol="069500"),
        ),
        clock=lambda: next(provider_clock),
    ).fetch_snapshot(security_ids=(STOCK_ID, BENCHMARK_ID), as_of_date=AS_OF)
    board = AgentNewsSnapshot.from_markdown(
        board=AgentNewsBoard.KR,
        url=AgentNewsBoard.KR.url,
        raw_body=b'---\nupdated: "2026-07-26T10:00:00+00:00"\n---\n# KR\n',
        fetched_at=AS_OF.astimezone(UTC),
        freshness_window=timedelta(hours=24),
    )
    evidence = await LiveKREvidenceProvider(
        agentnews_fetcher=lambda: board,
        fundamentals_client=FMPIncomeStatementClient(
            requester=StaleFMPRequester(), api_key="fixture-key"
        ),
        fmp_symbol="005930.KS",
    ).build(
        snapshot=market,
        stock_id=STOCK_ID,
        benchmark_id=BENCHMARK_ID,
        as_of=AS_OF,
    )

    assert evidence.field_quality["fundamental"] is DataQualityStatus.PARTIAL
    assert "STALE_FMP_FUNDAMENTALS" in evidence.hard_vetoes


def test_stale_weekend_agentnews_remains_analyzable_but_vetoes_new_entry() -> None:
    board = AgentNewsSnapshot.from_markdown(
        board=AgentNewsBoard.KR,
        url=AgentNewsBoard.KR.url,
        raw_body=b'---\nupdated: "2026-07-24T18:40:00+00:00"\n---\n# Context\n',
        fetched_at=AS_OF.astimezone(UTC),
        freshness_window=timedelta(hours=72),
    )
    stale = replace(board, quality=DataQualityStatus.STALE)

    assert agentnews_hard_vetoes(board) == ()
    assert agentnews_hard_vetoes(stale) == ("STALE_AGENTNEWS_CONTEXT",)


@pytest.mark.asyncio
async def test_live_us_evidence_uses_fmp_prices_and_agentnews_us() -> None:
    provider_clock = iter((AS_OF, AS_OF + timedelta(seconds=1)))
    kr_snapshot = await KISMarketDataProvider(
        transport=PriceTransport(),
        instruments=(
            KISInstrument(security_id=STOCK_ID, kis_symbol="005930"),
            KISInstrument(security_id=BENCHMARK_ID, kis_symbol="069500"),
        ),
        clock=lambda: next(provider_clock),
    ).fetch_snapshot(security_ids=(STOCK_ID, BENCHMARK_ID), as_of_date=AS_OF)
    symbol_by_id = {STOCK_ID: "AAPL", BENCHMARK_ID: "SPY"}
    us_snapshot = kr_snapshot.model_copy(
        update={
            "market": "US",
            "price_bars": tuple(
                bar.model_copy(
                    update={
                        "provider": "fmp",
                        "provider_symbol": symbol_by_id[bar.security_id],
                    }
                )
                for bar in kr_snapshot.price_bars
            ),
        }
    )
    board = AgentNewsSnapshot.from_markdown(
        board=AgentNewsBoard.US,
        url=AgentNewsBoard.US.url,
        raw_body=(
            b'---\nupdated: "2026-07-26T10:00:00+00:00"\n---\n'
            b"# US market context\nTechnology leadership remains in focus.\n"
        ),
        fetched_at=AS_OF.astimezone(UTC),
        freshness_window=timedelta(hours=24),
    )
    requester = FMPRequester()
    evidence = await LiveUSEvidenceProvider(
        agentnews_fetcher=lambda: board,
        fundamentals_client=FMPIncomeStatementClient(
            requester=requester, api_key="fixture-key"
        ),
        fmp_symbol="005930.KS",
    ).build(
        snapshot=us_snapshot,
        stock_id=STOCK_ID,
        benchmark_id=BENCHMARK_ID,
        as_of=AS_OF,
    )

    assert any(key.startswith("agentnews:us:") for key in evidence.evidence_payload)
    assert any(key.startswith("fmp:price:AAPL:SPY:") for key in evidence.evidence_payload)
