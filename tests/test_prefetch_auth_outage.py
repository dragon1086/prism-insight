import ast
from pathlib import Path
import sys
from types import SimpleNamespace

from cores import data_prefetch


def test_prefetch_uses_repository_provider_chain_not_eager_legacy_server(monkeypatch):
    monkeypatch.setitem(sys.modules, "kospi_kosdaq_stock_server", SimpleNamespace(__name__="legacy_sentinel"))
    server = data_prefetch._get_mcp_server_module()
    assert server.__name__ == "cores.market_data.mcp_server"


def test_sector_auth_failure_is_optional_and_does_not_invent_data(monkeypatch):
    def unavailable(*args, **kwargs):
        raise RuntimeError("authentication unavailable")
    monkeypatch.setattr("cores.kis_sector_map.get_sector_info", unavailable)
    assert data_prefetch._prefetch_sector_info("20260911", "KOSPI") == {}


def test_sector_lookup_keeps_original_provider_and_reference_date(monkeypatch):
    seen = []
    def query(day, market):
        seen.append((day, market))
        return {"005930": "전기전자"}
    monkeypatch.setattr("cores.kis_sector_map.get_sector_info", query)
    assert data_prefetch._prefetch_sector_info("20260911", "KOSDAQ") == {"005930": "전기전자"}
    assert seen == [("20260911", "KOSDAQ")]


def test_macro_uses_sector_helper_not_legacy_server_attribute():
    source = (Path(__file__).resolve().parents[1] / "cores/data_prefetch.py").read_text()
    node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == "prefetch_macro_intelligence_data")
    text = ast.get_source_segment(source, node)
    assert "_prefetch_sector_info(reference_date" in text
    assert "server.get_sector_info" not in text


def test_macro_prefetch_remains_off_event_loop():
    source = (Path(__file__).resolve().parents[1] / "stock_analysis_orchestrator.py").read_text()
    tree = ast.parse(source)
    macro = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "run_macro_intelligence")
    assert "await asyncio.to_thread(prefetch_macro_intelligence_data" in ast.get_source_segment(source, macro)


def test_filtered_stdio_child_has_no_legacy_browser_guard():
    source = (Path(__file__).resolve().parents[1] / "cores/market_data/mcp_server.py").read_text()
    assert "KRX_ALLOW_BROWSER_LOGIN" not in source
