"""Never pair a successful report with an obsolete diagnostic PDF."""
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import report_generator as generator


@pytest.fixture(params=["kr", "us"])
def cache(request, monkeypatch, tmp_path):
    reports, pdfs = tmp_path / "reports", tmp_path / "pdfs"
    reports.mkdir()
    pdfs.mkdir()
    prefix = "US_" if request.param == "us" else ""
    monkeypatch.setattr(generator, prefix + "REPORTS_DIR", reports)
    monkeypatch.setattr(generator, prefix + "PDF_REPORTS_DIR", pdfs)
    monkeypatch.setattr(generator, "_is_current_kst_day", lambda _: True)
    md = reports / "000660_SK_Hynix_20260914_analysis.md"
    md.write_text("# 정상 분석 보고서", encoding="utf-8")
    pdf = pdfs / md.with_suffix(".pdf").name
    reader = generator.get_cached_us_report if prefix else generator.get_cached_report
    saver = generator.save_us_pdf_report if prefix else generator.save_pdf_report
    return md, pdf, lambda: reader("000660"), lambda: saver("000660", "SK_Hynix", md)


def converter(monkeypatch, callback):
    monkeypatch.setitem(sys.modules, "pdf_converter", SimpleNamespace(markdown_to_pdf=callback))


@pytest.mark.parametrize("partial", [False, True])
def test_stale_diagnostic_pdf_never_accepted_after_conversion_failure(cache, monkeypatch, partial):
    md, pdf, read, _ = cache
    pdf.write_bytes(b"old private diagnostic")
    old_time = md.stat().st_mtime_ns - 1
    os.utime(pdf, ns=(old_time, old_time))

    def fail(source, target, *args, **kwargs):
        assert Path(target) != pdf
        if partial:
            Path(target).write_bytes(b"partial output")
        raise RuntimeError("conversion failed")

    converter(monkeypatch, fail)
    for _ in range(2):
        with pytest.raises(RuntimeError, match="conversion failed"):
            read()
        assert pdf.read_bytes() == b"old private diagnostic"
        assert list(pdf.parent.iterdir()) == [pdf]


def test_newer_wrong_stem_pdf_is_not_selected(cache, monkeypatch):
    md, pdf, read, _ = cache
    wrong = pdf.with_name("000660_Other_20260913_analysis.pdf")
    wrong.write_bytes(b"wrong report")

    def render(source, target, *args, **kwargs):
        Path(target).write_bytes(b"new valid pdf")

    converter(monkeypatch, render)
    assert read()[3] == pdf
    assert pdf.read_bytes() == b"new valid pdf"
    assert pdf.stat().st_mode & 0o777 == 0o644
    assert wrong.read_bytes() == b"wrong report"


def test_atomic_success_replaces_stale_pdf_and_preserves_permissions(cache, monkeypatch):
    md, pdf, read, save = cache
    pdf.write_bytes(b"old private diagnostic")
    pdf.chmod(0o640)
    old_time = md.stat().st_mtime_ns - 1
    os.utime(pdf, ns=(old_time, old_time))

    def render(source, target, *args, **kwargs):
        target = Path(target)
        assert target.parent == pdf.parent and target != pdf
        target.write_bytes(b"new valid pdf")
        assert pdf.read_bytes() == b"old private diagnostic"

    converter(monkeypatch, render)
    assert save() == pdf
    assert pdf.stat().st_mode & 0o777 == 0o640
    assert pdf.stat().st_mtime_ns >= md.stat().st_mtime_ns
    assert read()[3] == pdf
    assert pdf.read_bytes() == b"new valid pdf"
    assert list(pdf.parent.iterdir()) == [pdf]


def test_empty_conversion_is_not_published(cache, monkeypatch):
    _, pdf, _, save = cache
    converter(monkeypatch, lambda *args, **kwargs: None)
    with pytest.raises(RuntimeError):
        save()
    assert not list(pdf.parent.iterdir())


def test_exact_pdf_with_equal_nanosecond_timestamp_is_reused(cache, monkeypatch):
    md, pdf, read, _ = cache
    pdf.write_bytes(b"valid pdf")
    timestamp = md.stat().st_mtime_ns
    os.utime(pdf, ns=(timestamp, timestamp))

    def unexpected_conversion(*args, **kwargs):
        pytest.fail("Fresh matching PDF must not be regenerated")

    converter(monkeypatch, unexpected_conversion)
    assert read()[3] == pdf


def test_markdown_rewrite_during_conversion_is_not_published(cache, monkeypatch):
    md, pdf, _, save = cache
    pdf.write_bytes(b"previous pdf")
    timestamp = md.stat().st_mtime_ns

    def render(source, target, *args, **kwargs):
        md.write_text("# 수정된 분석 보고서", encoding="utf-8")
        os.utime(md, ns=(timestamp + 1, timestamp + 1))
        Path(target).write_bytes(b"pdf for superseded markdown")

    converter(monkeypatch, render)
    with pytest.raises(RuntimeError, match="Markdown changed"):
        save()
    assert pdf.read_bytes() == b"previous pdf"
    assert list(pdf.parent.iterdir()) == [pdf]
