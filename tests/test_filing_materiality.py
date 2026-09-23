import pytest

from prism_core.filing_materiality import TOPICS, material_topics


@pytest.mark.parametrize(('topic', 'texts'), [
    ('earnings_quality', ('영업활동 현금흐름과 매출채권 회수', 'Inventory increased; revenue recognition unchanged.')),
    ('liquidity_covenants', ('재무약정 위반은 없습니다.', 'Debt maturity and covenant compliance.')),
    ('dilution_overhang', ('전환사채는 전액 상환하였습니다.', 'Convertible bonds contain a refixing clause.')),
    ('business_contracts', ('조건부 마일스톤 대금은 반환의무가 있습니다.', 'Customer concentration and order backlog.')),
    ('audit_contingencies', ('감사의견은 적정이며 계속기업 관련 불확실성은 없습니다.', 'No pending litigation or financial guarantees.')),
    ('asset_rnd_quality', ('개발비 자산화 및 영업권 손상검사', 'Capitalized development costs and goodwill impairment.')),
    ('related_parties', ('특수관계자 대여금은 없습니다.', 'Related party receivables were collected.')),
    ('subsequent_events', ('보고기간 후 사건은 없습니다.', 'No material subsequent events.')),
])
def test_explicit_review_topics_include_absent_and_conditional_clauses(topic, texts):
    for text in texts:
        assert topic in material_topics(text)


def test_negation_is_not_an_adverse_event_classifier():
    assert material_topics('재무약정 위반이 발생했습니다.') == material_topics('재무약정 위반은 없습니다.')
    assert material_topics('전환사채 발행 예정입니다.') == material_topics('전환사채를 전액 상환했습니다.')
    assert all(isinstance(item, str) for item in material_topics('재무약정 위반'))


@pytest.mark.parametrize('text', ['', '회사 소개와 제품 디자인', '현금 계약 이사회 주소 전화번호',
                                   'We recall our corporate history; call us today.', 'inventorying artwork'])
def test_nonfinancial_noise_and_generic_words_are_not_topics(text):
    assert material_topics(text) == ()


def test_path_context_is_used_without_inventing_scope_or_dates():
    assert material_topics('| 잔액 | 1 |', ['3. 연결재무제표 주석', '매출채권']) == ('earnings_quality',)
    assert material_topics('| 잔액 | 1 |') == ()
    assert material_topics('매출채권', ['별도재무제표']) == material_topics('매출채권')
    assert material_topics('cash', ['flow']) == ()


def test_deterministic_deduplicated_taxonomy_order_and_normalized_spaces():
    text = '보고기간 후 사건: 매출채권, 전환사채 및 매출채권. CASH\n FLOW.'
    result = material_topics(text)
    assert result == ('earnings_quality', 'dilution_overhang', 'subsequent_events')
    assert result == material_topics(text)
    assert 'earnings_quality' in material_topics('cash-flow statement')


@pytest.mark.parametrize(('text', 'path'), [(None, ()), (123, ()), ('매출채권'.encode(), ()),
                                           ('매출채권', None), ('매출채권', '주석'),
                                           ('매출채권', [None]), ('매출채권', {'a': 'b'}),
                                           ('\ud800', ()), ('매출채권', ['\ud800'])])
def test_malformed_inputs_return_empty(text, path):
    assert material_topics(text, path) == ()


def test_utf8_limit_applies_to_combined_path_and_text():
    limit = 2 * 1024 * 1024
    assert material_topics('현' * (limit // 3 + 1)) == ()
    assert material_topics('매출채권' + ' ' * limit) == ()
    assert material_topics('매출채권', ['x' * limit]) == ()
    assert material_topics('매출채권', ['주석'] * 65) == ()
    assert material_topics('매출채권' + ' ' * (limit - len('매출채권'.encode()))) == ('earnings_quality',)


def test_metadata_has_questions_and_sector_cautions_not_decision_authority():
    assert len(TOPICS) == 8
    assert all(set(meta) == {'label_ko', 'owner_section', 'question_ko', 'canslim_role', 'caution_ko'}
               for meta in TOPICS.values())
    assert all(meta['owner_section'] in {'company_status', 'company_overview', 'news_analysis'}
               for meta in TOPICS.values())
    cautions = ' '.join(meta['caution_ko'] for meta in TOPICS.values())
    assert all(word in cautions for word in ['은행', '계절', '초기'])
    assert all(meta['question_ko'] and meta['caution_ko'] for meta in TOPICS.values())


@pytest.mark.parametrize('text', [
    '영업부문', '사업부문', '부문별 정보', '제품별 매출', '지역별 매출', '주요 제품',
    '판매경로', '가동률', '생산능력', 'Operating segment information', 'operating segments',
    'revenue mix', 'capacity utilization',
])
def test_business_composition_and_execution_are_review_topics(text):
    assert 'business_contracts' in material_topics(text)


@pytest.mark.parametrize('text', [
    '보고기한 후 사건', '보고기한후 사건', '재해손실', '화재 발생 없음',
    '보험금 수령 여부와 금액은 확정되지 않았습니다.',
])
def test_event_heading_variants_and_uncertain_events_do_not_establish_dates_or_outcomes(text):
    assert material_topics(text) == ('subsequent_events',)
    assert material_topics(text, ['2025년 보고서']) == ('subsequent_events',)


def test_fire_negation_does_not_change_review_tag_or_claim_insurance_payment():
    assert material_topics('화재 발생 없음') == material_topics('화재가 발생하였습니다.')
    assert material_topics('보험금 수령 가능성은 미확정입니다.') == material_topics('보험금을 수령하였습니다.')
