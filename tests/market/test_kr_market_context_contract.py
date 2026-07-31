from __future__ import annotations

import importlib
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from prism_core.data.contracts import DataQualityStatus
from prism_core.data.providers.agentnews import AgentNewsFetchEvidence, AgentNewsFetchResult
from prism_core.data.providers.agentnews_models import AgentNewsBoard, AgentNewsSnapshot
from prism_core.data.providers.kis import ProviderPayload
from prism_core.market import ContextDisposition, KRMarketRegime
from prism_core.market.composer import KRMarketContextComposer


KST = ZoneInfo("Asia/Seoul")
UTC = ZoneInfo("UTC")
AS_OF = datetime(2026, 7, 29, 15, 33, tzinfo=KST)
SESSION = date(2026, 7, 29)
KRX_EVIDENCE_ID = f"KRX:market-context:contract:{'b' * 64}"


def _completed_sessions(*, count: int, end: date) -> list[date]:
    sessions: list[date] = []
    cursor = end
    while len(sessions) < count:
        if cursor.weekday() < 5:
            sessions.append(cursor)
        cursor -= timedelta(days=1)
    return list(reversed(sessions))


class FixtureKISMarketContextTransport:
    async def fetch_volume_rank(self) -> ProviderPayload:
        observed_at = datetime(2026, 7, 29, 15, 31, tzinfo=KST)
        return ProviderPayload(
            provider="KIS",
            source_record_id="KIS:volume-rank:contract",
            revision=0,
            observed_at=observed_at,
            available_at=observed_at,
            payload={
                "volume_rank": [
                    {"mksc_shrn_iscd": f"RANK{index:02d}", "prdy_ctrt": "9.9"}
                    for index in range(30)
                ],
                "ranking_session": {
                    "latest_completed_session": SESSION.isoformat(),
                    "state": "COMPLETE_CURRENT_SESSION",
                },
            },
            quality=DataQualityStatus.FRESH,
            raw_payload_hash="a" * 64,
        )


class FixtureAgentNewsProvider:
    async def fetch_result(self, board: AgentNewsBoard) -> AgentNewsFetchResult:
        assert board is AgentNewsBoard.KR
        fetched_at = datetime(2026, 7, 29, 6, 32, tzinfo=UTC)
        snapshot = AgentNewsSnapshot.from_markdown(
            board=board,
            url=board.url,
            raw_body=b'---\nupdated: "2026-07-29T06:30:00Z"\n---\n# KR market\n',
            fetched_at=fetched_at,
            freshness_window=timedelta(hours=12),
        )
        return AgentNewsFetchResult(
            snapshot=snapshot,
            attempts=(
                AgentNewsFetchEvidence(
                    url=board.url,
                    status_code=200,
                    fetched_at=fetched_at,
                    latency_ms=10,
                    content_hash=snapshot.content_hash,
                    outcome="RESPONSE_RECEIVED",
                ),
            ),
            used_last_known_good=False,
        )


class FixtureOfficialKRXProvider:
    async def fetch_market_context(self, *, as_of: datetime) -> ProviderPayload:
        assert as_of == AS_OF
        sessions = _completed_sessions(count=20, end=SESSION)
        observed_at = datetime(2026, 7, 29, 15, 30, tzinfo=KST)
        available_at = datetime(2026, 7, 29, 15, 32, tzinfo=KST)
        return ProviderPayload(
            provider="KRX",
            source_record_id=KRX_EVIDENCE_ID,
            revision=0,
            observed_at=observed_at,
            available_at=available_at,
            payload={
                "session_date": SESSION.isoformat(),
                "index_history": [
                    {"trade_date": session.isoformat(), "close": str(100 + index)}
                    for index, session in enumerate(sessions)
                ],
                "equity_breadth": {
                    "advance_count": 1200,
                    "decline_count": 900,
                    "unchanged_count": 100,
                    "eligible_equity_count": 2200,
                    "excluded_non_equity_count": 175,
                    "unclassified_equity_count": 0,
                    "universe": "KOSPI_KOSDAQ_EQUITIES",
                },
                "investor_flows": {
                    "foreign_net_purchase_krw": "125000000000",
                    "institution_net_purchase_krw": "-32000000000",
                },
                "investor_flow_markets": ["KOSDAQ", "KOSPI"],
                "investor_flow_missing_markets": [],
            },
            quality=DataQualityStatus.FRESH,
            raw_payload_hash="b" * 64,
        )


