import asyncio
import copy
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


def failed_full_run(tmp_path):
    """Historical full runs captured prose and editor input, not writer config."""
    sources = packet()
    sources['receipt'].update(core_union_sha256='1' * 64, core_conserved=True, capacity_ok=True)
    drafts = []
    for role in dart_deep_analysis.ROLES:
        text = ('### ' + role + ' 원문 분석\n\n' + '조건과 기간을 구분해 설명합니다. ' * 60
                + '\n\n출처: ' + SOURCE_URL)
        drafts.append(text)
        (tmp_path / f'dart_depth_{role}.md').write_text(text, encoding='utf-8')
        (tmp_path / f'dart_depth_{role}_usage.json').write_text(json.dumps({
            'input_bytes': 100, 'usage': None, 'output_chars': len(text)}), encoding='utf-8')
    chapter = (dart_deep_analysis.CHAPTER_START + '\n\n## 5. DART 주요 재무·사업 위험 분석\n\n'
               + '\n\n'.join(drafts) + '\n\n' + dart_deep_analysis.CHAPTER_END)
    request = {'company_code': '123456', 'reference_date': '20260924',
               'sections': {'dart_deep_analysis': chapter}}
    (tmp_path / 'dart_inputs.json').write_text(json.dumps(sources), encoding='utf-8')
    (tmp_path / 'validation_invocation.json').write_text(json.dumps({
        'ticker': '123456', 'reference_date': '20260924'}), encoding='utf-8')
    (tmp_path / 'final_editor_request_1.json').write_text(json.dumps(request), encoding='utf-8')
    return sources, chapter, request


def test_failed_full_run_reuses_exact_reviewed_prose_without_fabricating_receipt(tmp_path, monkeypatch):
    import hashlib
    sources, chapter, _ = failed_full_run(tmp_path)
    original = copy.deepcopy(sources)

    async def forbidden(*args, **kwargs):
        raise AssertionError('Reviewed staged reuse must not call a model')

    monkeypatch.setattr(dart_deep_analysis, '_write', forbidden)
    result, receipt = reviewed_chapter(tmp_path, sources, company_code='123456', reference_date='20260924')
    assert result == chapter and sources == original
    assert receipt['status'] == 'validation_only_reused_failed_full_run_source_chapter'
    assert receipt['calls'] == 0 and 'writers' not in receipt and 'original_calls' not in receipt
    assert receipt['original_writer_configuration'] == 'not_recorded_in_prior_full_run_capture'
    assert receipt['chapter_sha256'] == hashlib.sha256(chapter.encode()).hexdigest()
    assert set(receipt['writer_artifacts']) == set(dart_deep_analysis.ROLES)
    for role, artifact in receipt['writer_artifacts'].items():
        assert artifact['prose_sha256'] == hashlib.sha256((tmp_path / f'dart_depth_{role}.md').read_bytes()).hexdigest()
        assert artifact['capture_receipt_sha256'] == hashlib.sha256((tmp_path / f'dart_depth_{role}_usage.json').read_bytes()).hexdigest()


@pytest.mark.parametrize('changed', [
    'source_hash', 'current_capacity', 'prior_conservation', 'company', 'date',
    'invocation_company', 'invocation_date', 'request_company', 'request_date',
    'current_peer', 'prior_peer', 'chapter_prose', 'writer_prose', 'source_url',
    'missing_usage', 'usage_count', 'usage_input_bytes', 'usage_missing_field',
    'missing_start', 'missing_end', 'duplicate_marker', 'missing_writer',
])
def test_failed_full_run_rejects_changed_or_incomplete_capture(tmp_path, changed):
    sources, chapter, request = failed_full_run(tmp_path)
    kwargs = {'company_code': '123456', 'reference_date': '20260924'}
    role = next(iter(dart_deep_analysis.ROLES))
    prior = copy.deepcopy(sources)
    invocation = {'ticker': '123456', 'reference_date': '20260924'}
    usage_path = tmp_path / f'dart_depth_{role}_usage.json'
    if changed == 'source_hash': sources['receipt']['core_union_sha256'] = '2' * 64
    elif changed == 'current_capacity': sources['receipt']['capacity_ok'] = False
    elif changed == 'prior_conservation': prior['receipt']['core_conserved'] = False
    elif changed == 'company': kwargs['company_code'] = '654321'
    elif changed == 'date': kwargs['reference_date'] = '20260923'
    elif changed == 'invocation_company': invocation['ticker'] = '654321'
    elif changed == 'invocation_date': invocation['reference_date'] = '20260923'
    elif changed == 'request_company': request['company_code'] = '654321'
    elif changed == 'request_date': request['reference_date'] = '20260923'
    elif changed == 'current_peer': kwargs['peer_context'] = 'unverified peer'
    elif changed == 'prior_peer': request['sections']['peer_comparison'] = ''
    elif changed == 'chapter_prose': request['sections']['dart_deep_analysis'] = chapter.replace('조건과 기간', '다른 기간', 1)
    elif changed == 'writer_prose':
        (tmp_path / f'dart_depth_{role}.md').write_text('incomplete draft', encoding='utf-8')
    elif changed == 'source_url':
        sources['contexts'][role] = sources['contexts'][role].replace(SOURCE_URL, SOURCE_URL + 'changed')
    elif changed == 'missing_usage': usage_path.unlink()
    elif changed.startswith('usage_'):
        usage = json.loads(usage_path.read_text(encoding='utf-8'))
        if changed == 'usage_count': usage['output_chars'] += 1
        elif changed == 'usage_input_bytes': usage['input_bytes'] = 0
        else: del usage['usage']
        usage_path.write_text(json.dumps(usage), encoding='utf-8')
    elif changed == 'missing_start': request['sections']['dart_deep_analysis'] = chapter.replace(dart_deep_analysis.CHAPTER_START, '')
    elif changed == 'missing_end': request['sections']['dart_deep_analysis'] = chapter.replace(dart_deep_analysis.CHAPTER_END, '')
    elif changed == 'duplicate_marker': request['sections']['dart_deep_analysis'] = dart_deep_analysis.CHAPTER_START + chapter
    elif changed == 'missing_writer': (tmp_path / f'dart_depth_{role}.md').unlink()
    (tmp_path / 'dart_inputs.json').write_text(json.dumps(prior), encoding='utf-8')
    (tmp_path / 'validation_invocation.json').write_text(json.dumps(invocation), encoding='utf-8')
    (tmp_path / 'final_editor_request_1.json').write_text(json.dumps(request), encoding='utf-8')
    with pytest.raises((ValueError, FileNotFoundError)):
        reviewed_chapter(tmp_path, sources, **kwargs)
