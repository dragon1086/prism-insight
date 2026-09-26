"""WiseReport annual Financial Summary for the KR report's fundamental charts.

KIS publishes PER/PBR/EPS/BPS and market cap only as today's snapshot, and the
KIS-only migration deliberately refuses to stretch one snapshot into a history
(docs/KIS_ONLY_MIGRATION_20260911.md). The report's fundamentals section
already reads WiseReport's company page (company_status agent, peer table);
this reads the same page's "Financial Summary" table, which carries reported
fiscal-year values, so the charts show published annual figures rather than a
synthesized daily series.

Report-only optional data: two fixed-host reads, no LLM, never a trading gate.
Any fetch/parse/validation failure returns None and the chart is omitted.
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
        "source": "WiseReport Financial Summary (annual)",
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


async def collect_wisereport_financial_summary(company_code, reference_date, *, fetch=None):
    """Return the annual summary frame, or None when it cannot be verified.

    ``fetch`` is an optional async ``(url, referer) -> text`` callable for tests.
    """
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
                html = await _fetch_text(session, SUMMARY_URL.format(
                    code=code, encparam=encparam, table_id=table_id), referer=page_url)
        else:
            page = await fetch(page_url, None)
            encparam, table_id = parse_encparam(page)
            html = await fetch(SUMMARY_URL.format(
                code=code, encparam=encparam, table_id=table_id), page_url)
        frame = parse_financial_summary(html)
    except asyncio.CancelledError:
        raise
    except Exception as error:  # noqa: BLE001 - optional data never blocks a report
        reason = str(error) if isinstance(error, ValueError) else type(error).__name__
        logger.warning("[FINANCIAL_SUMMARY] symbol=%s status=skipped reason=%s", code, reason)
        return None
    frame.attrs["ticker"] = code
    logger.info("[FINANCIAL_SUMMARY] symbol=%s status=ready periods=%s basis=%s",
                code, ",".join(frame.index), frame.attrs["basis"])
    return frame
