"""Deterministic KR issuer profile that selects the report's analytical lens.

Evidence: the official KSIC industry name on the DART company profile and the
latest primary financial statements. Financial institutions present assets and
liabilities in liquidity order (no 유동자산/유동부채 rows). Order-based issuers
must also show contract-asset/liability rows on the balance sheet, and loss-
making biotech must show an operating loss in every cumulative income-statement
period. KIS/KRX sector labels are not used: they file non-financial holding
companies and a shipbuilding holding company under "금융". Anything ambiguous
is general.
"""
import re

from prism_core.dart_balance_sheet import _amount, _norm, _statement_tables

GENERAL = {'kind': 'general', 'subtype': None}

# Official KSIC names observed on DART profiles, mapped to a financial subtype.
_KSIC_FINANCIAL = (
    (re.compile(r'은행'), 'bank'),
    (re.compile(r'보험업'), 'insurance'),
    (re.compile(r'증권|투자매매|투자중개'), 'securities'),
    (re.compile(r'신용카드|할부금융|여신|리스업|대부업'), 'card_capital'),
)
_HOLDING = '지주회사'
# Observed: 도로/토목/아파트/주거용 건물/건물/비주거용 건물 건설업. Specialty trades end in 공사업.
_KSIC_CONSTRUCTION = re.compile(r'건설업$')
# Observed: 기타 선박 건조업, 선박 및 수상 부유 구조물 건조업 (parts makers are
# 선박 구성 부분품 제조업); 무기 및 총포탄, 전투용 차량, 항공기용 엔진, 유인 항공기;
# 기타 엔지니어링 서비스업 (삼성E&A, 한전기술). 세미파이브's 건축기술, 엔지니어링 및
# 관련 기술 서비스업 is excluded.
_KSIC_ORDER = (
    (re.compile(r'선박.*건조업'), 'shipbuilding'),
    (re.compile(r'무기|총포탄|전투용\s*차량|항공기|우주선'), 'defense'),
    (re.compile(r'^(?!건축기술).*엔지니어링\s*서비스업$'), 'engineering'),
)
# Observed: 의학 및 약학 연구개발업, 기초 의약 물질 제조업, 의약품 제조업,
# 의료용품 및 기타 의약 관련제품 제조업, 자연과학 및 공학 연구개발업. Profitable
# issuers share these names, so an operating loss is also required.
_KSIC_BIOTECH = re.compile(r'의학\s*및\s*약학|의약|생물학|자연과학')
_NOTE_REF = re.compile(r'\(주[0-9,.~\-]*\)')
# 확정계약자산/부채 are hedge firm commitments, not contract balances.
_CONTRACT_ROW = re.compile(r'(유동|비유동)?(계약자산|계약부채|미청구공사|초과청구공사)')
# '영업손실' alone leaves the sign convention ambiguous and is not read.
_OPERATING_ROWS = {'영업이익', '영업이익(손실)', '영업손익'}


def _primary_catalog(sources):
    """Readable catalog of the single latest primary statements section, or None."""
    from prism_core.dart_source_table_evidence import pack_readable_units
    from prism_core.dart_source_tree_catalog import build_catalog

    statements = [s for s in sources if s.get('filing', {}).get('section') == 'financial_statements'
                  and s.get('filing', {}).get('role') == 'primary']
    if len(statements) != 1:
        return None
    try:
        return pack_readable_units(build_catalog(statements[0]['html'])['units'])
    except Exception:  # noqa: BLE001 - unreadable statements leave the profile general
        return None


def _statement_labels(catalog):
    """Row labels of the statement of financial position, or None."""
    try:
        _, body = _statement_tables(catalog)
    except Exception:  # noqa: BLE001
        return None
    if body is None:
        return None
    return {_norm(c['text']) for c in body['cells'] if c['col'] == 0}


def _financial_layout(labels):
    return not any(label in labels for label in ('유동자산', '유동부채'))


def _contract_rows(labels):
    return any(_CONTRACT_ROW.fullmatch(_NOTE_REF.sub('', label)) for label in labels)


def _income_verdict(table):
    """True/False when every cumulative period shows an operating loss; None if unreadable."""
    rows = [c['row'] for c in table['cells']
            if c['col'] == 0 and _NOTE_REF.sub('', _norm(c['text'])) in _OPERATING_ROWS]
    if not rows:
        return 'absent'
    if len(rows) != 1:
        return None
    cells = {(c['row'], c['col']): c for c in table['cells']}
    headers = {(col, _norm(c['text'])) for (_, col), c in cells.items() if c['tag'] == 'th' and col > 0}
    columns = range(1, table['column_count'])
    # Interim statements pair 3-month and cumulative columns; only cumulative periods count.
    cumulative = [col for col in columns if (col, '누적') in headers]
    quarter = [col for col in columns if (col, '3개월') in headers]
    if cumulative or quarter:
        # Both kinds must sit on distinct period columns, pairwise; otherwise the header is misread.
        if len(cumulative) != len(quarter) or set(cumulative) & set(quarter) or \
                len(cumulative) + len(quarter) != len(columns):
            return None
        columns = cumulative
    values = []
    for col in columns:
        cell = cells.get((rows[0], col))
        amount = _amount(cell['text']) if cell and cell.get('colspan', 1) == 1 else None
        if amount is None:
            return None
        values.append(amount)
    return bool(values) and all(value < 0 for value in values)


