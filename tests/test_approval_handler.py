"""Unit tests for approval/handler.py — ApprovalManager flows.

Telegram bot and CallbackQuery are stubbed so the tests don't require
python-telegram-bot at runtime for the handler logic itself. The keyboard
builder is the only path that touches the real telegram package, and we
only exercise that in test_message.py (or implicitly via request_approval
with a real Bot — out of scope here).
"""
from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass, field
from typing import Any, List, Optional

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from approval.handler import ApprovalManager, ApprovalManagerConfig
from approval.message import build_callback_data, ACTION_APPROVE, ACTION_REJECT, ACTION_MODIFY
from approval.models import (
    ApprovalDecision,
    ExecutionResult,
    TradeProposal,
    TradeSide,
)
from approval.store import ApprovalStore


# --------------------------------------------------------------------- stubs


@dataclass
class FakeMessage:
    message_id: int = 100


@dataclass
class FakeUser:
    id: int = 42


@dataclass
class FakeBot:
    sent: List[dict] = field(default_factory=list)
    edited: List[dict] = field(default_factory=list)
    next_message_id: int = 100

    async def send_message(self, chat_id, text, reply_markup=None, parse_mode=None, **kw):
        self.next_message_id += 1
        self.sent.append({
            "chat_id": chat_id, "text": text,
            "reply_markup": reply_markup, "parse_mode": parse_mode,
        })
        return FakeMessage(message_id=self.next_message_id)

    async def edit_message_text(self, chat_id, message_id, text, **kw):
        self.edited.append({"chat_id": chat_id, "message_id": message_id, "text": text})


@dataclass
class FakeCallbackQuery:
    data: str
    bot: FakeBot
    chat_id: int = 99
    message_id: int = 100
    from_user: FakeUser = field(default_factory=FakeUser)
    answered: List[Any] = field(default_factory=list)
    edits: List[str] = field(default_factory=list)

    async def answer(self, text: Optional[str] = None, show_alert: bool = False):
        self.answered.append({"text": text, "alert": show_alert})

    async def edit_message_text(self, text: str, **kw):
        self.edits.append(text)


# --------------------------------------------------------------------- fixtures


def _proposal(side=TradeSide.BUY, auto_execute=False) -> TradeProposal:
    return TradeProposal(
        ticker="005930", stock_name="삼성전자",
        side=side, entry_price=70_000.0, proposed_amount_krw=500_000,
        stop_loss=66_500.0, target_price=78_000.0,
        score=82, rationale=["a", "b", "c"],
        auto_execute=auto_execute,
    )


@pytest.fixture
def store(tmp_path):
    s = ApprovalStore(str(tmp_path / "approvals.db"))
    yield s
    s.close()


@pytest.fixture
def bot():
    return FakeBot()


class _FakeExecutor:
    """Captures executor invocations and returns a configurable result."""

    def __init__(self, result: ExecutionResult, *, raise_exc: Exception | None = None):
        self.result = result
        self.raise_exc = raise_exc
        self.calls: List[tuple] = []

    async def __call__(self, proposal: TradeProposal, amount: int) -> ExecutionResult:
        self.calls.append((proposal.approval_id, amount))
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.result


def _manager(store, executor, **config_kwargs):
    return ApprovalManager(store, executor, ApprovalManagerConfig(**config_kwargs))


def _patch_keyboard(monkeypatch):
    """Replace the InlineKeyboardMarkup builder so we don't need telegram package."""
    import approval.message as msg_mod
    monkeypatch.setattr(msg_mod, "build_approval_keyboard", lambda proposal: None)
    # The handler imports build_approval_keyboard from approval.message at
    # module load time, so we also patch the binding inside approval.handler.
    import approval.handler as h_mod
    monkeypatch.setattr(h_mod, "build_approval_keyboard", lambda proposal: None)


# --------------------------------------------------------------------- tests


