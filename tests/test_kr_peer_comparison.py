import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from prism_core.kr_peer_comparison import (
    _day,
    collect_peer_comparison,
    parse_wisereport,
    render_peer_comparison,
    select_report_peer_candidates,
)


def page(code="030200", name="KT", day="2026.09.22", scope="연결"):
    return f'''<table id="comInfo"><tr><td><span class="cd">{code}</span>
    <span class="nm_k">{name}</span></td></tr></table>
    <div><div class="header">시세 [기준:{day}]</div><div class="body">
    <table id="cTB11"><tr><td>가격</td></tr></table></div></div>
    <p>주주 기준:2099.01.01</p><div><table>
    <caption>기업 펀더멘털 실적, 컨센서스</caption><thead><tr>
    <th>주요지표</th><th>2025/12(A)</th><th>2026/12(E)</th></tr></thead><tbody>
    <tr><th>PER</th><td>7.73</td><td>9.23</td></tr>
    <tr><th>EPS</th><td>6,869원</td><td>5,750원</td></tr>
    <tr><th>PBR</th><td>N/A</td><td></td></tr>
    <tr><th>현금배당수익률</th><td>4.52%</td><td>4.56%</td></tr>
    <tr><th>회계기준</th><td colspan="2">{scope}</td></tr></tbody></table>
    <dl class="annotation"><li>지표 계산 시 주가 : 전 영업일 보통주 수정주가</li>
    <li>연결 기업의 당기순이익 및 자본총계는 지배주주 기준</li>
    <li>컨센서스 : 최근 3개월간 증권사에서 발표한 추정치의 평균</li></dl></div>'''


def parsed(**kwargs):
    return parse_wisereport(page(**kwargs), kwargs.get("code", "030200"),
                            kwargs.get("name", "KT"), "2026-09-23")


def test_column_period_unit_and_negative_eps_preserved():
    result = parsed()
    assert result["price_date"] == "2026-09-22"
    assert result["fundamentals_asof"] == ""
    assert result["facts"][0]["period"] == "2025/12(A)"
    assert result["facts"][1]["value"] == 9.23
    eps = next(f for f in result["facts"] if f["metric"] == "EPS")
    assert eps["value"] == 6869 and eps["unit"] == "원"
    assert not any(f["metric"] == "PBR" for f in result["facts"])


@pytest.mark.parametrize("change", [
    lambda s: s.replace('class="cd">030200', 'class="cd">000001'),
    lambda s: s.replace('class="nm_k">KT', 'class="nm_k">Other'),
    lambda s: s.replace("2026.09.22", "2026.09.24"),
    lambda s: s.replace("2026/12(E)", "미상"),
    lambda s: s.replace('<td>9.23</td>', ''),
])
def test_identity_future_date_and_broken_table_rejected(change):
    with pytest.raises(ValueError):
        parse_wisereport(change(page()), "030200", "KT", "2026-09-23")


def test_missing_quote_date_not_borrowed_from_shareholder_date():
    result = parse_wisereport(page().replace("시세 [기준:2026.09.22]", "시세"),
                              "030200", "KT", "2026-09-23")
    assert not result["price_date"]


def test_same_basis_comparison_but_no_business_leader_signal():
    out = render_peer_comparison([parsed(), parsed(code="032640", name="LG유플러스")])
    assert "| PER | 2025/12(A) · 연결 | 7.73 | 7.73 | 배 |" in out
    assert "전체 업종 평균이나 순위는 아닙니다" in out
    assert "자동 매수 조건 충족을 판단하지 않습니다" in out
    assert "UNKNOWN" not in out


@pytest.mark.parametrize("kwargs", [{"day": "2026.09.21"}, {"scope": "별도"}])
def test_mismatched_date_or_scope_no_comparison(kwargs):
    out = render_peer_comparison([parsed(), parsed(code="032640", name="LG유플러스", **kwargs)])
    assert "직접적인 배수 비교를 하지 않았습니다" in out
    assert "| 7.73 | 7.73 |" not in out
    assert "KT에서 확인한 지표" in out


def test_collector_bounded_fixed_urls_and_partial_failure():
    calls = []
    today = datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()

    async def transport(server, tool, args):
        calls.append(args)
        code = args["url"].split("=")[-1]
        if code == "032640":
            raise RuntimeError("must not leak sensitive provider diagnostics")
        name = {"030200": "KT", "017670": "SK텔레콤"}[code]
        return {"data": {"html": page(code, name, today.replace("-", "."))}}

    candidate = lambda ticker, name: {"ticker": ticker, "name": name,
                                     "rationale": "사업이 중첩됨", "source": "공식 보고서"}
    result = asyncio.run(collect_peer_comparison("030200", "KT", [
        candidate("https://localhost", "Bad"), candidate("030200", "KT"),
        candidate("017670", "SK텔레콤"), candidate("032640", "LG유플러스"),
        candidate("005930", "추가기업"),
    ], today, transport=transport))
    assert len(calls) == 3
    assert all(c["url"].startswith("https://comp.wisereport.co.kr/company/") for c in calls)
    assert result["private_receipt"]["received"] == 2
    assert "sensitive" not in str(result)
    assert "SK텔레콤" in result["public_markdown"]
    assert "LG유플러스의 비교 지표는 이번 조회에서 확보하지 못해" in result["public_markdown"]


