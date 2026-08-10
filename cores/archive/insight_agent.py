"""
insight_agent.py — /insight 명령을 처리하는 메인 에이전트.

흐름:
  1. retrieval: persistent_insights (FTS + embedding) + weekly_summary + report_archive
  2. synthesis: mcp-agent Agent + AnthropicAugmentedLLM (기본 claude-sonnet-5,
                INSIGHT_MODEL 로 교체 가능)
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
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from mcp_agent.agents.agent import Agent
from mcp_agent.workflows.llm.augmented_llm import RequestParams
from mcp_agent.workflows.llm.augmented_llm_anthropic import AnthropicAugmentedLLM
from pydantic import BaseModel, Field

from . import persistent_insights as pi_store
from .archive_db import ARCHIVE_DB_PATH
from .embedding import embed_text
from .insight_prompts import INSIGHT_SYSTEM_PROMPT
from .query_engine import (
    QueryEngine,
    extract_query_tickers,
    load_api_key,
    parse_query_hints,
)

_KST = timezone(timedelta(hours=9))

logger = logging.getLogger(__name__)

# Claude handles MCP function calling reliably in this repo (firecrawl pattern).
# Overridable so model swaps don't need a code change — the report path already
# works this way via REPORT_MODEL.
DEFAULT_MODEL = os.getenv("INSIGHT_MODEL", "claude-sonnet-5")
_MAX_REPORTS_IN_CONTEXT = 6

# MCP 서버 연결 순서 — 무료 우선, 유료 후순 (프롬프트 가드레일과 함께 동작)
_MCP_SERVERS = ["yahoo_finance", "kospi_kosdaq", "perplexity", "firecrawl"]

# 도구 루프 상한. mcp-agent 기본값은 10인데, 그 값을 다 쓰면 마지막 턴에
# 라이브러리가 "도구를 멈추고 최종 답변을 하라"는 프롬프트를 주입한다.
# 그 프롬프트에는 JSON 요구가 없어서 모델이 산문으로 답하고, 결과적으로
# JSON 파싱이 깨진다. 상한에 닿지 않는 것이 1차 방어선이다.
_MAX_AGENT_ITERATIONS = 6

# 도구 화이트리스트 (bare 이름 → mcp-agent 가 namespaced 이름으로 노출).
#
# firecrawl 서버는 도구를 24개 노출한다 — browser_*, monitor_*, crawl, extract,
# search_feedback 등 이 에이전트가 쓸 일이 없는 것들이다. 이 숲을 그대로 주면
# 모델이 길을 잃는다. 실제로 Sonnet 5 는 `firecrawl_search_feedback` 을
# 전부 0 인 UUID 로 호출하고 `perplexity_ask` 를 'placeholder'·'ignore' 같은
# 인자로 8번 호출해 유료 크레딧을 태웠다.
#
# 필터 dict 에 없는 서버는 필터링되지 않는다(전체 허용). yahoo_finance 는
# 서버가 제공하는 금융 도구 전체를 쓸 수 있도록 의도적으로 비워둔다.
_MCP_TOOL_ALLOWLIST = {
    "perplexity": {"perplexity_ask"},
    "firecrawl": {"firecrawl_scrape"},
    "kospi_kosdaq": {
        "load_all_tickers",
        "get_stock_ohlcv",
        "get_stock_market_cap",
        "get_stock_fundamental",
        "get_stock_trading_volume",
        "get_index_ohlcv",
        "get_sector_info",
    },
}

# generate_str() 은 모든 반복의 assistant 턴을 이어붙이므로 tool_use 블록이
# 이 문자열로 함께 들어온다. 사용자에게 절대 나가면 안 되는 표식이다.
_TRACE_MARKER = "[Calling tool "


def _is_archive_only_question(question: str) -> bool:
    """Return True when the user explicitly forbids external research."""
    patterns = (
        r"(?:PRISM|프리즘)?\s*(?:리포트|보고서|아카이브)\s*(?:만|만을|에만)",
        r"외부\s*(?:검색|도구|자료)\s*(?:없이|금지|사용하지)",
        r"웹\s*검색\s*(?:없이|금지|사용하지)",
        r"\b(?:archive|prism)\s+(?:reports?\s+)?only\b",
        r"\bno\s+(?:external|web)\s+(?:search|tools?|sources?)\b",
    )
    return any(re.search(pattern, question, re.IGNORECASE) for pattern in patterns)


def _extract_actual_tools(raw: str) -> List[str]:
    """Extract de-duplicated tool names from mcp-agent's actual call trace."""
    tools: List[str] = []
    for name in re.findall(r"\[Calling tool\s+([^\s\]]+)", raw or ""):
        if name not in tools:
            tools.append(name)
    return tools[:10]


