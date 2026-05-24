"""Telegram message and InlineKeyboard builders for the approval layer."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Tuple

from approval.models import TradeProposal, TradeSide

# Telegram callback_data has a 64-byte limit. We keep it small with a 12-char
# id slice plus a 3-letter action code. Schema: "apv:<action>:<short_id>"
CALLBACK_PREFIX = "apv"
ACTION_APPROVE = "ok"
ACTION_REJECT = "no"
ACTION_MODIFY = "mod"


@dataclass
class CallbackData:
    action: str  # ok | no | mod
    short_id: str


def parse_callback_data(data: str) -> Optional[CallbackData]:
    """Parse Telegram callback_data. Returns None on a non-approval payload."""
    if not data or not data.startswith(f"{CALLBACK_PREFIX}:"):
        return None
    parts = data.split(":", 2)
    if len(parts) != 3:
        return None
    _, action, short_id = parts
    if action not in (ACTION_APPROVE, ACTION_REJECT, ACTION_MODIFY):
        return None
    if not short_id:
        return None
    return CallbackData(action=action, short_id=short_id)


def build_callback_data(action: str, short_id: str) -> str:
    payload = f"{CALLBACK_PREFIX}:{action}:{short_id}"
    assert len(payload.encode("utf-8")) <= 64, "callback_data exceeds Telegram 64-byte limit"
    return payload


def build_approval_message(proposal: TradeProposal, *, expires_at: datetime) -> str:
    """Render the Korean approval prompt — mirrors the project's 합쇼체 tone."""
    side_ko = "매수" if proposal.side == TradeSide.BUY else "매도"
    lines = [
        f"🟡 *{side_ko} 승인 요청*",
        f"",
        f"*{proposal.stock_name}* ({proposal.ticker})",
        f"진입가: {int(proposal.entry_price):,} 원",
    ]
    if proposal.stop_loss:
        lines.append(f"손절가: {int(proposal.stop_loss):,} 원")
    if proposal.target_price:
        lines.append(f"목표가: {int(proposal.target_price):,} 원")
    lines.append(f"투자금액: {proposal.proposed_amount_krw:,} 원")
    if proposal.score is not None:
        lines.append(f"신뢰도: {proposal.score}점")
    if proposal.rationale:
        lines.append("")
        lines.append("*AI 근거:*")
        for item in proposal.rationale[:3]:
            lines.append(f"• {item}")
    lines.append("")
    lines.append(f"만료: {expires_at.strftime('%H:%M:%S')} (30분 후 자동 거절)")
    return "\n".join(lines)


def build_approval_keyboard(proposal: TradeProposal):
    """Build the 3-button approval InlineKeyboard.

    Imported lazily so the package doesn't require python-telegram-bot at
    import time (useful for tests that only exercise store/models).
    """
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    sid = proposal.short_id()
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ 매수 승인" if proposal.side == TradeSide.BUY else "✅ 매도 승인",
                             callback_data=build_callback_data(ACTION_APPROVE, sid)),
        InlineKeyboardButton("❌ 거절",
                             callback_data=build_callback_data(ACTION_REJECT, sid)),
        InlineKeyboardButton("📝 금액 수정",
                             callback_data=build_callback_data(ACTION_MODIFY, sid)),
    ]])


def expires_at(proposed_at: datetime, timeout_seconds: int) -> datetime:
    return proposed_at + timedelta(seconds=timeout_seconds)
