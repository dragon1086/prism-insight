"""Issuer lens comes from the official KSIC name plus the statement layout."""
import hashlib

import pytest

from prism_core.sector_profile import classify_issuer, sector_lens

FINANCIAL = ['현금및예치금', '상각후원가측정대출채권', '자산총계', '예수부채', '부채총계', '자본총계']
INDUSTRIAL = ['유동자산', '현금및현금성자산', '자산총계', '유동부채', '부채총계', '자본총계']


INTERIM = ('<tr><th rowspan="2"></th><th colspan="2">제 2 기 반기</th><th colspan="2">제 1 기 반기</th></tr>'
           '<tr><th>3개월</th><th>누적</th><th>3개월</th><th>누적</th></tr>')
ANNUAL = '<tr><th></th><th>제 2 기</th><th>제 1 기</th><th>제 0 기</th></tr>'


def income_table(label, values, header=INTERIM, title='연결 포괄손익계산서'):
    cells = ''.join(f'<td>{value}</td>' for value in values)
    return (f'<table><tr><td>{title}</td></tr><tr><td>(단위 : 원)</td></tr></table>'
            f'<table><thead>{header}</thead><tbody><tr><td>매출액</td>{"<td>1</td>" * len(values)}</tr>'
            f'<tr><td>{label}</td>{cells}</tr></tbody></table>')


def statement(labels, income=''):
    body = ''.join(f'<tr><td>{label}</td><td>1</td><td>1</td></tr>' for label in labels)
    html = ('<table><tr><td>연결 재무상태표</td></tr><tr><td>제 2 기 반기말 2026.06.30 현재</td></tr>'
            '<tr><td>제 1 기말 2025.12.31 현재</td></tr><tr><td>(단위 : 원)</td></tr></table>'
            '<table><thead><tr><th></th><th>제 2 기 반기말</th><th>제 1 기말</th></tr></thead>'
            f'<tbody>{body}</tbody></table>' + income)
    return [{'source_id': 's', 'html': html, 'sha256': hashlib.sha256(html.encode()).hexdigest(),
             'filing': {'role': 'primary', 'section': 'financial_statements', 'scope': 'consolidated'}}]


@pytest.mark.parametrize('industry,labels,kind,subtype', [
    ('지주회사', FINANCIAL, 'financial', 'financial_group'),      # KB금융, 메리츠금융지주
    ('지주회사', INDUSTRIAL, 'holding', None),                     # SK스퀘어, LG, HD한국조선해양
    ('지주회사', None, 'general', None),                           # layout unknown: no guess
    ('국내은행', FINANCIAL, 'financial', 'bank'),
    ('생명 보험업', FINANCIAL, 'financial', 'insurance'),
    ('손해 보험업', None, 'financial', 'insurance'),               # KSIC alone suffices for a clear name
    ('증권 중개업', FINANCIAL, 'financial', 'securities'),
    ('신용카드 및 할부금융업', FINANCIAL, 'financial', 'card_capital'),
    ('국내은행', INDUSTRIAL, 'general', None),                     # statements contradict: general
    ('그 외 기타 금융 지원 서비스업', INDUSTRIAL, 'general', None),  # 카카오페이
    ('포털 및 기타 인터넷 정보매개 서비스업', INDUSTRIAL, 'general', None),
    (None, FINANCIAL, 'general', None),                           # no official name: general
])
def test_classification(industry, labels, kind, subtype):
    profile = classify_issuer(industry, statement(labels) if labels else [])
    assert (profile['kind'], profile['subtype']) == (kind, subtype)


def test_lens_is_empty_for_general_and_specific_otherwise():
    assert sector_lens({'kind': 'general'}) == '' and sector_lens(None) == ''
    bank = sector_lens({'kind': 'financial', 'subtype': 'bank'})
    assert '금융업·은행' in bank and 'NIM' in bank and '부채비율' in bank and '미확인' in bank
    assert 'K-ICS' in sector_lens({'kind': 'financial', 'subtype': 'insurance'})
    assert 'NCR' in sector_lens({'kind': 'financial', 'subtype': 'securities'})
    holding = sector_lens({'kind': 'holding'})
    assert '지주회사' in holding and 'NAV' in holding and '비지배지분' in holding
    assert 'financial institution' in sector_lens({'kind': 'financial', 'subtype': 'bank'}, 'en')


def test_lens_reaches_report_references_but_not_market_or_general():
    from prism_core.kr_report_context import reference_context
    base = reference_context({'official_dart': {}})
    assert reference_context({'official_dart': {'sector_profile': {'kind': 'general'}}}) == base
    fin = {'official_dart': {'sector_profile': {'kind': 'financial', 'subtype': 'bank'}}}
    assert '업종 관점(금융업·은행)' in reference_context(fin)
    assert '업종 관점' not in reference_context(fin, market_only=True)


