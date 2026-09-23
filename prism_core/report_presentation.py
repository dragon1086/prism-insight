"""Reader-facing report guidance; never changes evidence or trading authority."""


def report_narrative_contract(language="ko"):
    if language == "ko":
        return """
## 독자를 위한 보고서 표현
사람과 AI가 함께 읽는 투자 보고서입니다. 확보한 수치와 기준 시점, 투자상 의미를 먼저 설명하고
필요한 한계는 짧게 한 번 덧붙이세요. 내부 상태 코드, 변수명, 진단 로그를 본문에 복사하지 마세요.
관측 가격이 있으면 해당 시점의 관측값으로 설명하고, 마감 확정 여부가 불분명하면
'최종 마감 수치와 차이가 있을 수 있습니다'처럼 자연스럽게 표현하세요.
최신 가격 자체가 없으면 최근 확인된 가격의 실제 날짜를 명시하세요. 과거 가격이나 장중 가격을
오늘의 종가로 바꾸어 부르거나 누락된 값을 만들어 내면 안 됩니다. 출처·날짜·단위·회계 기준과
판단에 중요한 불확실성은 유지하세요. 반복되는 자료 한계는 묶어서 설명하되 수집 실패를 숨기지 마세요.
이 표현 규칙은 수치, 매매 조건, 위험 한도나 근거의 신뢰도를 변경하지 않습니다.
"""
    return """
## Reader-facing report style
Write an investment report for human and AI readers, not a diagnostic log. Lead with available
numbers, their observation dates and investment meaning, followed by one concise qualification.
Do not copy internal status codes or variable names into the narrative. Describe an available quote
as an observation at its actual time; if finality is unverified, say it may differ from the final close.
If the latest quote is missing, date the last available observation explicitly. Never relabel historical
or intraday prices as today's close or invent missing values. Preserve sources, dates, units, accounting
bases and material uncertainty. Group repetitive limitations without hiding collection failures.
These style rules do not change numbers, trading conditions, risk limits or evidence confidence.
"""


def humanize_report_status(text, language="ko"):
    """Translate only the known finality token at the final publication boundary."""
    phrase = "마감 확정 여부를 확인하지 못한" if language == "ko" else "final close not yet verified"
    return text.replace("BAR_FINALITY_UNKNOWN", phrase)
