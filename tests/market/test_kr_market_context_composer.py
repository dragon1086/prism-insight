import asyncio
from datetime import date, datetime, timedelta
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
from dataclasses import replace
from zoneinfo import ZoneInfo

import pytest

import prism_core.market.composer as composer_module
import prism_core.market.kr_metrics as kr_metrics_module
from prism_core.data.contracts import DataQualityStatus
from prism_core.data.providers.agentnews import AgentNewsFetchEvidence, AgentNewsFetchResult
from prism_core.data.providers.agentnews_models import AgentNewsBoard, AgentNewsSnapshot
from prism_core.data.providers.kis import ProviderPayload
from prism_core.market import ContextDisposition, KRMarketContext, KRMarketRegime
from prism_core.market.composer import KRMarketContextComposer, resolve_session_state


KST = ZoneInfo("Asia/Seoul")
UTC = ZoneInfo("UTC")


class FixtureKISMarketContextTransport:
    async def fetch_volume_rank(self) -> ProviderPayload:
        observed_at = datetime(2026, 7, 29, 15, 31, tzinfo=KST)
        return ProviderPayload(
            provider="KIS",
            source_record_id="KIS:volume-rank:fixture",
            revision=0,
            observed_at=observed_at,
            available_at=observed_at,
            payload={
                "volume_rank": [
                    {
                        "mksc_shrn_iscd": "005930",
                        "prdy_ctrt": "2.5",
                        "acml_tr_pbmn": "500000000",
                    },
                    {
                        "mksc_shrn_iscd": "000660",
                        "prdy_ctrt": "-1.0",
                        "acml_tr_pbmn": "400000000",
                    },
                    {
                        "mksc_shrn_iscd": "035420",
                        "prdy_ctrt": "0",
                        "acml_tr_pbmn": "200000000",
                    },
                ],
                "ranking_session": {
                    "latest_completed_session": "2026-07-29",
                    "state": "COMPLETE_CURRENT_SESSION",
                },
                "transport_evidence": [
                    {
                        "endpoint": "/uapi/domestic-stock/v1/quotations/volume-rank",
                        "status_code": 200,
                        "received_at": observed_at.isoformat(),
                        "raw_payload_hash": "a" * 64,
                    }
                ],
            },
            quality=DataQualityStatus.FRESH,
            raw_payload_hash="a" * 64,
        )


