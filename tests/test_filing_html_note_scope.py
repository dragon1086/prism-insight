"""Synthetic source-local note boundaries; no borrowed issuer body text."""

import hashlib

import pytest

from prism_core.filing_html import parse_filing_html


@pytest.mark.parametrize('parent,policy,scope', [
    ('3. 연결재무제표 주석', '2. 연결재무제표 작성기준 및 중요한 회계정책 (연결)', 'consolidated'),
    ('5. 재무제표 주석', '2. 별도재무제표 작성기준 및 중요한 회계정책 (별도)', 'standalone'),
    ('3. 연결재무제표 주석', '2. 연결 재무제표 작성의 기초', 'consolidated'),
    ('5. 재무제표 주석', '2. 별도 재무제표 작성 기준', 'standalone'),
])
def test_explicit_accounting_prefix_matches_parent_without_reset(parent, policy, scope):
    raw = (f'<p>{parent}</p><p>{policy}</p><p>제 20 기 반기말 (단위: 백만원)</p>'
           '<table><tr><th>항목</th><th>금액</th></tr><tr><td>차입금</td><td>100</td></tr></table>'
           '<p>주1) 약정 위반은 없으나 조건 변경 시 상환 의무가 있습니다.</p>'
           '<p>3. 재무위험관리</p><p>추가 조건은 다음 보고기간에 확인합니다.</p>')
    parsed = parse_filing_html(raw)
    assert {row['scope'] for row in parsed['records']} == {scope}
    table = next(row for row in parsed['records'] if row['kind'] == 'table')
    assert table['section_path'] == [parent, policy]
    assert '백만원' in table['context_before'] and '반기말' in table['context_before']
    assert '약정 위반은 없으나' in table['footnotes']
    assert parsed['source_sha256'] == hashlib.sha256(raw.encode()).hexdigest()


@pytest.mark.parametrize('parent,policy', [
    ('3. 연결재무제표 주석', '2. 별도재무제표 작성기준'),
    ('5. 재무제표 주석', '2. 연결재무제표 작성기준'),
    ('3. 연결재무제표 주석', '2. 별도재무제표 작성기준 (연결)'),
    ('5. 재무제표 주석', '2. 연결재무제표 작성기준 (별도)'),
    ('3. 연결재무제표 주석', '2. 연결재무제표 작성기준 (별도)'),
    ('5. 재무제표 주석', '2. 별도재무제표 작성기준 (연결)'),
    ('', '2. 연결재무제표 작성기준'),
    ('', '2. 별도재무제표 작성기준'),
])
def test_accounting_prefix_never_overrides_missing_or_conflicting_parent(parent, policy):
    rows = parse_filing_html(f'<p>{parent}</p><p>{policy}</p><p>차입금 조건입니다.</p>'
                             '<p>3. 재무위험관리</p><p>다음 주석입니다.</p>')['records']
    assert rows and {row['scope'] for row in rows} == {'unknown'}


@pytest.mark.parametrize('title,scope', [('3. 연결재무제표 주석', 'consolidated'),
                                       ('5. 재무제표 주석', 'standalone')])
@pytest.mark.parametrize('policy', ['2. 재무제표 작성 기준 및 중요한 회계정책 (연결)',
                                  '2. 재무제표 작성기준 및 중요한 회계정책'])
def test_accounting_policy_note_keeps_source_scope_and_qualifications(title, scope, policy):
    if scope == 'standalone':
        policy = policy.replace('(연결)', '(별도)')
    fair_value = '3. 공정가치 (연결)' if scope == 'consolidated' else '3. 공정가치 (별도)'
    raw = (f'<p>{title}</p><p>{policy}</p><p>제 20 기 반기말</p><p>(단위: 백만원)</p>'
           '<table><tr><th>항목</th><th>당반기</th></tr><tr><td>채무</td><td>100</td></tr></table>'
           f'<p>{fair_value}</p><p>약정이 있으나 위반은 없으며 면제 조건은 확인이 필요합니다.</p>')
    parsed = parse_filing_html(raw)
    assert parsed['source_sha256'] == hashlib.sha256(raw.encode()).hexdigest()
    assert {r['scope'] for r in parsed['records']} == {scope}
    table = next(r for r in parsed['records'] if r['kind'] == 'table')
    assert table['section_path'] == [title, policy]
    assert '백만원' in table['context_before'] and '반기말' in table['context_before']
    note = parsed['records'][-1]
    assert note['section_path'] == [title, fair_value]
    assert note['text'] == '약정이 있으나 위반은 없으며 면제 조건은 확인이 필요합니다.'
    assert note['source_path'] and note['source_paths']


