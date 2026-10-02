"""Sector-specific BUY criteria for KR issuers (roadmap stage 4).

The report pipeline classifies each issuer with ``classify_issuer``; this
module turns that profile into per-report BUY guidance. Financial
institutions change only F2: their liquidity-order balance sheets fail
the industrial debt-ratio test by construction, so F2 is judged on a
disclosed regulatory capital ratio instead. Non-financial holding companies
change F1 and F4 (2026-10-02): their value and competitiveness sit in core
subsidiaries and equity-method affiliates, so both are judged look-through. DART periodic reports carry the
ratio only for pure banks, so when the inputs lack it the BUY agent may look
it up within its existing single perplexity query; a ratio that is still
missing or stale fails F2.

The block is appended to the per-report BUY message, never to the shared
instruction, so every other issuer's prompt stays byte-identical. It is live
only when ``PRISM_KR_SECTOR_F2_MODE=live``; ``off`` (default, and any unknown
value) records the profile without changing the prompt.
"""
import os

MODE_ENV = 'PRISM_KR_SECTOR_F2_MODE'

# One preregistered threshold per subtype; the ratio must be disclosed, never estimated.
_F2_CAPITAL_RULES = {
    'bank': ('보통주자본비율(CET1) 11% 이상 또는 BIS 총자본비율 14% 이상',
             'common equity Tier 1 (CET1) ratio of at least 11% or total BIS capital ratio of at least 14%'),
    'financial_group': ('그룹 보통주자본비율(CET1) 11% 이상 또는 BIS 총자본비율 14% 이상',
                        'group CET1 ratio of at least 11% or total BIS capital ratio of at least 14%'),
    'insurance': ('지급여력비율(K-ICS) 150% 이상 (경과조치 적용 전·후가 함께 공시되면 적용 전 수치)',
                  'K-ICS solvency ratio of at least 150% (use the figure before transitional measures when both '
                  'are disclosed)'),
    'securities': ('순자본비율(NCR) 150% 이상', 'net capital ratio (NCR) of at least 150%'),
    'card_capital': ('조정자기자본비율 10% 이상', 'adjusted equity capital ratio of at least 10%'),
}
# Ratio names used when the BUY agent looks the figure up.
_RATIO_NAMES = {
    'bank': ('보통주자본비율(CET1)·BIS 총자본비율', 'CET1 and total BIS capital ratios'),
    'financial_group': ('그룹 보통주자본비율(CET1)·BIS 총자본비율', 'group CET1 and total BIS capital ratios'),
    'insurance': ('K-ICS 지급여력비율', 'K-ICS solvency ratio'),
    'securities': ('순자본비율(NCR)', 'net capital ratio (NCR)'),
    'card_capital': ('조정자기자본비율', 'adjusted equity capital ratio'),
}
_SUBTYPE_NAMES = {
    'bank': ('은행', 'bank'), 'financial_group': ('금융지주', 'financial holding company'),
    'insurance': ('보험', 'insurer'), 'securities': ('증권', 'securities firm'),
    'card_capital': ('카드·캐피탈', 'card/consumer-finance company'),
}


def sector_f2_mode(environ=None):
    """'live' only when explicitly enabled; anything else is 'off'."""
    value = (environ if environ is not None else os.environ).get(MODE_ENV, '')
    return 'live' if str(value).strip().lower() == 'live' else 'off'


def sector_profile_stamp(profile):
    """Stable identifiers stored with the BUY scenario for later outcome analysis."""
    if not isinstance(profile, dict) or not profile.get('kind'):
        return {'kind': None, 'subtype': None, 'basis': 'not_provided'}
    return {key: profile.get(key) for key in ('kind', 'subtype', 'basis')}


def f2_rule(profile, mode):
    """Reason code for the F2 rule actually applied to this issuer."""
    if (mode == 'live' and isinstance(profile, dict) and profile.get('kind') == 'financial'
            and profile.get('subtype') in _F2_CAPITAL_RULES):
        return 'financial_capital_ratio'
    return 'default'


def f4_rule(profile, mode):
    """Reason code for the F1/F4 rule actually applied to this issuer."""
    if mode == 'live' and isinstance(profile, dict) and profile.get('kind') == 'holding':
        return 'holding_look_through'
    return 'default'


