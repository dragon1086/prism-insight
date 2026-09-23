"""Final specialist classification rules, without experimental transport dependencies."""
import re

from prism_core.dart_source_tree_routing import family


def _role(owner, title, label):
    topic = family(title)
    text = title + ' ' + label
    if owner == 'company_status':
        if '재무제표' in title and '정책' not in title and '작성' not in title:
            if '자본변동' in label:
                return 'accounting_valuation'
            return 'debt_liquidity' if '재무상태' in label else 'financial_performance'
        if re.search(r'차입|사채|유동성|만기|약정|담보|보증|위험관리|위험 관리|리스', text):
            return 'debt_liquidity'
        if topic in {'segment', 'cashflow', 'other_income', 'general', 'transactions', 'related'} or re.search(
                r'손익계산|포괄손익|매출|수익|영업이익|현금흐름|현금 흐름', text):
            return 'financial_performance'
        return 'accounting_valuation'
    if owner == 'company_overview':
        if topic == 'investments' and re.search(r'재무정보|요약.*재무|영업.*성과', label):
            return 'business_segments'
        if '재무제표' in title and re.search(r'재무상태|자본변동', label):
            return 'ownership_capital'
        return ('business_segments' if topic in {'general', 'segment', 'policy', 'related'}
                or re.search(r'재무제표', title)
                else 'ownership_capital')
    return ('corporate_events' if topic in {'transactions', 'subsequent', 'segment', 'related',
                                           'cashflow', 'other_income', 'impairment',
                                           'inventory', 'tax'}
            or re.search(r'사업결합|매각|처분|구조조정|사업재편|분할|합병', text)
            else 'contingent_risks')
