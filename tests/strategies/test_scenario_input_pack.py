from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from prism_core.data.contracts import (
    CorporateAction,
    CorporateActionType,
    DataQualityStatus,
    EvidenceItem,
    FundamentalObservation,
    MarketSnapshot,
    ObservationTime,
    PriceBar,
    SecurityId,
    SymbolMapping,
)
from prism_core.data.quality import QualityDisposition
from prism_core.strategies import (
    FeatureSnapshot,
    FeatureValue,
    Market,
    ScenarioInputStatus,
    ScenarioPriceBasis,
    StrategyId,
    build_scenario_input_pack,
)
from prism_core.strategies.registry import DEFAULT_STRATEGY_REGISTRY


UTC = timezone.utc
AS_OF = datetime(2026, 7, 24, 22, 0, tzinfo=UTC)
CREATED_AT = AS_OF + timedelta(minutes=1)
STOCK_ID = SecurityId(value=UUID("00000000-0000-0000-0000-000000000701"))
BENCHMARK_ID = SecurityId(value=UUID("00000000-0000-0000-0000-000000000702"))
SNAPSHOT_ID = UUID("00000000-0000-0000-0000-000000000703")


def _sessions(count: int) -> list[date]:
    values: list[date] = []
    cursor = AS_OF.date()
    while len(values) < count:
        if cursor.weekday() < 5:
            values.append(cursor)
        cursor -= timedelta(days=1)
    return list(reversed(values))


def _timing(session: date, *, as_of: datetime = AS_OF) -> ObservationTime:
    observed = datetime.combine(session, time(21), tzinfo=UTC)
    return ObservationTime(
        observed_at=observed,
        available_at=observed + timedelta(minutes=1),
        ingested_at=CREATED_AT,
        as_of_date=as_of,
    )


def _mapping(security_id: SecurityId, symbol: str) -> SymbolMapping:
    return SymbolMapping(
        security_id=security_id,
        provider="fmp",
        provider_symbol=symbol,
        market="US",
        valid_from=datetime(1980, 1, 1, tzinfo=UTC),
        timing=ObservationTime(
            observed_at=AS_OF - timedelta(days=1),
            available_at=AS_OF - timedelta(days=1),
            ingested_at=CREATED_AT,
            as_of_date=AS_OF,
        ),
        source_hash=("a" if symbol == "AAPL" else "b") * 64,
    )


def _bar(
    security_id: SecurityId,
    symbol: str,
    session: date,
    index: int,
    *,
    adjusted: bool = False,
) -> PriceBar:
    base = Decimal("100") if security_id == STOCK_ID else Decimal("200")
    close = base + Decimal(index) / Decimal("5")
    raw = {
        "raw_open": close - Decimal("0.25"),
        "raw_high": close + Decimal("1"),
        "raw_low": close - Decimal("1"),
        "raw_close": close,
        "raw_volume": Decimal("1000000") + Decimal(index * 1000),
    }
    adjusted_values = {}
    if adjusted:
        adjusted_values = {
            "adjusted_open": raw["raw_open"] / Decimal("2"),
            "adjusted_high": raw["raw_high"] / Decimal("2"),
            "adjusted_low": raw["raw_low"] / Decimal("2"),
            "adjusted_close": raw["raw_close"] / Decimal("2"),
            "adjusted_volume": raw["raw_volume"] * Decimal("2"),
            "adjustment_as_of": AS_OF,
        }
    return PriceBar(
        security_id=security_id,
        provider="fmp",
        provider_symbol=symbol,
        source_record_id=f"fmp:{symbol}:{session.isoformat()}",
        source_hash=f"{index % 16:x}" * 64,
        revision=0,
        timing=_timing(session),
        quality=DataQualityStatus.FRESH,
        bar_start=datetime.combine(session, time(14, 30), tzinfo=UTC),
        bar_end=datetime.combine(session, time(21), tzinfo=UTC),
        interval="1d",
        currency="USD",
        **raw,
        **adjusted_values,
    )


