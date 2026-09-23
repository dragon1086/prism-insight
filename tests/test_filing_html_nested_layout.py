"""Accounting-scope repair; nested narrative layouts remain unsupported."""
import pytest

from prism_core.filing_html import parse_filing_html

HEAD = '<p>3. 연결재무제표 주석</p>'
DATA = '<table><tr><th>항목</th><th>금액</th></tr><tr><td>차입금</td><td>100</td></tr></table>'


def wrap(body):
    return '<table class="nb" border="0"><tbody><tr><td>' + body + '</td></tr></tbody></table>'


@pytest.mark.parametrize('policy', ['2. 재무제표의 작성기준 (연결)',
                                  '2. 연결 재무제표 의 작성 기준',
                                  '2. 재무제표의 작성의 기초'])
def test_accounting_optional_particle_retains_explicit_notes(policy):
    out = parse_filing_html(HEAD + f'<p>{policy}</p><p>조건입니다.</p>')
    assert out['records'][0]['scope'] == 'consolidated'
    assert out['records'][0]['section_path'] == ['3. 연결재무제표 주석', policy]


@pytest.mark.parametrize('policy', ['2. 별도 재무제표의 작성기준',
                                  '2. 재무제표의 작성기준 (별도)',
                                  '2. 재무제표의 작성기준 및 임의 제목',
                                  '4. 재무제표(별도)'])
def test_optional_particle_does_not_weaken_scope_guard(policy):
    out = parse_filing_html(HEAD + f'<p>{policy}</p><p>조건입니다.</p>')
    assert out['records'][0]['scope'] == 'unknown'



def test_standalone_accounting_particle_keeps_explicit_parent():
    out = parse_filing_html('<p>5. 재무제표 주석</p>'
                            '<p>2. 별도 재무제표의 작성기준 (별도)</p><p>조건입니다.</p>')
    assert out['records'][0]['scope'] == 'standalone'


@pytest.mark.parametrize('title', ['2.1 재무제표 작성기준',
                                  '2.1 반기연결재무제표 작성기준',
                                  "1) 기업회계기준서 제1234호 '재무제표 표시' 제정"])
def test_unsupported_nested_layout_cannot_mutate_following_original_scope(title):
    raw = HEAD + '<p>2. 중요한 회계정책 (연결)</p>'
    raw += wrap(f'<p>{title}</p><p>내부 설명입니다.</p>' + DATA)
    raw += '<p>3. 약정사항 (연결)</p>' + DATA
    out = parse_filing_html(raw)
    assert out['status'] == 'PARTIAL'
    assert out['errors'] == ['TABLE_UNSUPPORTED']
    assert len(out['records']) == 1
    assert out['records'][0]['scope'] == 'consolidated'
    assert out['records'][0]['section_path'] == ['3. 연결재무제표 주석', '3. 약정사항 (연결)']
    assert out['records'][0]['source_path'] == '/html[1]/body[1]/table[2]'


def test_non_nested_narrative_table_retains_existing_atomic_representation():
    out = parse_filing_html(HEAD + wrap('<p>조건입니다.</p><p>추가 조건입니다.</p>'))
    assert out['status'] == 'COMPLETE'
    assert len(out['records']) == 1
    assert out['records'][0]['kind'] == 'table'
    assert out['records'][0]['table']['cells'][0]['text'] == '조건입니다. 추가 조건입니다.'


@pytest.mark.parametrize('policy', ['2. 재무제표의 작성기준', '2. 연결 재무제표의 작성기준'])
def test_optional_particle_without_explicit_parent_does_not_infer_scope(policy):
    out = parse_filing_html(f'<p>{policy}</p><p>조건입니다.</p>')
    assert out['records'][0]['scope'] == 'unknown'
