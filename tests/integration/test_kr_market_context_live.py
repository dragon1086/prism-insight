from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Mapping, cast
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from prism_core.data.contracts import DataQualityStatus
from prism_core.data.providers.agentnews import AgentNewsFetchEvidence, AgentNewsFetchResult
from prism_core.data.providers.agentnews_models import AgentNewsBoard, AgentNewsSnapshot
from prism_core.data.providers.kis import ProviderPayload
from prism_core.data.providers.kis_http import (
    KISHTTPTransport,
    KISMarketDataCredentials,
    SecureFileKISTokenCache,
    TOKEN_PATH,
    VOLUME_RANK_PATH,
)
from prism_core.market import KRMarketContext, KRMarketRegime
from prism_core.market.composer import KRMarketContextComposer
from prism_core.market.krx import KRXMarketContextProvider, OfficialKRXEquityMarketClient


KST = ZoneInfo("Asia/Seoul")
UTC = ZoneInfo("UTC")
_EXACT_INVOCATION = (
    "PYTHONPATH=. PRISM_RUN_KR_CONTEXT_LIVE=1 "
    "PRISM_KR_CONTEXT_SMOKE_EVIDENCE=1 python -m pytest "
    "tests/integration/test_kr_market_context_live.py -q -rs -s"
)


def _kis_live_activation_available() -> bool:
    return all(os.environ.get(name) for name in ("KIS_APP_KEY", "KIS_APP_SECRET"))


def _krx_live_activation_available() -> bool:
    method = os.environ.get("KRX_LOGIN_METHOD", "krx").lower()
    required = ("KAKAO_ID", "KAKAO_PW") if method == "kakao" else ("KRX_ID", "KRX_PW")
    return all(os.environ.get(name) for name in required) or (
        OfficialKRXEquityMarketClient.default_session_copy_available()
    )


class _LocalAgentNewsProvider:
    """Satisfy the integrated composer port without making a third-party request."""

    async def fetch_result(self, board: AgentNewsBoard) -> AgentNewsFetchResult:
        assert board is AgentNewsBoard.KR
        fetched_at = datetime.now(tz=UTC)
        raw = (
            f'---\nupdated: "{fetched_at.isoformat()}"\n---\n'
            "# Local non-network KR market-context smoke scaffold\n"
        ).encode()
        snapshot = AgentNewsSnapshot.from_markdown(
            board=board,
            url=board.url,
            raw_body=raw,
            fetched_at=fetched_at,
            freshness_window=timedelta(minutes=1),
        )
        return AgentNewsFetchResult(
            snapshot=snapshot,
            attempts=(
                AgentNewsFetchEvidence(
                    url=board.url,
                    status_code=None,
                    fetched_at=fetched_at,
                    latency_ms=0,
                    content_hash=snapshot.content_hash,
                    outcome="LOCAL_NON_NETWORK_SCAFFOLD",
                ),
            ),
            used_last_known_good=False,
        )


class _BoundedKRXClient:
    """Allow exactly the five core official reads and no optional flow request."""

    def __init__(self) -> None:
        self._client = OfficialKRXEquityMarketClient()
        self.calls: list[dict[str, str]] = []

    def _guard_call(self, call: dict[str, str]) -> None:
        if len(self.calls) >= 5:
            raise RuntimeError("KRX smoke exhausted its five-request core budget")
        if call in self.calls:
            raise RuntimeError("KRX smoke rejected a duplicate core request")
        self.calls.append(call)

    def get_index_history(self, start: date, end: date, ticker: str) -> pd.DataFrame:
        if ticker != "1001" or start > end or (end - start).days > 60:
            raise ValueError("KRX smoke index request exceeded its approved scope")
        self._guard_call(
            {
                "operation": "index_history",
                "ticker": ticker,
                "start": start.isoformat(),
                "end": end.isoformat(),
            }
        )
        return self._client.get_index_history(start, end, ticker)

    def get_equity_ohlcv(self, session: date, market: str) -> pd.DataFrame:
        if market not in {"KOSPI", "KOSDAQ"}:
            raise ValueError("KRX smoke equity request exceeded its approved scope")
        self._guard_call(
            {
                "operation": "equity_ohlcv",
                "market": market,
                "session": session.isoformat(),
            }
        )
        return self._client.get_equity_ohlcv(session, market)

    def get_investor_flows(self, session: date, market: str) -> pd.DataFrame:
        del session, market
        raise NotImplementedError("optional KRX aggregate investor flow is not requested")

    def close(self) -> None:
        self._client.close()


