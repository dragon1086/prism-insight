"""Bounded, transport-agnostic replay for durable exit effects."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from prism_core.exit_effects import (
    EXIT_EFFECT_TYPES,
    REMOTE_ID_EFFECT_TYPES,
    ExitEffectStore,
)


EffectHandler = Callable[[dict[str, Any]], Awaitable[str | bool | None]]


@dataclass(frozen=True)
class ExitEffectDeliveryOutcome:
    """Result of attempting one exact durable effect."""

    status: str
    remote_id: str | None = None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _delivery_result(
    effect_type: str, result: str | bool | None
) -> tuple[bool, str | None]:
    if effect_type in REMOTE_ID_EFFECT_TYPES:
        if isinstance(result, str) and result.strip():
            return True, result.strip()
        return False, None
    if result is True:
        return True, None
    if isinstance(result, str) and result.strip():
        return True, result.strip()
    return False, None


def _retry_delay(
    attempt_count: int, *, base_delay_seconds: int, max_delay_seconds: int
) -> int:
    delay = base_delay_seconds
    for _ in range(max(0, int(attempt_count) - 1)):
        delay = min(max_delay_seconds, delay * 2)
        if delay == max_delay_seconds:
            break
    return delay


def _open_replay_database(db_path: str | Path) -> sqlite3.Connection:
    resolved_db_path = Path(db_path).expanduser().resolve()
    if not resolved_db_path.is_file():
        raise FileNotFoundError("exit effect replay database does not exist")
    connection = sqlite3.connect(resolved_db_path, timeout=30)
    connection.row_factory = sqlite3.Row
    table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='exit_effect_outbox'"
    ).fetchone()
    if table is None:
        connection.close()
        raise RuntimeError("exit effect outbox schema is missing")
    return connection


async def _process_claimed_effect(
    connection: sqlite3.Connection,
    store: ExitEffectStore,
    effect: dict[str, Any],
    *,
    handler: EffectHandler,
    owner: str,
    handler_timeout_seconds: float,
    max_attempts: int,
    base_delay_seconds: int,
    max_delay_seconds: int,
    now: Callable[[], datetime],
) -> ExitEffectDeliveryOutcome:
    if connection.in_transaction:
        raise RuntimeError("replay handler cannot run inside a DB transaction")
    effect_type = str(effect["effect_type"])
    error_type = None
    try:
        result = await asyncio.wait_for(
            handler(effect["payload"]), timeout=handler_timeout_seconds
        )
        delivered, remote_id = _delivery_result(effect_type, result)
        if not delivered:
            error_type = "DeliveryNotConfirmed"
    except asyncio.CancelledError:
        try:
            current = now()
            delay = _retry_delay(
                effect["attempt_count"],
                base_delay_seconds=base_delay_seconds,
                max_delay_seconds=max_delay_seconds,
            )
            connection.execute("BEGIN IMMEDIATE")
            store.record_failure(
                effect_id=effect["id"],
                owner=owner,
                error_type="CancelledError",
                next_attempt_at=current + timedelta(seconds=delay),
                max_attempts=max_attempts,
                now=current,
            )
            connection.commit()
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
        raise
    except Exception as error:
        delivered = False
        remote_id = None
        error_type = type(error).__name__

    current = now()
    connection.execute("BEGIN IMMEDIATE")
    try:
        if delivered:
            store.mark_delivered(
                effect_id=effect["id"],
                owner=owner,
                remote_id=remote_id,
                now=current,
            )
            connection.commit()
            return ExitEffectDeliveryOutcome("delivered", remote_id)

        delay = _retry_delay(
            effect["attempt_count"],
            base_delay_seconds=base_delay_seconds,
            max_delay_seconds=max_delay_seconds,
        )
        status = store.record_failure(
            effect_id=effect["id"],
            owner=owner,
            error_type=error_type or "DeliveryNotConfirmed",
            next_attempt_at=current + timedelta(seconds=delay),
            max_attempts=max_attempts,
            now=current,
        )
        connection.commit()
        return ExitEffectDeliveryOutcome("dead" if status == "DEAD" else "rescheduled")
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise


async def deliver_exit_effect_once(
    db_path: str | Path,
    *,
    effect_id: str,
    effect_type: str,
    handler: EffectHandler,
    owner: str,
    lease_seconds: int = 60,
    handler_timeout_seconds: float = 30,
    max_attempts: int = 5,
    base_delay_seconds: int = 30,
    max_delay_seconds: int = 3600,
    now: Callable[[], datetime] = _utc_now,
) -> ExitEffectDeliveryOutcome:
    """Claim and deliver one exact effect, never unrelated ready work."""

    if effect_type not in EXIT_EFFECT_TYPES:
        raise ValueError("unsupported exit effect type")
    if not callable(handler):
        raise TypeError("exit effect handler must be callable")
    if handler_timeout_seconds <= 0 or handler_timeout_seconds >= lease_seconds:
        raise ValueError("handler timeout must be positive and shorter than the lease")
    if base_delay_seconds < 1 or max_delay_seconds < base_delay_seconds:
        raise ValueError("invalid replay delay bounds")
    if not isinstance(max_attempts, int) or max_attempts < 1:
        raise ValueError("max_attempts must be a positive integer")

    connection = _open_replay_database(db_path)
    store = ExitEffectStore(connection)
    try:
        existing = store.get_effect(effect_id)
        if existing is None:
            raise ValueError("exit effect does not exist")
        if existing["effect_type"] != effect_type:
            raise ValueError("exit effect type does not match requested handler")
        if existing["status"] == "DELIVERED":
            return ExitEffectDeliveryOutcome(
                "already_delivered", existing.get("remote_id")
            )

        connection.execute("BEGIN IMMEDIATE")
        claimed = store.claim_effect(
            effect_id=effect_id,
            owner=owner,
            now=now(),
            lease_seconds=lease_seconds,
        )
        connection.commit()
        if claimed is None:
            latest = store.get_effect(effect_id)
            if latest is not None and latest["status"] == "DELIVERED":
                return ExitEffectDeliveryOutcome(
                    "already_delivered", latest.get("remote_id")
                )
            return ExitEffectDeliveryOutcome("not_ready")
        return await _process_claimed_effect(
            connection,
            store,
            claimed,
            handler=handler,
            owner=owner,
            handler_timeout_seconds=handler_timeout_seconds,
            max_attempts=max_attempts,
            base_delay_seconds=base_delay_seconds,
            max_delay_seconds=max_delay_seconds,
            now=now,
        )
    finally:
        connection.close()


async def run_exit_effect_replay(
    db_path: str | Path,
    *,
    handlers: Mapping[str, EffectHandler],
    owner: str,
    limit: int = 10,
    lease_seconds: int = 60,
    handler_timeout_seconds: float = 30,
    max_attempts: int = 5,
    base_delay_seconds: int = 30,
    max_delay_seconds: int = 3600,
    now: Callable[[], datetime] = _utc_now,
) -> dict[str, int]:
    """Claim and process at most ``limit`` effects without holding I/O locks."""

    effect_types = tuple(
        effect_type for effect_type in EXIT_EFFECT_TYPES if effect_type in handlers
    )
    unsupported = set(handlers) - set(EXIT_EFFECT_TYPES)
    if unsupported:
        raise ValueError("unsupported exit effect handler")
    if any(not callable(handler) for handler in handlers.values()):
        raise TypeError("exit effect handlers must be callable")
    summary = {"claimed": 0, "delivered": 0, "rescheduled": 0, "dead": 0}
    if not effect_types:
        return summary
    if handler_timeout_seconds <= 0 or handler_timeout_seconds >= lease_seconds:
        raise ValueError("handler timeout must be positive and shorter than the lease")
    if base_delay_seconds < 1 or max_delay_seconds < base_delay_seconds:
        raise ValueError("invalid replay delay bounds")
    if not isinstance(max_attempts, int) or max_attempts < 1:
        raise ValueError("max_attempts must be a positive integer")

    connection = _open_replay_database(db_path)
    store = ExitEffectStore(connection)
    try:
        connection.execute("BEGIN IMMEDIATE")
        claimed = store.claim_ready_effects(
            owner=owner,
            limit=limit,
            effect_types=effect_types,
            now=now(),
            lease_seconds=lease_seconds,
        )
        connection.commit()
        summary["claimed"] = len(claimed)

        for effect in claimed:
            effect_type = str(effect["effect_type"])
            handler = handlers[effect_type]
            outcome = await _process_claimed_effect(
                connection,
                store,
                effect,
                handler=handler,
                owner=owner,
                handler_timeout_seconds=handler_timeout_seconds,
                max_attempts=max_attempts,
                base_delay_seconds=base_delay_seconds,
                max_delay_seconds=max_delay_seconds,
                now=now,
            )
            summary[outcome.status] += 1
    finally:
        connection.close()
    return summary
