from datetime import datetime
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

import pytest

from prism_core.candidates import (
    CandidateChannel,
    CandidateStatus,
    KISVolumeCandidateSource,
    reconcile_candidates,
)
from prism_core.data.contracts import DataQualityStatus
from prism_core.data.providers.kis import ProviderPayload


KST = ZoneInfo("Asia/Seoul")


class FixtureRankingTransport:
    async def fetch_volume_rank(self) -> ProviderPayload:
        return ProviderPayload(
            provider="KIS",
            source_record_id="kis:volume-rank:fixture",
            revision=0,
            observed_at=datetime(2026, 7, 29, 15, 31, tzinfo=KST),
            available_at=datetime(2026, 7, 29, 15, 31, tzinfo=KST),
            payload={
                "volume_rank": [
                    {
                        "mksc_shrn_iscd": "005930",
                        "hts_kor_isnm": "삼성전자",
                        "data_rank": "1",
                        "acml_vol": "12345678",
                        "acml_tr_pbmn": "987654321000",
                    }
                ]
            },
            raw_payload_hash="a" * 64,
        )


@pytest.mark.asyncio
async def test_kis_volume_rank_maps_to_stable_candidate_snapshot() -> None:
    source = KISVolumeCandidateSource(
        transport=FixtureRankingTransport(),
        clock=lambda: datetime(2026, 7, 29, 15, 32, tzinfo=KST),
    )

    candidates = await source.discover()

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.security_id.value == uuid5(NAMESPACE_URL, "prism:kr:005930")
    assert candidate.provider == "KIS"
    assert candidate.provider_symbol == "005930"
    assert candidate.display_name == "삼성전자"
    assert candidate.channel is CandidateChannel.CORE_PRISM
    assert candidate.trigger_ids == ("KIS_VOLUME_RANK",)
    assert candidate.raw_scores == {
        "KIS_VOLUME_RANK.data_rank": Decimal("1"),
        "KIS_VOLUME_RANK.acml_vol": Decimal("12345678"),
        "KIS_VOLUME_RANK.acml_tr_pbmn": Decimal("987654321000"),
    }
    assert candidate.status is CandidateStatus.ELIGIBLE
    assert candidate.observed_at == datetime(2026, 7, 29, 15, 31, tzinfo=KST)
    assert candidate.available_at == datetime(2026, 7, 29, 15, 31, tzinfo=KST)
    assert candidate.ingested_at == datetime(2026, 7, 29, 15, 32, tzinfo=KST)
    assert candidate.as_of == candidate.ingested_at
    assert candidate.source_snapshot_id == f"kis:volume-rank:{'a' * 64}"
    assert candidate.evidence_ids == (
        f"kis:volume-rank:{'a' * 64}:005930",
    )


@pytest.mark.asyncio
async def test_unverified_session_rank_is_report_only_with_visible_issue() -> None:
    class PartialRankingTransport(FixtureRankingTransport):
        async def fetch_volume_rank(self) -> ProviderPayload:
            payload = await super().fetch_volume_rank()
            return ProviderPayload(
                provider=payload.provider,
                source_record_id=payload.source_record_id,
                revision=payload.revision,
                observed_at=payload.observed_at,
                available_at=payload.available_at,
                payload=payload.payload,
                quality=DataQualityStatus.PARTIAL,
                raw_payload_hash=payload.raw_payload_hash,
            )

    source = KISVolumeCandidateSource(
        transport=PartialRankingTransport(),
        clock=lambda: datetime(2026, 7, 29, 15, 32, tzinfo=KST),
    )

    candidates = await source.discover()

    assert candidates[0].status is CandidateStatus.REPORT_ONLY
    assert candidates[0].issues == ("KIS_VOLUME_RANK_SESSION_UNVERIFIED",)


@pytest.mark.asyncio
async def test_malformed_kis_row_is_isolated_by_reconciliation() -> None:
    class OneMalformedRowTransport(FixtureRankingTransport):
        async def fetch_volume_rank(self) -> ProviderPayload:
            payload = await super().fetch_volume_rank()
            rows = list(payload.payload["volume_rank"])
            rows.append(
                {
                    "mksc_shrn_iscd": "000660",
                    "hts_kor_isnm": "SK하이닉스",
                    "data_rank": "not-a-number",
                    "acml_vol": "100",
                    "acml_tr_pbmn": "200",
                }
            )
            return ProviderPayload(
                provider=payload.provider,
                source_record_id=payload.source_record_id,
                revision=payload.revision,
                observed_at=payload.observed_at,
                available_at=payload.available_at,
                payload={"volume_rank": rows},
                raw_payload_hash=payload.raw_payload_hash,
            )

    source = KISVolumeCandidateSource(
        transport=OneMalformedRowTransport(),
        clock=lambda: datetime(2026, 7, 29, 15, 32, tzinfo=KST),
    )

    reconciliation = reconcile_candidates(await source.discover())

    assert reconciliation.input_count == 2
    assert reconciliation.included_identity_count == 1
    assert reconciliation.invalid_record_count == 1
    assert reconciliation.included[0].provider_symbols == ("005930",)
    assert reconciliation.excluded[0].input_index == 1
    assert "raw_scores" in reconciliation.excluded[0].exclusion_reason
