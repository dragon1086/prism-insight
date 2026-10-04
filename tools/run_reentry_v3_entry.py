#!/usr/bin/env python3
"""Execute approved re-entry v3 LIVE buys (KR or US) in an isolated process.

Reads {"entries": [...]} JSON on stdin (built by prism_core/reentry_v3_live.process), prints one
JSON line {"outcomes": {key: {...}}}. Called only by tools/run_reentry_v3_shadow.py when
REENTRY_V3_LIVE_ENABLED is on. Same isolation as tools/run_micro_split_add.py: the market's own
`cores` package wins on sys.path. Each entry goes through the tracker's normal entry pipeline
(enter_reentry_candidate -> _enter_eligible_candidate); the queued Telegram buy messages are sent
once at the end.
"""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _bootstrap_path(market):
    """Same isolation as tools/hardstop_seller.py: the market's own `cores` must win."""
    root, us = str(ROOT), str(ROOT / "prism-us")
    for path in ((root, str(ROOT / "prism-us" / "trading"), us) if market == "US" else (us, root)):
        sys.path.insert(0, path)


async def open_agent(market):
    """The market's tracking agent, lightweight-initialised (no LLM agent: the recheck already decided)."""
    db_path = os.getenv("STOCK_TRACKING_DB") or str(ROOT / "stock_tracking_db.sqlite")
    if market == "KR":
        from stock_tracking_agent import StockTrackingAgent
        from stock_tracking_enhanced_agent import EnhancedStockTrackingAgent
        agent = EnhancedStockTrackingAgent(db_path=db_path)
        # Base initialisation only (DB, ledger, accounts, Telegram sender): the enhanced extras build
        # the LLM sell agent, which the entry path never uses.
        await StockTrackingAgent.initialize(agent, language="ko", skip_llm_agent=True)
    else:
        from us_stock_tracking_agent import USStockTrackingAgent
        agent = USStockTrackingAgent(db_path=db_path)
        await agent.initialize(skip_llm_agent=True)
    try:
        from telegram_config import TelegramConfig
        languages = [x.strip() for x in os.getenv("LOOP_BROADCAST_LANGUAGES", "en,ja,zh,es").split(",") if x.strip()]
        agent.telegram_config = TelegramConfig(use_telegram=True, broadcast_languages=languages)
    except Exception as error:  # noqa: BLE001 - broadcast config is optional
        print(f"broadcast config unavailable: {error}", file=sys.stderr)
    return agent


async def run(market, entries, agent_factory=open_agent, chat_id=None):
    from prism_core.reentry_v3_live import enter_with_agent
    agent = await agent_factory(market)
    outcomes = {}
    try:
        for entry in entries:
            outcomes[entry["key"]] = await enter_with_agent(agent, market, entry)
        if any(o.get("bought") for o in outcomes.values()) and chat_id:
            try:
                await agent.send_telegram_message(chat_id, await_broadcast=True)
            except Exception as error:  # noqa: BLE001 - the order stands even if the message fails
                print(f"telegram send failed: {type(error).__name__}", file=sys.stderr)
    finally:
        if getattr(agent, "conn", None):
            agent.conn.close()
    return outcomes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", choices=["kr", "us"], required=True)
    args = parser.parse_args()
    market = args.market.upper()
    _bootstrap_path(market)
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from prism_core.reentry_v3_live import live_enabled
    payload = json.loads(sys.stdin.read() or "{}")
    entries = payload.get("entries") or []
    if not live_enabled(market):
        print(json.dumps({"outcomes": {e["key"]: {"bought": False, "reason": "live_off"} for e in entries}}))
        return 0
    outcomes = asyncio.run(run(market, entries, chat_id=os.getenv("TELEGRAM_CHANNEL_ID")))
    print(json.dumps({"outcomes": outcomes}, default=str, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
