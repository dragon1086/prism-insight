"""Orchestrator for proposal → approval → execution lifecycle.

Wiring expected from the Telegram bot:

    manager = ApprovalManager(store, executor=async_execute_buy)
    # On a new AI proposal:
    await manager.request_approval(bot, chat_id, proposal)
    # In CallbackQueryHandler(pattern=r"^apv:") handler:
    await manager.handle_callback(update.callback_query)

The executor is an async callable invoked with (proposal, final_amount_krw)
that returns an `ExecutionResult`. This keeps the approval layer decoupled
from `trading/domestic_stock_trading.py` — tests can supply a stub.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Awaitable, Callable, Dict, Optional

from approval.message import (
    ACTION_APPROVE,
    ACTION_MODIFY,
    ACTION_REJECT,
    build_approval_keyboard,
    build_approval_message,
    expires_at,
    parse_callback_data,
)
from approval.models import (
    ApprovalDecision,
    ApprovalRecord,
    ExecutionResult,
    TradeProposal,
)
from approval.store import ApprovalStore

logger = logging.getLogger(__name__)

Executor = Callable[[TradeProposal, int], Awaitable[ExecutionResult]]


@dataclass
class ApprovalManagerConfig:
    timeout_seconds: int = 30 * 60          # 30 min as per plan §2 Phase 2
    auto_stop_loss: bool = False            # emergency bypass for SELL @ stop
    parse_mode: str = "Markdown"
    pending_message_template: str = "⏳ 처리 중..."
    approved_message_template: str = "✅ 승인 완료 — 주문 전송 중"
    rejected_message_template: str = "❌ 거절되었습니다."
    expired_message_template: str = "⏰ 30분 경과 — 자동 거절되었습니다."
    modify_message_template: str = (
        "📝 금액 수정 요청을 받았습니다.\n"
        "새 금액(원)으로 다음 명령을 입력해주세요:\n"
        "/retry_{short_id} <새 금액>\n"
        "예: /retry_{short_id} 300000"
    )
    executed_message_template: str = "🎯 주문 체결: {order_no} ({quantity}주 @ {fill_price:,}원)"
    execution_failed_template: str = "⚠️ 주문 실패: {message}"


@dataclass
class _PendingEntry:
    proposal: TradeProposal
    bot: object  # telegram.Bot or stub
    chat_id: int
    message_id: int
    decided: asyncio.Event = field(default_factory=asyncio.Event)
    timer_task: Optional[asyncio.Task] = None


class ApprovalManager:
    def __init__(
        self,
        store: ApprovalStore,
        executor: Executor,
        config: Optional[ApprovalManagerConfig] = None,
    ):
        self.store = store
        self.executor = executor
        self.config = config or ApprovalManagerConfig()
        self._pending: Dict[str, _PendingEntry] = {}
        # Holding bay for MODIFY_REQUESTED proposals awaiting /retry_<id> <amount>.
        # Metadata (account_name/scenario/holding_qty) isn't persisted to SQLite,
        # so we keep the original proposal in memory until the user retries or
        # the entry expires.
        self._modify_pending: Dict[str, TradeProposal] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ public

    async def request_approval(
        self, bot, chat_id: int, proposal: TradeProposal
    ) -> ApprovalRecord:
        """Send the approval prompt to Telegram and start the expiry timer.

        Emergency stop-loss path (proposal.auto_execute=True AND config.auto_stop_loss=True):
        skip Telegram entirely and execute the order immediately, persisting the
        decision as AUTO_EXECUTED.
        """
        if proposal.auto_execute and self.config.auto_stop_loss:
            return await self._auto_execute(proposal, chat_id=chat_id)

        text = build_approval_message(
            proposal,
            expires_at=expires_at(proposal.proposed_at, self.config.timeout_seconds),
        )
        keyboard = build_approval_keyboard(proposal)

        sent = await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=keyboard,
            parse_mode=self.config.parse_mode,
        )

        record = self.store.insert_proposal(
            proposal,
            chat_id=chat_id,
            message_id=getattr(sent, "message_id", None),
        )

        entry = _PendingEntry(
            proposal=proposal,
            bot=bot,
            chat_id=chat_id,
            message_id=getattr(sent, "message_id", 0),
        )
        async with self._lock:
            self._pending[proposal.approval_id] = entry

        entry.timer_task = asyncio.create_task(
            self._expire_after(proposal.approval_id, self.config.timeout_seconds)
        )
        logger.info("approval requested: %s for %s", proposal.short_id(), proposal.ticker)
        return record

    async def handle_callback(self, query) -> ApprovalDecision:
        """Process a Telegram CallbackQuery. Returns the resolved decision.

        `query` must expose `.data` (str), `.from_user.id`, `.answer()`,
        `.edit_message_text()`. The standard python-telegram-bot CallbackQuery
        satisfies this; tests pass a stub.
        """
        parsed = parse_callback_data(query.data)
        if parsed is None:
            await query.answer("알 수 없는 콜백입니다", show_alert=True)
            return ApprovalDecision.PENDING

        async with self._lock:
            entry = self._find_entry_by_short_id(parsed.short_id)
        if entry is None:
            await query.answer("이미 처리되었거나 만료된 요청입니다", show_alert=True)
            return ApprovalDecision.EXPIRED

        user_id = str(getattr(getattr(query, "from_user", None), "id", "")) or "unknown"

        if parsed.action == ACTION_APPROVE:
            await query.answer("승인되었습니다 — 주문을 실행합니다")
            return await self._resolve_approve(entry, decided_by=user_id, query=query)

        if parsed.action == ACTION_REJECT:
            await query.answer("거절되었습니다")
            return await self._resolve_reject(entry, decided_by=user_id, query=query)

        # ACTION_MODIFY
        await query.answer("금액 수정 요청을 기록했습니다")
        return await self._resolve_modify(entry, decided_by=user_id, query=query)

    async def cancel_pending(self, approval_id: str) -> bool:
        """Force-cancel a pending approval (e.g. on shutdown)."""
        async with self._lock:
            entry = self._pending.pop(approval_id, None)
        if entry is None:
            return False
        if entry.timer_task and not entry.timer_task.done():
            entry.timer_task.cancel()
        self.store.update_decision(approval_id, ApprovalDecision.EXPIRED)
        return True

    # ------------------------------------------------------------------ internal

    def _find_entry_by_short_id(self, short_id: str) -> Optional[_PendingEntry]:
        for approval_id, entry in self._pending.items():
            if approval_id.startswith(short_id):
                return entry
        return None

    async def _expire_after(self, approval_id: str, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return

        async with self._lock:
            entry = self._pending.pop(approval_id, None)
        if entry is None:
            return  # already resolved

        self.store.update_decision(approval_id, ApprovalDecision.EXPIRED)
        try:
            await entry.bot.edit_message_text(
                chat_id=entry.chat_id,
                message_id=entry.message_id,
                text=self.config.expired_message_template,
            )
        except Exception as exc:  # pragma: no cover — best-effort UX
            logger.warning("failed to edit expired message: %s", exc)
        entry.decided.set()
        logger.info("approval expired: %s", approval_id[:12])

    async def _resolve_approve(
        self, entry: _PendingEntry, *, decided_by: str, query
    ) -> ApprovalDecision:
        approval_id = entry.proposal.approval_id
        self._discard(approval_id)
        # Persist intermediate APPROVED state before we kick off the order, so
        # an executor crash still leaves an audit trail.
        self.store.update_decision(
            approval_id, ApprovalDecision.APPROVED,
            decided_by=decided_by, final_amount_krw=entry.proposal.proposed_amount_krw,
        )
        await self._edit(query, self.config.approved_message_template)

        result = await self._invoke_executor(entry.proposal, entry.proposal.proposed_amount_krw)
        self.store.update_decision(
            approval_id,
            ApprovalDecision.APPROVED,
            decided_by=decided_by,
            final_amount_krw=entry.proposal.proposed_amount_krw,
            execution=result,
        )
        await self._notify_execution(entry, result)
        entry.decided.set()
        return ApprovalDecision.APPROVED

    async def _resolve_reject(
        self, entry: _PendingEntry, *, decided_by: str, query
    ) -> ApprovalDecision:
        approval_id = entry.proposal.approval_id
        self._discard(approval_id)
        self.store.update_decision(
            approval_id, ApprovalDecision.REJECTED, decided_by=decided_by,
        )
        await self._edit(query, self.config.rejected_message_template)
        entry.decided.set()
        return ApprovalDecision.REJECTED

    async def _resolve_modify(
        self, entry: _PendingEntry, *, decided_by: str, query
    ) -> ApprovalDecision:
        approval_id = entry.proposal.approval_id
        self._discard(approval_id)
        self.store.update_decision(
            approval_id, ApprovalDecision.MODIFY_REQUESTED, decided_by=decided_by,
        )
        # Stash the proposal so /retry_<short_id> <amount> can rebuild a new
        # TradeProposal with all the original metadata (account, scenario,
        # holding_qty) preserved. Reaped by _expire_modify_after.
        async with self._lock:
            self._modify_pending[approval_id] = entry.proposal
        asyncio.create_task(
            self._expire_modify_after(approval_id, self.config.timeout_seconds)
        )
        await self._edit(query, self.config.modify_message_template.format(
            short_id=entry.proposal.short_id()
        ))
        entry.decided.set()
        return ApprovalDecision.MODIFY_REQUESTED

    async def _expire_modify_after(self, approval_id: str, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        async with self._lock:
            self._modify_pending.pop(approval_id, None)

    async def retry_with_amount(
        self,
        bot,
        chat_id: int,
        *,
        short_id: str,
        new_amount_krw: int,
    ) -> Optional[TradeProposal]:
        """Build a fresh proposal from a MODIFY_REQUESTED stash and re-prompt.

        Returns the new TradeProposal on success, None if the short_id has no
        matching modify-stash (already retried, expired, or never modified).
        The original MODIFY_REQUESTED record in SQLite is left intact for
        audit; the new proposal gets a brand-new approval_id.
        """
        if new_amount_krw <= 0:
            return None

        async with self._lock:
            stash_key = None
            for approval_id in self._modify_pending:
                if approval_id.startswith(short_id):
                    stash_key = approval_id
                    break
            if stash_key is None:
                return None
            original = self._modify_pending.pop(stash_key)

        # New proposal: same fields, new amount, new approval_id (auto-gen),
        # new proposed_at, never inherits auto_execute from a sell stop-loss
        # (a stop-loss skipping approval can't have been MODIFY_REQUESTED).
        new_proposal = TradeProposal(
            ticker=original.ticker,
            stock_name=original.stock_name,
            side=original.side,
            entry_price=original.entry_price,
            proposed_amount_krw=int(new_amount_krw),
            stop_loss=original.stop_loss,
            target_price=original.target_price,
            score=original.score,
            confidence=original.confidence,
            rationale=list(original.rationale),
            trigger_type=original.trigger_type,
            auto_execute=False,
            metadata=dict(original.metadata),
        )
        await self.request_approval(bot, chat_id, new_proposal)
        logger.info(
            "approval retried: old=%s new=%s amount=%s",
            short_id, new_proposal.short_id(), new_amount_krw,
        )
        return new_proposal

    async def _auto_execute(self, proposal: TradeProposal, *, chat_id: int) -> ApprovalRecord:
        record = self.store.insert_proposal(proposal, chat_id=chat_id, message_id=None)
        self.store.update_decision(
            proposal.approval_id, ApprovalDecision.AUTO_EXECUTED,
            decided_by="system", final_amount_krw=proposal.proposed_amount_krw,
        )
        result = await self._invoke_executor(proposal, proposal.proposed_amount_krw)
        self.store.update_decision(
            proposal.approval_id, ApprovalDecision.AUTO_EXECUTED,
            decided_by="system", final_amount_krw=proposal.proposed_amount_krw,
            execution=result,
        )
        logger.warning(
            "auto-executed proposal %s for %s (stop-loss bypass)",
            proposal.short_id(), proposal.ticker,
        )
        return record

    async def _invoke_executor(self, proposal: TradeProposal, amount_krw: int) -> ExecutionResult:
        try:
            return await self.executor(proposal, amount_krw)
        except Exception as exc:
            logger.exception("executor raised for %s", proposal.short_id())
            return ExecutionResult(success=False, message=f"executor exception: {exc}")

    def _discard(self, approval_id: str) -> None:
        entry = self._pending.pop(approval_id, None)
        if entry and entry.timer_task and not entry.timer_task.done():
            entry.timer_task.cancel()

    async def _edit(self, query, text: str) -> None:
        try:
            await query.edit_message_text(text=text)
        except Exception as exc:  # pragma: no cover
            logger.warning("failed to edit approval message: %s", exc)

    async def _notify_execution(self, entry: _PendingEntry, result: ExecutionResult) -> None:
        if result.success:
            text = self.config.executed_message_template.format(
                order_no=result.order_no or "?",
                quantity=result.quantity,
                fill_price=int(result.fill_price or 0),
            )
        else:
            text = self.config.execution_failed_template.format(message=result.message)
        try:
            await entry.bot.send_message(chat_id=entry.chat_id, text=text)
        except Exception as exc:  # pragma: no cover
            logger.warning("failed to send execution notification: %s", exc)
