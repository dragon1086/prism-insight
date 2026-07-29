from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from prism_core.data.providers.agentnews import AgentNewsProvider
from prism_core.data.providers.kis_http import (
    KISHTTPTransport,
    KISMarketDataCredentials,
    SecureFileKISTokenCache,
    VOLUME_RANK_PATH,
)
from prism_core.market import KRMarketContext, KRMarketRegime
from prism_core.market.composer import KRMarketContextComposer


KST = ZoneInfo("Asia/Seoul")

pytestmark = [
    pytest.mark.live_kis,
    pytest.mark.live_agentnews,
    pytest.mark.skipif(
        os.environ.get("PRISM_RUN_KR_CONTEXT_LIVE") != "1",
        reason="set PRISM_RUN_KR_CONTEXT_LIVE=1 for the bounded KR context smoke",
    ),
]


@pytest.mark.asyncio
async def test_live_kis_and_agentnews_compose_one_sanitized_context_readback() -> None:
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
    agentnews = AgentNewsProvider(
        max_attempts=2,
        connect_timeout_seconds=3,
        read_timeout_seconds=7,
        total_timeout_seconds=10,
        max_response_bytes=131_072,
    )
    composer = KRMarketContextComposer(
        kis_transport=transport,
        agentnews_provider=agentnews,
        clock=lambda: datetime.now(tz=KST),
    )

    context = await composer.compose()

    assert context.regime.regime is KRMarketRegime.UNKNOWN
    assert context.action_eligible is False
    assert context.evidence_ids
    assert [item["endpoint"] for item in transport.evidence][-1] == VOLUME_RANK_PATH
    assert all(item["status_code"] == 200 for item in transport.evidence)

    output_path = Path(
        os.environ.get(
            "PRISM_KR_CONTEXT_EVIDENCE_PATH",
            ".hermes/uat/kr-market-context-live.json",
        )
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    artifact = {
        "artifact_kind": "KR_MARKET_CONTEXT_LIVE_READBACK",
        "context": context.model_dump(mode="json"),
        "context_content_hash": context.content_hash,
        "disposition": context.disposition.value,
        "action_eligible": context.action_eligible,
        "kis_transport_evidence": list(transport.evidence),
        "agentnews": {
            "provider": context.supplemental_evidence[0].provider,
            "quality": context.supplemental_evidence[0].quality.value,
            "evidence_id": context.supplemental_evidence[0].evidence_id,
        },
        "observed_kis_endpoints": [
            item["endpoint"] for item in transport.evidence
        ],
    }
    output_path.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    readback = json.loads(output_path.read_text(encoding="utf-8"))
    restored = KRMarketContext.model_validate_json(
        json.dumps(readback["context"], ensure_ascii=False)
    )

    assert restored.content_hash == readback["context_content_hash"]
    assert restored.to_canonical_json() == context.to_canonical_json()
