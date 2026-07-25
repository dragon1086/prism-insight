"""aiohttp Kakao Gateway client and process-level safety adapters."""

from __future__ import annotations

import asyncio
import fcntl
import json
import logging
import math
import os
import random
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any

from aiohttp import WSMsgType

from kakao_bot.adapters.kakao.gateway_protocol import (
    CloseDisposition,
    DispatchReceived,
    GatewayDispatch,
    GatewayProtocol,
    GatewayProtocolError,
    HeartbeatConfigured,
    ReadyReceived,
    SendCommand,
    classify_close,
)
from kakao_bot.ports.gateway_state import GatewayState, GatewayStatePort

DEFAULT_GATEWAY_URL = "wss://bot-gateway.kakao.com/gateway"
_SENSITIVE_LOG_KEYS = frozenset(
    {
        "authorization",
        "callback_token",
        "callbacktoken",
        "token",
    }
)

logger = logging.getLogger(__name__)


class GatewayConnectionError(ConnectionError):
    """Raised when the websocket cannot continue safely."""


class FatalGatewayError(RuntimeError):
    """Base error for close codes that must not be retried."""


class GatewayAuthenticationError(FatalGatewayError):
    """Kakao rejected the bot authentication."""


class GatewayPayloadRejectedError(FatalGatewayError):
    """Kakao rejected a client protocol payload."""


class DuplicateGatewayConnectionError(FatalGatewayError):
    """Another process identified with the same bot token."""


class AlreadyRunningError(RuntimeError):
    """The local singleton lock is already owned."""


class InMemoryGatewayState:
    """Small state adapter intended for tests and local smoke processes."""

    def __init__(self, initial: GatewayState | None = None) -> None:
        self._state = initial or GatewayState()

    async def load(self) -> GatewayState:
        return self._state

    async def save(self, state: GatewayState) -> None:
        self._state = state

    async def clear(self) -> None:
        self._state = GatewayState()


