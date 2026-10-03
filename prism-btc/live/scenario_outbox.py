"""Durable, one-attempt Telegram event delivery outside the trading lock.

UNKNOWN is not retried automatically. This trades possible omission for avoiding
duplicate trade notices after an ACK is lost. No public/private fallback routing.
"""
from __future__ import annotations

import asyncio
import os

from live.scenario_notice import render_notice
from live.shared_entry_coordinator import mutation_lock

PRIVATE = {"PLAN", "SUBMITTED", "PENDING", "HALTED", "RESOLVED", "MODEL_ERROR", "MODEL_RECOVERED"}


def _schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS llm_scenario_outbox (
        event_id TEXT PRIMARY KEY, kind TEXT NOT NULL, body TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'QUEUED', message_id INTEGER)""")


def enqueue(conn, event_id, event):
    """Caller owns transaction/lock; no commit or network here."""
    if not isinstance(event_id,str) or not event_id or len(event_id)>200:
        raise ValueError("notice_identity_required")
    body=render_notice(event)
    _schema(conn)
    existing=conn.execute("SELECT kind,body FROM llm_scenario_outbox WHERE event_id=?",(event_id,)).fetchone()
    if existing and tuple(existing)!=(event["kind"],body):
        raise ValueError("conflicting_notice_identity")
    conn.execute("INSERT OR IGNORE INTO llm_scenario_outbox(event_id,kind,body) VALUES(?,?,?)",
                 (event_id,event["kind"],body))


def _destination(kind):
    if kind in PRIVATE:
        from live.ops_alerts import _resolve_ops_destination
        return _resolve_ops_destination()
    return os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHANNEL_ID")


async def _deliver(token,channel,body):
    from telegram import Bot
    async with Bot(token=token) as bot:
        receipt=await bot.send_message(chat_id=channel,text=body)
    return getattr(receipt,"message_id",None)


def flush(conn, *, sender=None, limit=10):
    """Claim under lock, then send once without it. sender(body, kind)->message_id."""
    if type(limit) is not int or not 1<=limit<=50:
        raise ValueError("invalid_notice_limit")
    sent=unknown=0
    with mutation_lock(conn):
        _schema(conn)
        conn.commit()
        rows=conn.execute("SELECT event_id,kind,body FROM llm_scenario_outbox WHERE status='QUEUED' ORDER BY rowid LIMIT ?",(limit,)).fetchall()
    for event_id,kind,body in rows:
        token=channel=None
        if sender is None:
            token,channel=_destination(kind)
            if not token or not channel:
                continue  # No attempt made; operator may restore configuration.
        with mutation_lock(conn):
            changed=conn.execute("UPDATE llm_scenario_outbox SET status='SENDING' WHERE event_id=? AND status='QUEUED'",(event_id,)).rowcount
            conn.commit()
        if not changed:
            continue
        try:
            receipt=sender(body,kind) if sender else asyncio.run(_deliver(token,channel,body))
        except Exception:
            receipt=None
        confirmed=type(receipt) is int and receipt>0
        # The claim is already durable. Receipt-only bookkeeping must not lose
        # a known ACK merely because trading acquired its separate flock while
        # the network call was in flight. SQLite serializes this conditional
        # update; no order/risk state is read or changed here.
        try:
            changed=conn.execute("UPDATE llm_scenario_outbox SET status=?,message_id=? WHERE event_id=? AND status='SENDING'",
                                 ('SENT' if confirmed else 'UNKNOWN',receipt if confirmed else None,event_id)).rowcount
            if changed != 1:
                raise RuntimeError("notice_receipt_claim_lost")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        sent+=int(confirmed)
        unknown+=int(not confirmed)
    return {"sent":sent,"unknown":unknown}