@pytest.mark.asyncio
async def test_request_approval_sends_message_and_persists_pending(store, bot, monkeypatch):
    _patch_keyboard(monkeypatch)
    executor = _FakeExecutor(ExecutionResult(success=True, order_no="X1", quantity=7, fill_price=70_100))
    mgr = _manager(store, executor, timeout_seconds=3600)

    proposal = _proposal()
    record = await mgr.request_approval(bot, chat_id=99, proposal=proposal)

    assert record.decision == ApprovalDecision.PENDING.value
    assert len(bot.sent) == 1
    sent = bot.sent[0]
    assert sent["chat_id"] == 99
    assert "매수 승인 요청" in sent["text"]
    assert "30분 후 자동 거절" in sent["text"]
    pending = store.list_pending()
    assert len(pending) == 1


@pytest.mark.asyncio
async def test_approve_flow_persists_decision_and_invokes_executor(store, bot, monkeypatch):
    _patch_keyboard(monkeypatch)
    executor = _FakeExecutor(ExecutionResult(success=True, order_no="X1", quantity=7, fill_price=70_100))
    mgr = _manager(store, executor, timeout_seconds=3600)

    proposal = _proposal()
    await mgr.request_approval(bot, chat_id=99, proposal=proposal)

    query = FakeCallbackQuery(
        data=build_callback_data(ACTION_APPROVE, proposal.short_id()),
        bot=bot,
    )
    decision = await mgr.handle_callback(query)

    assert decision == ApprovalDecision.APPROVED
    assert len(executor.calls) == 1
    assert executor.calls[0] == (proposal.approval_id, proposal.proposed_amount_krw)

    record = store.get(proposal.approval_id)
    assert record.decision == ApprovalDecision.APPROVED.value
    assert record.order_no == "X1"
    assert record.decided_by == "42"
    assert "주문 체결" in bot.sent[-1]["text"]


@pytest.mark.asyncio
async def test_reject_flow_does_not_invoke_executor(store, bot, monkeypatch):
    _patch_keyboard(monkeypatch)
    executor = _FakeExecutor(ExecutionResult(success=True))
    mgr = _manager(store, executor, timeout_seconds=3600)

    proposal = _proposal()
    await mgr.request_approval(bot, chat_id=99, proposal=proposal)

    query = FakeCallbackQuery(
        data=build_callback_data(ACTION_REJECT, proposal.short_id()), bot=bot,
    )
    decision = await mgr.handle_callback(query)

    assert decision == ApprovalDecision.REJECTED
    assert executor.calls == []
    record = store.get(proposal.approval_id)
    assert record.decision == ApprovalDecision.REJECTED.value
    assert record.order_no is None
    assert "거절" in query.edits[0]


@pytest.mark.asyncio
async def test_modify_flow_marks_modify_requested(store, bot, monkeypatch):
    _patch_keyboard(monkeypatch)
    executor = _FakeExecutor(ExecutionResult(success=True))
    mgr = _manager(store, executor, timeout_seconds=3600)

    proposal = _proposal()
    await mgr.request_approval(bot, chat_id=99, proposal=proposal)

    query = FakeCallbackQuery(
        data=build_callback_data(ACTION_MODIFY, proposal.short_id()), bot=bot,
    )
    decision = await mgr.handle_callback(query)

    assert decision == ApprovalDecision.MODIFY_REQUESTED
    assert executor.calls == []
    record = store.get(proposal.approval_id)
    assert record.decision == ApprovalDecision.MODIFY_REQUESTED.value
    assert "/retry_" in query.edits[0]


@pytest.mark.asyncio
async def test_unknown_callback_returns_pending(store, bot, monkeypatch):
    _patch_keyboard(monkeypatch)
    executor = _FakeExecutor(ExecutionResult(success=True))
    mgr = _manager(store, executor, timeout_seconds=3600)

    query = FakeCallbackQuery(data="garbage:payload", bot=bot)
    decision = await mgr.handle_callback(query)
    assert decision == ApprovalDecision.PENDING
    assert query.answered[0]["alert"] is True


@pytest.mark.asyncio
async def test_callback_for_unknown_id_returns_expired(store, bot, monkeypatch):
    _patch_keyboard(monkeypatch)
    executor = _FakeExecutor(ExecutionResult(success=True))
    mgr = _manager(store, executor, timeout_seconds=3600)

    query = FakeCallbackQuery(data=build_callback_data(ACTION_APPROVE, "deadbeef0000"), bot=bot)
    decision = await mgr.handle_callback(query)
    assert decision == ApprovalDecision.EXPIRED


