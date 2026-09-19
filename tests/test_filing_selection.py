import json

import pytest

from prism_core.filing_selection import select_filing_evidence

FILING = ('## III. 재무에 관한 사항\n\n### 3. 연결재무제표 주석\n\n'
          '2. 중요한 회계정책\n\n회사는 수익을 인식하는 회계정책을 적용합니다.\n\n'
          '9. 매출채권\n\n(단위: 천원)\n\n| 구분 | 당기말 | 전기말 |\n| --- | --- | --- |\n'
          '| 매출채권 | 1,000 | 900 |\n\n주) 충당금 차감 전 금액\n\n'
          '10. 담보 및 우발부채\n\n차입금 300백만원을 담보하기 위하여 토지를 제공하였습니다.\n\n'
          '### 5. 재무제표 주석\n\n9. 매출채권\n\n(단위: 천원)\n\n'
          '| 구분 | 당기말 | 전기말 |\n| --- | --- | --- |\n| 매출채권 | 800 | 700 |\n')


def size(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode())


@pytest.mark.parametrize('method', ['structured', 'structure_order', 'legacy'])
def test_budget_determinism_and_source_recovery(method):
    for budget in [2, 30, 1000, 3000, 6000]:
        result = select_filing_evidence(FILING, budget_bytes=budget, method=method)
        assert size(result) <= budget
        assert result == select_filing_evidence(FILING, budget_bytes=budget, method=method)
        for record in result.get('records', []):
            assert record['text'] == ''.join(FILING[a:b] for a, b in record['source_spans'])
            assert record['projected'] is False


def test_scope_is_explicit_and_tables_keep_notes_and_units():
    result = select_filing_evidence(FILING, method='structure_order')
    tables = [r for r in result['records'] if r['kind'] == 'table']
    assert {r['scope'] for r in tables} == {'consolidated', 'standalone'}
    assert all('단위: 천원' in r['text'] for r in tables)
    assert '주)' in next(r['text'] for r in tables if r['scope'] == 'consolidated')


def test_material_note_ranks_before_accounting_policy():
    result = select_filing_evidence(FILING)
    assert '회계정책' not in result['records'][0]['text']
    assert any('담보' in r['text'] for r in result['records'])


def test_oversized_table_not_partially_shipped():
    huge = '### 3. 연결재무제표 주석\n\n9. 매출채권\n\n| 구분 | 금액 |\n| --- | --- |\n' + '| 채권 | 1000 |\n' * 2000
    result = select_filing_evidence(huge, budget_bytes=2000)
    assert result['records'] == []


def test_empty_tiny_budget_invalid_arguments():
    assert select_filing_evidence('')['records'] == []
    assert select_filing_evidence('', budget_bytes=2) == {}
    with pytest.raises(ValueError):
        select_filing_evidence('', budget_bytes=1)
    with pytest.raises(ValueError):
        select_filing_evidence('', method='anything')


def test_projected_variant_is_labeled_and_exactly_recoverable():
    text = ('### 3. 연결재무제표 주석\n\n18. 차입금\n\n(단위: 천원)\n'
            '| 구분 | 당기 | 전기 |\n| --- | --- | --- |\n'
            + ''.join(f'| 장기차입금{i} | {i+1} | {i+2} |\n' for i in range(180))
            + '| 합계 | 999 | 900 |\n\n주) 담보 제공분을 포함합니다.\n')
    result = select_filing_evidence(text, budget_bytes=6000, method='structured_projected')
    assert size(result) <= 6000
    projected = [r for r in result['records'] if r['projected']]
    assert projected
    for record in projected:
        assert record['projection_kind'] == 'selected_rows_not_complete_table'
        assert record['text'] == '\n'.join(text[a:b] for a, b in record['source_spans'])
        assert '주)' in record['text'] and '단위: 천원' in record['text']


def test_legacy_gets_same_source_derived_scope_without_new_candidates():
    text = ('## III. 재무에 관한 사항\n\n### 3. 연결재무제표 주석\n\n'
            '회사의 주요 고객에 대한 매출액은 당기 100백만원이며 전기 90백만원입니다.\n')
    records = select_filing_evidence(text, method='legacy')['records']
    assert records and records[0]['scope'] == 'consolidated'
    assert records[0]['section_path'][-1] == '3. 연결재무제표 주석'


def test_tax_note_is_eligible():
    text = '### 3. 연결재무제표 주석\n\n20. 법인세\n\n미인식 이연법인세 자산은 300백만원입니다.\n'
    result = select_filing_evidence(text)
    assert result['records'][0]['topic'] == 'tax'


def test_contractual_thresholds_outrank_synonym_dense_routine_table():
    text = ('### 3. 연결재무제표 주석\n\n9. 매출채권 및 기타채권\n\n'
            '| 매출채권 | 대손 | 손실충당금 | 매출 |\n| --- | --- | --- | --- |\n| 100 | 10 | 10 | 200 |\n\n'
            '18. 차입금\n\n차입약정에 따라 부채비율 200%를 초과할 경우 기한이익을 상실합니다.\n')
    assert '기한이익' in select_filing_evidence(text)['records'][0]['text']


def test_consolidated_is_preferred_and_short_labels_not_evidence():
    text = ('### 3. 연결재무제표 주석\n\n17. 차입금\n\n장기차입금은 200백만원이며 만기는 2028년입니다.\n\n'
            '### 5. 재무제표 주석\n\n18. 차입금\n\n장기차입금은 100백만원이며 만기는 2028년입니다.\n\n'
            '가. 자본금 변동추이\n')
    records = select_filing_evidence(text)['records']
    assert records[0]['scope'] == 'consolidated'
    assert not any(r['text'].strip() == '가. 자본금 변동추이' for r in records)


