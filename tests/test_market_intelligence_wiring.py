"""Boundary wiring supplements real KR/US batch integrations."""
import ast
import asyncio
import io
import json
import sys
from datetime import datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize("present", [False, True])
def test_batch_metadata_reaches_shared_macro_context_without_changing_candidates(monkeypatch, market, present):
    path = ROOT / ("stock_analysis_orchestrator.py" if market == "KR" else "prism-us/us_stock_analysis_orchestrator.py")
    module = ModuleType("trigger_batch" if market == "KR" else "us_trigger_batch")
    frame = pd.DataFrame({"Company Name": ["Test"], "CompanyName": ["Test"]}, index=["TEST"])
    module.run_batch = Mock(return_value={"Gap": frame})
    module.MarketSnapshotUnavailableError = RuntimeError
    monkeypatch.setitem(sys.modules, module.__name__, module)
    function = next(node for node in ast.walk(ast.parse(path.read_text()))
                    if isinstance(node, ast.AsyncFunctionDef) and node.name == "run_trigger_batch")
    participation = {"status": "AVAILABLE", "advance": 1, "decline": 0,
                     "universe_count": 2, "missing_count": 1, "timing": "snapshot_may_be_intraday"}
    full = {"metadata": {"market_participation": participation} if present else {}}
    namespace = {"datetime": datetime, "asyncio": asyncio, "json": json, "logger": Mock(),
                 "os": SimpleNamespace(path=SimpleNamespace(exists=lambda _: True)), "PRISM_US_DIR": ROOT,
                 "resolve_us_trade_date": lambda _: "20260917",
                 "open": lambda *args, **kwargs: io.StringIO(json.dumps(full))}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)
    context = {"market_regime": "sideways", "leading_sectors": [{"sector": "Technology", "industry": "Semiconductors"}]}
    tickers = asyncio.run(namespace["run_trigger_batch"](SimpleNamespace(selected_tickers={}), "morning", context))
    assert len(tickers) == 1
    assert context["market_regime"] == "sideways"
    assert context["leading_sectors"][0]["industry"] == "Semiconductors"
    if present:
        assert context["market_intelligence"]["participation"] == participation
    else:
        assert "market_intelligence" not in context


def test_configuration_market_context_default_off_and_explicit_on(tmp_path):
    from tools.configure_report_research import configure
    path = tmp_path / "settings.json"
    assert configure(path, True)["market_context_enabled"] is False
    assert configure(path, True, market_context_enabled=True)["market_context_enabled"] is True
    assert json.loads(path.read_text())["market_context_enabled"] is True
    # Turning off research must not accidentally disable independent market data.
    assert configure(path, False)["market_context_enabled"] is True
    assert configure(path, False, market_context_enabled=False)["market_context_enabled"] is False


def test_compact_buy_context_preserves_rows_provenance_and_valid_json():
    from prism_core.report_research_context import market_context_for_buy
    packet = {"contract": "market_etf_rotation_v1", "market": "US", "status": "AVAILABLE",
              "price_asof": "2026-09-16", "source": "yfinance_adjusted_daily", "input_sha256": "abc",
              "unused_debug": "x" * 50000,
              "rows": [{"symbol": "IWF", "returns_pct": {"20": 1.2}, "relative_spy_pp": {"20": .2}}]}
    text = market_context_for_buy({"market_intelligence": packet,
                                  "leading_sectors": [{"sector": "Technology", "industry": "Semiconductors"}]})
    data = json.loads(text[text.index('{'):])
    assert data["market_intelligence"]["rows"][0]["symbol"] == "IWF"
    assert data["market_intelligence"]["source"] == "yfinance_adjusted_daily"
    assert data["leading_sectors"][0]["industry"] == "Semiconductors"
    packet["limitations"] = "x" * 20000
    text = market_context_for_buy({"market_intelligence": packet})
    assert json.loads(text[text.index('{'):])["market_intelligence"]["status"] == "UNKNOWN"


@pytest.mark.parametrize("market", ["KR", "US"])
def test_macro_packet_preserved_and_buy_injection_precedes_both_execution_paths(market):
    base = ROOT if market == "KR" else ROOT / "prism-us"
    prefix = "" if market == "KR" else "us_"
    orchestrator = (base / f"{prefix}stock_analysis_orchestrator.py").read_text()
    tracker = (base / f"{prefix}stock_tracking_agent.py").read_text()
    assert 'macro_data["market_intelligence"] = prefetched["market_intelligence"]' in orchestrator
    assert '"market_intelligence": macro_context["market_intelligence"]' in orchestrator
    assert '"leading_sectors": (macro_context or {}).get("leading_sectors")' in orchestrator
    offset = tracker.index('prompt_message += market_context_for_buy(')
    assert offset < tracker.index('codex_enabled =', offset)
