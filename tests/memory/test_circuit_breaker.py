"""
Focused test for threading safety of the _FAILURES deque circuit breaker
in tracking/memory/extract.py.

10 concurrent threads each record a failure. After all threads finish, the
breaker state must be internally consistent (no torn reads, no lost updates).
"""

from __future__ import annotations

import threading

import pytest

import tracking.memory.extract as extract_mod
from tracking.memory.extract import (
    BREAKER_THRESHOLD,
    _breaker_open,
    _record_failure,
    reset_breaker,
)


@pytest.fixture(autouse=True)
def _clean_breaker():
    reset_breaker()
    yield
    reset_breaker()


def test_concurrent_record_failure_consistent():
    """10 threads each call _record_failure once; breaker state is consistent."""
    n_threads = 10
    barrier = threading.Barrier(n_threads)
    errors: list = []

    def worker():
        try:
            barrier.wait()  # all start simultaneously
            _record_failure()
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Exceptions in worker threads: {errors}"

    # With 10 failures and BREAKER_THRESHOLD=5, breaker must be open.
    assert _breaker_open(), (
        f"Expected breaker open after {n_threads} failures "
        f"(threshold={BREAKER_THRESHOLD})"
    )


def test_concurrent_reset_and_record_no_crash():
    """Simultaneous resets and records must not crash (no torn deque state)."""
    errors: list = []
    stop = threading.Event()

    def recorder():
        for _ in range(50):
            try:
                _record_failure()
            except Exception as e:
                errors.append(e)

    def resetter():
        for _ in range(20):
            try:
                reset_breaker()
            except Exception as e:
                errors.append(e)

    def checker():
        for _ in range(30):
            try:
                _breaker_open()
            except Exception as e:
                errors.append(e)

    threads = (
        [threading.Thread(target=recorder) for _ in range(4)]
        + [threading.Thread(target=resetter) for _ in range(2)]
        + [threading.Thread(target=checker) for _ in range(2)]
    )
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Exceptions during concurrent breaker ops: {errors}"


def test_breaker_trips_at_threshold():
    """Breaker is closed below threshold and open at/above it."""
    for i in range(BREAKER_THRESHOLD - 1):
        assert not _breaker_open(), f"Should be closed at {i} failures"
        _record_failure()
    _record_failure()  # hit threshold
    assert _breaker_open(), "Should be open at threshold"