@pytest.mark.asyncio
async def test_timer_expires_pending_proposal(store, bot, monkeypatch):
    _patch_keyboard(monkeypatch)
    executor = _FakeExecutor(ExecutionResult(success=True))
    # Very small timeout to keep the test fast.
    mgr = _manager(store, executor, timeout_seconds=0)

    proposal = _proposal()
    await mgr.request_approval(bot, chat_id=99, proposal=proposal)

    # Let the timer fire.
    await asyncio.sleep(0.1)

    record = store.get(proposal.approval_id)
    assert record.decision == ApprovalDecision.EXPIRED.value
    assert any("자동 거절" in e["text"] for e in bot.edited)
    assert executor.calls == []


@pytest.mark.asyncio
async def test_auto_execute_stop_loss_bypasses_telegram(store, bot, monkeypatch):
    _patch_keyboard(monkeypatch)
    executor = _FakeExecutor(ExecutionResult(success=True, order_no="STOP1", quantity=10, fill_price=65_000))
    mgr = _manager(store, executor, auto_stop_loss=True)

    proposal = _proposal(side=TradeSide.SELL, auto_execute=True)
    record = await mgr.request_approval(bot, chat_id=99, proposal=proposal)

    # Telegram message should NOT have been sent.
    assert bot.sent == []
    # Executor was invoked synchronously.
    assert executor.calls == [(proposal.approval_id, proposal.proposed_amount_krw)]
    assert record.decision == ApprovalDecision.PENDING.value  # insert_proposal sets PENDING; update follows
    # Final state must be AUTO_EXECUTED in the DB.
    final = store.get(proposal.approval_id)
    assert final.decision == ApprovalDecision.AUTO_EXECUTED.value
    assert final.order_no == "STOP1"


@pytest.mark.asyncio
async def test_auto_stop_loss_disabled_still_requires_approval(store, bot, monkeypatch):
    _patch_keyboard(monkeypatch)
    executor = _FakeExecutor(ExecutionResult(success=True))
    mgr = _manager(store, executor, auto_stop_loss=False, timeout_seconds=3600)

    proposal = _proposal(side=TradeSide.SELL, auto_execute=True)
    await mgr.request_approval(bot, chat_id=99, proposal=proposal)

    assert len(bot.sent) == 1
    assert executor.calls == []


@pytest.mark.asyncio
async def test_executor_exception_records_failed_execution(store, bot, monkeypatch):
    _patch_keyboard(monkeypatch)
    executor = _FakeExecutor(
        ExecutionResult(success=True), raise_exc=RuntimeError("KIS down"),
    )
    mgr = _manager(store, executor, timeout_seconds=3600)

    proposal = _proposal()
    await mgr.request_approval(bot, chat_id=99, proposal=proposal)
    query = FakeCallbackQuery(data=build_callback_data(ACTION_APPROVE, proposal.short_id()), bot=bot)
    await mgr.handle_callback(query)

    record = store.get(proposal.approval_id)
    # APPROVED is persisted even though the executor failed, with the
    # exception captured in execution_result_json.
    assert record.decision == ApprovalDecision.APPROVED.value
    assert "executor exception" in record.execution_result_json
    assert "주문 실패" in bot.sent[-1]["text"]


def test_parse_callback_data_round_trip():
    from approval.message import parse_callback_data, build_callback_data
    raw = build_callback_data(ACTION_APPROVE, "abcdef012345")
    parsed = parse_callback_data(raw)
    assert parsed is not None
    assert parsed.action == ACTION_APPROVE
    assert parsed.short_id == "abcdef012345"

    assert parse_callback_data("") is None
    assert parse_callback_data("other:ok:x") is None
    assert parse_callback_data("apv:xx:abc") is None  # unknown action
    assert parse_callback_data("apv:ok:") is None     # empty id


# --------------------------------------------------------------- retry flow


