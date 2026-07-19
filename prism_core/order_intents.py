"""Additive broker-order intent ledger for issue #412 Phase 3."""

from __future__ import annotations

import hashlib
import json
import re
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


_SENSITIVE_KEY_PARTS = (
    "authorization",
    "api_key",
    "apikey",
    "app_key",
    "appkey",
    "app_secret",
    "appsecret",
    "password",
    "passwd",
    "secret",
    "token",
)


def _redact_text(value: str) -> str:
    value = re.sub(r"(?i)bearer\s+[^\s,;]+", "Bearer [REDACTED]", value)
    return re.sub(
        r"(?i)(api[_-]?key|app[_-]?key|app[_-]?secret|token|password)"
        r"(\s*[:=]\s*)[^\s,;]+",
        r"\1\2[REDACTED]",
        value,
    )


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: (
                "[REDACTED]"
                if any(part in str(key).lower() for part in _SENSITIVE_KEY_PARTS)
                else _redact(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _json(value: Any) -> str:
    return json.dumps(
        _redact(value), ensure_ascii=False, sort_keys=True, default=str
    )


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
        if side == "BUY" and decision_id:
            identity = f"decision:{decision_id}"
        elif position_id:
            identity = f"position:{position_id}"
        else:
            identity = f"decision:{decision_id}"
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


class _ReservationCapability(dict[str, Any]):
    """Unforgeable-by-API proof that this store inserted one CREATED intent."""

    def __init__(
        self,
        value: dict[str, Any],
        *,
        owner: object,
        intent: OrderIntent,
        connection: sqlite3.Connection,
    ) -> None:
        super().__init__(value)
        self._owner = owner
        self._intent = intent
        self._intent_id = intent.id
        self._idempotency_key = intent.idempotency_key
        self._connection = connection
        self._consumed = False


class IntentStore:
    """SQLite intent store with cross-process idempotency reservation."""

    def __init__(self, db_path: str | Path, *, timeout: float = 30.0):
        self.db_path = str(db_path)
        self._resolved_db_path = str(Path(db_path).expanduser().resolve())
        self._capability_owner = object()
        self.timeout = timeout
        self.ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=self.timeout)
        conn.row_factory = sqlite3.Row
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

    def _validate_connection(self, connection: sqlite3.Connection) -> None:
        if not isinstance(connection, sqlite3.Connection):
            raise TypeError("reserve_in_transaction requires sqlite3.Connection")
        main_path = next(
            (
                str(row[2])
                for row in connection.execute("PRAGMA database_list")
                if str(row[1]) == "main"
            ),
            "",
        )
        if not main_path or str(Path(main_path).resolve()) != self._resolved_db_path:
            raise ValueError("connection does not target this IntentStore database")

    def _reserve_on(
        self,
        connection: sqlite3.Connection,
        intent: OrderIntent,
        *,
        issue_capability: bool,
    ) -> tuple[bool, dict[str, Any]]:
        now = _utc_now()
        try:
            connection.execute(
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
            row = connection.execute(
                "SELECT id, status, idempotency_key FROM order_intents "
                "WHERE idempotency_key = ?",
                (intent.idempotency_key,),
            ).fetchone()
            if row is None:
                raise
            columns = ("id", "status", "idempotency_key")
            return False, dict(zip(columns, row))
        value = {
            "id": intent.id,
            "status": "CREATED",
            "idempotency_key": intent.idempotency_key,
        }
        if issue_capability:
            value = _ReservationCapability(
                value,
                owner=self._capability_owner,
                intent=intent,
                connection=connection,
            )
        return True, value

    def reserve_in_transaction(
        self,
        connection: sqlite3.Connection,
        intent: OrderIntent,
    ) -> tuple[bool, dict[str, Any]]:
        """Reserve without beginning, committing, or rolling back caller work."""

        self._validate_connection(connection)
        if not connection.in_transaction:
            raise RuntimeError(
                "reserve_in_transaction requires an active caller-owned transaction"
            )
        return self._reserve_on(connection, intent, issue_capability=True)

    def reserve(self, intent: OrderIntent) -> tuple[bool, dict[str, Any]]:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            return self._reserve_on(conn, intent, issue_capability=False)

    def claim_reservation(
        self,
        reservation: Any,
        intent: OrderIntent,
        *,
        expected_side: str,
    ) -> None:
        """Consume an opaque committed reservation before any broker call."""

        if (
            not isinstance(reservation, _ReservationCapability)
            or reservation._owner is not self._capability_owner
            or reservation._intent != intent
            or reservation._intent_id != intent.id
            or reservation._idempotency_key != intent.idempotency_key
            or reservation._consumed
        ):
            raise TypeError("valid unused IntentStore reservation is required")
        if intent.side != expected_side:
            raise ValueError("reservation side does not match execution method")
        try:
            transaction_is_open = reservation._connection.in_transaction
        except sqlite3.ProgrammingError:
            # A caller may commit and close its short-lived transaction before
            # handing the capability to the execution service.  mark_submitting()
            # below is the authoritative committed-row check; a close-triggered
            # rollback leaves no CREATED row and therefore still fails closed.
            transaction_is_open = False
        if transaction_is_open:
            raise RuntimeError(
                "reservation transaction must commit before broker execution"
            )
        self.mark_submitting(intent.id)
        reservation._consumed = True

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
        broker: str = "KIS",
        response: Any,
        error: BaseException | None = None,
    ) -> None:
        now = _utc_now()
        payload = response if isinstance(response, dict) else {"result": response}
        if error is not None:
            safe_error = _redact_text(str(error))
            payload = {
                "error_type": type(error).__name__,
                "error_message": safe_error,
            }
        else:
            safe_error = None
        broker_order_id = payload.get("order_no") or payload.get("broker_order_id")
        raw_code = payload.get("rt_cd") or payload.get("code")
        raw_message = _redact_text(
            str(payload.get("message") or payload.get("msg1") or "")
        ) or None
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
                    safe_error,
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
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    intent.id,
                    broker,
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
