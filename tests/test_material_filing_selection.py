"""Source coverage is not a severity or trading decision score."""
from prism_core.material_filing_selection import material_filing_records


def test_contract_conditions_stay_together_in_same_source_unit():
    text = ('## III. 재무에 관한 사항\n### 3. 연결재무제표 주석\n38. 우발채무 및 약정사항\n'
            '(3) 기술이전계약\n\n계약금은 반환의무가 없습니다.\n\n'
            '다만 특정 조건 충족 시 원천징수세액 반환의무가 발생할 수 있습니다.\n\n'
            '기술이전수익은 임상시험 성공 여부에 따라 달라질 수 있습니다.\n')
    rows, gaps = material_filing_records(text)
    matches = [r for r in rows if '다만' in r['text']]
    assert matches and not gaps
    assert '반환의무가 없습니다' in matches[0]['text']
    assert '임상시험 성공 여부' in matches[0]['text']
    for row in rows:
        assert '\n'.join(text[a:b] for a, b in row['source_spans']) == row['text']
        assert 'risk_score' not in row and 'action' not in row


def test_material_groups_never_cross_scope_or_note_boundaries():
    text = ('## III. 재무에 관한 사항\n### 3. 연결재무제표 주석\n1. 차입금\n'
            '차입약정 위반은 없습니다.\n\n2. 전환사채\n전환가격 조정 조건이 있습니다.\n'
            '### 5. 재무제표 주석\n1. 차입금\n차입약정 위반 여부는 미확인입니다.\n')
    rows, _ = material_filing_records(text)
    for row in rows:
        if '전환가격' in row['text']:
            assert '차입약정' not in row['text']
        if '위반은 없습니다' in row['text']:
            assert row['scope'] == 'consolidated'
            assert '미확인' not in row['text']


def test_table_source_period_unit_and_dashes_not_repaired():
    text = ('## III. 재무에 관한 사항\n### 3. 연결재무제표 주석\n9. 매출채권\n'
            '(단위: 백만원)\n| 구분 | 당기 | 전기 |\n|---|---|---|\n'
            '| 손실충당금 | - | 20 |\n주) 미확인 값입니다.\n')
    rows, _ = material_filing_records(text)
    row = next(r for r in rows if r['kind'] == 'table')
    assert '- | 20' in row['text'] and '백만원' in row['text'] and '미확인' in row['text']
    assert row['scope'] == 'consolidated'


def test_large_qualifying_prose_group_is_omitted_not_cut():
    text = '## 사업 내용\n기술이전 계약금 반환의무가 없습니다.\n\n' + '다만 반환의무를 검토해야 합니다. ' * 300
    rows, gaps = material_filing_records(text)
    assert not rows
    assert 'MATERIAL_PROSE_GROUP_OVERSIZE' in gaps


def test_generalized_topic_breadth_not_repeated_short_cash_tables():
    text = ('## III. 재무에 관한 사항\n### 3. 연결재무제표 주석\n'
            '1. 매출채권\n매출채권 손실충당금은 전기와 비교합니다.\n\n'
            '2. 차입금\n차입금 만기와 약정 위반 여부를 확인합니다.\n\n'
            '3. 전환사채\n전환사채 전환가격 조정 조건을 확인합니다.\n\n'
            '4. 보고기간후 사건\n보고기간후 사건으로 자본 조달 계약이 체결됐습니다.\n')
    rows, _ = material_filing_records(text)
    tags = {tag for row in rows for tag in row['material_topics']}
    assert {'earnings_quality', 'liquidity_covenants', 'dilution_overhang', 'subsequent_events'} <= tags


def test_invalid_input_and_no_topic_are_visible():
    assert material_filing_records(None) == ([], ['MATERIAL_INPUT_INVALID'])
    assert material_filing_records('a' * (2 * 1024 * 1024 + 1)) == ([], ['MATERIAL_INPUT_LIMIT'])
    assert material_filing_records('일반적인 회사 주소 안내입니다.')[0] == []


def test_concrete_note_breadth_precedes_generic_policy_and_duplicate_scope():
    text = ('## III. 재무에 관한 사항\n### 3. 연결재무제표 주석\n'
            '3. 회계정책의 변경\n새 기준은 현금흐름 분류 조건을 설명합니다.\n\n'
            '9. 매출채권\n매출채권 손실충당금은 20백만원입니다.\n\n'
            '10. 재고자산\n재고자산 평가손실은 30백만원입니다.\n\n'
            '### 5. 재무제표 주석\n9. 매출채권\n매출채권 손실충당금은 10백만원입니다.\n')
    rows, _ = material_filing_records(text)
    assert '20백만원' in rows[0]['text']
    assert '30백만원' in rows[1]['text']
    assert any('10백만원' in row['text'] for row in rows)  # retained, not mislabeled


def test_html_rejected_table_or_heading_breaks_group_adjacency():
    from prism_core.filing_html import parse_filing_html
    from prism_core.material_filing_selection import material_html_records

    raw = ('<h2>II. 사업의 내용</h2><p>반환의무가 없습니다.</p>'
           '<table><tr><td colspan="bad">다른 원문</td></tr></table>'
           '<p>다른 조건입니다.</p>')
    parsed = parse_filing_html(raw)
    records, _ = material_html_records(parsed['records'])
    assert len(records) == 2
    assert records[0]['text'] == '반환의무가 없습니다.'
