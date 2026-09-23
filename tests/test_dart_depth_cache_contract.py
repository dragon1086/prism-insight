"""Human report requests must not reuse shallow or incomplete depth artifacts."""
import inspect

import report_generator


def test_human_cache_requires_depth_contract_without_changing_legacy_default(monkeypatch, tmp_path):
    reports = tmp_path / 'reports'
    pdfs = tmp_path / 'pdfs'
    reports.mkdir()
    pdfs.mkdir()
    md = reports / '017670_SK텔레콤_20260923_analysis.md'
    pdf = pdfs / md.with_suffix('.pdf').name
    md.write_text('# 기존 기본 보고서\n정상 기본 분석', encoding='utf-8')
    pdf.write_bytes(b'fake existing PDF')
    monkeypatch.setattr(report_generator, 'REPORTS_DIR', reports)
    monkeypatch.setattr(report_generator, 'PDF_REPORTS_DIR', pdfs)
    assert report_generator.get_cached_report('017670')[0] is True
    assert report_generator.get_cached_report('017670', require_dart_depth=True)[0] is False
    md.write_text('# 새 보고서\n<!-- DART_DEEP_ANALYSIS_START -->\n공시 분석\n'
                  '<!-- DART_DEEP_ANALYSIS_END -->', encoding='utf-8')
    pdf.write_bytes(b'newer existing PDF')
    assert report_generator.get_cached_report('017670', require_dart_depth=True)[0] is True


def test_incomplete_chapter_cannot_be_promoted_to_human_cache():
    assert not report_generator._is_cacheable_report('# 기본 보고서\n<!-- DART_DEPTH_INCOMPLETE -->\n미반영 고지')


def test_human_generation_explicitly_requires_depth():
    assert 'require_dart_depth=True' in inspect.getsource(report_generator.generate_report_response_sync)
