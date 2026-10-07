"""Official corporate-event filings for the BUY/SELL decision AI (KR/US).

Core-0 of the SELL prompt (tender offer, going private, delisting) used to rely
on the AI's own news search. Search results vary call to call: MRVL was bought
on 2026-10-06 and its first sell check found nothing, the next one found a
2026-09-29 press release and sold. That release was a mini-tender (a third party
offering to buy 500,000 shares, about 0.06%, with no SEC filing), which neither
pins the price nor delists the stock.

This module reads the official record instead, on every decision:
- US: the issuer's SEC submissions list (it includes filings by others about the
  issuer, such as Schedule TO-T from a bidder), last ``US_LOOKBACK_DAYS``.
- KR: KIS disclosure titles (provider ``공시``) for the stock, last
  ``KR_LOOKBACK_DAYS`` (the feed is paged, so the block states the covered range).
The result is a prompt block that lists only event-type filings with their dates
and whether they predate the purchase. Lookups never raise; a failed lookup says
so and the AI falls back to its news search.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta

logger = logging.getLogger(__name__)

US_LOOKBACK_DAYS = 120
KR_LOOKBACK_DAYS = 30
MAX_ITEMS = 12
_TICKER_MAP_TTL = 24 * 3600
_ticker_map_cache: dict = {}


@dataclass(frozen=True)
class EventFiling:
    filed: str          # YYYY-MM-DD
    kind: str           # "event" (Core-0 candidate) or "not_event" (shown, never a sell reason)
    label: str          # form type / disclosure title
    note: str           # plain-language meaning
    url: str = ""


# --- US (SEC) ---------------------------------------------------------------

# Forms filed by or about a target that can pin the price or end the listing.
_US_EVENT_FORMS = {
    "SC TO-T": "third-party tender offer (Schedule TO-T)",
    "SC TO-C": "tender offer pre-commencement communication",
    "SC 14D9": "target board response to a tender offer (14D-9)",
    "SC 14D9-C": "target communication on a tender offer",
    "SC 13E3": "going-private transaction (13E-3)",
    "DEFM14A": "definitive merger proxy (check whether this company is the target)",
    "PREM14A": "preliminary merger proxy (check whether this company is the target)",
    "DEFM14C": "definitive merger information statement",
    "PREM14C": "preliminary merger information statement",
    "25": "exchange delisting (Form 25)",
    "25-NSE": "exchange delisting (Form 25)",
    "15-12B": "SEC deregistration (Form 15)",
    "15-12G": "SEC deregistration (Form 15)",
    "15-15D": "suspension of SEC reporting (Form 15)",
    "15F-12B": "SEC deregistration (Form 15F)",
    "15F-12G": "SEC deregistration (Form 15F)",
}
_US_NOT_EVENT_FORMS = {
    "SC TO-I": "issuer tender offer: the company buying back its own shares (not a sell reason by itself)",
}
_US_EVENT_8K_ITEMS = {
    "1.03": "bankruptcy or receivership (8-K 1.03)",
    "3.01": "delisting notice or listing-rule failure (8-K 3.01)",
    "5.01": "change in control (8-K 5.01)",
}


def _us_form(form: str) -> str:
    form = str(form or "").strip().upper()
    form = re.sub(r"^SCHEDULE\s+", "SC ", form)  # EDGAR's newer schedule names
    return re.sub(r"/A$", "", form)


def _fetch_sec(url: str) -> bytes:
    from prism_core.us_official_company_sources import _fetch

    body, _ = _fetch(url, timeout=10)
    return body


# A bidder's own filing list also carries its Schedule TO-T (Copart for ACV,
# 2026-10-01), so these forms count only when this company is the subject.
_US_ROLE_FORMS = {"SC TO-T", "SC TO-C", "SC 14D9", "SC 14D9-C", "SC 13E3"}
_MAX_ROLE_LOOKUPS = 8
_role_cache: dict = {}  # accession -> subject CIK; filings never change


def _subject_cik(cik: int, accession: str, fetch) -> int | None:
    """SUBJECT COMPANY CIK from the filing's index headers (None when absent)."""
    dashed = f"{accession[:10]}-{accession[10:12]}-{accession[12:]}"
    text = fetch(f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{dashed}-index-headers.html")
    text = re.sub(r"<[^>]+>", " ", text.decode("utf-8", "replace") if isinstance(text, bytes) else text)
    match = re.search(r"SUBJECT COMPANY:.*?CENTRAL INDEX KEY:\s*(\d+)", text, re.S)
    return int(match.group(1)) if match else None


def _us_cik(ticker: str, fetch) -> int | None:
    now = time.monotonic()
    cached = _ticker_map_cache.get("map")
    if not cached or now - cached[0] > _TICKER_MAP_TTL:
        rows = json.loads(fetch("https://www.sec.gov/files/company_tickers.json"))
        mapping = {str(r.get("ticker", "")).upper(): int(r["cik_str"]) for r in rows.values()}
        _ticker_map_cache["map"] = (now, mapping)
        cached = _ticker_map_cache["map"]
    symbol = str(ticker or "").upper()
    return cached[1].get(symbol) or cached[1].get(symbol.replace(".", "-"))


def us_event_filings(ticker: str, today: date, *, fetch=_fetch_sec) -> list[EventFiling]:
    cik = _us_cik(ticker, fetch)
    if cik is None:
        raise LookupError("cik_not_found")
    recent = json.loads(fetch(f"https://data.sec.gov/submissions/CIK{cik:010d}.json"))["filings"]["recent"]
    since = (today - timedelta(days=US_LOOKBACK_DAYS)).isoformat()
    found, role_lookups = [], 0
    for i, raw_form in enumerate(recent.get("form", [])):
        filed = str(recent["filingDate"][i])
        if filed < since:
            continue
        form = _us_form(raw_form)
        accession = str(recent.get("accessionNumber", [""] * (i + 1))[i]).replace("-", "")
        document = str(recent.get("primaryDocument", [""] * (i + 1))[i])
        url = (f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"
               if accession and document else "")
        if form in _US_ROLE_FORMS and accession:
            if accession not in _role_cache and role_lookups < _MAX_ROLE_LOOKUPS:
                role_lookups += 1
                try:
                    _role_cache[accession] = _subject_cik(cik, accession, fetch)
                except Exception:  # noqa: BLE001 — unknown role stays an event to check
                    pass
            subject = _role_cache.get(accession)
            if subject is not None and subject != cik:
                found.append(EventFiling(filed, "not_event", raw_form,
                                         "filed by this company as the bidder/acquirer of another company, "
                                         "not an offer for this company's shares", url))
                continue
        if form in _US_EVENT_FORMS:
            found.append(EventFiling(filed, "event", raw_form, _US_EVENT_FORMS[form], url))
        elif form in _US_NOT_EVENT_FORMS:
            found.append(EventFiling(filed, "not_event", raw_form, _US_NOT_EVENT_FORMS[form], url))
        elif form == "8-K":
            items = [x.strip() for x in str(recent.get("items", [""] * (i + 1))[i]).split(",")]
            notes = [_US_EVENT_8K_ITEMS[x] for x in items if x in _US_EVENT_8K_ITEMS]
            if notes:
                found.append(EventFiling(filed, "event", f"8-K ({', '.join(items)})", "; ".join(notes), url))
    return found[:MAX_ITEMS]


# --- KR (KIS disclosure titles) ---------------------------------------------

_KR_EVENT_KEYWORDS = (
    ("공개매수", "공개매수 관련 공시"),
    ("상장폐지", "상장폐지 관련 공시"),
    ("정리매매", "정리매매 공시"),
    ("매매거래정지", "매매거래정지 공시"),
    ("거래정지", "거래정지 공시"),
    ("관리종목", "관리종목 지정 관련 공시"),
    ("상장적격성", "상장적격성 실질심사 관련 공시"),
    ("감사의견", "감사의견 관련 공시"),
    ("주식교환", "주식교환 관련 공시(완전자회사화 여부 확인)"),
    ("주식이전", "주식이전 관련 공시"),
    ("합병", "합병 관련 공시(이 회사가 소멸·피합병 회사인지 확인)"),
)
# The company buying back its own shares (including by tender) is not a forced exit.
_KR_NOT_EVENT = re.compile(r"자기주식|자사주")


def _fetch_kr_titles(ticker, start, end):
    from cores.market_data import default_chain

    return default_chain().fetch("news_titles", ticker, start, end)


def kr_event_disclosures(ticker: str, today: date, *, fetch=_fetch_kr_titles) -> tuple[list[EventFiling], str]:
    start = (today - timedelta(days=KR_LOOKBACK_DAYS)).strftime("%Y%m%d")
    frame = fetch(ticker, start, today.strftime("%Y%m%d"))
    if frame is None or frame.empty:
        return [], ""
    rows = frame.to_dict("records")
    oldest = min(str(r.get("published_at", ""))[:10] for r in rows)
    found = []
    for r in rows:
        if str(r.get("provider", "")).strip() != "공시":
            continue
        title = str(r.get("title", "")).strip()
        compact = title.replace(" ", "")
        for keyword, note in _KR_EVENT_KEYWORDS:
            if keyword in compact:
                kind = "not_event" if _KR_NOT_EVENT.search(compact) else "event"
                if kind == "not_event":
                    note = "자기주식(자사주) 관련 공시: 회사의 자사주 매입이며 매도 사유가 아닙니다"
                found.append(EventFiling(str(r.get("published_at", ""))[:10], kind, title, note))
                break
    return found[:MAX_ITEMS], oldest


# --- Prompt block -------------------------------------------------------------

def _buy_day(buy_date) -> str:
    return str(buy_date or "")[:10]


def _line(item: EventFiling, buy_day: str, language: str) -> str:
    before = buy_day and item.filed < buy_day
    if language == "ko":
        tag = " (매수 전 공시)" if before else ""
        kind = "참고·매도 사유 아님" if item.kind == "not_event" else "점검 대상"
    else:
        tag = " (filed before purchase)" if before else ""
        kind = "info only, not a sell reason" if item.kind == "not_event" else "check"
    url = f" {item.url}" if item.url else ""
    return f"- {item.filed} {item.label} — {item.note} [{kind}]{tag}{url}"


def official_event_block(market: str, ticker: str, *, buy_date=None, today: date | None = None,
                         language: str = "ko", purpose: str = "sell", us_fetch=_fetch_sec,
                         kr_fetch=_fetch_kr_titles) -> str:
    """Prompt block listing official event filings; never raises ('' when CORP_FILINGS_ENABLED=false)."""
    if os.getenv("CORP_FILINGS_ENABLED", "true").strip().lower() in ("0", "false", "no", "off"):
        return ""
    today = today or datetime.now().date()
    language = "ko" if language == "ko" else "en"
    market = market.upper()
    try:
        if market == "US":
            items, covered = us_event_filings(ticker, today, fetch=us_fetch), ""
            span = US_LOOKBACK_DAYS
        else:
            items, covered = kr_event_disclosures(ticker, today, fetch=kr_fetch)
            span = KR_LOOKBACK_DAYS
        status = "ok"
    except Exception as exc:  # noqa: BLE001 — optional input; the AI falls back to its news search
        logger.warning("[CORP_FILINGS] market=%s ticker=%s lookup failed: %s", market, ticker, type(exc).__name__)
        items, covered, status = [], "", "unavailable"
        span = US_LOOKBACK_DAYS if market == "US" else KR_LOOKBACK_DAYS
    events = sum(i.kind == "event" for i in items)
    logger.info("[CORP_FILINGS] market=%s ticker=%s purpose=%s status=%s events=%d listed=%d",
                market, ticker, purpose, status, events, len(items))
    source = ("SEC EDGAR filing list (includes filings by others about this company)" if market == "US"
              else "KIS 공시 제목 피드")
    buy_day = _buy_day(buy_date)
    if language == "ko":
        head = f"\n### 공식 공시 점검 (시스템 자동 조회 {today:%Y-%m-%d}, {source}, 최근 {span}일"
        head += f", 실제 조회 범위 {covered}부터)" if covered else ")"
        if status != "ok":
            body = "- 자동 조회에 실패했습니다. 핵심-0 점검은 검색 도구로 하되, 공식 공시로 확인되는 경우에만 법인이벤트로 판단하십시오."
        elif not items:
            body = ("- 공개매수·비공개전환·상장폐지·거래정지·관리종목 관련 공식 공시가 없습니다. 뉴스나 보도자료로만 보이는 "
                    "공개매수·인수설은 미확인으로 보고 법인이벤트 매도 사유로 쓰지 마십시오.")
        else:
            body = "\n".join(_line(i, buy_day, "ko") for i in items)
        rule = ("- 핵심-0의 '공식 확인'은 이 목록의 공시를 1차 근거로 합니다. 공시일을 sell_reason에 쓰고, 매수 전 공시는 이미 알려진 "
                "정보임을 감안하십시오." if purpose == "sell" else
                "- 경영권 인수·상장폐지 목적의 공개매수, 상장폐지·정리매매·거래정지가 진행 중이면 신규 진입하지 마십시오. "
                "자사주 매입·소량 공개매수는 진입 판단에 영향을 주지 않습니다.")
    else:
        head = f"\n### Official filing check (system lookup {today:%Y-%m-%d}, {source}, last {span} days"
        head += f", covered from {covered})" if covered else ")"
        if status != "ok":
            body = ("- The automatic lookup failed. Run the Core-0 check with the search tool, but treat an event as "
                    "confirmed only when an official filing confirms it.")
        elif not items:
            body = ("- No official tender offer, going-private, delisting, deregistration, bankruptcy or "
                    "change-of-control filing. A tender offer or takeover seen only in news or a press release is "
                    "unconfirmed and is not a corporate-event sell reason.")
        else:
            body = "\n".join(_line(i, buy_day, "en") for i in items)
        rule = ("- Core-0 'officially confirmed' means a filing in this list. Quote the filing date in sell_reason and "
                "remember that a filing made before the purchase was already known information." if purpose == "sell" else
                "- Do not enter while a control or going-private tender offer, a delisting or a trading halt is in "
                "progress. Buybacks and small (mini) tender offers do not affect the entry decision.")
    return f"{head}\n{body}\n{rule}\n"


async def official_event_block_async(market: str, ticker: str, *, timeout: float = 30, **kwargs) -> str:
    """Run the lookup off the event loop; a slow source yields the 'lookup failed' block."""
    import asyncio

    try:
        return await asyncio.wait_for(asyncio.to_thread(official_event_block, market, ticker, **kwargs), timeout)
    except asyncio.TimeoutError:
        logger.warning("[CORP_FILINGS] market=%s ticker=%s lookup timed out", market, ticker)
        kwargs.update(us_fetch=_timed_out, kr_fetch=_timed_out)
        return official_event_block(market, ticker, **kwargs)


def _timed_out(*_args, **_kwargs):
    raise TimeoutError("corporate_filings_timeout")
