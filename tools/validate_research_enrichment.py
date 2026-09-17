"""Operator-only paired report-section probe. No trading, channel sends or API-key fallback.

Outputs are private runtime research artifacts, not trading decisions or public reports.
The baseline and treatment run sequentially against current sources, so this is not
a point-in-time replay or a causal profitability experiment.
"""
import argparse
import asyncio
import importlib.util
import json
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def run(args):
    from dotenv import load_dotenv
    load_dotenv(args.env)
    from cores.chatgpt_proxy import inject_env, start_proxy, stop_proxy
    from cores.llm.agent_bridge import spec_from_mcp_agent
    from cores.llm.backends.openai_agents_backend import (
        OpenAIAgentsBackend,
        configure_openai_agents_for_proxy,
    )
    from cores.llm.config_loader import load_report_mcp_registry
    from cores.llm.ports import LLMParams
    from prism_core.report_research_context import apply_section_research
    from prism_core.report_research_prefetch import VERSION, prefetch_report_research
    from report_model_config import REPORT_EFFORT, REPORT_MODEL

    inject_env(args.port)
    if not await start_proxy(args.port):
        return {"status": "OAUTH_PROXY_UNAVAILABLE"}
    try:
        configure_openai_agents_for_proxy(f"http://localhost:{args.port}/v1")
        packet = await prefetch_report_research(args.market, args.symbol, args.date, args.company,
            _config={"version": VERSION, "enabled": True, "namespace": "validation", "timeout_seconds": 60})
        if args.market == "KR":
            from cores.agents.news_strategy_agents import create_news_analysis_agent
            agent = create_news_analysis_agent(args.company, args.symbol, args.date, "ko")
        else:
            path = ROOT / "prism-us/cores/agents/news_strategy_agents.py"
            spec = importlib.util.spec_from_file_location("validation_us_news", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            agent = module.create_us_news_analysis_agent(args.company, args.symbol, args.date, "ko")
        backend = OpenAIAgentsBackend(load_report_mcp_registry())
        results = []
        for arm in ("baseline", "enriched"):
            candidate = agent if arm == "baseline" else apply_section_research(
                agent, "news_analysis", {"report_research": packet}, args.date, "ko")
            spec = spec_from_mcp_agent(candidate, model=REPORT_MODEL,
                params=LLMParams(max_tokens=6000, reasoning_effort=REPORT_EFFORT, max_iterations=6))
            started = time.monotonic()
            try:
                result = await asyncio.wait_for(backend.run(spec,
                    f"Analyze {args.company} ({args.symbol}), {args.market}, reference {args.date}. "
                    "Assess business competitive position using 2-3 genuinely comparable peers. "
                    "Preserve missing or incomparable evidence. Do not place orders."), timeout=240)
                record = {"arm": arm, "status": "COMPLETED", "seconds": round(time.monotonic()-started, 3),
                          "model": REPORT_MODEL, "usage": result.usage, "text": result.text}
            except Exception as exc:  # noqa: BLE001 - isolated probe records safe failure types
                record = {"arm": arm, "status": "FAILED", "error_type": type(exc).__name__,
                          "seconds": round(time.monotonic()-started, 3)}
            results.append(record)
        return {"market": args.market, "symbol": args.symbol, "reference_date": args.date,
                "receipt": packet.get("receipt") if packet else None, "results": results,
                "limitations": "Sequential current-data comparison; no fills or performance inference."}
    finally:
        await stop_proxy()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", choices=["KR", "US"], required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--company", required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--env", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18749)
    args = parser.parse_args()
    logging.basicConfig(level=logging.CRITICAL)
    result = asyncio.run(run(args))
    args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    args.output.chmod(0o600)
    print(json.dumps({"status": result.get("status", "PROBE_FINISHED"),
                      "results": [{k: v for k, v in row.items() if k != "text"}
                                  for row in result.get("results", [])]}))


if __name__ == "__main__":
    main()
