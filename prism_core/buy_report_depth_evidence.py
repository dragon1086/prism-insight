"""Opt-in BUY prompt wiring for report depth evidence (DART chapter, competitor table).

Flag ``PRISM_BUY_REPORT_DEPTH_EVIDENCE`` defaults OFF. When OFF the BUY instruction
is returned unchanged. When ON the existing F1-F4, Step 4 and PER-relative criteria
cite the KR DART chapter (5-1..5-3) and the competitor comparison table as evidence
sources. No threshold, gate, score or schema changes.
"""
from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger(__name__)

ENV_FLAG = "PRISM_BUY_REPORT_DEPTH_EVIDENCE"
MARKER = "REPORT_DEPTH_EVIDENCE:"


def buy_report_depth_evidence_enabled() -> bool:
    return os.getenv(ENV_FLAG, "").strip().lower() in {"1", "true", "yes", "on"}


def report_depth_evidence_active(instruction: str) -> bool:
    return MARKER in (instruction or "")


_PEER_KO = ("보고서에 동일 기준으로 {per} 값이 양수인 피어가 3개 이상인 '경쟁사 비교 분석' 표가 있으면 "
            "그 피어 중앙값을 비교 중앙값으로 사용할 수 있고, 없으면 2-1 기준을 사용")
_PEER_EN = ("if the report contains a '경쟁사 비교 분석' (competitor comparison) table with ≥3 peers "
            "having positive {per} on the same basis, its peer median may serve as the comparison "
            "median; otherwise use 2-1")

_LEADER = {
    "ko": ("| L — 리더 | 업종 내 리더 위치 | 2-2 기업 개요, 4 시장 |",
           "| L — 리더 | 업종 내 리더 위치 | 2-2 기업 개요, 4 시장 (+ '경쟁사 비교 분석' 표, 있을 때) |"),
    "en": ("| L — Leader | Leadership position within sector | 2-2 Overview, 4 Market |",
           "| L — Leader | Leadership position within sector | 2-2 Overview, 4 Market "
           "(+ '경쟁사 비교 분석' competitor table, if present) |"),
}
_F4 = {
    "ko": ("| F4 사업 명확성   | 사업 모델 + 경쟁우위가 보고서에서 식별됨 | 2-2 |",
           "| F4 사업 명확성   | 사업 모델 + 경쟁우위가 보고서에서 식별됨 | 2-2 (+ 경쟁사 비교 분석) |"),
    "en": ("| F4 Business clarity     | Business model + competitive edge identifiable in report | 2-2 |",
           "| F4 Business clarity     | Business model + competitive edge identifiable in report "
           "| 2-2 (+ 경쟁사 비교 분석) |"),
}
_KR_DART = {
    "ko": [
        ("| C — 분기 실적 | 최근 분기 EPS/매출 가속화 | 2-1 기업 현황 |",
         "| C — 분기 실적 | 최근 분기 EPS/매출 가속화 | 2-1 기업 현황 "
         "(+ DART 5-1 핵심 포인트: 일회성 영업외·세금 효과 등 이익의 질) |"),
        ("| A — 연간 실적 | 다년 EPS 성장, ROE, 영업이익률 | 2-1 기업 현황 |",
         "| A — 연간 실적 | 다년 EPS 성장, ROE, 영업이익률 | 2-1 기업 현황 "
         "(+ DART 5-1 핵심 포인트: 일회성 영업외·세금 효과 등 이익의 질) |"),
        ("| F1 수익성        | 최근 2개 분기 영업이익 흑자 (또는 흑자 전환 신호 명확) | 2-1 |",
         "| F1 수익성        | 최근 2개 분기 영업이익 흑자 (또는 흑자 전환 신호 명확) | 2-1 (+ DART 5-1) |"),
        ("| F2 재무 건전성   | 부채비율 < 200% OR 업종 평균 이하 | 2-1 |",
         "| F2 재무 건전성   | 부채비율 < 200% OR 업종 평균 이하 "
         "| 2-1 (+ DART 5-1·5-3: 차입 만기·담보·보증·우발채무·CB 희석) |"),
    ],
    "en": [
        ("| C — Current quarter | Recent quarterly EPS / revenue acceleration | 2-1 Company Status |",
         "| C — Current quarter | Recent quarterly EPS / revenue acceleration | 2-1 Company Status "
         "(+ DART 5-1 key points: earnings quality, one-off non-operating/tax effects) |"),
        ("| A — Annual earnings | Multi-year EPS growth, ROE, operating margin | 2-1 Company Status |",
         "| A — Annual earnings | Multi-year EPS growth, ROE, operating margin | 2-1 Company Status "
         "(+ DART 5-1 key points: earnings quality, one-off non-operating/tax effects) |"),
        ("| F1 Profitability        | Operating profit positive in latest 2 quarters "
         "(or clear turnaround signal) | 2-1 |",
         "| F1 Profitability        | Operating profit positive in latest 2 quarters "
         "(or clear turnaround signal) | 2-1 (+ DART 5-1) |"),
        ("| F2 Balance sheet        | Debt ratio < 200% OR ≤ industry average | 2-1 |",
         "| F2 Balance sheet        | Debt ratio < 200% OR ≤ industry average | 2-1 (+ DART 5-1·5-3: "
         "maturities, collateral, guarantees, contingent liabilities, CB dilution) |"),
    ],
}
_PER_ANCHORS = {
    "ko": ("30% 이상 저평가 (단순 1배 차이는 인정 X)", "업종 평균 2.5배 (극단적 고평가)"),
    "en": ("30% vs sector median per report 2-1 (small 1× differences do NOT count)",
           "2.5× industry average (extreme overvaluation)"),
}

