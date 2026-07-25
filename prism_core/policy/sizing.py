"""Deterministic stop-distance sizing from resolved validator dispositions."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_FLOOR

from prism_core.policy.dispositions import DispositionAction, FieldDisposition
from prism_core.policy.proposal_validator import (
    ProposalValidationResult,
    ProposalValidationStatus,
)


class SizingPolicyViolation(ValueError):
    """Validated inputs are absent or unsafe for deterministic sizing."""


@dataclass(frozen=True)
class SizingConstraints:
    """Explicit injected research/SHADOW limits; no production defaults exist."""

    account_risk_budget: Decimal
    max_risk_multiplier: Decimal
    fx_rate_to_base: Decimal
    available_cash: Decimal
    symbol_exposure_headroom: Decimal
    sector_exposure_headroom: Decimal
    market_exposure_headroom: Decimal
    currency_exposure_headroom: Decimal
    gross_exposure_headroom: Decimal
    open_order_exposure_headroom: Decimal
    max_liquidity_notional: Decimal
    lot_size: int

    def __post_init__(self) -> None:
        for label, value in (
            ("account_risk_budget", self.account_risk_budget),
            ("max_risk_multiplier", self.max_risk_multiplier),
            ("fx_rate_to_base", self.fx_rate_to_base),
            ("available_cash", self.available_cash),
            ("symbol_exposure_headroom", self.symbol_exposure_headroom),
            ("sector_exposure_headroom", self.sector_exposure_headroom),
            ("market_exposure_headroom", self.market_exposure_headroom),
            ("currency_exposure_headroom", self.currency_exposure_headroom),
            ("gross_exposure_headroom", self.gross_exposure_headroom),
            ("open_order_exposure_headroom", self.open_order_exposure_headroom),
            ("max_liquidity_notional", self.max_liquidity_notional),
        ):
            if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise ValueError(f"{label} must be a non-negative finite Decimal")
        if self.max_risk_multiplier <= 0 or self.max_risk_multiplier > Decimal("1"):
            raise ValueError("max_risk_multiplier must be greater than 0 and at most 1")
        if self.fx_rate_to_base <= 0:
            raise ValueError("fx_rate_to_base must be greater than 0")
        if type(self.lot_size) is not int or self.lot_size <= 0:
            raise ValueError("lot_size must be a positive integer")


@dataclass(frozen=True)
class SizingResult:
    """Auditable deterministic quantity calculation without order authority."""

    resolved_stop: Decimal
    resolved_risk_multiplier: Decimal
    risk_per_unit: Decimal
    risk_limited_quantity: int
    final_quantity: int
    limiting_constraint: str


class DeterministicSizingPolicy:
    """Calculate long-only quantity using resolved fields, never raw proposal fields."""

    def calculate(
        self,
        *,
        validation_result: ProposalValidationResult,
        entry_price: Decimal,
        stop_field_path: str,
        constraints: SizingConstraints,
    ) -> SizingResult:
        if not isinstance(validation_result, ProposalValidationResult):
            raise TypeError("validation_result must be a ProposalValidationResult")
        if validation_result.status is not ProposalValidationStatus.ACCEPTED:
            raise SizingPolicyViolation("accepted_validation_required")
        if not isinstance(entry_price, Decimal) or not entry_price.is_finite() or entry_price <= 0:
            raise ValueError("entry_price must be a positive finite Decimal")
        if not isinstance(stop_field_path, str) or not stop_field_path.strip():
            raise ValueError("stop_field_path must be a non-empty string")
        if not stop_field_path.startswith("stop_candidates[") or not stop_field_path.endswith(
            "].price"
        ):
            raise ValueError("stop_field_path must identify a stop candidate")
        if not isinstance(constraints, SizingConstraints):
            raise TypeError("constraints must be SizingConstraints")

        stop_disposition = _resolved_disposition(
            validation_result,
            field_path=stop_field_path,
            allowed_actions=frozenset({DispositionAction.ACCEPT, DispositionAction.CLAMP}),
        )
        multiplier_disposition = _resolved_disposition(
            validation_result,
            field_path="risk_multiplier_candidate.value",
            allowed_actions=frozenset({DispositionAction.ACCEPT, DispositionAction.CLAMP}),
        )
        resolved_stop = _decimal_from_resolved(stop_disposition, "resolved_stop_invalid")
        multiplier = _decimal_from_resolved(
            multiplier_disposition, "resolved_risk_multiplier_invalid"
        )
        if multiplier <= 0 or multiplier > Decimal("1"):
            raise SizingPolicyViolation("resolved_risk_multiplier_out_of_range")
        if multiplier > constraints.max_risk_multiplier:
            raise SizingPolicyViolation("configured_risk_multiplier_exceeded")
        if resolved_stop <= 0 or resolved_stop >= entry_price:
            raise SizingPolicyViolation("resolved_stop_not_below_entry")

        base_entry_price = entry_price * constraints.fx_rate_to_base
        risk_per_unit = (entry_price - resolved_stop) * constraints.fx_rate_to_base
        risk_quantity = _floor_units(
            constraints.account_risk_budget / risk_per_unit * multiplier
        )
        caps = (
            ("risk_budget", risk_quantity),
            ("available_cash", _floor_units(constraints.available_cash / base_entry_price)),
            (
                "symbol_exposure",
                _floor_units(constraints.symbol_exposure_headroom / base_entry_price),
            ),
            (
                "sector_exposure",
                _floor_units(constraints.sector_exposure_headroom / base_entry_price),
            ),
            (
                "market_exposure",
                _floor_units(constraints.market_exposure_headroom / base_entry_price),
            ),
            (
                "currency_exposure",
                _floor_units(constraints.currency_exposure_headroom / base_entry_price),
            ),
            (
                "gross_exposure",
                _floor_units(constraints.gross_exposure_headroom / base_entry_price),
            ),
            (
                "open_order_exposure",
                _floor_units(constraints.open_order_exposure_headroom / base_entry_price),
            ),
            (
                "liquidity_notional",
                _floor_units(constraints.max_liquidity_notional / base_entry_price),
            ),
        )
        limiting_constraint, uncapped_lot_quantity = min(caps, key=lambda item: item[1])
        final_quantity = uncapped_lot_quantity - (
            uncapped_lot_quantity % constraints.lot_size
        )
        return SizingResult(
            resolved_stop=resolved_stop,
            resolved_risk_multiplier=multiplier,
            risk_per_unit=risk_per_unit,
            risk_limited_quantity=risk_quantity,
            final_quantity=final_quantity,
            limiting_constraint=limiting_constraint,
        )


def _resolved_disposition(
    validation_result: ProposalValidationResult,
    *,
    field_path: str,
    allowed_actions: frozenset[DispositionAction],
) -> FieldDisposition:
    matches = tuple(
        item for item in validation_result.dispositions if item.field_path == field_path
    )
    if len(matches) != 1:
        raise SizingPolicyViolation(f"unique_resolved_disposition_required:{field_path}")
    disposition = matches[0]
    if disposition.action not in allowed_actions or disposition.resolved_value is None:
        raise SizingPolicyViolation(f"usable_resolved_disposition_required:{field_path}")
    return disposition


def _decimal_from_resolved(disposition: FieldDisposition, reason: str) -> Decimal:
    try:
        value = Decimal(disposition.resolved_value or "")
    except InvalidOperation as exc:
        raise SizingPolicyViolation(reason) from exc
    if not value.is_finite():
        raise SizingPolicyViolation(reason)
    return value


def _floor_units(value: Decimal) -> int:
    return int(value.to_integral_value(rounding=ROUND_FLOOR))
