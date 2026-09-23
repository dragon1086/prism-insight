import pytest

from prism_core.dart_source_tree_catalog import build_catalog
from prism_core.dart_source_tree_routing import family, route_catalogs


@pytest.mark.parametrize(('title', 'expected'), [
    ('31. 우발채무 및 약정사항 (연결)', 'commitments'),
    ('29. 약정사항 및 우발부채', 'commitments'),
    ('2. 재무제표 작성기준 및 중요한 회계정책', 'policy'),
    ('25. 위험회피회계 및 위험관리', 'risk'),
    ('6. 관계기업, 공동기업투자 및 공동영업', 'investments'),
    ('99. 알 수 없는 새로운 주석', 'other'),
])
def test_generic_heading_family(title, expected):
    assert family(title) == expected


def test_primary_unknown_kept_finance_and_no_frontier():
    catalog = build_catalog('<p>99. 새로운 주석</p>' + ''.join(
        f'<p>설명 {i}</p>' for i in range(40)))
    result = route_catalogs({'s': catalog}, {'s': {'role': 'primary', 'section': 'financial_notes'}})
    assert result['selected']['company_status']['s'] == catalog['units']
    assert len(result['selected']['company_status']['s']) > 24


def test_nested_policy_selection_keeps_whole_authored_subtopic_and_context():
    catalog = build_catalog('<p>2. 중요한 회계정책</p><table class="nb"><tr><td>'
        '<p>2-6 영업권</p><p>손상차손을 환입하지 않는다.</p><p>그 밖의 조건도 보존한다.</p>'
        '<p>2-7 재고자산</p><p>일반적인 정책 반복.</p></td></tr></table>')
    result = route_catalogs({'s': catalog}, {'s': {'role': 'annual_supplement', 'section': 'financial_notes'}})
    units = result['selected']['company_status']['s']
    texts = [u['payload'] for u in units if u['kind'] == 'text']
    assert '2. 중요한 회계정책' in texts
    assert '그 밖의 조건도 보존한다.' in texts
    assert '일반적인 정책 반복.' not in texts
    assert any(u['kind'] == 'container' for u in units)


def test_annual_risk_not_removed_because_primary_same_title():
    primary = build_catalog('<p>29. 금융위험관리</p><p>반기 설명.</p>')
    annual = build_catalog('<p>30. 금융위험관리</p><p>연간 미할인 만기별 설명.</p>')
    result = route_catalogs({'p': primary, 'a': annual}, {
        'p': {'role': 'primary', 'section': 'financial_notes'},
        'a': {'role': 'annual_supplement', 'section': 'financial_notes'}})
    assert result['selected']['company_status']['a'] == annual['units']


def test_input_metadata_mismatch_rejected():
    with pytest.raises(ValueError):
        route_catalogs({'s': build_catalog('<p>x</p>')}, {})


def _annual(html):
    return route_catalogs({'s': build_catalog(html)}, {
        's': {'role': 'annual_supplement', 'section': 'financial_notes'}})


def _label(text):
    return f'<table><tr><td>{text}</td></tr><tr><td>(단위: 원)</td></tr></table>'


def test_annual_finance_disclosure_retains_periods_and_notes_not_adjacent_group():
    result = _annual('<p>30. 금융위험관리</p>'
        + _label('비파생금융부채의 만기분석에 대한 공시')
        + '<p>당기말</p><table><tr><td>100</td></tr></table>'
        + '<p>전기말</p><table><tr><td>90</td></tr></table><p>이자는 포함한다.</p>'
        + _label('환위험 민감도분석에 대한 공시') + '<p>가정 변동 10퍼센트.</p>')
    selected = result['selected']['company_status']['s']
    texts = [u['payload'] for u in selected if u['kind'] == 'text']
    assert {'당기말', '전기말', '이자는 포함한다.'} <= set(texts)
    assert '가정 변동 10퍼센트.' not in texts
    assert len([u for u in selected if u['kind'] == 'table']) == 3


