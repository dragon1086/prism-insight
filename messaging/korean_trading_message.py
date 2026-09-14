"""Presentation-only Korean labels; never mutate machine evidence or decisions."""

import re


_LABELS = {
    'NO_HISTORY': '조회한 전략 원장에 해당 종목 매도 기록 없음',
    'SOURCE_UNAVAILABLE': '자료 조회 불가(자료 없음으로 단정할 수 없음)',
    'EPS_BASIS': '주당순이익 산정 기준',
    'DENOMINATOR_DEFINITION': '산정에 사용하는 주식 수의 정의',
    'DENOMINATOR_VALUE': '산정에 사용한 실제 주식 수',
    'BASIS_SOURCE_URL': '산정 기준의 출처',
    'EPS': '주당순이익',
    'PF': '누적 이익/손실 비율',
    'SOURCE_REPORTED_YOY': '출처가 제시한 전년 동기 대비 증가율(직접 검산한 값은 아님)',
    'COMPUTED_YOY': '동일 기준 실적으로 직접 계산한 전년 동기 대비 증가율',
    'NOT_IN_INPUT': '제공된 자료 내 미확인',
    'NOT_REQUESTED': '추가 조회 미실시',
    'INCOMPARABLE': '동일 기준 비교 어려움',
    'NOT_FOUND_IN_CHECKED_SOURCES': '확인한 출처 범위에서 미발견',
    'EVIDENCE_NOT_RETAINED': '당시 원자료 미보존',
    'RETRIEVAL_OR_PARSE_FAILURE': '자료 조회 또는 해석 실패',
    'COLLECTION_OR_TRANSFER_GAP': '자료 수집 또는 전달 누락',
    'PRESENT_BUT_UNUSED': '입력에는 있으나 판단에 미반영',
    'UNSUPPORTED_POSITIVE_CLAIM': '출처로 뒷받침되지 않은 긍정 판단',
    'RATIONALE_RECONCILIATION_GAP': '상반된 근거를 대조한 설명 부족',
    'SOURCE_CHECKED': '출처 원문 확인',
    'SUBMITTED_ONLY': '주문 접수만 확인(체결 미확인)',
    'MISSING': '자료 미확인',
    'UNKNOWN': '확인되지 않은 상태',
    'ATR20': '20일 평균 변동폭',
    'F1': '수익성 기준',
    'F2': '재무 건전성 기준',
    'F3': '성장성 기준',
    'F4': '사업 모델·경쟁력 기준',
    'T1': '중기 이동평균선 이탈 조건',
    'T2': '하락 중인 단기 이동평균선 이탈 조건',
}
_CODES = re.compile(r'(?<![A-Za-z0-9_])(' + '|'.join(map(re.escape, sorted(_LABELS, key=len, reverse=True))) + r')(?![A-Za-z0-9_])')
_PROTECTED = re.compile(r'(https?://[^\s<>]+|```[\s\S]*?```|\[exit-event: [^\]]+\])')


def _render_prose(text: str) -> str:
    text = text.replace('매수 Score:', '매수 점수:')
    text = text.replace('실제 매매:', '전략 원장 거래:')
    text = text.replace('최근 매도 주의:', '매도 이력·경험 참고:')
    text = re.sub(r'(?<=결정: )Enter\b', '매수 판단', text)
    text = re.sub(r'(?<=AI 판단: )Enter\b', '매수 판단', text)
    text = re.sub(r'(?<=결정: )(?:Skip|No Entry|no_entry)\b', '미진입', text)
    text = re.sub(r'(?<=AI 판단: )(?:Skip|No Entry|no_entry)\b', '미진입', text)
    text = text.replace('F1~F4', '필수 재무·사업 기준 4개').replace('F1–F4', '필수 재무·사업 기준 4개')
    text = text.replace('T1·T2', '하락 추세 차단 조건').replace('T1/T2', '하락 추세 차단 조건')
    text = re.sub(r'\bn=(\d+)\b', r'표본 \1건', text)
    text = re.sub(r'(?<![A-Za-z0-9_])MA(\d+)((?:·MA\d+)+)(?![A-Za-z0-9_])',
                  lambda m: m[1] + m[2].replace('MA', '') + '일 이동평균선', text)
    text = re.sub(r'(?<![A-Za-z0-9_])MA(\d+)(?![A-Za-z0-9_])', r'\1일 이동평균선', text)
    # Numeric identifiers use vowel particles in model prose; Korean labels end
    # in "기준", so repair these two particles at the same presentation boundary.
    text = re.sub(r'(?<![A-Za-z0-9_])(F[1-4])를', lambda m: _LABELS[m[1]] + '을', text)
    text = re.sub(r'(?<![A-Za-z0-9_])(F[1-4])가', lambda m: _LABELS[m[1]] + '이', text)
    return _CODES.sub(lambda m: _LABELS[m[0]], text)


def render_korean_trading_message(text: str) -> str:
    """Translate known display codes while preserving URLs, IDs and numbers."""
    return ''.join(part if index % 2 else _render_prose(part)
                   for index, part in enumerate(_PROTECTED.split(text)))