@pytest.mark.asyncio
async def test_composer_contract_exposes_authoritative_timestamped_kr_context() -> None:
    composer = KRMarketContextComposer(
        kis_transport=FixtureKISMarketContextTransport(),
        krx_provider=FixtureOfficialKRXProvider(),
        agentnews_provider=FixtureAgentNewsProvider(),
        clock=lambda: AS_OF,
    )

    first = await composer.compose()
    with localcontext(Context(prec=6, rounding=ROUND_HALF_EVEN)):
        second = await composer.compose()

    assert first.to_canonical_json() == second.to_canonical_json()
    assert first.timing.session_date == SESSION
    assert first.timing.as_of == AS_OF
    assert {
        clock.source: (
            clock.role.value,
            clock.observed_at,
            clock.available_at,
            clock.ingested_at,
            clock.quality,
            clock.evidence_ids,
        )
        for clock in first.source_clocks
    }["KRX"] == (
        "OFFICIAL",
        datetime(2026, 7, 29, 15, 30, tzinfo=KST),
        datetime(2026, 7, 29, 15, 32, tzinfo=KST),
        AS_OF,
        DataQualityStatus.FRESH,
        (KRX_EVIDENCE_ID,),
    )
    assert {metric.name: metric.value for metric in first.index_state} == {
        "kospi_close": Decimal("119"),
        "kospi_ma20": Decimal("109.5"),
        "kospi_return_10d_pct": Decimal("9.174311926605504587155963300"),
    }
    assert {metric.name: metric.value for metric in first.breadth} == {
        "advance_count": Decimal("1200"),
        "decline_count": Decimal("900"),
        "eligible_equity_count": Decimal("2200"),
        "excluded_non_equity_count": Decimal("175"),
        "unclassified_equity_count": Decimal("0"),
        "unchanged_count": Decimal("100"),
    }
    assert {metric.name: metric.value for metric in first.investor_flows} == {
        "foreign_net_purchase_krw": Decimal("125000000000"),
        "institution_net_purchase_krw": Decimal("-32000000000"),
    }
    assert first.regime.regime is KRMarketRegime.STRONG_BULL
    assert first.regime.version == "kr_regime_v1"
    assert first.quality is DataQualityStatus.FRESH
    assert first.disposition is ContextDisposition.ELIGIBLE
    assert first.optional_missing_sources == ("DART", "KIND")
    assert "DART" not in first.missing_fields
    assert "KIND" not in first.missing_fields


@pytest.mark.asyncio
async def test_optional_sources_and_flows_can_be_missing_without_invalidating_kis_core() -> None:
    class NoFlowOfficialKRXProvider(FixtureOfficialKRXProvider):
        async def fetch_market_context(self, *, as_of: datetime) -> ProviderPayload:
            payload = await super().fetch_market_context(as_of=as_of)
            body = dict(payload.payload)
            body.pop("investor_flows")
            body["investor_flow_markets"] = []
            body["investor_flow_missing_markets"] = ["KOSDAQ", "KOSPI"]
            return replace(payload, payload=body)

    context = await KRMarketContextComposer(
        kis_transport=FixtureKISMarketContextTransport(),
        krx_provider=NoFlowOfficialKRXProvider(),
        agentnews_provider=FixtureAgentNewsProvider(),
        clock=lambda: AS_OF,
    ).compose()

    assert context.quality is DataQualityStatus.FRESH
    assert context.disposition is ContextDisposition.ELIGIBLE
    assert context.missing_fields == ()
    assert context.investor_flows == ()
    assert context.optional_missing_sources == (
        "DART",
        "KIND",
        "KRX_INVESTOR_FLOWS",
        "KRX_INVESTOR_FLOWS_KOSDAQ",
        "KRX_INVESTOR_FLOWS_KOSPI",
    )


