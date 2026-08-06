import asyncio
import sys
from types import ModuleType

import stock_analysis_orchestrator as orchestrator_module
from stock_analysis_orchestrator import (
    StockAnalysisOrchestrator,
    _batch_report_parallel_limit,
)


def test_batch_report_parallel_limit_defaults_to_three():
    assert _batch_report_parallel_limit(5, {}) == 3
    assert _batch_report_parallel_limit(2, {}) == 2


def test_batch_report_parallel_limit_honors_and_sanitizes_environment():
    key = "PRISM_BATCH_REPORT_MAX_CONCURRENCY"

    assert _batch_report_parallel_limit(5, {key: "2"}) == 2
    assert _batch_report_parallel_limit(5, {key: "0"}) == 1
    assert _batch_report_parallel_limit(5, {key: "99"}) == 5
    assert _batch_report_parallel_limit(5, {key: "invalid"}) == 3


def test_generate_reports_runs_with_bounded_parallelism_and_keeps_input_order(
    monkeypatch, tmp_path
):
    active = 0
    max_active = 0

    async def fake_analyze_stock(company_code, **_kwargs):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep({"001": 0.03, "002": 0.02, "003": 0.01}[company_code])
        active -= 1
        return f"report-{company_code}"

    fake_cores_main = ModuleType("cores.main")
    fake_cores_main.analyze_stock = fake_analyze_stock
    monkeypatch.setitem(sys.modules, "cores.main", fake_cores_main)
    monkeypatch.setattr(orchestrator_module, "REPORTS_DIR", tmp_path)
    monkeypatch.setenv("PRISM_BATCH_REPORT_MAX_CONCURRENCY", "2")

    orchestrator = StockAnalysisOrchestrator.__new__(StockAnalysisOrchestrator)
    reports = asyncio.run(
        orchestrator.generate_reports(
            [
                {"code": "001", "name": "첫째"},
                {"code": "002", "name": "둘째"},
                {"code": "003", "name": "셋째"},
            ],
            "afternoon",
        )
    )

    assert max_active == 2
    assert [path.split("/")[-1].split("_")[0] for path in reports] == [
        "001",
        "002",
        "003",
    ]
