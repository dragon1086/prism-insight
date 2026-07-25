"""Runtime entry point for the local durable campaign queue."""

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

from kakao_bot.adapters.local_campaign_consumer import (
    CampaignConsumerRunResult,
    LocalBatchCampaignConsumer,
)
from kakao_bot.adapters.persistence.sqlite import SQLiteKakaoRepository
from messaging.local_campaign_queue import SQLiteBatchCampaignQueue

logger = logging.getLogger(__name__)


class ConsumerConfigurationError(ValueError):
    """Raised when local campaign consumer configuration is invalid."""


@dataclass(frozen=True)
class ConsumerRuntimeConfig:
    queue_path: Path = Path("prism_campaign_queue.sqlite")
    database_path: Path = Path("kakao_bot.sqlite")
    poll_seconds: float = 2.0
    batch_size: int = 20
    lease_seconds: int = 30
    lease_owner: str = field(
        default_factory=lambda: f"{socket.gethostname()}:{os.getpid()}"
    )


def load_config(
    environ: Mapping[str, str] | None = None,
) -> ConsumerRuntimeConfig:
    values = os.environ if environ is None else environ
    return ConsumerRuntimeConfig(
        queue_path=Path(
            values.get(
                "PRISM_CAMPAIGN_QUEUE_PATH",
                "prism_campaign_queue.sqlite",
            )
        ).expanduser(),
        database_path=Path(
            values.get("KAKAO_BOT_DATABASE_PATH", "kakao_bot.sqlite")
        ).expanduser(),
        poll_seconds=_positive_float(
            values.get("KAKAO_CONSUMER_POLL_SECONDS", "2"),
            "KAKAO_CONSUMER_POLL_SECONDS",
        ),
        batch_size=_positive_int(
            values.get("KAKAO_CONSUMER_BATCH_SIZE", "20"),
            "KAKAO_CONSUMER_BATCH_SIZE",
        ),
        lease_seconds=_positive_int(
            values.get("KAKAO_CONSUMER_LEASE_SECONDS", "30"),
            "KAKAO_CONSUMER_LEASE_SECONDS",
        ),
    )


def run_consumer_once(
    config: ConsumerRuntimeConfig,
    *,
    now: datetime | None = None,
) -> CampaignConsumerRunResult:
    with SQLiteBatchCampaignQueue(config.queue_path) as queue:
        consumer = _consumer(config, queue)
        return consumer.run_once(now=now)


async def run_consumer(
    config: ConsumerRuntimeConfig,
    *,
    stop_event: asyncio.Event | None = None,
) -> int:
    stop = stop_event or asyncio.Event()
    with SQLiteBatchCampaignQueue(config.queue_path) as queue:
        consumer = _consumer(config, queue)
        while not stop.is_set():
            try:
                result = consumer.run_once()
                if result.claimed:
                    logger.info(
                        "Local campaign consume batch "
                        "(claimed=%d, consumed=%d, retry=%d, dead=%d)",
                        result.claimed,
                        result.consumed,
                        result.retry_scheduled,
                        result.dead,
                    )
            except Exception:
                logger.exception("Local campaign consumer cycle failed")

            try:
                await asyncio.wait_for(
                    stop.wait(),
                    timeout=config.poll_seconds,
                )
            except TimeoutError:
                pass
    return 0


def _consumer(
    config: ConsumerRuntimeConfig,
    queue: SQLiteBatchCampaignQueue,
) -> LocalBatchCampaignConsumer:
    return LocalBatchCampaignConsumer(
        queue,
        lambda: SQLiteKakaoRepository(config.database_path),
        lease_owner=config.lease_owner,
        lease_seconds=config.lease_seconds,
        batch_size=config.batch_size,
    )


async def async_main() -> int:
    try:
        config = load_config()
    except ConsumerConfigurationError as exc:
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
        return await run_consumer(config, stop_event=stop)
    finally:
        for sig in installed_signals:
            loop.remove_signal_handler(sig)


def main() -> int:
    logging.basicConfig(
        level=os.getenv("KAKAO_CONSUMER_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    return asyncio.run(async_main())


def _positive_float(raw: str, name: str) -> float:
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConsumerConfigurationError(f"{name} must be numeric") from exc
    if value <= 0:
        raise ConsumerConfigurationError(f"{name} must be positive")
    return value


def _positive_int(raw: str, name: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConsumerConfigurationError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ConsumerConfigurationError(f"{name} must be positive")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