def _feature(
    strategy_id: StrategyId, *, market: Market = Market.US
) -> FeatureSnapshot:
    definition = DEFAULT_STRATEGY_REGISTRY.get(strategy_id)
    return FeatureSnapshot(
        feature_snapshot_id=UUID(
            "00000000-0000-0000-0000-000000000711"
            if strategy_id is StrategyId.SWING_V1
            else "00000000-0000-0000-0000-000000000712"
        ),
        strategy_id=strategy_id,
        strategy_version=definition.version,
        market=market,
        security_id=STOCK_ID,
        data_snapshot_id=SNAPSHOT_ID,
        as_of=AS_OF,
        feature_version="features.v1",
        values=(
            FeatureValue(
                name=f"{strategy_id.value.lower()}.fixture_signal",
                value=Decimal("1"),
            ),
        ),
        data_quality_status=DataQualityStatus.FRESH,
        quality_disposition=QualityDisposition.ACCEPT,
    )


def _snapshot(*, adjusted: bool = False) -> MarketSnapshot:
    sessions = _sessions(260)
    bars = tuple(
        _bar(security_id, symbol, session, index, adjusted=adjusted)
        for index, session in enumerate(sessions)
        for security_id, symbol in ((STOCK_ID, "AAPL"), (BENCHMARK_ID, "SPY"))
    )
    fundamental_timing = ObservationTime(
        observed_at=AS_OF - timedelta(days=30),
        available_at=AS_OF - timedelta(days=29),
        ingested_at=CREATED_AT,
        as_of_date=AS_OF,
    )
    fundamentals = (
        FundamentalObservation(
            security_id=STOCK_ID,
            provider="fmp",
            provider_symbol="AAPL",
            source_record_id="fmp:AAPL:income:2026Q2:revenue",
            source_hash="c" * 64,
            revision=0,
            timing=fundamental_timing,
            quality=DataQualityStatus.FRESH,
            metric="revenue",
            period_start=date(2026, 4, 1),
            period_end=date(2026, 6, 30),
            value=Decimal("123456000000"),
            unit="USD",
        ),
    )
    evidence = (
        EvidenceItem(
            security_id=STOCK_ID,
            provider="sec",
            provider_symbol="AAPL",
            source_record_id="sec:AAPL:10-Q:2026Q2",
            source_hash="d" * 64,
            revision=0,
            timing=fundamental_timing,
            quality=DataQualityStatus.FRESH,
            evidence_id=UUID("00000000-0000-0000-0000-000000000721"),
            kind="company_filing",
            title="Quarterly filing",
            source_url="https://www.sec.gov/example/aapl",
            content_hash="e" * 64,
        ),
        EvidenceItem(
            security_id=BENCHMARK_ID,
            provider="agentnews",
            provider_symbol="US",
            source_record_id="agentnews:us:2026-07-24",
            source_hash="f" * 64,
            revision=0,
            timing=fundamental_timing,
            quality=DataQualityStatus.FRESH,
            evidence_id=UUID("00000000-0000-0000-0000-000000000722"),
            kind="market_context",
            title="US market context",
            source_url="https://agentnews.md/finance.md",
            content_hash="1" * 64,
        ),
        EvidenceItem(
            security_id=STOCK_ID,
            provider="fmp",
            provider_symbol="AAPL",
            source_record_id="fmp:AAPL:earnings:2026-08-01",
            source_hash="7" * 64,
            revision=0,
            timing=fundamental_timing,
            quality=DataQualityStatus.FRESH,
            evidence_id=UUID("00000000-0000-0000-0000-000000000723"),
            kind="earnings_event",
            title="Scheduled earnings",
            source_url="https://example.com/aapl/earnings",
            content_hash="8" * 64,
        ),
        EvidenceItem(
            security_id=STOCK_ID,
            provider="prism",
            provider_symbol="AAPL",
            source_record_id="prism:AAPL:next-review:2026-07-28",
            source_hash="9" * 64,
            revision=0,
            timing=fundamental_timing,
            quality=DataQualityStatus.FRESH,
            evidence_id=UUID("00000000-0000-0000-0000-000000000724"),
            kind="next_review",
            title="Next scenario review",
            source_url="https://localhost/prism/aapl/review",
            content_hash="a" * 64,
        ),
    )
    actions = ()
    if adjusted:
        actions = (
            CorporateAction(
                security_id=STOCK_ID,
                provider="fmp",
                provider_symbol="AAPL",
                source_record_id="fmp:AAPL:split:2026-07-01",
                source_hash="2" * 64,
                revision=0,
                timing=ObservationTime(
                    observed_at=AS_OF - timedelta(days=40),
                    available_at=AS_OF - timedelta(days=39),
                    ingested_at=CREATED_AT,
                    as_of_date=AS_OF,
                ),
                quality=DataQualityStatus.FRESH,
                action_type=CorporateActionType.SPLIT,
                effective_date=date(2026, 7, 1),
                ratio=Decimal("2"),
            ),
        )
    return MarketSnapshot(
        snapshot_id=SNAPSHOT_ID,
        market="US",
        as_of_date=AS_OF,
        created_at=CREATED_AT,
        content_hash="3" * 64,
        quality=DataQualityStatus.FRESH,
        symbol_mappings=(_mapping(STOCK_ID, "AAPL"), _mapping(BENCHMARK_ID, "SPY")),
        price_bars=bars,
        fundamentals=fundamentals,
        corporate_actions=actions,
        evidence=evidence,
    )


