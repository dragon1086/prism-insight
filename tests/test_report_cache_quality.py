import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import report_generator


KST = ZoneInfo("Asia/Seoul")


def _configure_cache_dirs(monkeypatch, tmp_path: Path) -> tuple[Path, Path]:
    reports = tmp_path / "reports"
    pdf_reports = tmp_path / "pdf_reports"
    reports.mkdir()
    pdf_reports.mkdir()
    monkeypatch.setattr(report_generator, "REPORTS_DIR", reports)
    monkeypatch.setattr(report_generator, "PDF_REPORTS_DIR", pdf_reports)
    return reports, pdf_reports


def _configure_us_cache_dirs(monkeypatch, tmp_path: Path) -> tuple[Path, Path]:
    reports = tmp_path / "us_reports"
    pdf_reports = tmp_path / "us_pdf_reports"
    reports.mkdir()
    pdf_reports.mkdir()
    monkeypatch.setattr(report_generator, "US_REPORTS_DIR", reports)
    monkeypatch.setattr(report_generator, "US_PDF_REPORTS_DIR", pdf_reports)
    return reports, pdf_reports


@pytest.mark.parametrize("market", ["kr", "us"])
def test_cache_expires_at_kst_calendar_day_boundary(monkeypatch, tmp_path, market):
    if market == "kr":
        reports, pdf_reports = _configure_cache_dirs(monkeypatch, tmp_path)
        markdown_path = reports / "035720_카카오_20260806_analysis.md"
        pdf_path = pdf_reports / "035720_카카오_20260806_analysis.pdf"
        get_cached = lambda: report_generator.get_cached_report("035720")
    else:
        reports, pdf_reports = _configure_us_cache_dirs(monkeypatch, tmp_path)
        markdown_path = reports / "IONQ_IonQ_20260806_analysis.md"
        pdf_path = pdf_reports / "IONQ_IonQ_20260806_analysis.pdf"
        get_cached = lambda: report_generator.get_cached_us_report("IONQ")

    markdown_path.write_text("# 정상 분석 보고서", encoding="utf-8")
    pdf_path.write_bytes(b"pdf")
    created_at = datetime(2026, 8, 6, 23, 55, tzinfo=KST)
    os.utime(markdown_path, (created_at.timestamp(), created_at.timestamp()))
    monkeypatch.setattr(
        report_generator,
        "_now_kst",
        lambda: datetime(2026, 8, 7, 0, 5, tzinfo=KST),
    )

    assert get_cached() == (False, "", None, None)


@pytest.mark.parametrize("market", ["kr", "us"])
def test_cache_remains_valid_through_same_kst_calendar_day(
    monkeypatch, tmp_path, market
):
    if market == "kr":
        reports, pdf_reports = _configure_cache_dirs(monkeypatch, tmp_path)
        markdown_path = reports / "035720_카카오_20260807_analysis.md"
        pdf_path = pdf_reports / "035720_카카오_20260807_analysis.pdf"
        get_cached = lambda: report_generator.get_cached_report("035720")
    else:
        reports, pdf_reports = _configure_us_cache_dirs(monkeypatch, tmp_path)
        markdown_path = reports / "IONQ_IonQ_20260807_analysis.md"
        pdf_path = pdf_reports / "IONQ_IonQ_20260807_analysis.pdf"
        get_cached = lambda: report_generator.get_cached_us_report("IONQ")

    markdown_path.write_text("# 정상 분석 보고서", encoding="utf-8")
    pdf_path.write_bytes(b"pdf")
    created_at = datetime(2026, 8, 7, 0, 1, tzinfo=KST)
    os.utime(markdown_path, (created_at.timestamp(), created_at.timestamp()))
    monkeypatch.setattr(
        report_generator,
        "_now_kst",
        lambda: datetime(2026, 8, 7, 23, 59, tzinfo=KST),
    )

    is_cached, content, cached_markdown, cached_pdf = get_cached()

    assert is_cached is True
    assert content == "# 정상 분석 보고서"
    assert cached_markdown == markdown_path
    assert cached_pdf == pdf_path


def test_failed_report_is_not_reused_as_cache(monkeypatch, tmp_path):
    reports, pdf_reports = _configure_cache_dirs(monkeypatch, tmp_path)
    (reports / "035720_카카오_20260806_analysis.md").write_text(
        "# 카카오 분석 보고서\n"
        "Analysis failed: price_volume_analysis\n"
        "Analysis failed: company_status\n"
        "Analysis failed: news_analysis",
        encoding="utf-8",
    )
    (pdf_reports / "035720_카카오_20260806_analysis.pdf").write_bytes(b"bad")

    assert report_generator.get_cached_report("035720") == (
        False,
        "",
        None,
        None,
    )


def test_successful_report_remains_cacheable(monkeypatch, tmp_path):
    reports, pdf_reports = _configure_cache_dirs(monkeypatch, tmp_path)
    markdown_path = reports / "035720_카카오_20260806_analysis.md"
    pdf_path = pdf_reports / "035720_카카오_20260806_analysis.pdf"
    markdown_path.write_text("# 카카오 분석 보고서\n\n정상 분석 내용", encoding="utf-8")
    pdf_path.write_bytes(b"pdf")

    is_cached, content, cached_markdown, cached_pdf = (
        report_generator.get_cached_report("035720")
    )

    assert is_cached is True
    assert content == "# 카카오 분석 보고서\n\n정상 분석 내용"
    assert cached_markdown == markdown_path
    assert cached_pdf == pdf_path