def _operating_loss(catalog):
    """True only when every income statement agrees on a loss in all cumulative periods."""
    from prism_core.dart_source_table_evidence import unpack_readable_units
    from prism_core.dart_source_tree_catalog import decode_table

    try:
        tables = [decode_table(u['payload'], u['path']) for u in unpack_readable_units(catalog)
                  if u['kind'] == 'table']
    except Exception:  # noqa: BLE001
        return False
    verdicts = set()
    for index, table in enumerate(tables[:-1]):
        texts = [c['text'] for c in table['cells']]
        if table['column_count'] == 1 and texts and '손익계산서' in _norm(texts[0]):
            verdict = _income_verdict(tables[index + 1])
            if verdict != 'absent':
                verdicts.add(verdict)
    return verdicts == {True}


def classify_issuer(industry_name, sources):
    """Return {'kind', 'subtype', 'industry_name', 'basis'} for the lens.

    kind: 'financial' (subtype bank/insurance/securities/card_capital/
    financial_group), 'holding' (non-financial holding company),
    'construction', 'order_backlog' (subtype shipbuilding/defense/engineering),
    'loss_biotech' or 'general'.
    """
    industry = industry_name.strip() if isinstance(industry_name, str) else ''
    catalog = _primary_catalog(sources or [])
    labels = _statement_labels(catalog) if catalog is not None else None
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
    industrial = labels is not None and not _financial_layout(labels)
    if _KSIC_CONSTRUCTION.search(industry):
        if not industrial:
            return {**GENERAL, **base, 'basis': 'ksic_construction_without_industrial_layout'}
        return {'kind': 'construction', 'subtype': None, **base, 'basis': 'ksic_construction+industrial_layout'}
    for pattern, subtype in _KSIC_ORDER:
        if pattern.search(industry):
            if not (industrial and _contract_rows(labels)):
                return {**GENERAL, **base, 'basis': 'ksic_order_without_contract_rows'}
            return {'kind': 'order_backlog', 'subtype': subtype, **base, 'basis': 'ksic_order+contract_rows'}
    if _KSIC_BIOTECH.search(industry):
        if not (industrial and _operating_loss(catalog)):
            return {**GENERAL, **base, 'basis': 'ksic_biotech_without_operating_loss'}
        return {'kind': 'loss_biotech', 'subtype': None, **base, 'basis': 'ksic_biotech+operating_loss'}
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


_ORDER_LABELS = {'shipbuilding': ('조선', 'shipbuilding'), 'defense': ('방산·항공', 'defense and aerospace'),
                 'engineering': ('엔지니어링·플랜트', 'engineering and plant')}
_ORDER_FOCUS = {
    'shipbuilding': ('후판 등 원자재 가격과 선가, 선종별 수주 구성, 인도 일정과 인도 시 잔금 유입, 선수금환급보증(RG)',
                     'steel-plate and other input prices versus newbuild prices, order mix by vessel type, delivery '
                     'schedule and delivery-time cash inflows, and refund guarantees'),
    'defense': ('국내 방위사업청 사업과 수출 계약의 비중, 수출 계약의 선수금·이행보증, 개발사업의 원가 초과와 지체상금',
                'the split between domestic procurement and export contracts, export advances and performance '
                'guarantees, and cost overruns and delay penalties on development programs'),
    'engineering': ('프로젝트별 공정률·원가율과 예정원가 변경, 해외 현장의 지역·발주처 위험, 공동도급과 이행보증',
                    'progress and cost ratios by project with estimate revisions, regional and client risk at '
                    'overseas sites, and joint-venture and performance guarantees'),
}
_CONSTRUCTION_LENS = (
    '업종 관점(건설): 이 회사는 공시상 건설업이며 공사 진행률에 따라 수익을 인식합니다. 이익은 수주잔고와 공사 '
    '원가율, 예정원가 변경에 좌우되므로 공시 범위에서 다음을 확인하세요: 주택·건축·토목·플랜트 등 부문별 매출과 '
    '원가율 추이, 예정원가 변경과 공사손실충당부채, 미청구공사(계약자산)와 초과청구공사(계약부채)의 증감과 매출 '
    '대비 수준, 회수가 늦어진 현장의 매출채권, 부동산 PF 채무보증·자금보충·책임준공 약정 같은 우발채무(보증 '
    '한도와 실행 잔액, 착공 여부, 만기 구분), 분양률과 미분양, 신규 수주와 수주잔고. 미청구공사 증가는 원가 상승이나 '
    '발주처 회수 지연 때문일 수 있으므로 원인을 공시로 확인하세요. 공시에 없는 수치는 추정하지 말고 미확인으로 '
    '남기세요.\n',
    'Sector lens (construction): the issuer is officially classified as a builder and recognizes revenue by '
    'progress. Earnings depend on the backlog, cost ratios and cost-estimate revisions, so check within the '
    'disclosures: revenue and cost ratios by housing, building, civil and plant segments; estimate revisions and '
    'onerous-contract provisions; changes in unbilled (contract assets) and overbilled (contract liabilities) '
    'balances relative to revenue; receivables on delayed sites; real-estate PF guarantees, funding-support and '
    'completion-guarantee commitments (limits versus drawn amounts, construction start, maturities); presale rates '
    'and unsold units; new orders and backlog. Rising unbilled balances may reflect cost overruns or slow client '
    'payment; confirm the cause in the filings. Leave undisclosed figures unverified; never estimate them.\n')
