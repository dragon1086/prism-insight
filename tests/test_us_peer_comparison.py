"""Deterministic US peer comparison: selection, metrics, rendering (no network)."""
import asyncio
from datetime import date

import pandas as pd
import pytest

from prism_core import us_peer_comparison as peers

ASOF = date(2026, 9, 25)


def info(ticker, cap, sector="Technology", **extra):
    base = {"quoteType": "EQUITY", "exchange": "NMS", "marketCap": cap, "sector": sector,
            "financialCurrency": "USD", "shortName": f"{ticker} Inc.", "industryKey": "software-application",
            "enterpriseToRevenue": 10.0, "forwardPE": 30.0, "revenueGrowth": 0.2,
            "grossMargins": 0.7, "operatingMargins": 0.1, "totalRevenue": 4e9}
    base.update(extra)
    return base


def quarters(n, start="2026-06-30"):
    return list(pd.date_range(end=start, periods=n, freq="QE"))[::-1]


def income(revenues, gross=None, operating=None, start="2026-06-30"):
    cols = quarters(len(revenues), start)
    rows = {"Total Revenue": revenues}
    if gross:
        rows["Gross Profit"] = gross
    if operating:
        rows["Operating Income"] = operating
    return pd.DataFrame(rows, index=cols).T


def test_parse_candidates_accepts_fenced_json_and_drops_bad_rows():
    content = """```json
{"peers": [{"ticker": "dt", "name": "Dynatrace", "overlap": "APM"},
           {"ticker": "DDOG", "name": "self", "overlap": ""},
           {"ticker": "not a ticker", "name": "x", "overlap": ""},
           {"ticker": "DT", "name": "dup", "overlap": ""},
           {"ticker": "BRK.B", "name": "Berkshire", "overlap": "none"}, "junk"]}
```"""
    result = peers.parse_candidates(content, "DDOG")
    assert [r["ticker"] for r in result] == ["DT", "BRK.B"]
    assert peers.parse_candidates("no json here", "DDOG") == []
    assert peers.parse_candidates('[{"ticker": "A"}]', "B")[0]["ticker"] == "A"


@pytest.mark.parametrize("override, issue", [
    ({"quoteType": "ETF"}, "not_equity"),
    ({"exchange": "PNK"}, "not_us_listed"),
    ({"marketCap": None}, "no_market_cap"),
    ({"financialCurrency": "EUR"}, "non_usd_financials"),
    ({}, None),
])
def test_listing_validation(override, issue):
    assert peers.listing_issue(info("X", 1e9, **override)) == issue


def test_size_filter_keeps_two_largest_and_prefers_same_sector():
    target = info("T", 100e9)
    candidates = [{"ticker": t, "name": t, "overlap": ""} for t in ("A", "B", "C", "D", "E", "F")]
    infos = {"A": info("A", 5e9), "B": info("B", 50e9, sector="Industrials"), "C": info("C", 20e9),
             "D": info("D", 30e9), "E": info("E", 11e9), "F": info("F", 12e9, quoteType="ETF")}
    kept, excluded = peers.select_peers(target, candidates, infos)
    assert [p["ticker"] for p in kept] == ["C", "D", "E", "B"]  # same sector first, cap 4
    reasons = {p["ticker"]: p["reason"] for p in excluded}
    assert reasons == {"A": "too_small", "F": "not_equity"}

    tiny = {t: info(t, cap) for t, cap in (("A", 1e9), ("B", 3e9), ("C", 2e9))}
    kept, excluded = peers.select_peers(target, candidates[:3], tiny)
    assert [p["ticker"] for p in kept] == ["B", "C"]
    assert [p["ticker"] for p in excluded] == ["A"]


def test_fallback_ceiling_drops_giant_peers():
    target = info("T", 10e9)
    candidates = [{"ticker": t, "name": "", "overlap": ""} for t in ("A", "B")]
    kept, excluded = peers.select_peers(target, candidates, {"A": info("A", 500e9), "B": info("B", 9e9)},
                                        cap_ceiling=10)
    assert [p["ticker"] for p in kept] == ["B"] and excluded[0]["reason"] == "too_large"


def test_ttm_growth_from_eight_consecutive_quarters():
    revenues = [120, 110, 100, 90, 80, 75, 70, 65]
    row = peers.compute_fundamentals(info("X", 1e9), income(revenues, gross=[60] * 8, operating=[12] * 8))
    assert row["revenue_ttm"] == 420 and row["revenue_basis"] == "quarterly_sum"
    assert row["growth"] == pytest.approx(420 / 290 - 1) and row["growth_basis"] == "ttm_yoy"
    assert row["gross_margin"] == pytest.approx(240 / 420)
    assert row["op_margin"] == pytest.approx(48 / 420)
    assert row["quarter_end"] == date(2026, 6, 30)


