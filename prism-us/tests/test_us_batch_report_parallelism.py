from __future__ import annotations

import asyncio
import sys
from types import ModuleType

import us_stock_analysis_orchestrator as orchestrator_module
from us_stock_analysis_orchestrator import (
    USStockAnalysisOrchestrator,
    _batch_report_parallel_limit,
)


def test_us_batch_report_parallel_limit_matches_kr_contract() -> None:
    key = "PRISM_BATCH_REPORT_MAX_CONCURRENCY"
    assert _batch_report_parallel_limit(5, {}) == 3
    assert _batch_report_parallel_limit(2, {}) == 2
    assert _batch_report_parallel_limit(5, {key: "2"}) == 2
    assert _batch_report_parallel_limit(5, {key: "0"}) == 1
    assert _batch_report_parallel_limit(5, {key: "99"}) == 5
    assert _batch_report_parallel_limit(5, {key: "invalid"}) == 3


def test_us_generate_reports_uses_kr_bounded_parallelism_and_keeps_order(
    monkeypatch, tmp_path
) -> None:
    active = 0
    max_active = 0

    async def fake_analyze_us_stock(ticker, **_kwargs):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep({"AAA": 0.03, "BBB": 0.02, "CCC": 0.01}[ticker])
        active -= 1
        return f"report-{ticker}"

    fake_module = ModuleType("cores.us_analysis")
    fake_module.analyze_us_stock = fake_analyze_us_stock
    monkeypatch.setitem(sys.modules, "cores.us_analysis", fake_module)
    monkeypatch.setattr(orchestrator_module, "US_REPORTS_DIR", tmp_path)
    monkeypatch.setenv("PRISM_BATCH_REPORT_MAX_CONCURRENCY", "2")

    orchestrator = USStockAnalysisOrchestrator.__new__(USStockAnalysisOrchestrator)
    reports = asyncio.run(orchestrator.generate_reports(
        [
            {"ticker": "AAA", "name": "Alpha"},
            {"ticker": "BBB", "name": "Beta"},
            {"ticker": "CCC", "name": "Gamma"},
        ],
        "morning",
        reference_date="20260821",
    ))

    assert max_active == 2
    assert [path.split("/")[-1].split("_")[0] for path in reports] == [
        "AAA", "BBB", "CCC",
    ]
