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


def test_incomplete_depth_report_is_delivered_fresh_but_never_reused(monkeypatch, tmp_path):
    from prism_core import report_service
    body = '# 기본 보고서\n<!-- DART_DEPTH_INCOMPLETE -->\n공시 심층 장은 이번 보고서에 반영하지 못했습니다.\n정상 기본 분석'
    assert report_generator._is_deliverable_report(body)
    saved = []
    monkeypatch.setattr(report_service, 'get_cached_report', lambda *a, **k: (False, '', None, None))
    monkeypatch.setattr(report_service, 'generate_report_response_sync', lambda *a: body)
    monkeypatch.setattr(report_service, 'save_report', lambda *a: saved.append('md') or tmp_path / 'r.md')
    monkeypatch.setattr(report_service, 'save_pdf_report', lambda *a: saved.append('pdf') or tmp_path / 'r.pdf')
    artifact = report_service.generate_report('035720', '카카오')
    assert artifact.succeeded and artifact.content == body and saved == ['md', 'pdf']
    # Diagnostic failures remain undeliverable.
    monkeypatch.setattr(report_service, 'generate_report_response_sync', lambda *a: 'Analysis failed: x')
    assert not report_service.generate_report('035720', '카카오').succeeded


def test_ops_alert_never_falls_back_to_public_channel(monkeypatch):
    import asyncio

    from prism_core import ops_alert
    for name in ('OPS_ALERT_CHAT_ID', 'OPS_ALERT_BOT_TOKEN', 'OAUTH_ALERT_CHAT_ID', 'OAUTH_ALERT_BOT_TOKEN'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('TELEGRAM_CHANNEL_ID', '-100public')
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'public-token')
    assert ops_alert._target() == (None, None)
    assert asyncio.run(ops_alert.send_ops_alert('x')) is False
    monkeypatch.setenv('OAUTH_ALERT_CHAT_ID', '-100ops')
    monkeypatch.setenv('OAUTH_ALERT_BOT_TOKEN', 'ops-token')
    assert ops_alert._target() == ('-100ops', 'ops-token')
    monkeypatch.setenv('OPS_ALERT_CHAT_ID', '-100maint')
    monkeypatch.setenv('OPS_ALERT_BOT_TOKEN', 'maint-token')
    assert ops_alert._target() == ('-100maint', 'maint-token')
