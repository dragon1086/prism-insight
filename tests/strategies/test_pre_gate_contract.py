from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from prism_core.data import SecurityId
from prism_core.strategies import Market, StrategyId, StrategyVersion
from prism_core.strategies.pre_gate import (
    PreGateDecision,
    PreGateError,
    PreGateErrorClass,
    PreGateOutcome,
    PreGateStatus,
)


EVALUATED_AT = datetime(2026, 7, 30, 7, 0, tzinfo=timezone.utc)
SECURITY_ID = SecurityId(value=UUID("10000000-0000-4000-8000-000000000001"))
DATA_SNAPSHOT_ID = UUID("20000000-0000-4000-8000-000000000002")
FEATURE_SNAPSHOT_ID = UUID("30000000-0000-4000-8000-000000000003")
QUANT_SCORE_ID = UUID("40000000-0000-4000-8000-000000000004")


def _outcome_payload() -> dict[str, object]:
    return {
        "status": PreGateStatus.PASS,
        "decision": PreGateDecision.PROCEED_TO_LLM,
        "strategy_id": StrategyId.SWING_V1,
        "strategy_version": StrategyVersion("1.0.0"),
        "market": Market.KR,
        "security_id": SECURITY_ID,
        "data_snapshot_id": DATA_SNAPSHOT_ID,
        "feature_snapshot_id": FEATURE_SNAPSHOT_ID,
        "quant_score_id": QUANT_SCORE_ID,
        "score_version": "SHADOW_SCORE_V1.SWING_V1",
        "threshold_version": "SHADOW_ENTRY_THRESHOLDS_V1.SWING_V1",
        "score": Decimal("70.000000"),
        "threshold": Decimal("65"),
        "hard_vetoes": (),
        "evaluated_at": EVALUATED_AT,
        "llm_called": False,
    }


def test_pass_outcome_is_strategy_scoped_and_serialization_round_trips() -> None:
    outcome = PreGateOutcome.model_validate(_outcome_payload())

    assert outcome.status is PreGateStatus.PASS
    assert outcome.decision is PreGateDecision.PROCEED_TO_LLM
    assert outcome.strategy_id is StrategyId.SWING_V1
    assert outcome.score == Decimal("70.000000")
    assert outcome.threshold == Decimal("65")
    assert outcome.hard_vetoes == ()
    assert outcome.data_snapshot_id == DATA_SNAPSHOT_ID
    assert outcome.feature_snapshot_id == FEATURE_SNAPSHOT_ID
    assert outcome.llm_called is False
    assert PreGateOutcome.model_validate_json(outcome.model_dump_json()) == outcome


def test_rejected_outcome_is_explicit_investment_no_entry_and_never_called_llm() -> None:
    outcome = PreGateOutcome.model_validate(
        {
            **_outcome_payload(),
            "status": PreGateStatus.PRE_GATE_REJECTED,
            "decision": PreGateDecision.NO_ENTRY,
            "score": Decimal("64.999999"),
            "hard_vetoes": ("shadow_score_v1:swing_v1.min_quant_score",),
        }
    )

    assert outcome.status is PreGateStatus.PRE_GATE_REJECTED
    assert outcome.decision is PreGateDecision.NO_ENTRY
    assert outcome.hard_vetoes == (
        "shadow_score_v1:swing_v1.min_quant_score",
    )
    assert outcome.llm_called is False
    assert PreGateOutcome.model_validate_json(outcome.model_dump_json()) == outcome


