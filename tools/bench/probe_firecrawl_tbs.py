#!/usr/bin/env python3
"""
Probe: does tbs/sources/include_domains actually change Firecrawl result freshness?

Compares the current bare call (no options) against the weekly-report options
on the same query, and reports how many results carry a usable publish date.

Usage:  python3 tools/bench/probe_firecrawl_tbs.py
Costs a handful of Firecrawl search credits. Read-only; touches no prod state.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from firecrawl_client import firecrawl_search_multi  # noqa: E402

KR_DOMAINS = [
    "yna.co.kr", "einfomax.co.kr", "hankyung.com", "mk.co.kr", "edaily.co.kr",
    "sedaily.com", "fnnews.com", "mt.co.kr", "asiae.co.kr", "newsis.com",
    "biz.chosun.com", "wowtv.co.kr", "infostock.co.kr", "thebell.co.kr",
]

QUERY = "코스피 증시 마감 시황"


def summarize(label: str, items: list) -> None:
    dated = [i for i in items if i.get("date")]
    low_trust = [i for i in items if i.get("low_trust")]
    print(f"\n===== {label} =====")
    print(f"  results={len(items)}  dated={len(dated)}  undated={len(items) - len(dated)}  low_trust={len(low_trust)}")
    for i in items[:8]:
        print(f"    [{i.get('date') or '발행일미상':<22}] {i.get('channel','?'):4} "
              f"{'LOW ' if i.get('low_trust') else '    '}{(i.get('title') or '')[:52]}")


def main() -> int:
    if not os.getenv("FIRECRAWL_API_KEY"):
        try:
            from firecrawl_client import _get_api_key
            _get_api_key()
        except Exception as exc:
            print(f"FIRECRAWL_API_KEY unavailable: {type(exc).__name__}: {exc}")
            return 1

    # A) what the bot fallback does today
    bare = firecrawl_search_multi([QUERY], limit=8, with_content=False)
    summarize("A. BARE (현행 봇 폴백 — 옵션 없음)", bare)

    # B) what the weekly report does
    tuned = firecrawl_search_multi(
        [QUERY], limit=8, with_content=False,
        tbs="qdr:w", sources=["news", "web"], location="KR",
        include_domains=KR_DOMAINS,
    )
    summarize("B. TUNED (주간리포트 옵션 — tbs+sources+location+domains)", tuned)

    print("\n===== 판정 =====")
    for label, items in (("A(bare)", bare), ("B(tuned)", tuned)):
        n = len(items)
        d = sum(1 for i in items if i.get("date"))
        lt = sum(1 for i in items if i.get("low_trust"))
        rate = (d / n * 100) if n else 0.0
        print(f"  {label:9} 총 {n:3}건 | 날짜보유 {d:3}건 ({rate:5.1f}%) | 저신뢰 {lt}건")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
