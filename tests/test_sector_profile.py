"""Issuer lens comes from the official KSIC name plus the statement layout."""
import hashlib

import pytest

from prism_core.sector_profile import classify_issuer, sector_lens

FINANCIAL = ['현금및예치금', '상각후원가측정대출채권', '자산총계', '예수부채', '부채총계', '자본총계']
INDUSTRIAL = ['유동자산', '현금및현금성자산', '자산총계', '유동부채', '부채총계', '자본총계']


def statement(labels):
    body = ''.join(f'<tr><td>{label}</td><td>1</td><td>1</td></tr>' for label in labels)
    html = ('<table><tr><td>연결 재무상태표</td></tr><tr><td>제 2 기 반기말 2026.06.30 현재</td></tr>'
            '<tr><td>제 1 기말 2025.12.31 현재</td></tr><tr><td>(단위 : 원)</td></tr></table>'
            '<table><thead><tr><th></th><th>제 2 기 반기말</th><th>제 1 기말</th></tr></thead>'
            f'<tbody>{body}</tbody></table>')
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