class SingleInstanceFileLock:
    """Non-blocking Unix ``flock`` guard for the Gateway process."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self._fd: int | None = None

    def __enter__(self) -> SingleInstanceFileLock:
        self.acquire()
        return self

    def __exit__(self, *_: object) -> None:
        self.release()

    def acquire(self) -> None:
        if self._fd is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(fd)
            raise AlreadyRunningError(
                f"Kakao Gateway is already running (lock: {self.path})"
            ) from exc
        try:
            os.ftruncate(fd, 0)
            os.write(fd, str(os.getpid()).encode("ascii"))
        except BaseException:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
            raise
        self._fd = fd

    def release(self) -> None:
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


class ExponentialBackoff:
    """Bounded exponential delay with symmetric jitter."""

    def __init__(
        self,
        *,
        base_seconds: float = 1.0,
        maximum_seconds: float = 30.0,
        jitter_ratio: float = 0.2,
        random_value: Callable[[], float] = random.random,
    ) -> None:
        if base_seconds <= 0:
            raise ValueError("base_seconds must be positive")
        if maximum_seconds < base_seconds:
            raise ValueError("maximum_seconds must be >= base_seconds")
        if not 0 <= jitter_ratio <= 1:
            raise ValueError("jitter_ratio must be between 0 and 1")
        self._base = base_seconds
        self._maximum = maximum_seconds
        self._jitter_ratio = jitter_ratio
        self._random_value = random_value
        self._saturation_attempt = math.ceil(math.log2(self._maximum / self._base))

    def delay(self, attempt: int) -> float:
        if attempt < 0:
            raise ValueError("attempt must not be negative")
        if attempt >= self._saturation_attempt:
            unjittered = self._maximum
        else:
            unjittered = min(self._maximum, self._base * (2**attempt))
        unit = min(1.0, max(0.0, self._random_value()))
        factor = 1 + self._jitter_ratio * ((unit * 2) - 1)
        return min(self._maximum, max(0.0, unjittered * factor))


def redact_sensitive(value: object) -> object:
    """Return a recursively log-safe representation of structured data."""

    if isinstance(value, Mapping):
        redacted: dict[object, object] = {}
        for key, item in value.items():
            normalized = str(key).replace("-", "_").lower()
            redacted[key] = (
                "<redacted>"
                if normalized in _SENSITIVE_LOG_KEYS
                else redact_sensitive(item)
            )
        return redacted
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_sensitive(item) for item in value)
    return value


EventHandler = Callable[[GatewayDispatch], Awaitable[None]]
Clock = Callable[[], float]
Sleep = Callable[[float], Awaitable[None]]


class GatewayClient:
    """Long-running Kakao Gateway websocket client.

    The caller owns the injected aiohttp session. A dispatch sequence is saved
    only after the injected application handler completes successfully.
    """

    def __init__(
        self,
        *,
        token: str,
        session: Any,
        state_store: GatewayStatePort,
        event_handler: EventHandler,
        clock: Clock,
        sleep: Sleep,
        gateway_url: str = DEFAULT_GATEWAY_URL,
        backoff: ExponentialBackoff | None = None,
    ) -> None:
        if not token:
            raise ValueError("Kakao bot token must not be empty")
        self._token = token
        self._session = session
        self._state_store = state_store
        self._event_handler = event_handler
        self._clock = clock
        self._sleep = sleep
        self._gateway_url = gateway_url
        self._backoff = backoff or ExponentialBackoff()
        self._protocol: GatewayProtocol | None = None
        self._hello_received = asyncio.Event()
        self._stop_requested = asyncio.Event()
        self._active_websocket: Any | None = None
        self._ready = False

    def __repr__(self) -> str:
        return (
            "GatewayClient("
            f"gateway_url={self._gateway_url!r}, ready={self._ready!r}, "
            "token=<redacted>)"
        )

    @property
    def ready(self) -> bool:
        return self._ready

    async def stop(self) -> None:
        """Request shutdown and close the active websocket gracefully."""

        self._stop_requested.set()
        websocket = self._active_websocket
        if websocket is not None and not websocket.closed:
            await websocket.close(code=1000, message=b"shutdown")

    async def run(self) -> None:
        """Connect until stopped or Kakao returns a fatal close code."""

        attempt = 0
        while not self._stop_requested.is_set():
            was_ready = False
            try:
                close_code, was_ready = await self._connect_once()
                if self._stop_requested.is_set():
                    return
                await self._prepare_reconnect(close_code)
            except FatalGatewayError:
                raise
            except asyncio.CancelledError:
                await self.stop()
                raise
            except Exception as exc:
                if self._stop_requested.is_set():
                    return
                logger.warning(
                    "Kakao Gateway connection interrupted: %s",
                    type(exc).__name__,
                )

            if was_ready:
                attempt = 0
            delay = self._backoff.delay(attempt)
            attempt += 1
            logger.info("Kakao Gateway reconnect scheduled in %.2fs", delay)
            if await self._sleep_until_reconnect_or_stop(delay):
                return

    async def process_frame(
        self,
        frame: Mapping[str, object],
        websocket: Any,
    ) -> None:
        """Process one decoded frame; exposed for deterministic adapter tests."""

        protocol = self._require_protocol()
        actions = protocol.receive(frame, now=self._clock())
        for action in actions:
            if isinstance(action, SendCommand):
                await websocket.send_json(action.payload)
            elif isinstance(action, HeartbeatConfigured):
                self._hello_received.set()
            elif isinstance(action, ReadyReceived):
                state = protocol.current_state
                await self._state_store.save(state)
                self._ready = True
                logger.info(
                    "Kakao Gateway READY (resumed=%s)",
                    action.resumed,
                )
            elif isinstance(action, DispatchReceived):
                await self._event_handler(action.dispatch)
                state = protocol.confirm_dispatch(action.dispatch.sequence)
                await self._state_store.save(state)

    async def initialize_protocol(self) -> GatewayProtocol:
        """Load persisted state and initialize one connection state machine."""

        self._protocol = GatewayProtocol(
            token=self._token,
            state=await self._state_store.load(),
        )
        self._hello_received = asyncio.Event()
        self._ready = False
        return self._protocol

    async def _connect_once(self) -> tuple[int | None, bool]:
        await self.initialize_protocol()
        websocket = await self._session.ws_connect(
            self._gateway_url,
            autoping=True,
        )
        self._active_websocket = websocket
        reader = asyncio.create_task(self._receive_loop(websocket))
        heartbeat = asyncio.create_task(self._heartbeat_loop(websocket))
        stopper = asyncio.create_task(self._stop_requested.wait())
        tasks = {reader, heartbeat, stopper}
        close_code: int | None = None
        try:
            done, _ = await asyncio.wait(
                tasks,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if stopper in done and self._stop_requested.is_set():
                await websocket.close(code=1000, message=b"shutdown")
            elif reader in done:
                close_code = reader.result()
                if heartbeat in done:
                    heartbeat.result()
            elif heartbeat in done:
                heartbeat.result()
            return close_code, self._ready
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if not websocket.closed:
                await websocket.close()
            self._active_websocket = None
            self._ready = False

    async def _receive_loop(self, websocket: Any) -> int | None:
        async for message in websocket:
            if message.type is WSMsgType.TEXT:
                try:
                    frame = json.loads(message.data)
                except (TypeError, json.JSONDecodeError) as exc:
                    raise GatewayProtocolError("Gateway sent malformed JSON") from exc
                if not isinstance(frame, Mapping):
                    raise GatewayProtocolError("Gateway frame must be a JSON object")
                await self.process_frame(frame, websocket)
            elif message.type in {WSMsgType.CLOSE, WSMsgType.CLOSED}:
                break
            elif message.type is WSMsgType.ERROR:
                raise GatewayConnectionError("Kakao Gateway websocket receive failed")
        return websocket.close_code

    async def _heartbeat_loop(self, websocket: Any) -> None:
        await self._hello_received.wait()
        protocol = self._require_protocol()
        interval = protocol.heartbeat_interval_seconds
        if interval is None:
            raise GatewayProtocolError("HELLO did not configure heartbeat")
        cadence = interval * 0.9
        while not self._stop_requested.is_set() and not websocket.closed:
            deadline = protocol.heartbeat_deadline
            delay = (
                max(0.0, deadline - self._clock())
                if protocol.awaiting_heartbeat_ack and deadline is not None
                else cadence
            )
            await self._sleep(delay)
            command = protocol.issue_heartbeat(now=self._clock())
            if command is not None:
                await websocket.send_json(command.payload)

    async def _prepare_reconnect(self, close_code: int | None) -> None:
        disposition = classify_close(close_code)
        if disposition is CloseDisposition.IDENTIFY:
            await self._state_store.clear()
            logger.warning(
                "Kakao Gateway session expired; next connection will IDENTIFY"
            )
            return
        if disposition is CloseDisposition.RESUME:
            return
        if close_code == 4001:
            raise GatewayAuthenticationError(
                "Kakao Gateway authentication failed (close 4001)"
            )
        if close_code == 4003:
            raise GatewayPayloadRejectedError(
                "Kakao Gateway rejected a payload (close 4003)"
            )
        if close_code == 4004:
            raise DuplicateGatewayConnectionError(
                "Kakao Gateway duplicate connection detected (close 4004)"
            )
        raise FatalGatewayError(f"Kakao Gateway returned fatal close code {close_code}")

    async def _sleep_until_reconnect_or_stop(self, delay: float) -> bool:
        sleeper = asyncio.create_task(self._sleep(delay))
        stopper = asyncio.create_task(self._stop_requested.wait())
        tasks = {sleeper, stopper}
        try:
            done, _ = await asyncio.wait(
                tasks,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if sleeper in done:
                sleeper.result()
            return stopper in done and self._stop_requested.is_set()
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    def _require_protocol(self) -> GatewayProtocol:
        if self._protocol is None:
            raise RuntimeError("Gateway protocol has not been initialized")
        return self._protocol