def _features() -> dict[StrategyId, FeatureSnapshot]:
    return {
        StrategyId.SWING_V1: _feature(StrategyId.SWING_V1),
        StrategyId.TREND_V1: _feature(StrategyId.TREND_V1),
    }


def _kr_snapshot() -> MarketSnapshot:
    snapshot = _snapshot()
    symbol_by_id = {STOCK_ID: "005930", BENCHMARK_ID: "069500"}
    return snapshot.model_copy(
        update={
            "market": "KR",
            "symbol_mappings": tuple(
                item.model_copy(
                    update={
                        "provider": "KIS",
                        "provider_symbol": symbol_by_id[item.security_id],
                        "market": "KR",
                    }
                )
                for item in snapshot.symbol_mappings
            ),
            "price_bars": tuple(
                item.model_copy(
                    update={
                        "provider": "KIS",
                        "provider_symbol": symbol_by_id[item.security_id],
                    }
                )
                for item in snapshot.price_bars
            ),
        }
    )


def _kr_features() -> dict[StrategyId, FeatureSnapshot]:
    return {
        strategy_id: _feature(strategy_id, market=Market.KR)
        for strategy_id in (StrategyId.SWING_V1, StrategyId.TREND_V1)
    }


def test_builds_complete_strategy_separated_pack_from_snapshot_and_features() -> None:
    pack = build_scenario_input_pack(
        snapshot=_snapshot(),
        market=Market.US,
        security_id=STOCK_ID,
        benchmark_security_id=BENCHMARK_ID,
        price_basis=ScenarioPriceBasis.RAW,
        feature_snapshots=_features(),
    )

    assert pack.contract_version == "scenario-input-pack.v1"
    assert pack.status is ScenarioInputStatus.COMPLETE
    assert pack.policy_disposition is QualityDisposition.ACCEPT
    assert pack.price_basis is ScenarioPriceBasis.RAW
    assert pack.identity.provider_symbol == "AAPL"
    assert pack.identity.benchmark_provider_symbol == "SPY"
    assert pack.provenance.latest_completed_session == date(2026, 7, 24)
    assert pack.latest_bar.close > 0
    assert pack.fundamentals[0].metric == "revenue"
    assert pack.fundamentals[0].accepted_at == AS_OF - timedelta(days=29)
    assert len(pack.company_evidence) == len(pack.market_context_evidence) == 1
    assert pack.earnings_events[0].event_type == "EARNINGS"
    assert pack.next_review_events[0].event_type == "NEXT_REVIEW"
    assert {item.strategy_id for item in pack.strategies} == {
        StrategyId.SWING_V1,
        StrategyId.TREND_V1,
    }
    swing = next(item for item in pack.strategies if item.strategy_id is StrategyId.SWING_V1)
    trend = next(item for item in pack.strategies if item.strategy_id is StrategyId.TREND_V1)
    assert {item.name for item in swing.indicators} >= {
        "swing_v1.ma_20",
        "swing_v1.ma_50",
        "swing_v1.ma_20_slope_5",
        "swing_v1.atr_14",
        "swing_v1.atr_percent_14",
        "swing_v1.realized_volatility_20",
        "swing_v1.max_abs_gap_percent_20",
        "swing_v1.recent_swing_high_20",
        "swing_v1.recent_swing_low_20",
        "swing_v1.support_20",
        "swing_v1.resistance_20",
        "swing_v1.breakout_level_20",
        "swing_v1.relative_strength_20",
        "swing_v1.high_52_week",
        "swing_v1.distance_from_52_week_high_percent",
        "swing_v1.momentum_5",
        "swing_v1.momentum_20",
        "swing_v1.volume_expansion_20",
        "swing_v1.average_dollar_volume_20",
    }
    assert {item.name for item in trend.indicators} >= {
        "trend_v1.ma_50",
        "trend_v1.ma_200",
        "trend_v1.ma_50_slope_20",
        "trend_v1.relative_strength_60",
        "trend_v1.momentum_60",
        "trend_v1.momentum_120",
    }
    assert swing.peak_state.value in {"AT_HIGH", "NEAR_HIGH", "BELOW_HIGH"}
    assert swing.peak_state_formula_version == "peak_state.distance_from_high_252.v1"
    assert trend.peak_state == swing.peak_state
    assert all(item.formula_version for strategy in pack.strategies for item in strategy.indicators)