def test_value_cell_cannot_impersonate_caption_and_no_label_crosses_chapter():
    result = _annual('<p>30. 금융위험관리</p>'
        + _label('환위험에 대한 공시')
        + '<table><tr><td>만기 공시</td><td>100</td></tr></table>'
        + '<p>미선택 내용</p><p>31. 차입금</p><p>독립된 새 주석.</p>')
    texts = [u['payload'] for u in result['selected']['company_status']['s']
             if u['kind'] == 'text']
    assert '미선택 내용' not in texts
    assert '독립된 새 주석.' in texts


def test_control_policy_only_overview_but_goodwill_policy_is_multiowner():
    result = _annual('<p>2. 중요한 회계정책</p><p>2-1 연결</p>'
        '<p>지배력 원칙.</p><p>2-2 영업권</p><p>손상환입 금지.</p>')
    for owner in ('company_status', 'news_analysis'):
        texts = [u['payload'] for u in result['selected'][owner]['s'] if u['kind'] == 'text']
        assert '지배력 원칙.' not in texts
        assert '손상환입 금지.' in texts
    assert any(u['payload'] == '지배력 원칙.'
               for u in result['selected']['company_overview']['s'])


@pytest.mark.parametrize('title', ['파생상품', '위험회피회계'])
def test_derivative_control_disclosures_have_multiowner_context(title):
    result = _annual(f'<p>20. {title}</p><p>계약 조건 전체.</p>')
    for owner in ('company_overview', 'news_analysis'):
        assert any(u['payload'] == '계약 조건 전체.' for u in result['selected'][owner]['s'])


def test_specialized_hybrid_chapter_retains_generic_contract_subcaptions():
    result = _annual('<p>24. 신종자본증권</p>' + _label('신종자본증권에 대한 공시')
        + '<p>발행 조건.</p>' + _label('만기 연장에 대한 기술')
        + '<p>자동연장 선택권과 이자 지급 유예 조건.</p>')
    assert any(u['payload'] == '자동연장 선택권과 이자 지급 유예 조건.'
               for u in result['selected']['company_status']['s'])


def test_annual_supplements_not_duplicate_every_operating_snapshot():
    result = _annual('<p>3. 영업부문</p><p>연간 지역별 상세.</p>'
                     '<p>4. 특수관계자</p>' + _label('특수관계자거래 잔액에 대한 공시')
                     + '<p>연간 영업 잔액.</p>' + _label('특수관계자 지분거래에 대한 공시')
                     + '<p>구조적인 지분 거래.</p>')
    overview = [u['payload'] for u in result['selected']['company_overview']['s'] if u['kind'] == 'text']
    assert '연간 지역별 상세.' not in overview
    assert '연간 영업 잔액.' not in overview
    assert '구조적인 지분 거래.' in overview


def test_overview_receives_statement_framing_without_title_inference():
    catalog = build_catalog('<table><tr><td>계속영업과 중단영업</td></tr></table>')
    result = route_catalogs({'s': catalog}, {'s': {
        'role': 'primary', 'section': 'financial_statements'}})
    assert result['selected']['company_overview']['s'] == catalog['units']
    assert result['selected']['news_analysis']['s'] == []


def test_finance_routine_scope_does_not_hide_titled_material_exceptions():
    catalog = build_catalog('<p>10. 투자부동산</p>'
        + _label('투자부동산 장부금액에 대한 공시') + '<p>일반적인 세부 금액.</p>'
        + _label('투자부동산 손상차손에 대한 공시') + '<p>평가 가정과 당기 및 전기 손상.</p>')
    result = route_catalogs({'s': catalog}, {'s': {
        'role': 'primary', 'section': 'financial_notes'}})
    for owner in ('company_status', 'news_analysis'):
        texts = [u['payload'] for u in result['selected'][owner]['s'] if u['kind'] == 'text']
        assert '일반적인 세부 금액.' not in texts
        assert '평가 가정과 당기 및 전기 손상.' in texts