def test_quarter_yoy_when_fewer_than_eight_quarters_and_gap_stops_the_series():
    revenues = [150, 130, 120, 110, 100, float("nan")]
    row = peers.compute_fundamentals(info("X", 1e9), income(revenues))
    assert row["growth"] == pytest.approx(0.5) and row["growth_basis"] == "quarter_yoy"
    assert row["revenue_ttm"] == 510
    # A missing quarter breaks consecutiveness: fall back to provider TTM and growth.
    frame = income([150, 130, 120, 110, 100])
    frame = frame.drop(columns=frame.columns[1])
    row = peers.compute_fundamentals(info("X", 1e9, revenueGrowth=0.33), frame)
    assert row["revenue_basis"] == "provider_ttm" and row["revenue_ttm"] == 4e9
    assert row["growth"] == pytest.approx(0.33) and row["growth_basis"] == "quarter_yoy"
    assert row["gross_margin"] == 0.7 and row["op_margin"] == 0.1


def test_fcf_margin_requires_the_same_four_quarters():
    inc = income([100, 100, 100, 100])
    same = pd.DataFrame({"Free Cash Flow": [20, 20, 20, 20]}, index=quarters(4)).T
    shifted = pd.DataFrame({"Free Cash Flow": [20, 20, 20, 20]}, index=quarters(4, "2026-03-31")).T
    assert peers.compute_fundamentals(info("X", 1), inc, same)["fcf_margin"] == pytest.approx(0.2)
    assert peers.compute_fundamentals(info("X", 1), inc, shifted)["fcf_margin"] is None


def test_negative_ev_is_not_a_multiple():
    assert peers.compute_fundamentals(info("X", 1, enterpriseToRevenue=-2.0))["ev_sales"] is None


def test_returns_use_prior_close_within_a_week():
    series = [(date(2025, 9, 25), 50.0), (date(2026, 6, 24), 80.0), (date(2026, 9, 25), 100.0),
              (date(2026, 9, 26), 999.0)]
    result = peers.compute_returns({"X": series, "Y": [(date(2026, 9, 1), 10.0)]}, ASOF)
    assert result["X"]["ret_12m"] == pytest.approx(1.0)
    assert result["X"]["ret_3m"] == pytest.approx(0.25)
    assert result["Y"] == {"price_date": date(2026, 9, 1), "ret_3m": None, "ret_12m": None}


def _rows():
    def row(ticker, name, **values):
        base = {"ticker": ticker, "name": name, "market_cap": 100e9, "revenue_ttm": 4e9, "growth": 0.2,
                "growth_basis": "quarter_yoy", "gross_margin": 0.7, "op_margin": 0.1, "fcf_margin": 0.2,
                "ev_sales": 10.0, "forward_pe": 30.0, "ret_3m": 0.1, "ret_12m": 0.5,
                "quarter_end": date(2026, 6, 30)}
        base.update(values)
        return base
    return [row("TGT", "Target", growth=0.356, op_margin=0.007, ev_sales=22.3, ret_12m=0.98),
            row("AAA", "Alpha", growth=0.162, op_margin=0.13, ev_sales=7.6, forward_pe=-5.0),
            row("BBB", "Beta", growth=0.176, op_margin=0.254, ev_sales=6.9, fcf_margin=None,
                quarter_end=date(2026, 3, 31)),
            row("CCC", "Gamma", growth=0.24, op_margin=0.114, ev_sales=9.9, ret_3m=None)]


def test_render_table_sentences_and_flags():
    text, misaligned = peers.render_peer_markdown(_rows(), source="perplexity", asof=ASOF,
                                                   excluded=[{"ticker": "SML", "reason": "too_small"}])
    lines = text.splitlines()
    assert lines[0] == "#### 경쟁사 비교 분석"
    assert lines[2] == "| 구분 | TGT | AAA | BBB | CCC | 피어 중앙값 |"
    assert "| 매출 성장률(최근 분기 YoY,%) | 35.6 | 16.2 | 17.6 | 24.0 | 17.6 |" in lines
    assert "| Forward PER(배) | 30.0 | 적자 | 30.0 | 30.0 | 30.0 |" in lines
    assert "| FCF 마진(%) | 20.0 | 20.0 | - | 20.0 | 20.0 |" in lines
    assert "| 최근 분기 말 | 2026-06-30 | 2026-06-30 | 2026-03-31 | 2026-06-30 | - |" in lines
    assert "매출 성장률(최근 분기 YoY)은 35.6%로 피어 중앙값(17.6%)보다 18.0%p 높습니다." in text
    assert "영업이익률은 0.7%로 피어 중앙값(13.0%)보다 12.3%p 낮습니다." in text
    assert "EV/Sales는 22.3배로 피어 중앙값(7.6배) 대비 약 193% 할증된 수준입니다." in text
    assert "피어 중앙값(50.0%)을 48.0%p 상회했습니다." in text
    assert misaligned == ["BBB"] and "BBB의 최근 분기 말은 분석 대상과 60일 넘게" in text
    assert "SML" in text and "적자" in text and "Perplexity 후보 + yfinance 검증" in text
    assert "기준일 2026-09-25" in text and "N/A" not in text