_BULLET = {
    ("KR", "ko"): [
        f"- {MARKER} 보고서의 '5. DART 주요 재무·사업 위험 분석'(5-1 실적·현금흐름·차입과 회계 판단,",
        "  5-2 사업구조·지배구조와 자본변동, 5-3 주요 약정·기업 사건과 우발위험)과 '경쟁사 비교 분석' 표·",
        "  '경쟁사 대비 위치 분석'은 기존 F1–F4, 4단계 추가 확인, PER 상대 기준을 판정하는 근거 출처일 뿐입니다.",
        "  각 절의 '핵심 포인트'로 2-1·2-2 수치를 확인하거나 반박하고, 사용한 근거는 fundamental_check와",
        "  rationale에 절 번호로 인용하십시오. 이 자료는 새로운 통과·실패 규칙, 점수, 감점, 미진입 사유를",
        "  추가하지 않습니다. 공시상 위험 하나만으로 severity = \"high\" 리스크 이벤트가 되지 않습니다.",
        "  해당 장·표가 없으면 NOT_IN_INPUT으로 표기할 뿐 게이트가 아닙니다. 'DART 5-1'은 투자 전략 장",
        "  ('6. 투자 전략'·'6-1')과 다른 절입니다. '경쟁사 비교 분석'의 피어 중앙값은 선택된 피어의",
        "  중앙값이지 업종 평균이 아니므로 F2의 '업종 평균 이하' 판정에 대체 사용하지 마십시오.",
    ],
    ("KR", "en"): [
        f"- {MARKER} the report's '5. DART 주요 재무·사업 위험 분석' chapter (5-1 earnings/cash flow/"
        "borrowing and accounting,",
        "  5-2 business structure/governance/capital changes, 5-3 key contracts/corporate events/contingencies)",
        "  and the '경쟁사 비교 분석' competitor table with its '경쟁사 대비 위치 분석' overview are evidence",
        "  sources for the existing F1–F4, Step 4 and PER-relative criteria only. Use each subsection's",
        "  '핵심 포인트' (key points) to confirm or contradict 2-1/2-2 figures and cite the section used in",
        "  fundamental_check and rationale. They add no new pass/fail rule, score, penalty or rejection reason.",
        "  A filing risk alone is not a 'high' severity risk event. A missing chapter/table is NOT_IN_INPUT,",
        "  never a gate. DART 5-1 is a different section from the strategy chapter ('6. 투자 전략' / 6-1).",
        "  The competitor-table peer median is a selected-peer median, not an industry average; do NOT",
        "  substitute it for F2's 'industry average' branch.",
    ],
    ("US", "ko"): [
        f"- {MARKER} 보고서의 '경쟁사 비교 분석' 표(피어 중앙값 포함)와 그 해설은 기존 F4, 4단계 추가 확인,",
        "  P/E 상대 기준을 판정하는 근거 출처일 뿐입니다. 표의 수치로 2-1·2-2 서술을 확인하거나 반박하고,",
        "  사용한 근거는 fundamental_check와 rationale에 인용하십시오. 이 표는 새로운 통과·실패 규칙, 점수,",
        "  감점, 미진입 사유를 추가하지 않습니다. 표가 없으면 NOT_IN_INPUT으로 표기할 뿐 게이트가 아닙니다.",
        "  피어 중앙값은 선택된 피어의 중앙값이지 업종 평균이 아니므로 F2의 '업종 평균 이하' 판정에 대체",
        "  사용하지 마십시오.",
    ],
    ("US", "en"): [
        f"- {MARKER} the report's '경쟁사 비교 분석' competitor table (with peer median) and its commentary",
        "  are evidence sources for the existing F4, Step 4 and PE-relative criteria only. Use its figures to",
        "  confirm or contradict 2-1/2-2 statements and cite them in fundamental_check and rationale. It adds",
        "  no new pass/fail rule, score, penalty or rejection reason. A missing table is NOT_IN_INPUT, never a",
        "  gate. The peer median is a selected-peer median, not an industry average; do NOT substitute it for",
        "  F2's 'industry average' branch.",
    ],
}
_NEXT_HEADING = re.compile(r"\n\n([ \t]*)## (?:시간대별 데이터 신뢰도|Time-of-day Data Reliability)")


