"""Deterministic KR issuer profile that selects the report's analytical lens.

Evidence: the official KSIC industry name on the DART company profile and the
layout of the latest statement of financial position. Financial institutions
present assets and liabilities in liquidity order (no 유동자산/유동부채 rows).
KIS/KRX sector labels are not used: they file non-financial holding companies
and a shipbuilding holding company under "금융". Anything ambiguous is general.
"""
import re

from prism_core.dart_balance_sheet import _norm, _statement_tables

GENERAL = {'kind': 'general', 'subtype': None}

# Official KSIC names observed on DART profiles, mapped to a financial subtype.
_KSIC_FINANCIAL = (
    (re.compile(r'은행'), 'bank'),
    (re.compile(r'보험업'), 'insurance'),
    (re.compile(r'증권|투자매매|투자중개'), 'securities'),
    (re.compile(r'신용카드|할부금융|여신|리스업|대부업'), 'card_capital'),
)
_HOLDING = '지주회사'


def _statement_labels(sources):
    """Row labels of the single latest statement of financial position, or None."""
    from prism_core.dart_source_table_evidence import pack_readable_units
    from prism_core.dart_source_tree_catalog import build_catalog

    statements = [s for s in sources if s.get('filing', {}).get('section') == 'financial_statements'
                  and s.get('filing', {}).get('role') == 'primary']
    if len(statements) != 1:
        return None
    try:
        catalog = pack_readable_units(build_catalog(statements[0]['html'])['units'])
        _, body = _statement_tables(catalog)
    except Exception:  # noqa: BLE001 - unreadable statements leave the profile general
        return None
    if body is None:
        return None
    return {_norm(c['text']) for c in body['cells'] if c['col'] == 0}


def _financial_layout(labels):
    return not any(label in labels for label in ('유동자산', '유동부채'))


def classify_issuer(industry_name, sources):
    """Return {'kind', 'subtype', 'industry_name', 'basis'} for the lens.

    kind: 'financial' (subtype bank/insurance/securities/card_capital/
    financial_group), 'holding' (non-financial holding company) or 'general'.
    """
    industry = industry_name.strip() if isinstance(industry_name, str) else ''
    labels = _statement_labels(sources or [])
    base = {'industry_name': industry or None}
    if industry == _HOLDING:
        if labels is None:
            return {**GENERAL, **base, 'basis': 'holding_without_statement_layout'}
        if _financial_layout(labels):
            return {'kind': 'financial', 'subtype': 'financial_group', **base, 'basis': 'ksic_holding+financial_layout'}
        return {'kind': 'holding', 'subtype': None, **base, 'basis': 'ksic_holding+industrial_layout'}
    for pattern, subtype in _KSIC_FINANCIAL:
        if pattern.search(industry):
            # Statements, when readable, must agree; an industrial layout wins.
            if labels is not None and not _financial_layout(labels):
                return {**GENERAL, **base, 'basis': 'ksic_financial+industrial_layout'}
            return {'kind': 'financial', 'subtype': subtype, **base,
                    'basis': 'ksic_financial' + ('+financial_layout' if labels is not None else '')}
    return {**GENERAL, **base, 'basis': 'ksic_general' if industry else 'ksic_unknown'}


_SUBTYPE_LABELS = {
    'bank': ('은행', 'bank'), 'insurance': ('보험', 'insurer'), 'securities': ('증권', 'securities firm'),
    'card_capital': ('카드·캐피탈', 'card/consumer-finance company'),
    'financial_group': ('금융지주', 'financial holding company'),
}
_SUBTYPE_FOCUS = {
    'bank': ('순이자이익과 순이자마진(NIM), 대출·예수금 증감과 예대율, 대손충당금 전입액과 대손비용률, '
             '손상·고정이하여신 추이와 충당금 적립 수준, 자본비율(BIS·CET1), 유동성커버리지비율(LCR), '
             '수수료 등 비이자이익 비중',
             'net interest income and margin (NIM), loan and deposit growth and the loan-to-deposit ratio, '
             'credit-loss provisions and credit cost, impaired/non-performing loans and coverage, capital ratios '
             '(BIS, CET1), liquidity coverage ratio (LCR), and the share of fee and other non-interest income'),
    'insurance': ('보험계약마진(CSM) 잔액·신계약 CSM·상각액, 보험서비스결과와 보험금융손익·투자손익의 구분, '
                  '지급여력비율(K-ICS), 금리·할인율 가정 변화와 자산·부채 금리 민감도, 손해보험의 손해율·사업비율',
                  'contractual service margin (CSM) balance, new-business CSM and release, insurance service '
                  'result versus insurance finance and investment results, solvency ratio (K-ICS), discount-rate '
                  'assumptions and asset-liability rate sensitivity, and loss and expense ratios for non-life'),
    'securities': ('수수료(위탁매매·투자은행·자산관리)와 트레이딩·운용손익의 구분, 부동산 PF 등 채무보증·우발채무, '
                   '순자본비율(NCR)과 레버리지비율, 신용공여와 파생결합증권 잔액',
                   'fees (brokerage, investment banking, asset management) versus trading and investment results, '
                   'real-estate PF guarantees and contingent exposures, net capital ratio (NCR) and leverage ratio, '
                   'margin lending and derivative-linked securities outstanding'),
    'card_capital': ('회사채·자산유동화·차입 등 조달 구조와 조달금리, 연체율과 대손비용, 카드·할부·리스 등 영업자산 증감, '
                     '자본적정성과 레버리지배율',
                     'funding mix (bonds, ABS, borrowings) and funding cost, delinquency and credit cost, growth of '
                     'card, installment and lease assets, capital adequacy and leverage multiple'),
    'financial_group': ('은행·보험·증권 등 자회사별 이익 기여와 비은행 비중, 그룹 자본비율(BIS·CET1)과 이중레버리지, '
                        '배당·자사주 등 주주환원, 그리고 은행·보험 부문 지표 가운데 공시된 항목',
                        'earnings contribution by bank, insurance, securities and other subsidiaries and the '
                        'non-bank share, group capital ratios (BIS, CET1) and double leverage, shareholder returns '
                        '(dividends, buybacks), and the disclosed bank and insurance metrics'),
}


