"""Transitional single entry point for broker order execution.

Phase 2 of issue #412 is intentionally a behaviour-preserving strangler step.
Phase 3 adds optional additive OrderIntent persistence around new broker orders.
Callers without an intent retain the Phase 2 behaviour-preserving delegation;
production call sites pass an intent and store explicitly.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Any

from prism_core.order_intents import IntentStore, OrderIntent


logger = logging.getLogger(__name__)


class OrderOutcomeUnknown(RuntimeError):
    """The broker may have accepted the order, so callers must not mark rejection."""

    def __init__(
        self,
        intent_id: str,
        *,
        broker_result: Any = None,
        cause: BaseException | None = None,
    ):
        super().__init__(f"order outcome unknown for intent {intent_id}")
        self.intent_id = intent_id
        self.broker_result = broker_result
        self.cause = cause


class ExecutionService:
    """Wrap an existing trader or async trading context without changing it."""

    _DIRECT_ORDER_METHODS = {
        "async_buy_stock",
        "async_sell_stock",
        "amend_order",
        "cancel_order",
        "buy_reserved_order",
        "sell_reserved_order",
    }
    _AMBIGUOUS_FAILURE_MARKERS = (
        "timeout",
        "timed out",
        "error during",
        "exception",
        "connection",
        "network",
        "temporarily unavailable",
        "request failed",
    )

    def __init__(
        self,
        context_or_trader: Any,
        *,
        intent_store: IntentStore | None = None,
    ):
        self._resource = context_or_trader
        self._trader: Any | None = None
        self._entered_context = False
        self._intent_store = intent_store

    @classmethod
    def domestic(
        cls,
        account_name: str | None = None,
        *,
        db_path: str | Path | None = None,
    ) -> "ExecutionService":
        from trading.domestic_stock_trading import AsyncTradingContext

        store = IntentStore(db_path) if db_path is not None else None
        return cls(
            AsyncTradingContext(account_name=account_name),
            intent_store=store,
        )

    @classmethod
    def us(
        cls,
        account_name: str | None = None,
        *,
        db_path: str | Path | None = None,
    ) -> "ExecutionService":
        try:
            from trading.us_stock_trading import AsyncUSTradingContext
        except ModuleNotFoundError as exc:
            if exc.name != "trading.us_stock_trading":
                raise
            # Some long-lived processes import the root ``trading`` package
            # before switching to the US runtime. Python then keeps that package
            # cached and cannot discover ``prism-us/trading`` as a subpackage.
            # Import the existing standalone module path used by the loop tools
            # instead of deleting or replacing the process-wide package cache.
            us_trading_dir = Path(__file__).resolve().parents[1] / "prism-us" / "trading"
            if not us_trading_dir.is_dir():
                raise
            path = str(us_trading_dir)
            if path not in sys.path:
                sys.path.insert(0, path)
            from us_stock_trading import AsyncUSTradingContext

        store = IntentStore(db_path) if db_path is not None else None
        return cls(
            AsyncUSTradingContext(account_name=account_name),
            intent_store=store,
        )

    async def __aenter__(self) -> "ExecutionService":
        enter = getattr(self._resource, "__aenter__", None)
        if enter is not None:
            self._trader = await enter()
            self._entered_context = True
        else:
            self._trader = self._resource
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if not self._entered_context:
            return None
        exit_context = getattr(self._resource, "__aexit__")
        return await exit_context(exc_type, exc, tb)

    @property
    def _active_trader(self) -> Any:
        return self._trader if self._trader is not None else self._resource

    def __getattr__(self, name: str) -> Any:
        """Preserve read-only/query calls while order calls move explicitly."""
        if name in self._DIRECT_ORDER_METHODS:
            raise AttributeError(
                f"direct order method {name!r} is blocked; use ExecutionService methods"
            )
        return getattr(self._active_trader, name)

    @classmethod
    def _classify_result(cls, result: Any) -> tuple[str, bool, str]:
        if not isinstance(result, dict):
            return "UNKNOWN", False, "KIS"
        order_type = str(result.get("order_type") or "").lower()
        order_no = str(result.get("order_no") or "").upper()
        if order_type.startswith("queued_") or order_no.startswith("PENDING-"):
            return "QUEUED", True, "LOCAL_QUEUE"
        if result.get("success") or result.get("partial_success"):
            return "SUBMITTED", True, "KIS"
        message = str(result.get("message") or "").lower()
        if any(marker in message for marker in cls._AMBIGUOUS_FAILURE_MARKERS):
            return "UNKNOWN", False, "KIS"
        return "FAILED", False, "KIS"

    @staticmethod
    def _with_intent_metadata(
        result: Any,
        *,
        intent: OrderIntent,
        status: str,
        broker: str,
    ) -> Any:
        if not isinstance(result, dict):
            return result
        enriched = dict(result)
        enriched["intent_id"] = intent.id
        enriched["intent_status"] = status
        enriched["intent_broker"] = broker
        return enriched

    async def _execute_order(
        self,
        method,
        *args,
        intent: OrderIntent | None = None,
        **kwargs,
    ):
        if intent is None:
            return await method(*args, **kwargs)
        if self._intent_store is None:
            raise RuntimeError(
                "OrderIntent was provided without an IntentStore; broker call blocked"
            )

        created, existing = await asyncio.to_thread(
            self._intent_store.reserve, intent
        )
        if not created:
            logger.warning(
                "[ORDER_INTENT] duplicate blocked id=%s status=%s market=%s side=%s symbol=%s",
                existing["id"], existing["status"], intent.market, intent.side,
                intent.symbol,
            )
            return self._intent_store.blocked_result(existing)
        await asyncio.to_thread(self._intent_store.mark_submitting, intent.id)

        try:
            result = await method(*args, **kwargs)
        except asyncio.CancelledError as exc:
            await asyncio.to_thread(
                self._intent_store.record_result,
                intent,
                status="UNKNOWN",
                accepted=False,
                response=None,
                error=exc,
            )
            logger.error(
                "[ORDER_INTENT] UNKNOWN id=%s market=%s side=%s symbol=%s error=%s",
                intent.id, intent.market, intent.side, intent.symbol,
                type(exc).__name__,
            )
            raise
        except Exception as exc:
            try:
                await asyncio.to_thread(
                    self._intent_store.record_result,
                    intent,
                    status="UNKNOWN",
                    accepted=False,
                    response=None,
                    error=exc,
                )
            except Exception as persistence_error:
                logger.critical(
                    "[ORDER_INTENT] UNKNOWN persistence failed id=%s error=%s",
                    intent.id,
                    type(persistence_error).__name__,
                )
            raise OrderOutcomeUnknown(intent.id, cause=exc) from exc

        status, accepted, broker = self._classify_result(result)
        try:
            await asyncio.to_thread(
                self._intent_store.record_result,
                intent,
                status=status,
                accepted=accepted,
                broker=broker,
                response=result,
            )
        except Exception as exc:
            logger.critical(
                "[ORDER_INTENT] broker returned but persistence failed id=%s status=%s",
                intent.id,
                status,
            )
            raise OrderOutcomeUnknown(
                intent.id,
                broker_result=result,
                cause=exc,
            ) from exc
        logger.log(
            logging.INFO if accepted else logging.ERROR,
            "[ORDER_INTENT] %s id=%s market=%s side=%s symbol=%s",
            status,
            intent.id,
            intent.market,
            intent.side,
            intent.symbol,
        )
        return self._with_intent_metadata(
            result,
            intent=intent,
            status=status,
            broker=broker,
        )

    def _execute_order_sync(
        self,
        method,
        *args,
        intent: OrderIntent | None = None,
        **kwargs,
    ):
        if intent is None:
            return method(*args, **kwargs)
        if self._intent_store is None:
            raise RuntimeError(
                "OrderIntent was provided without an IntentStore; broker call blocked"
            )

        created, existing = self._intent_store.reserve(intent)
        if not created:
            logger.warning(
                "[ORDER_INTENT] duplicate blocked id=%s status=%s market=%s side=%s symbol=%s",
                existing["id"], existing["status"], intent.market, intent.side,
                intent.symbol,
            )
            return self._intent_store.blocked_result(existing)
        self._intent_store.mark_submitting(intent.id)
        try:
            result = method(*args, **kwargs)
        except Exception as exc:
            try:
                self._intent_store.record_result(
                    intent,
                    status="UNKNOWN",
                    accepted=False,
                    response=None,
                    error=exc,
                )
            except Exception as persistence_error:
                logger.critical(
                    "[ORDER_INTENT] UNKNOWN persistence failed id=%s error=%s",
                    intent.id,
                    type(persistence_error).__name__,
                )
            raise OrderOutcomeUnknown(intent.id, cause=exc) from exc
        status, accepted, broker = self._classify_result(result)
        try:
            self._intent_store.record_result(
                intent,
                status=status,
                accepted=accepted,
                broker=broker,
                response=result,
            )
        except Exception as exc:
            logger.critical(
                "[ORDER_INTENT] broker returned but persistence failed id=%s status=%s",
                intent.id,
                status,
            )
            raise OrderOutcomeUnknown(
                intent.id,
                broker_result=result,
                cause=exc,
            ) from exc
        logger.log(
            logging.INFO if accepted else logging.ERROR,
            "[ORDER_INTENT] %s id=%s market=%s side=%s symbol=%s",
            status,
            intent.id,
            intent.market,
            intent.side,
            intent.symbol,
        )
        return self._with_intent_metadata(
            result,
            intent=intent,
            status=status,
            broker=broker,
        )

    async def execute_buy(
        self,
        *args,
        intent: OrderIntent | None = None,
        **kwargs,
    ):
        return await self._execute_order(
            self._active_trader.async_buy_stock,
            *args,
            intent=intent,
            **kwargs,
        )

    async def execute_sell(
        self,
        *args,
        intent: OrderIntent | None = None,
        **kwargs,
    ):
        return await self._execute_order(
            self._active_trader.async_sell_stock,
            *args,
            intent=intent,
            **kwargs,
        )

    async def amend_or_cancel(self, action: str, *args, **kwargs):
        return await asyncio.to_thread(
            self.amend_or_cancel_sync, action, *args, **kwargs
        )

    def amend_or_cancel_sync(self, action: str, *args, **kwargs):
        """Forward synchronous amend/cancel calls, including dry-run payloads."""
        if action == "amend":
            method = self._active_trader.amend_order
        elif action == "cancel":
            method = self._active_trader.cancel_order
        else:
            raise ValueError(f"unsupported order action: {action}")
        return method(*args, **kwargs)

    def execute_reserved_buy(
        self,
        *args,
        intent: OrderIntent | None = None,
        **kwargs,
    ):
        return self._execute_order_sync(
            self._active_trader.buy_reserved_order,
            *args,
            intent=intent,
            **kwargs,
        )

    def execute_reserved_sell(
        self,
        *args,
        intent: OrderIntent | None = None,
        **kwargs,
    ):
        return self._execute_order_sync(
            self._active_trader.sell_reserved_order,
            *args,
            intent=intent,
            **kwargs,
        )


__all__ = ["ExecutionService", "OrderOutcomeUnknown"]
