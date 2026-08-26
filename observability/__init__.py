"""Fail-open observability helpers for PRISM trading pipelines."""

from .events import build_event, emit_event

__all__ = ["build_event", "emit_event"]
