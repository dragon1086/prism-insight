"""Transitional single entry point for broker order execution.

Phase 2 of issue #412 is intentionally a behaviour-preserving strangler step.
Phase 3 adds optional additive OrderIntent persistence around new broker orders.
Callers without an intent retain the Phase 2 behaviour-preserving delegation;
production call sites pass an intent and store explicitly.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
from pathlib import Path
from typing import Any

from prism_core.order_intents import IntentStore, OrderIntent


logger = logging.getLogger(__name__)
_stance_count_lock = threading.Lock()
_stance_set_counts = {"KR": 0, "US": 0}


def normalize_checked_holding(result: Any) -> tuple[str, int | None]:
    """Return only authoritative HELD/FLAT results; collapse malformed data."""

    if not isinstance(result, tuple) or len(result) != 2:
        return "UNKNOWN", None
    state = str(result[0] or "UNKNOWN").upper()
    quantity = result[1]
    if state == "HELD" and type(quantity) is int and quantity > 0:
        return "HELD", quantity
    if state == "FLAT" and type(quantity) is int and quantity == 0:
        return "FLAT", 0
    if state == "UNKNOWN" and quantity is None:
        return "UNKNOWN", None
    return "UNKNOWN", None


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


class _LocalFlatPersistenceError(RuntimeError):
    """Keep capability validation errors distinct from ledger write failures."""

    def __init__(self, cause: BaseException):
        super().__init__(str(cause))
        self.cause = cause


def _stance_reporter_from_env(
    market: str | None = None,
    account_name: str | None = None,
):
    """Stance 리포터를 만든다. 환경변수가 없으면 꺼진 것을 돌려준다.

    STANCE_ENDPOINT / STANCE_API_KEY 가 모두 있어야 켜진다.
    서버가 뜨기 전에 켜면 주문마다 죽은 엔드포인트를 때리므로 기본은 꺼짐이다.
    """
    try:
        from prism_core.stance_adapter import StanceReporter

        normalized_market = (market or "").upper()
        selected_account = os.getenv(f"STANCE_{normalized_market}_ACCOUNT")
        if selected_account and account_name is not None and selected_account != account_name:
            return None
        return StanceReporter.from_env(market)
    except Exception:  # pragma: no cover - 리포터가 매매를 막아서는 안 된다
        logger.debug("Stance reporter unavailable", exc_info=True)
        return None


def stance_declaration_count(market: str) -> int:
    """현재 프로세스에서 시도한 시장별 set 선언 수를 돌려준다."""
    with _stance_count_lock:
        return _stance_set_counts.get(market.upper(), 0)


async def declare_stance_hold(market: str, reason: str = "prism:no_portfolio_change") -> bool:
    """거래 없는 판단 주기의 Hold를 fail-open으로 한 번 전송한다."""
    reporter = _stance_reporter_from_env(market)
    if reporter is None or not getattr(reporter, "enabled", False):
        return False
    try:
        result = await asyncio.to_thread(reporter.report_hold, reason=reason)
        return result is not None
    except Exception:  # pragma: no cover - 리포터가 매매 배치를 막아서는 안 된다
        logger.warning("[STANCE] hold declaration failed market=%s", market, exc_info=True)
        return False


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
        stance_reporter: Any | None = None,
        stance_market: str | None = None,
    ):
        self._resource = context_or_trader
        self._trader: Any | None = None
        self._entered_context = False
        self._intent_store = intent_store
        # Stance 선언 전송. 환경변수가 없으면 꺼진 상태로 만들어져 아무 일도 하지 않는다.
        self._stance = stance_reporter
        self._stance_market = (stance_market or "").upper() or None

    @classmethod
    def domestic(
        cls,
        account_name: str | None = None,
        *,
        db_path: str | Path | None = None,
        intent_store: IntentStore | None = None,
    ) -> "ExecutionService":
        from trading.domestic_stock_trading import AsyncTradingContext

        if db_path is not None and intent_store is not None:
            requested_path = Path(db_path).expanduser().resolve()
            store_path = Path(intent_store.db_path).expanduser().resolve()
            if requested_path != store_path:
                raise ValueError(
                    "db_path must target the originating IntentStore database"
                )
        store = (
            intent_store
            if intent_store is not None
            else IntentStore(db_path) if db_path is not None else None
        )
        return cls(
            AsyncTradingContext(account_name=account_name),
            intent_store=store,
            stance_reporter=_stance_reporter_from_env("KR", account_name),
            stance_market="KR",
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
            stance_reporter=_stance_reporter_from_env("US", account_name),
            stance_market="US",
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
        if result.get("outcome_unknown") is True:
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
        before_order=None,
        **kwargs,
    ):
        if intent is None:
            if before_order is not None:
                await before_order()
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

        if before_order is not None:
            await before_order()

        return await self._execute_submitting_order(
            method, *args, intent=intent, **kwargs
        )

    async def _execute_submitting_order(
        self,
        method,
        *args,
        intent: OrderIntent,
        **kwargs,
    ):
        """Call broker and persist the result for an already-SUBMITTING intent."""

        try:
            result = await method(*args, **kwargs)
        except asyncio.CancelledError as exc:
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
                    "[ORDER_INTENT] cancellation UNKNOWN persistence failed "
                    "id=%s error=%s",
                    intent.id,
                    type(persistence_error).__name__,
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

    async def _execute_pre_reserved_order(
        self,
        method,
        *args,
        intent: OrderIntent,
        reservation: Any,
        side: str,
        before_order=None,
        **kwargs,
    ):
        await self._claim_pre_reserved(
            intent=intent,
            reservation=reservation,
            side=side,
        )
        if before_order is not None:
            await before_order()
        return await self._execute_submitting_order(
            method, *args, intent=intent, **kwargs
        )

    async def _claim_pre_reserved(
        self,
        *,
        intent: OrderIntent,
        reservation: Any,
        side: str,
    ) -> None:
        if self._intent_store is None:
            raise RuntimeError(
                "pre-reserved execution requires the originating IntentStore"
            )
        claim_task = asyncio.create_task(
            asyncio.to_thread(
                self._intent_store.claim_reservation,
                reservation,
                intent,
                expected_side=side,
            )
        )
        try:
            await asyncio.shield(claim_task)
        except asyncio.CancelledError:
            claimed = False
            try:
                await asyncio.shield(claim_task)
                claimed = True
            except Exception as claim_error:
                logger.critical(
                    "[ORDER_INTENT] cancelled pre-reserved claim failed id=%s error=%s",
                    intent.id,
                    type(claim_error).__name__,
                )
            if claimed:
                try:
                    await asyncio.to_thread(
                        self._intent_store.record_result,
                        intent,
                        status="UNKNOWN",
                        accepted=False,
                        response=None,
                        error=asyncio.CancelledError(),
                    )
                except Exception as persistence_error:
                    logger.critical(
                        "[ORDER_INTENT] cancelled pre-reserved UNKNOWN persistence "
                        "failed id=%s error=%s",
                        intent.id,
                        type(persistence_error).__name__,
                    )
            raise

    def _execute_order_sync(
        self,
        method,
        *args,
        intent: OrderIntent | None = None,
        before_order=None,
        **kwargs,
    ):
        if intent is None:
            if before_order is not None:
                before_order()
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

        if before_order is not None:
            before_order()

        return self._execute_submitting_order_sync(
            method, *args, intent=intent, **kwargs
        )

    def _execute_submitting_order_sync(
        self,
        method,
        *args,
        intent: OrderIntent,
        **kwargs,
    ):
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

    def _execute_pre_reserved_order_sync(
        self,
        method,
        *args,
        intent: OrderIntent,
        reservation: Any,
        side: str,
        before_order=None,
        **kwargs,
    ):
        if self._intent_store is None:
            raise RuntimeError(
                "pre-reserved execution requires the originating IntentStore"
            )
        self._intent_store.claim_reservation(
            reservation, intent, expected_side=side
        )
        if before_order is not None:
            before_order()
        return self._execute_submitting_order_sync(
            method, *args, intent=intent, **kwargs
        )


    def _declare_stance_sync(self, side: str, kwargs: dict) -> None:
        """Stance 에 판단을 선언한다. **매매를 막지 않는 것이 최우선이다.**

        - 주문 전에 호출한다. 사후 성과가 아니라 당시 판단을 기록한다.
        - 모든 예외를 삼킨다.
        - 서버가 죽어 있으면 클라이언트 타임아웃(기본 3초)만큼만 기다린다.
        """
        reporter = self._stance
        if reporter is None or not getattr(reporter, "enabled", False):
            return

        symbol = kwargs.get("stock_code") or kwargs.get("symbol")
        if not symbol:
            return

        if self._stance_market:
            with _stance_count_lock:
                _stance_set_counts[self._stance_market] = (
                    _stance_set_counts.get(self._stance_market, 0) + 1
                )

        try:
            from prism_core.stance_adapter import snapshot_from_trader

            snapshot = snapshot_from_trader(self._active_trader)
            if side == "BUY":
                reporter.report_buy(
                    snapshot,
                    symbol,
                    reason="prism",
                    add_amount=kwargs.get("buy_amount"),
                )
            else:
                reporter.report_sell(
                    symbol, reason="prism", snapshot=snapshot,
                    sold_qty=kwargs.get("quantity"),
                    held_qty=self._active_trader.get_holding_quantity(symbol),
                )
        except Exception:
            logger.warning("Stance declaration failed (%s %s)", side, symbol, exc_info=True)

    async def _declare_stance(self, side: str, kwargs: dict) -> None:
        """블로킹 선언 I/O를 이벤트 루프 밖에서 실행한다."""
        await asyncio.to_thread(self._declare_stance_sync, side, kwargs)

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
            before_order=lambda: self._declare_stance("BUY", kwargs),
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
            before_order=lambda: self._declare_stance("SELL", kwargs),
            **kwargs,
        )

    async def execute_pre_reserved_buy(
        self,
        *args,
        intent: OrderIntent,
        reservation: Any,
        **kwargs,
    ):
        return await self._execute_pre_reserved_order(
            self._active_trader.async_buy_stock,
            *args,
            intent=intent,
            reservation=reservation,
            side="BUY",
            before_order=lambda: self._declare_stance("BUY", kwargs),
            **kwargs,
        )

    async def execute_pre_reserved_sell(
        self,
        *args,
        intent: OrderIntent,
        reservation: Any,
        **kwargs,
    ):
        return await self._execute_pre_reserved_order(
            self._active_trader.async_sell_stock,
            *args,
            intent=intent,
            reservation=reservation,
            side="SELL",
            before_order=lambda: self._declare_stance("SELL", kwargs),
            **kwargs,
        )

    async def execute_pre_reserved_local_flat_sell(
        self,
        *,
        intent: OrderIntent,
        reservation: Any,
    ) -> dict[str, Any]:
        """Audit a committed SELL intent when broker holdings are already flat."""

        if self._intent_store is None:
            raise RuntimeError(
                "pre-reserved execution requires the originating IntentStore"
            )
        result = {
            "success": True,
            "local_flat": True,
            "quantity": 0,
        }
        broker = "LOCAL_RECONCILIATION"
        intent_store = self._intent_store

        def reconcile_local_flat() -> None:
            intent_store.claim_reservation(
                reservation,
                intent,
                expected_side="SELL",
            )
            try:
                intent_store.record_result(
                    intent,
                    status="SUBMITTED",
                    accepted=True,
                    broker=broker,
                    response=result,
                )
            except Exception as exc:
                raise _LocalFlatPersistenceError(exc) from exc

        reconciliation_task = asyncio.create_task(
            asyncio.to_thread(reconcile_local_flat)
        )
        try:
            await asyncio.shield(reconciliation_task)
        except asyncio.CancelledError as cancellation:
            while not reconciliation_task.done():
                try:
                    await asyncio.shield(reconciliation_task)
                except asyncio.CancelledError:
                    continue
                except Exception as reconciliation_error:
                    logger.critical(
                        "[ORDER_INTENT] cancelled local-flat reconciliation failed "
                        "id=%s error=%s",
                        intent.id,
                        type(reconciliation_error).__name__,
                    )
                    break
            raise cancellation
        except _LocalFlatPersistenceError as exc:
            logger.critical(
                "[ORDER_INTENT] local-flat persistence failed id=%s",
                intent.id,
            )
            raise OrderOutcomeUnknown(
                intent.id,
                broker_result=result,
                cause=exc.cause,
            ) from exc
        logger.info(
            "[ORDER_INTENT] SUBMITTED id=%s market=%s side=%s symbol=%s broker=%s",
            intent.id,
            intent.market,
            intent.side,
            intent.symbol,
            broker,
        )
        return self._with_intent_metadata(
            result,
            intent=intent,
            status="SUBMITTED",
            broker=broker,
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
            before_order=lambda: self._declare_stance_sync("BUY", kwargs),
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
            before_order=lambda: self._declare_stance_sync("SELL", kwargs),
            **kwargs,
        )

    def execute_pre_reserved_reserved_buy(
        self,
        *args,
        intent: OrderIntent,
        reservation: Any,
        **kwargs,
    ):
        return self._execute_pre_reserved_order_sync(
            self._active_trader.buy_reserved_order,
            *args,
            intent=intent,
            reservation=reservation,
            side="BUY",
            before_order=lambda: self._declare_stance_sync("BUY", kwargs),
            **kwargs,
        )

    def execute_pre_reserved_reserved_sell(
        self,
        *args,
        intent: OrderIntent,
        reservation: Any,
        **kwargs,
    ):
        return self._execute_pre_reserved_order_sync(
            self._active_trader.sell_reserved_order,
            *args,
            intent=intent,
            reservation=reservation,
            side="SELL",
            before_order=lambda: self._declare_stance_sync("SELL", kwargs),
            **kwargs,
        )

    # Explicit sync aliases keep call sites readable when the underlying KIS
    # operation is a synchronous reserved-order endpoint.
    execute_pre_reserved_buy_sync = execute_pre_reserved_reserved_buy
    execute_pre_reserved_sell_sync = execute_pre_reserved_reserved_sell


__all__ = ["ExecutionService", "OrderOutcomeUnknown"]