def test_same_contract_builds_kr_kis_swing_and_trend_pack() -> None:
    pack = build_scenario_input_pack(
        snapshot=_kr_snapshot(),
        market=Market.KR,
        security_id=STOCK_ID,
        benchmark_security_id=BENCHMARK_ID,
        price_basis=ScenarioPriceBasis.RAW,
        feature_snapshots=_kr_features(),
    )

    assert pack.status is ScenarioInputStatus.COMPLETE
    assert pack.identity.market is Market.KR
    assert pack.identity.provider == "KIS"
    assert pack.identity.provider_symbol == "005930"
    assert pack.identity.benchmark_provider_symbol == "069500"
    assert {item.strategy_id for item in pack.strategies} == {
        StrategyId.SWING_V1,
        StrategyId.TREND_V1,
    }


def test_future_available_fundamental_is_fail_closed_even_at_pack_boundary() -> None:
    snapshot = _snapshot()
    fundamental = snapshot.fundamentals[0]
    invalid_timing = fundamental.timing.model_copy(
        update={
            "available_at": AS_OF + timedelta(seconds=1),
            "ingested_at": AS_OF + timedelta(seconds=2),
        }
    )
    invalid_fundamental = fundamental.model_copy(update={"timing": invalid_timing})
    invalid_snapshot = snapshot.model_copy(
        update={"fundamentals": (invalid_fundamental,)}
    )

    pack = build_scenario_input_pack(
        snapshot=invalid_snapshot,
        market=Market.US,
        security_id=STOCK_ID,
        benchmark_security_id=BENCHMARK_ID,
        price_basis=ScenarioPriceBasis.RAW,
        feature_snapshots=_features(),
    )

    assert pack.policy_disposition is QualityDisposition.REPORT_ONLY
    assert not pack.fundamentals
    assert any(
        issue.field == "fundamentals.revenue.timing"
        and issue.quality is DataQualityStatus.CONFLICT
        for issue in pack.issues
    )


def test_rejects_malformed_strategy_feature_mapping_before_derivation() -> None:
    with pytest.raises(TypeError, match="FeatureSnapshot"):
        build_scenario_input_pack(
            snapshot=_snapshot(),
            market=Market.US,
            security_id=STOCK_ID,
            benchmark_security_id=BENCHMARK_ID,
            price_basis=ScenarioPriceBasis.RAW,
            feature_snapshots={
                StrategyId.SWING_V1: "not-a-feature",  # type: ignore[dict-item]
                StrategyId.TREND_V1: _feature(StrategyId.TREND_V1),
            },
        )


def test_missing_strategy_feature_blocks_all_strategy_derivation() -> None:
    pack = build_scenario_input_pack(
        snapshot=_snapshot(),
        market=Market.US,
        security_id=STOCK_ID,
        benchmark_security_id=BENCHMARK_ID,
        price_basis=ScenarioPriceBasis.RAW,
        feature_snapshots={StrategyId.TREND_V1: _feature(StrategyId.TREND_V1)},
    )

    assert pack.status is ScenarioInputStatus.ANALYSIS_INCOMPLETE
    assert pack.policy_disposition is QualityDisposition.REPORT_ONLY
    assert not pack.strategies
    assert any(
        issue.field == "features.SWING_V1"
        and issue.quality is DataQualityStatus.UNAVAILABLE
        for issue in pack.issues
    )


def test_conflicting_symbol_identity_blocks_strategy_derivation() -> None:
    snapshot = _snapshot()
    conflicting = snapshot.symbol_mappings[0].model_copy(
        update={"provider_symbol": "AAPL.CONFLICT", "source_hash": "4" * 64}
    )
    conflicted_snapshot = snapshot.model_copy(
        update={"symbol_mappings": (*snapshot.symbol_mappings, conflicting)}
    )

    pack = build_scenario_input_pack(
        snapshot=conflicted_snapshot,
        market=Market.US,
        security_id=STOCK_ID,
        benchmark_security_id=BENCHMARK_ID,
        price_basis=ScenarioPriceBasis.RAW,
        feature_snapshots=_features(),
    )

    assert pack.status is ScenarioInputStatus.ANALYSIS_INCOMPLETE
    assert pack.policy_disposition is QualityDisposition.REPORT_ONLY
    assert pack.identity.provider_symbol is None
    assert not pack.strategies
    assert any(
        issue.field == "identity.security"
        and issue.quality is DataQualityStatus.CONFLICT
        for issue in pack.issues
    )


