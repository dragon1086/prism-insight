"""Auditable field-level actions emitted by deterministic proposal policy."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class DispositionAction(str, Enum):
    """Allowed deterministic treatments for one proposed or derived field."""

    ACCEPT = "ACCEPT"
    CLAMP = "CLAMP"
    RECALCULATE = "RECALCULATE"
    REJECT = "REJECT"


@dataclass(frozen=True)
class FieldDisposition:
    """Immutable audit record for one field-level validation decision."""

    field_path: str
    action: DispositionAction
    reason: str
    proposed_value: str | None = None
    resolved_value: str | None = None
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.field_path, str) or not self.field_path.strip():
            raise ValueError("field_path must be a non-empty string")
        if not isinstance(self.action, DispositionAction):
            raise TypeError("action must be a DispositionAction")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("reason must be a non-empty string")
        if any(not isinstance(item, str) or not item.strip() for item in self.evidence_ids):
            raise ValueError("evidence_ids must contain non-empty strings")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("evidence_ids must be unique")