def test_primary_finance_entity_summary_not_directory_overview_preserves_both():
    catalog = build_catalog('<p>1. 일반사항</p><p>1-1 종속기업 현황</p><p>주소와 지분율.</p>'
                            '<p>1-2 요약 재무정보</p><p>당기 및 전기 실적.</p>')
    result = route_catalogs({'s': catalog}, {'s': {
        'role': 'primary', 'section': 'financial_notes'}})
    texts = [u['payload'] for u in result['selected']['company_status']['s'] if u['kind'] == 'text']
    assert '주소와 지분율.' not in texts
    assert '당기 및 전기 실적.' in texts
    assert result['selected']['company_overview']['s'] == catalog['units']


def test_annual_news_keeps_revenue_reconciliation_not_every_geography():
    result = _annual('<p>3. 영업부문</p>' + _label('지역에 대한 공시')
                     + '<p>모든 지역.</p>' + _label('수익 및 현금흐름의 범주별 손익에 대한 공시')
                     + '<p>수익 차감 및 연결조정.</p>')
    texts = [u['payload'] for u in result['selected']['news_analysis']['s'] if u['kind'] == 'text']
    assert '모든 지역.' not in texts
    assert '수익 차감 및 연결조정.' in texts


def test_policy_routes_whole_main_subtopic_not_incidental_child_heading():
    result = _annual('<p>2. 중요한 회계정책</p><p>2-1 리스</p>'
        '<p>(1) 리스이용자로서의 연결실체</p><p>일반 리스 정책.</p>'
        '<p>2-2 영업권</p><p>(1) 평가 절차</p><p>손상 정책 전체.</p>')
    for owner in ('company_status', 'company_overview', 'news_analysis'):
        texts = [u['payload'] for u in result['selected'][owner]['s'] if u['kind'] == 'text']
        assert '일반 리스 정책.' not in texts
    assert any(u['payload'] == '손상 정책 전체.'
               for u in result['selected']['company_status']['s'])


def test_selected_disclosure_keeps_unlabelled_chapter_qualifiers():
    result = _annual('<p>30. 금융위험관리</p>'
        '<p>다음 표는 전기말 연결기준이며 모든 금액의 단위는 백만원입니다.</p>'
        + _label('환위험에 대한 공시') + '<p>생략하는 별도 분석.</p>'
        + _label('비파생금융부채의 만기분석에 대한 공시') + '<p>미할인 금액.</p>')
    texts = [u['payload'] for u in result['selected']['company_status']['s'] if u['kind'] == 'text']
    assert '다음 표는 전기말 연결기준이며 모든 금액의 단위는 백만원입니다.' in texts
    assert '생략하는 별도 분석.' not in texts
    assert '미할인 금액.' in texts
    qualifier = next(u for u in result['selected']['company_status']['s']
                     if u['kind'] == 'text' and u['payload'].startswith('다음 표는'))
    row = next(row for row in result['ledger'] if row['path'] == qualifier['path'])
    assert 'company_status' not in row['owners']
    assert 'company_status' in row['dependency_owners']
    assert 'company_status' in row['final_owners']


def test_repeated_chapter_titles_are_distinct_source_occurrences():
    result = _annual('<p>30. 금융위험관리</p>' + _label('환위험에 대한 공시')
        + '<p>선택하지 않는 첫 번째 주석.</p>'
        + '<p>30. 금융위험관리</p><p>별개 출처 위치의 미분할 두 번째 주석.</p>')
    texts = [u['payload'] for u in result['selected']['company_status']['s'] if u['kind'] == 'text']
    assert '선택하지 않는 첫 번째 주석.' not in texts
    assert '별개 출처 위치의 미분할 두 번째 주석.' in texts
