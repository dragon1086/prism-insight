"""Transitional single entry point for broker order execution.

Phase 2 of issue #412 is intentionally a behaviour-preserving strangler step.
This wrapper owns no retry, persistence, locking, or idempotency policy; it only
forwards the existing trading-context calls behind explicit order methods.
Those policies belong to later phases after the current behaviour is covered by
regression tests.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any


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

    def __init__(self, context_or_trader: Any):
        self._resource = context_or_trader
        self._trader: Any | None = None
        self._entered_context = False

    @classmethod
    def domestic(cls, account_name: str | None = None) -> "ExecutionService":
        from trading.domestic_stock_trading import AsyncTradingContext

        return cls(AsyncTradingContext(account_name=account_name))

    @classmethod
    def us(cls, account_name: str | None = None) -> "ExecutionService":
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

        return cls(AsyncUSTradingContext(account_name=account_name))

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

    async def execute_buy(self, *args, **kwargs):
        return await self._active_trader.async_buy_stock(*args, **kwargs)

    async def execute_sell(self, *args, **kwargs):
        return await self._active_trader.async_sell_stock(*args, **kwargs)

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

    def execute_reserved_buy(self, *args, **kwargs):
        return self._active_trader.buy_reserved_order(*args, **kwargs)

    def execute_reserved_sell(self, *args, **kwargs):
        return self._active_trader.sell_reserved_order(*args, **kwargs)


__all__ = ["ExecutionService"]
