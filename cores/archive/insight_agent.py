"""
insight_agent.py — /insight 명령을 처리하는 메인 에이전트.

흐름:
  1. retrieval: persistent_insights (FTS + embedding) + weekly_summary + report_archive
  2. synthesis: mcp-agent Agent + AnthropicAugmentedLLM (claude-sonnet-4-6)
                function calling으로 필요시 MCP 도구 자동 선택
                (perplexity / firecrawl / yahoo_finance / kospi_kosdaq)

                OpenAI gpt-5.x reasoning 모델은 function calling과 reasoning_effort를
                동시에 지원하지 않아 (400 invalid_request_error) Claude로 전환.
                OpenAI는 embedding / 비-tool synthesize 전용.
  3. storage:   persistent_insights INSERT (fire-and-forget 성격이지만 동기로 기다림)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from datetime import datetime, timezone, timedelta

from mcp_agent.agents.agent import Agent
from mcp_agent.workflows.llm.augmented_llm import RequestParams
from mcp_agent.workflows.llm.augmented_llm_anthropic import AnthropicAugmentedLLM

_KST = timezone(timedelta(hours=9))

from . import persistent_insights as pi_store
from .archive_db import ARCHIVE_DB_PATH
from .embedding import embed_text
from .insight_prompts import INSIGHT_SYSTEM_PROMPT
from .query_engine import QueryEngine, load_api_key

logger = logging.getLogger(__name__)

# Claude handles MCP function calling reliably in this repo (firecrawl pattern).
DEFAULT_MODEL = "claude-sonnet-4-6"
_MAX_REPORTS_IN_CONTEXT = 6

# MCP 서버 연결 순서 — 무료 우선, 유료 후순 (프롬프트 가드레일과 함께 동작)
_MCP_SERVERS = ["yahoo_finance", "kospi_kosdaq", "perplexity", "firecrawl"]


@dataclass
class InsightResult:
    answer: str
    key_takeaways: List[str]
    tickers_mentioned: List[str]
    tools_used: List[str]
    evidence_report_ids: List[int]
    insight_id: Optional[int] = None
    remaining_quota: int = -1
    model_used: str = DEFAULT_MODEL


class InsightAgent:
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        db_path: Optional[str] = None,
    ):
        self.model = model
        self.db_path = db_path or str(ARCHIVE_DB_PATH)
        self._api_key: Optional[str] = None

    # ------------------------------------------------------------------
    # Retrieval (5-tier: insights + weekly + reports + semantic facts + outcomes)
    # ------------------------------------------------------------------
    async def _build_retrieval_context(self, question: str) -> Dict[str, Any]:
        api_key = self._api_key or load_api_key()
        self._api_key = api_key
        q_emb = await embed_text(question, api_key) if api_key else None

        engine = QueryEngine(db_path=self.db_path, model=self.model)

        insights_task = pi_store.search_insights(
            question, q_emb, limit=5, exclude_superseded=True, db_path=self.db_path,
        )
        weekly_task = pi_store.recent_weekly_summaries(weeks=4, db_path=self.db_path)
        reports_task = engine.retrieve(
            text=question, market=None, ticker=None,
            date_from=None, date_to=None,
        )
        insights, weekly, reports = await asyncio.gather(
            insights_task, weekly_task, reports_task,
            return_exceptions=True,
        )
        if isinstance(insights, Exception):
            logger.warning(f"insight retrieval failed: {insights}")
            insights = []
        if isinstance(weekly, Exception):
            weekly = []
        if isinstance(reports, Exception):
            reports = []

        insights = insights or []
        reports = (reports or [])[:_MAX_REPORTS_IN_CONTEXT]

        # Outcome grounding + semantic facts (Phase B):
        # collect tickers from retrieved insights + reports, then JOIN
        # report_enrichment for objective return data and ticker_semantic_facts
        # for distilled per-ticker knowledge.
        ticker_set: set = set()
        for ins in insights:
            for t in (ins.tickers_mentioned or []):
                if t:
                    ticker_set.add(str(t).upper())
        for r in reports:
            if r.ticker:
                ticker_set.add(str(r.ticker).upper())
        tickers = sorted(ticker_set)[:20]   # cap for context size

        outcomes_task = pi_store.fetch_outcomes_for_tickers(
            tickers, db_path=self.db_path,
        ) if tickers else asyncio.sleep(0, result={})
        facts_task = pi_store.get_semantic_facts_for_tickers(
            tickers, limit_per_ticker=3, db_path=self.db_path,
        ) if tickers else asyncio.sleep(0, result={})
        outcomes, semantic_facts = await asyncio.gather(
            outcomes_task, facts_task, return_exceptions=True,
        )
        if isinstance(outcomes, Exception):
            outcomes = {}
        if isinstance(semantic_facts, Exception):
            semantic_facts = {}

        return {
            "insights": insights,
            "weekly": weekly or [],
            "reports": reports,
            "outcomes": outcomes,
            "semantic_facts": semantic_facts,
            "q_emb": q_emb,
        }

    def _format_context(self, ctx: Dict[str, Any]) -> str:
        parts: List[str] = []

        # Tier 1 — distilled semantic facts per ticker (Mem0 pattern)
        sf = ctx.get("semantic_facts") or {}
        if sf:
            parts.append("## 종목별 누적 사실 (자동 증류, 신뢰도 정렬)")
            for ticker, facts in sf.items():
                parts.append(f"- **{ticker}**")
                for f in facts:
                    cat = f.get("category") or "?"
                    conf = f.get("confidence", 0.0)
                    parts.append(
                        f"  · [{cat}|conf={conf:.2f}] {f['fact'][:240]}"
                    )

        # Tier 2 — objective outcome grounding (수익률·MDD·시장국면 + 참조 기간)
        outcomes = ctx.get("outcomes") or {}
        if outcomes:
            parts.append("\n## 종목별 객관 결과 (report_enrichment)")
            for ticker, o in outcomes.items():
                # Data window is mandatory for verifiability — show prominently.
                first = o.get("first_analysis_date") or o.get("analysis_date") or "?"
                last_a = o.get("last_analysis_date") or o.get("analysis_date") or "?"
                last_p = o.get("last_price_update") or "?"
                rc = o.get("report_count")
                window_bits = [f"분석일범위={first}~{last_a}"]
                if rc:
                    window_bits.append(f"리포트수={rc}건")
                if last_p and last_p != "?":
                    window_bits.append(f"가격최종={last_p}")
                bits = []
                for k, label in [
                    ("return_30d", "30d"), ("return_90d", "90d"),
                    ("return_180d", "180d"), ("return_365d", "365d"),
                    ("return_current", "현재"),
                ]:
                    v = o.get(k)
                    if v is not None:
                        bits.append(f"{label}={v:+.1f}%")
                mdd = o.get("max_drawdown")
                if mdd is not None:
                    bits.append(f"MDD={mdd:+.1f}%")
                phase = o.get("market_phase")
                if phase:
                    bits.append(f"국면={phase}")
                parts.append(
                    f"- **{ticker}** [{' | '.join(window_bits)}]: "
                    + " | ".join(bits)
                )

        # Tier 3 — recent weekly summaries
        if ctx["weekly"]:
            parts.append("\n## 최근 주간 인사이트 요약")
            for w in ctx["weekly"]:
                parts.append(
                    f"- ({w['week_start']}~{w['week_end']}) "
                    f"건수={w.get('insight_count')} 주요종목={w.get('top_tickers')}"
                )
                parts.append(f"  {(w['summary_text'] or '')[:600]}")

        # Tier 4 — accumulated insights (top-5 with feedback signals applied)
        if ctx["insights"]:
            parts.append("\n## 누적 인사이트 (top-5)")
            for i, ins in enumerate(ctx["insights"], 1):
                parts.append(
                    f"{i}. [{ins.created_at[:10]}] Q: {ins.question[:120]}"
                )
                tk = " | ".join(ins.key_takeaways[:3])
                parts.append(f"   takeaways: {tk}")
                parts.append(
                    f"   ticker={ins.tickers_mentioned} "
                    f"evidence={ins.evidence_report_ids}"
                )

        # Tier 5 — raw report excerpts
        if ctx["reports"]:
            parts.append("\n## 관련 분석 리포트 (archive)")
            for r in ctx["reports"]:
                parts.append(
                    f"- id={r.report_id} [{r.report_date}] {r.ticker} "
                    f"{r.company_name} ({r.market.upper()})"
                )
                excerpt = ((r.content_excerpt or "")[:400]).replace("\n", " ")
                parts.append(f"  {excerpt}")
        return "\n".join(parts) if parts else "(관련 컨텍스트 없음)"

    # ------------------------------------------------------------------
    # JSON response parser — resilient to model quirks
    # ------------------------------------------------------------------
    def _parse_response(self, raw: str) -> Dict[str, Any]:
        # JSON 블록 추출 (```json ... ``` 또는 순수 JSON)
        text = raw.strip()
        fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        candidate = fence.group(1) if fence else None
        if not candidate:
            m = re.search(r"\{.*\}", text, re.DOTALL)
            candidate = m.group(0) if m else None
        if candidate:
            try:
                obj = json.loads(candidate)
                return {
                    "answer": str(obj.get("answer") or raw[:1500]),
                    "key_takeaways": [
                        str(x) for x in obj.get("key_takeaways", []) if x
                    ][:5],
                    "tickers_mentioned": [
                        str(x).upper() for x in obj.get("tickers_mentioned", []) if x
                    ][:10],
                    "tools_used": [
                        str(x) for x in obj.get("tools_used", []) if x
                    ][:10],
                    "evidence_report_ids": [
                        int(x) for x in obj.get("evidence_report_ids", [])
                        if str(x).lstrip("-").isdigit()
                    ][:10],
                }
            except Exception as e:
                logger.warning(f"InsightAgent JSON parse failed: {e}")
        logger.warning("InsightAgent: fallback to raw response (no JSON)")
        return {
            "answer": raw[:1500],
            "key_takeaways": [raw[:200]] if raw else [],
            "tickers_mentioned": [],
            "tools_used": [],
            "evidence_report_ids": [],
        }

    # ------------------------------------------------------------------
    # Main entrypoint
    # ------------------------------------------------------------------
    async def run(
        self,
        question: str,
        user_id: int,
        chat_id: int,
        daily_limit: int = 20,
        previous_insight_id: Optional[int] = None,
    ) -> InsightResult:
        # 1. Quota
        allowed, remaining = await pi_store.check_and_increment_quota(
            user_id, daily_limit, db_path=self.db_path,
        )
        if not allowed:
            return InsightResult(
                answer=(
                    "일일 `/insight` 호출 한도를 초과했습니다. "
                    "자정(KST) 이후 초기화됩니다."
                ),
                key_takeaways=[], tickers_mentioned=[], tools_used=[],
                evidence_report_ids=[], remaining_quota=0, model_used=self.model,
            )

        # 2. Retrieval
        ctx = await self._build_retrieval_context(question)
        context_str = self._format_context(ctx)

        # 3. Agent + LLM (mcp-agent global app reused)
        response_text = ""
        try:
            # global app 초기화 보장 (firecrawl 패턴과 동일)
            from report_generator import get_or_create_global_mcp_app, reset_global_mcp_app
            _ = await get_or_create_global_mcp_app()

            today_kst = datetime.now(_KST).strftime("%Y-%m-%d")
            dated_prompt = (
                f"# 오늘 날짜: {today_kst} (KST)\n"
                "- 날짜 관련 모든 질문/응답에서 이 날짜를 기준으로 해석하세요.\n"
                "- 'N일 수익률', '최근', '올해', '30거래일' 등의 표현은 이 기준일로부터 역산합니다.\n"
                "- 외부 도구 호출 시에도 이 기준일 범위의 데이터를 요청하세요.\n\n"
                f"{INSIGHT_SYSTEM_PROMPT}"
            )
            agent = Agent(
                name="insight_agent",
                instruction=dated_prompt,
                server_names=_MCP_SERVERS,
            )
            try:
                async with agent:
                    llm = await agent.attach_llm(AnthropicAugmentedLLM)
                    user_msg = (
                        f"## 사용자 질문\n{question}\n\n"
                        f"## 컨텍스트 (누적 인사이트 + 리포트)\n{context_str}\n\n"
                        "위 컨텍스트와 JSON 형식만으로 답하세요."
                    )
                    response_text = await llm.generate_str(
                        message=user_msg,
                        request_params=RequestParams(
                            model=self.model,
                            maxTokens=4000,
                        ),
                    )
            except Exception as agent_err:
                logger.error(
                    f"InsightAgent LLM call failed: {agent_err}", exc_info=True
                )
                try:
                    await reset_global_mcp_app()
                except Exception:
                    pass
                # Fallback: retrieval 원문 반환
                fallback = (
                    "[인사이트 엔진 오류] 관련 컨텍스트만 전달합니다.\n\n"
                    + context_str[:3000]
                )
                return InsightResult(
                    answer=fallback,
                    key_takeaways=[], tickers_mentioned=[], tools_used=[],
                    evidence_report_ids=[],
                    remaining_quota=remaining, model_used=self.model,
                )
        except Exception as outer_err:
            logger.error(
                f"InsightAgent outer failure: {outer_err}", exc_info=True
            )
            return InsightResult(
                answer=(
                    "⚠️ 인사이트 엔진 초기화 중 오류가 발생했습니다. "
                    "잠시 후 다시 시도해주세요."
                ),
                key_takeaways=[], tickers_mentioned=[], tools_used=[],
                evidence_report_ids=[],
                remaining_quota=remaining, model_used=self.model,
            )

        # 4. Parse response
        parsed = self._parse_response(response_text)

        # 5. Embedding for key_takeaways (fire-and-forget 성격)
        api_key = self._api_key or load_api_key()
        takeaway_text = (
            " \n".join(parsed["key_takeaways"])
            or parsed["answer"][:500]
        )
        emb_blob = await embed_text(takeaway_text, api_key) if api_key else None

        # 6. Save
        insight_id: Optional[int] = None
        try:
            insight_id = await pi_store.save_insight(
                user_id=user_id, chat_id=chat_id,
                question=question, answer=parsed["answer"],
                key_takeaways=parsed["key_takeaways"],
                tools_used=parsed["tools_used"],
                tickers_mentioned=parsed["tickers_mentioned"],
                evidence_report_ids=parsed["evidence_report_ids"],
                model_used=self.model, embedding=emb_blob,
                previous_insight_id=previous_insight_id,
                db_path=self.db_path,
            )
        except Exception as save_err:
            logger.error(f"save_insight failed: {save_err}", exc_info=True)

        # 7. Cost tracking (fire-and-forget)
        try:
            perp = parsed["tools_used"].count("perplexity") + sum(
                1 for t in parsed["tools_used"] if t.startswith("perplexity")
            )
            fcs = sum(
                1 for t in parsed["tools_used"] if t.startswith("firecrawl")
            )
            await pi_store.increment_cost(
                perplexity_calls=perp,
                firecrawl_calls=fcs,
                db_path=self.db_path,
            )
        except Exception:
            pass

        return InsightResult(
            answer=parsed["answer"],
            key_takeaways=parsed["key_takeaways"],
            tickers_mentioned=parsed["tickers_mentioned"],
            tools_used=parsed["tools_used"],
            evidence_report_ids=parsed["evidence_report_ids"],
            insight_id=insight_id,
            remaining_quota=remaining,
            model_used=self.model,
        )


# Standalone CLI smoke
if __name__ == "__main__":
    async def _main():
        a = InsightAgent()
        r = await a.run(
            "삼성전자 장기투자 적합한가?",
            user_id=1, chat_id=-1,
        )
        print("answer:", r.answer[:300])
        print("takeaways:", r.key_takeaways)
        print("tools:", r.tools_used)
        print("insight_id:", r.insight_id)
        print("remaining_quota:", r.remaining_quota)

    asyncio.run(_main())
