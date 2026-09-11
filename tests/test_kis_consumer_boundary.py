"""Production consumers must not reintroduce interactive exchange providers."""
import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CONSUMERS = [
    "stock_tracking_agent.py", "stock_tracking_enhanced_agent.py",
    "tracking/helpers.py", "tracking/compression.py", "performance_tracker_batch.py",
    "weekly_insight_report.py", "weekly_market_facts.py", "events/jeoningu_price_fetcher.py",
    "observability/third_slot_shadow.py", "update_stock_data.py",
    "examples/generate_dashboard_json.py",
    "tools/market_pulse_backtest.py", "tools/rs_rating_backtest.py",
    "tools/trend_exit_seller.py", "tools/verify_kakao_ask_e2e.py",
    "utils/backfill_performance_tracker.py", "utils/migrate_watchlist_to_performance_tracker.py",
    "kakao_bot/adapters/prism/report_adapter.py",
]


@pytest.mark.parametrize("filename", CONSUMERS)
def test_consumers_never_import_removed_exchange_clients(filename):
    tree = ast.parse((ROOT / filename).read_text())
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")
        elif isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
    assert not [name for name in imports if name.split(".")[0] in {"krx_data_client", "pykrx", "FinanceDataReader"}]


def test_weekly_missing_kis_snapshot_and_flows_are_unknown(monkeypatch):
    from datetime import date
    import weekly_market_facts as facts

    def missing(*args, **kwargs):
        raise ValueError("historical KIS snapshot unavailable")

    monkeypatch.setattr(facts, "_market_data_fn", lambda name: missing)
    monkeypatch.setattr(facts, "_kr_index_block", lambda *a, **k: ["- KOSPI: 2500"])
    result = facts.build_kr_facts(date(2026, 9, 7), date(2026, 9, 11))
    assert "시장 전체 투자자 수급: UNKNOWN" in result
    assert "종목 등락률: UNKNOWN" in result
    assert "확정값" not in result
    assert "KRX 원천" not in result


def test_weekly_does_not_fallback_to_naver_market_data():
    source = (ROOT / "weekly_market_facts.py").read_text()
    assert "finance.naver.com" not in source
    assert "_naver_investor_daily" not in source


@pytest.mark.asyncio
@pytest.mark.parametrize("today_present", [True, False])
async def test_kakao_session_facts_require_kis_today_and_prior_close(monkeypatch, today_present):
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    import pandas as pd
    from kakao_bot.adapters.prism import report_adapter as adapter
    from cores import market_data

    today = datetime.now(ZoneInfo("Asia/Seoul")).date()
    dates = [today - timedelta(days=2), today if today_present else today - timedelta(days=1)]
    frame = pd.DataFrame(
        {"Open": [100, 100], "High": [110, 120], "Low": [90, 95],
         "Close": [100, 110], "Volume": [1000, 2000]}, index=pd.to_datetime(dates),
    )
    calls = []

    def fetch(start, end, ticker):
        calls.append((start, end, ticker))
        return frame

    monkeypatch.setattr(adapter, "_resolve_kr_stock", lambda subject: ("005930", "삼성전자"))
    monkeypatch.setattr(market_data, "get_market_ohlcv_by_date", fetch)
    result = await adapter._stock_session_facts("삼성전자")
    assert calls[0][1:] == (today.strftime("%Y%m%d"), "005930")
    if today_present:
        assert "출처: KIS" in result
        assert "+10.00%" in result
    else:
        assert result == ""


@pytest.mark.parametrize("filename", [
    "utils/backfill_performance_tracker.py", "utils/migrate_watchlist_to_performance_tracker.py",
    "tools/rs_rating_backtest.py",
])
def test_direct_file_bootstrap_without_pythonpath(filename, tmp_path):
    import os
    import subprocess
    import sys
    env = dict(os.environ, PYTHONPATH="")
    code = (
        "import runpy; "
        f"scope=runpy.run_path({str(ROOT / filename)!r}, run_name='entrypoint_import_test'); "
        "import cores.market_data; "
        "assert scope.get('MARKET_DATA_AVAILABLE', True) is True"
    )
    result = subprocess.run([sys.executable, "-I", "-c", code], cwd=tmp_path, env=env,
                            text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