def test_expired_symbol_mapping_is_unavailable_at_scenario_as_of() -> None:
    snapshot = _snapshot()
    expired = snapshot.symbol_mappings[0].model_copy(
        update={"valid_to": AS_OF - timedelta(days=1)}
    )
    invalid_snapshot = snapshot.model_copy(
        update={"symbol_mappings": (expired, snapshot.symbol_mappings[1])}
    )

    pack = build_scenario_input_pack(
        snapshot=invalid_snapshot,
        market=Market.US,
        security_id=STOCK_ID,
        benchmark_security_id=BENCHMARK_ID,
        price_basis=ScenarioPriceBasis.RAW,
        feature_snapshots=_features(),
    )

    assert pack.status is ScenarioInputStatus.ANALYSIS_INCOMPLETE
    assert pack.identity.provider_symbol is None
    assert not pack.strategies
    assert any(
        issue.field == "identity.security"
        and issue.quality is DataQualityStatus.UNAVAILABLE
        for issue in pack.issues
    )


def test_stale_core_price_is_analysis_incomplete_and_report_only() -> None:
    stale = _snapshot().model_copy(update={"quality": DataQualityStatus.STALE})

    pack = build_scenario_input_pack(
        snapshot=stale,
        market=Market.US,
        security_id=STOCK_ID,
        benchmark_security_id=BENCHMARK_ID,
        price_basis=ScenarioPriceBasis.RAW,
        feature_snapshots=_features(),
    )

    assert pack.status is ScenarioInputStatus.ANALYSIS_INCOMPLETE
    assert pack.policy_disposition is QualityDisposition.REPORT_ONLY
    assert not pack.strategies
    assert "CORE:price.snapshot_quality:STALE" in pack.entry_vetoes


def test_zero_prior_volume_window_is_classified_instead_of_crashing_formula() -> None:
    snapshot = _snapshot()
    stock_sessions = [
        bar for bar in snapshot.price_bars if bar.security_id == STOCK_ID
    ]
    zero_volume_ids = {
        bar.source_record_id for bar in stock_sessions[-21:-1]
    }
    invalid_bars = tuple(
        bar.model_copy(update={"raw_volume": Decimal("0")})
        if bar.source_record_id in zero_volume_ids
        else bar
        for bar in snapshot.price_bars
    )
    invalid_snapshot = snapshot.model_copy(update={"price_bars": invalid_bars})

    pack = build_scenario_input_pack(
        snapshot=invalid_snapshot,
        market=Market.US,
        security_id=STOCK_ID,
        benchmark_security_id=BENCHMARK_ID,
        price_basis=ScenarioPriceBasis.RAW,
        feature_snapshots=_features(),
    )

    assert pack.status is ScenarioInputStatus.ANALYSIS_INCOMPLETE
    assert not pack.strategies
    assert any(
        issue.field == "price.liquidity"
        and issue.quality is DataQualityStatus.CONFLICT
        for issue in pack.issues
    )


def test_split_adjusted_basis_keeps_action_and_adjustment_provenance() -> None:
    pack = build_scenario_input_pack(
        snapshot=_snapshot(adjusted=True),
        market=Market.US,
        security_id=STOCK_ID,
        benchmark_security_id=BENCHMARK_ID,
        price_basis=ScenarioPriceBasis.SPLIT_ADJUSTED,
        feature_snapshots=_features(),
    )

    assert pack.status is ScenarioInputStatus.COMPLETE
    assert pack.price_basis is ScenarioPriceBasis.SPLIT_ADJUSTED
    assert pack.adjustment_as_of == AS_OF
    assert pack.corporate_actions[0].action_type == "SPLIT"
    assert pack.corporate_actions[0].ratio == Decimal("2")
    assert pack.latest_bar is not None
    assert pack.latest_bar.close == _snapshot().price_bars[-2].raw_close / Decimal("2")
    assert not any(issue.field == "price.adjustment_provenance" for issue in pack.issues)


