"""Quarterly EPS facts collected by code (2026-09-30, 018880 afternoon report).

The company-status writer scraped different pages per run: the morning report
quoted the prior-year quarter's EPS change, the afternoon one said it was
unconfirmed. The same cF1001 table's quarterly view carries the prior-year
quarter and quarter-end share counts, so the comparison is computed here.
"""
import asyncio
import math
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from cores.agents.company_info_agents import create_company_status_agent
from prism_core import kr_financial_summary as fs

FIXTURES = Path(__file__).parent / "fixtures" / "wisereport"
QUARTERLY = (FIXTURES / "cF1001_Q_018880.html").read_text(encoding="utf-8")
PAGE = ("<script>$.ajax({url: 'ajax/cF1001.aspx', data: {cmp_cd: '018880', "
        "encparam: 'TG9BUEY0MlNUeHI4aDA2dWhhZERpZz09', id: 'TDdCYnFkT0'}});</script>")
URLS = {"기업현황": "https://example.invalid/status", "투자지표": "https://example.invalid/ind"}


def _today():
    return datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d")


@pytest.fixture
def quarterly():
    return fs.parse_financial_summary(QUARTERLY)


def test_quarterly_view_parses_prior_year_quarter_and_shares(quarterly):
    assert list(quarterly.index[:5]) == ["2025/06", "2025/09", "2025/12", "2026/03", "2026/06"]
    assert quarterly["estimate"].tolist() == [False] * 5 + [True] * 3
    assert quarterly["eps"].tolist()[:5] == [-21, 71, -274, 65, 84]
    assert quarterly.loc["2025/06", "shares"] == 772_041_577
    assert quarterly.loc["2026/06", "shares"] == 1_026_262_552
    # The decoy table's repeated values never leak into the parsed frame.
    assert 9395 not in quarterly.to_numpy()


def test_facts_state_turnaround_and_share_change(quarterly):
    text = fs.render_quarterly_facts(quarterly, "ko")
    assert "최근 확정 분기 2026/06 vs 전년 동기 2025/06" in text
    assert "EPS -21원 → 84원 (흑자전환)" in text
    assert "지배순이익 -164억원 → 865억원 (흑자전환)" in text
    assert "772,041,577주 → 1,026,262,552주 (+32.9%)" in text
    # A loss base never becomes a growth rate such as the provider's +497%.
    assert "497" not in text
    # Consensus quarters are excluded and the vendor is not named.
    assert "2026/09" not in text
    assert "wise" not in text.lower()


def test_english_facts(quarterly):
    text = fs.render_quarterly_facts(quarterly, "en")
    assert "2026/06 vs prior-year 2025/06" in text
    assert "turned to profit" in text
    assert "+32.9%" in text


@pytest.mark.parametrize("prior,current,label", [
    (100, 150, "+50.0%"),
    (-21, 84, "흑자전환"),
    (0, 10, "흑자전환"),
    (50, -5, "적자전환"),
    (-5, -9, "적자 지속"),
    (0, 0, "증감률 산출 불가(기준값 0)"),
    (math.nan, 10, "UNKNOWN"),
])
def test_change_labels(prior, current, label):
    assert fs._change(prior, current, "ko") == label


def test_missing_prior_year_quarter_is_unknown(quarterly):
    text = fs.render_quarterly_facts(quarterly.drop(index="2025/06"), "ko")
    assert "전년 동기(2025/06) 행이 표에 없어 전년 동기 비교는 UNKNOWN" in text
    assert "흑자전환" not in text


def test_unchanged_and_missing_share_counts(quarterly):
    same = quarterly.copy()
    same.loc["2025/06", "shares"] = same.loc["2026/06", "shares"]
    assert "두 분기 말 발행주식수가 1,026,262,552주로 같습니다" in fs.render_quarterly_facts(same, "ko")
    assert "EPS 분모 변화는 UNKNOWN" in fs.render_quarterly_facts(
        quarterly.drop(columns=["shares"]), "ko")


def test_no_reported_quarter_renders_nothing(quarterly):
    assert fs.render_quarterly_facts(None, "ko") == ""
    assert fs.render_quarterly_facts(quarterly[quarterly["estimate"]], "ko") == ""


def test_quarterly_collector_requests_quarterly_view():
    seen = []

    async def fetch(url, referer):
        seen.append(url)
        return PAGE if referer is None else QUARTERLY

    frame = asyncio.run(fs.collect_wisereport_quarterly_summary("018880", _today(), fetch=fetch))
    assert frame is not None and frame.attrs["ticker"] == "018880"
    assert "freq_typ=Q" in seen[1] and "freq_typ=Y" not in seen[1]
    assert frame.attrs["source"] == "Quarterly financial summary"


def test_quarterly_collector_failure_is_optional():
    async def fetch(url, referer):
        raise OSError("connection reset")

    assert asyncio.run(fs.collect_wisereport_quarterly_summary("018880", _today(), fetch=fetch)) is None


def test_company_status_agent_uses_facts_only_when_collected(quarterly):
    facts = fs.render_quarterly_facts(quarterly, "ko")
    with_facts = create_company_status_agent("한온시스템", "018880", "20260930", URLS, "ko",
                                             quarterly_facts=facts)
    without = create_company_status_agent("한온시스템", "018880", "20260930", URLS, "ko")
    assert "## 사전 수집된 데이터 (분기 실적)" in with_facts.instruction
    assert facts in with_facts.instruction
    assert "사전 수집된 데이터 (분기 실적)" not in without.instruction
    assert with_facts.instruction.startswith(without.instruction)
