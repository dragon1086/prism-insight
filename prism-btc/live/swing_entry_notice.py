"""Prospective recovered-entry outbox; ambiguous delivery is never retried.

Recovery enqueues in its receipt transaction. Only the broker recovery safety
gate drains it. No broker I/O, historical receipt discovery, or private fallback.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

from live import tracking

MODE = "swing"
PREFIX = "swing_entry_notice:"
log = logging.getLogger(__name__)


def enqueue(conn, pos, receipt_key: str, *, commit: bool = False) -> None:
    """Enqueue an explicitly selected, exact open receipt, never replace a claim."""
    receipt = tracking.get_meta(conn, receipt_key, MODE)
    if (not receipt_key.startswith("swing_entry_receipt:") or not receipt
            or receipt.get("closed") or not receipt.get("order_id")
            or receipt.get("position_id") != pos.id or receipt.get("qty") != pos.qty
            or not any(p.id == pos.id and p.entry_time == pos.entry_time
                       and p.entry_price == pos.entry_price and p.qty == pos.qty
                       and p.side == pos.side for p in tracking.load_open_positions(conn, MODE))):
        raise ValueError("notice requires an exact open entry receipt")
    record = {"status": "pending", "position_id": pos.id, "receipt_key": receipt_key,
              "receipt": receipt, "side": pos.side, "entry_time": pos.entry_time,
              "entry_price": pos.entry_price, "qty": pos.qty, "initial_sl": pos.sl_price}
    conn.execute("INSERT OR IGNORE INTO btc_meta(mode,key,value) VALUES(?,?,?)",
                 (MODE, PREFIX + str(pos.id), json.dumps(record)))
    if commit:
        conn.commit()


def _claim(conn, key, raw, record, status="attempted") -> bool:
    with conn:
        return conn.execute("UPDATE btc_meta SET value=? WHERE mode=? AND key=? AND value=?",
                            (json.dumps({**record, "status": status}), MODE, key, raw)).rowcount == 1


def claim_normal(conn, position_id) -> bool:
    """The ordinary path consumes the same key, including legacy unqueued entries."""
    key = PREFIX + str(position_id)
    with conn:
        inserted = conn.execute("INSERT OR IGNORE INTO btc_meta(mode,key,value) VALUES(?,?,?)",
                                (MODE, key, json.dumps({"status": "attempted", "source": "normal"})))
        if inserted.rowcount == 1:
            return True
        row = conn.execute("SELECT value FROM btc_meta WHERE mode=? AND key=?", (MODE, key)).fetchone()
        record = json.loads(row["value"])
        if record.get("status") != "pending":
            return False
        return _claim(conn, key, row["value"], {**record, "source": "normal"})


async def _send_public(main_mode, message):
    from live.telegram_reporter import _load_env, _resolve_channel

    _load_env()
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    channel = _resolve_channel(None, mode=main_mode)
    if not token or not channel:
        return {"status": "unconfigured"}
    from telegram import Bot
    # A direct single request, intentionally not TelegramSender/_send (fallbacks).
    transports = [logging.getLogger(name) for name in ("httpx", "httpcore")]
    levels = [logger.level for logger in transports]
    try:
        for logger in transports:
            logger.setLevel(max(logger.getEffectiveLevel(), logging.WARNING))
        async with Bot(token=token) as bot:
            sent = await bot.send_message(chat_id=channel, text=message)
    finally:
        for logger, level in zip(transports, levels):
            logger.setLevel(level)
    if (type(sent.message_id) is not int or sent.message_id <= 0
            or type(sent.chat.id) is not int or sent.chat.id == 0):
        return {"status": "unknown"}
    return {"status": "delivered", "chat_id": sent.chat.id, "message_id": sent.message_id}


def build_recovered_message(record, pos, main_mode, snapshot, logical_capital=None):
    """Pure renderer usable for an explicitly authorized in-place correction."""
    from live.position_snapshot import number, snapshot_lines
    from live.swing import _entry_time_kst, _side_kr

    label = "데모" if main_mode == "demo" else "실거래"
    lines = [f"📌 BTC [{label} · 스윙레인] 진입 체결 지연 안내",
             "이미 체결된 진입의 누락된 알림을 뒤늦게 전해 드립니다. 새 진입이 아닙니다.",
             f"방향: {_side_kr(record['side'])}",
             f"실제 진입 시각: {_entry_time_kst(record['entry_time'])}",
             f"체결 가격: {record['entry_price']:,.2f} USDT",
             f"체결 수량: {record['qty']:.8f} BTC",
             f"최초 손절 가격: {record['initial_sl']:,.2f} USDT",
             "현재 보호 상태를 확인한 뒤 안내합니다."]
    capital = number(logical_capital)
    lines.append(f"• 전략 배정자본: {capital:,.2f} USD (거래소 전체 잔고 아님)"
                 if capital is not None else "• 전략 배정자본: 확인 불가")
    lines.extend(snapshot_lines(snapshot, pos))
    lines.extend(["• 진입 신호 상세: 복구 공지에 보존된 근거 없음 (현재 지표로 재구성하지 않음)",
                  "• 고정 익절가는 없음 · 4시간봉 종가가 MA35 "
                  + ("아래" if pos.side == "long" else "위") + "로 이탈하면 추세청산",
                  "데모 계정의 가상자금 모의투자입니다." if main_mode == "demo"
                  else "실거래 계정의 체결 안내입니다."])
    return "\n".join(lines)


def drain(conn, main_mode: str, backend=None) -> None:
    """Called only after recovery, protection and retirement checks pass."""
    if main_mode not in ("demo", "live"):
        return
    try:
        rows = conn.execute("SELECT key,value FROM btc_meta WHERE mode=? AND key LIKE ?",
                            (MODE, PREFIX + "%")).fetchall()
        for row in rows:
            record = json.loads(row["value"])
            if record.get("status") != "pending":
                continue
            positions = tracking.load_open_positions(conn, MODE)
            matches = [p for p in positions if p.id == record["position_id"]
                       and all(getattr(p, field) == record[field]
                               for field in ("side", "entry_time", "entry_price", "qty"))]
            receipt = tracking.get_meta(conn, record["receipt_key"], MODE)
            if len(matches) != 1 or receipt != record["receipt"]:
                _claim(conn, row["key"], row["value"], record, "superseded")
                continue
            from live.position_snapshot import capture_swing_snapshot
            snapshot = capture_swing_snapshot(backend, matches[0])
            record = {**record, "account_snapshot": snapshot}
            message = build_recovered_message(record, matches[0], main_mode, snapshot,
                tracking.get_meta(conn, "swing_entry_logical_capital", MODE))
            if not _claim(conn, row["key"], row["value"], record):
                continue
            try:
                result = asyncio.run(_send_public(main_mode, message))
            except Exception:  # noqa: BLE001 - uncertain sends must never be retried
                # Includes timeout after acceptance. Never infer failure/retry.
                result = {"status": "unknown"}
                log.warning("swing recovered entry delivery unknown; automatic retry disabled")
            tracking.set_meta(conn, row["key"], {**record, **result}, MODE)
    except Exception:
        log.exception("swing recovered entry notice failed; trading continues")
