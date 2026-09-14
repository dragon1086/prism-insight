"""Screening never supplies a fabricated trade scenario to report generation."""
import ast
import asyncio
import sys
from datetime import datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest


@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize("legacy_rr", [None, 3.0])
def test_orchestrator_does_not_revive_legacy_screening_ratio(monkeypatch, market, legacy_rr):
    root = Path(__file__).resolve().parents[1]
    path = root / ("stock_analysis_orchestrator.py" if market == "KR"
                   else "prism-us/us_stock_analysis_orchestrator.py")
    name = "trigger_batch" if market == "KR" else "us_trigger_batch"
    module = ModuleType(name)
    frame = pd.DataFrame({"Company Name": ["Test"], "CompanyName": ["Test"],
                          "Risk/Reward Ratio": [legacy_rr], "risk_reward_ratio": [legacy_rr]},
                         index=["TEST"])
    module.run_batch = Mock(return_value={"Gap": frame})
    module.MarketSnapshotUnavailableError = RuntimeError
    monkeypatch.setitem(sys.modules, name, module)
    tree = ast.parse(path.read_text())
    function = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)
                    and n.name == "run_trigger_batch")
    namespace = {"datetime": datetime, "asyncio": asyncio, "logger": Mock(),
                 "os": SimpleNamespace(path=SimpleNamespace(exists=lambda _: False)),
                 "PRISM_US_DIR": root, "resolve_us_trade_date": lambda _: "20260914"}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)  # noqa: S102
    result = asyncio.run(namespace["run_trigger_batch"](SimpleNamespace(selected_tickers={}), "morning"))
    assert len(result) == 1
    assert result[0]["risk_reward_ratio"] is None
    assert module.run_batch.call_count == 1
