import asyncio

import pytest

import report_generator as report

START = "<!-- DART_DEEP_ANALYSIS_START -->"
END = "<!-- DART_DEEP_ANALYSIS_END -->"


def test_middle_dart_chapter_survives_existing_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "_EVALUATION_REPORT_MAX_CHARS", 40000)
    chapter = START + "\n# 5. 공시 심층분석\n\n| 약정 | 금액 |\n|---|---|\n| 조건 충족 시 출자 | 987.65억원 |\n\n조건 미충족 시 의무가 발생하지 않습니다.\n" + END
    paragraph = "일반 분석입니다. " * 100
    content = (paragraph + "\n\n") * 40 + chapter + ("\n\n" + paragraph) * 40
    path = tmp_path / "017670_report.md"
    path.write_text(content)
    result = report._evaluation_report_context(path)
    assert len(result) <= 40000
    assert chapter in result
    assert result.count(paragraph) > 0
    assert result.replace(paragraph, "").count("일반 분석입니다.") == 0


def test_short_and_unmarked_reports_keep_existing_behavior(tmp_path):
    path = tmp_path / "report.md"
    path.write_text("짧은 보고서")
    assert report._evaluation_report_context(path) == "짧은 보고서"
    path.write_text("a" * 100000)
    result = report._evaluation_report_context(path)
    assert "[중간 상세 내용 생략]" in result


@pytest.mark.parametrize("body", [START + "x" * 41000 + END, START + "incomplete", END + START])
def test_unsafe_chapter_stops_cache_lookup_and_model_calls(tmp_path, monkeypatch, body):
    monkeypatch.setattr(report, "_EVALUATION_REPORT_MAX_CHARS", 40000)
    monkeypatch.setattr(report, "REPORTS_DIR", tmp_path)
    path = tmp_path / "017670_report.md"
    path.write_text(body)
    with pytest.raises(report.EvaluationReportContextError):
        report.get_recent_evaluation_report("017670")
    calls = []

    async def forbidden(**kwargs):
        calls.append(kwargs)
        raise AssertionError("must not assess without protected disclosure")

    monkeypatch.setattr(report, "_generate_telegram_text", forbidden)
    monkeypatch.setattr(report, "_generate_evaluation_fallback", forbidden)
    result = asyncio.run(report.generate_evaluation_response(
        "017670", "합성", 100, 6, "간결", "", report_path=path))
    assert "평가를 중단" in result
    assert not calls
