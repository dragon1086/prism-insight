"""Runtime entry point for the single Kakao Gateway receive process."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

import aiohttp

from kakao_bot.adapters.kakao.gateway import (
    AlreadyRunningError,
    FatalGatewayError,
    GatewayClient,
    SingleInstanceFileLock,
)
from kakao_bot.adapters.kakao.gateway_inbound_handler import (
    GatewayDispatchHandler,
)
from kakao_bot.adapters.kakao.gateway_protocol import GatewayDispatch
from kakao_bot.adapters.persistence.sqlite import (
    SQLiteGatewayStateStore,
    SQLiteKakaoRepository,
)
from kakao_bot.application.gateway_inbound_service import GatewayInboundService
from kakao_bot.ports.gateway_state import GatewayStatePort

logger = logging.getLogger(__name__)

DEFAULT_LOCK_PATH = Path("/tmp/prism-kakao-gateway.lock")


class GatewayConfigurationError(ValueError):
    """Raised when required runtime configuration is missing."""


@dataclass(frozen=True)
class GatewayRuntimeConfig:
    token: str = field(repr=False)
    lock_path: Path = DEFAULT_LOCK_PATH
    database_path: Path = Path("kakao_bot.sqlite")


def load_config(
    environ: Mapping[str, str] | None = None,
) -> GatewayRuntimeConfig:
    values = os.environ if environ is None else environ
    token = values.get("KAKAO_BOT_TOKEN", "").strip()
    if not token:
        raise GatewayConfigurationError("KAKAO_BOT_TOKEN is required")
    lock_path = Path(
        values.get("KAKAO_GATEWAY_LOCK_PATH", str(DEFAULT_LOCK_PATH))
    ).expanduser()
    database_path = Path(
        values.get("KAKAO_BOT_DATABASE_PATH", "kakao_bot.sqlite")
    ).expanduser()
    return GatewayRuntimeConfig(
        token=token,
        lock_path=lock_path,
        database_path=database_path,
    )


EventHandler = Callable[[GatewayDispatch], Awaitable[None]]


async def run_gateway(
    config: GatewayRuntimeConfig,
    *,
    state_store: GatewayStatePort | None = None,
    event_handler: EventHandler | None = None,
) -> int:
    """Run the Gateway with process lock and graceful signal handling."""

    try:
        lock = SingleInstanceFileLock(config.lock_path)
        lock.acquire()
    except AlreadyRunningError as exc:
        logger.error("%s", exc)
        return 1

    repository: SQLiteKakaoRepository | None = None
    try:
        if state_store is None or event_handler is None:
            repository = SQLiteKakaoRepository(config.database_path)
        resolved_state_store = state_store or SQLiteGatewayStateStore(repository)
        resolved_handler = event_handler or GatewayDispatchHandler(
            GatewayInboundService(repository)
        )
        async with aiohttp.ClientSession() as session:
            loop = asyncio.get_running_loop()
            client = GatewayClient(
                token=config.token,
                session=session,
                state_store=resolved_state_store,
                event_handler=resolved_handler,
                clock=loop.time,
                sleep=asyncio.sleep,
            )
            installed_signals: list[signal.Signals] = []
            for sig in (signal.SIGTERM, signal.SIGINT):
                try:
                    loop.add_signal_handler(
                        sig,
                        lambda: asyncio.create_task(client.stop()),
                    )
                    installed_signals.append(sig)
                except NotImplementedError:
                    pass
            try:
                await client.run()
            except FatalGatewayError as exc:
                logger.critical("%s", exc)
                return 2
            finally:
                for sig in installed_signals:
                    loop.remove_signal_handler(sig)
    finally:
        if repository is not None:
            repository.close()
        lock.release()
    return 0


async def async_main() -> int:
    try:
        config = load_config()
    except GatewayConfigurationError as exc:
        logger.error("%s", exc)
        return 2
    return await run_gateway(config)


def main() -> int:
    logging.basicConfig(
        level=os.getenv("KAKAO_GATEWAY_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
