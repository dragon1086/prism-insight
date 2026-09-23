"""Conservative row projection with exact source spans, never numeric inference.

Only explicit-label, fixed-width tables are eligible. Implicit merged cells are
rejected; missing units or column headers are not repaired. All context and notes
already attached by the structure parser remain mandatory. This helper cannot
recover notes the upstream parser did not attach or prove row completeness.
"""
from __future__ import annotations

import re

_NUMBER = re.compile(r"^[+-]?\(?\d[\d,]*(?:\.\d+)?\)?(?:%|％|원|주)?$")
_SEPARATOR = re.compile(r"^[:\-\s]+$")
_UNIT = re.compile(r"단\s*위\s*[:：]")
_HEADER = re.compile(r"구\s*분|항\s*목|품\s*목|금\s*액|당\s*기|전\s*기|매출액|영업손익|평가전|장부금액")
_PERIOD_LABEL = re.compile(r"^(?:연도|년도|사업연도|회계연도|회계기간|기간|기준일)$")
_PRIORITY = re.compile(r"매출|영업|손익|이익|현금|채권|재고|차입|부채|담보|보증|매입|계약|법인세|자산|전환|수익|비용")


def _cells(line: str) -> list[str] | None:
    if not line.lstrip().startswith("|") or line.count("|") < 2:
        return None
    # Escaped delimiters and HTML cell merges cannot safely be reconstructed.
    if "\\|" in line or re.search(r"rowspan|colspan", line, re.IGNORECASE):
        return []
    return [c.strip() for c in line.strip().strip("|").split("|")]


def project_table(block: dict, text: str, max_bytes: int = 2400) -> dict | None:
    """Return selected original rows plus mandatory context, or safely decline.

    ``source_spans`` are half-open character offsets; joining exact source slices
    with ``\n`` reproduces ``text``. ``max_bytes`` limits projected text only, not
    metadata. Returning None means retain the original or omit it, never truncate.
    """
    if block.get("kind") != "table" or block.get("projected") or max_bytes <= 0:
        return None
    start, end = block.get("start"), block.get("end")
    if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start <= end <= len(text):
        return None
    raw = text[start:end]
    if raw != block.get("text") or len(raw.encode()) <= max_bytes or not _UNIT.search(raw):
        return None
    lines, spans, offset = [], [], start
    for line in raw.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        lines.append(content)
        spans.append([offset, offset + len(content)])
        offset += len(line)
    mandatory, data, header_seen, expected_width = set(), [], False, None
    for i, line in enumerate(lines):
        cells = _cells(line)
        if cells == []:
            return None
        if data and _UNIT.search(line):
            return None
        if cells is None or not line.strip() or _UNIT.search(line):
            mandatory.add(i)
            continue
        nonempty = [c for c in cells if c]
        if not nonempty or all(_SEPARATOR.fullmatch(c) for c in nonempty):
            mandatory.add(i)
            continue
        numeric = any(_NUMBER.fullmatch(c) for c in cells[1:])
        header_label = cells[0] in {"구분", "구 분", "항목", "품목"} or bool(_PERIOD_LABEL.fullmatch(cells[0]))
        if data and header_label:
            return None  # a second table/context must not inherit the first header
        if not data and _PERIOD_LABEL.fullmatch(cells[0]):
            mandatory.add(i)
            continue
        if not data and len(cells) > 1 and all(re.fullmatch(r'(?:19|20|21)\d{2}', cell) for cell in cells[1:]):
            mandatory.add(i)  # ambiguous year-valued headers are never discarded
            continue
        header = not data and (_HEADER.search(cells[0]) and not numeric
                              or cells[0] in {"구분", "항목", "품목"})
        if header:
            header_seen = True
            expected_width = len(cells)
            mandatory.add(i)
            continue
        if not numeric:
            # Includes all multirow headers, label-only ancestors, date captions,
            # footnotes and inline prose. Keeping them all avoids lost hierarchy.
            mandatory.add(i)
            continue
        if not header_seen or not cells[0] or _NUMBER.fullmatch(cells[0]):
            return None
        if len(cells) != expected_width:
            return None
        # Empty numerical columns could be merged/inherited values, not zeros.
        if any(not c for c in cells[1:]):
            return None
        compact_label = re.sub(r"\s+", "", cells[0])
        priority = 10 if re.search(r"합계|소계|총계|조정후", compact_label) else (
            5 if _PRIORITY.search(compact_label) else 0)
        data.append((priority, i))
    if len(data) < 2 or not header_seen:
        return None

    def render(indices):
        chosen_spans = [spans[i] for i in sorted(indices) if lines[i]]
        return "\n".join(text[a:b] for a, b in chosen_spans), chosen_spans

    result_text, _ = render(mandatory)
    if len(result_text.encode()) >= max_bytes:
        return None
    selected = set(mandatory)
    selected_rows = 0
    for _, i in sorted(data, key=lambda item: (-item[0], item[1])):
        candidate, _ = render(selected | {i})
        if len(candidate.encode()) <= max_bytes:
            selected.add(i)
            selected_rows += 1
    if not 0 < selected_rows < len(data):
        return None
    result_text, result_spans = render(selected)
    return {**block, "text": result_text, "source_spans": result_spans,
            "projected": True, "projection_kind": "selected_rows_not_complete_table",
            "selected_data_rows": selected_rows, "original_data_rows": len(data)}
