#!/usr/bin/env python3
"""Back-office: send an ad-hoc message to the Telegram channel(s).

Purpose
-------
Operator tool for manual/retroactive notices — e.g. re-sending a sell alert that
the automated pipeline dropped (2026-07-14: US loop sells lost their Telegram
message to an `await_broadcast` signature bug; AVGO/NVDA needed a delayed notice).

Safety
------
- **Dry-run by default.** Prints what WOULD be sent. Add `--send` to actually post.
- Sends to the MAIN Korean channel (`TELEGRAM_CHANNEL_ID`) by default. Broadcast
  language channels are opt-in via `--lang en,ja,...` (they must exist as
  `TELEGRAM_CHANNEL_ID_{LANG}`).
- Reads token/channel from env (same as the pipeline): `TELEGRAM_BOT_TOKEN`,
  `TELEGRAM_CHANNEL_ID`, `TELEGRAM_CHANNEL_ID_{LANG}`.

Usage
-----
    # preview (no send)
    python tools/admin_send_message.py --text-file notice.txt
    python tools/admin_send_message.py --text "⏰ [지연 안내] ..."

    # actually send to the main channel
    python tools/admin_send_message.py --text-file notice.txt --send

    # also post to specific broadcast language channels
    python tools/admin_send_message.py --text-file notice.txt --lang en,ja --send
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys


def _load_env_file() -> None:
    """Best-effort .env load so the tool works the same as the batch (which uses
    python-dotenv). No-op if python-dotenv is absent or no .env present."""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass


def _resolve_channels(langs: list[str]) -> list[tuple[str, str]]:
    """Return [(label, channel_id)]. Main first, then requested language channels."""
    out: list[tuple[str, str]] = []
    main = os.getenv("TELEGRAM_CHANNEL_ID")
    if not main:
        print("ERROR: TELEGRAM_CHANNEL_ID not set in env", file=sys.stderr)
        sys.exit(2)
    out.append(("main(ko)", main))
    for lang in langs:
        lang = lang.strip().lower()
        if not lang:
            continue
        cid = os.getenv(f"TELEGRAM_CHANNEL_ID_{lang.upper()}")
        if cid:
            out.append((lang, cid))
        else:
            print(f"WARN: TELEGRAM_CHANNEL_ID_{lang.upper()} not set — skipping {lang}")
    return out


async def _send(text: str, channels: list[tuple[str, str]]) -> int:
    from telegram import Bot
    from telegram.request import HTTPXRequest

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        print("ERROR: TELEGRAM_BOT_TOKEN not set in env", file=sys.stderr)
        return 2
    bot = Bot(token=token, request=HTTPXRequest(connect_timeout=10.0, read_timeout=15.0))
    rc = 0
    for label, cid in channels:
        try:
            msg = await bot.send_message(chat_id=cid, text=text)
            print(f"SENT   {label:<10} chat={cid} message_id={msg.message_id}")
        except Exception as e:
            print(f"FAILED {label:<10} chat={cid} error={e}", file=sys.stderr)
            rc = 1
    return rc


def main() -> None:
    _load_env_file()
    ap = argparse.ArgumentParser(description="Send an ad-hoc message to the Telegram channel(s).")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--text", help="Message text (use \\n for newlines).")
    src.add_argument("--text-file", help="Read message text from this file (UTF-8).")
    ap.add_argument("--lang", default="", help="Comma list of broadcast langs to ALSO post to (e.g. en,ja). Main is always included.")
    ap.add_argument("--send", action="store_true", help="Actually send. Without this, dry-run (preview only).")
    args = ap.parse_args()

    if args.text_file:
        with open(args.text_file, encoding="utf-8") as f:
            text = f.read().strip()
    else:
        text = args.text.replace("\\n", "\n")
    if not text:
        print("ERROR: empty message", file=sys.stderr)
        sys.exit(2)

    langs = [x for x in args.lang.split(",") if x.strip()]
    channels = _resolve_channels(langs)

    print("=" * 60)
    print("TARGET CHANNELS:", ", ".join(f"{l}" for l, _ in channels))
    print("-" * 60)
    print(text)
    print("=" * 60)

    if not args.send:
        print("DRY-RUN — nothing sent. Re-run with --send to post.")
        return

    rc = asyncio.run(_send(text, channels))
    sys.exit(rc)


if __name__ == "__main__":
    main()
