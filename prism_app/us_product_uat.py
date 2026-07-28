"""User-operated US Phase 1 SHADOW product command using live FMP and AgentNews US."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence
from uuid import NAMESPACE_URL, uuid5

from prism_app.live_kr_evidence import FMPIncomeStatementClient, LiveUSEvidenceProvider
from prism_app.market_snapshot_composer import USProductSnapshotComposer
from prism_app.oauth_llm import ChatGPTOAuthRuntime
from prism_app.product_composition import ProductRunConfig, run_us_shadow_product
from prism_app.product_uat import require_product_runtime_proof, sanitized_failure_payload
from prism_core.data.contracts import DataQualityStatus, MarketSnapshot, SecurityId
from prism_core.data.providers.agentnews import AgentNewsFetchResult, AgentNewsProvider
from prism_core.data.providers.agentnews_models import AgentNewsBoard
from prism_core.data.providers.fmp import FMPFetchResult, FMPInstrument, FMPMarketDataProvider
from prism_core.data.providers.fmp_http import FMPHTTPTransport, load_fmp_api_key_from_env
from prism_core.data.providers.fmp_models import FMPApiKey
from prism_core.data.providers.fmp_splits_http import (
    FMPSplitCalendarHTTPTransport,
    FMPSplitCoverage,
)
from prism_core.feedback.repository import canonical_json
from prism_core.runtime.settings import ProductMode, RuntimeSettings


_QUALITY_ORDER = {
    DataQualityStatus.FRESH: 0,
    DataQualityStatus.PARTIAL: 1,
    DataQualityStatus.STALE: 2,
    DataQualityStatus.CONFLICT: 3,
    DataQualityStatus.UNAVAILABLE: 4,
}


def _security_id(symbol: str) -> SecurityId:
    return SecurityId(value=uuid5(NAMESPACE_URL, f"prism:us:{symbol}"))


@dataclass(frozen=True)
class _PairFetchResult:
    snapshot: MarketSnapshot


class LiveFMPPairProvider:
    """Make exactly one bounded FMP price request per configured US symbol."""

    def __init__(
        self,
        *,
        stock_provider: FMPMarketDataProvider,
        benchmark_provider: FMPMarketDataProvider,
        stock_id: SecurityId,
        benchmark_id: SecurityId,
        transports: tuple[FMPHTTPTransport, FMPHTTPTransport],
        split_transport: FMPSplitCalendarHTTPTransport,
        split_api_key: FMPApiKey,
        split_instruments: tuple[FMPInstrument, FMPInstrument],
    ) -> None:
        self._stock_provider = stock_provider
        self._benchmark_provider = benchmark_provider
        self._stock_id = stock_id
        self._benchmark_id = benchmark_id
        self._transports = transports
        self._split_transport = split_transport
        self._split_api_key = split_api_key
        self._split_instruments = split_instruments

    async def fetch_result(
        self, *, security_ids: tuple[SecurityId, ...], as_of_date: datetime
    ) -> _PairFetchResult:
        if security_ids != (self._stock_id, self._benchmark_id):
            raise ValueError("US pair provider requires the configured stock and benchmark")
        stock, benchmark, split_coverage = await asyncio.gather(
            self._stock_provider.fetch_result(
                security_ids=(self._stock_id,), as_of_date=as_of_date
            ),
            self._benchmark_provider.fetch_result(
                security_ids=(self._benchmark_id,), as_of_date=as_of_date
            ),
            self._split_transport.fetch(
                api_key=self._split_api_key,
                as_of=as_of_date,
                instruments=self._split_instruments,
            ),
        )
        return _PairFetchResult(
            snapshot=_merge_snapshots(stock, benchmark, split_coverage)
        )

    @property
    def evidence(self) -> tuple[dict[str, object], ...]:
        return tuple(
            {
                "provider": "FMP",
                "host": "financialmodelingprep.com",
                "endpoint": item.endpoint,
                "status_code": item.status_code,
                "received_at": item.received_at.isoformat(),
                "latency_ms": item.latency_ms,
                "request_correlation": item.request_correlation,
                "provider_version": item.provider_version,
            }
            for transport in self._transports
            for item in transport.evidence
        )

    @property
    def corporate_action_evidence(self) -> tuple[dict[str, object], ...]:
        return self._split_transport.evidence


def _merge_snapshots(
    stock: FMPFetchResult,
    benchmark: FMPFetchResult,
    split_coverage: FMPSplitCoverage,
) -> MarketSnapshot:
    snapshots = (stock.snapshot, benchmark.snapshot)
    if snapshots[0].as_of_date != snapshots[1].as_of_date:
        raise RuntimeError("FMP stock and benchmark snapshots have different PIT boundaries")
    identity = {
        "market": "US",
        "as_of": snapshots[0].as_of_date,
        "snapshots": [snapshot.content_hash for snapshot in snapshots],
        "corporate_actions": [
            item.model_dump(mode="json") for item in split_coverage.corporate_actions
        ],
        "corporate_action_coverage": [
            item.model_dump(mode="json") for item in split_coverage.coverage_evidence
        ],
    }
    content_hash = hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()
    return MarketSnapshot(
        snapshot_id=uuid5(NAMESPACE_URL, f"prism:us:fmp-pair:{content_hash}"),
        market="US",
        as_of_date=snapshots[0].as_of_date,
        created_at=max(snapshot.created_at for snapshot in snapshots),
        content_hash=content_hash,
        quality=max(
            (snapshot.quality for snapshot in snapshots), key=_QUALITY_ORDER.__getitem__
        ),
        symbol_mappings=tuple(
            item for snapshot in snapshots for item in snapshot.symbol_mappings
        ),
        price_bars=tuple(item for snapshot in snapshots for item in snapshot.price_bars),
        fundamentals=(),
        corporate_actions=split_coverage.corporate_actions,
        evidence=split_coverage.coverage_evidence,
    )


def _provider(
    *, symbol: str, benchmark: str, lookback_days: int
) -> tuple[LiveFMPPairProvider, SecurityId, SecurityId]:
    api_key = load_fmp_api_key_from_env()
    stock_id = _security_id(symbol)
    benchmark_id = _security_id(benchmark)
    stock_transport = FMPHTTPTransport(lookback_days=lookback_days)
    benchmark_transport = FMPHTTPTransport(lookback_days=lookback_days)
    stock_instrument = FMPInstrument(
        security_id=stock_id,
        fmp_symbol=symbol,
        valid_from=datetime(1900, 1, 1, tzinfo=timezone.utc),
    )
    benchmark_instrument = FMPInstrument(
        security_id=benchmark_id,
        fmp_symbol=benchmark,
        valid_from=datetime(1900, 1, 1, tzinfo=timezone.utc),
    )

    def one_provider(
        security_id: SecurityId, provider_symbol: str, transport: FMPHTTPTransport
    ) -> FMPMarketDataProvider:
        return FMPMarketDataProvider(
            transport=transport,
            api_key=api_key,
            instruments=(
                FMPInstrument(
                    security_id=security_id,
                    fmp_symbol=provider_symbol,
                    valid_from=datetime(1900, 1, 1, tzinfo=timezone.utc),
                ),
            ),
            clock=lambda: datetime.now(timezone.utc),
            max_attempts=2,
            max_pages=1,
            max_requests=2,
        )

    return (
        LiveFMPPairProvider(
            stock_provider=one_provider(stock_id, symbol, stock_transport),
            benchmark_provider=one_provider(benchmark_id, benchmark, benchmark_transport),
            stock_id=stock_id,
            benchmark_id=benchmark_id,
            transports=(stock_transport, benchmark_transport),
            split_transport=FMPSplitCalendarHTTPTransport(
                lookback_days=lookback_days
            ),
            split_api_key=api_key,
            split_instruments=(stock_instrument, benchmark_instrument),
        ),
        stock_id,
        benchmark_id,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the read-only US FMP→features→quant→OAuth LLM→SQLite→SHADOW "
            "product path. No account or broker capability is loaded."
        )
    )
    parser.add_argument("--symbol", default="AAPL")
    parser.add_argument("--benchmark", default="SPY")
    parser.add_argument("--as-of", type=datetime.fromisoformat)
    parser.add_argument("--research-db", required=True, type=Path)
    parser.add_argument("--ops-db", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--base-report", type=Path)
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--model-version", default="gpt-5.6-sol")
    parser.add_argument("--code-version", default="uncommitted-phase1-us-product")
    parser.add_argument("--lookback-days", type=int, default=400)
    return parser


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    as_of = args.as_of or datetime.now(timezone.utc)
    if as_of.tzinfo is None:
        raise ValueError("--as-of must include a timezone offset")
    provider, stock_id, benchmark_id = _provider(
        symbol=args.symbol,
        benchmark=args.benchmark,
        lookback_days=args.lookback_days,
    )
    agentnews = AgentNewsProvider()
    agentnews_results: list[AgentNewsFetchResult] = []

    async def fetch_agentnews_us():
        result = await agentnews.fetch_result(AgentNewsBoard.US)
        agentnews_results.append(result)
        return result.snapshot

    fundamentals = FMPIncomeStatementClient.from_env()
    composer = USProductSnapshotComposer(
        provider=provider,
        stock_id=stock_id,
        benchmark_id=benchmark_id,
        stock_symbol=args.symbol,
        benchmark_symbol=args.benchmark,
        evidence_provider=LiveUSEvidenceProvider(
            agentnews_fetcher=fetch_agentnews_us,
            fundamentals_client=fundamentals,
            fmp_symbol=args.symbol,
        ),
    )
    settings = RuntimeSettings(
        product_mode=ProductMode.SHADOW,
        broker_enabled=False,
        research_db_path=args.research_db,
        ops_db_path=args.ops_db,
    )
    prior_auth_mode = os.environ.get("PRISM_OPENAI_AUTH_MODE")
    if prior_auth_mode is None:
        os.environ["PRISM_OPENAI_AUTH_MODE"] = "chatgpt_oauth"
    try:
        async with ChatGPTOAuthRuntime() as backend:
            result = await run_us_shadow_product(
                composer=composer,
                backend=backend,
                settings=settings,
                config=ProductRunConfig(
                    evaluated_at=as_of,
                    run_type="daily-close",
                    model_id=args.model,
                    model_version=args.model_version,
                    code_version=args.code_version,
                    owner_id=f"phase1-us-uat-{os.getpid()}",
                ),
                output_path=args.output,
                base_report_path=args.base_report,
            )
    finally:
        if prior_auth_mode is None:
            os.environ.pop("PRISM_OPENAI_AUTH_MODE", None)
    require_product_runtime_proof(
        result.analysis, idempotent_replay=result.idempotent_replay
    )
    board_evidence = []
    for fetch_result in agentnews_results:
        board_evidence.extend(
            {
                "provider": "AgentNews",
                "host": "agentnews.md",
                "endpoint": "/finance.md",
                "status_code": attempt.status_code,
                "received_at": attempt.fetched_at.isoformat(),
                "latency_ms": attempt.latency_ms,
                "outcome": attempt.outcome,
            }
            for attempt in fetch_result.attempts
        )
    return {
        "stage": "PHASE1_US_SHADOW_PRODUCT",
        "status": "PERSISTED_READBACK_VERIFIED",
        "job_key": result.analysis.job_key,
        "invocation_id": result.invocation_id,
        "data_snapshot_id": str(result.analysis.data_snapshot_id),
        "quality_disposition": result.analysis.quality_decision.disposition.value,
        "strategy_count": len(result.analysis.strategies),
        "network_evidence": {
            "fmp_price": provider.evidence,
            "fmp_corporate_actions": provider.corporate_action_evidence,
            "fmp_fundamentals": fundamentals.evidence,
            "agentnews_us": board_evidence,
            "oauth": {
                "auth_mode": "chatgpt_oauth",
                "requested_and_forwarded_model": args.model,
                "structured_response_count": len(result.analysis.strategies),
                "tool_count": 0,
            },
        },
        "output": str(result.output_path),
        "broker_called": False,
        "schedule_activated": False,
        "uat_accepted": False,
        "operational_readiness": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        payload = asyncio.run(_run(args))
        exit_code = 0
    except Exception as exc:  # noqa: BLE001 - stable sanitized local evidence only
        payload = sanitized_failure_payload(exc)
        payload["stage"] = "PHASE1_US_SHADOW_PRODUCT"
        exit_code = 2
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