def test_adjacent_continuation_preserves_condition_without_crossing_note():
    text = ('### 3. 연결재무제표 주석\n\n18. 차입금\n\n차입금 200백만원의 상환일은 2028년입니다.\n\n'
            '다만 계약을 위반한 경우 조기상환하여야 합니다.\n\n19. 자본금\n\n자본금은 300백만원입니다.\n')
    records = select_filing_evidence(text)['records']
    borrowing = next(r for r in records if '차입금 200' in r['text'])
    assert '다만 계약' in borrowing['text'] and '자본금' not in borrowing['text']
    assert ''.join(text[a:b] for a,b in borrowing['source_spans']) == borrowing['text']


def test_stop_instead_of_filling_budget_with_negative_marginal_repetitions():
    text = '### 3. 연결재무제표 주석\n\n18. 차입금\n\n' + '\n\n'.join(
        f'차입금은 {i}백만원이며 만기는 2028년입니다.' for i in range(1, 15))
    result = select_filing_evidence(text, budget_bytes=18000)
    assert 0 < len(result['records']) < 5


def test_pointers_and_nonnumeric_recognition_policy_not_selected():
    text = ('### 3. 연결재무제표 주석\n\n회사의 매출액은 아래와 같습니다.\n\n'
            '계약상 현금흐름을 고려하여 분류를 결정하고 내재파생상품을 분리하여 인식하지 않습니다.\n')
    assert select_filing_evidence(text)['records'] == []


def test_note_bundles_preserve_complete_note_and_all_subheadings():
    text = ('## III. 재무에 관한 사항\n\n### 3. 연결재무제표 주석\n\n'
            '9. 매출채권\n\n(1) 잔액\n\n(단위: 천원)\n\n| 채권 | 금액 |\n| --- | --- |\n| 채권 | 100 |\n\n'
            '(2) 조건\n\n다만 만기 이후에는 추가 이자를 받습니다.\n\n'
            '10. 재고자산\n\n재고자산은 300백만원입니다.\n')
    result = select_filing_evidence(text, method='note_bundles')
    records = result['records']
    note = next(r for r in records if '9. 매출채권' in r['section_path'])
    assert note['kind'] == 'note_bundle'
    assert all(s in note['text'] for s in ['9. 매출채권', '(1) 잔액', '(2) 조건', '추가 이자'])
    assert '10. 재고자산' not in note['text']
    assert ''.join(text[a:b] for a,b in note['source_spans']) == note['text']


def test_note_bundles_skip_oversized_notes_instead_of_fragments():
    text = '### 3. 연결재무제표 주석\n\n9. 매출채권\n\n' + '채권 금액은 100백만원입니다.\n\n' * 100
    result = select_filing_evidence(text, method='note_bundles', budget_bytes=1000)
    assert result['records'] == [] and result['omitted_note_count'] == 1


def test_note_bundles_events_and_scope_matching_and_policy_exclusion():
    text = ('### 3. 연결재무제표 주석\n\n2. 중요한 회계정책\n\n회계정책 설명입니다.\n\n'
            '9. 매출채권\n\n채권은 300백만원입니다.\n\n'
            '### 5. 재무제표 주석\n\n10. 매출채권\n\n채권은 200백만원입니다.\n\n'
            '35. 보고기한 후 사건\n\n시설 취득 계약을 체결하였습니다.\n')
    records = select_filing_evidence(text, method='note_bundles')['records']
    assert records[0]['topic'] == 'events' and records[0]['scope'] == 'standalone'
    assert not any('회계정책' in r['text'] for r in records)
    claims = [r for r in records if r['topic'] == 'working_capital']
    assert claims[0]['scope'] == 'consolidated'


@pytest.mark.parametrize('budget', [2, 30, 3000, 6000, 12000, 18000])
def test_note_bundles_bounded_and_deterministic(budget):
    result = select_filing_evidence(FILING, method='note_bundles', budget_bytes=budget)
    assert size(result) <= budget
    assert result == select_filing_evidence(FILING, method='note_bundles', budget_bytes=budget)


def test_note_bundles_financial_income_not_customer_revenue():
    text = ('### 3. 연결재무제표 주석\n\n20. 금융수익 및 금융비용\n\n이자수익은 100백만원입니다.\n\n'
            '30. 고객과의 계약에서 생기는 수익\n\n고객 매출은 300백만원입니다.\n')
    records = select_filing_evidence(text, method='note_bundles')['records']
    assert len(records) == 1 and '고객과의 계약' in records[0]['text']


def test_note_bundles_distinguish_subsequent_disaster_and_legal_events():
    text = ('### 3. 연결재무제표 주석\n\n30. 우발채무\n\n소송이 진행 중입니다.\n\n'
            '31. 재해\n\n시설 화재가 발생하였습니다.\n\n'
            '32. 보고기간 후 사건\n\n시설 취득 계약을 체결하였습니다.\n')
    records = select_filing_evidence(text, method='note_bundles')['records']
    assert [r['topic'] for r in records] == ['events', 'disasters', 'legal_contingencies']


def test_hyphen_numbered_notes_form_separate_complete_bundles():
    text = ('### 3. 연결재무제표 주석\n\n18. 현금흐름\n\n현금 설명입니다.\n\n'
            '19-1. 우발채무 및 약정사항 (연결)\n\n계약 설명입니다.\n\n'
            '19-2. 담보제공 (연결)\n\n담보 설명입니다.\n')
    records = select_filing_evidence(text, method='note_bundles')['records']
    assert len(records) == 3
    assert all(not ('현금 설명' in r['text'] and '계약 설명' in r['text']) for r in records)
    assert next(r for r in records if '계약 설명' in r['text'])['section_path'][-1].startswith('19-1.')