def sector_lens(profile, language='ko'):
    """Report-wide analytical lens for a non-general issuer; '' for general."""
    if not isinstance(profile, dict) or profile.get('kind') not in ('financial', 'holding'):
        return ''
    ko = language == 'ko'
    if profile['kind'] == 'holding':
        return (
            '업종 관점(지주회사): 이 회사는 공시상 지주회사이며 재무제표는 일반 기업 형식입니다. 연결 실적은 자회사 '
            '실적의 합산이므로 지주회사 주주에게 귀속되는 가치와 같지 않습니다. 지배기업 소유주 귀속 순이익과 '
            '비지배지분을 구분하고, 상장·비상장 자회사 지분가치(NAV)와 지주회사 할인, 지주회사 자체의 수익원'
            '(배당금·브랜드 사용료·지분법손익)과 차입·이중레버리지, 자회사 지분 취득·매각 같은 포트폴리오 변화를 '
            '중심으로 판단하세요. 자회사 사업의 세부 실적은 지주회사 가치에 영향을 주는 경로와 함께 설명하세요. '
            'NAV·할인율 수치는 제공된 자료에 있을 때만 쓰고 추정하지 마세요.\n'
            if ko else
            'Sector lens (holding company): the issuer is an officially classified holding company with an industrial '
            'statement layout. Consolidated results aggregate subsidiaries and are not the value attributable to '
            'holding-company shareholders. Separate owners-of-parent profit from non-controlling interests and focus on '
            'the value of listed and unlisted stakes (NAV) and the holding discount, the parent\'s own income '
            '(dividends, brand royalties, equity-method income), parent debt and double leverage, and portfolio changes. '
            'Explain subsidiary results through their effect on holding-company value. Use NAV or discount figures only '
            'when supplied; never estimate them.\n')
    subtype = profile.get('subtype')
    name = _SUBTYPE_LABELS.get(subtype, ('금융업', 'financial institution'))[0 if ko else 1]
    focus = _SUBTYPE_FOCUS.get(subtype, ('', ''))[0 if ko else 1]
    return (
        f'업종 관점(금융업·{name}): 이 회사의 재무상태표는 유동·비유동 구분 없이 유동성 순서로 표시되는 금융업 '
        '형식입니다. 일반 기업의 부채비율·유동비율, 1년 내 만기 차입금과 현금의 단순 비교, 매출액·영업이익률 '
        '기준의 판단을 금융업 건전성·수익성의 잣대로 쓰지 마세요. 예수부채·보험계약부채·차입부채는 영업을 위한 '
        '조달 구조이므로 부채 규모 자체를 위험으로 해석하지 마세요. 대신 공시에 있는 범위에서 다음을 확인하세요: '
        f'{focus}. 공시에 없는 규제비율은 계산하거나 추정하지 말고 미확인으로 남기세요.\n'
        if ko else
        f'Sector lens (financial institution, {name}): the statement of financial position is presented in liquidity '
        'order without a current/non-current split. Do not judge soundness or profitability with industrial yardsticks '
        'such as debt-to-equity, current ratio, near-term debt versus cash, revenue or operating margin. Deposits, '
        'insurance contract liabilities and borrowings are operating funding, not risk by their size. Instead check, '
        f'within the disclosures: {focus}. Leave undisclosed regulatory ratios unverified; never compute or estimate '
        'them.\n')