def test_financial_writer_replaces_industrial_net_risk_rule_only():
    from cores import dart_deep_analysis as depth
    general = depth.writer_agent('finance', 'A', '000001', '20260926').instruction
    financial = depth.writer_agent('finance', 'A', '000001', '20260926',
                                   sector={'kind': 'financial', 'subtype': 'bank'}).instruction
    assert depth.NET_RISK_RULE in general and depth.NET_RISK_RULE not in financial
    assert depth.FINANCIAL_NET_RISK_RULE in financial and depth.FINANCIAL_FINANCE_REMIT in financial
    business = depth.writer_agent('business', 'A', '000001', '20260926',
                                  sector={'kind': 'financial', 'subtype': 'bank'}).instruction
    assert depth.ROLES['business'][1] in business
    assert depth.writer_agent('risks', 'A', '000001', '20260926', sector={'kind': 'holding'}).instruction == \
        depth.writer_agent('risks', 'A', '000001', '20260926').instruction


# Official KSIC names and statement rows observed in DART filings (2026 H1).
CONTRACT = INDUSTRIAL + ['계약자산', '계약부채']
LOSS = ('영업이익(손실)', ['(6,183)', '(11,832)', '(6,188)', '(11,292)'])       # 신라젠
PROFIT_WITH_LOSS_QUARTER = ('영업이익', ['34,180', '73,480', '(426)', '60,591'])  # 알테오젠


@pytest.mark.parametrize('industry,labels,income,kind,subtype', [
    ('도로 건설업', INDUSTRIAL + ['미청구공사', '초과청구공사'], None, 'construction', None),   # 현대건설
    ('아파트 건설업', INDUSTRIAL, None, 'construction', None),                            # 대우건설
    ('도로 건설업', None, None, 'general', None),                                          # layout unknown
    ('실내건축 및 건축마무리 공사업', INDUSTRIAL + ['계약자산'], None, 'general', None),     # specialty trade
    ('선박 및 수상 부유 구조물 건조업', CONTRACT, None, 'order_backlog', 'shipbuilding'),  # 한화오션
    ('기타 선박 건조업', INDUSTRIAL + ['유동계약자산(주5)'], None, 'order_backlog', 'shipbuilding'),
    ('선박 구성 부분품 제조업', CONTRACT, None, 'general', None),                          # 기자재
    ('무기 및 총포탄 제조업', INDUSTRIAL + ['유동계약자산', '유동계약부채'], None, 'order_backlog', 'defense'),  # LIG
    ('전투용 차량 제조업', CONTRACT, None, 'order_backlog', 'defense'),                    # 현대로템
    ('항공기용 엔진 제조업', CONTRACT, None, 'order_backlog', 'defense'),                  # 한화에어로스페이스
    ('무기 및 총포탄 제조업', INDUSTRIAL, None, 'general', None),                          # no contract rows
    ('기타 엔지니어링 서비스업', INDUSTRIAL + ['미청구공사(주18)', '확정계약자산(주29)'], None,
     'order_backlog', 'engineering'),                                                   # 삼성E&A
    ('기타 엔지니어링 서비스업', INDUSTRIAL + ['확정계약자산', '확정계약부채'], None, 'general', None),  # hedges only
    ('건축기술, 엔지니어링 및 관련 기술 서비스업', CONTRACT, None, 'general', None),         # 세미파이브
    ('기타 기관 및 터빈 제조업', CONTRACT, None, 'general', None),                         # 두산에너빌리티
    ('의학 및 약학 연구개발업', INDUSTRIAL, LOSS, 'loss_biotech', None),                   # 신라젠
    ('의학 및 약학 연구개발업', INDUSTRIAL, PROFIT_WITH_LOSS_QUARTER, 'general', None),    # 알테오젠
    ('의료용품 및 기타 의약 관련제품 제조업', INDUSTRIAL, LOSS, 'loss_biotech', None),      # HLB
    ('의학 및 약학 연구개발업', INDUSTRIAL, ('영업이익(손실)', ['(1)', '(2)', '(3)', '4']), 'general', None),
    ('의학 및 약학 연구개발업', INDUSTRIAL, ('영업손실', ['(1)', '(2)', '(3)', '(4)']), 'general', None),  # sign ambiguous
    ('의학 및 약학 연구개발업', INDUSTRIAL, None, 'general', None),                        # no income statement
    ('포털 및 기타 인터넷 정보매개 서비스업', INDUSTRIAL, LOSS, 'general', None),          # loss, not biotech
    ('기타 전문 도매업', CONTRACT, None, 'general', None),                                 # 삼성물산
])
def test_stage2_classification(industry, labels, income, kind, subtype):
    html = income_table(*income) if income else ''
    profile = classify_issuer(industry, statement(labels, html) if labels else [])
    assert (profile['kind'], profile['subtype']) == (kind, subtype)


