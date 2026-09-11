"""Prevent retired Korean market-data providers from re-entering any executable path."""
import ast
from datetime import datetime
from pathlib import Path
import subprocess
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

import cores.market_data as data
from cores.market_data.source import Unsupported
from cores.kis_sector_map import parse_sector_master

ROOT = Path(__file__).resolve().parents[1]
BANNED = {
    "krx_data_client", "kospi_kosdaq_stock_server", "pykrx", "FinanceDataReader",
    "cores.krx_openapi_snapshot", "cores.naver_market_snapshot",
    "cores.market_data.krx_source", "cores.market_data.fdr_source", "cores.market_data.naver_source",
}


def test_all_executable_sources_have_no_retired_imports_or_dynamic_imports():
    paths = subprocess.check_output(["git", "ls-files", "-z", "*.py"], cwd=ROOT).decode().split("\0")
    violations = []
    for name in paths:
        path = ROOT / name
        if not name or name.startswith(("tests/", "docs/", "tasks/")) or not path.is_file():
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            imports = []
            if isinstance(node, ast.Import):
                imports = [item.name for item in node.names]
            elif isinstance(node, ast.ImportFrom):
                imports = [node.module or ""]
            elif isinstance(node, ast.Call):
                function = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
                if function in {"__import__", "import_module"} and node.args and isinstance(node.args[0], ast.Constant):
                    imports = [node.args[0].value]
            for module in imports:
                if isinstance(module, str) and any(module == old or module.startswith(old + ".") for old in BANNED):
                    violations.append(f"{name}:{node.lineno}:{module}")
    assert not violations, "\n".join(violations)


def test_retired_provider_files_dependencies_and_login_template_are_removed():
    for file in ("cores/krx_openapi_snapshot.py", "cores/naver_market_snapshot.py", "cores/market_data/krx_source.py", "cores/market_data/fdr_source.py", "cores/market_data/naver_source.py"):
        assert not (ROOT / file).exists()
    requirements = (ROOT / "requirements.txt").read_text().lower()
    for name in ("pykrx", "finance-datareader", "kospi_kosdaq_stock_server"):
        assert name not in requirements
    env = (ROOT / ".env.example").read_text()
    for variable in ("KRX_ID=", "KRX_PW=", "KRX_OPENAPI_AUTH_KEY=", "KAKAO_ID=", "KAKAO_PW="):
        assert variable not in env
    config = (ROOT / "mcp_agent.config.yaml.example").read_text()
    assert "cores.market_data.mcp_server" in config
    for retired in ("kospi_kosdaq_stock_server", "KRX_ID:", "KRX_PW:", "KAKAO_ID:", "KAKAO_PW:"):
        assert retired not in config


@pytest.mark.parametrize("order", ["", "krx", "fdr,krx", "kis,naver,krx", "unknown", "kis"])
def test_stale_config_cannot_reactivate_retired_providers(monkeypatch, order):
    monkeypatch.setenv("PRISM_MARKET_DATA_SOURCES", order)
    data.set_default_chain(None)
    try:
        assert data.default_chain().names == ["kis"]
    finally:
        data.set_default_chain(None)


def test_whole_market_helpers_refuse_historical_universe(monkeypatch):
    monkeypatch.setattr(data, "_now_kst", lambda: datetime(2026, 9, 11, tzinfo=ZoneInfo("Asia/Seoul")))
    for function in (data.get_market_ticker_list, data.get_market_ohlcv_by_ticker, data.get_market_cap_by_ticker):
        with pytest.raises(Unsupported):
            function("20260910")


def test_current_master_membership_cap_units_and_calendar(monkeypatch):
    import cores.kis_market_snapshot as snapshot
    monkeypatch.setattr(data, "_now_kst", lambda: datetime(2026, 9, 11, tzinfo=ZoneInfo("Asia/Seoul")))
    master = SimpleNamespace(names={"005930": "Samsung", "000001": "Example"}, markets={"005930": "KOSPI", "000001": "KOSDAQ"}, observed_date="20260911", cap_df=pd.DataFrame({"시가총액": [100_000_000, 200_000_000]}, index=["005930", "000001"]))
    monkeypatch.setattr(snapshot, "fetch_kis_master_data", lambda: master)
    assert data.get_market_ticker_list("20260911", "KOSPI") == ["005930"]
    cap = data.get_market_cap_by_ticker("20260911", "KOSDAQ")
    assert cap.loc["000001", "시가총액"] == 200_000_000
    assert cap.attrs["data_status"] == "previous_session_snapshot"
    assert data.get_nearest_business_day_in_a_week("20260913") == "20260911"


def test_sector_fixed_width_uses_bytes_not_unicode_character_offsets():
    raw = b"0" + b"0013" + "전기전자".encode("cp949").ljust(40, b" ")
    assert parse_sector_master(raw + b"\n") == {("0", "0013"): "전기전자"}