def _replace_once(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise ValueError(f"report-depth anchor must occur exactly once: {old[:60]!r}")
    return text.replace(old, new)


def apply_buy_report_depth_evidence(
    instruction: str, *, market: str, language: str, enabled: bool | None = None
) -> str:
    """Return ``instruction`` unchanged unless the flag is on; ``language`` is ko|en.

    Anchor drift never propagates into BUY agent construction: it is logged as
    critical and the unmodified base instruction is used (the marker is absent,
    so the ``[BUY_REPORT_DEPTH] enabled=`` log reports false).
    """
    if not (buy_report_depth_evidence_enabled() if enabled is None else enabled):
        return instruction
    try:
        return _apply_depth_edits(instruction, market=market, language=language)
    except ValueError as error:
        logger.critical('[BUY_REPORT_DEPTH] anchor drift; using base instruction: %s', error)
        return instruction


def _apply_depth_edits(instruction: str, *, market: str, language: str) -> str:
    """Strict edit application; raises ValueError on anchor drift."""
    lang = "ko" if language == "ko" else "en"
    per = "PER" if market == "KR" else ("P/E" if lang == "ko" else "PE")
    peer = (_PEER_KO if lang == "ko" else _PEER_EN).format(per=per)
    edits = list(_KR_DART[lang]) if market == "KR" else []
    edits += [_LEADER[lang], _F4[lang]]
    for anchor in _PER_ANCHORS[lang]:
        edits.append((anchor, f"{anchor[:-1]}; {peer})"))
    for old, new in edits:
        instruction = _replace_once(instruction, old, new)
    matches = list(_NEXT_HEADING.finditer(instruction))
    if len(matches) != 1:
        raise ValueError("report-depth anchor: EVIDENCE_RECONCILIATION end not unique")
    indent = matches[0].group(1)
    bullet = "\n".join(indent + line for line in _BULLET[(market, lang)])
    at = matches[0].start()
    return instruction[:at] + "\n" + bullet + instruction[at:]
