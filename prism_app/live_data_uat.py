"""User-visible, read-only KIS live-data UAT for the Phase 1 quality boundary."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, Sequence
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from prism_core.data.contracts import DataQualityStatus, SecurityId
from prism_core.data.providers.kis import (
    KISInstrument,
    KISMarketDataProvider,
)
from prism_core.data.providers.kis_http import (
    KISHTTPTransport,
    KISMarketDataCredentials,
    SecureFileKISTokenCache,
)
from prism_core.features.market_inputs import build_feature_computation_input
from prism_core.features.service import PriceBasis
from prism_core.strategies.contracts import Market


KST = ZoneInfo("Asia/Seoul")


class KISFetchProvider(Protocol):
    async def fetch_result(
        self, *, security_ids: tuple[SecurityId, ...], as_of_date: datetime
    ) -> Any: ...


async def run_kr_live_data_uat(
    *,
    provider: KISFetchProvider,
    stock_id: SecurityId,
    benchmark_id: SecurityId,
    stock_symbol: str,
    benchmark_symbol: str,
    as_of: datetime,
) -> dict[str, Any]:
    """Fetch real provider data and expose the exact fail-closed quality result."""
    try:
        result = await provider.fetch_result(
            security_ids=(stock_id, benchmark_id),
            as_of_date=as_of,
        )
        snapshot = result.snapshot
        quality = {
            "calendar": DataQualityStatus.FRESH,
            "evidence": DataQualityStatus.UNAVAILABLE,
            "fundamental": DataQualityStatus.UNAVAILABLE,
            "price": snapshot.quality,
            "regime": DataQualityStatus.UNAVAILABLE,
        }
        inputs = build_feature_computation_input(
            snapshot=snapshot,
            market=Market.KR,
            security_id=stock_id,
            benchmark_security_id=benchmark_id,
            price_basis=PriceBasis.RAW,
            observations=(),
            field_quality=quality,
        )
        counts = Counter(bar.provider_symbol for bar in snapshot.price_bars)
    except Exception as exc:  # noqa: BLE001 - fail closed and redact provider detail
        return _unavailable_result(
            stock_symbol=stock_symbol,
            benchmark_symbol=benchmark_symbol,
            as_of=as_of,
            error_type=type(exc).__name__,
        )
    return {
        "stage": "PHASE1_LIVE_DATA_UAT",
        "as_of": as_of.isoformat(),
        "market": "KR",
        "provider": "KIS",
        "provider_quality": snapshot.quality.value,
        "provider_error_type": None,
        "stock_symbol": stock_symbol,
        "benchmark_symbol": benchmark_symbol,
        "bar_counts": {
            stock_symbol: counts.get(stock_symbol, 0),
            benchmark_symbol: counts.get(benchmark_symbol, 0),
        },
        "aligned_sessions": len(inputs.prices),
        "quality_disposition": inputs.quality_decision.disposition.value,
        "quality_reasons": list(inputs.quality_decision.reasons),
        "missing_inputs": ["catalyst_evidence", "regime_observation"],
        "decision": "NO_ENTRY",
        "llm_called": False,
        "broker_called": False,
        "operational_readiness": False,
        "uat_passed": False,
    }


def _unavailable_result(
    *, stock_symbol: str, benchmark_symbol: str, as_of: datetime, error_type: str
) -> dict[str, Any]:
    return {
        "stage": "PHASE1_LIVE_DATA_UAT",
        "as_of": as_of.isoformat(),
        "market": "KR",
        "provider": "KIS",
        "provider_quality": "UNAVAILABLE",
        "provider_error_type": error_type,
        "stock_symbol": stock_symbol,
        "benchmark_symbol": benchmark_symbol,
        "bar_counts": {stock_symbol: 0, benchmark_symbol: 0},
        "aligned_sessions": 0,
        "quality_disposition": "REJECT",
        "quality_reasons": ["provider_or_normalization_unavailable"],
        "missing_inputs": [
            "market_data",
            "catalyst_evidence",
            "regime_observation",
        ],
        "decision": "NO_ENTRY",
        "llm_called": False,
        "broker_called": False,
        "operational_readiness": False,
        "uat_passed": False,
    }


def _security_id(label: str) -> SecurityId:
    return SecurityId(value=uuid5(NAMESPACE_URL, f"prism:kr:{label}"))


def _build_live_provider(
    *, stock_symbol: str, benchmark_symbol: str, lookback_calendar_days: int
) -> tuple[KISMarketDataProvider, SecurityId, SecurityId]:
    stock_id = _security_id(stock_symbol)
    benchmark_id = _security_id(benchmark_symbol)
    credentials = KISMarketDataCredentials.from_env()
    transport = KISHTTPTransport(
        credentials=credentials,
        symbols=(stock_symbol, benchmark_symbol),
        lookback_calendar_days=lookback_calendar_days,
        timeout_seconds=10.0,
        max_response_bytes=2_000_000,
        min_request_interval_seconds=0.1,
        token_cache=SecureFileKISTokenCache(
            Path.home() / ".cache" / "prism-insight" / "kis-market-data-token.json"
        ),
    )
    provider = KISMarketDataProvider(
        transport=transport,
        instruments=(
            KISInstrument(security_id=stock_id, kis_symbol=stock_symbol),
            KISInstrument(security_id=benchmark_id, kis_symbol=benchmark_symbol),
        ),
        clock=lambda: datetime.now(tz=KST),
        max_attempts=2,
    )
    return provider, stock_id, benchmark_id


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the read-only KR Phase 1 live-data UAT. This never calls an LLM "
            "or any broker/account/order endpoint."
        )
    )
    parser.add_argument("--symbol", default="005930")
    parser.add_argument("--benchmark", default="069500")
    parser.add_argument("--lookback-days", type=int, default=60)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    provider, stock_id, benchmark_id = _build_live_provider(
        stock_symbol=args.symbol,
        benchmark_symbol=args.benchmark,
        lookback_calendar_days=args.lookback_days,
    )
    result = asyncio.run(
        run_kr_live_data_uat(
            provider=provider,
            stock_id=stock_id,
            benchmark_id=benchmark_id,
            stock_symbol=args.symbol,
            benchmark_symbol=args.benchmark,
            as_of=datetime.now(tz=KST),
        )
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result["uat_passed"] is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
