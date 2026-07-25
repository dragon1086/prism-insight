"""Persistence contract for resumable Kakao Gateway sessions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class GatewayState:
    """The minimum state required by the Gateway RESUME command."""

    session_id: str | None = None
    sequence: int | None = None

    @property
    def can_resume(self) -> bool:
        return self.session_id is not None and self.sequence is not None


class GatewayStatePort(Protocol):
    async def load(self) -> GatewayState:
        """Load the most recently committed Gateway session state."""

    async def save(self, state: GatewayState) -> None:
        """Persist Gateway state after the related work has committed."""

    async def clear(self) -> None:
        """Discard an expired or otherwise non-resumable session."""
