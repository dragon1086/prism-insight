"""Pure legacy paragraph selection, without experimental retrieval orchestration."""

import re

TOPICS = {
    'direct_peers_competitive_position': ('competitor', 'competes with', 'compete with', 'competition', '경쟁업체', '경쟁사', '경쟁현황', '시장점유율'),
    'business_segments': ('business segment', 'principal products', '주요 제품', '주요제품', '사업부문', '사업의 개요'),
    'earnings_estimates_guidance': ('guidance', 'outlook', 'backlog', '수주', '가이던스', '매출 전망'),
    'catalysts_risks_counterevidence': ('risk factor', 'regulatory', 'customer concentration', '위험', '리스크', '규제', '고객 집중'),
    'financial_quality_valuation': ('operating margin', 'cash flow', '영업이익률', '현금흐름', '매출액'),
    'ownership_governance': ('major shareholders', '주요 주주', '최대주주', '지배구조'),
}

def _mentions(text, word):
    if word == '수주':
        return bool(re.search(r'(?<![가-힣])(?:(?:신규|누적|총|주요)\s*)?수주', text))
    return word in text

def topic_blocks(text, max_block_bytes=2400):
    """Select full topic units with nearby headings; no claims or ranks inferred."""
    from prism_core.report_research_prefetch import _body_text

    if not isinstance(text, str):
        return []
    pieces = re.split(r'\n\s*\n', _body_text(text))
    units, heading = [], ''
    for piece in pieces:
        piece = piece.strip()
        if not piece:
            continue
        if re.fullmatch(r'#{1,6}\s+[^\n]+', piece) or (
                len(piece) < 90 and re.match(r'^(?:\([가-힣\d]+\)|[가-힣\d]+[.)]|[IVX]+[.])\s', piece)):
            if re.search(r'[A-Za-z가-힣]{2,}', piece):
                heading = piece
            continue
        # Treat table notes and related lists atomically with the prior unit.
        note = piece.lstrip(' *_\\').lower().startswith(('note', 'due to rounding', '주:', '주)', '참고', '반올림'))
        caption = units[-1].split('\n', 1)[-1] if units else ''
        unit_caption = units and len(caption) < 180 and re.search(r'(?i)(?:단위\s*[:：]|units?\s*[:：]|in (?:millions|billions))', caption)
        if units and (note and '|' in units[-1] or
                      (piece.startswith('|') and unit_caption) or
                      (re.match(r'^(?:[-*+]\s|•)', piece) and units[-1].splitlines()[0].endswith(':'))):
            units[-1] += '\n\n' + piece
        else:
            units.append((heading + '\n' if heading else '') + piece)
    output = []
    for topic, words in TOPICS.items():
        def body(unit):
            first, separator, rest = unit.partition('\n')
            heading_line = first.startswith('#') or re.match(r'^(?:\([가-힣\d]+\)|[가-힣\d]+[.)]|[IVX]+[.])\s', first)
            return rest if separator and heading_line else unit
        candidates = [unit for unit in units if any(_mentions(re.sub(r'https?://[^\s)]+', '',
                        unit if '|' in unit else body(unit)).lower(), word) for word in words)]
        # Explicit named relationship prose outranks general demand/risk mentions.
        candidates.sort(key=lambda unit: not any(word in unit.lower() for word in (
            '경쟁업체', '대표적인 경쟁', 'competes with', 'competitors include', '주요 고객')))
        seen = set()
        for unit in candidates:
            if unit in seen or len(unit.encode('utf-8')) > max_block_bytes:
                continue
            if unit.count('](') > 3 or ('|' not in unit and len(unit) < 25):
                continue
            prose = body(unit).strip()
            if re.search(r'!\[|(?i:자동.{0,8}요약|자동.{0,8}생성)', prose):
                continue
            if re.search(r'(?i)(신용등급.{0,8}정의|등급기호|rating definitions|definition of ratings)', prose):
                continue
            if re.match(r'(?i)^(?:for (?:more|additional) information|see (?:item|note)|refer to|Act,|driving\b|which\b)', prose):
                continue
            if topic == 'earnings_estimates_guidance' and re.search(r'(?i)(\b(?:accounting guidance|lease accounting|FASB|credit ratings?|ratings? (?:outlook|definitions?)|Fitch|Moody\S*)\b|회계기준|신용등급)', prose):
                continue
            if '|' not in unit:
                # PDF line/page fragments and IR titles are not standalone facts.
                if not prose.endswith(('.', '。', '!', '?', ')')):
                    continue
                if re.search(r'(?i)(forward-looking statements|actual results may differ|미래예측진술)', prose):
                    continue
            elif not re.search(r'\|\s*:?-{3,}', unit):
                continue  # never ship numeric rows without their table header
            seen.add(unit)
            output.append({'topic': topic, 'excerpt': unit, 'status': 'SOURCE_TEXT_NOT_FACT_VALIDATED'})
            if len(seen) >= 3:
                break
    return output
