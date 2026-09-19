"""Bounded source-topic labels, not materiality judgments or trading signals.

A label means only that an explicit review topic occurs in the supplied text or
section path. Negated, conditional, historical and accounting-policy passages
receive the same labels as confirmed events. This module cannot establish an
issuer, date, financial scope, amount, polarity or significance. Callers must
retain the original clauses and provenance and separately verify those facts.

Input is limited to 2 MiB combined UTF-8 text/path and 64 path components. Invalid
inputs return an empty tuple, which means no labels, NOT no investment risks.
No I/O, model calls, numerical parsing or decision authority is provided here.
"""

import re

TOPICS = {
    'earnings_quality': {
        'label_ko': '이익과 현금흐름의 질',
        'owner_section': 'company_status',
        'question_ko': '이익·영업현금흐름·매출채권·재고의 변화를 같은 기간과 범위로 비교하고 일회성 요인과 수익인식 조건을 확인합니다.',
        'canslim_role': 'C/A의 실적 성장 지속성을 보완하는 확인 항목입니다.',
        'caution_ko': '은행의 현금흐름과 제조업을 단순 비교하지 않습니다. 계절적 운전자본 증가나 초기 기업의 투자를 즉시 부실로 해석하지 않습니다.',
    },
    'liquidity_covenants': {
        'label_ko': '유동성·차입 조건',
        'owner_section': 'news_analysis',
        'question_ko': '차입 만기·담보·재무약정의 조건과 실제 위반 여부, 사용 가능한 유동성과 대응 계획을 원문에서 확인합니다.',
        'canslim_role': '실적 성장의 자금 조달 조건과 하방 위험을 보완합니다.',
        'caution_ko': '차입이나 담보 자체를 위반으로 간주하지 않습니다. 설비투자 목적과 차환 여부, 금융업의 구조적 차이를 구분합니다.',
    },
    'dilution_overhang': {
        'label_ko': '희석·잠재 주식 공급',
        'owner_section': 'news_analysis',
        'question_ko': '전환·행사·가격 조정·상환·보호예수 조건과 확정 여부, 시점, 규모 및 이미 완료된 변화를 확인합니다.',
        'canslim_role': 'S의 주식 공급 조건과 주당 실적 해석을 보완합니다.',
        'caution_ko': '발행 잔액과 상환 완료분을 구분합니다. 자금 사용 목적과 발행 조건 없이 모든 조달을 악재로 보거나 자사주 매입을 호재로 확정하지 않습니다.',
    },
    'business_contracts': {
        'label_ko': '사업구성·수주·고객·계약 조건',
        'owner_section': 'company_overview',
        'question_ko': '사업부문·제품·지역별 매출 구성과 가동률·생산능력의 기준일·기간·범위를 확인합니다. 수주잔고·고객 의존도·계약 취소·조건부 대금·반환의무의 조건과 확정 여부, 시점, 규모를 확인합니다.',
        'canslim_role': 'C/A의 매출 지속성과 N의 사업 촉매가 실제 실적으로 이어질 조건을 보완합니다.',
        'caution_ko': '초기 바이오 기업의 조건부 마일스톤을 확정 매출로 계산하지 않습니다. 수주잔고와 매출을 중복 합산하거나 고객 집중만으로 결론을 내리지 않습니다.',
    },
    'audit_contingencies': {
        'label_ko': '감사·계속기업·우발 부담',
        'owner_section': 'news_analysis',
        'question_ko': '감사·검토 의견, 계속기업 관련 설명, 소송·보증의 확정 여부와 충당·공시 범위 및 해당 기간을 확인합니다.',
        'canslim_role': '보고된 성장 수치의 신뢰성과 미확정 부담을 점검합니다.',
        'caution_ko': '핵심감사사항이나 소송의 존재를 비적정 의견 또는 확정 손실로 바꾸지 않습니다. 부재·해소·추정 불가라는 원문 조건을 유지합니다.',
    },
    'asset_rnd_quality': {
        'label_ko': '개발비·자산 가치',
        'owner_section': 'company_status',
        'question_ko': '개발비 자산화·상각·손상과 영업권의 규모, 인식 기준, 추정 가정 및 실적 영향을 확인합니다.',
        'canslim_role': 'C/A의 이익 비교 가능성과 N의 개발 성과가 갖는 불확실성을 보완합니다.',
        'caution_ko': '연구개발 지출이나 자산화를 곧바로 부실로 단정하지 않습니다. 업종과 개발 단계, 손상 발생 여부 및 회계정책 변경을 구분합니다.',
    },
    'related_parties': {
        'label_ko': '특수관계자 거래',
        'owner_section': 'company_overview',
        'question_ko': '특수관계자 거래·대여·채권·보증의 상대방, 잔액, 조건, 회수 및 연결 제거 여부를 확인합니다.',
        'canslim_role': '이익·자금 흐름과 지배구조에 대한 보완 점검이며 독립 매매 신호가 아닙니다.',
        'caution_ko': '정상적인 그룹 내 거래도 포함되므로 존재만으로 부당 거래라 단정하지 않습니다. 연결·별도 범위를 혼합하지 않습니다.',
    },
    'subsequent_events': {
        'label_ko': '보고기간 후 사건',
        'owner_section': 'news_analysis',
        'question_ko': '보고기간 말 이후 사건이나 재해·보험금 관련 설명의 발생일·공시일·확정 여부와 수정·비수정 구분, 후속 공시를 확인합니다.',
        'canslim_role': '기존 실적과 기준시점 사이의 변화 및 N에 해당할 가능성을 검토합니다.',
        'caution_ko': '주석이나 사건 관련 단어만으로 보고기간 후의 최신 사건이라 추론하지 않습니다. 과거 사건·사건 부재·미확정 계획을 새 확정 사건으로 반복하거나 보험금 지급을 가정하지 않습니다.',
    },
}

