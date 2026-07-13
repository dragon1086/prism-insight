"""Cancellation-safe boundary for synchronous broker submissions."""

from __future__ import annotations

import asyncio


class OrderOutcomeUnknown(RuntimeError):
    """The caller timed out after a broker submission began."""


async def submit_blocking_order(task_registry, func, *args, logger):
    """Run ``func`` in a worker and return promptly with an unknown outcome.

    Cancelling a ``to_thread`` await cannot stop the worker. Retain that task so
    its eventual result is observed, but never report the timeout as a confirmed
    broker failure that would make an automatic retry appear safe.
    """
    task = asyncio.create_task(asyncio.to_thread(func, *args))
    task_registry.add(task)

    def completed(done_task):
        task_registry.discard(done_task)
        try:
            outcome = done_task.result()
            logger.error(
                "Timed-out broker submission completed later; reconcile before retry: %s",
                outcome,
            )
        except Exception as exc:
            logger.error("Timed-out broker submission later failed: %s", exc)

    try:
        result = await asyncio.shield(task)
        task_registry.discard(task)
        return result
    except asyncio.CancelledError as exc:
        task.add_done_callback(completed)
        raise OrderOutcomeUnknown(
            "Broker submission is still running; outcome is unknown and retry is unsafe"
        ) from exc
