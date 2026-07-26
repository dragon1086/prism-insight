"""Persistence-safe coordination for already-rendered daily analysis publication."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class AnalysisPublisher(Protocol):
    async def publish(self, analysis: object) -> None: ...


@dataclass(frozen=True)
class PublicationResult:
    attempted: bool
    succeeded: bool
    error_type: str | None = None


class ReportService:
    """Publish only an analysis object that its caller has already persisted.

    Rendering and read-model construction belong to Task 21. This service neither
    writes research evidence nor imports a concrete messaging transport.
    """

    def __init__(self, publisher: AnalysisPublisher | None = None) -> None:
        self._publisher = publisher

    @property
    def can_publish(self) -> bool:
        return self._publisher is not None

    async def publish_persisted(
        self, analysis: object, *, enabled: bool
    ) -> PublicationResult:
        if not enabled:
            return PublicationResult(attempted=False, succeeded=False)
        if self._publisher is None:
            raise RuntimeError("publication capability has no injected publisher")
        try:
            await self._publisher.publish(analysis)
        except Exception as exc:  # noqa: BLE001 - preserve persisted analysis
            return PublicationResult(
                attempted=True,
                succeeded=False,
                error_type=type(exc).__name__,
            )
        return PublicationResult(attempted=True, succeeded=True)
