#!/usr/bin/env python3
"""Execute one LIVE micro-split add (KR or US) in an isolated process.

Reads {"campaign", "decision", "now"} JSON on stdin, prints one JSON result line.
Called only by tools/run_b3_ae_shadow.py when MICRO_SPLIT_LIVE_ENABLED is on.
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


async def run(market, payload):
    if market == "KR":
        from stock_tracking_agent import StockTrackingAgent as Agent
    else:
        from us_stock_tracking_agent import USStockTrackingAgent as Agent
    from prism_core import micro_split_live

    agent = Agent(db_path=os.getenv("STOCK_TRACKING_DB") or str(ROOT / "stock_tracking_db.sqlite"))
    await agent.initialize(skip_llm_agent=True)
    try:
        from telegram_config import TelegramConfig
        languages = [x.strip() for x in os.getenv("LOOP_BROADCAST_LANGUAGES", "en,ja,zh,es").split(",") if x.strip()]
        agent.telegram_config = TelegramConfig(use_telegram=True, broadcast_languages=languages)
    except Exception as error:  # noqa: BLE001 - broadcast config is optional
        print(f"broadcast config unavailable: {error}", file=sys.stderr)
    try:
        return await micro_split_live.execute_add(agent, market=market, campaign=payload["campaign"],
                                                  decision=payload["decision"], now=payload["now"],
                                                  chat_id=os.getenv("TELEGRAM_CHANNEL_ID"))
    finally:
        if getattr(agent, "conn", None):
            agent.conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", choices=["kr", "us"], required=True)
    args = parser.parse_args()
    market = args.market.upper()
    _bootstrap_path(market)
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from prism_core import micro_split_live
    if not micro_split_live.live_enabled(market):
        print(json.dumps({"status": "LIVE_OFF"}))
        return 0
    if not micro_split_live.plan_adds_enabled(market):
        print(json.dumps({"status": "ADDS_PAUSED"}))
        return 0
    result = asyncio.run(run(market, json.loads(sys.stdin.read())))
    print(json.dumps(result, default=str, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
