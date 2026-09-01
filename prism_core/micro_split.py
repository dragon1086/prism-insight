"""Pure domain model for PRISM's 초분할 position-building policy.

This module has no database, network, broker, logging, or runtime-agent imports.
Importing it cannot place an order or change the existing all-in/all-out paths.
It only validates target-exposure transitions and projects the whole-share
quantity that an execution adapter could request on an upward stage change.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


PYRAMID_REGIMES = frozenset({"strong_bull", "parabolic"})


def _strictly_increasing(values: tuple[int, ...]) -> bool:
    return bool(values) and all(left < right for left, right in zip(values, values[1:]))


def _regime(value: str) -> str:
    raw = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return raw.split(":", 1)[0]


def _positive_decimal(value, *, field: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{field} must be numeric") from error
    if not parsed.is_finite() or parsed <= 0:
        raise ValueError(f"{field} must be positive")
    return parsed


@dataclass(frozen=True)
class MicroSplitPolicy:
    """Versioned cumulative target percentages where one slot equals 100%."""

    policy_version: str
    base_steps_pct: tuple[int, ...] = (10, 30, 60, 100)
    pyramid_steps_pct: tuple[int, ...] = (130, 160, 200, 230, 260, 300)
    pyramid_regimes: frozenset[str] = PYRAMID_REGIMES

    def __post_init__(self) -> None:
        if not str(self.policy_version).strip():
            raise ValueError("policy_version is required")
        if not _strictly_increasing(self.base_steps_pct):
            raise ValueError("base_steps_pct must be strictly increasing")
        if self.base_steps_pct[-1] != 100 or any(
            step <= 0 or step > 100 for step in self.base_steps_pct
        ):
            raise ValueError("base steps must be within 1..100 and end at 100")
        if not _strictly_increasing(self.pyramid_steps_pct):
            raise ValueError("pyramid_steps_pct must be strictly increasing")
        if any(step <= 100 or step > 300 for step in self.pyramid_steps_pct):
            raise ValueError("pyramid steps must be within 101..300")
        if self.pyramid_steps_pct[-1] != 300:
            raise ValueError("pyramid steps must end at 300")

    def max_target_pct(self, regime: str) -> int:
        return 300 if _regime(regime) in self.pyramid_regimes else 100

    def allowed_steps_pct(self, regime: str) -> tuple[int, ...]:
        if self.max_target_pct(regime) > 100:
            return self.base_steps_pct + self.pyramid_steps_pct
        return self.base_steps_pct


DEFAULT_POLICY = MicroSplitPolicy(policy_version="micro-split-v1-draft")


@dataclass(frozen=True)
class MicroSplitTransition:
    policy_version: str
    regime: str
    previous_target_pct: int
    target_pct: int
    max_target_pct: int
    is_pyramid: bool

    @property
    def target_slot_units(self) -> float:
        return self.target_pct / 100.0


@dataclass(frozen=True)
class ExecutionProjection:
    """Whole-share projection only; it is not an order or a fill claim."""

    previous_target_pct: int
    target_pct: int
    target_notional: float
    execution_price: float
    confirmed_strategy_quantity: int
    desired_quantity: int
    buy_delta_quantity: int
    projected_notional: float


def advance_target(
    policy: MicroSplitPolicy,
    previous_target_pct: int,
    target_pct: int,
    *,
    regime: str,
) -> MicroSplitTransition:
    """Validate one monotonic internal-ledger target transition."""
    previous = int(previous_target_pct)
    target = int(target_pct)
    if previous < 0:
        raise ValueError("previous_target_pct cannot be negative")
    if target <= previous:
        raise ValueError("target_pct must increase; invalidation closes the campaign")
    cap = policy.max_target_pct(regime)
    if target > cap:
        raise ValueError(f"target_pct {target} exceeds regime cap {cap}")
    if target not in policy.allowed_steps_pct(regime):
        raise ValueError(f"target_pct {target} is not a policy step")
    return MicroSplitTransition(
        policy_version=policy.policy_version,
        regime=_regime(regime),
        previous_target_pct=previous,
        target_pct=target,
        max_target_pct=cap,
        is_pyramid=target > 100,
    )


def project_execution_on_advance(
    *,
    unit_amount,
    previous_target_pct: int,
    target_pct: int,
    execution_price,
    confirmed_strategy_quantity: int,
) -> ExecutionProjection:
    """Project a non-negative whole-share delta for an upward target transition.

    The function intentionally refuses an unchanged or lower target. Therefore a
    price decline alone cannot create an averaging-down order. Broker execution
    status never changes the internal target; callers store it in a separate
    execution ledger.
    """
    previous = int(previous_target_pct)
    target = int(target_pct)
    if target <= previous:
        raise ValueError("target_pct must increase before projecting execution")
    if target <= 0:
        raise ValueError("target_pct must be positive")
    confirmed = int(confirmed_strategy_quantity)
    if confirmed < 0:
        raise ValueError("confirmed_strategy_quantity cannot be negative")

    unit = _positive_decimal(unit_amount, field="unit_amount")
    price = _positive_decimal(execution_price, field="execution_price")
    target_notional = unit * Decimal(target) / Decimal(100)
    desired_quantity = int(target_notional // price)
    buy_delta = max(0, desired_quantity - confirmed)
    projected_notional = price * Decimal(desired_quantity)
    return ExecutionProjection(
        previous_target_pct=previous,
        target_pct=target,
        target_notional=float(target_notional),
        execution_price=float(price),
        confirmed_strategy_quantity=confirmed,
        desired_quantity=desired_quantity,
        buy_delta_quantity=buy_delta,
        projected_notional=float(projected_notional),
    )


__all__ = [
    "DEFAULT_POLICY",
    "ExecutionProjection",
    "MicroSplitPolicy",
    "MicroSplitTransition",
    "PYRAMID_REGIMES",
    "advance_target",
    "project_execution_on_advance",
]
