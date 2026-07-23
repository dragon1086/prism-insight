"""Runtime modes and external-effect settings for the target PRISM system."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class ProductMode(str, Enum):
    """Approved product modes. Live trading is intentionally not represented."""

    RESEARCH = "research"
    SHADOW = "shadow"
    INTERNAL_PAPER = "internal_paper"
    BROKER_PAPER = "broker_paper"


class Phase1BrokerCapabilityError(ValueError):
    """Raised when a Phase 1 mode requests an external broker capability."""


PHASE1_MODES = frozenset(
    {ProductMode.RESEARCH, ProductMode.SHADOW, ProductMode.INTERNAL_PAPER}
)


@dataclass(frozen=True)
class RuntimeSettings:
    """Dependency-free runtime policy settings with safe defaults."""

    product_mode: ProductMode | str = ProductMode.RESEARCH
    telegram_enabled: bool = False
    broker_enabled: bool = False
    research_db_path: Path = field(default_factory=lambda: Path("research.sqlite"))
    paper_db_path: Path = field(default_factory=lambda: Path("paper.sqlite"))
    ops_db_path: Path = field(default_factory=lambda: Path("ops.sqlite"))
    allowed_chat_id: str | None = field(default=None, repr=False)
    allowed_user_id: str | None = field(default=None, repr=False)
    telegram_bot_token: str | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        for field_name in ("telegram_enabled", "broker_enabled"):
            if type(getattr(self, field_name)) is not bool:
                raise ValueError(f"{field_name} must be a boolean")

        try:
            mode = ProductMode(self.product_mode)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{self.product_mode!r} is not an approved product mode"
            ) from exc
        object.__setattr__(self, "product_mode", mode)

        if self.broker_enabled and mode is not ProductMode.BROKER_PAPER:
            raise Phase1BrokerCapabilityError(
                f"broker capability is unavailable in Phase 1 mode {mode.value!r}"
            )