def test_split_adjusted_basis_rejects_adjustment_vintage_before_latest_action() -> None:
    snapshot = _snapshot(adjusted=True)
    stale_adjustment = datetime(2026, 6, 30, 23, 59, tzinfo=UTC)
    stale_bars = tuple(
        bar.model_copy(update={"adjustment_as_of": stale_adjustment})
        for bar in snapshot.price_bars
    )
    stale_snapshot = snapshot.model_copy(update={"price_bars": stale_bars})

    pack = build_scenario_input_pack(
        snapshot=stale_snapshot,
        market=Market.US,
        security_id=STOCK_ID,
        benchmark_security_id=BENCHMARK_ID,
        price_basis=ScenarioPriceBasis.SPLIT_ADJUSTED,
        feature_snapshots=_features(),
    )

    assert pack.status is ScenarioInputStatus.ANALYSIS_INCOMPLETE
    assert not pack.strategies
    assert any(
        issue.field == "price.adjustment_provenance"
        and issue.quality is DataQualityStatus.CONFLICT
        for issue in pack.issues
    )


def test_split_adjusted_basis_rejects_mixed_adjustment_vintages() -> None:
    snapshot = _snapshot(adjusted=True)
    first_stock_id = next(
        bar.source_record_id
        for bar in snapshot.price_bars
        if bar.security_id == STOCK_ID
    )
    mixed_bars = tuple(
        bar.model_copy(
            update={"adjustment_as_of": AS_OF - timedelta(days=1)}
        )
        if bar.source_record_id == first_stock_id
        else bar
        for bar in snapshot.price_bars
    )
    mixed_snapshot = snapshot.model_copy(update={"price_bars": mixed_bars})

    pack = build_scenario_input_pack(
        snapshot=mixed_snapshot,
        market=Market.US,
        security_id=STOCK_ID,
        benchmark_security_id=BENCHMARK_ID,
        price_basis=ScenarioPriceBasis.SPLIT_ADJUSTED,
        feature_snapshots=_features(),
    )

    assert pack.status is ScenarioInputStatus.ANALYSIS_INCOMPLETE
    assert not pack.strategies
    assert any(
        issue.field == "price.adjustment_provenance"
        and issue.quality is DataQualityStatus.CONFLICT
        for issue in pack.issues
    )


def test_raw_basis_with_split_in_structure_window_blocks_strategy_derivation() -> None:
    pack = build_scenario_input_pack(
        snapshot=_snapshot(adjusted=True),
        market=Market.US,
        security_id=STOCK_ID,
        benchmark_security_id=BENCHMARK_ID,
        price_basis=ScenarioPriceBasis.RAW,
        feature_snapshots=_features(),
    )

    assert pack.status is ScenarioInputStatus.ANALYSIS_INCOMPLETE
    assert not pack.strategies
    assert any(
        issue.field == "price.raw_corporate_action_window"
        and issue.quality is DataQualityStatus.CONFLICT
        for issue in pack.issues
    )


@pytest.mark.parametrize(
    "action_type",
    (
        CorporateActionType.STOCK_DIVIDEND,
        CorporateActionType.RIGHTS,
        CorporateActionType.SPINOFF,
    ),
)
def test_raw_basis_blocks_other_discontinuous_corporate_actions(
    action_type: CorporateActionType,
) -> None:
    snapshot = _snapshot(adjusted=True)
    discontinuous_action = snapshot.corporate_actions[0].model_copy(
        update={"action_type": action_type}
    )
    raw_snapshot = snapshot.model_copy(
        update={"corporate_actions": (discontinuous_action,)}
    )

    pack = build_scenario_input_pack(
        snapshot=raw_snapshot,
        market=Market.US,
        security_id=STOCK_ID,
        benchmark_security_id=BENCHMARK_ID,
        price_basis=ScenarioPriceBasis.RAW,
        feature_snapshots=_features(),
    )

    assert pack.status is ScenarioInputStatus.ANALYSIS_INCOMPLETE
    assert not pack.strategies
    assert any(
        issue.field == "price.raw_corporate_action_window"
        and issue.quality is DataQualityStatus.CONFLICT
        for issue in pack.issues
    )


