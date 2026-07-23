"""Structural interface for point-in-time market-data providers."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import AwareDatetime

from prism_core.data.contracts import MarketSnapshot, SecurityId


@runtime_checkable
class MarketDataProvider(Protocol):
    """Minimal adapter boundary; concrete network capabilities are defined later."""

    provider_name: str

    async def fetch_snapshot(
        self,
        *,
        security_ids: tuple[SecurityId, ...],
        as_of_date: AwareDatetime,
    ) -> MarketSnapshot:
        """Return an immutable snapshot containing only data knowable as of the instant."""
        ...