def test_biotech_loss_reads_every_annual_period_and_agreeing_statements():
    annual_loss = income_table('영업이익(손실)', ['(25,587)', '(26,768)', '(21,345)'], ANNUAL)
    assert classify_issuer('의학 및 약학 연구개발업', statement(INDUSTRIAL, annual_loss))['kind'] == 'loss_biotech'
    recovered = income_table('영업이익(손실)', ['106,900', '25,403', '(9,736)'], ANNUAL)
    assert classify_issuer('의학 및 약학 연구개발업', statement(INDUSTRIAL, recovered))['kind'] == 'general'
    # 손익계산서 and 포괄손익계산서 must agree.
    both = LOSS[0], LOSS[1]
    conflict = income_table(*both, title='연결 손익계산서') + income_table('영업이익(손실)', ['1', '1', '1', '1'])
    assert classify_issuer('의학 및 약학 연구개발업', statement(INDUSTRIAL, conflict))['kind'] == 'general'
    agree = income_table(*both, title='연결 손익계산서') + income_table(*both)
    assert classify_issuer('의학 및 약학 연구개발업', statement(INDUSTRIAL, agree))['kind'] == 'loss_biotech'
    # A header whose 3-month/cumulative labels do not map onto period columns is not read.
    shifted = INTERIM.replace('<th rowspan="2"></th>', '<th></th>')
    misread = income_table('영업이익(손실)', ['(1)', '(2)', '(3)', '4'], shifted)
    assert classify_issuer('의학 및 약학 연구개발업', statement(INDUSTRIAL, misread))['kind'] == 'general'


def test_stage2_lenses():
    construction = sector_lens({'kind': 'construction'})
    assert '업종 관점(건설)' in construction and 'PF' in construction and '미청구공사' in construction
    ship = sector_lens({'kind': 'order_backlog', 'subtype': 'shipbuilding'})
    assert '수주산업·조선' in ship and '계약부채' in ship and 'RG' in ship
    assert '지체상금' in sector_lens({'kind': 'order_backlog', 'subtype': 'defense'})
    assert '공정률' in sector_lens({'kind': 'order_backlog', 'subtype': 'engineering'})
    bio = sector_lens({'kind': 'loss_biotech'})
    assert '적자 바이오' in bio and '현금 소진' in bio and '자산화' in bio
    for profile in ({'kind': 'construction'}, {'kind': 'order_backlog', 'subtype': 'defense'}, {'kind': 'loss_biotech'}):
        english = sector_lens(profile, 'en')
        assert english.startswith('Sector lens') and not any('\uac00' <= ch <= '\ud7a3' for ch in english)


def test_stage2_writer_rules():
    from cores import dart_deep_analysis as depth
    general = depth.writer_agent('finance', 'A', '000001', '20260926').instruction
    bio = depth.writer_agent('finance', 'A', '000001', '20260926', sector={'kind': 'loss_biotech'}).instruction
    assert depth.BIOTECH_NET_RISK_RULE in bio and depth.NET_RISK_RULE not in bio
    order = depth.writer_agent('risks', 'A', '000001', '20260926',
                               sector={'kind': 'order_backlog', 'subtype': 'shipbuilding'}).instruction
    assert depth.NET_RISK_RULE + depth.ORDER_NET_RISK_NOTE in order
    assert depth.ORDER_NET_RISK_NOTE not in general
    # Construction keeps the general writer rules; its lens arrives via the shared reference.
    assert depth.writer_agent('finance', 'A', '000001', '20260926', sector={'kind': 'construction'}).instruction == general
    bio_en = depth.writer_agent('finance', 'A', '000001', '20260926', 'en', {'kind': 'loss_biotech'}).instruction
    assert 'cash runway' in bio_en and 'interest expense vs operating profit' not in bio_en


def test_stage2_lens_reaches_report_references():
    from prism_core.kr_report_context import reference_context
    bio = {'official_dart': {'sector_profile': {'kind': 'loss_biotech'}}}
    assert '업종 관점(적자 바이오' in reference_context(bio)
    assert '업종 관점' not in reference_context(bio, market_only=True)
