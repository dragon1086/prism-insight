"""WiseReport Financial Summary: annual charts and quarterly EPS facts for KR reports.

KIS publishes PER/PBR/EPS/BPS and market cap only as today's snapshot, and the
KIS-only migration deliberately refuses to stretch one snapshot into a history
(docs/KIS_ONLY_MIGRATION_20260911.md). The report's fundamentals section
already reads WiseReport's company page (company_status agent, peer table);
this reads the same page's "Financial Summary" table, which carries reported
fiscal-year values, so the charts show published annual figures rather than a
synthesized daily series.

The same table's quarterly view gives the company-status writer a fixed
quarterly EPS series with its prior-year quarter, instead of whatever pages an
agent happens to scrape on a given run.

Report-only optional data: two fixed-host reads, no LLM, never a trading gate.
Any fetch/parse/validation failure returns None and the chart or facts are omitted.
"""
from __future__ import annotations

import asyncio
import logging
import math
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import aiohttp
import pandas as pd
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_HOST = "https://comp.wisereport.co.kr/company/"
PAGE_URL = _HOST + "c1010001.aspx?cmp_cd={code}"
SUMMARY_URL = (_HOST + "ajax/cF1001.aspx?cmp_cd={code}&fin_typ=0&freq_typ=Y"
               "&encparam={encparam}&id={table_id}")
QUARTERLY_URL = SUMMARY_URL.replace("freq_typ=Y", "freq_typ=Q")
_TIMEOUT_SECONDS = 8
_MAX_BYTES = 500_000
_CODE = re.compile(r"\d{6}")
_ENCPARAM = re.compile(r"encparam\s*:\s*'([A-Za-z0-9+/=]{8,128})'")
_TABLE_ID = re.compile(r"\bid\s*:\s*'([A-Za-z0-9]{4,32})'")
_PERIOD = re.compile(r"(\d{4})/(\d{2})\s*(\(E\))?\s*(?:\(([^)]*)\))?")

# Provider row label (whitespace removed) -> column. Money rows are 억원.
_ROWS = {
    "매출액": "revenue",
    "영업이익": "operating_income",
    "영업이익(발표기준)": "operating_income_reported",
    "당기순이익(지배)": "net_income_controlling",
    "영업이익률": "op_margin",
    "ROE(%)": "roe",
    "EPS(원)": "eps",
    "PER(배)": "per",
    "BPS(원)": "bps",
    "PBR(배)": "pbr",
    "현금배당수익률": "dividend_yield",
    "발행주식수(보통주)": "shares",
}
_REQUIRED = ("revenue", "per", "pbr", "roe")
_MIN_ACTUAL_YEARS = 2


def _number(raw):
    value = str(raw or "").replace(",", "").strip()
    if not re.fullmatch(r"[-+]?\d+(?:\.\d+)?", value):
        return math.nan
    return float(value)


def parse_encparam(page_html):
    """The page embeds a per-session token the ajax table requires."""
    encparam = _ENCPARAM.search(page_html or "")
    table_id = _TABLE_ID.search(page_html or "")
    if not encparam or not table_id:
        raise ValueError("summary_token_missing")
    return encparam.group(1), table_id.group(1)


def parse_financial_summary(html):
    """cF1001 annual table -> frame indexed by fiscal period ('2025/12').

    Columns are the `_ROWS` values plus `estimate` (consensus column). Attrs
    carry the accounting basis and the source so the chart can label itself.
    """
    if not isinstance(html, str) or len(html.encode()) > _MAX_BYTES:
        raise ValueError("summary_size")
    soup = BeautifulSoup(html, "html.parser")
    table = next((t for t in soup.find_all("table") if "주요재무정보" in t.get_text()), None)
    if table is None:
        raise ValueError("summary_table_missing")

    periods, estimates, bases = [], [], set()
    rows = {}
    for tr in table.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        texts = [c.get_text(" ", strip=True) for c in cells]
        matches = [_PERIOD.fullmatch(t) for t in texts]
        if not periods and texts and all(matches) and len(matches) >= 2:
            for m in matches:
                periods.append(f"{m.group(1)}/{m.group(2)}")
                estimates.append(bool(m.group(3)))
                if m.group(4):
                    bases.add(m.group(4).strip())
            continue
        th = tr.find("th")
        label = re.sub(r"\s+", "", th.get_text(" ", strip=True)) if th else ""
        if label not in _ROWS or not periods:
            continue
        values = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if len(values) != len(periods):
            raise ValueError("summary_column_count")
        rows.setdefault(_ROWS[label], [_number(v) for v in values])

    if not periods or len(set(periods)) != len(periods):
        raise ValueError("summary_periods")
    if any(field not in rows for field in _REQUIRED):
        raise ValueError("summary_rows")

    frame = pd.DataFrame(rows, index=pd.Index(periods, name="period"))
    if "operating_income" in frame and "operating_income_reported" in frame:
        # Consensus columns fill only the "발표기준" row.
        frame = frame.assign(operating_income=frame["operating_income"].fillna(
            frame["operating_income_reported"]))
    frame = frame.drop(columns=["operating_income_reported"], errors="ignore")
    # Zero/negative multiples (pre-listing years, losses) are undefined, not cheap.
    frame = frame.assign(**{c: frame[c].where(frame[c] > 0) for c in ("per", "pbr")})
    frame["estimate"] = estimates
    actual = frame[~frame["estimate"]]
    if actual[list(_REQUIRED)].notna().any(axis=1).sum() < _MIN_ACTUAL_YEARS:
        raise ValueError("summary_too_few_actual_years")
    frame.attrs.update({
        "source": "Annual financial summary",
        "basis": ", ".join(sorted(bases)) or "unknown",
        "unit_money": "100M KRW",
        "note": "(A) fiscal year-end price basis; (E) consensus estimate",
    })
    return frame