def test_raw_basis_blocks_benchmark_split_that_would_corrupt_relative_strength() -> None:
    snapshot = _snapshot(adjusted=True)
    benchmark_split = snapshot.corporate_actions[0].model_copy(
        update={
            "security_id": BENCHMARK_ID,
            "provider_symbol": "SPY",
            "source_record_id": "fmp:SPY:split:2026-07-01",
        }
    )
    raw_snapshot = snapshot.model_copy(
        update={"corporate_actions": (benchmark_split,)}
    )

    pack = build_scenario_input_pack(
        snapshot=raw_snapshot,
        market=Market.US,
        security_id=STOCK_ID,
        benchmark_security_id=BENCHMARK_ID,
        price_basis=ScenarioPriceBasis.RAW,
        feature_snapshots=_features(),
    )

    assert pack.status is ScenarioInputStatus.ANALYSIS_INCOMPLETE
    assert not pack.strategies
    assert any(
        issue.field == "price.raw_benchmark_corporate_action_window"
        and issue.quality is DataQualityStatus.CONFLICT
        for issue in pack.issues
    )


def test_future_available_split_action_blocks_adjusted_strategy_derivation() -> None:
    snapshot = _snapshot(adjusted=True)
    action = snapshot.corporate_actions[0]
    invalid_timing = action.timing.model_copy(
        update={
            "available_at": AS_OF + timedelta(seconds=1),
            "ingested_at": AS_OF + timedelta(seconds=2),
        }
    )
    invalid_action = action.model_copy(update={"timing": invalid_timing})
    invalid_snapshot = snapshot.model_copy(
        update={"corporate_actions": (invalid_action,)}
    )

    pack = build_scenario_input_pack(
        snapshot=invalid_snapshot,
        market=Market.US,
        security_id=STOCK_ID,
        benchmark_security_id=BENCHMARK_ID,
        price_basis=ScenarioPriceBasis.SPLIT_ADJUSTED,
        feature_snapshots=_features(),
    )

    assert pack.status is ScenarioInputStatus.ANALYSIS_INCOMPLETE
    assert pack.policy_disposition is QualityDisposition.REPORT_ONLY
    assert not pack.corporate_actions
    assert not pack.strategies
    assert any(
        issue.field == "corporate_actions.timing"
        and issue.quality is DataQualityStatus.CONFLICT
        for issue in pack.issues
    )


def test_incomplete_current_session_row_is_excluded_from_latest_completed_ohlc() -> None:
    snapshot = _snapshot()
    last_stock = snapshot.price_bars[-2]
    incomplete = last_stock.model_copy(
        update={
            "bar_start": datetime(2026, 7, 27, 14, 30, tzinfo=UTC),
            "bar_end": datetime(2026, 7, 27, 20, 0, tzinfo=UTC),
            "source_record_id": "fmp:AAPL:2026-07-27:incomplete",
            "source_hash": "5" * 64,
        }
    )
    contaminated = snapshot.model_copy(
        update={"price_bars": (*snapshot.price_bars, incomplete)}
    )

    pack = build_scenario_input_pack(
        snapshot=contaminated,
        market=Market.US,
        security_id=STOCK_ID,
        benchmark_security_id=BENCHMARK_ID,
        price_basis=ScenarioPriceBasis.RAW,
        feature_snapshots=_features(),
    )

    assert pack.status is ScenarioInputStatus.COMPLETE
    assert pack.provenance.latest_completed_session == date(2026, 7, 24)
    assert pack.provenance.excluded_incomplete_sessions == (date(2026, 7, 27),)
    assert pack.latest_bar is not None
    assert pack.latest_bar.session == date(2026, 7, 24)


def test_future_available_completed_price_bar_blocks_strategy_derivation() -> None:
    snapshot = _snapshot()
    latest_stock = snapshot.price_bars[-2]
    invalid_timing = latest_stock.timing.model_copy(
        update={
            "available_at": AS_OF + timedelta(seconds=1),
            "ingested_at": AS_OF + timedelta(seconds=2),
        }
    )
    invalid_bar = latest_stock.model_copy(update={"timing": invalid_timing})
    invalid_snapshot = snapshot.model_copy(
        update={"price_bars": (*snapshot.price_bars[:-2], invalid_bar, snapshot.price_bars[-1])}
    )

    pack = build_scenario_input_pack(
        snapshot=invalid_snapshot,
        market=Market.US,
        security_id=STOCK_ID,
        benchmark_security_id=BENCHMARK_ID,
        price_basis=ScenarioPriceBasis.RAW,
        feature_snapshots=_features(),
    )

    assert pack.status is ScenarioInputStatus.ANALYSIS_INCOMPLETE
    assert not pack.strategies
    assert any(
        issue.field == "price.security.timing"
        and issue.quality is DataQualityStatus.CONFLICT
        for issue in pack.issues
    )


