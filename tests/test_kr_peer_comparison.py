import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from prism_core.kr_peer_comparison import (
    PEER_LIST_URL,
    PEER_TABLE_URL,
    build_peers,
    collect_wisereport_peers,
    parse_peer_header,
    parse_peer_table,
    render_peer_markdown,
    select_comparable_peers,
)

FIXTURES = Path(__file__).parent / "fixtures" / "wisereport"
HEADER = (FIXTURES / "cF6001_252990.json").read_text(encoding="utf-8")
TABLE = (FIXTURES / "cF6002_252990.html").read_text(encoding="utf-8")
TODAY = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d")


def peers():
    companies = parse_peer_header(HEADER)
    return build_peers(companies, parse_peer_table(TABLE, len(companies)))


def collect(header=HEADER, table=TABLE, code="252990", date=TODAY):
    async def fetch(url):
        return header if "cF6001" in url else table
    return asyncio.run(collect_wisereport_peers(code, "샘씨엔에스", date, fetch=fetch))


def test_parse_joins_columns_in_seq_order():
    result = peers()
    assert [p["code"] for p in result] == ["252990", "058470", "166090", "101160", "036810"]
    assert [p["name"] for p in result][1:] == ["리노공업", "하나머티리얼즈", "월덱스", "에프에스티"]
    assert result[1]["op_margin"] == 47.51 and result[1]["per"] == 30.24
    assert result[0]["market_cap"] == 10797.1 and result[0]["period"] == "2025/12"
    assert result[4]["per"] is None  # provider N/A
    assert [p["basis"] for p in result] == ["별도", "별도", "연결", "연결", "연결"]


def test_seq_order_not_payload_order():
    data = json.loads(HEADER)
    data["oDt_header"].reverse()
    assert [c["code"] for c in parse_peer_header(data)][0] == "252990"


def test_market_cap_mismatch_rejects_whole_packet():
    tampered = TABLE.replace("56,625.4", "11,609.5", 1)
    result = collect(table=tampered)
    assert result["ready"] is False and result["skip_reason"] == "market_cap_mismatch"
    assert result["public_markdown"] == "" and result["model_context"] == ""


def test_swapped_header_order_rejected():
    data = json.loads(HEADER)
    rows = data["oDt_header"]
    rows[1]["SEQ"], rows[2]["SEQ"] = rows[2]["SEQ"], rows[1]["SEQ"]
    assert collect(header=json.dumps(data))["skip_reason"] == "market_cap_mismatch"


def test_column_count_mismatch_rejected():
    data = json.loads(HEADER)
    data["oDt_header"].pop()
    assert collect(header=json.dumps(data))["skip_reason"] == "column_count"


@pytest.mark.parametrize("header,table,code,date,reason", [
    ("not json", TABLE, "252990", TODAY, "peer_list_json"),
    (HEADER, "<html></html>", "252990", TODAY, "table_missing"),
    (HEADER, TABLE, "017670", TODAY, "target_mismatch"),
    (HEADER, TABLE, "252990", "20200101", "reference_date_not_today"),
    (HEADER, TABLE, "25299", TODAY, "invalid_code"),
])
def test_failures_skip_with_reason(header, table, code, date, reason):
    result = collect(header=header, table=table, code=code, date=date)
    assert result["ready"] is False and result["skip_reason"] == reason


def test_company_shell_page_has_no_peer_table():
    shell = (FIXTURES / "c106_252990.html").read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="table_missing"):
        parse_peer_table(shell, 5)


def test_network_error_is_skip_not_exception():
    async def fetch(url):
        raise asyncio.TimeoutError()
    result = asyncio.run(collect_wisereport_peers("252990", "샘씨엔에스", TODAY, fetch=fetch))
    assert result["ready"] is False and result["skip_reason"] == "timeout"


def test_fixed_urls_use_annual_curated_set():
    assert "sec_cd=FG000" in PEER_LIST_URL and "frq=Y" in PEER_LIST_URL
    assert "sec_cd=FG000" in PEER_TABLE_URL and "frq=Y" in PEER_TABLE_URL


def test_render_table_median_and_sentences():
    out = render_peer_markdown(peers())
    assert out.startswith("#### 경쟁사 비교 분석\n")
    assert "| 구분 | 샘씨엔에스 | 리노공업 | 하나머티리얼즈 | 월덱스 | 에프에스티 | 피어 중앙값 |" in out
    assert "| 영업이익률(%) | 18.8 | 47.5 | 18.3 | 20.1 | -0.2 | 19.2 |" in out
    # Loss-making PER is not a number and is excluded from the peer median.
    assert "| PER(배) | 26.09 | 30.24 | 22.21 | 8.68 | 적자 | 22.21 |" in out
    assert "| 재무기준 | 별도 | 별도 | 연결 | 연결 | 연결 | - |" in out
    assert "영업이익률은 18.8%로 비교기업 중앙값(19.2%)보다 0.4%p 낮습니다." in out
    assert "ROE는 9.7%로 비교기업 중앙값(11.0%)보다 1.3%p 낮습니다." in out
    assert "| ROE(%) | 9.7 | 22.5 | 9.4 | 12.5 | -5.1 | 11.0 |" in out
    assert "PER은 26.09배로 비교기업 중앙값(22.21배) 대비 약 17% 할증된 수준입니다." in out
    assert "매출액 기준으로는 비교 대상 5개사 중 5위입니다." in out
    assert "재무 기준 2025/12 연간 실적" in out and "전일종가" in out and "WiseFn 선정 비교기업" in out
    assert "연결과 별도 재무기준이 섞여" in out


