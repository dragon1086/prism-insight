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
from kakao_bot.adapters.kakao.rest_client import KakaoRestClient
from kakao_bot.adapters.persistence.sqlite import (
    SQLiteGatewayStateStore,
    SQLiteKakaoRepository,
)
from kakao_bot.application.gateway_inbound_service import GatewayInboundService
from kakao_bot.ports.gateway_state import GatewayStatePort

logger = logging.getLogger(__name__)

DEFAULT_LOCK_PATH = Path("/tmp/prism-kakao-gateway.lock")

REGISTERED_COMMAND_UTTERANCES = [
    {
        "buttonName": "리포트",
        "description": (
            "한 칸 띄우고 국내 종목명 또는 미국 티커를 입력하세요. "
            "예: 리포트 삼성전자 / 리포트 AAPL"
        ),
    },
    {
        "buttonName": "평가",
        "description": (
            "한 칸 띄우고 종목명·평단가·보유기간(월)을 입력하세요. "
            "예: 평가 삼성전자 70000 6"
        ),
    },
    {"buttonName": "도움말", "description": None},
]


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


async def sync_command_menu(
    config: GatewayRuntimeConfig,
    *,
    client: KakaoRestClient | None = None,
) -> bool:
    """Replace Kakao's persistent mention menu with supported commands."""

    resolved_client = client or KakaoRestClient(config.token)
    result = await resolved_client.update_commands(REGISTERED_COMMAND_UTTERANCES)
    if not result.success:
        logger.warning(
            "Kakao command menu sync failed: status=%s code=%s",
            result.status_code,
            result.error_code,
        )
        return False
    logger.info(
        "Kakao command menu synced (%d commands)",
        len(REGISTERED_COMMAND_UTTERANCES),
    )
    return True


def _is_enabled(name: str, *, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _build_message_handler(
    config: GatewayRuntimeConfig,
    repository: SQLiteKakaoRepository,
):
    """Wire command handling, or return None to run receive-only.

    Imports are local so that a Gateway running with commands disabled does
    not pull in the Prism report stack at all.
    """

    if not _is_enabled("KAKAO_REPORT_ENABLED"):
        logger.info("Kakao command handling disabled (KAKAO_REPORT_ENABLED)")
        return None

    from kakao_bot.adapters.kakao.message_command_handler import (
        MessageCommandHandler,
    )
    from kakao_bot.adapters.kakao.rest_client import KakaoRestClient
    from kakao_bot.adapters.prism.ticker_adapter import PrismTickerResolver
    from kakao_bot.application.command_service import CommandService

    resolver = PrismTickerResolver()
    if not resolver.is_loaded:
        logger.error(
            "Stock map is empty; ticker lookups will fail. "
            "Update runtime/stock_map.json or set PRISM_STOCK_MAP_PATH."
        )

    return MessageCommandHandler(
        CommandService(repository, resolver),
        KakaoRestClient(config.token),
    )


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
            GatewayInboundService(
                repository,
                # Off by default: with real users an invitation should not be
                # enough to start broadcasting into someone's room. Turned on
                # for the review period, where an operator cannot approve ten
                # reviewers' rooms by hand fast enough to matter.
                auto_approve=_is_enabled(
                    "KAKAO_AUTO_APPROVE_ROOMS", default=False
                ),
                greet_on_join=_is_enabled("KAKAO_GREET_ON_JOIN"),
            ),
            message_handler=_build_message_handler(config, repository),
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
    await sync_command_menu(config)
    return await run_gateway(config)


def main() -> int:
    logging.basicConfig(
        level=os.getenv("KAKAO_GATEWAY_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
