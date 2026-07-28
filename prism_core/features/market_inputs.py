"""Fail-closed adapter from normalized market snapshots to feature inputs."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Mapping

from prism_core.data.contracts import (
    DataQualityStatus,
    MarketSnapshot,
    PriceBar,
    SecurityId,
)
from prism_core.data.quality import DataQualityGate
from prism_core.features.service import (
    BenchmarkPoint,
    FeatureComputationInput,
    NumericObservation,
    PriceBasis,
    PricePoint,
)
from prism_core.strategies.contracts import Market


_CORE_FIELDS = frozenset({"calendar", "evidence", "price", "regime"})
_REPORT_ONLY_FIELDS = frozenset({"fundamental"})


def build_feature_computation_input(
    *,
    snapshot: MarketSnapshot,
    market: Market,
    security_id: SecurityId,
    benchmark_security_id: SecurityId,
    price_basis: PriceBasis,
    observations: tuple[NumericObservation, ...],
    field_quality: Mapping[str, DataQualityStatus],
) -> FeatureComputationInput:
    """Build aligned PIT inputs without inventing prices or analytic observations."""
    if not isinstance(snapshot, MarketSnapshot):
        raise TypeError("snapshot must be MarketSnapshot")
    if not isinstance(market, Market):
        raise TypeError("market must be Market")
    if snapshot.market != market.value:
        raise ValueError("snapshot market does not match requested market")
    if not isinstance(security_id, SecurityId) or not isinstance(
        benchmark_security_id, SecurityId
    ):
        raise TypeError("security identities must be SecurityId values")
    if security_id == benchmark_security_id:
        raise ValueError("security and benchmark identities must be distinct")
    if not isinstance(price_basis, PriceBasis):
        raise TypeError("price_basis must be PriceBasis")
    if any(not isinstance(item, NumericObservation) for item in observations):
        raise TypeError("observations must contain NumericObservation values")
    if not isinstance(field_quality, Mapping) or any(
        not isinstance(name, str)
        or not name.strip()
        or not isinstance(status, DataQualityStatus)
        for name, status in field_quality.items()
    ):
        raise TypeError("field_quality must map names to DataQualityStatus")
    missing_core = sorted(_CORE_FIELDS - set(field_quality))
    if missing_core:
        raise ValueError("missing core quality fields: " + ", ".join(missing_core))

    stock_by_session = _one_bar_per_session(snapshot, security_id)
    benchmark_by_session = _one_bar_per_session(snapshot, benchmark_security_id)
    sessions = tuple(sorted(set(stock_by_session) & set(benchmark_by_session)))
    if not sessions:
        raise ValueError("security and benchmark have no aligned sessions")
    if len(sessions) != len(stock_by_session) or len(sessions) != len(
        benchmark_by_session
    ):
        raise ValueError("security and benchmark sessions must align exactly")

    selected = tuple(stock_by_session[session] for session in sessions) + tuple(
        benchmark_by_session[session] for session in sessions
    )
    if (
        snapshot.quality is not DataQualityStatus.FRESH
        or any(bar.quality is not DataQualityStatus.FRESH for bar in selected)
    ) and field_quality["price"] is DataQualityStatus.FRESH:
        raise ValueError("field quality cannot override snapshot quality to FRESH")

    prices = tuple(
        _price_point(stock_by_session[session], price_basis) for session in sessions
    )
    benchmark_points = tuple(
        _benchmark_point(benchmark_by_session[session], price_basis)
        for session in sessions
    )
    sorted_quality = tuple(sorted(field_quality.items()))
    decision = DataQualityGate(
        core_fields=_CORE_FIELDS,
        report_only_fields=_REPORT_ONLY_FIELDS,
    ).evaluate(dict(sorted_quality))
    return FeatureComputationInput(
        data_snapshot_id=snapshot.snapshot_id,
        market=market,
        security_id=security_id,
        as_of=snapshot.as_of_date,
        price_basis=price_basis,
        prices=prices,
        benchmark_points=benchmark_points,
        observations=observations,
        field_quality=sorted_quality,
        quality_decision=decision,
    )


def _one_bar_per_session(
    snapshot: MarketSnapshot, security_id: SecurityId
) -> dict[date, PriceBar]:
    selected: dict[date, PriceBar] = {}
    for bar in snapshot.price_bars:
        if bar.security_id != security_id:
            continue
        session = bar.bar_start.date()
        if session in selected:
            raise ValueError(
                f"multiple provider bars exist for security session {session}"
            )
        selected[session] = bar
    if not selected:
        raise ValueError(f"snapshot contains no bars for security {security_id.value}")
    return selected


def _price_point(bar: PriceBar, basis: PriceBasis) -> PricePoint:
    high, low, close = _prices(bar, basis)
    return PricePoint(
        observed_at=bar.timing.observed_at,
        available_at=bar.timing.available_at,
        high=high,
        low=low,
        close=close,
        volume=bar.raw_volume,
    )


def _benchmark_point(bar: PriceBar, basis: PriceBasis) -> BenchmarkPoint:
    _, _, close = _prices(bar, basis)
    return BenchmarkPoint(available_at=bar.timing.available_at, close=close)


def _prices(bar: PriceBar, basis: PriceBasis) -> tuple[Decimal, Decimal, Decimal]:
    if basis is PriceBasis.RAW:
        return bar.raw_high, bar.raw_low, bar.raw_close
    adjusted = (bar.adjusted_high, bar.adjusted_low, bar.adjusted_close)
    if any(value is None for value in adjusted):
        raise ValueError("adjusted price basis requires complete adjusted OHLC values")
    return adjusted  # type: ignore[return-value]
