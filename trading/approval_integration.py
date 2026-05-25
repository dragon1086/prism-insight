"""Wire the generic approval/ layer into prism-insight's trading pipeline.

Default behavior: **OFF**. Existing code continues to invoke
`AsyncTradingContext.async_buy_stock` / `async_sell_stock` directly. When
`ENABLE_TRADE_APPROVAL=true` is set, AI buy/sell decisions instead route
through `ApprovalManager` → Telegram approval → KIS order.

Environment variables (all optional):

  ENABLE_TRADE_APPROVAL     "true" to enable the approval gate (default false)
  APPROVAL_DB_PATH          SQLite path for trade_approvals (default
                            ./trade_approvals.db)
  APPROVAL_TIMEOUT_SECONDS  auto-expire window in seconds (default 1800)
  AUTO_STOP_LOSS_BYPASS     "true" to let emergency stop-loss sells skip
                            approval (default false)

Account routing: each `TradeProposal.metadata["account_name"]` selects the
KIS account when the executor fires. This keeps the approval layer
agnostic of multi-account specifics — the dispatcher in this module reads
the metadata, opens the right `AsyncTradingContext`, and calls KIS.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Awaitable, Callable, Dict, Optional

from approval.handler import ApprovalManager, ApprovalManagerConfig
from approval.models import (
    ApprovalDecision,
    ExecutionResult,
    TradeProposal,
    TradeSide,
)
from approval.store import ApprovalStore

logger = logging.getLogger(__name__)

# Module-level singletons. Reset via reset_for_tests() in unit tests.
_store: Optional[ApprovalStore] = None
_manager: Optional[ApprovalManager] = None


def _truthy(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def is_enabled() -> bool:
    """Whether the approval gate is active for this process."""
    return _truthy(os.environ.get("ENABLE_TRADE_APPROVAL"))


def get_store() -> ApprovalStore:
    """Return the shared ApprovalStore. Builds the SQLite connection on first call."""
    global _store
    if _store is None:
        db_path = os.environ.get("APPROVAL_DB_PATH", "trade_approvals.db")
        _store = ApprovalStore(db_path)
        logger.info("ApprovalStore opened at %s", db_path)
    return _store


def get_manager() -> ApprovalManager:
    """Return the shared ApprovalManager singleton.

    Uses a unified KIS executor that dispatches by `proposal.side` and
    `proposal.metadata["account_name"]`, so callers don't need separate
    manager instances per account.
    """
    global _manager
    if _manager is None:
        config = ApprovalManagerConfig(
            timeout_seconds=int(os.environ.get("APPROVAL_TIMEOUT_SECONDS", "1800")),
            auto_stop_loss=_truthy(os.environ.get("AUTO_STOP_LOSS_BYPASS")),
        )
        _manager = ApprovalManager(get_store(), _kis_executor, config)
        logger.info(
            "ApprovalManager initialised (timeout=%ds, auto_stop_loss=%s)",
            config.timeout_seconds, config.auto_stop_loss,
        )
    return _manager


def reset_for_tests() -> None:
    """Drop cached singletons. Tests use this to isolate state."""
    global _store, _manager
    if _store is not None:
        try:
            _store.close()
        except Exception:
            pass
    _store = None
    _manager = None


# ---------------------------------------------------------- proposal builders


def build_buy_proposal(
    *,
    ticker: str,
    company_name: str,
    current_price: float,
    scenario: Dict[str, Any],
    rank_change_msg: str = "",
    account_name: Optional[str] = None,
    source: str = "AI Analysis",
) -> TradeProposal:
    """Convert a Buy Specialist signal into a TradeProposal."""
    rationale = []
    if rank_change_msg:
        rationale.append(rank_change_msg)
    for key in ("rationale", "reason", "summary", "buy_reason"):
        v = scenario.get(key)
        if v:
            rationale.append(str(v)[:200])

    score = scenario.get("buy_score") or scenario.get("score")
    return TradeProposal(
        ticker=ticker,
        stock_name=company_name,
        side=TradeSide.BUY,
        entry_price=float(current_price or 0),
        proposed_amount_krw=int(scenario.get("buy_amount_krw") or scenario.get("amount") or 0),
        stop_loss=_safe_float(scenario.get("stop_loss")),
        target_price=_safe_float(scenario.get("target_price")),
        score=int(score) if score is not None else None,
        rationale=rationale[:3],
        trigger_type=scenario.get("trigger_type") or scenario.get("trigger"),
        auto_execute=False,  # BUYs always require human approval
        metadata={
            "account_name": account_name,
            "source": source,
            "scenario": scenario,  # preserved for downstream signal publish
        },
    )


def build_sell_proposal(
    *,
    ticker: str,
    company_name: str,
    current_price: float,
    sell_reason: str,
    buy_price: float = 0.0,
    is_stop_loss: bool = False,
    holding_qty: int = 0,
    account_name: Optional[str] = None,
    source: str = "AI Analysis",
) -> TradeProposal:
    """Convert a Sell Specialist (or stop-loss) signal into a TradeProposal.

    `is_stop_loss=True` flips the auto_execute bit so a stop-loss can bypass
    approval if `AUTO_STOP_LOSS_BYPASS` is also set in the manager config.
    """
    return TradeProposal(
        ticker=ticker,
        stock_name=company_name,
        side=TradeSide.SELL,
        entry_price=float(current_price or 0),
        proposed_amount_krw=int(holding_qty * (current_price or 0)),
        stop_loss=float(buy_price) if buy_price else None,
        target_price=None,
        score=None,
        rationale=[sell_reason[:200]] if sell_reason else [],
        trigger_type="stop_loss" if is_stop_loss else "sell_signal",
        auto_execute=is_stop_loss,
        metadata={
            "account_name": account_name,
            "source": source,
            "holding_qty": holding_qty,
            "buy_price": buy_price,
        },
    )


# ---------------------------------------------------------- KIS executor


async def _kis_executor(proposal: TradeProposal, amount_krw: int) -> ExecutionResult:
    """Single executor that dispatches BUY/SELL to KIS using the metadata-provided account.

    After the KIS call returns, this also fans out the existing signal-
    publishing pipeline (Redis + GCP Pub/Sub) so that approving via Telegram
    does not lose the downstream notifications a direct call would have made.
    Each publish is best-effort and any failure is logged but does not break
    the executor return value.

    Imports are deferred so this module can be loaded (and tested) without
    pulling in pandas/PyKRX via trading.domestic_stock_trading.
    """
    from trading.domestic_stock_trading import AsyncTradingContext

    metadata = proposal.metadata or {}
    account_name = metadata.get("account_name")
    limit_price = int(proposal.entry_price)

    async with AsyncTradingContext(account_name=account_name) as trading:
        if proposal.side == TradeSide.BUY:
            raw = await trading.async_buy_stock(
                stock_code=proposal.ticker, limit_price=limit_price,
            )
        else:
            raw = await trading.async_sell_stock(
                stock_code=proposal.ticker, limit_price=limit_price,
            )

    result = _execution_from_kis_result(raw, proposal.entry_price)

    # Best-effort signal publish — mirrors the inline flow in
    # stock_tracking_agent.py when approval is disabled.
    if result.success:
        await _publish_signal(proposal, result, metadata)

    return result


async def _publish_signal(
    proposal: TradeProposal, result: ExecutionResult, metadata: Dict[str, Any]
) -> None:
    """Publish buy/sell signal to Redis Streams and GCP Pub/Sub if configured."""
    source = metadata.get("source", "AI Analysis")
    if proposal.side == TradeSide.BUY:
        scenario = metadata.get("scenario", {})
        try:
            from messaging.redis_signal_publisher import publish_buy_signal
            await publish_buy_signal(
                ticker=proposal.ticker, company_name=proposal.stock_name,
                price=proposal.entry_price, scenario=scenario,
                source=source, trade_result=result.raw,
            )
        except Exception as exc:
            logger.warning("Redis buy signal publish failed (non-critical): %s", exc)
        try:
            from messaging.gcp_pubsub_signal_publisher import publish_buy_signal as gcp_publish_buy_signal
            await gcp_publish_buy_signal(
                ticker=proposal.ticker, company_name=proposal.stock_name,
                price=proposal.entry_price, scenario=scenario,
                source=source, trade_result=result.raw,
            )
        except Exception as exc:
            logger.warning("GCP buy signal publish failed (non-critical): %s", exc)
    else:
        buy_price = float(metadata.get("buy_price") or 0)
        profit_rate = (
            (proposal.entry_price - buy_price) / buy_price * 100
            if buy_price else 0.0
        )
        sell_reason = proposal.rationale[0] if proposal.rationale else ""
        try:
            from messaging.redis_signal_publisher import publish_sell_signal
            await publish_sell_signal(
                ticker=proposal.ticker, company_name=proposal.stock_name,
                price=proposal.entry_price, buy_price=buy_price,
                profit_rate=profit_rate, sell_reason=sell_reason,
                trade_result=result.raw,
            )
        except Exception as exc:
            logger.warning("Redis sell signal publish failed (non-critical): %s", exc)
        try:
            from messaging.gcp_pubsub_signal_publisher import publish_sell_signal as gcp_publish_sell_signal
            await gcp_publish_sell_signal(
                ticker=proposal.ticker, company_name=proposal.stock_name,
                price=proposal.entry_price, buy_price=buy_price,
                profit_rate=profit_rate, sell_reason=sell_reason,
                trade_result=result.raw,
            )
        except Exception as exc:
            logger.warning("GCP sell signal publish failed (non-critical): %s", exc)


def _execution_from_kis_result(raw: Dict[str, Any], fallback_price: float) -> ExecutionResult:
    """Normalise the dict returned by trading/domestic_stock_trading.py into ExecutionResult."""
    return ExecutionResult(
        success=bool(raw.get("success")),
        order_no=raw.get("order_no"),
        quantity=int(raw.get("quantity") or 0),
        fill_price=_safe_float(raw.get("fill_price")) or float(fallback_price or 0),
        message=str(raw.get("message") or ""),
        raw=raw,
    )


# ---------------------------------------------------------- pipeline glue


async def request_buy_approval(
    bot,
    chat_id: int,
    *,
    ticker: str,
    company_name: str,
    current_price: float,
    scenario: Dict[str, Any],
    rank_change_msg: str = "",
    account_name: Optional[str] = None,
) -> Optional[TradeProposal]:
    """Submit a BUY proposal to the approval queue. Returns the TradeProposal
    if the gate is enabled, None otherwise. Callers should fall back to the
    direct KIS path when None is returned.
    """
    if not is_enabled():
        return None
    proposal = build_buy_proposal(
        ticker=ticker, company_name=company_name, current_price=current_price,
        scenario=scenario, rank_change_msg=rank_change_msg, account_name=account_name,
    )
    manager = get_manager()
    await manager.request_approval(bot, chat_id, proposal)
    return proposal


async def request_sell_approval(
    bot,
    chat_id: int,
    *,
    ticker: str,
    company_name: str,
    current_price: float,
    sell_reason: str,
    buy_price: float = 0.0,
    is_stop_loss: bool = False,
    holding_qty: int = 0,
    account_name: Optional[str] = None,
) -> Optional[TradeProposal]:
    """Submit a SELL proposal. Returns the TradeProposal if gate is enabled."""
    if not is_enabled():
        return None
    proposal = build_sell_proposal(
        ticker=ticker, company_name=company_name, current_price=current_price,
        sell_reason=sell_reason, buy_price=buy_price, is_stop_loss=is_stop_loss,
        holding_qty=holding_qty, account_name=account_name,
    )
    manager = get_manager()
    await manager.request_approval(bot, chat_id, proposal)
    return proposal


# ---------------------------------------------------------- Telegram handler


async def telegram_callback_handler(update, context) -> None:
    """python-telegram-bot CallbackQueryHandler entry-point.

    Register with `application.add_handler(CallbackQueryHandler(
        telegram_callback_handler, pattern=r"^apv:"))`. Safe to register even
    when ENABLE_TRADE_APPROVAL=false — it'll see no traffic because nothing
    will have sent the approval messages with `apv:` callback_data.
    """
    query = update.callback_query
    if query is None:
        return
    try:
        decision = await get_manager().handle_callback(query)
        logger.info("approval callback resolved: %s", decision.value)
    except Exception:
        logger.exception("approval callback dispatch failed")
        try:
            await query.answer("승인 처리 중 오류가 발생했습니다", show_alert=True)
        except Exception:
            pass


# ------------------------------------------------ MODIFY → /retry_<id> <amount>

import re as _re

_RETRY_PATTERN = _re.compile(
    r"^/retry_([0-9a-fA-F]{6,32})(?:[\s_]+([\d,]+))?\s*$"
)


async def telegram_retry_handler(update, context) -> None:
    """python-telegram-bot MessageHandler entry-point for `/retry_<id> <amount>`.

    Register with `application.add_handler(MessageHandler(
        filters.Regex(r'^/retry_[a-f0-9]'), telegram_retry_handler))`. Inert
    when ENABLE_TRADE_APPROVAL=false (the manager won't have any modify-
    pending entries, so retry always reports "expired").
    """
    msg = getattr(update, "message", None)
    if msg is None or not getattr(msg, "text", None):
        return
    match = _RETRY_PATTERN.match(msg.text.strip())
    if match is None:
        await msg.reply_text(
            "사용법: /retry_<승인ID> <금액(원)>\n예: /retry_abc123def456 500000"
        )
        return
    short_id = match.group(1).lower()
    amount_raw = match.group(2)
    if not amount_raw:
        await msg.reply_text(
            "금액을 함께 입력해주세요. 예: /retry_" + short_id + " 500000"
        )
        return
    try:
        new_amount_krw = int(amount_raw.replace(",", ""))
    except ValueError:
        await msg.reply_text("금액은 숫자로 입력해주세요. 예: 500000")
        return
    if new_amount_krw <= 0:
        await msg.reply_text("금액은 0보다 커야 합니다.")
        return

    chat_id = msg.chat.id if getattr(msg, "chat", None) else None
    if chat_id is None:
        return
    bot = context.bot if context and getattr(context, "bot", None) else msg.get_bot()

    try:
        new_proposal = await get_manager().retry_with_amount(
            bot, chat_id, short_id=short_id, new_amount_krw=new_amount_krw,
        )
    except Exception:
        logger.exception("retry dispatch failed for short_id=%s", short_id)
        await msg.reply_text("재요청 처리 중 오류가 발생했습니다.")
        return

    if new_proposal is None:
        await msg.reply_text(
            "해당 수정 요청을 찾을 수 없거나 만료되었습니다. "
            "(이미 재요청했거나 30분이 경과한 경우 새 매매 시그널을 기다려주세요)"
        )


# ---------------------------------------------------------- helpers


def _safe_float(value: Any) -> Optional[float]:
    if value in (None, "", "null"):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f != 0 else None
