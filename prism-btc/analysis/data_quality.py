"""Offline replay preflight. Missing execution bars are not tradable silence."""
from __future__ import annotations


def require_regular_bars(open_times, interval_ms: int) -> None:
    if interval_ms <= 0:
        raise ValueError("interval must be positive")
    times = [int(value) for value in open_times]
    if len(times) < 2:
        raise ValueError("insufficient execution bars")
    defects = [(a, b) for a, b in zip(times, times[1:]) if b-a != interval_ms]
    if defects:
        raise ValueError(f"execution data is discontinuous: {len(defects)} boundaries; first={defects[0]}")
