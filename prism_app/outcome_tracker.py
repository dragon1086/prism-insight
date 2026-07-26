"""Thin append-only proposal-outcome application boundary."""

from __future__ import annotations

import sqlite3

from prism_core.feedback.outcomes import OutcomeRecord, OutcomeRepository
from prism_core.feedback.repository import AppendDisposition


class OutcomeTracker:
    """Schedule-agnostic coordinator over the authoritative outcome repository."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._repository = OutcomeRepository(connection)

    def record(self, outcome: OutcomeRecord) -> AppendDisposition:
        return self._repository.append(outcome)