class _RecordingKRXProvider:
    def __init__(self, provider: KRXMarketContextProvider) -> None:
        self._provider = provider
        self.payload: ProviderPayload | None = None
        self.error_type: str | None = None

    async def fetch_market_context(self, *, as_of: datetime) -> ProviderPayload:
        try:
            self.payload = await self._provider.fetch_market_context(as_of=as_of)
        except Exception as exc:
            self.error_type = type(exc).__name__
            raise
        return self.payload


pytestmark = [
    pytest.mark.live_kis,
    pytest.mark.live_kr_official,
    pytest.mark.skipif(
        os.environ.get("PRISM_RUN_KR_CONTEXT_LIVE") != "1",
        reason="set PRISM_RUN_KR_CONTEXT_LIVE=1 for the bounded KR context smoke",
    ),
]


@pytest.mark.asyncio
async def test_live_kis_krx_compose_one_bounded_sanitized_market_context() -> None:
    if not _kis_live_activation_available():
        pytest.skip("KIS live integration skipped: market-data credentials are unavailable")
    if not _krx_live_activation_available():
        pytest.skip("KRX live integration skipped: approved authenticated access is unavailable")

    transport = KISHTTPTransport(
        credentials=KISMarketDataCredentials.from_env(),
        symbols=(),
        timeout_seconds=8.0,
        max_response_bytes=1_000_000,
        min_request_interval_seconds=0.1,
        token_cache=SecureFileKISTokenCache(
            Path.home() / ".cache" / "prism-insight" / "kis-market-data-token.json"
        ),
    )
    krx_client = _BoundedKRXClient()
    krx_provider = _RecordingKRXProvider(
        KRXMarketContextProvider(
            client=krx_client,
            clock=lambda: datetime.now(tz=KST),
            history_calendar_days=60,
            timeout_seconds=8.0,
        )
    )
    composer = KRMarketContextComposer(
        kis_transport=transport,
        krx_provider=krx_provider,
        agentnews_provider=_LocalAgentNewsProvider(),
        clock=lambda: datetime.now(tz=KST),
    )

    try:
        context = await composer.compose()
    finally:
        krx_client.close()

    if (
        krx_provider.payload is None
        and krx_provider.error_type == "RuntimeError"
        and len(krx_client.calls) == 1
    ):
        pytest.skip(
            "KRX live integration skipped: approved authenticated access failed "
            "on the first bounded core request; no fixture fallback was used"
        )
    assert krx_provider.payload is not None, (
        "sufficient core KRX data was unavailable or invalid; "
        f"sanitized_error_type={krx_provider.error_type}; "
        f"completed_bounded_calls={len(krx_client.calls)}"
    )
    krx_payload = krx_provider.payload
    assert len(krx_client.calls) == 5
    assert [call["operation"] for call in krx_client.calls] == [
        "index_history",
        "equity_ohlcv",
        "equity_ohlcv",
        "equity_ohlcv",
        "equity_ohlcv",
    ]
    assert {call.get("market") for call in krx_client.calls[1:]} == {"KOSPI", "KOSDAQ"}

    kis_evidence = list(transport.evidence)
    assert 1 <= len(kis_evidence) <= 2
    assert [item["endpoint"] for item in kis_evidence] in (
        [VOLUME_RANK_PATH],
        [TOKEN_PATH, VOLUME_RANK_PATH],
    )
    assert all(item["status_code"] == 200 for item in kis_evidence)

    assert context.regime.regime is not KRMarketRegime.UNKNOWN
    assert context.quality in {DataQualityStatus.FRESH, DataQualityStatus.PARTIAL}
    assert context.action_eligible is (context.quality is DataQualityStatus.FRESH)
    assert context.missing_fields == ()
    metrics = {metric.name: metric for metric in context.index_state}
    assert set(metrics) == {"kospi_close", "kospi_ma20", "kospi_return_10d_pct"}
    assert metrics["kospi_close"].value > 0
    assert metrics["kospi_ma20"].value > 0
    assert metrics["kospi_return_10d_pct"].value.is_finite()
    assert all(metric.source == "KRX" for metric in metrics.values())

    breadth = {metric.name: metric.value for metric in context.breadth}
    assert set(breadth) == {
        "advance_count",
        "decline_count",
        "eligible_equity_count",
        "excluded_non_equity_count",
        "unclassified_equity_count",
        "unchanged_count",
    }
    assert breadth["eligible_equity_count"] > 0
    assert breadth["excluded_non_equity_count"] >= 0
    assert (
        breadth["advance_count"]
        + breadth["decline_count"]
        + breadth["unchanged_count"]
        == breadth["eligible_equity_count"]
    )
    assert all(value == value.to_integral_value() for value in breadth.values())
    raw_breadth = cast(Mapping[str, object], krx_payload.payload["equity_breadth"])
    assert raw_breadth["universe"] == "KOSPI_KOSDAQ_EQUITIES"

    source_clocks = {source.source: source for source in context.source_clocks}
    assert {"KIS", "KRX"} <= set(source_clocks)
    for source in source_clocks.values():
        assert source.observed_at <= source.available_at <= source.ingested_at
    assert context.timing.as_of == context.timing.ingested_at
    assert {"DART", "KIND", "KRX_INVESTOR_FLOWS"} <= set(
        context.optional_missing_sources
    )

    serialized = context.to_canonical_json()
    restored = KRMarketContext.model_validate_json(serialized)
    assert restored.to_canonical_json() == serialized
    assert restored.content_hash == context.content_hash

    if os.environ.get("PRISM_KR_CONTEXT_SMOKE_EVIDENCE") == "1":
        public_limitations = [
            "DART and KIND were not requested by this bounded smoke",
            "aggregate KRX investor flow is unavailable in the approved wrapper",
            "ETF/ETN exclusion is guaranteed by the equity-only endpoint contract; "
            "the endpoint does not return excluded product rows",
        ]
        evidence = {
            "result": "PASS",
            "exact_invocation": _EXACT_INVOCATION,
            "providers": ["KIS", "KRX"],
            "network_scope": {
                "kis_endpoints": [item["endpoint"] for item in kis_evidence],
                "krx_operations": krx_client.calls,
            },
            "session_date": context.timing.session_date.isoformat(),
            "as_of": context.timing.as_of.isoformat(),
            "quality": context.quality.value,
            "action_eligible": context.action_eligible,
            "index_metrics": {
                name: str(metric.value) for name, metric in sorted(metrics.items())
            },
            "breadth": {name: str(value) for name, value in sorted(breadth.items())},
            "regime": context.regime.regime.value,
            "regime_version": context.regime.version,
            "optional_sources": {
                "DART": "SKIPPED",
                "KIND": "SKIPPED",
                "KRX_INVESTOR_FLOWS": "UNAVAILABLE",
            },
            "public_data_limitations": public_limitations,
            "residual_risks": [
                "authenticated KRX wrapper behavior and entitlement may change",
                "KIS token issuance is provider-rate-limited; an owner-only cache may be reused",
                "this manual smoke does not prove scheduler or operated readiness",
            ],
            "prohibited_effects_observed": False,
            "persisted_output": False,
        }
        assert all(isinstance(value, Decimal) for value in breadth.values())
        print(json.dumps(evidence, ensure_ascii=False, sort_keys=True))
