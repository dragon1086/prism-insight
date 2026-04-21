"""
auto_insight.py — Automated insight generation from the PRISM report archive.

Generates structured insights by querying the archive DB directly:
  - daily_digest:           Today's analysis summary (reports filed, market phase)
  - performance_leaderboard: Top/bottom performers by N-day return
  - stop_loss_analysis:     Stop-loss hit-rate and accuracy statistics
  - market_phase_report:    Return distribution by market phase at analysis time
  - weekly_summary:         Aggregated weekly stats with LLM narrative

Each function returns an InsightReport that can be rendered as Telegram
message, JSON, or markdown.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import re

import aiosqlite

from .archive_db import ARCHIVE_DB_PATH, init_db  # type: ignore[import]
from .query_engine import QueryEngine, load_api_key, synthesize  # type: ignore[import]

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent

_DEFAULT_MODEL = "gpt-5.4-mini"


def _sanitize_for_llm(text: str, max_len: int = 3000) -> str:
    """Strip control characters and limit length to prevent prompt injection."""
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    return cleaned[:max_len]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class InsightReport:
    """Container for a generated insight."""

    title: str
    body: str  # Markdown-formatted text
    data: Dict[str, Any] = field(default_factory=dict)
    market: Optional[str] = None
    generated_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M"))

    def to_telegram(self, max_length: int = 4000) -> str:
        """Format for Telegram (truncated to max_length)."""
        msg = f"📊 {self.title}\n{self.generated_at}\n\n{self.body}"
        if len(msg) > max_length:
            msg = msg[: max_length - 3] + "…"
        return msg

    def to_json(self) -> str:
        return json.dumps(
            {"title": self.title, "body": self.body, "data": self.data,
             "market": self.market, "generated_at": self.generated_at},
            ensure_ascii=False, indent=2,
        )


# ---------------------------------------------------------------------------
# AutoInsight engine
# ---------------------------------------------------------------------------

class AutoInsight:
    """
    Automated insight generator backed by the archive DB.

    Usage::

        ai = AutoInsight()
        report = await ai.daily_digest(market="kr")
        print(report.body)
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        model: str = _DEFAULT_MODEL,
    ):
        self.db_path = db_path or str(ARCHIVE_DB_PATH)
        self.model = model
        self._query_engine: Optional[QueryEngine] = None

    @property
    def engine(self) -> QueryEngine:
        if self._query_engine is None:
            self._query_engine = QueryEngine(db_path=self.db_path, model=self.model)
        return self._query_engine

    # ------------------------------------------------------------------
    # Daily digest
    # ------------------------------------------------------------------

    async def daily_digest(
        self,
        date: Optional[str] = None,
        market: Optional[str] = None,
    ) -> InsightReport:
        """
        Summary of analyses filed on a given date.

        Args:
            date: ISO date (default: today)
            market: 'kr', 'us', or None for both
        """
        target_date = date or datetime.today().strftime("%Y-%m-%d")
        await init_db(self.db_path)

        clauses = ["report_date = ?"]
        params: list = [target_date]
        if market:
            clauses.append("market = ?")
            params.append(market)

        where = " AND ".join(clauses)

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            # Report count + tickers
            cur = await db.execute(
                f"SELECT ticker, company_name, market, mode FROM report_archive "
                f"WHERE {where} ORDER BY ticker",
                params,
            )
            reports = [dict(r) for r in await cur.fetchall()]

            # Enrichment summary for the date
            cur = await db.execute(
                f"""SELECT re.ticker, re.market, re.market_phase,
                           re.return_7d, re.return_30d, re.price_at_analysis
                    FROM report_enrichment re
                    JOIN report_archive ra ON ra.id = re.report_id
                    WHERE ra.report_date = ? {' AND ra.market = ?' if market else ''}
                    ORDER BY re.return_7d DESC NULLS LAST""",
                [target_date] + ([market] if market else []),
            )
            enrichments = [dict(r) for r in await cur.fetchall()]

        if not reports:
            return InsightReport(
                title=f"일일 다이제스트 — {target_date}",
                body=f"{target_date} 에 등록된 분석 리포트가 없습니다.",
                data={"date": target_date, "count": 0},
                market=market,
            )

        # Build summary
        lines = [f"**분석 리포트: {len(reports)}건**\n"]
        for r in reports:
            lines.append(f"- {r['ticker']} {r['company_name']} ({r['market'].upper()})")

        if enrichments:
            lines.append(f"\n**성과 요약 (enriched: {len(enrichments)}건)**")
            for e in enrichments[:10]:
                phase = e.get("market_phase", "?")
                r7 = e.get("return_7d")
                r30 = e.get("return_30d")
                r7_str = f"{r7:+.1f}%" if r7 is not None else "n/a"
                r30_str = f"{r30:+.1f}%" if r30 is not None else "n/a"
                lines.append(f"  {e['ticker']} | 7d={r7_str} 30d={r30_str} | {phase}")

        body = "\n".join(lines)

        return InsightReport(
            title=f"일일 다이제스트 — {target_date}",
            body=body,
            data={"date": target_date, "count": len(reports),
                  "reports": reports, "enrichments": enrichments},
            market=market,
        )

    # ------------------------------------------------------------------
    # Performance leaderboard
    # ------------------------------------------------------------------

    async def performance_leaderboard(
        self,
        days: int = 30,
        market: Optional[str] = None,
        top_n: int = 10,
    ) -> InsightReport:
        """
        Top and bottom performers by N-day forward return.

        Args:
            days: Return period (7, 14, 30, 60, 90)
            market: 'kr', 'us', or None
            top_n: Number of entries per side
        """
        top_n = min(max(top_n, 1), 100)
        col_map = {7: "return_7d", 14: "return_14d", 30: "return_30d",
                    60: "return_60d", 90: "return_90d"}
        col = col_map.get(days, "return_30d")

        await init_db(self.db_path)
        market_clause = "AND re.market = ?" if market else ""
        params: list = [market] if market else []

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            # Top performers
            cur = await db.execute(
                f"""SELECT re.ticker, ra.company_name, re.market, re.analysis_date,
                           re.{col} as ret, re.market_phase, re.price_at_analysis
                    FROM report_enrichment re
                    JOIN report_archive ra ON ra.id = re.report_id
                    WHERE re.{col} IS NOT NULL {market_clause}
                    ORDER BY re.{col} DESC
                    LIMIT ?""",
                params + [top_n],
            )
            top = [dict(r) for r in await cur.fetchall()]

            # Bottom performers
            cur = await db.execute(
                f"""SELECT re.ticker, ra.company_name, re.market, re.analysis_date,
                           re.{col} as ret, re.market_phase, re.price_at_analysis
                    FROM report_enrichment re
                    JOIN report_archive ra ON ra.id = re.report_id
                    WHERE re.{col} IS NOT NULL {market_clause}
                    ORDER BY re.{col} ASC
                    LIMIT ?""",
                params + [top_n],
            )
            bottom = [dict(r) for r in await cur.fetchall()]

        lines = [f"**Top {top_n} ({days}d 수익률)**\n"]
        for i, t in enumerate(top, 1):
            lines.append(f"{i}. {t['ticker']} {t.get('company_name','')} "
                         f"| {t['ret']:+.1f}% | {t['analysis_date']} | {t.get('market_phase','?')}")

        lines.append(f"\n**Bottom {top_n} ({days}d 수익률)**\n")
        for i, b in enumerate(bottom, 1):
            lines.append(f"{i}. {b['ticker']} {b.get('company_name','')} "
                         f"| {b['ret']:+.1f}% | {b['analysis_date']} | {b.get('market_phase','?')}")

        body = "\n".join(lines)
        market_label = (market or "all").upper()

        return InsightReport(
            title=f"성과 리더보드 — {days}d | {market_label}",
            body=body,
            data={"days": days, "top": top, "bottom": bottom},
            market=market,
        )

    # ------------------------------------------------------------------
    # Stop-loss analysis
    # ------------------------------------------------------------------

    async def stop_loss_analysis(
        self,
        market: Optional[str] = None,
        date_from: Optional[str] = None,
    ) -> InsightReport:
        """
        Stop-loss trigger statistics and accuracy.

        Measures:
          - Total reports with stop_loss_price set
          - How many actually triggered
          - Accuracy: % of cases where stop_was_correct=1
          - Avg post-stop 30d/60d return (was cutting correct?)
        """
        await init_db(self.db_path)
        market_clause = "AND market = ?" if market else ""
        date_clause = "AND analysis_date >= ?" if date_from else ""
        params: list = []
        if market:
            params.append(market)
        if date_from:
            params.append(date_from)

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            # Overall stats
            cur = await db.execute(
                f"""SELECT
                        COUNT(*) as total,
                        SUM(CASE WHEN stop_loss_price IS NOT NULL THEN 1 ELSE 0 END) as with_sl,
                        SUM(CASE WHEN stop_loss_triggered = 1 THEN 1 ELSE 0 END) as triggered,
                        SUM(CASE WHEN stop_was_correct = 1 THEN 1 ELSE 0 END) as correct,
                        AVG(CASE WHEN stop_loss_triggered = 1 THEN post_stop_30d END) as avg_post30,
                        AVG(CASE WHEN stop_loss_triggered = 1 THEN post_stop_60d END) as avg_post60
                    FROM report_enrichment
                    WHERE 1=1 {market_clause} {date_clause}""",
                params,
            )
            row = await cur.fetchone()
            stats = dict(row) if row else {}

            # Recent triggered examples
            cur = await db.execute(
                f"""SELECT re.ticker, ra.company_name, re.analysis_date,
                           re.stop_loss_date, re.post_stop_30d, re.stop_was_correct
                    FROM report_enrichment re
                    JOIN report_archive ra ON ra.id = re.report_id
                    WHERE re.stop_loss_triggered = 1 {market_clause} {date_clause}
                    ORDER BY re.analysis_date DESC
                    LIMIT 10""",
                params,
            )
            examples = [dict(r) for r in await cur.fetchall()]

        total = stats.get("total", 0)
        with_sl = stats.get("with_sl", 0)
        triggered = stats.get("triggered", 0)
        correct = stats.get("correct", 0)
        avg_p30 = stats.get("avg_post30")
        avg_p60 = stats.get("avg_post60")

        trigger_rate = (triggered / with_sl * 100) if with_sl else 0
        accuracy = (correct / triggered * 100) if triggered else 0

        lines = [
            f"**손절 통계**",
            f"- 전체 리포트: {total}건",
            f"- 손절가 설정: {with_sl}건",
            f"- 손절 발동: {triggered}건 ({trigger_rate:.1f}%)",
            f"- 손절 정확도 (cut이 맞았음): {correct}건 ({accuracy:.1f}%)",
        ]
        if avg_p30 is not None:
            lines.append(f"- 손절 후 30일 평균 수익: {avg_p30:+.1f}%")
        if avg_p60 is not None:
            lines.append(f"- 손절 후 60일 평균 수익: {avg_p60:+.1f}%")

        if examples:
            lines.append(f"\n**최근 손절 사례 ({len(examples)}건)**")
            for e in examples:
                correct_tag = "✓" if e.get("stop_was_correct") else "✗"
                p30 = e.get("post_stop_30d")
                p30_str = f"{p30:+.1f}%" if p30 is not None else "n/a"
                lines.append(
                    f"  {e['ticker']} {e.get('company_name','')} | "
                    f"{e['analysis_date']} → {e.get('stop_loss_date','?')} | "
                    f"post30d={p30_str} {correct_tag}"
                )

        body = "\n".join(lines)
        return InsightReport(
            title="손절 분석 리포트",
            body=body,
            data={"stats": stats, "examples": examples},
            market=market,
        )

    # ------------------------------------------------------------------
    # Market phase correlation
    # ------------------------------------------------------------------

    async def market_phase_report(
        self,
        market: Optional[str] = None,
    ) -> InsightReport:
        """
        Return distribution grouped by market phase at analysis time.

        Shows how analysis accuracy varies across bull/bear/sideways regimes.
        """
        await init_db(self.db_path)
        market_clause = "AND market = ?" if market else ""
        params: list = [market] if market else []

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                f"""SELECT market_phase,
                           COUNT(*) as cnt,
                           AVG(return_7d) as avg_7d,
                           AVG(return_30d) as avg_30d,
                           AVG(return_90d) as avg_90d,
                           SUM(CASE WHEN stop_loss_triggered = 1 THEN 1 ELSE 0 END) as sl_count
                    FROM report_enrichment
                    WHERE market_phase IS NOT NULL {market_clause}
                    GROUP BY market_phase
                    ORDER BY cnt DESC""",
                params,
            )
            phases = [dict(r) for r in await cur.fetchall()]

        if not phases:
            return InsightReport(
                title="시장국면별 분석 성과",
                body="아직 enrichment 데이터가 없습니다.",
                data={},
                market=market,
            )

        lines = [
            f"{'국면':<12} {'건수':>5} {'7d평균':>8} {'30d평균':>8} {'90d평균':>8} {'손절':>5}",
            "-" * 55,
        ]
        for p in phases:
            phase = p.get("market_phase") or "unknown"
            cnt = p.get("cnt", 0) or 0
            a7 = f"{p['avg_7d']:+.1f}%" if p.get("avg_7d") is not None else "n/a"
            a30 = f"{p['avg_30d']:+.1f}%" if p.get("avg_30d") is not None else "n/a"
            a90 = f"{p['avg_90d']:+.1f}%" if p.get("avg_90d") is not None else "n/a"
            sl = p.get("sl_count", 0) or 0
            lines.append(f"{phase:<12} {cnt:>5} {a7:>8} {a30:>8} {a90:>8} {sl:>5}")

        body = "\n".join(lines)
        return InsightReport(
            title="시장국면별 분석 성과",
            body=body,
            data={"phases": phases},
            market=market,
        )

    # ------------------------------------------------------------------
    # Weekly summary (with optional LLM narrative)
    # ------------------------------------------------------------------

    async def weekly_summary(
        self,
        date: Optional[str] = None,
        market: Optional[str] = None,
        with_narrative: bool = False,
    ) -> InsightReport:
        """
        Aggregated weekly insight: reports filed, avg returns, top performer,
        market phase distribution.

        Args:
            date: Any date within the target week (default: today)
            market: 'kr', 'us', or None
            with_narrative: If True, generate LLM narrative summary
        """
        ref = datetime.strptime(date, "%Y-%m-%d") if date else datetime.today()
        week_start = (ref - timedelta(days=ref.weekday())).strftime("%Y-%m-%d")
        week_end = (ref + timedelta(days=6 - ref.weekday())).strftime("%Y-%m-%d")

        await init_db(self.db_path)
        market_clause = "AND ra.market = ?" if market else ""
        params_base: list = [week_start, week_end] + ([market] if market else [])

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            # Report count
            cur = await db.execute(
                f"""SELECT COUNT(*) as cnt, COUNT(DISTINCT ticker) as tickers
                    FROM report_archive ra
                    WHERE ra.report_date BETWEEN ? AND ? {market_clause}""",
                params_base,
            )
            row = await cur.fetchone()
            cnt = row["cnt"] if row else 0
            tickers = row["tickers"] if row else 0

            # Avg returns
            cur = await db.execute(
                f"""SELECT AVG(re.return_7d) as avg_7d,
                           AVG(re.return_30d) as avg_30d,
                           AVG(re.return_90d) as avg_90d
                    FROM report_enrichment re
                    JOIN report_archive ra ON ra.id = re.report_id
                    WHERE ra.report_date BETWEEN ? AND ? {market_clause}""",
                params_base,
            )
            avgs = dict(await cur.fetchone() or {})

            # Top performer of the week
            cur = await db.execute(
                f"""SELECT re.ticker, ra.company_name, re.return_30d, re.market_phase
                    FROM report_enrichment re
                    JOIN report_archive ra ON ra.id = re.report_id
                    WHERE ra.report_date BETWEEN ? AND ? {market_clause}
                      AND re.return_30d IS NOT NULL
                    ORDER BY re.return_30d DESC LIMIT 1""",
                params_base,
            )
            top_row = await cur.fetchone()
            top = dict(top_row) if top_row else None

        a7 = f"{avgs.get('avg_7d', 0):+.1f}%" if avgs.get("avg_7d") is not None else "n/a"
        a30 = f"{avgs.get('avg_30d', 0):+.1f}%" if avgs.get("avg_30d") is not None else "n/a"
        a90 = f"{avgs.get('avg_90d', 0):+.1f}%" if avgs.get("avg_90d") is not None else "n/a"

        lines = [
            f"**주간 요약 ({week_start} ~ {week_end})**\n",
            f"- 분석 리포트: {cnt}건 ({tickers} 종목)",
            f"- 평균 수익률: 7d={a7}  30d={a30}  90d={a90}",
        ]
        if top:
            lines.append(
                f"- 주간 MVP: {top['ticker']} {top.get('company_name','')} "
                f"(30d={top['return_30d']:+.1f}%, {top.get('market_phase','?')})"
            )

        body = "\n".join(lines)

        # Optional LLM narrative
        if with_narrative and cnt > 0:
            api_key = load_api_key()
            if api_key:
                context = _sanitize_for_llm(
                    body + "\n\n" + json.dumps(
                        {"avgs": avgs, "top": top, "count": cnt, "tickers": tickers},
                        ensure_ascii=False,
                    )
                )
                narrative = await synthesize(
                    query=f"{week_start}~{week_end} 주간 분석 성과를 요약하고 인사이트를 제시하세요.",
                    context=context,
                    api_key=api_key,
                    model=self.model,
                )
                body += f"\n\n**AI 인사이트**\n{narrative}"

        return InsightReport(
            title=f"주간 아카이브 인사이트 — {week_start}",
            body=body,
            data={"week_start": week_start, "week_end": week_end,
                  "count": cnt, "tickers": tickers, "avgs": avgs, "top": top},
            market=market,
        )

    # ------------------------------------------------------------------
    # Persistent insight weekly compression
    # ------------------------------------------------------------------
    async def compress_weekly_insights(
        self,
        week_start: Optional[str] = None,
        week_end: Optional[str] = None,
    ) -> Optional[int]:
        """
        Compress persistent_insights created between week_start~week_end into
        one weekly_insight_summary row + mark them as superseded.

        Returns the summary_id on success, None on skip/failure.
        Default window: last completed Mon~Sun relative to today.
        """
        from datetime import datetime, timedelta

        if not week_start or not week_end:
            today = datetime.today()
            last_sunday = today - timedelta(days=today.weekday() + 1)
            last_monday = last_sunday - timedelta(days=6)
            week_start = last_monday.strftime("%Y-%m-%d")
            week_end = last_sunday.strftime("%Y-%m-%d")

        await init_db(self.db_path)

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                """
                SELECT id, question, key_takeaways, tickers_mentioned
                FROM persistent_insights
                WHERE superseded_by IS NULL
                  AND DATE(created_at) >= ? AND DATE(created_at) <= ?
                ORDER BY id ASC
                """,
                (week_start, week_end),
            )
            rows = await cur.fetchall()

        if not rows or len(rows) < 6:
            logger.info(
                f"weekly compression skip: {len(rows)} rows in "
                f"{week_start}~{week_end}"
            )
            return None

        # Aggregate top tickers
        tick_counts: Dict[str, int] = {}
        for r in rows:
            try:
                tks = json.loads(r["tickers_mentioned"] or "[]")
            except Exception:
                tks = []
            for t in tks:
                tick_counts[t] = tick_counts.get(t, 0) + 1
        top_tickers = sorted(tick_counts.items(), key=lambda x: -x[1])[:5]

        # Build compact input
        content_lines: List[str] = []
        for r in rows:
            try:
                tk = json.loads(r["key_takeaways"] or "[]")
            except Exception:
                tk = []
            ta = " | ".join(tk[:2])
            content_lines.append(f"- Q: {r['question'][:80]} | takeaways: {ta}")
        weekly_input = "\n".join(content_lines)

        # LLM compression
        api_key = load_api_key()
        if not api_key:
            logger.warning("weekly compression skipped: no API key")
            return None

        system_prompt = (
            "PRISM 장기투자 인사이트 축적 시스템의 주간 압축 엔진입니다. "
            "아래 Q&A 요약 목록에서 **재사용 가능한 공통 패턴**만 5~10개 bullet로 "
            "정리하세요. 개별 질문이 아닌 공통 패턴 중심. 한국어 합쇼체."
        )
        try:
            from .query_engine import _get_openai_client
            client = _get_openai_client(api_key)
            resp = await client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"## 주간 Q&A 목록\n{weekly_input}"},
                ],
                max_completion_tokens=1500,
                reasoning_effort="none",
                temperature=0.3,
            )
            summary_text = resp.choices[0].message.content or ""
        except Exception as e:
            logger.warning(f"weekly compression LLM failed: {e}")
            return None

        if not summary_text.strip():
            logger.warning("weekly compression: empty LLM response")
            return None

        # Insert summary row and mark sources as superseded
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                """
                INSERT OR IGNORE INTO weekly_insight_summary
                    (week_start, week_end, summary_text, source_insight_ids,
                     insight_count, top_tickers)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    week_start, week_end, summary_text,
                    json.dumps([r["id"] for r in rows]),
                    len(rows),
                    json.dumps(
                        [{"ticker": t, "count": c} for t, c in top_tickers],
                        ensure_ascii=False,
                    ),
                ),
            )
            await db.commit()
            summary_id = cur.lastrowid

        if summary_id is None:
            logger.warning("weekly compression: INSERT produced no id")
            return None

        from .persistent_insights import mark_superseded
        await mark_superseded(
            [r["id"] for r in rows], summary_id, db_path=self.db_path,
        )
        logger.info(
            f"weekly compression done: {len(rows)} insights → "
            f"summary_id={summary_id} ({week_start}~{week_end})"
        )
        return summary_id

    # ------------------------------------------------------------------
    # Run all insights
    # ------------------------------------------------------------------

    async def generate_all(
        self,
        market: Optional[str] = None,
        with_narrative: bool = False,
    ) -> List[InsightReport]:
        """Generate all insight reports. Useful for scheduled batch runs."""
        reports = await asyncio.gather(
            self.daily_digest(market=market),
            self.performance_leaderboard(days=30, market=market),
            self.stop_loss_analysis(market=market),
            self.market_phase_report(market=market),
            self.weekly_summary(market=market, with_narrative=with_narrative),
        )
        # Persistent insight weekly compression (market-agnostic — shared pool)
        try:
            await self.compress_weekly_insights()
        except Exception as e:
            logger.warning(f"compress_weekly_insights failed in generate_all: {e}")
        return list(reports)


# ---------------------------------------------------------------------------
# CLI entry point (for cron / manual invocation)
# ---------------------------------------------------------------------------

async def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="PRISM 아카이브 자동 인사이트 생성")
    parser.add_argument("--market", choices=["kr", "us"], help="시장 필터")
    parser.add_argument(
        "--type",
        choices=["daily", "leaderboard", "stoploss", "phase", "weekly", "compress", "all"],
        default="all",
        help="인사이트 유형 (기본값: all). compress=persistent_insights 주간 압축만 실행",
    )
    parser.add_argument("--week-start", dest="week_start", help="compress 전용: 시작일(월)")
    parser.add_argument("--week-end", dest="week_end", help="compress 전용: 종료일(일)")
    parser.add_argument("--days", type=int, default=30, help="리더보드 수익률 기간 (기본값: 30)")
    parser.add_argument("--date", help="대상 날짜 (YYYY-MM-DD, 기본값: 오늘)")
    parser.add_argument("--narrative", action="store_true", help="주간 요약에 LLM 내러티브 포함")
    parser.add_argument("--json", action="store_true", dest="as_json", help="JSON 출력")
    parser.add_argument("--dry-run", action="store_true", dest="dry_run", help="출력만 (전송 없음)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    ai = AutoInsight(model=_DEFAULT_MODEL)

    type_map = {
        "daily": lambda: ai.daily_digest(date=args.date, market=args.market),
        "leaderboard": lambda: ai.performance_leaderboard(days=args.days, market=args.market),
        "stoploss": lambda: ai.stop_loss_analysis(market=args.market),
        "phase": lambda: ai.market_phase_report(market=args.market),
        "weekly": lambda: ai.weekly_summary(date=args.date, market=args.market, with_narrative=args.narrative),
    }

    if args.type == "compress":
        sid = await ai.compress_weekly_insights(
            week_start=args.week_start, week_end=args.week_end,
        )
        print(f"compress result: summary_id={sid}")
        return

    if args.type == "all":
        reports = await ai.generate_all(market=args.market, with_narrative=args.narrative)
    else:
        reports = [await type_map[args.type]()]

    for report in reports:
        if args.as_json:
            print(report.to_json())
        else:
            print(f"\n{'='*60}")
            print(report.to_telegram(max_length=10000))
            print("=" * 60)


if __name__ == "__main__":
    import asyncio as _asyncio

    _asyncio.run(_main())
