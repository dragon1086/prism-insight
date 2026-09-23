import asyncio
import logging
import sys
from types import SimpleNamespace

import pytest

from cores import report_generation


@pytest.fixture(autouse=True)
def no_external_backend(monkeypatch):
    def forbidden():
        raise AssertionError('External backend prohibited in integration unit tests')
    monkeypatch.setattr(report_generation, '_get_report_backend', forbidden)


def test_existing_summary_stage_applies_fact_edits_without_extra_generation(monkeypatch):
    reports = {'dart_deep_analysis': 'protected chapter', 'company_status': 'old explanation'}
    calls = []

    async def edit(source, *args, **kwargs):
        calls.append(dict(source))
        return {**source, 'company_status': 'qualified explanation'}, '## 핵심 요약\n정확한 구분', {'edit_count': 1}

    async def forbidden(*args, **kwargs):
        raise AssertionError('A second summary model was called')

    monkeypatch.setitem(sys.modules, 'cores.report_fact_editor', SimpleNamespace(edit_and_summarize=edit))
    monkeypatch.setattr(report_generation, '_generate_agent_text', forbidden)
    summary = asyncio.run(report_generation.generate_summary(reports, '예시', '123456', '20260924', logging.getLogger()))
    assert summary == '## 핵심 요약\n정확한 구분' and len(calls) == 1
    assert reports['company_status'] == 'qualified explanation'
    assert reports['dart_deep_analysis'] == 'protected chapter'


def test_fact_editor_failure_is_not_a_successful_summary_fallback(monkeypatch):
    async def fail(*args, **kwargs):
        raise ValueError('unresolved_fact_conflict')

    monkeypatch.setitem(sys.modules, 'cores.report_fact_editor', SimpleNamespace(edit_and_summarize=fail))
    monkeypatch.setattr(report_generation, '_generate_agent_text', fail)
    with pytest.raises(ValueError, match='unresolved_fact_conflict'):
        asyncio.run(report_generation.generate_summary({'dart_deep_analysis': 'protected'},
            '예시', '123456', '20260924', logging.getLogger()))
