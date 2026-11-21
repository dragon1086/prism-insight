"""ClickStack events through the shared ``observability.events`` spool (analysis only)."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)
SERVICE = "prism-inverse"


class PrismEvents:
    def emit(self, event_type: str, **fields) -> None:
        try:
            from observability.events import emit_event

            emit_event(event_type, service=SERVICE, **fields)
        except Exception as exc:  # observability must never block a decision
            logger.warning("inverse event %s not emitted: %s", event_type, type(exc).__name__)