@pytest.mark.asyncio
async def test_kis_core_unavailability_is_a_hard_failure_not_an_optional_skip() -> None:
    class UnavailableKISTransport:
        async def fetch_volume_rank(self) -> ProviderPayload:
            raise TimeoutError("fixture KIS core unavailable")

    composer = KRMarketContextComposer(
        kis_transport=UnavailableKISTransport(),
        krx_provider=FixtureOfficialKRXProvider(),
        agentnews_provider=FixtureAgentNewsProvider(),
        clock=lambda: AS_OF,
    )

    with pytest.raises(TimeoutError, match="fixture KIS core unavailable"):
        await composer.compose()


@pytest.mark.asyncio
async def test_composer_rejects_kis_krx_completed_session_mismatch() -> None:
    class MismatchedSessionKRXProvider(FixtureOfficialKRXProvider):
        async def fetch_market_context(self, *, as_of: datetime) -> ProviderPayload:
            payload = await super().fetch_market_context(as_of=as_of)
            body = dict(payload.payload)
            body["session_date"] = "2026-07-28"
            return replace(payload, payload=body)

    composer = KRMarketContextComposer(
        kis_transport=FixtureKISMarketContextTransport(),
        krx_provider=MismatchedSessionKRXProvider(),
        agentnews_provider=FixtureAgentNewsProvider(),
        clock=lambda: AS_OF,
    )

    with pytest.raises(ValueError, match="session"):
        await composer.compose()


@pytest.mark.asyncio
async def test_kis_volume_rank_top_30_is_not_market_breadth() -> None:
    context = await KRMarketContextComposer(
        kis_transport=FixtureKISMarketContextTransport(),
        agentnews_provider=FixtureAgentNewsProvider(),
        clock=lambda: AS_OF,
    ).compose()

    assert context.breadth == ()
    assert "breadth" in context.missing_fields
    assert context.action_eligible is False
    assert context.regime.regime is KRMarketRegime.UNKNOWN
    assert context.regime.missing_features == (
        "kospi_close",
        "kospi_ma20",
        "kospi_return_10d_pct",
    )


@pytest.mark.asyncio
async def test_official_breadth_excludes_etf_etn_leveraged_and_inverse_products() -> None:
    krx_module = importlib.import_module("prism_core.market.krx")
    provider_type = krx_module.KRXMarketContextProvider

    class ClassifiedKRXClient:
        def get_index_history(
            self, start: date, end: date, ticker: str
        ) -> pd.DataFrame:
            del start, ticker
            sessions = _completed_sessions(count=20, end=end)
            return pd.DataFrame(
                {"Close": range(100, 120)},
                index=pd.to_datetime(sessions),
            )

        def get_equity_ohlcv(self, session: date, market: str) -> pd.DataFrame:
            rows = [
                (f"{market}-EQUITY-A", 101, "EQUITY"),
                (f"{market}-EQUITY-B", 99, "EQUITY"),
                (f"{market}-ETF-A", 110, "ETF"),
                (f"{market}-ETN-A", 110, "ETN"),
                (f"{market}-LEV-A", 110, "LEVERAGED_ETF"),
                (f"{market}-INV-A", 110, "INVERSE_ETF"),
            ]
            if session != SESSION:
                rows = [(ticker, 100, security_type) for ticker, _, security_type in rows]
            return pd.DataFrame(
                {
                    "Close": [close for _, close, _ in rows],
                    "SecurityType": [security_type for _, _, security_type in rows],
                },
                index=[ticker for ticker, _, _ in rows],
            )

        def get_investor_flows(self, session: date, market: str) -> pd.DataFrame:
            del session, market
            return pd.DataFrame()

    payload = await provider_type(
        client=ClassifiedKRXClient(),
        clock=lambda: datetime(2026, 7, 29, 15, 32, tzinfo=KST),
    ).fetch_market_context(as_of=AS_OF)

    breadth = payload.payload["equity_breadth"]
    assert breadth == {
        "advance_count": 2,
        "decline_count": 2,
        "unchanged_count": 0,
        "eligible_equity_count": 4,
        "excluded_non_equity_count": 8,
        "unclassified_equity_count": 0,
        "universe": "KOSPI_KOSDAQ_EQUITIES",
    }
    assert payload.quality is DataQualityStatus.FRESH
    assert payload.raw_payload_hash is not None
    assert len(payload.raw_payload_hash) == 64
    assert payload.source_record_id.endswith(payload.raw_payload_hash)
