from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfo

import pytest

from prism_core.candidates.contracts import CandidateSnapshot
from prism_core.candidates.kis_volume import KISVolumeCandidateSource
from prism_core.candidates.reconcile import reconcile_candidates
from prism_core.data.providers.kis_http import (
    KISHTTPTransport,
    KISMarketDataCredentials,
    SecureFileKISTokenCache,
    TOKEN_PATH,
    VOLUME_RANK_PATH,
)


KST = ZoneInfo("Asia/Seoul")

pytestmark = [
    pytest.mark.live_kis,
    pytest.mark.skipif(
        os.environ.get("PRISM_RUN_KIS_LIVE") != "1",
        reason="set PRISM_RUN_KIS_LIVE=1 for the authorized KIS market-data smoke",
    ),
]


@pytest.mark.asyncio
async def test_live_kis_volume_candidates_reconcile_without_truncation() -> None:
    transport = KISHTTPTransport(
        credentials=KISMarketDataCredentials.from_env(),
        symbols=(),
        timeout_seconds=10.0,
        max_response_bytes=1_000_000,
        min_request_interval_seconds=0.1,
        token_cache=SecureFileKISTokenCache(
            Path.home() / ".cache" / "prism-insight" / "kis-market-data-token.json"
        ),
    )
    source = KISVolumeCandidateSource(
        transport=transport,
        clock=lambda: datetime.now(tz=KST),
    )

    candidates = await source.discover()
    reconciliation = reconcile_candidates(candidates)

    assert candidates
    assert all(isinstance(item, CandidateSnapshot) for item in candidates)
    snapshots = cast(tuple[CandidateSnapshot, ...], candidates)
    assert reconciliation.input_count == len(candidates)
    assert reconciliation.unique_identity_count == len(candidates)
    assert reconciliation.included_identity_count == len(candidates)
    assert reconciliation.included_source_count == len(candidates)
    assert reconciliation.excluded == ()
    assert reconciliation.duplicate_source_count == 0
    assert reconciliation.invalid_record_count == 0
    assert {item.provider for item in snapshots} == {"KIS"}
    assert {item.channel.value for item in snapshots} == {"CORE_PRISM"}
    assert all(item.observed_at <= item.available_at <= item.as_of for item in snapshots)
    assert all(item.available_at <= item.ingested_at for item in snapshots)
    endpoints = [item["endpoint"] for item in transport.evidence]
    assert endpoints[-1] == VOLUME_RANK_PATH
    assert set(endpoints) <= {TOKEN_PATH, VOLUME_RANK_PATH}
    assert all(item["status_code"] == 200 for item in transport.evidence)

    if os.environ.get("PRISM_KIS_SMOKE_EVIDENCE") == "1":
        print(
            json.dumps(
                {
                    "provider": "KIS",
                    "endpoint": VOLUME_RANK_PATH,
                    "input_count": len(snapshots),
                    "input_unique_count": len(
                        {(item.market.value, str(item.security_id.value)) for item in snapshots}
                    ),
                    "reconciled_unique_count": reconciliation.unique_identity_count,
                    "reconciled_included_count": reconciliation.included_identity_count,
                    "truncated_count": reconciliation.truncated_candidate_count,
                    "provider_symbols": [item.provider_symbol for item in snapshots],
                    "security_ids": [str(item.security_id.value) for item in snapshots],
                    "channels": sorted({item.channel.value for item in snapshots}),
                    "candidate_statuses": sorted(
                        {item.status.value for item in snapshots}
                    ),
                    "issues": sorted(
                        {issue for item in snapshots for issue in item.issues}
                    ),
                    "source_snapshot_ids": sorted(
                        {item.source_snapshot_id for item in snapshots}
                    ),
                    "observed_at": min(item.observed_at for item in snapshots).isoformat(),
                    "available_at": max(item.available_at for item in snapshots).isoformat(),
                    "ingested_at": max(item.ingested_at for item in snapshots).isoformat(),
                    "as_of": max(item.as_of for item in snapshots).isoformat(),
                    "transport_evidence": transport.evidence,
                    "score_policy": reconciliation.score_policy,
                    "ordering_policy": reconciliation.ordering_policy,
                },
                sort_keys=True,
            )
        )