_PHRASES = {
    'earnings_quality': ('현금흐름', '현금 흐름', '매출채권', '재고자산', '일회성 이익', '일회성 손익',
                         '수익인식', '수익 인식', '매출 인식', '손실충당금', 'cash flow', 'cash-flow',
                         'receivables', 'inventory', 'inventories', 'revenue recognition',
                         'one-off gain', 'one-off gains', 'nonrecurring income'),
    'liquidity_covenants': ('차입금', '차입 만기', '차입금 만기', '재무약정', '재무 약정', '기한의 이익',
                           '담보 제공', '담보로 제공', '질권', '채무불이행', '채무 불이행', '유동성위험',
                           '유동성 위험', 'debt maturity', 'debt maturities', 'covenant', 'covenants',
                           'collateral', 'pledged assets', 'loan default', 'liquidity risk'),
    'dilution_overhang': ('전환사채', '신주인수권', '전환가액', '전환가격', '전환 가격', '리픽싱',
                         '풋옵션', '콜옵션', '보호예수', '자기주식', '자사주', '유상증자', '주식선택권',
                         'convertible bond', 'convertible bonds', 'conversion price', 'refixing',
                         'put option', 'call option', 'share buyback', 'share buybacks',
                         'lock-up', 'lockup', 'bonds with warrants', 'stock options'),
    'business_contracts': ('수주잔고', '수주 잔고', '기술이전', '계약 취소', '계약 해지', '고객 집중', '고객 의존',
                           '영업부문', '사업부문', '부문별 정보', '제품별 매출', '지역별 매출',
                           '주요 제품', '판매경로', '가동률', '생산능력', 'operating segment',
                           'operating segments', 'revenue mix', 'capacity utilization',
                           '주요 고객', '주요고객', '마일스톤', '반환의무', '반환 의무',
                           'customer concentration', 'major customer', 'major customers',
                           'order backlog', 'contract cancellation', 'contract termination',
                           'conditional milestone', 'conditional milestones', 'refund obligation'),
    'audit_contingencies': ('감사의견', '감사 의견', '검토의견', '검토 의견', '계속기업', '계속 기업',
                            '소송', '지급보증', '채무보증', '우발부채', '충당부채', '핵심감사사항',
                            'audit opinion', 'going concern', 'litigation', 'financial guarantee',
                            'financial guarantees', 'contingent liabilities', 'key audit matters'),
    'asset_rnd_quality': ('개발비', '영업권', '손상차손', '손상검사', '손상 검사', '자산 손상',
                          'goodwill', 'impairment', 'capitalized development', 'capitalised development'),
    'related_parties': ('특수관계자', '특수관계인', '특수 관계자', '특수 관계인',
                        'related party', 'related parties', 'related-party'),
    'subsequent_events': ('보고기간 후', '보고기간후', '보고기간 이후', '후속사건', '후속 사건',
                          '보고기한 후 사건', '보고기한후 사건', '재해손실', '화재', '보험금',
                          'subsequent event', 'subsequent events', 'events after the reporting period'),
}
# English phrases need token boundaries ("recall" must not match "call").
# Korean financial terms also occur with particles, so use literal matching.
_PATTERNS = {
    topic: re.compile('|'.join(
        (r'(?<![a-z0-9_])' + re.escape(phrase) + r'(?![a-z0-9_])')
        if phrase.isascii() else re.escape(phrase)
        for phrase in phrases
    ))
    for topic, phrases in _PHRASES.items()
}
_MAX_BYTES = 2 * 1024 * 1024


def material_topics(text, section_path=()):
    """Return ordered, deduplicated review labels; absent labels prove nothing."""
    if not isinstance(text, str) or not isinstance(section_path, (tuple, list)):
        return ()
    if len(section_path) > 64 or any(not isinstance(part, str) for part in section_path):
        return ()
    parts = (text, *section_path)
    # Reject excessive codepoints before UTF-8 encoding or allocating a join.
    if sum(map(len, parts)) > _MAX_BYTES:
        return ()
    try:
        if sum(len(part.encode('utf-8')) for part in parts) > _MAX_BYTES:
            return ()
    except UnicodeEncodeError:
        return ()
    normalized = tuple(' '.join(part.lower().split()) for part in parts)
    return tuple(topic for topic, pattern in _PATTERNS.items()
                 if any(pattern.search(part) for part in normalized))
