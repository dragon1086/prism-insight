"""KIS-primary read-only candidate discovery adapters."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Callable, Mapping, Protocol, cast
from uuid import NAMESPACE_URL, uuid5

from prism_core.candidates.contracts import (
    CandidateChannel,
    CandidateSnapshot,
    CandidateStatus,
)
from prism_core.data.contracts import DataQualityStatus, SecurityId
from prism_core.data.providers.kis import ProviderPayload
from prism_core.strategies.contracts import Market


class KISVolumeRankingTransport(Protocol):
    """Read-only KIS volume-ranking boundary; no account or order surface exists."""

    async def fetch_volume_rank(self) -> ProviderPayload: ...


class KISVolumeCandidateSource:
    """Map every provider-returned KIS volume-rank row without a local cap."""

    def __init__(
        self,
        *,
        transport: KISVolumeRankingTransport,
        clock: Callable[[], datetime],
    ) -> None:
        self._transport = transport
        self._clock = clock

    async def discover(
        self,
    ) -> tuple[CandidateSnapshot | Mapping[str, object], ...]:
        payload = await self._transport.fetch_volume_rank()
        ingested_at = self._clock()
        if ingested_at.tzinfo is None or ingested_at.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")
        if payload.provider != "KIS":
            raise ValueError("KIS volume candidate source requires a KIS payload")
        if payload.available_at > ingested_at:
            raise ValueError("KIS candidate payload cannot be available after ingestion")
        rows = payload.payload.get("volume_rank")
        if not isinstance(rows, list):
            raise ValueError("KIS volume-rank payload must contain a row list")
        snapshot_id = f"kis:volume-rank:{payload.source_hash}"
        status = (
            CandidateStatus.ELIGIBLE
            if payload.quality is DataQualityStatus.FRESH
            else CandidateStatus.REPORT_ONLY
        )
        issues = (
            ()
            if status is CandidateStatus.ELIGIBLE
            else ("KIS_VOLUME_RANK_SESSION_UNVERIFIED",)
        )
        candidates: list[CandidateSnapshot | Mapping[str, object]] = []
        for raw_row in rows:
            if not isinstance(raw_row, Mapping):
                candidates.append({"volume_rank_row": raw_row})
                continue
            row = cast(Mapping[str, object], raw_row)
            symbol = str(row.get("mksc_shrn_iscd", ""))
            display_name = str(row.get("hts_kor_isnm", ""))
            candidate_payload: dict[str, object] = {
                "market": Market.KR,
                "security_id": SecurityId(
                    value=uuid5(NAMESPACE_URL, f"prism:kr:{symbol}")
                ),
                "provider": "KIS",
                "provider_symbol": symbol,
                "display_name": display_name,
                "channel": CandidateChannel.CORE_PRISM,
                "source_id": "kis-volume-rank-v1",
                "source_snapshot_id": snapshot_id,
                "observed_at": payload.observed_at,
                "available_at": payload.available_at,
                "ingested_at": ingested_at,
                "as_of": ingested_at,
                "trigger_ids": ("KIS_VOLUME_RANK",),
                "raw_scores": {
                    "KIS_VOLUME_RANK.data_rank": row.get("data_rank", ""),
                    "KIS_VOLUME_RANK.acml_vol": row.get("acml_vol", ""),
                    "KIS_VOLUME_RANK.acml_tr_pbmn": row.get(
                        "acml_tr_pbmn", ""
                    ),
                },
                "evidence_ids": (f"{snapshot_id}:{symbol}",),
                "status": status,
                "issues": issues,
            }
            try:
                converted_scores = {
                    "KIS_VOLUME_RANK.data_rank": Decimal(
                        str(row.get("data_rank", ""))
                    ),
                    "KIS_VOLUME_RANK.acml_vol": Decimal(
                        str(row.get("acml_vol", ""))
                    ),
                    "KIS_VOLUME_RANK.acml_tr_pbmn": Decimal(
                        str(row.get("acml_tr_pbmn", ""))
                    ),
                }
                candidate_payload["raw_scores"] = converted_scores
                candidate = CandidateSnapshot.model_validate(candidate_payload)
            except (ArithmeticError, ValueError):
                candidate_payload["raw_scores"] = {
                    "KIS_VOLUME_RANK.data_rank": row.get("data_rank", ""),
                    "KIS_VOLUME_RANK.acml_vol": row.get("acml_vol", ""),
                    "KIS_VOLUME_RANK.acml_tr_pbmn": row.get(
                        "acml_tr_pbmn", ""
                    ),
                }
                candidates.append(candidate_payload)
            else:
                candidates.append(candidate)
        return tuple(candidates)