_HOLDING_BLOCK = (
    '\n\n### 업종별 F1·F4 기준: 지주회사 (결정론적 업종 판별)\n'
    '이 종목은 DART 공식 업종명·법인명과 재무상태표 구조로 비금융 지주회사로 판별되었습니다(근거: {basis}). '
    '지주회사의 가치와 경쟁력은 핵심 자회사·관계회사에서 나오므로, 이 종목에 한해 다음처럼 판정하십시오.\n'
    '- F4 사업 명확성: 연결 자회사와 지분법 관계회사 가운데 장부금액·지분법이익·배당 기여가 큰 핵심 회사(상위 1~3개)의 '
    '사업 모델·제품·주요 고객·시장 지위를 지주회사의 사업 근거로 인정합니다. 관계회사라는 이유만으로 근거에서 빼지 마십시오. '
    '근거에는 회사명, 지분율 또는 장부금액, 이익 기여(지분법이익·배당)와 출처를 쓰십시오. 핵심 회사의 사업이 식별되고 구조적 '
    '훼손 근거가 없으면 통과입니다.\n'
    '- F1 수익성: 연결 영업이익만으로 판정하지 말고, 최근 2개 분기 지배기업 소유주 귀속 순이익 또는 영업이익+지분법손익이 '
    '흑자면 통과입니다. 자산 처분이익 같은 일회성 이익은 빼고 판단하십시오.\n'
    '- 비교: 일반 제조업 비교기업과의 영업이익률 비교는 지주회사의 수익성·경쟁력 판단 근거가 아닙니다. NAV 대비 할인, 배당·'
    '브랜드 수익은 입력에 있을 때만 참고하고 추정하지 마십시오.\n'
    '- 보완 조회: 핵심 관계회사의 경쟁 근거가 입력에 없으면 기존 `perplexity-ask` 통합 질의 최대 1회 안에 포함하십시오.\n'
    'F2·F3, buy_score 기준, 시장별 하한, 추세 게이트, 손절·R/R 등 다른 기준과 스키마는 바뀌지 않습니다.\n',
    '\n\n### Sector-specific F1/F4 rule: holding company (deterministic issuer classification)\n'
    'This issuer is classified as a non-financial holding company from its official DART industry name, legal name '
    'and balance-sheet layout (basis: {basis}). Its value and competitiveness sit in its core subsidiaries and '
    'affiliates, so for this issuer only judge as follows.\n'
    '- F4 Business clarity: accept the business model, products, key customers and market position of the core '
    'companies (top one to three by book value, equity-method income or dividends), including equity-method '
    'affiliates, as the holding company\'s business evidence. Do not drop an affiliate because it is not consolidated. '
    'Cite the company, stake or book value, earnings contribution (equity-method income, dividends) and source. Pass '
    'when the core companies\' business is identifiable and there is no evidence of structural impairment.\n'
    '- F1 Profitability: do not judge on consolidated operating profit alone; pass when owners-of-parent net income, or '
    'operating profit plus equity-method income, was positive in the latest two quarters, excluding one-off gains such '
    'as asset disposals.\n'
    '- Peers: an operating-margin comparison with industrial peers is not evidence of a holding company\'s '
    'profitability or competitiveness. Use NAV discount, dividends and brand income only when supplied; never estimate.\n'
    '- Lookup: if the core affiliates\' competitive evidence is missing, include it within the existing at-most-one '
    '`perplexity-ask` query.\n'
    'F2, F3, buy_score rules, regime floors, the trend gate, stop/R-R rules and the schema are unchanged.\n',
)