def test_true_financial_statement_and_major_boundaries_still_reset():
    raw = ('<p>3. 연결재무제표 주석</p><p>2. 재무제표 작성기준 및 중요한 회계정책</p>'
           '<p>연결 내용입니다.</p><p>4. 재무제표</p><p>별도 내용입니다.</p>'
           '<p>5. 재무제표 주석</p><p>2. 재무제표 작성기준 및 중요한 회계정책</p>'
           '<p>별도 주석입니다.</p><p>III. 기타 사항</p><p>불명 내용입니다.</p>')
    rows = parse_filing_html(raw)['records']
    assert [r['scope'] for r in rows] == ['consolidated', 'standalone', 'standalone', 'unknown']
    assert rows[1]['section_path'] == ['4. 재무제표']
    assert rows[2]['section_path'] == ['5. 재무제표 주석', '2. 재무제표 작성기준 및 중요한 회계정책']


@pytest.mark.parametrize('title', ['2. 재무제표 작성기준 및 중요한 회계정책',
                                  '2. 재무제표 작성 기준 및 중요한 회계정책 (연결)'])
def test_policy_heading_without_explicit_parent_does_not_infer_scope(title):
    rows = parse_filing_html(f'<p>{title}</p><p>약정 내용입니다.</p>')['records']
    assert rows[0]['scope'] == 'unknown'


@pytest.mark.parametrize('parent,policy', [
    ('3. 연결재무제표 주석', '2. 재무제표 작성 기준 및 중요한 회계정책 (별도)'),
    ('5. 재무제표 주석', '2. 재무제표 작성 기준 및 중요한 회계정책 (연결)'),
    ('3. 연결재무제표 주석', '3. 공정가치 (별도)'),
    ('5. 재무제표 주석', '3. 공정가치 (연결)'),
])
def test_policy_explicit_scope_conflict_cannot_inherit_parent(parent, policy):
    rows = parse_filing_html(f'<p>{parent}</p><p>{policy}</p><p>약정 내용입니다.</p>'
                            '<p>4. 충당부채</p><p>다음 주석 내용입니다.</p>')['records']
    assert {r['scope'] for r in rows} == {'unknown'}


@pytest.mark.parametrize('parent,boundary', [
    ('3. 연결재무제표 주석', '4. 재무제표(별도)'),
    ('3. 연결재무제표 주석', '5. 재무제표 주석(별도)'),
    ('3. 연결재무제표 주석', '4. 별도재무제표'),
    ('3. 연결재무제표 주석', '4. 재무제표(개별)'),
    ('5. 재무제표 주석', '2. 연결재무제표(연결)'),
    ('5. 재무제표 주석', '3. 연결재무제표 주석(연결)'),
])
def test_unrecognized_financial_boundary_does_not_inherit_previous_scope(parent, boundary):
    rows = parse_filing_html(f'<p>{parent}</p><p>이전 내용입니다.</p>'
                            f'<p>{boundary}</p><p>경계 이후 내용입니다.</p>')['records']
    assert rows[-1]['scope'] == 'unknown'
    assert rows[-1]['section_path'] == [boundary]


@pytest.mark.parametrize('policy', ['2. 재무제표 작성의 기초', '2. 재무제표 작성 기준',
                                  '2. 재무제표 작성기준 및 중요한 회계정책'])
def test_known_accounting_preparation_note_remains_beneath_explicit_parent(policy):
    rows = parse_filing_html('<p>3. 연결재무제표 주석</p>'
                            f'<p>{policy}</p><p>작성 조건입니다.</p>')['records']
    assert rows[0]['scope'] == 'consolidated'
    assert rows[0]['section_path'] == ['3. 연결재무제표 주석', policy]
