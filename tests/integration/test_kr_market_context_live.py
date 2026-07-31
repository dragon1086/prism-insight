from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from prism_core.data.providers.agentnews import AgentNewsProvider
from prism_core.data.contracts import DataQualityStatus
from prism_core.data.providers.kis_http import (
    KISHTTPTransport,
    KISMarketDataCredentials,
    SecureFileKISTokenCache,
    VOLUME_RANK_PATH,
)
from prism_core.market import KRMarketContext, KRMarketRegime
from prism_core.market.composer import KRMarketContextComposer
from prism_core.market.krx import KRXMarketContextProvider, OfficialKRXEquityMarketClient


KST = ZoneInfo("Asia/Seoul")


def _krx_live_activation_available() -> bool:
    method = os.environ.get("KRX_LOGIN_METHOD", "krx").lower()
    required = (
        ("KAKAO_ID", "KAKAO_PW")
        if method == "kakao"
        else ("KRX_ID", "KRX_PW")
    )
    return all(os.environ.get(name) for name in required) or (
        OfficialKRXEquityMarketClient.default_session_copy_available()
    )


def _krx_private_session_metadata() -> dict[str, tuple[int, int, int, str] | None]:
    paths = (
        Path.home() / ".krx_session.json",
        Path.home() / ".krx_cookies.json",
        Path.home() / ".krx_session.lock",
        Path.home() / ".krx_isin_cache.json",
    )
    return {
        path.name: None
        if not path.exists()
        else (
            path.stat().st_mtime_ns,
            path.stat().st_size,
            path.stat().st_mode,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in paths
    }

pytestmark = [
    pytest.mark.live_kis,
    pytest.mark.live_kr_official,
    pytest.mark.live_agentnews,
    pytest.mark.skipif(
        os.environ.get("PRISM_RUN_KR_CONTEXT_LIVE") != "1",
        reason="set PRISM_RUN_KR_CONTEXT_LIVE=1 for the bounded KR context smoke",
    ),
]


@pytest.mark.asyncio
async def test_live_kis_and_agentnews_compose_one_sanitized_context_readback() -> None:
    if not _krx_live_activation_available():
        pytest.skip("KRX live integration blocked: authenticated wrapper credentials are unavailable")
    private_session_before = _krx_private_session_metadata()
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
        krx_provider=KRXMarketContextProvider(clock=lambda: datetime.now(tz=KST)),
        agentnews_provider=agentnews,
        clock=lambda: datetime.now(tz=KST),
    )

    try:
        context = await composer.compose()
    finally:
        assert _krx_private_session_metadata() == private_session_before

    assert context.regime.regime is not KRMarketRegime.UNKNOWN
    assert context.quality in {DataQualityStatus.FRESH, DataQualityStatus.PARTIAL}
    assert context.action_eligible is (context.quality is DataQualityStatus.FRESH)
    assert context.missing_fields == ()
    assert {metric.name for metric in context.index_state} == {
        "kospi_close",
        "kospi_ma20",
        "kospi_return_10d_pct",
    }
    assert {metric.name for metric in context.breadth} == {
        "advance_count",
        "decline_count",
        "eligible_equity_count",
        "excluded_non_equity_count",
        "unclassified_equity_count",
        "unchanged_count",
    }
    assert all(metric.source == "KRX" for metric in context.breadth)
    assert context.optional_missing_sources[:2] == ("DART", "KIND")
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
