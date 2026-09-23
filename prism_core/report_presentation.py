"""Reader-facing report guidance; never changes evidence or trading authority."""

import re

from prism_core.competitive_evidence import plain_evidence_fields

_EVIDENCE_LABELS = {
    "field": ("점검 항목", "Check"), "type": ("비교 주제", "Topic"),
    "entity": ("대상", "Entity"), "peer_universe": ("비교 대상", "Peers"),
    "metric": ("비교 지표", "Metric"), "value": ("수치·내용", "Value"),
    "unit": ("단위", "Unit"), "period": ("대상 기간", "Period"),
    "geography": ("대상 지역", "Geography"), "source": ("출처", "Source"),
    "publication_date": ("자료 발표일", "Publication date"),
    "status": ("근거 확인 범위", "Evidence scope"),
    "supporting excerpt": ("근거 설명", "Supporting excerpt"),
    "excerpt": ("근거 설명", "Excerpt"),
    "metric/value/unit/period/geography": ("지표·수치·단위·기간·지역", "Metric/value/unit/period/geography"),
}
_EVIDENCE_VALUES = {
    "UNKNOWN": ("확인되지 않음", "Not established"),
    "NOT_FOUND": ("이번 수집에서 근거를 확보하지 못함", "Evidence not obtained in this collection"),
    "INCOMPARABLE": ("동일 기준 비교에 필요한 근거가 부족함", "Insufficient evidence for a like-for-like comparison"),
    "SOURCE_CHECKED": ("원자료 확인 범위의 근거이며 독립 검증을 뜻하지 않음", "Original source reviewed; not independently verified"),
    "SEARCH_ONLY": ("검색 결과만 확인했으며 원자료는 확인하지 못함", "Search results only; original source not reviewed"),
    "sector_tailwind": ("업종 수요·환경", "Sector demand and conditions"),
    "price_leadership": ("주가 상대 강세", "Relative price strength"),
    "business_competitive_position": ("사업 경쟁력", "Business competitive position"),
}
_EVIDENCE_HEADINGS = {
    "Competitive Evidence": ("경쟁력 비교 근거", "Competitive comparison evidence"),
    "Competitive Evidence Handoff": ("경쟁력 비교 근거의 재사용", "Reused competitive comparison evidence"),
}
_HANDOFF_STATES = {
    "RECORD_ABSENT": ("전달할 비교 근거 기록이 확보되지 않았습니다.", "No comparison evidence record was available to reuse."),
    "RECORD_EMPTY": ("비교 근거 기록에 전달할 내용이 없었습니다.", "The comparison evidence record contained no reusable content."),
    "RECORD_AMBIGUOUS": ("비교 근거 기록의 구분이 불명확해 전달하지 못했습니다.", "Ambiguous record boundaries prevented evidence reuse."),
    "RECORD_OVERSIZE": ("비교 근거 기록이 전달 가능한 크기를 초과했습니다.", "The evidence record exceeded the supported transfer size."),
    "RECORD_MALFORMED": ("비교 근거 기록의 형식을 해석하지 못해 전달하지 못했습니다.", "The evidence record could not be reused because its format could not be interpreted."),
}