def _balanced_json_objects(text: str) -> List[str]:
    """텍스트에서 균형 잡힌 최상위 `{...}` 덩어리를 순서대로 뽑는다.

    문자열 리터럴 안의 중괄호와 이스케이프를 건너뛴다. 최종 판정은 호출부의
    `json.loads` 가 하므로 여기서는 후보만 만든다.
    """
    out: List[str] = []
    depth = 0
    start = None
    in_str = False
    escaped = False
    for i, ch in enumerate(text):
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    out.append(text[start:i + 1])
                    start = None
    return out


class InsightJSON(BaseModel):
    """구조화 복구 패스용 스키마. 프롬프트의 응답 형식과 같은 모양이다."""

    answer: str = ""
    key_takeaways: List[str] = Field(default_factory=list)
    tickers_mentioned: List[str] = Field(default_factory=list)
    tools_used: List[str] = Field(default_factory=list)
    evidence_report_ids: List[int] = Field(default_factory=list)


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
        engine = QueryEngine(db_path=self.db_path, model=self.model)
        hints = parse_query_hints(question)
        explicit_tickers = extract_query_tickers(question)

        async def retrieve_reports():
            if len(explicit_tickers) <= 1:
                return await engine.retrieve(
                    text=question, market=hints["market"],
                    ticker=hints["ticker"], date_from=hints["date_from"],
                    date_to=hints["date_to"],
                )
            batches = await asyncio.gather(*(
                engine.retrieve(
                    text=question, market=hints["market"], ticker=ticker,
                    date_from=hints["date_from"], date_to=hints["date_to"],
                )
                for ticker in explicit_tickers
            ))
            seen: set[int] = set()
            merged = []
            for batch in batches:
                for report in batch:
                    if report.report_id not in seen:
                        seen.add(report.report_id)
                        merged.append(report)
            return merged[:_MAX_REPORTS_IN_CONTEXT]

        reports_task = retrieve_reports()

        # "PRISM 리포트만" means exactly that: skip accumulated summaries and
        # derived facts as well as all external MCP tools.
        if _is_archive_only_question(question):
            reports = await reports_task
            return {
                "insights": [], "weekly": [],
                "reports": (reports or [])[:_MAX_REPORTS_IN_CONTEXT],
                "outcomes": {}, "semantic_facts": {}, "q_emb": None,
            }

        q_emb = await embed_text(question, api_key) if api_key else None

        insights_task = pi_store.search_insights(
            question, q_emb, limit=5, exclude_superseded=True, db_path=self.db_path,
        )
        weekly_task = pi_store.recent_weekly_summaries(weeks=4, db_path=self.db_path)
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

    def _ground_response_metadata(
        self,
        parsed: Dict[str, Any],
        ctx: Dict[str, Any],
        response_text: str,
    ) -> Dict[str, Any]:
        """Replace model self-reporting with server-observed provenance."""
        allowed_ids = [r.report_id for r in (ctx.get("reports") or [])]
        allowed_set = set(allowed_ids)
        claimed_ids = parsed.get("evidence_report_ids") or []
        evidence_ids = [rid for rid in claimed_ids if rid in allowed_set]
        if not evidence_ids and allowed_ids:
            evidence_ids = allowed_ids

        actual_tools = _extract_actual_tools(response_text)
        tools_used = (["archive_retrieval"] if allowed_ids else []) + actual_tools
        parsed["evidence_report_ids"] = evidence_ids[:10]
        parsed["tools_used"] = tools_used[:10]
        return parsed

    # ------------------------------------------------------------------
    # JSON response parser — resilient to model quirks
    # ------------------------------------------------------------------
    def _parse_response(self, raw: str) -> Optional[Dict[str, Any]]:
        """생성 텍스트에서 JSON 을 뽑는다. 실패하면 None — 원문을 답변으로
        쓰지 않는다.

        예전에는 파싱 실패 시 `raw[:1500]` 을 답변으로 내보냈다. generate_str()
        이 도구 호출 추적을 포함하므로, 그 폴백은 내부 추적 텍스트를 그대로
        사용자에게 보내는 경로였다(실제 사고: insight id=29, 정확히 1500자).
        """
        # JSON 블록 추출 (```json ... ``` 또는 순수 JSON)
        text = raw.strip()
        fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        candidates: List[str] = [fence.group(1)] if fence else []
        # 펜스가 없으면 균형 잡힌 최상위 객체를 뒤에서부터 시도한다.
        # `re.search(r"\{.*\}", ...)` 는 탐욕적이라 도구 추적의 첫 `{` 부터
        # 마지막 `}` 까지를 한 덩어리로 잡아 깨진다 — 추적 뒤에 순수 JSON 이
        # 오는 실제 형태에서 정확히 그 일이 났다.
        if not candidates:
            candidates = list(reversed(_balanced_json_objects(text)))
        for candidate in candidates:
            try:
                obj = json.loads(candidate)
                if "answer" not in obj:
                    continue
                answer = str(obj.get("answer") or "").strip()
                if not answer or _TRACE_MARKER in answer:
                    logger.warning(
                        "InsightAgent: answer 가 비었거나 도구 추적을 포함한다"
                    )
                    return None
                return {
                    "answer": answer,
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
        logger.warning("InsightAgent: 응답에서 JSON 을 찾지 못했다")
        return None

    async def _repair_to_json(
        self, llm, question: str, context_str: str, draft: str,
    ) -> Optional[Dict[str, Any]]:
        """JSON 파싱이 실패했을 때의 복구 패스.

        `generate_structured` 는 Anthropic 의 강제 tool_call 로 스키마를 받아내는
        한 턴 호출이라 형식이 보장된다. 도구를 주지 않으므로 추가 유료 호출도 없다.

        **`use_history=False` 가 핵심이다.** 파싱이 깨지는 상황은 대개 도구 루프가
        중간에 끊긴 경우인데, 그때 이력의 마지막이 `tool_result` 없는 `tool_use` 로
        남는다. 그 이력을 재생하면 Anthropic 이 400 으로 거절한다:

            messages.4: `tool_use` ids were found without `tool_result` blocks
            immediately after

        그래서 이력을 버리고 질문·컨텍스트·조사 메모를 직접 실어 자립적인 한 턴으로
        만든다. 메모에서 도구 추적 줄은 걷어낸다 — 모델에게 다시 먹일 내용이 아니다.
        """
        clean_draft = "\n".join(
            ln for ln in (draft or "").splitlines()
            if not ln.lstrip().startswith(_TRACE_MARKER)
        ).strip()

        msg = (
            f"## 사용자 질문\n{question}\n\n"
            f"## 컨텍스트 (누적 인사이트 + 리포트)\n{context_str}\n\n"
        )
        if clean_draft:
            msg += f"## 앞선 조사 메모\n{clean_draft[:2000]}\n\n"
        msg += (
            "위 내용만으로 최종 답변을 만드세요. 도구를 쓰지 마세요.\n"
            "answer 는 합쇼체 400~1200자로, 확인된 사실만 담으세요."
        )

        try:
            result = await llm.generate_structured(
                message=msg,
                response_model=InsightJSON,
                request_params=RequestParams(
                    model=self.model, maxTokens=4000,
                    max_iterations=1, use_history=False,
                ),
            )
        except Exception as e:
            logger.error(f"InsightAgent structured 복구 실패: {e}")
            return None

        answer = (result.answer or "").strip()
        if not answer or _TRACE_MARKER in answer:
            return None
        logger.info("InsightAgent: structured 복구 패스로 JSON 을 확보했다")
        return {
            "answer": answer,
            "key_takeaways": [str(x) for x in result.key_takeaways if x][:5],
            "tickers_mentioned": [
                str(x).upper() for x in result.tickers_mentioned if x
            ][:10],
            "tools_used": [str(x) for x in result.tools_used if x][:10],
            "evidence_report_ids": [
                int(x) for x in result.evidence_report_ids
                if str(x).lstrip("-").isdigit()
            ][:10],
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

        # 3. Agent + LLM (archive-only legacy mcp-agent path)
        response_text = ""
        parsed: Optional[Dict[str, Any]] = None
        try:
            today_kst = datetime.now(_KST).strftime("%Y-%m-%d")
            dated_prompt = (
                f"# 오늘 날짜: {today_kst} (KST)\n"
                "- 날짜 관련 모든 질문/응답에서 이 날짜를 기준으로 해석하세요.\n"
                "- 'N일 수익률', '최근', '올해', '30거래일' 등의 표현은 이 기준일로부터 역산합니다.\n"
                "- 외부 도구 호출 시에도 이 기준일 범위의 데이터를 요청하세요.\n\n"
                f"{INSIGHT_SYSTEM_PROMPT}"
            )
            archive_only = _is_archive_only_question(question)
            server_names = [] if archive_only else _MCP_SERVERS
            tool_filter = {} if archive_only else _MCP_TOOL_ALLOWLIST
            if archive_only:
                dated_prompt += (
                    "\n\n# 이번 요청의 도구 정책\n"
                    "사용자가 PRISM 아카이브만 요청했습니다. 외부 검색이나 "
                    "MCP 도구를 사용하지 말고 제공된 리포트만 근거로 답하세요."
                )
            agent = Agent(
                name="insight_agent",
                instruction=dated_prompt,
                server_names=server_names,
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
                            max_iterations=_MAX_AGENT_ITERATIONS,
                            tool_filter=tool_filter,
                        ),
                    )
                    # 세션이 살아 있는 동안 파싱한다. 실패 시 같은 대화 이력
                    # 위에서 구조화 복구를 한 번 시도한다.
                    parsed = self._parse_response(response_text)
                    if parsed is None:
                        parsed = await self._repair_to_json(
                            llm, question, context_str, response_text,
                        )
            except Exception as agent_err:
                logger.error(
                    f"InsightAgent LLM call failed: {agent_err}", exc_info=True
                )
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

        # 4. 파싱·복구가 모두 실패하면 여기서 끝낸다.
        #    원문에는 도구 호출 추적이 섞여 있어 사용자에게 내보낼 수 없다.
        if parsed is None:
            logger.error(
                "InsightAgent: JSON 확보 실패 — 응답을 폐기한다 "
                f"(raw {len(response_text)}자, trace={_TRACE_MARKER in response_text})"
            )
            return InsightResult(
                answer=(
                    "⚠️ 답변을 정리하는 데 실패했습니다. "
                    "질문 범위를 좁혀 다시 시도해주세요."
                ),
                key_takeaways=[], tickers_mentioned=[], tools_used=[],
                evidence_report_ids=[],
                remaining_quota=remaining, model_used=self.model,
            )

        parsed = self._ground_response_metadata(parsed, ctx, response_text)

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
