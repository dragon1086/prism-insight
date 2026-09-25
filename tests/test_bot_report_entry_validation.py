"""No models/subprocesses: validate fresh-entry wiring and artifact isolation."""
from datetime import datetime
import json
import os
from pathlib import Path
import socket
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from tools import verify_bot_report_entry as tool


@pytest.fixture(autouse=True)
def no_external_effects(monkeypatch, tmp_path):
    monkeypatch.setattr(os, 'environ', dict(os.environ))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('PYTHON_DOTENV_DISABLED', '1')
    def deny(*args, **kwargs): raise AssertionError('No live network permitted')
    monkeypatch.setattr(socket.socket, 'connect', deny)
    monkeypatch.setattr(socket, 'create_connection', deny)


def paths(tmp_path):
    operational = tmp_path / 'operational'
    operational.mkdir()
    return operational, operational / 'runtime/report_validation/fresh'


@pytest.mark.parametrize('case', ['outside', 'root_output', 'same_checkout', 'nested_checkout', 'preexisting', 'historical_date'])
def test_preflight_rejects_unsafe_or_untruthful_invocation(tmp_path, monkeypatch, case):
    operational, output = paths(tmp_path)
    date = None
    if case == 'outside': output = tmp_path / 'outside'
    elif case == 'root_output': output = operational / 'runtime/report_validation'
    elif case == 'same_checkout': monkeypatch.setattr(tool, 'ROOT', operational)
    elif case == 'nested_checkout': monkeypatch.setattr(tool, 'ROOT', operational / 'child-worktree')
    elif case == 'preexisting': output.mkdir(parents=True)
    else: date = '20000101'
    monkeypatch.setattr(tool, '_configure_environment', lambda *a: pytest.fail('Preflight must finish before loading configuration'))
    with pytest.raises((ValueError, FileExistsError)):
        tool.run_validation(operational_root=operational, output=output, ticker='017670', company='Example', date=date)


def test_symlink_output_escape_is_rejected(tmp_path, monkeypatch):
    operational, _ = paths(tmp_path)
    allowed = operational / 'runtime/report_validation'
    allowed.mkdir(parents=True)
    outside = tmp_path / 'outside'
    outside.mkdir()
    (allowed / 'link').symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        tool.run_validation(operational_root=operational, output=allowed / 'link/new', ticker='017670', company='Example')
    assert not list(outside.iterdir())


def test_current_date_requires_real_host_parity_for_kr(monkeypatch):
    class Clock:
        @staticmethod
        def now(tz=None):
            return datetime(2026, 9, 24 if tz is not None else 23, 10)
    monkeypatch.setattr(tool, 'datetime', Clock)
    with pytest.raises(ValueError, match='Host'):
        tool._invocation_date('20260924', 'kr')
    assert tool._invocation_date('20260924', 'us') == '20260924'
    with pytest.raises(ValueError, match='current'):
        tool._invocation_date('20260923', 'us')


@pytest.mark.parametrize('market', ['kr', 'us'])
@pytest.mark.parametrize('outcome', ['completed', 'cached', 'failed', 'escape', 'missing_pdf', 'changed_markdown', 'missing_depth'])
def test_service_binding_and_saved_artifact_contract(tmp_path, monkeypatch, market, outcome):
    import report_generator
    from prism_core import report_service
    operational, output = paths(tmp_path)
    monkeypatch.setattr(tool, '_invocation_date', lambda date, market: '20260924')
    environment = []
    def configure(op, out):
        environment.append((op, out))
        monkeypatch.setenv('PRISM_DISABLE_SIGNAL_PUBLISH', '1')
        monkeypatch.setenv('PRISM_REPORT_DIAGNOSTICS_DIR', str(out / 'diagnostics'))
    monkeypatch.setattr(tool, '_configure_environment', configure)
    content = '<!-- DART_DEEP_ANALYSIS_START -->\nPublic report\n<!-- DART_DEEP_ANALYSIS_END -->'
    if outcome == 'missing_depth': content = 'Public report without DART chapter'
    calls = []
    names = ('REPORTS_DIR', 'PDF_REPORTS_DIR') if market == 'kr' else ('US_REPORTS_DIR', 'US_PDF_REPORTS_DIR')
    old = {name: getattr(report_generator, name) for name in names}

    def service(ticker, company, **kwargs):
        calls.append((ticker, company, kwargs))
        reports, pdfs = (getattr(report_generator, name) for name in names)
        assert reports == output / 'reports' and pdfs == output / 'pdf_reports'
        assert not list(reports.iterdir()) and not list(pdfs.iterdir())
        md = (tmp_path if outcome == 'escape' else reports) / 'report.md'
        pdf = pdfs / 'report.pdf'
        md.write_text(content if outcome != 'changed_markdown' else 'different')
        if outcome != 'missing_pdf': pdf.write_bytes(b'%PDF-fixture')
        return report_service.ReportArtifact(status='failed' if outcome == 'failed' else 'completed',
            content=content, markdown_path=md, pdf_path=pdf, cached=outcome == 'cached')

    monkeypatch.setattr(report_service, 'generate_report', service)
    success = outcome == 'completed' or (market == 'us' and outcome == 'missing_depth')
    args = dict(operational_root=operational, output=output, ticker='017670' if market == 'kr' else 'AAPL',
                company='Example', market=market, date='20260924')
    if success:
        result = tool.run_validation(**args)
        assert result['status'] == 'completed' and result['cached'] is False
        assert len(result['markdown']['sha256']) == len(result['pdf']['sha256']) == 64
        assert result['delivery_tested'] is False and result['fresh_bot_service_entry'] is True
    else:
        with pytest.raises(ValueError): tool.run_validation(**args)
    assert calls == [(args['ticker'], 'Example', {'market': market, 'cache_only': False})]
    assert environment == [(operational, output)]
    receipt = json.loads((output / 'bot_entry_receipt.json').read_text())
    assert receipt['status'] == ('completed' if success else 'failed')
    assert 'duration_seconds' in receipt
    assert all(getattr(report_generator, name) == value for name, value in old.items())


def test_bootstrap_loads_existing_oauth_and_inherits_diagnostics_without_copying_secrets(tmp_path, monkeypatch):
    import dotenv
    from cores.llm import config_loader
    from cores import market_data
    operational, output = paths(tmp_path)
    loaded = []
    monkeypatch.setattr(dotenv, 'load_dotenv', lambda path, **kw: loaded.append((path, kw)))
    monkeypatch.setenv('OPENAI_BASE_URL', 'http://127.0.0.1:18888/v1')
    monkeypatch.setenv('OPENAI_API_KEY', 'chatgpt-oauth-placeholder')
    monkeypatch.setenv('ARCHIVE_API_URL', 'https://fixture-db.example')
    monkeypatch.setattr(config_loader, 'load_mcp_registry', lambda *a, **kw: SimpleNamespace(get=lambda name: SimpleNamespace(env={})))
    monkeypatch.setattr(market_data, 'default_chain', lambda: SimpleNamespace(names=['kis-remote']))
    tool._configure_environment(operational, output)
    assert loaded == [(operational / '.env', {}), (operational / '.env.report-oauth', {'override': True})]
    assert os.environ['PRISM_REPORT_DIAGNOSTICS_DIR'] == str(output / 'diagnostics')
    assert os.environ['PRISM_DISABLE_SIGNAL_PUBLISH'] == '1'
    assert not output.exists()