def test_same_vintage_fundamental_conflict_is_explicit_supplemental_veto() -> None:
    snapshot = _snapshot()
    conflicting = snapshot.fundamentals[0].model_copy(
        update={
            "provider": "sec",
            "source_record_id": "sec:AAPL:income:2026Q2:revenue",
            "source_hash": "6" * 64,
            "value": Decimal("999"),
        }
    )
    conflicted = snapshot.model_copy(
        update={"fundamentals": (*snapshot.fundamentals, conflicting)}
    )

    pack = build_scenario_input_pack(
        snapshot=conflicted,
        market=Market.US,
        security_id=STOCK_ID,
        benchmark_security_id=BENCHMARK_ID,
        price_basis=ScenarioPriceBasis.RAW,
        feature_snapshots=_features(),
    )

    assert pack.status is ScenarioInputStatus.COMPLETE
    assert pack.policy_disposition is QualityDisposition.REPORT_ONLY
    assert not pack.fundamentals
    assert any(
        issue.field == "fundamentals.revenue"
        and issue.quality is DataQualityStatus.CONFLICT
        for issue in pack.issues
    )


def test_evidence_from_a_later_point_in_time_is_excluded_and_vetoes_entry() -> None:
    snapshot = _snapshot()
    company = snapshot.evidence[0]
    later_as_of = AS_OF + timedelta(days=1)
    later_timing = company.timing.model_copy(
        update={
            "available_at": AS_OF + timedelta(seconds=1),
            "ingested_at": AS_OF + timedelta(seconds=2),
            "as_of_date": later_as_of,
        }
    )
    future_company = company.model_copy(update={"timing": later_timing})
    invalid_snapshot = snapshot.model_copy(
        update={"evidence": (future_company, *snapshot.evidence[1:])}
    )

    pack = build_scenario_input_pack(
        snapshot=invalid_snapshot,
        market=Market.US,
        security_id=STOCK_ID,
        benchmark_security_id=BENCHMARK_ID,
        price_basis=ScenarioPriceBasis.RAW,
        feature_snapshots=_features(),
    )

    assert pack.status is ScenarioInputStatus.COMPLETE
    assert pack.policy_disposition is QualityDisposition.REPORT_ONLY
    assert not pack.company_evidence
    assert any(
        issue.field == "evidence.company_filing.timing"
        and issue.quality is DataQualityStatus.CONFLICT
        for issue in pack.issues
    )


def test_stale_supplemental_evidence_is_visible_and_vetoes_entry() -> None:
    snapshot = _snapshot()
    stale_company = snapshot.evidence[0].model_copy(
        update={"quality": DataQualityStatus.STALE}
    )
    stale_snapshot = snapshot.model_copy(
        update={"evidence": (stale_company, *snapshot.evidence[1:])}
    )

    pack = build_scenario_input_pack(
        snapshot=stale_snapshot,
        market=Market.US,
        security_id=STOCK_ID,
        benchmark_security_id=BENCHMARK_ID,
        price_basis=ScenarioPriceBasis.RAW,
        feature_snapshots=_features(),
    )

    assert pack.status is ScenarioInputStatus.COMPLETE
    assert pack.policy_disposition is QualityDisposition.REPORT_ONLY
    assert pack.company_evidence[0].quality is DataQualityStatus.STALE
    assert any(
        issue.field == "evidence.company_filing.quality"
        and issue.quality is DataQualityStatus.STALE
        for issue in pack.issues
    )


def test_supplemental_gaps_veto_entry_without_erasing_strategy_analysis() -> None:
    snapshot = _snapshot().model_copy(
        update={"fundamentals": (), "evidence": ()}
    )

    pack = build_scenario_input_pack(
        snapshot=snapshot,
        market=Market.US,
        security_id=STOCK_ID,
        benchmark_security_id=BENCHMARK_ID,
        price_basis=ScenarioPriceBasis.RAW,
        feature_snapshots=_features(),
    )

    assert pack.status is ScenarioInputStatus.COMPLETE
    assert pack.policy_disposition is QualityDisposition.REPORT_ONLY
    assert {item.strategy_id for item in pack.strategies} == {
        StrategyId.SWING_V1,
        StrategyId.TREND_V1,
    }
    assert all(
        issue.classification.value == "SUPPLEMENTAL" for issue in pack.issues
    )
    assert pack.entry_vetoes