def _public_evidence_records(text, language):
    """Translate known labels only in explicit evidence sections, losslessly.

    Free prose is unchanged except one known Korean narrative topic alias.
    URLs, inline code and fenced payloads are not parsed or rewritten.
    Unknown/malformed fields remain intact rather than dropping their contents.
    """
    index = 0 if language == "ko" else 1
    active_level = None
    fence = None
    result = []
    field = re.compile(r"\*\*([a-z_]+):\*\*([ \t]*)(.*?)(?=[ \t]+/[ \t]+|$)")

    def render_field(match):
        key, whitespace, value = match.groups()
        if key not in _EVIDENCE_LABELS:
            return match.group(0)
        # Exact scalar matching cannot alter identifiers inside a URL or quote.
        scalar = value.rstrip()
        mapped = _EVIDENCE_VALUES.get(scalar)
        value = (mapped[index] + value[len(scalar):]) if mapped else value
        return f"**{_EVIDENCE_LABELS[key][index]}:**{whitespace}{value}"

    for line in text.splitlines(keepends=True):
        marker = re.match(r"^[ \t]{0,3}(`{3,}|~{3,})", line)
        if fence:
            result.append(line)
            if (marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence)
                    and not line[marker.end():].strip()):
                fence = None
            continue
        if marker:
            fence = marker[1]
            result.append(line)
            continue
        for title in ("주가·수급 지표 기준값", "시장 지표 기준값"):
            if line.startswith(title + "기준일:"):
                line = "### " + title + "\n\n" + line[len(title):]
        if line.startswith("회사: ") and "; 주요 공시; 공시일: " in line and "; 대상 기간: " in line:
            line = line.replace("; 연결 기준출처: https://", "; 연결 기준\n\n출처: https://", 1)
        heading = re.match(r"^(#{1,6})[ \t]+(.+?)[ \t]*(\r?\n)?$", line)
        if heading:
            title = heading[2]
            if title.startswith("**") and title.endswith("**"):
                title = title[2:-2]
            if active_level and len(heading[1]) <= active_level:
                active_level = None
            if title in _EVIDENCE_HEADINGS:
                active_level = len(heading[1])
                line = heading[1] + " " + _EVIDENCE_HEADINGS[title][index] + (heading[3] or "")
            result.append(line)
            continue
        if active_level and re.match(r"^ {0,3}- field:", line) and "`" not in line:
            fields = plain_evidence_fields(line)
            # Unknown field labels, duplicate/empty fields and malformed records
            # remain untouched. Do not repair or discard source text.
            explicit_keys = re.findall(r"(?:^ {0,3}- |,\s*)([a-z_ /]+):", line)
            if (fields and "type" in fields and "peer_universe" in fields
                    and all(key in _EVIDENCE_LABELS for key in explicit_keys)
                    and all(fields.values())):
                ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
                rendered = []
                for key, value in fields.items():
                    mapped = _EVIDENCE_VALUES.get(value)
                    rendered.append(f"**{_EVIDENCE_LABELS[key][index]}:** {mapped[index] if mapped else value}")
                result.append("- " + " / ".join(rendered) + ending)
                continue
        # Code spans and URLs are retained even when they contain instructions.
        chunks = re.split(r"(`+[^`]*`+|https?://[^\s<>]+)", line)
        for position in range(0, len(chunks), 2):
            if re.match(r"^ {0,3}\|", line):
                # Exact missing-value cells in ordinary financial tables are
                # reader labels too. Never rewrite URLs, code or numeric facts.
                label = '확인되지 않음' if language == 'ko' else 'Not available'
                chunks[position] = re.sub(
                    r"(?<=\|)([ \t]*)(?:UNKNOWN|MISSING|N/A)([ \t]*)(?=\|)",
                    lambda match, label=label: match[1] + label + match[2], chunks[position])
            if language == "ko":
                chunks[position] = re.sub(r"(?<![\w])price_leadership는", "주가 상대강도는", chunks[position])
            if active_level:
                chunks[position] = field.sub(render_field, chunks[position])
                chunks[position] = chunks[position].replace(
                    "원문의 상태·출처·기간을 따르세요.", "원문의 확인 범위·출처·대상 기간을 유지한 기록입니다.")
                chunks[position] = chunks[position].replace(
                    "Preserve the record's status, source and period.",
                    "The original evidence scope, source and period are retained.")
        line = "".join(chunks)
        if active_level:
            if line.startswith("Evidence ID: "):
                line = ("근거 식별자: " if index == 0 else "Evidence identifier: ") + line[len("Evidence ID: "):]
            handoff = re.fullmatch(r"Handoff status: ([A-Z_]+)(\r?\n)?", line)
            if handoff and handoff[1] in _HANDOFF_STATES:
                line = _HANDOFF_STATES[handoff[1]][index] + (handoff[2] or "")
        result.append(line)
    return "".join(result)


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
    """Present known diagnostics only at the final publication boundary."""
    phrase = "마감 확정 여부를 확인하지 못한" if language == "ko" else "final close not yet verified"
    return _public_evidence_records(text.replace("BAR_FINALITY_UNKNOWN", phrase), language)
