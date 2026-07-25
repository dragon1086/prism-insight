from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from prism_core.data.contracts import DataQualityStatus, SecurityId
from prism_core.data.quality import QualityDisposition
from prism_core.llm.proposal_service import ProposalParseStatus, ProposalService
from prism_core.llm.trade_plan import TradePlanProposal
from prism_core.strategies.contracts import (
    FeatureSnapshot,
    FeatureValue,
    Market,
    StrategyId,
    StrategyVersion,
)
from tests.llm.test_trade_plan_schema import valid_proposal_payload


def feature_snapshot() -> FeatureSnapshot:
    return FeatureSnapshot(
        feature_snapshot_id=UUID("00000000-0000-0000-0000-000000000103"),
        strategy_id=StrategyId.SWING_V1,
        strategy_version=StrategyVersion("swing-v1.0.0"),
        market=Market.US,
        security_id=SecurityId(
            value=UUID("00000000-0000-0000-0000-000000000102")
        ),
        data_snapshot_id=UUID("00000000-0000-0000-0000-000000000104"),
        as_of=datetime(2026, 7, 24, 20, 0, tzinfo=timezone.utc),
        feature_version="features.v1",
        values=(
            FeatureValue(
                name="swing_v1.price_momentum_5d", value=Decimal("0.05")
            ),
        ),
        data_quality_status=DataQualityStatus.FRESH,
        quality_disposition=QualityDisposition.ACCEPT,
    )


def test_malformed_json_is_rejected_with_exact_raw_response_retained() -> None:
    raw_response = '{"proposal_id":'

    result = ProposalService().parse(
        raw_response=raw_response,
        feature_snapshot=feature_snapshot(),
    )

    assert result.status is ProposalParseStatus.REJECTED
    assert result.proposal is None
    assert result.raw_response == raw_response
    assert result.errors


def valid_raw_response() -> str:
    return TradePlanProposal.model_validate(valid_proposal_payload()).model_dump_json()


def test_valid_response_is_bound_to_snapshot_and_known_evidence() -> None:
    raw_response = valid_raw_response()

    result = ProposalService().parse(
        raw_response=raw_response,
        feature_snapshot=feature_snapshot(),
        available_evidence_ids=frozenset({"ev-price-1", "ev-risk-1"}),
    )

    assert result.status is ProposalParseStatus.PARSED
    assert result.proposal is not None
    assert result.proposal.feature_provenance.feature_snapshot_id == UUID(
        "00000000-0000-0000-0000-000000000103"
    )
    assert result.raw_response == raw_response
    assert result.errors == ()


def test_unknown_evidence_fails_closed_without_returning_a_proposal() -> None:
    result = ProposalService().parse(
        raw_response=valid_raw_response(),
        feature_snapshot=feature_snapshot(),
        available_evidence_ids=frozenset({"ev-price-1"}),
    )

    assert result.status is ProposalParseStatus.REJECTED
    assert result.proposal is None
    assert result.errors == ("unknown_evidence_id:ev-risk-1",)


def test_feature_snapshot_identity_mismatch_fails_closed() -> None:
    snapshot = feature_snapshot()
    mismatched = FeatureSnapshot(
        feature_snapshot_id=UUID("00000000-0000-0000-0000-000000000999"),
        strategy_id=snapshot.strategy_id,
        strategy_version=snapshot.strategy_version,
        market=snapshot.market,
        security_id=snapshot.security_id,
        data_snapshot_id=snapshot.data_snapshot_id,
        as_of=snapshot.as_of,
        feature_version=snapshot.feature_version,
        values=snapshot.values,
        data_quality_status=snapshot.data_quality_status,
        quality_disposition=snapshot.quality_disposition,
    )

    result = ProposalService().parse(
        raw_response=valid_raw_response(),
        feature_snapshot=mismatched,
        available_evidence_ids=frozenset({"ev-price-1", "ev-risk-1"}),
    )

    assert result.status is ProposalParseStatus.REJECTED
    assert "feature_binding_mismatch:feature_snapshot_id" in result.errors


def test_predicate_must_reference_a_supplied_feature() -> None:
    proposal = TradePlanProposal.model_validate(valid_proposal_payload())
    predicate = proposal.entry_predicates[0].model_copy(
        update={"feature_name": "swing_v1.unsupplied_feature"}
    )
    raw_response = proposal.model_copy(
        update={"entry_predicates": (predicate,)}
    ).model_dump_json()

    result = ProposalService().parse(
        raw_response=raw_response,
        feature_snapshot=feature_snapshot(),
        available_evidence_ids=frozenset({"ev-price-1", "ev-risk-1"}),
    )

    assert result.status is ProposalParseStatus.REJECTED
    assert result.errors == (
        "unsupported_predicate_feature:swing_v1.unsupplied_feature",
    )


def test_markdown_wrapped_json_is_rejected_and_retained_verbatim() -> None:
    raw_response = f"```json\n{valid_raw_response()}\n```"

    result = ProposalService().parse(
        raw_response=raw_response,
        feature_snapshot=feature_snapshot(),
    )

    assert result.status is ProposalParseStatus.REJECTED
    assert result.raw_response == raw_response
    assert result.proposal is None
