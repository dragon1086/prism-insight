"""Short-lived per-market lock around a new strategy entry (batch tracker and re-entry v3 LIVE).

The batch entry step (``_enter_eligible_candidate``) and the re-entry v3 LIVE entry take the same
``runtime/entry_lock_{kr,us}.lock`` so they never write a holdings row / place a buy for the same
market at the same moment; "already held" and slots are re-checked inside the lock
(``_buy_stock_with_position``). The batch waits and, if the lock cannot be taken in time, goes on
as before (fail-open: unchanged batch behaviour apart from the wait); the re-entry path fails
closed and skips the buy.
"""
from __future__ import annotations

import asyncio
import contextlib
import fcntl
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]


def lock_path(market):
    directory = Path(os.getenv("PRISM_ENTRY_LOCK_DIR") or (ROOT / "runtime"))
    return directory / f"entry_lock_{str(market).lower()}.lock"


@contextlib.asynccontextmanager
async def entry_lock(market, *, timeout=60.0, poll=0.2):
    """Yield True when the lock is held, False when it was not taken within ``timeout`` or is unusable.

    Never raises for a busy or unusable lock: the batch goes on (fail-open), the re-entry caller must
    skip the entry when this yields False (fail-closed). Waiting never blocks the event loop.
    """
    handle, acquired = None, False
    try:
        path = lock_path(market)
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("a")
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    break
                await asyncio.sleep(poll)
    except OSError as error:
        logger.warning("[ENTRY_LOCK][%s] unavailable: %s", market, type(error).__name__)
    if not acquired:
        logger.warning("[ENTRY_LOCK][%s] not acquired within %.0fs", market, timeout)
    try:
        yield acquired
    finally:
        if handle is not None:
            if acquired:
                with contextlib.suppress(OSError):
                    fcntl.flock(handle, fcntl.LOCK_UN)
            handle.close()
