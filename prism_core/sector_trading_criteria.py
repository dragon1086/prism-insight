"""Sector-specific BUY criteria for KR issuers (roadmap stage 4).

The report pipeline classifies each issuer with ``classify_issuer``; this
module turns that profile into per-report BUY guidance. Only financial
institutions change, and only F2: their liquidity-order balance sheets fail
the industrial debt-ratio test by construction, so F2 is judged on a
disclosed regulatory capital ratio instead. DART periodic reports carry the
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


def buy_sector_block(profile, language='ko', mode='off'):
    """Per-report BUY guidance; '' unless the financial F2 rule is live for this issuer."""
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
