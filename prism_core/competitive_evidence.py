"""Reuse a report's evidence prose without inventing or validating source facts.

The content hash identifies a report record, not an independently verified fact.
No network, model, account, or persistence operations belong in this module.
"""

import hashlib
import json
import re
from collections.abc import Mapping


_HEADING = re.compile(
    r"^(#{3,4})[ \t]+(?:Competitive Evidence|\*\*Competitive Evidence\*\*)[ \t]*$",
    re.MULTILINE | re.IGNORECASE,
)
_NEXT_SECTION = re.compile(r"^(#{1,4})(?:[ \t]|$)", re.MULTILINE)
_MAX_RECORD_CHARS = 12000


def _mask_fences(text: str) -> tuple[str, bool]:
    """Keep offsets while excluding fenced code from Markdown heading parsing."""
    masked = []
    fence = None
    for line in text.splitlines(keepends=True):
        match = re.match(r"^ {0,3}(`{3,}|~{3,})(.*?)(?:\r?\n)?$", line)
        was_open = fence is not None
        if match:
            marker, suffix = match.groups()
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence) and not suffix.strip():
                fence = None
        masked.append("".join("\n" if c == "\n" else " " for c in line) if was_open or fence else line)
    return "".join(masked), fence is not None


def attach_competitive_evidence(
    section_reports: Mapping[str, str],
    market: str,
    symbol: str,
    reference_date: str,
    language: str = "ko",
) -> tuple[dict[str, str], dict]:
    """Copy the news record into overview before downstream synthesis.

    Failures leave original news untouched and expose a handoff gap. Even a
    successfully copied SOURCE_CHECKED statement is only a source-agent claim.
    """
    reports = dict(section_reports)
    news = reports.get("news_analysis", "")
    visible, _ = _mask_fences(news)
    matches = list(_HEADING.finditer(visible))
    record = ""
    if not matches:
        status = "RECORD_ABSENT"
    elif len(matches) != 1:
        status = "RECORD_AMBIGUOUS"
    else:
        match = matches[0]
        next_section = next(
            (heading for heading in _NEXT_SECTION.finditer(visible, match.end())
             if len(heading.group(1)) <= len(match.group(1))),
            None,
        )
        end = next_section.start() if next_section else len(news)
        body = news[match.end():end].strip()
        record = news[match.start():end].strip()
        status = "COPIED_NOT_VALIDATED" if body else "RECORD_EMPTY"
        if len(record) > _MAX_RECORD_CHARS:
            status = "RECORD_OVERSIZE"
        elif _mask_fences(record)[1]:
            status = "RECORD_MALFORMED"

    evidence_id = None
    if status == "COPIED_NOT_VALIDATED":
        identity = json.dumps(
            [market, symbol, reference_date, record], ensure_ascii=False,
            separators=(",", ":"),
        )
        evidence_id = "CE-" + hashlib.sha256(identity.encode()).hexdigest()[:20]
        note = (
            "뉴스 섹션의 근거 기록을 그대로 재사용합니다. 근거 ID는 내용 식별자이며 "
            "독립적인 사실 검증이나 리더 판정을 뜻하지 않습니다. 원문의 상태·출처·기간을 따르세요."
            if language == "ko" else
            "Reused verbatim from news. The evidence ID identifies content, not independent "
            "fact verification or leadership. Preserve the record's status, source and period."
        )
        block = f"\n\n#### Competitive Evidence Handoff\nEvidence ID: {evidence_id}\n{note}\n\n{record}\n"
        offset = matches[0].end()
        reports["news_analysis"] = news[:offset] + f"\nEvidence ID: {evidence_id}" + news[offset:]
    else:
        note = (
            "뉴스 경쟁근거 기록을 전달하지 못했습니다. 이는 기업 경쟁력의 부재를 뜻하지 않습니다."
            if language == "ko" else
            "News competitive evidence could not be handed off. This does not imply a lack of business competitiveness."
        )
        block = f"\n\n#### Competitive Evidence Handoff\nHandoff status: {status}\n{note}\n"
    reports["company_overview"] = reports.get("company_overview", "") + block
    return reports, {
        "status": status,
        "evidence_id": evidence_id,
        "record_chars": len(record),
    }
