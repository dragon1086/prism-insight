"""One shared period/rank meaning contract, without changing trading rules."""
import asyncio
from types import SimpleNamespace

import pytest

from prism_core.report_evidence_contract import financial_evidence_contract
from prism_core.kr_report_context import reference_context


@pytest.mark.parametrize('language', ['ko', 'en'])
def test_specialists_and_synthesis_share_the_same_period_and_rank_contract(language):
    from cores.report_generation import synthesis_evidence_contract
    contract = financial_evidence_contract(language)
    assert contract in reference_context({}, language)
    assert contract in reference_context({}, language, market_only=True)
    assert contract in synthesis_evidence_contract(language)
    assert 'N+1' in contract
    assert ('분모' in contract and '누락' in contract and '순위' in contract) if language == 'ko' else (
        'denominator' in contract and 'gaps' in contract and 'rank' in contract)


@pytest.mark.parametrize('language', ['ko', 'en'])
def test_comparison_and_profit_owner_scope_are_common_not_reviewer_only(language):
    contract = financial_evidence_contract(language)
    for required in (('업종 PER', '업종 PBR', '지배주주', '비지배', '확인되지') if language == 'ko'
                     else ('industry PER', 'industry PBR', 'parent owners', 'non-controlling', 'unknown')):
        assert required in contract


@pytest.mark.parametrize('stage', ['assessment', 'final'])
def test_actual_review_receives_period_definition_and_all_section_rank_invariant(monkeypatch, tmp_path, stage):
    from cores import report_fact_editor as editor, report_generation
    from cores.llm.ports import LLMResult
    seen = []
    async def run(spec, message):
        seen.append(spec.instructions)
        summary = None if stage == 'assessment' else ('## 요약\n\n' + '제공한 자료의 확인 범위를 유지합니다. ' * 20)
        return LLMResult(structured=spec.output_schema.model_validate(
            {'status': 'READY', 'summary': summary, 'edits': [], 'unresolved': []}))
    monkeypatch.setattr(report_generation, '_get_report_backend', lambda: SimpleNamespace(run=run))
    monkeypatch.setattr(editor, '_calendar_context', lambda day: {'reference_date': day, 'is_session': False})
    monkeypatch.setenv('PRISM_REPORT_DIAGNOSTICS_DIR', str(tmp_path / 'diagnostics'))
    function = editor.assess_report_facts if stage == 'assessment' else editor.edit_and_summarize
    asyncio.run(function({'company_status': '제공한 기업 자료', 'shared_reference': '관측 범위 확인'},
                         'Example', '252990', '20260924'))
    assert len(seen) == 1
    assert financial_evidence_contract('ko') in seen[0]
    assert '동일 N만으로 정렬을' in seen[0]
    assert '모든 기업 관련 장' in seen[0]