class FixtureKRXMarketContextProvider:
    async def fetch_market_context(self, *, as_of: datetime) -> ProviderPayload:
        assert as_of == datetime(2026, 7, 29, 15, 33, tzinfo=KST)
        observed_at = datetime(2026, 7, 29, 15, 32, tzinfo=KST)
        return ProviderPayload(
            provider="KRX",
            source_record_id="KRX:market-context:fixture",
            revision=0,
            observed_at=observed_at,
            available_at=observed_at,
            payload={
                "session_date": "2026-07-29",
                "index_history": [
                    {"trade_date": f"2026-07-{day:02d}", "close": str(99 + day)}
                    for day in range(10, 30)
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
                "group_taxonomy": {
                    "version": "KRX_SECTOR_2026-07-29",
                    "assignments": [
                        {
                            "provider_symbol": "000660",
                            "group_id": "KRX:SECTOR:SEMICONDUCTOR",
                        },
                        {
                            "provider_symbol": "005930",
                            "group_id": "KRX:SECTOR:SEMICONDUCTOR",
                        },
                        {
                            "provider_symbol": "035420",
                            "group_id": "KRX:SECTOR:INTERNET",
                        },
                    ],
                },
                "group_observations": [
                    {
                        "provider_symbol": "000660",
                        "current_close": "120",
                        "previous_close": "100",
                        "turnover_krw": "400000000",
                    },
                    {
                        "provider_symbol": "005930",
                        "current_close": "110",
                        "previous_close": "100",
                        "turnover_krw": "500000000",
                    },
                    {
                        "provider_symbol": "035420",
                        "current_close": "101",
                        "previous_close": "100",
                        "turnover_krw": "200000000",
                    },
                ],
                "group_taxonomy_unclassified_count": 0,
                "investor_flows": {
                    "foreign_net_purchase_krw": "125000000000",
                    "institution_net_purchase_krw": "-32000000000",
                },
            },
            quality=DataQualityStatus.FRESH,
            raw_payload_hash="b" * 64,
        )


class FixtureAgentNewsProvider:
    async def fetch_result(self, board: AgentNewsBoard) -> AgentNewsFetchResult:
        assert board is AgentNewsBoard.KR
        fetched_at = datetime(2026, 7, 29, 6, 32, tzinfo=UTC)
        raw = b'---\nupdated: "2026-07-29T06:30:00Z"\n---\n# KR market\n'
        snapshot = AgentNewsSnapshot.from_markdown(
            board=board,
            url=board.url,
            raw_body=raw,
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


@pytest.mark.asyncio
async def test_composer_awaits_all_provider_cleanup_before_propagating_failure() -> None:
    class FailingAgentNewsProvider:
        async def fetch_result(self, board: AgentNewsBoard) -> AgentNewsFetchResult:
            assert board is AgentNewsBoard.KR
            raise RuntimeError("fixture AgentNews failure")

    class CleanupTrackingKRXProvider(FixtureKRXMarketContextProvider):
        completed = False

        async def fetch_market_context(self, *, as_of: datetime) -> ProviderPayload:
            await asyncio.sleep(0.01)
            result = await super().fetch_market_context(as_of=as_of)
            self.completed = True
            return result

    krx_provider = CleanupTrackingKRXProvider()
    composer = KRMarketContextComposer(
        kis_transport=FixtureKISMarketContextTransport(),
        krx_provider=krx_provider,
        agentnews_provider=FailingAgentNewsProvider(),
        clock=lambda: datetime(2026, 7, 29, 15, 33, tzinfo=KST),
    )

    with pytest.raises(RuntimeError, match="fixture AgentNews failure"):
        await composer.compose()

    assert krx_provider.completed is True


@pytest.mark.asyncio
@pytest.mark.parametrize("future_clock", ["attempt", "freshness_evaluation"])
async def test_composer_rejects_agentnews_decision_clocks_after_as_of(
    future_clock: str,
) -> None:
    class FutureClockAgentNewsProvider(FixtureAgentNewsProvider):
        async def fetch_result(self, board: AgentNewsBoard) -> AgentNewsFetchResult:
            result = await super().fetch_result(board)
            future = datetime(2026, 7, 29, 15, 34, tzinfo=KST)
            if future_clock == "attempt":
                return replace(
                    result,
                    attempts=(replace(result.attempts[0], fetched_at=future),),
                )
            return replace(
                result,
                snapshot=result.snapshot.as_stale_fallback(
                    reason="LIVE_FETCH_FAILED", evaluated_at=future
                ),
                used_last_known_good=True,
            )

    composer = KRMarketContextComposer(
        kis_transport=FixtureKISMarketContextTransport(),
        agentnews_provider=FutureClockAgentNewsProvider(),
        clock=lambda: datetime(2026, 7, 29, 15, 33, tzinfo=KST),
    )

    with pytest.raises(ValueError, match="AgentNews decision clock cannot follow context as_of"):
        await composer.compose()


@pytest.mark.asyncio
@pytest.mark.parametrize("primary_defect", ["wrong_provider", "future_available_at"])
async def test_composer_rejects_invalid_primary_provider_or_future_evidence(
    primary_defect: str,
) -> None:
    class InvalidPrimaryTransport(FixtureKISMarketContextTransport):
        async def fetch_volume_rank(self) -> ProviderPayload:
            payload = await super().fetch_volume_rank()
            update = (
                {"provider": "KRX"}
                if primary_defect == "wrong_provider"
                else {"available_at": datetime(2026, 7, 29, 15, 34, tzinfo=KST)}
            )
            return replace(payload, **update)

    composer = KRMarketContextComposer(
        kis_transport=InvalidPrimaryTransport(),
        agentnews_provider=FixtureAgentNewsProvider(),
        clock=lambda: datetime(2026, 7, 29, 15, 33, tzinfo=KST),
    )

    expected = (
        "requires the KIS primary provider"
        if primary_defect == "wrong_provider"
        else "provider evidence cannot be available after context as_of"
    )
    with pytest.raises(ValueError, match=expected):
        await composer.compose()


@pytest.mark.asyncio
async def test_composer_uses_real_provider_contracts_and_keeps_news_non_executable() -> None:
    composer = KRMarketContextComposer(
        kis_transport=FixtureKISMarketContextTransport(),
        agentnews_provider=FixtureAgentNewsProvider(),
        clock=lambda: datetime(2026, 7, 29, 15, 33, tzinfo=KST),
    )

    context = await composer.compose()

    assert context.timing.session_date.isoformat() == "2026-07-29"
    assert context.breadth == ()
    assert "breadth" in context.missing_fields
    assert tuple(clock.source for clock in context.source_clocks) == (
        "AgentNews",
        "KIS",
    )
    assert context.supplemental_evidence[0].provider == "AgentNews"
    assert context.supplemental_evidence[0].executable is False
    assert context.regime.regime is KRMarketRegime.UNKNOWN
    assert context.disposition is ContextDisposition.ANALYSIS_INCOMPLETE
    assert context.action_eligible is False
    assert "index_state" in context.missing_fields
    assert "SIDEWAYS" not in context.to_canonical_json()


@pytest.mark.asyncio
async def test_composer_uses_krx_equity_universe_for_index_breadth_flows_and_regime() -> None:
    context = await KRMarketContextComposer(
        kis_transport=FixtureKISMarketContextTransport(),
        krx_provider=FixtureKRXMarketContextProvider(),
        agentnews_provider=FixtureAgentNewsProvider(),
        clock=lambda: datetime(2026, 7, 29, 15, 33, tzinfo=KST),
    ).compose()

    assert {metric.name: metric.value for metric in context.index_state} == {
        "kospi_close": Decimal("128"),
        "kospi_ma20": Decimal("118.5"),
        "kospi_return_10d_pct": Decimal("8.474576271186440677966101700"),
    }
    assert {metric.name: metric.unit for metric in context.index_state} == {
        "kospi_close": "index_points",
        "kospi_ma20": "index_points",
        "kospi_return_10d_pct": "percent",
    }
    assert {metric.name: metric.value for metric in context.breadth} == {
        "advance_count": Decimal("1200"),
        "decline_count": Decimal("900"),
        "eligible_equity_count": Decimal("2200"),
        "excluded_non_equity_count": Decimal("175"),
        "unclassified_equity_count": Decimal("0"),
        "unchanged_count": Decimal("100"),
    }
    assert all(metric.source == "KRX" for metric in context.breadth)
    assert {metric.name: metric.value for metric in context.investor_flows} == {
        "foreign_net_purchase_krw": Decimal("125000000000"),
        "institution_net_purchase_krw": Decimal("-32000000000"),
    }
    assert context.regime.regime is KRMarketRegime.STRONG_BULL
    assert [group.group_id for group in context.group_leadership] == [
        "KRX:SECTOR:SEMICONDUCTOR",
        "KRX:SECTOR:INTERNET",
    ]
    assert [group.rank for group in context.group_leadership] == [1, 2]
    assert context.group_leadership[0].source == "KIS+KRX"
    assert context.group_leadership[0].breadth_pct == Decimal("100")
    assert context.group_leadership_state.quality is DataQualityStatus.FRESH
    assert context.group_leadership_state.reason_codes == (
        "AUTHORITATIVE_GROUPS_RANKED",
    )
    assert context.quality is DataQualityStatus.FRESH
    assert context.action_eligible is True
    assert context.optional_missing_sources == ("DART", "KIND")
    assert "DART" not in context.missing_fields
    assert "KIND" not in context.missing_fields
    assert not any(metric.name.startswith("volume_rank_") for metric in context.breadth)


@pytest.mark.asyncio
async def test_all_low_turnover_groups_preserve_exclusions_and_fail_closed() -> None:
    class LowTurnoverKRXProvider(FixtureKRXMarketContextProvider):
        async def fetch_market_context(self, *, as_of: datetime) -> ProviderPayload:
            payload = await super().fetch_market_context(as_of=as_of)
            body = dict(payload.payload)
            body["group_observations"] = [
                {**row, "turnover_krw": "10000000"}
                for row in body["group_observations"]  # type: ignore[union-attr]
            ]
            return replace(payload, payload=body)

    class LowTurnoverKISTransport(FixtureKISMarketContextTransport):
        async def fetch_volume_rank(self) -> ProviderPayload:
            payload = await super().fetch_volume_rank()
            body = dict(payload.payload)
            body["volume_rank"] = [
                {**row, "acml_tr_pbmn": "10000000"}
                for row in body["volume_rank"]  # type: ignore[union-attr]
            ]
            return replace(payload, payload=body)

    context = await KRMarketContextComposer(
        kis_transport=LowTurnoverKISTransport(),
        krx_provider=LowTurnoverKRXProvider(),
        agentnews_provider=FixtureAgentNewsProvider(),
        clock=lambda: datetime(2026, 7, 29, 15, 33, tzinfo=KST),
    ).compose()

    assert context.group_leadership == ()
    assert context.group_leadership_state is not None
    assert context.group_leadership_state.quality is DataQualityStatus.PARTIAL
    assert len(context.group_leadership_state.excluded_groups) == 2
    assert {item.reason_codes for item in context.group_leadership_state.excluded_groups} == {
        ("LOW_TURNOVER",)
    }
    assert "group_leadership" in context.missing_fields
    assert context.action_eligible is False


@pytest.mark.asyncio
async def test_missing_taxonomy_keeps_index_and_breadth_but_blocks_leadership() -> None:
    class MissingTaxonomyKRXProvider(FixtureKRXMarketContextProvider):
        async def fetch_market_context(self, *, as_of: datetime) -> ProviderPayload:
            payload = await super().fetch_market_context(as_of=as_of)
            body = dict(payload.payload)
            body.pop("group_taxonomy")
            body.pop("group_observations")
            body["group_leadership_availability"] = {
                "quality": "UNAVAILABLE",
                "reason_codes": ["KRX_SECTOR_TAXONOMY_UNAVAILABLE"],
            }
            return replace(payload, payload=body, quality=DataQualityStatus.PARTIAL)

    context = await KRMarketContextComposer(
        kis_transport=FixtureKISMarketContextTransport(),
        krx_provider=MissingTaxonomyKRXProvider(),
        agentnews_provider=FixtureAgentNewsProvider(),
        clock=lambda: datetime(2026, 7, 29, 15, 33, tzinfo=KST),
    ).compose()

    assert context.index_state
    assert context.breadth
    assert context.group_leadership == ()
    assert context.group_leadership_state is not None
    assert context.group_leadership_state.reason_codes == (
        "KRX_SECTOR_TAXONOMY_UNAVAILABLE",
    )
    assert "group_leadership" in context.missing_fields
    assert context.action_eligible is False


@pytest.mark.asyncio
async def test_malformed_taxonomy_keeps_index_and_breadth_but_blocks_leadership() -> None:
    class MalformedTaxonomyKRXProvider(FixtureKRXMarketContextProvider):
        async def fetch_market_context(self, *, as_of: datetime) -> ProviderPayload:
            payload = await super().fetch_market_context(as_of=as_of)
            body = dict(payload.payload)
            body["group_taxonomy"] = {"version": "KRX_SECTOR_2026-07-29"}
            return replace(payload, payload=body)

    context = await KRMarketContextComposer(
        kis_transport=FixtureKISMarketContextTransport(),
        krx_provider=MalformedTaxonomyKRXProvider(),
        agentnews_provider=FixtureAgentNewsProvider(),
        clock=lambda: datetime(2026, 7, 29, 15, 33, tzinfo=KST),
    ).compose()

    assert context.index_state
    assert context.breadth
    assert context.group_leadership == ()
    assert context.group_leadership_state is not None
    assert context.group_leadership_state.quality is DataQualityStatus.UNAVAILABLE
    assert context.group_leadership_state.reason_codes == (
        "KRX_GROUP_LEADERSHIP_INVALID",
    )
    assert "group_leadership" in context.missing_fields
    assert context.action_eligible is False


@pytest.mark.asyncio
async def test_malformed_kis_group_numeric_keeps_index_and_breadth() -> None:
    class MalformedNumericKISTransport(FixtureKISMarketContextTransport):
        async def fetch_volume_rank(self) -> ProviderPayload:
            payload = await super().fetch_volume_rank()
            body = dict(payload.payload)
            rows = list(body["volume_rank"])  # type: ignore[arg-type]
            rows[0] = {**rows[0], "prdy_ctrt": "N/A"}
            body["volume_rank"] = rows
            return replace(payload, payload=body)

    context = await KRMarketContextComposer(
        kis_transport=MalformedNumericKISTransport(),
        krx_provider=FixtureKRXMarketContextProvider(),
        agentnews_provider=FixtureAgentNewsProvider(),
        clock=lambda: datetime(2026, 7, 29, 15, 33, tzinfo=KST),
    ).compose()

    assert context.index_state
    assert context.breadth
    assert context.group_leadership == ()
    assert context.group_leadership_state is not None
    assert context.group_leadership_state.reason_codes == (
        "KRX_GROUP_LEADERSHIP_INVALID",
    )
    assert "group_leadership" in context.missing_fields
    assert context.action_eligible is False


@pytest.mark.asyncio
async def test_composer_delegates_authoritative_calculation_to_kr_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []
    compute = kr_metrics_module.compute_kr_market_metrics

    def tracking_compute(**kwargs: object) -> kr_metrics_module.KRComputedMetrics:
        calls.append(kwargs)
        return compute(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(kr_metrics_module, "compute_kr_market_metrics", tracking_compute)

    await KRMarketContextComposer(
        kis_transport=FixtureKISMarketContextTransport(),
        krx_provider=FixtureKRXMarketContextProvider(),
        agentnews_provider=FixtureAgentNewsProvider(),
        clock=lambda: datetime(2026, 7, 29, 15, 33, tzinfo=KST),
    ).compose()

    assert len(calls) == 1
    assert calls[0]["source_quality"] is DataQualityStatus.FRESH
    assert calls[0]["evidence_id"] == "KRX:market-context:fixture"


@pytest.mark.asyncio
async def test_index_metrics_and_regime_are_independent_of_ambient_decimal_context() -> None:
    class BoundaryKRXProvider(FixtureKRXMarketContextProvider):
        async def fetch_market_context(self, *, as_of: datetime) -> ProviderPayload:
            payload = await super().fetch_market_context(as_of=as_of)
            body = dict(payload.payload)
            body["index_history"] = [
                {
                    "trade_date": f"2026-07-{day:02d}",
                    "close": "104.99996" if day == 29 else "100",
                }
                for day in range(10, 30)
            ]
            return replace(payload, payload=body)

    async def compose() -> KRMarketContext:
        return await KRMarketContextComposer(
            kis_transport=FixtureKISMarketContextTransport(),
            krx_provider=BoundaryKRXProvider(),
            agentnews_provider=FixtureAgentNewsProvider(),
            clock=lambda: datetime(2026, 7, 29, 15, 33, tzinfo=KST),
        ).compose()

    with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
        expected = await compose()
    with localcontext(Context(prec=6, rounding=ROUND_HALF_EVEN)):
        actual = await compose()

    assert actual.index_state == expected.index_state
    assert actual.regime == expected.regime
    assert actual.regime.regime is KRMarketRegime.MODERATE_BULL


@pytest.mark.asyncio
async def test_krx_unavailability_fails_closed_without_blocking_context_readback() -> None:
    class UnavailableKRXProvider:
        async def fetch_market_context(self, *, as_of: datetime) -> ProviderPayload:
            del as_of
            raise TimeoutError("fixture KRX timeout")

    context = await KRMarketContextComposer(
        kis_transport=FixtureKISMarketContextTransport(),
        krx_provider=UnavailableKRXProvider(),
        agentnews_provider=FixtureAgentNewsProvider(),
        clock=lambda: datetime(2026, 7, 29, 15, 33, tzinfo=KST),
    ).compose()

    assert context.regime.regime is KRMarketRegime.UNKNOWN
    assert context.breadth == ()
    assert context.action_eligible is False
    assert context.missing_fields == (
        "breadth",
        "group_leadership",
        "index_state",
        "regime_features",
    )
    assert "KRX" in context.optional_missing_sources


@pytest.mark.asyncio
async def test_optional_partial_investor_flows_are_visible_without_blocking_core() -> None:
    class PartialFlowKRXProvider(FixtureKRXMarketContextProvider):
        async def fetch_market_context(self, *, as_of: datetime) -> ProviderPayload:
            payload = await super().fetch_market_context(as_of=as_of)
            body = dict(payload.payload)
            body["investor_flow_missing_markets"] = ["KOSDAQ"]
            return replace(payload, payload=body)

    context = await KRMarketContextComposer(
        kis_transport=FixtureKISMarketContextTransport(),
        krx_provider=PartialFlowKRXProvider(),
        agentnews_provider=FixtureAgentNewsProvider(),
        clock=lambda: datetime(2026, 7, 29, 15, 33, tzinfo=KST),
    ).compose()

    assert context.quality is DataQualityStatus.FRESH
    assert context.action_eligible is True
    assert "KRX_INVESTOR_FLOWS_KOSDAQ" in context.optional_missing_sources
    assert context.investor_flows


@pytest.mark.asyncio
async def test_last_known_good_news_stays_stale_supplemental_and_non_executable() -> None:
    class LastKnownGoodAgentNewsProvider(FixtureAgentNewsProvider):
        async def fetch_result(self, board: AgentNewsBoard) -> AgentNewsFetchResult:
            result = await super().fetch_result(board)
            evaluated_at = datetime(2026, 7, 29, 15, 32, tzinfo=KST)
            return replace(
                result,
                snapshot=result.snapshot.as_stale_fallback(
                    reason="LIVE_FETCH_FAILED", evaluated_at=evaluated_at
                ),
                used_last_known_good=True,
            )

    context = await KRMarketContextComposer(
        kis_transport=FixtureKISMarketContextTransport(),
        agentnews_provider=LastKnownGoodAgentNewsProvider(),
        clock=lambda: datetime(2026, 7, 29, 15, 33, tzinfo=KST),
    ).compose()

    assert context.supplemental_evidence[0].quality is DataQualityStatus.STALE
    assert context.supplemental_evidence[0].executable is False
    assert context.quality is DataQualityStatus.UNAVAILABLE
    assert context.disposition is ContextDisposition.ANALYSIS_INCOMPLETE


@pytest.mark.asyncio
async def test_composer_output_is_one_immutable_shared_context_instance() -> None:
    composer = KRMarketContextComposer(
        kis_transport=FixtureKISMarketContextTransport(),
        agentnews_provider=FixtureAgentNewsProvider(),
        clock=lambda: datetime(2026, 7, 29, 15, 33, tzinfo=KST),
    )

    context = await composer.compose()

    with pytest.raises(Exception, match="frozen"):
        context.quality = DataQualityStatus.FRESH


def test_mutable_kis_snapshot_distinguishes_preopen_from_intraday() -> None:
    assert resolve_session_state(
        provider_state="UNVERIFIED_MUTABLE_SNAPSHOT",
        observed_at=datetime(2026, 7, 29, 8, 55, tzinfo=KST),
        latest_completed_session=date(2026, 7, 28),
    ).value == "PRIOR_CLOSE"
    assert resolve_session_state(
        provider_state="UNVERIFIED_MUTABLE_SNAPSHOT",
        observed_at=datetime(2026, 7, 29, 10, 0, tzinfo=KST),
        latest_completed_session=date(2026, 7, 28),
    ).value == "INTRADAY"


def test_complete_session_label_requires_official_close_to_have_passed() -> None:
    assert resolve_session_state(
        provider_state="COMPLETE_CURRENT_SESSION",
        observed_at=datetime(2026, 7, 29, 10, 0, tzinfo=KST),
        latest_completed_session=date(2026, 7, 29),
    ) is composer_module.SessionState.UNKNOWN


@pytest.mark.parametrize(
    ("provider_state", "latest_completed_session"),
    [
        ("COMPLETE_CURRENT_SESSION", date(2026, 7, 28)),
        ("UNRECOGNIZED_PROVIDER_STATE", date(2026, 7, 28)),
    ],
)
def test_unverifiable_provider_session_state_is_unknown(
    provider_state: str, latest_completed_session: date
) -> None:
    assert resolve_session_state(
        provider_state=provider_state,
        observed_at=datetime(2026, 7, 29, 15, 31, tzinfo=KST),
        latest_completed_session=latest_completed_session,
    ) is composer_module.SessionState.UNKNOWN


@pytest.mark.parametrize(
    ("provider_state", "calendar_symbol"),
    [
        ("UNVERIFIED_MUTABLE_SNAPSHOT", "is_exchange_session"),
        ("COMPLETE_CURRENT_SESSION", "resolve_latest_completed_session"),
    ],
)
def test_calendar_failure_makes_session_state_unknown(
    monkeypatch: pytest.MonkeyPatch, provider_state: str, calendar_symbol: str
) -> None:
    def unavailable(*_args: object) -> bool:
        raise composer_module.ExchangeCalendarUnavailableError("unavailable")

    monkeypatch.setattr(composer_module, calendar_symbol, unavailable)

    assert resolve_session_state(
        provider_state=provider_state,
        observed_at=datetime(2026, 7, 29, 10, 0, tzinfo=KST),
        latest_completed_session=(
            date(2026, 7, 29)
            if provider_state == "COMPLETE_CURRENT_SESSION"
            else date(2026, 7, 28)
        ),
    ) is composer_module.SessionState.UNKNOWN


@pytest.mark.parametrize(
    ("observed_at", "latest_completed_session"),
    [
        (datetime(2026, 8, 1, 10, 0, tzinfo=KST), date(2026, 7, 31)),
        (datetime(2026, 8, 17, 10, 0, tzinfo=KST), date(2026, 8, 14)),
    ],
)
def test_mutable_snapshot_never_labels_weekend_or_holiday_intraday(
    observed_at: datetime, latest_completed_session: date
) -> None:
    assert resolve_session_state(
        provider_state="UNVERIFIED_MUTABLE_SNAPSHOT",
        observed_at=observed_at,
        latest_completed_session=latest_completed_session,
    ).value == "PRIOR_CLOSE"


@pytest.mark.asyncio
async def test_kis_conflict_is_represented_with_an_explicit_reason() -> None:
    class ConflictKISMarketContextTransport(FixtureKISMarketContextTransport):
        async def fetch_volume_rank(self) -> ProviderPayload:
            payload = await super().fetch_volume_rank()
            return ProviderPayload(
                provider=payload.provider,
                source_record_id=payload.source_record_id,
                revision=payload.revision,
                observed_at=payload.observed_at,
                available_at=payload.available_at,
                payload=payload.payload,
                quality=DataQualityStatus.CONFLICT,
                raw_payload_hash=payload.raw_payload_hash,
            )

    composer = KRMarketContextComposer(
        kis_transport=ConflictKISMarketContextTransport(),
        agentnews_provider=FixtureAgentNewsProvider(),
        clock=lambda: datetime(2026, 7, 29, 15, 33, tzinfo=KST),
    )

    context = await composer.compose()

    assert context.quality is DataQualityStatus.CONFLICT
    assert context.conflicts == ("KIS_PRIMARY_QUALITY_CONFLICT",)
    assert context.action_eligible is False


@pytest.mark.asyncio
async def test_volume_rank_rows_are_never_inferred_as_market_breadth() -> None:
    class VolumeRankTransport(FixtureKISMarketContextTransport):
        async def fetch_volume_rank(self) -> ProviderPayload:
            payload = await super().fetch_volume_rank()
            body = dict(payload.payload)
            body["volume_rank"] = [
                {"mksc_shrn_iscd": f"ETF{index}", "prdy_ctrt": "10"}
                for index in range(30)
            ]
            return replace(payload, payload=body)

    context = await KRMarketContextComposer(
        kis_transport=VolumeRankTransport(),
        agentnews_provider=FixtureAgentNewsProvider(),
        clock=lambda: datetime(2026, 7, 29, 15, 33, tzinfo=KST),
    ).compose()

    assert context.breadth == ()
    assert "breadth" in context.missing_fields
    assert context.quality is DataQualityStatus.UNAVAILABLE
    assert context.action_eligible is False
