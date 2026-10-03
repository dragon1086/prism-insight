"""LLM recheck of re-entry v2 SHADOW triggers, run beside production (one BUY call per trigger).

Only triggers whose session completed after the shadow started are rechecked (forward
evidence; backfilled triggers stay NOT_EVALUATED). The BUY agent gets the production
instruction plus the recheck section and exactly the inputs frozen at the trigger. Codex
runs WITHOUT MCP tools: the runner works after the close, so tools could read prices
after the intraday breakout. Reports older than 30 days are not regenerated; the frozen
stale flag is kept for the later comparison (user decision 2026-09-27).

No orders and no DB writes. Design: docs/REENTRY_V2_DESIGN_20260927_ko.md section 15.
"""
from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from observability.reentry_recheck_inputs import (
    archived_report, latest_report, recheck_instruction, strip_embedded_images,
)

RESULT_CONTRACT = "reentry_v2_recheck_result_v1"
MAX_ATTEMPTS = 2            # a failed call is retried once, on the next run
FINAL = {"OK", "NO_REPORT", "REPORT_CHANGED"}
APPROVE = {"진입", "enter", "entry"}


def eligible(watch, started_session):
    rc = watch.get("recheck") or {}
    return (bool(watch.get("trigger_event_id")) and watch.get("trigger_date", "") > (started_session or "9999")
            and rc.get("status") not in FINAL and rc.get("attempts", 0) < MAX_ATTEMPTS)


def load_report(item, reports_root, archive_db):
    """The frozen report text, re-resolved by the same point-in-time rule and checked by hash."""
    ref = item.get("report_ref")
    if not ref:
        return None, "NO_REPORT"
    if ref["kind"] == "file":
        report = latest_report(reports_root, item["market"], item["ticker"], item["trigger_date"])
    else:
        report = archived_report(archive_db, item["market"], item["ticker"], item["trigger_date"]) \
            if archive_db and Path(archive_db).exists() else None
    if report is None:
        return None, "REPORT_CHANGED"
    text = report.read_text(encoding="utf-8")
    if report.name != ref["name"] or hashlib.sha256(text.encode()).hexdigest() != ref["sha256"]:
        return None, "REPORT_CHANGED"
    return text, None


def _levels_line(levels):
    if not levels:
        return "- 원래 시나리오 가격 수준: 미제공\n"
    fmt = lambda v: "미제공" if v is None else f"{v:,.2f}"  # noqa: E731
    return (f"- 원래 시나리오 가격 수준(원래 판단 당시): 1차 지지 {fmt(levels.get('primary_support'))} / "
            f"2차 지지 {fmt(levels.get('secondary_support'))} / 1차 저항 {fmt(levels.get('primary_resistance'))} / "
            f"2차 저항 {fmt(levels.get('secondary_resistance'))}\n")


def _declined_block(declined):
    if not declined:
        return ""
    lines = ["### 이전 재점검 (미진입, 같은 감시)"]
    for d in declined:
        support = "미제공" if d.get("support") is None else f"{d['support']:,.2f}"
        lines.append(f"- {d['date']} {d['trigger']} 진입가 {d['entry']:,.2f}, 기다린 지지선 {support}: "
                     f"{str(d.get('reason') or '')[:300]}")
    return "\n".join(lines) + "\n\n"


def user_prompt(item, report_text):
    original = item["original"]
    ref = item["report_ref"]
    regime = item.get("deterministic_market_regime") or "미제공"
    return ("재진입 재점검 요청입니다.\n\n### 원래 판단\n"
            f"- 출처: {item['source']} / 원래 판단일 {original['decided_on']}\n"
            f"- 원래 점수/최소점수: {original.get('buy_score')}/{original.get('min_score')}\n"
            f"- 원래 사유: {str(original.get('reason') or '')[:500]}\n"
            f"{_levels_line(original.get('key_levels'))}\n"
            f"{_declined_block(item.get('declined'))}"
            f"### 시장 국면(결정론적 계산): {regime}\n\n{item['facts_text']}\n"
            f"### 보고서 작성일: {ref['report_date']} (트리거일까지 {ref['age_days']}일 경과)\n\n"
            f"### Report Content:\n{strip_embedded_images(report_text)}\n")


async def _codex(system, user):
    from cores.llm.codex_oauth_fast_backend import generate_codex_fast_async
    from prism_core.codex_config import resolve_buy_codex_settings
    settings = resolve_buy_codex_settings()
    result = await generate_codex_fast_async(system_prompt=system, user_prompt=user, model=settings.model,
                                             reasoning_effort=settings.reasoning_effort, timeout=settings.timeout,
                                             mcp_profile=None, require_mcp_calls=False)
    return result.text, {"model": settings.model, "reasoning_effort": settings.reasoning_effort,
                         "latency_s": round(result.latency_s, 1)}


def recheck(item, *, reports_root, archive_db, llm=None, instruction=None, prompt_fn=None, contract=None):
    """One recheck record. `llm(system, user)` is an async callable returning (text, meta).

    prompt_fn(item, report_text) and contract default to the v2 user prompt and result contract;
    re-entry v3 passes its own.
    """
    record = {"contract": contract or RESULT_CONTRACT, "event_id": item["event_id"], "watch_ref": item["watch_ref"],
              "market": item["market"], "ticker": item["ticker"], "source": item["source"],
              "trigger_date": item["trigger_date"], "entry": item["entry"],
              "report_stale": (item.get("report_ref") or {}).get("stale"),
              "evaluated_at": datetime.now(timezone.utc).isoformat()}
    text, problem = load_report(item, reports_root, archive_db)
    if problem:
        return {**record, "status": problem}
    try:
        from cores.utils import parse_llm_json
        system = instruction if instruction is not None else recheck_instruction(item["market"])
        raw, meta = asyncio.run((llm or _codex)(system, (prompt_fn or user_prompt)(item, text)))
        scenario = parse_llm_json(raw, context="reentry v2 shadow recheck") or {}
        decision = str(scenario.get("decision", "")).strip().lower()
        return {**record, **meta, "status": "OK" if scenario else "PARSE_ERROR",
                "decision": scenario.get("decision"), "approved": decision in APPROVE,
                "buy_score": scenario.get("buy_score"), "min_score": scenario.get("min_score"),
                "rejection_reason": scenario.get("rejection_reason"), "scenario": scenario}
    except Exception as error:  # noqa: BLE001 - one failure never stops the shadow run
        return {**record, "status": "ERROR", "error": f"{type(error).__name__}: {str(error)[:200]}"}
