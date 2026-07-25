"""Runtime entry point for durable Kakao outbox delivery."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from kakao_bot.adapters.kakao.campaign_renderer import (
    render_campaign_delivery,
)
from kakao_bot.adapters.kakao.rest_client import DEFAULT_BASE_URL, KakaoRestClient
from kakao_bot.adapters.persistence.sqlite import SQLiteKakaoRepository
from kakao_bot.application.delivery_service import (
    DeliveryRunResult,
    OutboxDeliveryService,
)
from kakao_bot.ports.repositories import KakaoMessageSender

logger = logging.getLogger(__name__)


class SenderConfigurationError(ValueError):
    """Raised when sender runtime configuration is invalid."""


@dataclass(frozen=True)
class SenderRuntimeConfig:
    token: str = field(repr=False)
    database_path: Path = Path("kakao_bot.sqlite")
    base_url: str = DEFAULT_BASE_URL
    poll_seconds: float = 2.0
    batch_size: int = 20
    lease_seconds: int = 30
    lease_owner: str = field(
        default_factory=lambda: f"{socket.gethostname()}:{os.getpid()}"
    )


def load_config(
    environ: Mapping[str, str] | None = None,
) -> SenderRuntimeConfig:
    values = os.environ if environ is None else environ
    token = values.get("KAKAO_BOT_TOKEN", "").strip()
    if not token:
        raise SenderConfigurationError("KAKAO_BOT_TOKEN is required")

    database_path = Path(
        values.get("KAKAO_BOT_DATABASE_PATH", "kakao_bot.sqlite")
    ).expanduser()
    base_url = values.get("KAKAO_REST_BASE_URL", DEFAULT_BASE_URL).strip()
    poll_seconds = _positive_float(
        values.get("KAKAO_SENDER_POLL_SECONDS", "2"),
        "KAKAO_SENDER_POLL_SECONDS",
    )
    batch_size = _positive_int(
        values.get("KAKAO_SENDER_BATCH_SIZE", "20"),
        "KAKAO_SENDER_BATCH_SIZE",
    )
    lease_seconds = _positive_int(
        values.get("KAKAO_SENDER_LEASE_SECONDS", "30"),
        "KAKAO_SENDER_LEASE_SECONDS",
    )
    return SenderRuntimeConfig(
        token=token,
        database_path=database_path,
        base_url=base_url,
        poll_seconds=poll_seconds,
        batch_size=batch_size,
        lease_seconds=lease_seconds,
    )


async def run_sender_once(
    config: SenderRuntimeConfig,
    *,
    sender: KakaoMessageSender | None = None,
    now: datetime | None = None,
) -> DeliveryRunResult:
    with SQLiteKakaoRepository(config.database_path) as repository:
        service = _delivery_service(
            config,
            repository,
            sender=sender,
        )
        return await service.run_once(now=now)


async def run_sender(
    config: SenderRuntimeConfig,
    *,
    stop_event: asyncio.Event | None = None,
) -> int:
    stop = stop_event or asyncio.Event()
    with SQLiteKakaoRepository(config.database_path) as repository:
        service = _delivery_service(config, repository)
        while not stop.is_set():
            try:
                result = await service.run_once()
                if result.claimed:
                    logger.info(
                        "Kakao sender batch "
                        "(claimed=%d, sent=%d, retry=%d, dead=%d, stale=%d)",
                        result.claimed,
                        result.sent,
                        result.retry_scheduled,
                        result.dead,
                        result.stale_lease,
                    )
            except Exception:
                logger.exception("Kakao sender cycle failed")

            try:
                await asyncio.wait_for(
                    stop.wait(),
                    timeout=config.poll_seconds,
                )
            except TimeoutError:
                pass
    return 0


def _delivery_service(
    config: SenderRuntimeConfig,
    repository: SQLiteKakaoRepository,
    *,
    sender: KakaoMessageSender | None = None,
) -> OutboxDeliveryService:
    resolved_sender = sender or KakaoRestClient(
        config.token,
        base_url=config.base_url,
    )
    return OutboxDeliveryService(
        repository,
        resolved_sender,
        render_campaign_delivery,
        lease_owner=config.lease_owner,
        lease_seconds=config.lease_seconds,
        batch_size=config.batch_size,
    )


async def async_main() -> int:
    try:
        config = load_config()
    except SenderConfigurationError as exc:
        logger.error("%s", exc)
        return 2

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    installed_signals: list[signal.Signals] = []
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
            installed_signals.append(sig)
        except NotImplementedError:
            pass
    try:
        return await run_sender(config, stop_event=stop)
    finally:
        for sig in installed_signals:
            loop.remove_signal_handler(sig)


def main() -> int:
    logging.basicConfig(
        level=os.getenv("KAKAO_SENDER_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    return asyncio.run(async_main())


def _positive_float(raw: str, name: str) -> float:
    try:
        value = float(raw)
    except ValueError as exc:
        raise SenderConfigurationError(f"{name} must be numeric") from exc
    if value <= 0:
        raise SenderConfigurationError(f"{name} must be positive")
    return value


def _positive_int(raw: str, name: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise SenderConfigurationError(f"{name} must be an integer") from exc
    if value <= 0:
        raise SenderConfigurationError(f"{name} must be positive")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