def test_historical_snapshot_never_fetches_current_consensus():
    async def forbidden(*args):
        raise AssertionError("must not call")

    result = asyncio.run(collect_peer_comparison("030200", "KT", [], "2000-01-01",
                                              transport=forbidden))
    assert result["public_markdown"] == ""


def test_negative_eps_preserved_without_positive_per():
    result = parse_wisereport(page().replace("6,869원", "-6,869원"),
                              "030200", "KT", "2026-09-23")
    assert any(f["metric"] == "EPS" and f["value"] == -6869 for f in result["facts"])
    assert not any(f["metric"] == "PER" and f["period"] == "2025/12(A)" for f in result["facts"])


def test_explicit_peer_candidates_only_exact_unique_names():
    mapping = {"017670": "SK텔레콤", "030200": "KT", "032640": "LG유플러스", "033780": "KT&G"}
    reports = {"news": "Mention KT&G\n#### Competitive Evidence\n"
               "**peer_universe:** SK텔레콤·KT·LG유플러스(032640) / **source:** https://example.com/report\n"
               "#### Other\n**peer_universe:** KT&G / **source:** https://example.com/other"}
    result = select_report_peer_candidates(reports, "017670", mapping)
    assert [r["ticker"] for r in result] == ["030200", "032640"]
    assert all("독립적 경쟁관계 검증 아님" in r["rationale"] for r in result)


def test_ambiguous_mismatched_or_unattributed_proposals_rejected():
    mapping = {"030200": "KT", "000001": "KT", "032640": "LG유플러스"}
    report = "#### Competitive Evidence\n**peer_universe:** KT·LG유플러스(030200) / **source:** https://example.com/a"
    assert select_report_peer_candidates([report], "017670", mapping) == []
    assert select_report_peer_candidates([report.replace("https://example.com/a", "UNKNOWN")],
                                         "017670", mapping) == []


def test_compact_day_supported_explicitly():
    assert _day("20260923").isoformat() == "2026-09-23"
    with pytest.raises(ValueError):
        _day("20260230")


def test_fences_excluded_bold_heading_supported():
    record = "#### **Competitive Evidence**\n**peer_universe:** KT / **source:** https://example.com/a"
    mapping = {"030200": "KT"}
    assert len(select_report_peer_candidates([record], "017670", mapping)) == 1
    assert select_report_peer_candidates(["```md\n" + record + "\n```"], "017670", mapping) == []
    assert select_report_peer_candidates(["```md\n" + record], "017670", mapping) == []


@pytest.mark.parametrize("source", ["https://user:pass@example.com/a", "https://127.0.0.1/a",
                                    "https://host.internal/a", "https://host.local/a"])
def test_nonpublic_or_credentialed_proposal_sources_rejected(source):
    record = f"#### Competitive Evidence\n**peer_universe:** KT / **source:** {source}"
    assert select_report_peer_candidates([record], "017670", {"030200": "KT"}) == []


@pytest.mark.parametrize("period", ["2099/99(A)", "2025/00(A)", "2026/12(A)", "2099/13(E)"])
def test_invalid_calendar_or_future_actual_period_rejected(period):
    with pytest.raises(ValueError):
        parse_wisereport(page().replace("2025/12(A)", period), "030200", "KT", "20260923")


def test_valid_future_estimate_period_allowed():
    result = parse_wisereport(page().replace("2026/12(E)", "2027/12(E)"),
                              "030200", "KT", "20260923")
    assert any(f["period"] == "2027/12(E)" for f in result["facts"])


@pytest.mark.parametrize("day", ["2026.09.16", "2026.08.22"])
def test_equally_stale_quotes_not_current_valuation_comparison(day):
    first, second = parsed(day=day), parsed(code="032640", name="LG유플러스", day=day)
    assert first["price_is_recent"] is False
    out = render_peer_comparison([first, second])
    assert "직접적인 배수 비교를 하지 않았습니다" in out
    assert "| 7.73 | 7.73 |" not in out


def test_render_retains_large_integer_and_decimal_provider_precision():
    html = page().replace("6,869원", "1,234,567원").replace("9.23", "9.23456789")
    first = parse_wisereport(html, "030200", "KT", "20260923")
    second = parse_wisereport(html.replace("030200", "032640").replace(">KT<", ">LG유플러스<"),
                              "032640", "LG유플러스", "20260923")
    comparison = render_peer_comparison([first, second])
    assert "| 1,234,567 | 1,234,567 | 원 |" in comparison
    assert "| 9.23456789 | 9.23456789 | 배 |" in comparison
    standalone = render_peer_comparison([first])
    assert "1,234,567원" in standalone
    assert "9.23456789배" in standalone
    assert "e+06" not in comparison + standalone