@pytest.mark.asyncio
async def test_retry_with_amount_creates_new_proposal(store, bot, monkeypatch):
    _patch_keyboard(monkeypatch)
    executor = _FakeExecutor(ExecutionResult(success=True))
    mgr = _manager(store, executor, timeout_seconds=3600)

    original = _proposal()
    original.metadata = {"account_name": "primary", "scenario": {"buy_score": 9}}
    await mgr.request_approval(bot, chat_id=99, proposal=original)

    # Click 📝 modify
    query = FakeCallbackQuery(
        data=build_callback_data(ACTION_MODIFY, original.short_id()), bot=bot,
    )
    await mgr.handle_callback(query)

    # /retry_<id> 300000 — new approval card should be sent
    sent_before = len(bot.sent)
    new_proposal = await mgr.retry_with_amount(
        bot, chat_id=99, short_id=original.short_id(), new_amount_krw=300_000,
    )
    assert new_proposal is not None
    assert new_proposal.proposed_amount_krw == 300_000
    assert new_proposal.approval_id != original.approval_id
    # Metadata is preserved so the executor can still route to the right account.
    assert new_proposal.metadata["account_name"] == "primary"
    assert new_proposal.metadata["scenario"]["buy_score"] == 9
    # A fresh approval message must have been sent.
    assert len(bot.sent) == sent_before + 1
    # Original record stays MODIFY_REQUESTED for audit; new record is PENDING.
    assert store.get(original.approval_id).decision == ApprovalDecision.MODIFY_REQUESTED.value
    assert store.get(new_proposal.approval_id).decision == ApprovalDecision.PENDING.value


@pytest.mark.asyncio
async def test_retry_with_unknown_short_id_returns_none(store, bot, monkeypatch):
    _patch_keyboard(monkeypatch)
    executor = _FakeExecutor(ExecutionResult(success=True))
    mgr = _manager(store, executor, timeout_seconds=3600)
    # No prior MODIFY → retry must report "not found".
    result = await mgr.retry_with_amount(
        bot, chat_id=99, short_id="deadbeefcafe", new_amount_krw=500_000,
    )
    assert result is None
    assert bot.sent == []


@pytest.mark.asyncio
async def test_retry_consumes_modify_stash_so_second_retry_fails(store, bot, monkeypatch):
    _patch_keyboard(monkeypatch)
    executor = _FakeExecutor(ExecutionResult(success=True))
    mgr = _manager(store, executor, timeout_seconds=3600)

    original = _proposal()
    await mgr.request_approval(bot, chat_id=99, proposal=original)
    await mgr.handle_callback(FakeCallbackQuery(
        data=build_callback_data(ACTION_MODIFY, original.short_id()), bot=bot,
    ))

    first = await mgr.retry_with_amount(
        bot, chat_id=99, short_id=original.short_id(), new_amount_krw=200_000,
    )
    assert first is not None
    second = await mgr.retry_with_amount(
        bot, chat_id=99, short_id=original.short_id(), new_amount_krw=300_000,
    )
    assert second is None  # stash was consumed on first retry


@pytest.mark.asyncio
async def test_retry_rejects_non_positive_amount(store, bot, monkeypatch):
    _patch_keyboard(monkeypatch)
    executor = _FakeExecutor(ExecutionResult(success=True))
    mgr = _manager(store, executor, timeout_seconds=3600)
    original = _proposal()
    await mgr.request_approval(bot, chat_id=99, proposal=original)
    await mgr.handle_callback(FakeCallbackQuery(
        data=build_callback_data(ACTION_MODIFY, original.short_id()), bot=bot,
    ))
    assert await mgr.retry_with_amount(
        bot, chat_id=99, short_id=original.short_id(), new_amount_krw=0,
    ) is None
    assert await mgr.retry_with_amount(
        bot, chat_id=99, short_id=original.short_id(), new_amount_krw=-1,
    ) is None
    # Stash should still be present (a rejected retry doesn't consume it).
    valid = await mgr.retry_with_amount(
        bot, chat_id=99, short_id=original.short_id(), new_amount_krw=100_000,
    )
    assert valid is not None
