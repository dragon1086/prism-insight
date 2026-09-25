from prism_core.competitive_evidence import attach_competitive_evidence
from prism_core.kr_report_context import strip_public_competitive_evidence

RECORD = (
    "#### Competitive Evidence\n\n"
    "| field | type | entity | peer_universe | metric·value·period | source | publication_date | status |\n"
    "|---|---|---|---|---|---|---|---|\n"
    "| 산업 수요 순풍 | sector_tailwind | 샘씨엔에스 | 티에스이·월덱스 | STF 수요 | https://kind.krx.co.kr/x | 2026-03-16 | SEARCH_ONLY |\n"
    "| 주가 선도 | price_leadership | 샘씨엔에스 | 미확인 | +40.93% | UNKNOWN | 2026-09-23 | NOT_FOUND |\n\n"
)
CONCLUSION = "종합하면 샘씨엔에스는 세라믹 부품 특화가 강점입니다. 다만 변동성 관리가 중요한 구간입니다."
NEWS = "### 3. 최근 주요 뉴스 요약\n\n뉴스 본문입니다.\n\n" + RECORD + CONCLUSION + "\n\n#### 다음 확인 사항\n\n실적 발표입니다.\n"
FORBIDDEN = ("Competitive Evidence", "SEARCH_ONLY", "NOT_FOUND", "sector_tailwind", "peer_universe",
             "Evidence ID", "| field |")


def attached():
    reports, receipt = attach_competitive_evidence(
        {"news_analysis": NEWS, "company_overview": "### 2-2. 기업 개요 분석\n\n개요 본문입니다.\n"},
        "KR", "252990", "20260924", "ko")
    assert receipt["status"] == "COPIED_NOT_VALIDATED"
    return reports


def test_news_record_removed_and_trailing_conclusion_kept():
    public = strip_public_competitive_evidence(attached())
    news = public["news_analysis"]
    for token in FORBIDDEN:
        assert token not in news
    assert "뉴스 본문입니다.\n\n" + CONCLUSION + "\n\n#### 다음 확인 사항" in news
    assert news.endswith("실적 발표입니다.\n")


def test_overview_handoff_removed_without_duplicating_news_prose():
    public = strip_public_competitive_evidence(attached())
    overview = public["company_overview"]
    for token in FORBIDDEN + ("근거 기록을 그대로 재사용",):
        assert token not in overview
    assert CONCLUSION not in overview
    assert overview.strip() == "### 2-2. 기업 개요 분석\n\n개요 본문입니다."


def test_handoff_status_without_record_removed():
    reports, receipt = attach_competitive_evidence(
        {"news_analysis": "뉴스", "company_overview": "개요"}, "KR", "252990", "20260924", "ko")
    assert receipt["status"] == "RECORD_ABSENT"
    public = strip_public_competitive_evidence(reports)
    assert public["company_overview"].strip() == "개요" and "Handoff" not in public["company_overview"]


def test_bold_heading_and_list_records_removed():
    news = ("앞\n\n### **Competitive Evidence**\n- **field:** 수요 / **status:** SEARCH_ONLY / **source:** UNKNOWN\n"
            "- field: 점유율, type: business_competitive_position, status: NOT_FOUND\n\n### 다음\n뒤\n")
    out = strip_public_competitive_evidence({"news_analysis": news})["news_analysis"]
    assert "SEARCH_ONLY" not in out and "NOT_FOUND" not in out and "Competitive" not in out
    assert out.startswith("앞\n\n### 다음\n뒤")


def test_fenced_example_and_other_sections_untouched():
    fenced = "예시\n```markdown\n#### Competitive Evidence\n| field |\n```\n"
    reports = {"news_analysis": fenced, "investment_strategy": "전략 본문", "peer_comparison": "#### 경쟁사 비교 분석"}
    assert strip_public_competitive_evidence(reports) == reports


def test_input_mapping_is_not_mutated():
    reports = attached()
    snapshot = dict(reports)
    strip_public_competitive_evidence(reports)
    assert reports == snapshot
