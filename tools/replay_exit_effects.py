#!/usr/bin/env python3
"""Audit or explicitly replay a bounded set of durable exit effects."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import sqlite3
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402

from prism_core.exit_effect_replay import (  # noqa: E402
    EffectHandler,
    run_exit_effect_replay,
)
from prism_core.exit_effects import EXIT_EFFECT_TYPES  # noqa: E402
from tools.audit_exit_effects import audit_database  # noqa: E402
from tracking.db_schema import migrate_trading_journal_exit_intent  # noqa: E402
from tracking.journal import JournalManager  # noqa: E402


load_dotenv(PROJECT_ROOT / ".env")


async def _send_telegram_message(bot, chat_id: str, message: str, event_id: str):
    event_ref = f"[exit-event: {event_id}]"
    text = f"{message}\n\n{event_ref}" if message else event_ref
    chunks = [text[index : index + 4000] for index in range(0, len(text), 4000)]
    first_message_id = None
    for chunk in chunks:
        result = await bot.send_message(chat_id=chat_id, text=chunk)
        if first_message_id is None:
            first_message_id = result.message_id
    return str(first_message_id) if first_message_id is not None else None


def build_exit_effect_handlers(
    db_path: str | Path,
    effect_types: Sequence[str],
    *,
    environment: Mapping[str, str] | None = None,
) -> dict[str, EffectHandler]:
    """Build production handlers from existing configuration only."""

    selected = tuple(dict.fromkeys(effect_types))
    if not selected or any(value not in EXIT_EFFECT_TYPES for value in selected):
        raise ValueError("unsupported or empty exit effect selection")
    env = dict(os.environ if environment is None else environment)
    resolved_db_path = Path(db_path).expanduser().resolve()
    handlers: dict[str, EffectHandler] = {}

    if "JOURNAL" in selected:
        journal_enabled = env.get("ENABLE_TRADING_JOURNAL", "false").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

        async def journal(payload: dict[str, Any]):
            if not journal_enabled:
                return "disabled-by-config"
            connection = sqlite3.connect(resolved_db_path, timeout=30)
            try:
                cursor = connection.cursor()
                migrate_trading_journal_exit_intent(cursor, connection)
                manager = JournalManager(
                    cursor,
                    connection,
                    enable_journal=True,
                )
                return await manager.create_entry(
                    stock_data=dict(payload.get("journal_stock_data") or {}),
                    sell_price=float(payload.get("sell_price") or 0),
                    profit_rate=float(payload.get("profit_rate") or 0),
                    holding_days=int(payload.get("holding_days") or 0),
                    sell_reason=str(payload.get("sell_reason") or ""),
                    exit_intent_id=str(payload.get("event_id") or ""),
                )
            finally:
                connection.close()

        handlers["JOURNAL"] = journal

    if "TELEGRAM" in selected:
        token = str(env.get("TELEGRAM_BOT_TOKEN") or "").strip()
        chat_id = str(env.get("TELEGRAM_CHANNEL_ID") or "").strip()
        bot = None
        if token and chat_id:
            from telegram import Bot

            bot = Bot(token=token)

        async def telegram(payload: dict[str, Any]):
            if bot is None:
                return None
            return await _send_telegram_message(
                bot,
                chat_id,
                str(payload.get("message") or ""),
                str(payload.get("event_id") or ""),
            )

        handlers["TELEGRAM"] = telegram

    def publish_kwargs(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "ticker": str(payload.get("symbol") or ""),
            "company_name": str(payload.get("company_name") or ""),
            "price": float(payload.get("sell_price") or 0),
            "buy_price": float(payload.get("buy_price") or 0),
            "profit_rate": float(payload.get("profit_rate") or 0),
            "sell_reason": str(payload.get("sell_reason") or ""),
            "market": str(payload.get("market") or "KR"),
            "event_id": str(payload.get("event_id") or ""),
        }

    if "REDIS" in selected:

        async def redis(payload: dict[str, Any]):
            from messaging.redis_signal_publisher import publish_sell_signal

            return await publish_sell_signal(**publish_kwargs(payload))

        handlers["REDIS"] = redis

    if "GCP" in selected:

        async def gcp(payload: dict[str, Any]):
            from messaging.gcp_pubsub_signal_publisher import publish_sell_signal

            return await publish_sell_signal(**publish_kwargs(payload))

        handlers["GCP"] = gcp

    return handlers


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument(
        "--effect",
        action="append",
        choices=EXIT_EFFECT_TYPES,
        dest="effects",
        default=[],
        help="Effect type to execute; repeat for multiple types.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Perform bounded external delivery. Default is read-only audit.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.limit < 1:
        parser.error("--limit must be a positive integer")
    if not args.execute:
        report = audit_database(args.db_path, limit=args.limit)
        print(
            json.dumps(
                {"mode": "dry-run", "audit": report},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return {"ready": 0, "blocked": 1}.get(report["status"], 2)
    if not args.effects:
        parser.error("--execute requires at least one explicit --effect")

    handlers = build_exit_effect_handlers(args.db_path, args.effects)
    owner = f"replay:{socket.gethostname()}:{os.getpid()}"
    summary = asyncio.run(
        run_exit_effect_replay(
            args.db_path,
            handlers=handlers,
            owner=owner,
            limit=args.limit,
        )
    )
    report = audit_database(args.db_path, limit=args.limit)
    print(
        json.dumps(
            {"mode": "execute", "summary": summary, "audit": report},
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
