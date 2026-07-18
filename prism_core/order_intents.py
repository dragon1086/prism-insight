"""Additive broker-order intent ledger for issue #412 Phase 3."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_INTENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS order_intents (
    id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    market TEXT NOT NULL,
    account_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    order_style TEXT NOT NULL,
    quantity INTEGER,
    cash_amount TEXT,
    limit_price TEXT,
    reason TEXT,
    source TEXT NOT NULL,
    source_decision_id TEXT,
    source_position_id TEXT,
    execution_mode TEXT NOT NULL,
    status TEXT NOT NULL,
    error_type TEXT,
    error_message TEXT,
    raw_request_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    submitted_at TEXT
)
"""

_BROKER_ORDER_SCHEMA = """
CREATE TABLE IF NOT EXISTS broker_orders (
    id TEXT PRIMARY KEY,
    intent_id TEXT NOT NULL,
    broker TEXT NOT NULL,
    broker_order_id TEXT,
    accepted INTEGER NOT NULL,
    status TEXT NOT NULL,
    submitted_quantity INTEGER,
    submitted_price TEXT,
    raw_code TEXT,
    raw_message TEXT,
    raw_response_json TEXT NOT NULL,
    submitted_at TEXT NOT NULL,
    FOREIGN KEY(intent_id) REFERENCES order_intents(id)
)
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any) -> str | None:
    return None if value is None else str(value)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


@dataclass(frozen=True)
class OrderIntent:
    id: str
    idempotency_key: str
    market: str
    account_id: str
    symbol: str
    side: str
    order_style: str
    source: str
    source_decision_id: str | None
    source_position_id: str | None
    execution_mode: str
    quantity: int | None
    cash_amount: str | None
    limit_price: str | None
    reason: str | None
    created_at: str

    @classmethod
    def create(
        cls,
        *,
        market: str,
        account_id: str,
        symbol: str,
        side: str,
        order_style: str,
        source: str,
        source_decision_id: Any = None,
        source_position_id: Any = None,
        execution_mode: str = "live",
        quantity: int | None = None,
        cash_amount: Any = None,
        limit_price: Any = None,
        reason: str | None = None,
    ) -> "OrderIntent":
        market = str(market).upper()
        side = str(side).upper()
        if market not in {"KR", "US"}:
            raise ValueError(f"unsupported market: {market}")
        if side not in {"BUY", "SELL"}:
            raise ValueError(f"unsupported order side: {side}")
        account_id = str(account_id or "default")
        symbol = str(symbol).upper()
        decision_id = _text(source_decision_id)
        position_id = _text(source_position_id)
        if not decision_id and not position_id:
            raise ValueError(
                "OrderIntent requires source_decision_id or source_position_id"
            )
        identity = (
            f"position:{position_id}" if position_id else f"decision:{decision_id}"
        )
        key_source = "|".join(
            ("v1", market, account_id, symbol, side, identity)
        )
        return cls(
            id=str(uuid.uuid4()),
            idempotency_key=hashlib.sha256(key_source.encode()).hexdigest(),
            market=market,
            account_id=account_id,
            symbol=symbol,
            side=side,
            order_style=str(order_style).lower(),
            source=str(source),
            source_decision_id=decision_id,
            source_position_id=position_id,
            execution_mode=str(execution_mode).lower(),
            quantity=quantity,
            cash_amount=_text(cash_amount),
            limit_price=_text(limit_price),
            reason=reason,
            created_at=_utc_now(),
        )

    def request_payload(self) -> dict[str, Any]:
        return {
            "market": self.market,
            "account_id": self.account_id,
            "symbol": self.symbol,
            "side": self.side,
            "order_style": self.order_style,
            "quantity": self.quantity,
            "cash_amount": self.cash_amount,
            "limit_price": self.limit_price,
            "reason": self.reason,
            "source": self.source,
            "source_decision_id": self.source_decision_id,
            "source_position_id": self.source_position_id,
            "execution_mode": self.execution_mode,
        }


class IntentStore:
    """SQLite intent store with cross-process idempotency reservation."""

    def __init__(self, db_path: str | Path, *, timeout: float = 30.0):
        self.db_path = str(db_path)
        self.timeout = timeout
        self.ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=self.timeout)
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout = {int(self.timeout * 1000)}")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(_INTENT_SCHEMA)
            conn.execute(_BROKER_ORDER_SCHEMA)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_order_intents_status "
                "ON order_intents(status, updated_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_broker_orders_intent "
                "ON broker_orders(intent_id, submitted_at)"
            )

    def reserve(self, intent: OrderIntent) -> tuple[bool, dict[str, Any]]:
        now = _utc_now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    """
                    INSERT INTO order_intents (
                        id, idempotency_key, market, account_id, symbol, side,
                        order_style, quantity, cash_amount, limit_price, reason,
                        source, source_decision_id, source_position_id,
                        execution_mode, status, raw_request_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                              'CREATED', ?, ?, ?)
                    """,
                    (
                        intent.id,
                        intent.idempotency_key,
                        intent.market,
                        intent.account_id,
                        intent.symbol,
                        intent.side,
                        intent.order_style,
                        intent.quantity,
                        intent.cash_amount,
                        intent.limit_price,
                        intent.reason,
                        intent.source,
                        intent.source_decision_id,
                        intent.source_position_id,
                        intent.execution_mode,
                        _json(intent.request_payload()),
                        intent.created_at,
                        now,
                    ),
                )
            except sqlite3.IntegrityError:
                row = conn.execute(
                    "SELECT id, status, idempotency_key FROM order_intents "
                    "WHERE idempotency_key = ?",
                    (intent.idempotency_key,),
                ).fetchone()
                if row is None:
                    raise
                return False, dict(row)
        return True, {
            "id": intent.id,
            "status": "CREATED",
            "idempotency_key": intent.idempotency_key,
        }

    def mark_submitting(self, intent_id: str) -> None:
        with self._connect() as conn:
            changed = conn.execute(
                "UPDATE order_intents SET status='SUBMITTING', updated_at=? "
                "WHERE id=? AND status='CREATED'",
                (_utc_now(), intent_id),
            ).rowcount
            if changed != 1:
                raise RuntimeError(f"intent {intent_id} is not in CREATED state")

    def record_result(
        self,
        intent: OrderIntent,
        *,
        status: str,
        accepted: bool,
        response: Any,
        error: BaseException | None = None,
    ) -> None:
        now = _utc_now()
        payload = response if isinstance(response, dict) else {"result": response}
        if error is not None:
            payload = {
                "error_type": type(error).__name__,
                "error_message": str(error),
            }
        broker_order_id = payload.get("order_no") or payload.get("broker_order_id")
        raw_code = payload.get("rt_cd") or payload.get("code")
        raw_message = payload.get("message") or payload.get("msg1")
        quantity = payload.get("quantity") or payload.get("submitted_quantity")
        price = payload.get("price") or payload.get("submitted_price")

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            changed = conn.execute(
                """
                UPDATE order_intents
                SET status=?, error_type=?, error_message=?, updated_at=?,
                    submitted_at=CASE WHEN ?='SUBMITTED' THEN ? ELSE submitted_at END
                WHERE id=? AND status='SUBMITTING'
                """,
                (
                    status,
                    type(error).__name__ if error else None,
                    str(error) if error else None,
                    now,
                    status,
                    now,
                    intent.id,
                ),
            ).rowcount
            if changed != 1:
                raise RuntimeError(
                    f"intent {intent.id} cannot transition SUBMITTING -> {status}"
                )
            conn.execute(
                """
                INSERT INTO broker_orders (
                    id, intent_id, broker, broker_order_id, accepted, status,
                    submitted_quantity, submitted_price, raw_code, raw_message,
                    raw_response_json, submitted_at
                ) VALUES (?, ?, 'KIS', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    intent.id,
                    _text(broker_order_id),
                    int(accepted),
                    status,
                    quantity,
                    _text(price),
                    _text(raw_code),
                    _text(raw_message),
                    _json(payload),
                    now,
                ),
            )

    @staticmethod
    def blocked_result(existing: dict[str, Any]) -> dict[str, Any]:
        return {
            "success": False,
            "accepted": False,
            "blocked": True,
            "duplicate_intent": True,
            "intent_id": existing["id"],
            "intent_status": existing["status"],
            "message": "duplicate order intent blocked before broker call",
        }


__all__ = ["IntentStore", "OrderIntent"]
