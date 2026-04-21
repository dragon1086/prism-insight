#!/usr/bin/env python3
"""
archive_query.py — CLI for the PRISM report archive insight engine.

Usage examples:
  python archive_query.py "반도체 강세 시기 수익률 높은 종목은?"
  python archive_query.py --market kr "최근 30일 기술주 트렌드"
  python archive_query.py --ticker AAPL "언제 매수 신호 떴어?"
  python archive_query.py --date-from 2025-10-01 --market us "AI 관련주 흐름"
  python archive_query.py --search "반도체" --market kr
  python archive_query.py --list --market kr --limit 20
  python archive_query.py --stats
  python archive_query.py --skip-cache "오늘 기준 최신 인사이트는?"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Allow running from project root without installing the package
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="PRISM 아카이브 인사이트 쿼리 CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "query",
        nargs="?",
        help="자연어 질문 (예: '반도체 강세 시기 최고 수익 종목은?')",
    )
    p.add_argument("--market", choices=["kr", "us"], help="시장 필터 (kr / us)")
    p.add_argument("--ticker", help="종목 코드 (예: 005930, AAPL)")
    p.add_argument("--date-from", dest="date_from", metavar="YYYY-MM-DD", help="시작일")
    p.add_argument("--date-to", dest="date_to", metavar="YYYY-MM-DD", help="종료일")
    _ALLOWED_MODELS = ["gpt-5.4-mini", "gpt-4.1-mini", "gpt-4.1", "gpt-4.1-nano"]
    p.add_argument(
        "--model",
        default="gpt-5.4-mini",
        choices=_ALLOWED_MODELS,
        help="LLM 모델 (기본값: gpt-5.4-mini)",
    )
    p.add_argument(
        "--skip-cache",
        action="store_true",
        dest="skip_cache",
        help="캐시 무시하고 새로 생성",
    )
    p.add_argument(
        "--search",
        metavar="KEYWORD",
        help="FTS5 키워드 검색 (LLM 합성 없음)",
    )
    p.add_argument(
        "--list",
        action="store_true",
        help="리포트 목록 출력",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=20,
        help="--list / --search 결과 최대 건수 (기본값: 20)",
    )
    p.add_argument(
        "--stats",
        action="store_true",
        help="아카이브 통계 출력",
    )
    p.add_argument(
        "--insight-stats",
        action="store_true",
        dest="insight_stats",
        help="persistent_insights 집계 출력 (총건수, 유저수, 도구사용분포, 비용)",
    )
    p.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="JSON 형식으로 출력",
    )
    return p


# ---------------------------------------------------------------------------
# Render helpers
# ---------------------------------------------------------------------------

def _render_stats(stats: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        return
    print("\n=== PRISM 아카이브 통계 ===")
    for row in stats.get("by_market", []):
        print(f"  {row['market'].upper():4s}  {row['count']:5d}건  "
              f"{row['earliest']} ~ {row['latest']}")
    print(f"  enriched:        {stats.get('enriched_count', 0):5d}건")
    print(f"  cached insights: {stats.get('cached_insights', 0):5d}건\n")


def _render_list(rows: list, as_json: bool) -> None:
    if as_json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    print(f"\n{'#':>3}  {'ticker':>8}  {'company':<20}  {'date':10}  {'market':4}  mode")
    print("-" * 68)
    for i, r in enumerate(rows, 1):
        print(
            f"{i:>3}  {r['ticker']:>8}  {r.get('company_name','?'):<20}  "
            f"{r.get('report_date','?'):10}  {r.get('market','?'):4}  {r.get('mode','?')}"
        )
    print()


def _render_search(hits: list, as_json: bool) -> None:
    if as_json:
        print(json.dumps(hits, ensure_ascii=False, indent=2))
        return
    print(f"\n=== FTS5 검색 결과 ({len(hits)}건) ===")
    for i, h in enumerate(hits, 1):
        print(f"\n[{i}] {h['ticker']} {h.get('company_name','')} | "
              f"{h.get('report_date','?')} | {h.get('market','?').upper()}")
        snippet = h.get("snippet", "")
        if snippet:
            print(f"     …{snippet}…")
    print()


async def _render_insight_stats(as_json: bool) -> None:
    """persistent_insights 집계 + 도구 분포 + 최근 7일 비용."""
    import aiosqlite
    from cores.archive.archive_db import ARCHIVE_DB_PATH, init_db
    await init_db()
    async with aiosqlite.connect(str(ARCHIVE_DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT COUNT(*) AS c, COUNT(DISTINCT user_id) AS u "
            "FROM persistent_insights"
        )
        r = await cur.fetchone()
        total = r["c"] if r else 0
        uniq = r["u"] if r else 0
        cur = await db.execute(
            "SELECT tool_name, COUNT(*) AS c FROM insight_tool_usage "
            "GROUP BY tool_name ORDER BY c DESC"
        )
        tools = [dict(r) for r in await cur.fetchall()]
        cur = await db.execute(
            "SELECT * FROM insight_cost_daily ORDER BY date DESC LIMIT 7"
        )
        costs = [dict(r) for r in await cur.fetchall()]
        cur = await db.execute(
            "SELECT COUNT(*) AS c FROM weekly_insight_summary"
        )
        weekly_count = (await cur.fetchone())["c"]
    data = {
        "total_insights": total,
        "unique_users": uniq,
        "weekly_summaries": weekly_count,
        "tool_usage": tools,
        "cost_last_7d": costs,
    }
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    print("\n=== /insight 통계 ===")
    print(f"  총 인사이트:     {total}")
    print(f"  고유 사용자:     {uniq}")
    print(f"  주간 요약:       {weekly_count}")
    print("\n  도구 사용 분포:")
    for t in tools:
        print(f"    {t['tool_name']:25s}  {t['c']}회")
    print("\n  최근 7일 비용:")
    for c in costs:
        print(
            f"    {c['date']}  in={c['input_tokens']:>6}  out={c['output_tokens']:>6}  "
            f"emb={c['embedding_tokens']:>6}  perp={c['perplexity_calls']:>3}  "
            f"fc={c['firecrawl_calls']:>3}"
        )
    print()


def _render_result(result, as_json: bool) -> None:
    if as_json:
        data = {
            "answer": result.answer,
            "cached": result.cached,
            "model_used": result.model_used,
            "evidence_ids": result.evidence_ids,
            "sources": [
                {
                    "report_id": s.report_id,
                    "ticker": s.ticker,
                    "company_name": s.company_name,
                    "report_date": s.report_date,
                    "market": s.market,
                    "enrichment": s.enrichment,
                }
                for s in result.sources
            ],
        }
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return

    cached_tag = " [캐시]" if result.cached else f" [{result.model_used}]"
    print(f"\n{'='*60}")
    print(f"인사이트{cached_tag}")
    print("=" * 60)
    print(result.answer)
    if result.sources:
        print(f"\n--- 근거 리포트 ({len(result.sources)}건) ---")
        for s in result.sources:
            enrich_str = ""
            e = s.enrichment
            if e:
                parts = []
                if e.get("market_phase"):
                    parts.append(e["market_phase"])
                for k, label in [("return_30d", "30d"), ("return_90d", "90d")]:
                    v = e.get(k)
                    if v is not None:
                        parts.append(f"{label}={v:+.1f}%")
                if e.get("stop_loss_triggered"):
                    parts.append("손절O")
                enrich_str = "  [" + " | ".join(parts) + "]" if parts else ""
            print(f"  • {s.ticker} {s.company_name} | {s.report_date} | "
                  f"{s.market.upper()}{enrich_str}")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def _main(args: argparse.Namespace) -> int:
    from cores.archive.query_engine import QueryEngine  # type: ignore[import]

    engine = QueryEngine(model=args.model)

    # --insight-stats (persistent_insights 집계)
    if args.insight_stats:
        await _render_insight_stats(args.as_json)
        return 0

    # --stats
    if args.stats:
        stats = await engine.stats()
        _render_stats(stats, args.as_json)
        return 0

    # --list
    if args.list:
        rows = await engine.list_reports(
            market=args.market,
            ticker=args.ticker,
            date_from=args.date_from,
            date_to=args.date_to,
            limit=args.limit,
        )
        _render_list(rows, args.as_json)
        return 0

    # --search KEYWORD
    if args.search:
        hits = await engine.search(args.search, market=args.market, limit=args.limit)
        _render_search(hits, args.as_json)
        return 0

    # Natural language query (default)
    if not args.query:
        print("질문을 입력하세요. 사용법: python archive_query.py --help")
        return 1

    result = await engine.query(
        args.query,
        market=args.market,
        ticker=args.ticker,
        date_from=args.date_from,
        date_to=args.date_to,
        skip_cache=args.skip_cache,
    )
    _render_result(result, args.as_json)
    return 0


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    sys.exit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