_BIOTECH_LENS = (
    '업종 관점(적자 바이오·신약개발): 이 회사는 공시상 의약·바이오 연구개발 업종이며 최근 비교 기간 모두 '
    '영업손실을 기록했습니다. 매출·이익 배수보다 현금 소진 속도와 파이프라인 진척을 중심으로 판단하세요. 공시 '
    '범위에서 다음을 확인하세요: 현금및현금성자산·단기금융상품 등 유동 자금과 영업활동 현금유출로 본 현금 소진 '
    '기간(기준일과 계산식 표기), 연구개발비 총액과 비용 처리 금액, 개발비 등 무형자산으로 자산화한 금액과 손상, '
    '기술이전 계약의 계약금·마일스톤·로열티와 수익 인식 시점, 임상 단계와 일정, 전환사채·유상증자 등 자금 조달과 '
    '희석, 전환사채 조기상환청구 가능 시기, 법인세비용차감전손실 등 상장 유지 요건 관련 공시. 임상 성공 확률이나 '
    '파이프라인 가치는 추정하지 말고, 공시에 없는 수치는 미확인으로 남기세요.\n',
    'Sector lens (loss-making biotech): the issuer is officially classified in pharmaceutical or life-science R&D '
    'and reported an operating loss in every recent comparative period. Judge cash burn and pipeline progress rather '
    'than revenue or earnings multiples. Check within the disclosures: liquid funds (cash, short-term financial '
    'instruments) against operating cash outflow as a cash runway (state the date and formula); total R&D spend, the '
    'amount expensed, capitalized development costs and impairment; upfront, milestone and royalty terms of licensing '
    'deals and when revenue is recognized; clinical stages and timelines; convertible bonds, rights offerings and '
    'dilution, and bondholder put dates; listing-maintenance disclosures such as pre-tax loss thresholds. Never '
    'estimate clinical success probabilities or pipeline value; leave undisclosed figures unverified.\n')


def sector_lens(profile, language='ko'):
    """Report-wide analytical lens for a non-general issuer; '' for general."""
    kind = profile.get('kind') if isinstance(profile, dict) else None
    if kind not in ('financial', 'holding', 'construction', 'order_backlog', 'loss_biotech'):
        return ''
    ko = language == 'ko'
    if kind == 'construction':
        return _CONSTRUCTION_LENS[0 if ko else 1]
    if kind == 'loss_biotech':
        return _BIOTECH_LENS[0 if ko else 1]
    if kind == 'order_backlog':
        subtype = profile.get('subtype')
        name = _ORDER_LABELS.get(subtype, ('수주산업', 'contract-based'))[0 if ko else 1]
        focus = _ORDER_FOCUS.get(subtype, ('', ''))[0 if ko else 1]
        return (
            f'업종 관점(수주산업·{name}): 이 회사는 장기 계약으로 제작·공사하며 진행률에 따라 수익을 인식합니다. '
            '계약부채(선수금·초과청구공사)는 발주처에서 미리 받은 대금으로 공사 진행으로 해소되는 의무이므로 차입금처럼 '
            '부채비율이나 단기 상환 부담으로 해석하지 마세요. 계약자산(미청구공사)은 아직 청구하지 못한 대금이므로 회수 '
            '조건과 함께 보세요. 공시 범위에서 다음을 확인하세요: 수주잔고와 신규 수주, 계약자산·계약부채의 증감과 순포지션, '
            '예정원가 변경과 공사손실충당부채, 원가율 추이, 환율 위험회피(확정계약·파생상품) 손익, '
            f'{focus}. 공시에 없는 수치는 추정하지 말고 미확인으로 남기세요.\n'
            if ko else
            f'Sector lens (contract-based, {name}): the issuer builds under long-term contracts and recognizes revenue '
            'by progress. Contract liabilities (customer advances, overbilled amounts) are settled by performing the work, '
            'not borrowings; do not read them as leverage or near-term repayment burden. Contract assets (unbilled '
            'amounts) are not yet billed; read them with their collection terms. Check within the disclosures: backlog '
            'and new orders, changes in contract assets and liabilities and the net position, cost-estimate revisions '
            'and onerous-contract provisions, cost-ratio trends, FX hedging (firm commitments, derivatives) results, '
            f'and {focus}. Leave undisclosed figures unverified; never estimate them.\n')
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
