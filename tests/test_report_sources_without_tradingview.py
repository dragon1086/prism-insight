"""Retirement guard: keep ordinary research, remove the unused provider surface."""
import ast
from pathlib import Path

from tools.configure_report_research import configure

ROOT = Path(__file__).resolve().parents[1]


def test_retired_provider_has_no_runtime_modules_or_imports():
    for name in ('collection', 'credentials', 'evidence', 'transport'):
        assert not (ROOT / 'prism_core' / f'tradingview_{name}.py').exists()
    for directory in ('cores', 'prism_core', 'prism-us/cores', 'tools'):
        for path in (ROOT / directory).rglob('*.py'):
            tree = ast.parse(path.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    assert all('tradingview' not in alias.name.lower() for alias in node.names), path
                elif isinstance(node, ast.ImportFrom):
                    assert 'tradingview' not in (node.module or '').lower(), path


def test_research_config_omits_retired_status_without_changing_other_sources(tmp_path):
    path = tmp_path / 'research.json'
    path.write_text('{"tradingview_status":"RIGHTS_UNCONFIRMED","validated_symbols":{"US":["TEST"]}}')
    result = configure(path, enabled=True, namespace='test')
    assert result['sources'] == ['perplexity_search', 'firecrawl_scrape']
    assert result['validated_symbols'] == {'US': ['TEST']}
    assert result['enabled'] is True
    assert not any('tradingview' in key.lower() for key in result)


def test_current_receipt_and_ci_do_not_reference_retired_feature():
    for relative in ('prism_core/report_research_prefetch.py',
                     'tools/configure_report_research.py'):
        assert 'tradingview' not in (ROOT / relative).read_text().lower()
    ci = (ROOT / '.github/workflows/ci.yml').read_text()
    assert 'tests/test_tradingview_' not in ci
    assert 'tests/test_report_research_prefetch.py' in ci
