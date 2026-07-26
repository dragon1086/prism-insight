from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from prism_core.data.contracts import SecurityId
from prism_core.data.contracts import DataQualityStatus
from prism_core.data.quality import QualityDisposition
from prism_core.llm.trade_plan import (
    ProposedDecision,
    ScoreComponentName,
    TradePlanProposal,
)
from prism_core.portfolio import (
    BookKind,
    ConsolidatedRiskPolicy,
    ExposureDimension,
    ExposureLimits,
    StrategyPosition,
)
from prism_core.strategies.contracts import Market, StrategyId, StrategyVersion


SECURITY_ID = SecurityId(value=UUID("00000000-0000-0000-0000-000000000102"))


def _strategy_proposal(strategy_id: StrategyId) -> TradePlanProposal:
    is_swing = strategy_id is StrategyId.SWING_V1
    prefix = "swing" if is_swing else "trend"
    payload = {
        "proposal_id": UUID(
            "00000000-0000-0000-0000-000000000101"
            if is_swing
            else "00000000-0000-0000-0000-000000000201"
        ),
        "proposal_version": "trade-plan.v1",
        "strategy_id": strategy_id,
        "strategy_version": StrategyVersion(f"{prefix}-v1.0.0"),
        "market": Market.US,
        "security_id": SECURITY_ID,
        "feature_provenance": {
            "feature_snapshot_id": UUID(
                "00000000-0000-0000-0000-000000000103"
                if is_swing
                else "00000000-0000-0000-0000-000000000203"
            ),
            "data_snapshot_id": UUID("00000000-0000-0000-0000-000000000104"),
            "as_of": datetime(2026, 7, 24, 20, 0, tzinfo=timezone.utc),
            "feature_version": f"{prefix}-features.v1",
            "data_quality_status": DataQualityStatus.FRESH,
            "quality_disposition": QualityDisposition.ACCEPT,
        },
        "decision": ProposedDecision.NO_ENTRY,
        "llm_score": Decimal("60"),
        "score_breakdown": (
            {
                "name": ScoreComponentName.TREND_STRUCTURE,
                "score": Decimal("60"),
                "rationale": f"{prefix} fixture rationale",
                "evidence_ids": (f"{prefix}-evidence",),
            },
        ),
        "regime": {
            "probabilities": {
                "strong_bull": Decimal("0.1"),
                "moderate_bull": Decimal("0.3"),
                "sideways": Decimal("0.4"),
                "moderate_bear": Decimal("0.15"),
                "strong_bear": Decimal("0.05"),
            },
            "confidence": Decimal("0.7"),
            "drivers": ("fixture driver",),
            "falsifiers": ("fixture falsifier",),
        },
        "entry_predicates": (),
        "stop_candidates": (),
        "target_candidates": (),
        "risk_multiplier_candidate": {
            "value": Decimal("0.5"),
            "rationale": "candidate only",
            "evidence_ids": (f"{prefix}-evidence",),
        },
        "reentry_candidates": (),
        "pyramiding_candidates": (),
        "bull_evidence_ids": (f"{prefix}-evidence",),
        "bear_evidence_ids": (f"{prefix}-risk",),
        "missing_or_stale_data": (),
        "uncertainty": {
            "level": Decimal("0.3"),
            "known_unknowns": ("fixture unknown",),
            "assumptions": (),
        },
        "model": {
            "provider": "fixture",
            "model_id": "fixture-model",
            "model_version": "fixture-v1",
        },
        "prompt_version": f"{prefix}-prompt.v1",
        "sampling": {
            "version": "sampling.v1",
            "temperature": Decimal("0.2"),
            "top_p": Decimal("0.9"),
            "seed": 7,
        },
    }
    return TradePlanProposal.model_validate(payload)


def _position(
    *, book_id: str, strategy_id: StrategyId, quantity: int
) -> StrategyPosition:
    return StrategyPosition(
        book_id=book_id,
        book_kind=BookKind.VIRTUAL,
        strategy_id=strategy_id,
        security_id=SECURITY_ID,
        symbol="AAPL",
        sector="TECH",
        market=Market.US,
        currency="USD",
        base_currency="USD",
        fx_rate_to_base=Decimal("1"),
        quantity=quantity,
        average_entry_price=Decimal("90"),
        current_price=Decimal("100"),
        stop_price=Decimal("80"),
    )


def test_same_security_has_distinct_swing_and_trend_proposal_contracts() -> None:
    swing = _strategy_proposal(StrategyId.SWING_V1)
    trend = _strategy_proposal(StrategyId.TREND_V1)

    assert swing.security_id == trend.security_id == SECURITY_ID
    assert swing.feature_provenance.data_snapshot_id == trend.feature_provenance.data_snapshot_id
    assert (swing.strategy_id, swing.strategy_version, swing.prompt_version) == (
        StrategyId.SWING_V1,
        StrategyVersion("swing-v1.0.0"),
        "swing-prompt.v1",
    )
    assert (trend.strategy_id, trend.strategy_version, trend.prompt_version) == (
        StrategyId.TREND_V1,
        StrategyVersion("trend-v1.0.0"),
        "trend-prompt.v1",
    )
    assert swing.proposal_id != trend.proposal_id
    assert swing.feature_provenance.feature_version == "swing-features.v1"
    assert trend.feature_provenance.feature_version == "trend-features.v1"


def test_consolidated_exposure_limits_duplicate_symbol_risk_across_books() -> None:
    policy = ConsolidatedRiskPolicy()
    exposure = policy.consolidate(
        positions=(
            _position(book_id="swing-us", strategy_id=StrategyId.SWING_V1, quantity=10),
            _position(book_id="trend-us", strategy_id=StrategyId.TREND_V1, quantity=8),
        ),
        open_orders=(),
        base_currency="USD",
    )
    decision = policy.evaluate(
        exposure=exposure,
        limits=ExposureLimits(
            max_gross_exposure=Decimal("5000"),
            max_symbol_exposure=Decimal("1500"),
            max_sector_exposure=Decimal("5000"),
            max_market_exposure=Decimal("5000"),
            max_currency_exposure=Decimal("5000"),
            max_open_order_exposure=Decimal("5000"),
        ),
    )

    assert exposure.total_for(ExposureDimension.SYMBOL, "AAPL") == Decimal("1800")
    assert exposure.total_for(ExposureDimension.STRATEGY, "SWING_V1") == Decimal("1000")
    assert exposure.total_for(ExposureDimension.STRATEGY, "TREND_V1") == Decimal("800")
    assert decision.accepted is False
    assert [(item.dimension, item.key) for item in decision.breaches] == [
        (ExposureDimension.SYMBOL, "AAPL")
    ]
