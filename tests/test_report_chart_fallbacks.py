"""Report charts that KIS-only data left blank (2026-09-26, 252990).

* Investor flows unavailable -> the volume slot charts total daily volume.
* KIS has no cap/valuation history -> WiseReport's reported fiscal-year
  summary feeds annual charts, never a history synthesized from a snapshot.
"""
import asyncio
import math
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from cores import stock_chart
from prism_core import kr_financial_summary as fs

FIXTURES = Path(__file__).parent / "fixtures" / "wisereport"
SUMMARY = (FIXTURES / "cF1001_252990.html").read_text(encoding="utf-8")
PAGE = ("<script>$.ajax({url: 'ajax/cF1001.aspx', data: {cmp_cd: '252990', "
        "encparam: 'MFIzY09MczZMZFQ1N1Znbi83aGM3Zz09', id: 'Qk80WlNFN0'}});</script>")


def _today():
    return datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d")


# --------------------------------------------------------------- source parse

def test_parses_reported_and_consensus_years_from_real_table():
    frame = fs.parse_financial_summary(SUMMARY)
    assert list(frame.index) == ["2021/12", "2022/12", "2023/12", "2024/12", "2025/12",
                                 "2026/12", "2027/12", "2028/12"]
    assert frame["estimate"].tolist() == [False] * 5 + [True] * 3
    row = frame.loc["2025/12"]
    assert (row["revenue"], row["operating_income"], row["eps"]) == (780, 147, 258)
    assert (row["per"], row["pbr"], row["roe"]) == (26.09, 2.35, 9.70)
    # Consensus columns only carry the "발표기준" operating income row.
    assert frame.loc["2026/12", "operating_income"] == 348
    # A loss year has no PER; the provider's N/A stays missing, not zero.
    assert math.isnan(frame.loc["2023/12", "per"])
    assert frame.attrs["basis"] == "IFRS별도"


def test_decoy_table_and_nonpositive_multiples_are_not_data():
    frame = fs.parse_financial_summary(SUMMARY.replace(">2.35<", ">0.00<"))
    assert 4286 not in frame.to_numpy()
    assert math.isnan(frame.loc["2025/12", "pbr"])


@pytest.mark.parametrize("html", ["", "<table><tr><th>x</th></tr></table>",
                                  SUMMARY.replace("PER(배)", "PXR")])
def test_malformed_summary_is_rejected(html):
    with pytest.raises(ValueError):
        fs.parse_financial_summary(html)


def test_collector_uses_page_token_and_referer():
    seen = []

    async def fetch(url, referer):
        seen.append((url, referer))
        return PAGE if referer is None else SUMMARY

    frame = asyncio.run(fs.collect_wisereport_financial_summary("252990", _today(), fetch=fetch))
    assert frame is not None and frame.attrs["ticker"] == "252990"
    assert seen[0] == (fs.PAGE_URL.format(code="252990"), None)
    assert "encparam=MFIzY09MczZMZFQ1N1Znbi83aGM3Zz09" in seen[1][0]
    assert "id=Qk80WlNFN0" in seen[1][0]
    assert seen[1][1] == fs.PAGE_URL.format(code="252990")


@pytest.mark.parametrize("code,reference,page", [
    ("252990", "20260911", PAGE),     # current page never attached to a past date
    ("25299", None, PAGE),            # invalid ticker
    ("252990", None, "<html></html>"),  # token missing
])
def test_collector_skips_instead_of_raising(code, reference, page):
    async def fetch(url, referer):
        return page if referer is None else SUMMARY

    assert asyncio.run(fs.collect_wisereport_financial_summary(
        code, reference or _today(), fetch=fetch)) is None


def test_collector_network_failure_is_optional():
    async def fetch(url, referer):
        raise OSError("connection reset")

    assert asyncio.run(fs.collect_wisereport_financial_summary("252990", _today(), fetch=fetch)) is None


# --------------------------------------------------------------- charts

@pytest.fixture
def summary():
    return fs.parse_financial_summary(SUMMARY)


@pytest.mark.parametrize("factory", ["create_annual_fundamentals_chart", "create_annual_earnings_chart"])
def test_annual_charts_render_from_reported_years(summary, factory):
    fig = getattr(stock_chart, factory)("252990", company_name="샘씨엔에스", financial_summary=summary)
    assert fig is not None
    text = " ".join(t.get_text() for t in fig.texts)
    assert "WiseReport" in text and "(E)" in text
    stock_chart.plt.close(fig)


@pytest.mark.parametrize("factory", ["create_annual_fundamentals_chart", "create_annual_earnings_chart"])
def test_annual_charts_need_two_reported_years(summary, factory):
    only_one = summary[summary["estimate"] | (summary.index == "2025/12")]
    assert getattr(stock_chart, factory)("252990", financial_summary=None) is None
    assert getattr(stock_chart, factory)("252990", financial_summary=only_one) is None


@pytest.fixture
def ohlcv():
    index = pd.bdate_range("2026-08-26", "2026-09-23")
    close = np.linspace(12_000, 17_000, len(index))
    return pd.DataFrame({"Open": close, "High": close, "Low": close, "Close": close,
                         "Volume": np.arange(len(index)) * 1000 + 200_000}, index=index)


def test_volume_chart_falls_back_to_total_volume(monkeypatch, ohlcv):
    monkeypatch.setattr(stock_chart, "get_market_trading_volume_by_date", lambda *a: pd.DataFrame())
    monkeypatch.setattr(stock_chart, "get_market_ohlcv_by_date", lambda *a, **k: ohlcv)
    fig = stock_chart.create_trading_volume_chart("252990", company_name="샘씨엔에스")
    assert fig is not None
    assert "investor-type breakdown unavailable" in fig.axes[0].get_title(loc='left')
    stock_chart.plt.close(fig)


def test_volume_chart_without_any_data_is_omitted(monkeypatch):
    monkeypatch.setattr(stock_chart, "get_market_trading_volume_by_date", lambda *a: pd.DataFrame())
    monkeypatch.setattr(stock_chart, "get_market_ohlcv_by_date", lambda *a, **k: pd.DataFrame())
    assert stock_chart.create_trading_volume_chart("252990", company_name="X") is None


def test_investor_flows_still_use_investor_chart(monkeypatch):
    flows = pd.DataFrame({"기관합계": [1, -2], "외국인합계": [3, 4], "개인": [-4, -2], "기타합계": [0, 0]},
                         index=pd.to_datetime(["2026-09-22", "2026-09-23"]))
    monkeypatch.setattr(stock_chart, "get_market_trading_volume_by_date", lambda *a: flows)
    monkeypatch.setattr(stock_chart, "get_market_ohlcv_by_date",
                        lambda *a, **k: pytest.fail("OHLCV fallback must not run when flows exist"))
    fig = stock_chart.create_trading_volume_chart("252990", company_name="X")
    assert fig.axes[0].get_title(loc='left').startswith("Net Purchase by Investor Type")
    stock_chart.plt.close(fig)


def test_empty_consensus_years_are_not_drawn(summary):
    sparse = summary.copy()
    sparse.loc[sparse["estimate"], ["per", "pbr", "roe", "revenue", "operating_income"]] = np.nan
    assert list(stock_chart._usable_annual(sparse, ["per", "pbr", "roe"]).index) == [
        "2021/12", "2022/12", "2023/12", "2024/12", "2025/12"]