@pytest.mark.parametrize(
    "changes",
    (
        {"status": PreGateStatus.PASS, "decision": PreGateDecision.NO_ENTRY},
        {
            "status": PreGateStatus.PASS,
            "decision": PreGateDecision.PROCEED_TO_LLM,
            "hard_vetoes": ("policy:veto",),
        },
        {
            "status": PreGateStatus.PRE_GATE_REJECTED,
            "decision": PreGateDecision.PROCEED_TO_LLM,
            "hard_vetoes": ("policy:veto",),
        },
        {
            "status": PreGateStatus.PRE_GATE_REJECTED,
            "decision": PreGateDecision.NO_ENTRY,
            "hard_vetoes": (),
        },
        {"llm_called": True},
    ),
)
def test_outcome_rejects_inconsistent_or_llm_called_states(
    changes: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        PreGateOutcome.model_validate({**_outcome_payload(), **changes})


@pytest.mark.parametrize(
    "changes",
    (
        {"score": Decimal("64.999999")},
        {"score": Decimal("-0.000001")},
        {"threshold": Decimal("100.000001")},
        {"score": 70.0},
        {"evaluated_at": datetime(2026, 7, 30, 7, 0)},
    ),
)
def test_pass_rejects_below_threshold_out_of_range_or_non_strict_values(
    changes: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        PreGateOutcome.model_validate({**_outcome_payload(), **changes})


@pytest.mark.parametrize(
    "hard_vetoes",
    (
        ("policy:duplicate", "policy:duplicate"),
        ("policy:z_last", "policy:a_first"),
    ),
)
def test_rejected_outcome_requires_unique_canonical_veto_order(
    hard_vetoes: tuple[str, ...],
) -> None:
    with pytest.raises(ValidationError):
        PreGateOutcome.model_validate(
            {
                **_outcome_payload(),
                "status": PreGateStatus.PRE_GATE_REJECTED,
                "decision": PreGateDecision.NO_ENTRY,
                "hard_vetoes": hard_vetoes,
            }
        )


def test_parser_and_infrastructure_errors_have_a_distinct_non_investment_contract() -> None:
    error = PreGateError(
        error_class=PreGateErrorClass.PARSER_ERROR,
        error_code="FEATURE_INPUT_PARSE_FAILED",
        strategy_id=StrategyId.TREND_V1,
        strategy_version=StrategyVersion("1.0.0"),
        market=Market.KR,
        security_id=SECURITY_ID,
        data_snapshot_id=DATA_SNAPSHOT_ID,
        evaluated_at=EVALUATED_AT,
    )

    payload = error.model_dump(mode="json")
    assert payload["status"] == "PRE_GATE_ERROR"
    assert "decision" not in payload
    assert "NO_ENTRY" not in error.model_dump_json()
    assert error.llm_called is False
    assert PreGateError.model_validate_json(error.model_dump_json()) == error

    with pytest.raises(ValidationError):
        PreGateOutcome.model_validate(payload)


def test_pre_gate_error_rejects_investment_decision_fields_and_unsanitized_details() -> None:
    payload = {
        "error_class": PreGateErrorClass.INFRASTRUCTURE_ERROR,
        "error_code": "FEATURE_STORE_UNAVAILABLE",
        "strategy_id": StrategyId.SWING_V1,
        "strategy_version": StrategyVersion("1.0.0"),
        "market": Market.KR,
        "security_id": SECURITY_ID,
        "data_snapshot_id": DATA_SNAPSHOT_ID,
        "evaluated_at": EVALUATED_AT,
        "decision": "NO_ENTRY",
        "error_detail": "private provider response",
    }

    with pytest.raises(ValidationError):
        PreGateError.model_validate(payload)


@pytest.mark.parametrize(
    "error_code",
    (
        "feature input parse failed",
        "FEATURE_PARSE_FAILED:/Users/private",
        "Traceback (most recent call last)",
        "X" * 121,
    ),
)
def test_pre_gate_error_rejects_unsanitized_error_codes(error_code: str) -> None:
    with pytest.raises(ValidationError):
        PreGateError(
            error_class=PreGateErrorClass.PARSER_ERROR,
            error_code=error_code,
            strategy_id=StrategyId.TREND_V1,
            strategy_version=StrategyVersion("1.0.0"),
            market=Market.KR,
            security_id=SECURITY_ID,
            data_snapshot_id=DATA_SNAPSHOT_ID,
            evaluated_at=EVALUATED_AT,
        )
