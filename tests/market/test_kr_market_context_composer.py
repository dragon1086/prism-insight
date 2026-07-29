from datetime import date, datetime, timedelta
from dataclasses import replace
from zoneinfo import ZoneInfo

import pytest

from prism_core.data.contracts import DataQualityStatus
from prism_core.data.providers.agentnews import AgentNewsFetchEvidence, AgentNewsFetchResult
from prism_core.data.providers.agentnews_models import AgentNewsBoard, AgentNewsSnapshot
from prism_core.data.providers.kis import ProviderPayload
from prism_core.market import ContextDisposition, KRMarketRegime
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
                    {"mksc_shrn_iscd": "005930", "prdy_ctrt": "2.5"},
                    {"mksc_shrn_iscd": "000660", "prdy_ctrt": "-1.0"},
                    {"mksc_shrn_iscd": "035420", "prdy_ctrt": "0"},
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
async def test_composer_uses_real_provider_contracts_and_keeps_news_non_executable() -> None:
    composer = KRMarketContextComposer(
        kis_transport=FixtureKISMarketContextTransport(),
        agentnews_provider=FixtureAgentNewsProvider(),
        clock=lambda: datetime(2026, 7, 29, 15, 33, tzinfo=KST),
    )

    context = await composer.compose()

    assert context.timing.session_date.isoformat() == "2026-07-29"
    assert [metric.name for metric in context.breadth] == [
        "volume_rank_advance_count",
        "volume_rank_decline_count",
        "volume_rank_returned_security_count",
        "volume_rank_unchanged_count",
    ]
    assert [metric.value for metric in context.breadth] == [1, 1, 3, 1]
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
