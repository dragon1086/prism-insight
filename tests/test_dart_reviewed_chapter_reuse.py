import asyncio
import json

import pytest
from test_dart_deep_analysis import SOURCE_URL, packet

from cores import dart_deep_analysis
from tools.verify_kr_report_depth import reviewed_chapter


def saved(tmp_path, monkeypatch):
    sources = packet()
    sources['receipt']['core_union_sha256'] = '1' * 64

    async def write(agent, message):
        text = '### 원문 분석\n\n' + '조건과 기간을 구분해 설명합니다. ' * 60 + '\n\n출처: ' + SOURCE_URL
        (tmp_path / (agent.name + '.md')).write_text(text, encoding='utf-8')
        return text, None

    monkeypatch.setattr(dart_deep_analysis, '_write', write)
    chapter, receipt = asyncio.run(dart_deep_analysis.generate_dart_chapter(
        sources, company_name='예시', company_code='123456', reference_date='20260924', peer_context='same peers'))
    (tmp_path / 'dart_chapter.md').write_text(chapter, encoding='utf-8')
    (tmp_path / 'generation_receipt.json').write_text(json.dumps({'receipt': receipt}), encoding='utf-8')
    return sources, chapter


def test_reviewed_reuse_is_exact_and_makes_no_model_calls(tmp_path, monkeypatch):
    sources, chapter = saved(tmp_path, monkeypatch)
    result, receipt = reviewed_chapter(tmp_path, sources, company_code='123456',
                                      reference_date='20260924', peer_context='same peers')
    assert result == chapter and receipt['calls'] == 0 and receipt['original_calls'] == 3


@pytest.mark.parametrize('changed', ['source', 'company', 'date', 'peers', 'text', 'model', 'missing_hash'])
def test_reviewed_reuse_rejects_changed_basis_without_regeneration(tmp_path, monkeypatch, changed):
    sources, _ = saved(tmp_path, monkeypatch)
    kwargs = {'company_code': '123456', 'reference_date': '20260924', 'peer_context': 'same peers'}
    if changed == 'source':
        sources['receipt']['core_union_sha256'] = '2' * 64
    elif changed == 'missing_hash':
        del sources['receipt']['core_union_sha256']
    elif changed in {'company', 'date', 'peers'}:
        kwargs[{'company': 'company_code', 'date': 'reference_date', 'peers': 'peer_context'}[changed]] = 'different'
    elif changed == 'text':
        with (tmp_path / 'dart_chapter.md').open('a') as handle:
            handle.write('altered')
    else:
        import report_model_config
        monkeypatch.setattr(report_model_config, 'DART_REPORT_MODEL', 'different')
    with pytest.raises(ValueError):
        reviewed_chapter(tmp_path, sources, **kwargs)
