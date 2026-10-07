"""Deterministic quarterly results table for the US company-status report (section 2-1).

KR mirror of prism_core.kr_financial_summary.render_quarterly_facts (#851). The BUY profitability gate (F1) needs
the operating income of the latest two quarters. The US report writer received the raw yfinance statements but
wrote only annual operating income and quarterly net income/EPS, so the BUY agent (and the MCP-less re-entry
recheck) could not verify F1 even for clearly profitable companies (2026-10-07). The table is computed by code
and appended to the report section, so it no longer depends on what the writer copies.
"""
from __future__ import annotations

import math

MAX_QUARTERS = 5
ROWS = {
    "revenue": ("Total Revenue", "Operating Revenue"),
    "operating_income": ("Operating Income", "Total Operating Income As Reported"),
    "net_income": ("Net Income Common Stockholders", "Net Income"),
    "diluted_eps": ("Diluted EPS",),
}


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) or math.isinf(number) else number


def quarterly_rows(q_income) -> list[dict]:
    """Up to the latest MAX_QUARTERS reported quarters, oldest first, from yfinance quarterly_income_stmt.

    Quarters with no value in any tracked row are dropped. Returns [] when nothing is usable.
    """
    if q_income is None or getattr(q_income, "empty", True):
        return []
    rows = []
    for column in q_income.columns:
        out = {"period": str(getattr(column, "date", lambda: column)())[:10]}
        for key, labels in ROWS.items():
            label = next((name for name in labels if name in q_income.index), None)
            out[key] = _number(q_income.at[label, column]) if label else None
        if any(out[key] is not None for key in ROWS):
            rows.append(out)
    rows.sort(key=lambda row: row["period"])
    return rows[-MAX_QUARTERS:]


def _money(value):
    return "N/A" if value is None else f"{value / 1e6:,.1f}"


def _eps(value):
    return "N/A" if value is None else f"{value:,.2f}"


def _sign(value, ko):
    if value is None:
        return "N/A"
    if ko:
        return "흑자" if value > 0 else ("적자" if value < 0 else "0")
    return "profit" if value > 0 else ("loss" if value < 0 else "zero")


def _prior_year(rows, latest):
    """The quarter that ended about one year before the latest one (360-370 days), else None."""
    from datetime import date
    end = date.fromisoformat(latest["period"])
    for row in rows:
        gap = (end - date.fromisoformat(row["period"])).days
        if 355 <= gap <= 375:
            return row
    return None


def _change(prior, now, ko):
    if prior is None or now is None:
        return "N/A"
    if prior > 0:
        return f"{(now / prior - 1) * 100:+.1f}%"
    if ko:
        return "흑자 전환" if now > 0 else "적자 지속"
    return "turned profitable" if now > 0 else "loss continued"


def render_us_quarterly_facts(rows: list[dict], language: str = "ko") -> str:
    """Markdown table + deterministic lines (latest two quarters' operating income, YoY). "" without rows."""
    if not rows:
        return ""
    ko = language == "ko"
    if ko:
        lines = ["### 분기 실적 표 (코드 사전 수집, 확정 분기만)",
                 "분기 손익계산서 기준 · 금액 단위 백만 달러 · EPS 단위 달러(희석) · 분기 = 회계 분기 말일",
                 "| 분기 말일 | 매출 | 영업이익 | 순이익(보통주) | 희석 EPS |", "|---|---:|---:|---:|---:|"]
    else:
        lines = ["### Quarterly results (pre-collected by code, reported quarters only)",
                 "Quarterly income statement · money in USD millions · diluted EPS in USD · quarter = fiscal quarter end",
                 "| Quarter end | Revenue | Operating income | Net income (common) | Diluted EPS |",
                 "|---|---:|---:|---:|---:|"]
    for row in rows:
        lines.append(f"| {row['period']} | {_money(row['revenue'])} | {_money(row['operating_income'])} | "
                     f"{_money(row['net_income'])} | {_eps(row['diluted_eps'])} |")
    last_two = rows[-2:]
    if all(row["operating_income"] is None for row in rows):
        lines.append("- 이 회사의 분기 손익계산서에는 영업이익 항목이 없습니다(금융업 등 손익 구조). 영업이익 기준은 "
                     "이 표로 판정할 수 없습니다." if ko else
                     "- This company's quarterly income statement has no operating income line (e.g. financials); "
                     "the operating income test cannot be judged from this table.")
    else:
        parts = [f"{row['period']} {_money(row['operating_income'])} ({_sign(row['operating_income'], ko)})"
                 for row in last_two]
        signs = {_sign(row["operating_income"], ko) for row in last_two}
        if ko:
            verdict = ("두 분기 모두 영업흑자" if signs == {"흑자"} and len(last_two) == 2 else
                       "두 분기 모두 영업적자" if signs == {"적자"} and len(last_two) == 2 else
                       "확인 불가(N/A 포함)" if "N/A" in signs or len(last_two) < 2 else "한 분기만 영업흑자")
            lines.append(f"- 최근 2개 분기 영업이익(백만 달러): {' / '.join(parts)} → {verdict}")
        else:
            verdict = ("operating profit in both quarters" if signs == {"profit"} and len(last_two) == 2 else
                       "operating loss in both quarters" if signs == {"loss"} and len(last_two) == 2 else
                       "cannot confirm (N/A)" if "N/A" in signs or len(last_two) < 2 else "operating profit in one quarter only")
            lines.append(f"- Operating income of the latest two quarters (USD M): {' / '.join(parts)} → {verdict}")
    latest = rows[-1]
    prior = _prior_year(rows, latest)
    if prior is None:
        lines.append("- 최근 분기의 전년 동기 행이 표에 없어 전년 동기 비교는 UNKNOWN입니다." if ko else
                     "- The prior-year quarter of the latest quarter is not in the table; YoY is UNKNOWN.")
    elif ko:
        lines.append(f"- 최근 분기 {latest['period']} vs 전년 동기 {prior['period']}: 매출 "
                     f"{_change(prior['revenue'], latest['revenue'], ko)}, 영업이익 "
                     f"{_change(prior['operating_income'], latest['operating_income'], ko)}, 희석 EPS "
                     f"{_eps(prior['diluted_eps'])} → {_eps(latest['diluted_eps'])}")
    else:
        lines.append(f"- Latest quarter {latest['period']} vs prior-year {prior['period']}: revenue "
                     f"{_change(prior['revenue'], latest['revenue'], ko)}, operating income "
                     f"{_change(prior['operating_income'], latest['operating_income'], ko)}, diluted EPS "
                     f"{_eps(prior['diluted_eps'])} → {_eps(latest['diluted_eps'])}")
    return "\n".join(lines)
