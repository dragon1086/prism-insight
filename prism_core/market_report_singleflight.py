"""Batch-owned market report tasks; no global or cross-batch report cache."""
import asyncio
from collections.abc import Awaitable, Callable


class MarketReportCache:
    def __init__(self):
        self._tasks: dict[tuple[str, str, str], asyncio.Task[str]] = {}

    async def get(self, reference_date: str, language: str,
                  generate: Callable[[], Awaitable[str]], *, evidence_key: str = '') -> str:
        key = (reference_date, language, evidence_key)
        task = self._tasks.get(key)
        if task is None:
            async def validated():
                result = await generate()
                if not isinstance(result, str) or not result.strip():
                    raise ValueError("Empty market report")
                if result.strip().lower().startswith("analysis failed"):
                    raise ValueError("Failed market report placeholder")
                return result

            task = asyncio.create_task(validated())
            self._tasks[key] = task

            def completed(done):
                # Consume exceptions even if every caller was cancelled. Failed
                # tasks must not poison a later report in the same batch.
                failed = done.cancelled() or done.exception() is not None
                if failed and self._tasks.get(key) is done:
                    del self._tasks[key]

            task.add_done_callback(completed)
        # Individual report cancellation does not cancel another report's work.
        return await asyncio.shield(task)
