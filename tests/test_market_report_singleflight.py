import ast
import asyncio
import logging
import sys
from pathlib import Path
from types import ModuleType

import pytest

from prism_core.market_report_singleflight import MarketReportCache


def test_three_concurrent_reports_generate_once_and_reuse_success():
    async def run():
        cache = MarketReportCache()
        calls = 0

        async def fake_llm():
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.01)
            return "market report"

        values = await asyncio.gather(*(cache.get("20260918", "ko", fake_llm) for _ in range(3)))
        assert values == ["market report"] * 3
        assert await cache.get("20260918", "ko", fake_llm) == "market report"
        assert calls == 1
    asyncio.run(run())


@pytest.mark.parametrize("failure", ["exception", "empty", "placeholder"])
def test_failed_result_is_not_cached(failure):
    async def run():
        cache = MarketReportCache()
        calls = 0

        async def fake_llm():
            nonlocal calls
            calls += 1
            if calls == 1:
                if failure == "exception":
                    raise RuntimeError("upstream failure")
                return "" if failure == "empty" else "Analysis failed: market"
            return "recovered"

        with pytest.raises((RuntimeError, ValueError)):
            await cache.get("20260918", "ko", fake_llm)
        assert await cache.get("20260918", "ko", fake_llm) == "recovered"
        assert calls == 2
    asyncio.run(run())


def test_batch_date_and_language_are_separate():
    async def run():
        cache = MarketReportCache()
        calls = 0

        async def fake_llm():
            nonlocal calls
            calls += 1
            return str(calls)

        assert await cache.get("20260918", "ko", fake_llm) == "1"
        assert await cache.get("20260919", "ko", fake_llm) == "2"
        assert await cache.get("20260918", "en", fake_llm) == "3"
        assert await MarketReportCache().get("20260918", "ko", fake_llm) == "4"
    asyncio.run(run())


def test_cancelling_one_waiter_does_not_cancel_shared_generation():
    async def run():
        cache = MarketReportCache()
        started, release = asyncio.Event(), asyncio.Event()
        calls = 0

        async def fake_llm():
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return "market"

        first = asyncio.create_task(cache.get("20260918", "ko", fake_llm))
        second = asyncio.create_task(cache.get("20260918", "ko", fake_llm))
        await started.wait()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        release.set()
        assert await second == "market"
        assert calls == 1
    asyncio.run(run())


def test_all_cancelled_waiters_do_not_leave_unretrieved_exception():
    async def run():
        loop = asyncio.get_running_loop()
        errors = []
        loop.set_exception_handler(lambda _, context: errors.append(context))
        cache = MarketReportCache()
        started, release = asyncio.Event(), asyncio.Event()

        async def failing_llm():
            started.set()
            await release.wait()
            raise RuntimeError("late failure")

        waiter = asyncio.create_task(cache.get("20260918", "ko", failing_llm))
        await started.wait()
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        release.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not cache._tasks
        assert not errors
    asyncio.run(run())


def test_actual_generate_reports_shares_cache_only_within_batch(monkeypatch, tmp_path):
    # Execute the actual method without importing operational module globals.
    source = Path(__file__).parents[1] / "prism-us/us_stock_analysis_orchestrator.py"
    tree = ast.parse(source.read_text())
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "USStockAnalysisOrchestrator")
    method = next(node for node in cls.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "generate_reports")
    namespace = {"asyncio": asyncio, "logger": logging.getLogger(__name__),
                 "_batch_report_parallel_limit": lambda count: min(count, 3),
                 "US_REPORTS_DIR": tmp_path, "REPORT_MODEL": "test-model",
                 "report_model_slug": lambda model: model}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), "exec"), namespace)  # noqa: S102 - trusted repository method
    calls = []
    received_caches = []

    async def fake_analysis(ticker, reference_date, language, market_report_cache, **kwargs):
        received_caches.append(market_report_cache)

        async def fake_llm():
            calls.append(ticker)
            await asyncio.sleep(0.01)
            return "shared market"

        return ticker + await market_report_cache.get(reference_date, language, fake_llm)

    fake_module = ModuleType("cores.us_analysis")
    fake_module.analyze_us_stock = fake_analysis
    monkeypatch.setitem(sys.modules, "cores.us_analysis", fake_module)

    async def run():
        stocks = [{"ticker": ticker, "name": ticker} for ticker in ("A", "B", "C")]
        first = await namespace["generate_reports"](None, stocks, "morning", reference_date="20260918")
        assert len(first) == 3
        assert [Path(path).read_text() for path in first] == [ticker + "shared market" for ticker in ("A", "B", "C")]
        assert len(calls) == 1
        assert received_caches[0] is received_caches[1] is received_caches[2]
        await namespace["generate_reports"](None, stocks, "afternoon", reference_date="20260918")
        assert len(calls) == 2
        assert received_caches[0] is not received_caches[3]
    asyncio.run(run())
