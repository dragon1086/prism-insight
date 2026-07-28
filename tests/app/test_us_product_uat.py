from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest

from prism_app import us_product_uat
from prism_core.data.contracts import DataQualityStatus, MarketSnapshot, SecurityId


def test_us_product_uat_defaults_to_fmp_aapl_spy_and_gpt_5_6_sol() -> None:
    args = us_product_uat._parser().parse_args(
        [
            "--research-db",
            "research.sqlite",
            "--ops-db",
            "ops.sqlite",
            "--output",
            "report.md",
        ]
    )

    assert args.symbol == "AAPL"
    assert args.benchmark == "SPY"
    assert args.model == "gpt-5.6-sol"
    assert args.model_version == "gpt-5.6-sol"
    assert args.lookback_days >= 400


@pytest.mark.asyncio
async def test_live_fmp_pair_fetches_and_binds_split_coverage() -> None:
    as_of = datetime(2026, 7, 28, 4, 0, tzinfo=timezone.utc)
    stock_id = SecurityId(value=UUID("00000000-0000-0000-0000-000000000201"))
    benchmark_id = SecurityId(value=UUID("00000000-0000-0000-0000-000000000202"))

    def snapshot(snapshot_id: UUID, content_hash: str) -> MarketSnapshot:
        return MarketSnapshot(
            snapshot_id=snapshot_id,
            market="US",
            as_of_date=as_of,
            created_at=as_of,
            content_hash=content_hash,
            quality=DataQualityStatus.FRESH,
            symbol_mappings=(),
            price_bars=(),
            fundamentals=(),
            corporate_actions=(),
            evidence=(),
        )

    class PriceProvider:
        def __init__(self, value: MarketSnapshot) -> None:
            self.value = value

        async def fetch_result(self, **_kwargs):
            return SimpleNamespace(snapshot=self.value)

    class SplitTransport:
        calls = []
        evidence = ({"endpoint": "/stable/splits-calendar", "status_code": 200},)

        async def fetch(self, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(
                corporate_actions=(),
                coverage_evidence=(),
                latest_completed_session=as_of.date(),
                excluded_future_dates=(),
                request_evidence=self.evidence[0],
            )

    split_transport = SplitTransport()
    provider = us_product_uat.LiveFMPPairProvider(
        stock_provider=PriceProvider(
            snapshot(UUID("00000000-0000-0000-0000-000000000211"), "a" * 64)
        ),
        benchmark_provider=PriceProvider(
            snapshot(UUID("00000000-0000-0000-0000-000000000212"), "b" * 64)
        ),
        stock_id=stock_id,
        benchmark_id=benchmark_id,
        transports=(SimpleNamespace(evidence=()), SimpleNamespace(evidence=())),
        split_transport=split_transport,
        split_api_key=object(),
        split_instruments=(object(), object()),
    )

    result = await provider.fetch_result(
        security_ids=(stock_id, benchmark_id), as_of_date=as_of
    )

    assert len(split_transport.calls) == 1
    assert result.snapshot.market == "US"
    assert provider.corporate_action_evidence == split_transport.evidence
