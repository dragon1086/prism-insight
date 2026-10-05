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
    'BAR_FINALITY_UNKNOWN': '마감 확정 여부 미확인',
    'KR_FLOW_EVIDENCE_V1': '수급 근거 자료',
    'OHLCV': '일봉 시세',
    'MCP': '시세 조회',
    'KIS': '증권사',
}
_CODES = re.compile(r'(?<![A-Za-z0-9_])(' + '|'.join(map(re.escape, sorted(_LABELS, key=len, reverse=True))) + r')(?![A-Za-z0-9_])')
_PROTECTED = re.compile(r'(https?://[^\s<>]+|```[\s\S]*?```|\[exit-event: [^\]]+\])')
# Korean particle pairs (after final consonant, after vowel). 로/으로 is special-cased
# because a final ㄹ takes 로.
_PARTICLES = {'은': ('은', '는'), '는': ('은', '는'), '이': ('이', '가'), '가': ('이', '가'),
              '을': ('을', '를'), '를': ('을', '를'), '과': ('과', '와'), '와': ('과', '와'),
              '으로': ('으로', '로'), '로': ('으로', '로')}
_REGIMES = {'parabolic': '폭주 강세장', 'strong_bull': '강한 강세장', 'moderate_bull': '보통 강세장',
            'sideways': '횡보장', 'moderate_bear': '보통 약세장', 'strong_bear': '강한 약세장'}
_REGIME_CODE = re.compile(r'(?<![A-Za-z0-9_])(' + '|'.join(_REGIMES) + r')(?![A-Za-z0-9_])')
# Score-adjustment reasons stay English at the source (observability parses them).
_ADJUSTMENT_REASONS = (
    (re.compile(r'Same stock (?:past|historical) (?:average|avg) (loss|profit) (-?[\d.]+%)'),
     lambda m: f"같은 종목 과거 평균 {'손실' if m[1] == 'loss' else '수익'} {m[2]}"),
    (re.compile(r'(\S+) sector (?:average|avg) (loss|profit) (-?[\d.]+%)'),
     lambda m: f"{m[1]} 업종 과거 평균 {'손실' if m[2] == 'loss' else '수익'} {m[3]}"),
    (re.compile(r"Trigger '([^']+)' actual trade win rate (low|high) (\d+%) \(n=(\d+)\)"),
     lambda m: f"'{m[1]}' 트리거 실제 승률 {'낮음' if m[2] == 'low' else '높음'} {m[3]}(표본 {m[4]}건)"),
    (re.compile(r'Recent stop-out ([\d.]+)h ago \((-?[\d.]+%)\) — churn guard'),
     lambda m: f'최근 손절 {m[1]}시간 전({m[2]}) — 잦은 재진입 방지'),
)
_RENDERED = sorted(set(_LABELS.values()) | set(_REGIMES.values())
                   | {'필수 재무·사업 기준 4개', '하락 추세 차단 조건', '이동평균선', '유효 점수', '손익비 하한'},
                   key=len, reverse=True)
_LABEL_PARTICLE = re.compile(
    '(' + '|'.join(map(re.escape, _RENDERED)) + ')(으로|은|는|이|가|을|를|과|와|로)'
    r'(?=(?:부터|서|써|의)?(?![가-힣]))')


def _fix_particle(match):
    word, particle = match[1], match[2]
    last = word[-1]
    if not '가' <= last <= '힣':
        return match[0]
    final = (ord(last) - 0xAC00) % 28
    with_final, without_final = _PARTICLES[particle]
    if particle in ('로', '으로'):
        return word + ('로' if final in (0, 8) else '으로')
    return word + (with_final if final else without_final)