def test_half_up_rounding_matches_provider_text():
    out = render_peer_markdown(peers())
    # 22.45 / -5.05 must not be binary-float rounded to 22.4 / -5.0.
    assert "| ROE(%) | 9.7 | 22.5 | 9.4 | 12.5 | -5.1 |" in out


def test_negative_target_per_falls_back_to_pbr_sentence():
    items = peers()
    items[0]["per"], items[0]["net_income_controlling"] = -3.0, -10.0
    out = render_peer_markdown(items)
    assert "| PER(배) | 적자 |" in out
    assert "PER은" not in out and "PBR은 2.35배로 비교기업 중앙값(2.21배) 대비" in out


def test_uniform_basis_has_no_mixed_note_and_no_loss_note():
    items = peers()[:4]
    for item in items:
        item["basis"] = "연결"
    out = render_peer_markdown(items)
    assert "섞여" not in out and "적자" not in out
    assert "비교기업 3개사 기준" in out


def test_public_text_has_no_internal_tokens():
    result = collect()
    assert result["ready"] is True and result["skip_reason"] is None
    assert result["period"] == "2025/12" and result["price_basis"] == "전일종가"
    public = result["public_markdown"]
    for token in ("N/A", "UNKNOWN", "IFRS", "FIN_GUBUN", "MKT_VAL", "CMP_CD", "ready", "skipped",
                  "EPS", "BPS", "DPS", "http"):
        assert token not in public
    assert not re.search(r"\b[a-z_]{4,}\b", public)
    assert "058470" in result["model_context"] and "058470" not in public


def test_rendered_block_survives_report_publication_formatting():
    from cores.utils import clean_markdown
    from prism_core.report_presentation import humanize_report_status
    out = render_peer_markdown(peers())
    published = humanize_report_status(clean_markdown(out), "ko")
    assert published.startswith("#### 경쟁사 비교 분석\n")
    for line in out.splitlines():
        if line.strip():
            assert line in published


SKT_HEADER = (FIXTURES / "cF6001_017670.json").read_text(encoding="utf-8")
SKT_TABLE = (FIXTURES / "cF6002_017670.html").read_text(encoding="utf-8")


def test_size_filter_drops_tiny_peers_from_table_and_median():
    async def fetch(url):
        return SKT_HEADER if "cF6001" in url else SKT_TABLE
    result = asyncio.run(collect_wisereport_peers("017670", "SK텔레콤", TODAY, fetch=fetch))
    assert result["ready"] is True
    assert [p["code"] for p in result["peers"]] == ["017670", "030200", "032640"]
    assert [p["name"] for p in result["excluded_peers"]] == ["와이어블", "프리티"]
    out = result["public_markdown"]
    assert "| 구분 | SK텔레콤 | KT | LG유플러스 | 피어 중앙값 |" in out
    assert "와이어블 |" not in out and "| 적자 |" not in out
    # Median of KT/LGU+ only: PER (7.66 + 12.19) / 2.
    assert "| PER(배) | 28.14 | 7.66 | 12.19 | 9.93 |" in out
    assert ("시가총액이 분석 대상의 10% 미만인 와이어블·프리티는 규모 차이가 커서 "
            "비교표와 중앙값에서 제외했습니다.") in out
    assert "비교기업 2개사 기준" in out and "비교 대상 3개사 중 2위" in out
    assert "065530" not in result["model_context"] and "030200" in result["model_context"]


def test_size_filter_keeps_all_comparable_peers():
    kept, excluded = select_comparable_peers(peers())
    assert len(kept) == 5 and excluded == []
    assert "10% 미만" not in collect()["public_markdown"]


def test_size_filter_keeps_two_largest_when_all_peers_are_tiny():
    items = peers()
    items[0]["market_cap"] = 10_000_000.0
    kept, excluded = select_comparable_peers(items)
    assert [p["code"] for p in kept] == ["252990", "058470", "166090"]  # SEQ order kept
    assert [p["code"] for p in excluded] == ["101160", "036810"]
    out = render_peer_markdown(kept, excluded)
    assert "월덱스·에프에스티는 규모 차이가 커서" in out


def test_size_filter_keeps_one_passing_peer_plus_next_largest():
    items = peers()
    items[0]["market_cap"] = 200_000.0  # only 리노공업 (56,625) passes the 10% floor
    kept, _ = select_comparable_peers(items)
    assert [p["code"] for p in kept] == ["252990", "058470", "166090"]


def test_size_filter_runs_after_full_packet_validation():
    # A mismatch in a column that would be filtered out still rejects the packet.
    async def fetch(url):
        return SKT_HEADER if "cF6001" in url else SKT_TABLE.replace("240.5", "999.9", 1)
    result = asyncio.run(collect_wisereport_peers("017670", "SK텔레콤", TODAY, fetch=fetch))
    assert result["ready"] is False and result["skip_reason"] == "market_cap_mismatch"


def test_fetch_text_waits_before_single_retry(monkeypatch):
    import aiohttp
    from prism_core import kr_peer_comparison as peer

    calls, sleeps = [], []

    class Response:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def read(self):
            return "ok".encode()

    class Session:
        def get(self, url):
            calls.append(url)
            if len(calls) == 1:
                raise aiohttp.ClientConnectionError("reset")
            return Response()

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(peer.asyncio, "sleep", fake_sleep)
    assert asyncio.run(peer._fetch_text(Session(), "https://example.test")) == "ok"
    assert len(calls) == 2 and sleeps == [0.5]
