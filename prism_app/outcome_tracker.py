"""Thin append-only proposal-outcome application boundary."""

from __future__ import annotations

import sqlite3

from prism_core.feedback.outcomes import OutcomeRecord, OutcomeRepository
from prism_core.feedback.repository import AppendDisposition
from prism_core.persistence.feedback_cycle import (
    MarketOutcomeInput,
    MarketOutcomeService,
    PendingMarketOutcomeInput,
    ProcessQualityRecord,
    ProcessQualityRepository,
)
from prism_core.persistence.leadership_history import (
    LeadershipHistoryRecord,
    LeadershipHistoryRepository,
)


class OutcomeTracker:
    """Schedule-agnostic coordinator over the authoritative outcome repository."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._repository = OutcomeRepository(connection)
        self._leadership_history = LeadershipHistoryRepository(connection)
        self._process_quality = ProcessQualityRepository(connection)
        self._market_outcomes = MarketOutcomeService(connection)

    def record(self, outcome: OutcomeRecord) -> AppendDisposition:
        return self._repository.append(outcome)

    def record_leadership(
        self, record: LeadershipHistoryRecord
    ) -> AppendDisposition:
        return self._leadership_history.append(record)

    def record_process_quality(
        self, record: ProcessQualityRecord
    ) -> AppendDisposition:
        return self._process_quality.append(record)

    def record_market_outcome(
        self, item: MarketOutcomeInput
    ) -> AppendDisposition:
        return self._market_outcomes.record(item)

    def record_pending_market_outcome(
        self, item: PendingMarketOutcomeInput
    ) -> AppendDisposition:
        return self._market_outcomes.record_pending(item)
