from pathlib import Path

import report_generator


def _configure_cache_dirs(monkeypatch, tmp_path: Path) -> tuple[Path, Path]:
    reports = tmp_path / "reports"
    pdf_reports = tmp_path / "pdf_reports"
    reports.mkdir()
    pdf_reports.mkdir()
    monkeypatch.setattr(report_generator, "REPORTS_DIR", reports)
    monkeypatch.setattr(report_generator, "PDF_REPORTS_DIR", pdf_reports)
    return reports, pdf_reports


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
