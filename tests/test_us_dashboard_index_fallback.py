"""A transient KIS index error must not blank the US benchmark and cumulative-return chart."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))

import generate_us_dashboard_json as gen  # noqa: E402


def test_index_fetch_error_reuses_previous_series(tmp_path, monkeypatch):
    previous = tmp_path / "us_dashboard_data.json"
    rows = [{"date": "2026-11-18", "spx_index": 7323.63, "nasdaq_index": 24000.0, "condition": 0, "volatility": 0}]
    previous.write_text(json.dumps({"market_condition": rows}), encoding="utf-8")
    monkeypatch.setattr(gen.USDashboardDataGenerator._previous_market_condition, "__defaults__", (previous,))

    def fail(*args, **kwargs):
        raise RuntimeError("KIS FHKST03030100 rejected")

    monkeypatch.setattr("prism_core.us_market_data.history", fail)
    generator = gen.USDashboardDataGenerator.__new__(gen.USDashboardDataGenerator)
    generator.US_SEASON1_START_DATE = "2026-01-29"

    assert generator.get_us_market_condition() == rows


def test_missing_previous_file_returns_empty(tmp_path):
    assert gen.USDashboardDataGenerator._previous_market_condition(tmp_path / "absent.json") == []


def test_real_summary_counts_sale_proceeds_still_settling():
    """11/20: a sell's proceeds were settling, so eval + settled cash showed $9,330.94 instead of $10,199.07."""
    portfolio = [{"value": 2035.71, "profit": -15.5}]
    account = {"usd_cash": 7295.23, "available_amount": 7295.23, "net_unsettled_usd": 868.13, "total_asset_usd": 10199.07}
    generator = gen.USDashboardDataGenerator.__new__(gen.USDashboardDataGenerator)

    summary = generator.calculate_real_trading_summary(portfolio, account)

    assert summary["total_assets"] == 10199.07
    assert round(summary["total_cash"], 2) == 8163.36
    assert summary["available_amount"] == 7295.23