def _render_prose(text: str) -> str:
    text = text.replace('매수 Score:', '매수 점수:')
    text = text.replace('초분할', '분할')  # internal policy name; readers know 분할 (2026-10-06)
    text = text.replace('실제 매매:', '전략 원장 거래:')
    text = text.replace('최근 매도 주의:', '매도 이력·경험 참고:')
    text = re.sub(r'(?<=결정: )Enter\b', '매수 판단', text)
    text = re.sub(r'(?<=AI 판단: )Enter\b', '매수 판단', text)
    text = re.sub(r'(?<=결정: )(?:Skip|No Entry|no_entry)\b', '미진입', text)
    text = re.sub(r'(?<=AI 판단: )(?:Skip|No Entry|no_entry)\b', '미진입', text)
    # Prompt-plumbing words the model sometimes echoes (2026-10-01 036810 hold message).
    text = re.sub(r'(\d{1,2}:\d{2}) 주입(?:된)? (?:자료|팩트|데이터)', r'\1 기준 자료', text)
    text = re.sub(r'주입(?:된)? (?:자료|팩트|데이터)', '제공된 자료', text)
    text = re.sub(r'당일 포함[·/ ]제외 계산', '당일 봉 포함 여부', text)
    for pattern, render in _ADJUSTMENT_REASONS:
        text = pattern.sub(render, text)
    text = _REGIME_CODE.sub(lambda m: _REGIMES[m[1]], text)
    text = text.replace('effective_score', '유효 점수').replace('R/R floor', '손익비 하한')
    text = text.replace('F1~F4', '필수 재무·사업 기준 4개').replace('F1–F4', '필수 재무·사업 기준 4개')
    text = text.replace('T1·T2', '하락 추세 차단 조건').replace('T1/T2', '하락 추세 차단 조건')
    text = re.sub(r'\bn=(\d+)\b', r'표본 \1건', text)
    text = re.sub(r'(?<![A-Za-z0-9_])MA(\d+)((?:·MA\d+)+)(?![A-Za-z0-9_])',
                  lambda m: m[1] + m[2].replace('MA', '') + '일 이동평균선', text)
    text = re.sub(r'(?<![A-Za-z0-9_])MA(\d+)(?![A-Za-z0-9_])', r'\1일 이동평균선', text)
    text = _CODES.sub(lambda m: _LABELS[m[0]], text)
    # Codes read with a vowel ending (F4는, T1·T2가, MCP를) keep the model's particle;
    # re-pick it for the Korean label that replaced the code.
    return _LABEL_PARTICLE.sub(_fix_particle, text)


def render_korean_trading_message(text: str) -> str:
    """Translate known display codes while preserving URLs, IDs and numbers."""
    return ''.join(part if index % 2 else _render_prose(part)
                   for index, part in enumerate(_PROTECTED.split(text)))


def korean_rationale_style_contract(language="ko"):
    """Prompt rule for text that is sent to Korean readers as-is; wording only."""
    if language != "ko":
        return ""
    return """

## 판단 근거 문장 작성 규칙 (한국어 메시지로 그대로 발송됩니다)
- rationale·sell_reason·조정 reason은 사람이 읽는 한국어 합쇼체 문장으로 쓰십시오.
- 입력의 내부 코드·변수명·상태값(예: F1~F4, T1·T2, BAR_FINALITY_UNKNOWN, NOT_IN_INPUT, MCP, KIS, OHLCV)을
  그대로 옮기지 말고 '수익성 기준', '하락 추세 차단 조건', '마감 확정 전 가격', '시세 조회'처럼 풀어 쓰십시오.
- 입력이 전달된 방식('주입 자료', '주입된 팩트', '블록')이나 계산 내부 사정('당일 포함·제외 계산')을 쓰지 말고
  '15:00 기준 장중 누적 거래량', '당일 봉을 포함하는지에 따라'처럼 독자가 이해할 사실로 쓰십시오.
- 조사는 바로 앞 단어의 받침에 맞추십시오(예: 기준은·조건이·이동평균선을·비율로).
- 이 규칙은 표현만 정하며 수치·점수·매매 판단을 바꾸지 않습니다.
"""


_US_REASON_LABELS = (
    ('AI judgment:', 'AI 판단:'),
    ('Insufficient score', '점수 부족'),
    ('Sector concentration', '섹터 집중'),
    ('Deterministic gate:', '결정론적 게이트:'),
    ('Recent risk-exit re-entry cooldown', '최근 손절 후 재진입 대기'),
)
_AI_PART = re.compile(r'(AI 판단: [^/]+?)(\s*/|$)')


def _first_sentence(text, limit):
    text = re.sub(r'\s+', ' ', str(text or '')).strip()
    match = re.search(r'(?<=[.!?。])\s', text)
    if match and match.start() <= limit:
        text = text[:match.start()]
    return text if len(text) <= limit else text[:limit - 1].rstrip() + '…'


def hold_reason_display(reason, scenario=None, limit=100):
    """'보류 사유' line for a Korean hold message; the stored skip_reason is unchanged.

    The AI's own rejection_reason (its actual blocking rule) is shown next to the
    'AI 판단' part instead of only the decision label and score.
    """
    text = str(reason or '기타')
    for english, korean in _US_REASON_LABELS:
        text = text.replace(english, korean)
    rejection = (scenario or {}).get('rejection_reason') if isinstance(scenario, dict) else None
    short = _first_sentence(rejection, limit) if isinstance(rejection, str) else ''
    if not short:
        return text
    if _AI_PART.search(text):
        return _AI_PART.sub(lambda m: f'{m[1].rstrip()} — {short}{m[2]}', text, count=1)
    return f'{text} / AI 차단 사유: {short}'
