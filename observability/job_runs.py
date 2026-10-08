"""One event per scheduled job run — the ledger's heartbeat (fail-open).

Decision events only appear when something happens, so on their own they
cannot tell "the hard-stop loop ran and found nothing" from "the loop stopped
running".  ``job.run_completed`` is appended at the end of every run of the
analysis batches and the intraday loops with its status (OK, SKIPPED, ERROR,
TIMEOUT, HOLIDAY, DISABLED), duration and the run's own counters, so a missing
run is visible as a gap in time.  Observation only: never raises.
"""

from __future__ import annotations

import time
from typing import Any, Mapping

from observability.events import emit_event

EVENT_TYPE = "job.run_completed"
_SUMMARY_LIMIT = 30


def _summary(summary: Mapping[str, Any] | None) -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in (summary or {}).items():
        if len(flat) >= _SUMMARY_LIMIT:
            break
        if isinstance(value, (bool, int, float)) or value is None:
            flat[str(key)] = value
        elif isinstance(value, str):
            flat[str(key)] = value[:120]
    return flat


def emit_job_run(
    job: str,
    *,
    status: str,
    market: str | None = None,
    mode: str | None = None,
    run_id: str | None = None,
    started: float | None = None,
    summary: Mapping[str, Any] | None = None,
    reason: str | None = None,
    error: BaseException | str | None = None,
) -> dict[str, Any] | None:
    """Append one ``job.run_completed`` event; ``started`` is a ``time.monotonic()`` value."""
    try:
        if isinstance(error, BaseException):
            error = f"{type(error).__name__}: {str(error)[:200]}"
        return emit_event(
            EVENT_TYPE,
            service=f"prism-job-{job}",
            market=(market or "").upper() or None,
            severity="INFO" if status not in ("ERROR", "TIMEOUT") else "ERROR",
            attributes={
                "job": job,
                "status": status,
                "mode": mode,
                "run_id": run_id,
                "duration_s": round(time.monotonic() - started, 3) if started is not None else None,
                "reason": str(reason)[:200] if reason else None,
                "error": str(error)[:300] if error else None,
                "summary": _summary(summary),
            },
        )
    except Exception:  # noqa: BLE001 - observation must never affect a job
        return None


__all__ = ["EVENT_TYPE", "emit_job_run"]