def buy_sector_block(profile, language='ko', mode='off'):
    """Per-report BUY guidance; '' unless a sector rule is live for this issuer."""
    if f4_rule(profile, mode) == 'holding_look_through':
        return _HOLDING_BLOCK[0 if language == 'ko' else 1].format(basis=profile.get('basis') or 'unknown')
    if f2_rule(profile, mode) != 'financial_capital_ratio':
        return ''
    subtype = profile['subtype']
    ko = language == 'ko'
    name = _SUBTYPE_NAMES[subtype][0 if ko else 1]
    rule = _F2_CAPITAL_RULES[subtype][0 if ko else 1]
    ratio = _RATIO_NAMES[subtype][0 if ko else 1]
    basis = profile.get('basis') or 'unknown'
    if ko:
        return (
            '\n\n### 업종별 F2 기준 (결정론적 업종 판별)\n'
            f'이 종목은 DART 공식 업종명과 재무상태표 구조로 금융업({name})으로 판별되었습니다(근거: {basis}). '
            '이 종목에 한해 1단계 펀더멘털 게이트의 F2 재무 건전성은 "부채비율 < 200% OR 업종 평균 이하" 대신 '
            '아래 규제 자본비율로 판정하십시오. 예수부채·보험계약부채 등 영업 조달 부채가 큰 금융업에서 '
            '부채비율은 건전성 잣대가 아니므로 F2 판정에 사용하지 마십시오.\n'
            f'- 확인 순서: 먼저 보고서·주입 팩트·이미 반환된 도구 결과에서 {ratio}을 찾으십시오. 없으면 '
            f'`perplexity-ask` 보완 조회에 이 회사의 최근 {ratio} 확인을 포함하십시오. 기존 통합 질의 최대 1회 안에 '
            '포함하고, 다른 보완 항목이 없으면 이 비율만으로 1회 조회할 수 있습니다. 실패·타임아웃이면 재시도하지 마십시오.\n'
            '- 인정 범위: 회사 공시·금융감독원·업권 협회 경영공시 또는 이를 인용한 보도에서 기준일이 명시된 실제 수치만 '
            '인정하고, 판단 시점으로부터 12개월 이내 기준일 중 가장 최근 값을 쓰십시오. 전망·목표치, 기준일 없는 수치, '
            '자회사 수치(금융지주는 그룹 기준)는 쓰지 마십시오. 출처끼리 값이 다르면 더 낮은 값을 쓰십시오.\n'
            f'- 통과: 위 범위에서 확인한 {rule}\n'
            '- 미달: 해당 비율이 기준 미만이거나, 입력과 조회에서 확인되지 않거나, 12개월보다 오래된 수치뿐인 경우'
            '(NOT_IN_INPUT·SOURCE_UNAVAILABLE). 없으면 없다고 쓰고, 비율을 계산하거나 추정하지 마십시오.\n'
            'F2_balance_sheet 근거에 사용한 비율·값·기준일·출처(보고서 절 또는 URL)를 쓰십시오. F1·F3·F4, buy_score 기준, 시장별 하한, '
            '추세 게이트, 손절·R/R 등 다른 기준과 스키마는 바뀌지 않습니다.\n')
    return (
        '\n\n### Sector-specific F2 rule (deterministic issuer classification)\n'
        f'This issuer is classified as a financial institution ({name}) from its official DART industry name and '
        f'balance-sheet layout (basis: {basis}). For this issuer only, judge F2 Balance sheet in the Stage 1 '
        'fundamental gate with the regulatory capital ratio below instead of "Debt ratio < 200% OR ≤ industry '
        'average". Deposits and insurance liabilities are operating funding, so do not use the debt ratio for F2.\n'
        f'- Lookup order: first search the report, injected facts and tool results already returned for the {ratio}. '
        f'If absent, include this issuer\'s latest {ratio} in the `perplexity-ask` supplemental lookup, within the '
        'existing at-most-one consolidated query (or as that one query when nothing else is missing). Do not retry a '
        'failed or timed-out lookup.\n'
        '- Accepted evidence: actual figures with a stated reference date from company filings, the Financial '
        'Supervisory Service, industry-association management disclosures or reports citing them; use the most recent '
        'reference date within 12 months of the decision. Never use forecasts or targets, undated figures, or a '
        'subsidiary\'s figure (a financial group needs the group figure). When sources differ, use the lower value.\n'
        f'- PASS: a {rule} confirmed as above\n'
        '- FAIL: the ratio is below the threshold, is not found in the inputs or the lookup, or is older than 12 '
        'months (NOT_IN_INPUT, SOURCE_UNAVAILABLE). Say it was not found; never compute or estimate the ratio.\n'
        'State the ratio, value, reference date and source (report section or URL) in F2_balance_sheet. F1, F3, F4, buy_score rules, '
        'regime floors, the trend gate, stop/R-R rules and the schema are unchanged.\n')
