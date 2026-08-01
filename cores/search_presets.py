"""
Retrieval presets for Firecrawl-backed commands.

Why this module exists
----------------------
`generate_firecrawl_search_response()` accepts recency/source/domain filters, but
they are optional keyword arguments. Calling it without them is silently legal and
silently terrible: a bare call for "코스피 증시 마감 시황" comes back with eight
undated *quote pages* (naver/KB/daum ticker screens) and zero news articles, so the
model has nothing to reason about and invents or hedges instead.

Measured on 2026-08-01 with tools/bench/probe_firecrawl_tbs.py:

    bare  (no options)                 8 results | 0 dated (0%)   | all quote pages
    tuned (tbs+sources+location+dom)   8 results | 4 dated (50%)  | 마감시황 articles

The weekly report already passed the right options; every bot command did not.
Centralizing them here keeps the two call paths from drifting apart again.

Notes on the individual knobs
-----------------------------
tbs             Recency window. Without it the engine happily returns years-old
                pages — the single biggest cause of stale reports.
sources         ["news", "web"]. The news channel is the only one that carries a
                publish date, which is what makes date filtering possible at all.
location        Country bias for the backend.
include_domains Primary-outlet allowlist. Firecrawl suppresses the news channel
                when a domain allowlist is set, so firecrawl_client._search_passes()
                issues a separate unfiltered news pass; that is expected.
"""
from __future__ import annotations

# 1차 매체만 허용. 블로그·커뮤니티가 검색 상위를 먹으면 리포트 수치가 오염된다.
KR_PRIMARY_DOMAINS = [
    "yna.co.kr", "einfomax.co.kr", "hankyung.com", "mk.co.kr", "edaily.co.kr",
    "sedaily.com", "fnnews.com", "mt.co.kr", "asiae.co.kr", "newsis.com",
    "biz.chosun.com", "wowtv.co.kr", "infostock.co.kr", "thebell.co.kr",
]

US_PRIMARY_DOMAINS = [
    "reuters.com", "cnbc.com", "bloomberg.com", "marketwatch.com", "wsj.com",
    "barrons.com", "ft.com", "investors.com", "apnews.com", "finance.yahoo.com",
    "fool.com", "seekingalpha.com",
]

_DOMAINS_BY_MARKET = {"KR": KR_PRIMARY_DOMAINS, "US": US_PRIMARY_DOMAINS}

# Progressive widening ladder used when a tight window returns nothing at all.
# Falling back to a wider window beats returning "결과 없음" to the user.
TBS_LADDER = ["qdr:d", "qdr:w", "qdr:m", "qdr:y"]


def widen_tbs(tbs: str | None) -> str | None:
    """Next wider recency window, or None once the ladder is exhausted."""
    if tbs not in TBS_LADDER:
        return None
    idx = TBS_LADDER.index(tbs)
    return TBS_LADDER[idx + 1] if idx + 1 < len(TBS_LADDER) else None


def search_preset(
    market: str,
    *,
    tbs: str = "qdr:w",
    allowlist: bool = True,
) -> dict:
    """
    Build retrieval kwargs for generate_firecrawl_search_response().

    Args:
        market: "KR" or "US" — sets country bias and which primary-outlet list to use.
        tbs: Recency window. Event-impact questions want "qdr:w"; theme health and
             free-form research need "qdr:m" so a slow news week does not empty the
             result set.
        allowlist: Restrict to primary outlets. Turn this OFF for free-form questions
             (/ask), where the subject is unpredictable and a KR/US press allowlist
             would drop legitimate sources.

    Returns:
        dict of keyword arguments, splat directly into the search call.
    """
    market = market.upper()
    if market not in _DOMAINS_BY_MARKET:
        raise ValueError(f"unknown market {market!r}; expected 'KR' or 'US'")

    preset: dict = {
        "tbs": tbs,
        "sources": ["news", "web"],
        "location": market,
    }
    if allowlist:
        preset["include_domains"] = _DOMAINS_BY_MARKET[market]
    return preset