def test_mixed_growth_basis_is_marked():
    rows = _rows()
    rows[1]["growth_basis"] = "ttm_yoy"
    text, _ = peers.render_peer_markdown(rows, source="industry", asof=ASOF)
    assert "| 매출 성장률(YoY,%) | 35.6* | 16.2 | 17.6* | 24.0* |" in text
    assert "yfinance 업종 상위" in text


def test_rendered_block_survives_us_report_cleaners():
    from cores.utils import clean_markdown
    from prism_core.report_presentation import humanize_report_status

    text, _ = peers.render_peer_markdown(_rows(), source="perplexity", asof=ASOF)
    for language in ("ko", "en"):
        assert text in humanize_report_status(clean_markdown("## 2. 펀더멘털 분석\n\n" + text + "\n"), language)


def _fake_fns(infos, candidates=None, fail_candidates=False, industry=()):
    async def candidates_fn(ticker, company, asof):
        if fail_candidates:
            raise ValueError("perplexity_http_500")
        return candidates or []

    def info_fn(ticker):
        return infos[ticker]

    def statements_fn(ticker):
        return income([100, 90, 80, 70, 60]), None

    def closes_fn(tickers, asof):
        return {t: [(date(2025, 9, 25), 10.0), (date(2026, 6, 26), 12.0), (date(2026, 9, 25), 15.0)] for t in tickers}

    return dict(candidates_fn=candidates_fn, info_fn=info_fn, statements_fn=statements_fn,
                industry_fn=lambda key: list(industry), closes_fn=closes_fn, today=ASOF)


def test_collect_uses_perplexity_candidates_then_yfinance_metrics():
    infos = {"TGT": info("TGT", 100e9), "AAA": info("AAA", 50e9), "BBB": info("BBB", 20e9),
             "OTC": info("OTC", 20e9, exchange="PNK")}
    candidates = [{"ticker": t, "name": t, "overlap": "same product"} for t in ("AAA", "OTC", "BBB")]
    packet = asyncio.run(peers.collect_us_peer_comparison("tgt", "Target", "20260925", **_fake_fns(infos, candidates)))
    assert packet["ready"] and packet["source"] == "perplexity"
    assert [p["ticker"] for p in packet["peers"]] == ["TGT", "AAA", "BBB"]
    assert packet["peers"][0]["growth"] == pytest.approx(100 / 60 - 1)
    assert packet["peers"][0]["ret_12m"] == pytest.approx(0.5)
    assert "same product" in packet["model_context"] and "same product" not in packet["public_markdown"]


def test_collect_falls_back_to_industry_leaders_when_proposer_fails():
    infos = {"TGT": info("TGT", 100e9), "BIG": info("BIG", 5000e9), "AAA": info("AAA", 50e9),
             "BBB": info("BBB", 20e9)}
    packet = asyncio.run(peers.collect_us_peer_comparison(
        "TGT", "Target", "20260925", **_fake_fns(infos, fail_candidates=True, industry=("TGT", "BIG", "AAA", "BBB"))))
    assert packet["ready"] and packet["source"] == "industry" and packet["proposer_error"] == "ValueError"
    assert [p["ticker"] for p in packet["peers"]] == ["TGT", "AAA", "BBB"]


def test_collect_skips_without_blocking():
    infos = {"TGT": info("TGT", 100e9)}
    packet = asyncio.run(peers.collect_us_peer_comparison("TGT", "T", "20260925", **_fake_fns(infos)))
    assert not packet["ready"] and packet["skip_reason"] == "insufficient_peers"
    old = asyncio.run(peers.collect_us_peer_comparison("TGT", "T", "20250101", **_fake_fns(infos)))
    assert old["skip_reason"] == "reference_date_not_current"
    assert asyncio.run(peers.collect_us_peer_comparison("bad ticker", "T", "20260925"))["skip_reason"] == "invalid_ticker"

    async def slow(*args):
        await asyncio.sleep(1)
        return []
    fns = _fake_fns(infos)
    fns["candidates_fn"] = slow
    packet = asyncio.run(peers.collect_us_peer_comparison("TGT", "T", "20260925", budget=0.05, **fns))
    assert packet["skip_reason"] == "timeout"


def test_english_rendering_for_en_reports():
    text, _ = peers.render_peer_markdown(_rows(), source="perplexity", asof=ASOF, language="en")
    assert text.startswith("#### Peer Comparison Analysis")
    assert "| Metric | TGT | AAA | BBB | CCC | Peer median |" in text
    assert "| Forward P/E (x) | 30.0 | Loss | 30.0 | 30.0 | 30.0 |" in text
    assert "Target's revenue growth (latest quarter YoY) is 35.6%, 18.0pp above the peer median (17.6%)." in text
    assert "EV/Sales of 22.3x is a 193% premium to the peer median (7.6x)." in text
    assert not any("\uac00" <= ch <= "\ud7a3" for ch in text)