async def _fetch_text(session, url, *, referer=None):
    headers = {"Referer": referer} if referer else None
    async with session.get(url, headers=headers) as response:
        if response.status != 200:
            raise ValueError(f"http_{response.status}")
        body = await response.read()
        if len(body) > _MAX_BYTES:
            raise ValueError("response_size")
        return body.decode("utf-8", errors="replace")


async def _collect(company_code, reference_date, table_url, tag, fetch):
    """Fetch and parse one cF1001 view, or None when it cannot be verified."""
    code = str(company_code or "")
    if not _CODE.fullmatch(code):
        return None
    try:
        reference = datetime.strptime(str(reference_date), "%Y%m%d").date()
    except ValueError:
        return None
    # WiseReport serves only the current page; never attach it to a past date.
    if reference != datetime.now(ZoneInfo("Asia/Seoul")).date():
        return None
    page_url = PAGE_URL.format(code=code)
    try:
        if fetch is None:
            timeout = aiohttp.ClientTimeout(total=_TIMEOUT_SECONDS)
            # One session: the ajax table checks the page's cookie and referer.
            async with aiohttp.ClientSession(timeout=timeout) as session:
                page = await _fetch_text(session, page_url)
                encparam, table_id = parse_encparam(page)
                html = await _fetch_text(session, table_url.format(
                    code=code, encparam=encparam, table_id=table_id), referer=page_url)
        else:
            page = await fetch(page_url, None)
            encparam, table_id = parse_encparam(page)
            html = await fetch(table_url.format(
                code=code, encparam=encparam, table_id=table_id), page_url)
        frame = parse_financial_summary(html)
    except asyncio.CancelledError:
        raise
    except Exception as error:  # noqa: BLE001 - optional data never blocks a report
        reason = str(error) if isinstance(error, ValueError) else type(error).__name__
        logger.warning("[%s] symbol=%s status=skipped reason=%s", tag, code, reason)
        return None
    frame.attrs["ticker"] = code
    logger.info("[%s] symbol=%s status=ready periods=%s basis=%s",
                tag, code, ",".join(frame.index), frame.attrs["basis"])
    return frame


async def collect_wisereport_financial_summary(company_code, reference_date, *, fetch=None):
    """Return the annual summary frame, or None when it cannot be verified.

    ``fetch`` is an optional async ``(url, referer) -> text`` callable for tests.
    """
    return await _collect(company_code, reference_date, SUMMARY_URL, "FINANCIAL_SUMMARY", fetch)


async def collect_wisereport_quarterly_summary(company_code, reference_date, *, fetch=None):
    """Return the quarterly summary frame (same table, quarterly view), or None."""
    frame = await _collect(company_code, reference_date, QUARTERLY_URL, "QUARTERLY_SUMMARY", fetch)
    if frame is not None:
        frame.attrs.update(source="Quarterly financial summary", note="(E) consensus estimate")
    return frame


# ------------------------------------------------------------ quarterly facts

def _finite(value):
    return value is not None and not (isinstance(value, float) and math.isnan(value))


def _change(prior, current, language):
    """Deterministic comparison label; a loss base never yields a growth rate."""
    ko = language == "ko"
    if not (_finite(prior) and _finite(current)):
        return "UNKNOWN"
    if prior > 0 and current > 0:
        return f"{(current - prior) / prior * 100:+.1f}%"
    if prior <= 0 < current:
        return "흑자전환" if ko else "turned to profit"
    if prior > 0 > current:
        return "적자전환" if ko else "turned to loss"
    if prior < 0 and current < 0:
        return "적자 지속" if ko else "loss continued"
    return "증감률 산출 불가(기준값 0)" if ko else "no growth rate (zero base)"


def _fmt(value, unit=""):
    return f"{value:,.0f}{unit}" if _finite(value) else "N/A"


def render_quarterly_facts(frame, language="ko"):
    """Markdown facts: reported quarters, latest vs prior-year quarter, share-count change.

    Returns "" when there is no reported quarter to anchor on.
    """
    if frame is None or "eps" not in frame:
        return ""
    actual = frame[~frame["estimate"]]
    if actual.empty:
        return ""
    ko = language == "ko"
    latest = actual.index[-1]
    year, month = latest.split("/")
    prior = f"{int(year) - 1}/{month}"
    shares = actual["shares"] if "shares" in actual else None

    def cell(period, column, unit=""):
        return _fmt(actual.at[period, column], unit) if column in actual else "N/A"

    if ko:
        lines = ["### 분기 실적 사전 수집 표 (확정 분기만, 추정치 제외)",
                 f"기준: {frame.attrs.get('basis', 'unknown')} · 금액 단위 억원 · EPS 단위 원 · "
                 "발행주식수는 분기말 보통주",
                 "| 분기 | 영업이익 | 지배순이익 | EPS | 발행주식수 |", "|---|---:|---:|---:|---:|"]
    else:
        lines = ["### Pre-collected quarterly results (reported quarters only, estimates excluded)",
                 f"Basis: {frame.attrs.get('basis', 'unknown')} · money in 100M KRW · EPS in KRW · "
                 "shares are quarter-end common shares",
                 "| Quarter | Operating income | Net income (controlling) | EPS | Shares |",
                 "|---|---:|---:|---:|---:|"]
    for period in actual.index:
        lines.append(f"| {period} | {cell(period, 'operating_income')} | "
                     f"{cell(period, 'net_income_controlling')} | {cell(period, 'eps')} | "
                     f"{cell(period, 'shares')} |")

    if prior not in actual.index:
        lines.append(f"- 최근 확정 분기 {latest}의 전년 동기({prior}) 행이 표에 없어 전년 동기 비교는 UNKNOWN입니다."
                     if ko else
                     f"- The prior-year quarter ({prior}) of the latest reported quarter {latest} is not in "
                     "the table; the year-over-year comparison is UNKNOWN.")
        return "\n".join(lines)

    eps_prior, eps_now = actual.at[prior, "eps"], actual.at[latest, "eps"]
    ni = "net_income_controlling" in actual
    ni_prior = actual.at[prior, "net_income_controlling"] if ni else math.nan
    ni_now = actual.at[latest, "net_income_controlling"] if ni else math.nan
    if ko:
        lines.append(f"- 최근 확정 분기 {latest} vs 전년 동기 {prior}: EPS {_fmt(eps_prior, '원')} → "
                     f"{_fmt(eps_now, '원')} ({_change(eps_prior, eps_now, language)}), 지배순이익 "
                     f"{_fmt(ni_prior, '억원')} → {_fmt(ni_now, '억원')} ({_change(ni_prior, ni_now, language)}).")
    else:
        lines.append(f"- Latest reported quarter {latest} vs prior-year {prior}: EPS {_fmt(eps_prior)} → "
                     f"{_fmt(eps_now)} KRW ({_change(eps_prior, eps_now, language)}), controlling net income "
                     f"{_fmt(ni_prior)} → {_fmt(ni_now)} (100M KRW) ({_change(ni_prior, ni_now, language)}).")

    s_prior = shares.at[prior] if shares is not None else math.nan
    s_now = shares.at[latest] if shares is not None else math.nan
    if not (_finite(s_prior) and _finite(s_now) and s_prior > 0):
        lines.append("- 두 분기의 발행주식수를 확인할 수 없어 EPS 분모 변화는 UNKNOWN입니다." if ko else
                     "- Share counts for the two quarters are unavailable; the EPS denominator change is UNKNOWN.")
    elif s_now == s_prior:
        lines.append(f"- 두 분기 말 발행주식수가 {_fmt(s_now, '주')}로 같습니다." if ko else
                     f"- Quarter-end shares are unchanged at {_fmt(s_now)}.")
    else:
        pct = (s_now - s_prior) / s_prior * 100
        lines.append(f"- 발행주식수 {_fmt(s_prior, '주')} → {_fmt(s_now, '주')} ({pct:+.1f}%): 주식 수가 달라 EPS "
                     "증감에는 주식 수 변화가 함께 반영됩니다. 주식 수와 무관한 비교는 지배순이익을 쓰세요."
                     if ko else
                     f"- Shares {_fmt(s_prior)} → {_fmt(s_now)} ({pct:+.1f}%): the EPS change includes the "
                     "share-count change; use controlling net income for a share-count-free comparison.")
    return "\n".join(lines)
