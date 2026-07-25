from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from types import SimpleNamespace

import pytest
from aiohttp import WSMsgType

from kakao_bot.adapters.kakao.gateway import (
    AlreadyRunningError,
    DuplicateGatewayConnectionError,
    ExponentialBackoff,
    GatewayAuthenticationError,
    GatewayClient,
    GatewayPayloadRejectedError,
    InMemoryGatewayState,
    SingleInstanceFileLock,
    redact_sensitive,
)
from kakao_bot.adapters.kakao.gateway_protocol import (
    CloseDisposition,
    GatewayDispatch,
    GatewayOpcode,
    GatewayPhase,
    GatewayProtocol,
    HeartbeatAckTimeout,
    HeartbeatConfigured,
    ReadyReceived,
    SendCommand,
    classify_close,
)
from kakao_bot.ports.gateway_state import GatewayState
from kakao_bot.runtime.gateway_main import GatewayRuntimeConfig, load_config

TOKEN = "never-log-this-bot-token"


class FakeClock:
    def __init__(self, current: float = 0.0) -> None:
        self.current = current

    def __call__(self) -> float:
        return self.current


class FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[Mapping[str, object]] = []
        self.closed = False
        self.close_code: int | None = None

    async def send_json(self, payload: Mapping[str, object]) -> None:
        self.sent.append(payload)

    async def close(
        self,
        *,
        code: int = 1000,
        message: bytes = b"",
    ) -> None:
        del message
        self.close_code = code
        self.closed = True


class ScriptedWebSocket(FakeWebSocket):
    def __init__(
        self,
        frames: list[Mapping[str, object]],
        *,
        close_code: int,
    ) -> None:
        super().__init__()
        self._messages = [
            SimpleNamespace(type=WSMsgType.TEXT, data=json.dumps(frame))
            for frame in frames
        ]
        self.close_code = close_code

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._messages:
            return self._messages.pop(0)
        self.closed = True
        raise StopAsyncIteration


class FakeSession:
    def __init__(self, websocket: ScriptedWebSocket) -> None:
        self.websocket = websocket
        self.calls: list[tuple[str, bool]] = []

    async def ws_connect(self, url: str, *, autoping: bool):
        self.calls.append((url, autoping))
        return self.websocket


async def no_sleep(_: float) -> None:
    return None


def protocol_command(actions: tuple[object, ...]) -> SendCommand:
    return next(action for action in actions if isinstance(action, SendCommand))


def test_hello_identify_ready_handshake_uses_central_wire_contract():
    protocol = GatewayProtocol(TOKEN)

    actions = protocol.receive(
        {"op": int(GatewayOpcode.HELLO), "d": {"heartbeat_interval": 42_000}},
        now=1.0,
    )

    interval = next(
        action for action in actions if isinstance(action, HeartbeatConfigured)
    )
    command = protocol_command(actions)
    assert interval.interval_seconds == 42.0
    assert command.payload == {
        "op": int(GatewayOpcode.IDENTIFY),
        "d": {"token": TOKEN},
    }
    assert protocol.phase is GatewayPhase.AUTHENTICATING

    [ready] = protocol.receive(
        {
            "op": int(GatewayOpcode.READY),
            "d": {"session_id": "session-new"},
        },
        now=2.0,
    )
    assert ready == ReadyReceived(session_id="session-new", resumed=False)
    assert protocol.phase is GatewayPhase.READY
    assert protocol.current_state == GatewayState(
        session_id="session-new",
        sequence=None,
    )


def test_hello_uses_resume_when_session_and_sequence_exist():
    protocol = GatewayProtocol(
        TOKEN,
        GatewayState(session_id="session-existing", sequence=41),
    )

    actions = protocol.receive(
        {"op": int(GatewayOpcode.HELLO), "d": {}},
        now=1.0,
    )

    assert protocol_command(actions).payload == {
        "op": int(GatewayOpcode.RESUME),
        "d": {
            "token": TOKEN,
            "session_id": "session-existing",
            "sequence": 41,
        },
    }
    [ready] = protocol.receive(
        {
            "op": int(GatewayOpcode.READY),
            "d": {"session_id": "session-existing"},
        },
        now=2.0,
    )
    assert ready == ReadyReceived(session_id="session-existing", resumed=True)
    assert protocol.current_state.sequence == 41


def test_heartbeat_tracks_ack_and_enforces_deadline():
    protocol = GatewayProtocol(TOKEN)
    protocol.receive(
        {"op": int(GatewayOpcode.HELLO), "d": {"heartbeat_interval": 1_000}},
        now=0.0,
    )

    heartbeat = protocol.issue_heartbeat(now=10.0)
    assert heartbeat is not None
    assert heartbeat.payload == {"op": int(GatewayOpcode.HEARTBEAT)}
    assert protocol.awaiting_heartbeat_ack
    assert protocol.heartbeat_deadline == 11.0
    assert protocol.issue_heartbeat(now=10.5) is None

    protocol.receive(
        {"op": int(GatewayOpcode.HEARTBEAT_ACK)},
        now=10.6,
    )
    assert not protocol.awaiting_heartbeat_ack
    assert protocol.heartbeat_deadline is None

    protocol.issue_heartbeat(now=20.0)
    with pytest.raises(HeartbeatAckTimeout):
        protocol.issue_heartbeat(now=21.0)


@pytest.mark.asyncio
async def test_dispatch_routes_then_persists_committed_sequence():
    events = []

    async def handler(dispatch):
        events.append(dispatch)

    state_store = InMemoryGatewayState()
    clock = FakeClock()
    client = GatewayClient(
        token=TOKEN,
        session=object(),
        state_store=state_store,
        event_handler=handler,
        clock=clock,
        sleep=no_sleep,
    )
    websocket = FakeWebSocket()
    await client.initialize_protocol()
    await client.process_frame(
        {"op": int(GatewayOpcode.HELLO), "d": {}},
        websocket,
    )
    await client.process_frame(
        {
            "op": int(GatewayOpcode.READY),
            "d": {"session_id": "session-1"},
        },
        websocket,
    )
    await client.process_frame(
        {
            "op": int(GatewayOpcode.DISPATCH),
            "s": 7,
            "t": "MESSAGE_CREATE",
            "d": {"safe": "payload"},
        },
        websocket,
    )

    assert len(events) == 1
    assert events[0].sequence == 7
    assert events[0].event_type == "MESSAGE_CREATE"
    assert "payload" not in repr(events[0])
    assert await state_store.load() == GatewayState(
        session_id="session-1",
        sequence=7,
    )


@pytest.mark.asyncio
async def test_dispatch_failure_does_not_advance_sequence():
    async def failing_handler(_):
        raise RuntimeError("application commit failed")

    state_store = InMemoryGatewayState()
    client = GatewayClient(
        token=TOKEN,
        session=object(),
        state_store=state_store,
        event_handler=failing_handler,
        clock=FakeClock(),
        sleep=no_sleep,
    )
    websocket = FakeWebSocket()
    await client.initialize_protocol()
    await client.process_frame(
        {"op": int(GatewayOpcode.HELLO), "d": {}},
        websocket,
    )
    await client.process_frame(
        {
            "op": int(GatewayOpcode.READY),
            "d": {"session_id": "session-1"},
        },
        websocket,
    )

    with pytest.raises(RuntimeError, match="application commit failed"):
        await client.process_frame(
            {
                "op": int(GatewayOpcode.DISPATCH),
                "s": 8,
                "t": "MESSAGE_CREATE",
                "d": {},
            },
            websocket,
        )
    assert await state_store.load() == GatewayState(
        session_id="session-1",
        sequence=None,
    )


@pytest.mark.asyncio
async def test_client_uses_injected_session_and_routes_scripted_connection():
    frames = [
        {"op": int(GatewayOpcode.HELLO), "d": {}},
        {
            "op": int(GatewayOpcode.READY),
            "d": {"session_id": "session-scripted"},
        },
        {
            "op": int(GatewayOpcode.DISPATCH),
            "s": 12,
            "t": "ENTRANCE",
            "d": {"room": "room-1"},
        },
    ]
    websocket = ScriptedWebSocket(frames, close_code=1001)
    session = FakeSession(websocket)
    state_store = InMemoryGatewayState()
    events = []
    never_wake = asyncio.Event()

    async def blocked_sleep(_: float) -> None:
        await never_wake.wait()

    async def handler(dispatch):
        events.append(dispatch)

    client = GatewayClient(
        token=TOKEN,
        session=session,
        state_store=state_store,
        event_handler=handler,
        clock=FakeClock(),
        sleep=blocked_sleep,
    )

    close_code, was_ready = await client._connect_once()

    assert close_code == 1001
    assert was_ready is True
    assert session.calls == [
        ("wss://bot-gateway.kakao.com/gateway", True),
    ]
    assert websocket.sent[0]["op"] == int(GatewayOpcode.IDENTIFY)
    assert [event.event_type for event in events] == ["ENTRANCE"]
    assert await state_store.load() == GatewayState(
        session_id="session-scripted",
        sequence=12,
    )


@pytest.mark.asyncio
async def test_close_1001_resumes_but_4009_clears_session_for_identify():
    initial = GatewayState(session_id="session-1", sequence=9)
    state_store = InMemoryGatewayState(initial)
    client = GatewayClient(
        token=TOKEN,
        session=object(),
        state_store=state_store,
        event_handler=lambda _: no_sleep(0),
        clock=FakeClock(),
        sleep=no_sleep,
    )

    assert classify_close(1001) is CloseDisposition.RESUME
    await client._prepare_reconnect(1001)
    assert await state_store.load() == initial

    assert classify_close(4009) is CloseDisposition.IDENTIFY
    await client._prepare_reconnect(4009)
    assert await state_store.load() == GatewayState()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "error_type"),
    [
        (4001, GatewayAuthenticationError),
        (4003, GatewayPayloadRejectedError),
        (4004, DuplicateGatewayConnectionError),
    ],
)
async def test_fatal_close_codes_do_not_reconnect(code, error_type):
    client = GatewayClient(
        token=TOKEN,
        session=object(),
        state_store=InMemoryGatewayState(),
        event_handler=lambda _: no_sleep(0),
        clock=FakeClock(),
        sleep=no_sleep,
    )

    with pytest.raises(error_type):
        await client._prepare_reconnect(code)


@pytest.mark.asyncio
async def test_graceful_stop_closes_active_websocket():
    client = GatewayClient(
        token=TOKEN,
        session=object(),
        state_store=InMemoryGatewayState(),
        event_handler=lambda _: no_sleep(0),
        clock=FakeClock(),
        sleep=no_sleep,
    )
    websocket = FakeWebSocket()
    client._active_websocket = websocket

    await client.stop()

    assert websocket.closed
    assert websocket.close_code == 1000


def test_exponential_backoff_is_jittered_and_bounded():
    low = ExponentialBackoff(
        maximum_seconds=30,
        jitter_ratio=0.2,
        random_value=lambda: 0.0,
    )
    high = ExponentialBackoff(
        maximum_seconds=30,
        jitter_ratio=0.2,
        random_value=lambda: 1.0,
    )

    assert [low.delay(attempt) for attempt in range(4)] == [
        0.8,
        1.6,
        3.2,
        6.4,
    ]
    assert high.delay(0) == 1.2
    assert high.delay(20) == 30


def test_single_instance_file_lock_rejects_second_owner_and_releases(tmp_path):
    path = tmp_path / "gateway.lock"
    first = SingleInstanceFileLock(path)
    second = SingleInstanceFileLock(path)
    first.acquire()
    try:
        with pytest.raises(AlreadyRunningError):
            second.acquire()
    finally:
        first.release()

    second.acquire()
    second.release()


def test_token_callback_and_authorization_are_redacted():
    raw = {
        "token": TOKEN,
        "d": {
            "callbackToken": "callback-secret",
            "Authorization": "Bearer secret",
            "text": "safe",
        },
    }

    redacted = redact_sensitive(raw)

    rendered = repr(redacted)
    assert TOKEN not in rendered
    assert "callback-secret" not in rendered
    assert "Bearer secret" not in rendered
    assert redacted["d"]["text"] == "safe"

    protocol = GatewayProtocol(TOKEN)
    command = protocol_command(
        protocol.receive(
            {"op": int(GatewayOpcode.HELLO), "d": {}},
            now=0.0,
        )
    )
    assert TOKEN not in repr(protocol)
    assert TOKEN not in repr(command)
    dispatch = GatewayDispatch(
        sequence=1,
        event_type="MESSAGE_CREATE",
        data={"callbackToken": "callback-secret"},
    )
    assert "callback-secret" not in repr(dispatch)


def test_runtime_config_reads_token_without_exposing_it_in_repr(tmp_path):
    config = load_config(
        {
            "KAKAO_BOT_TOKEN": TOKEN,
            "KAKAO_GATEWAY_LOCK_PATH": str(tmp_path / "gateway.lock"),
            "KAKAO_BOT_DATABASE_PATH": str(tmp_path / "kakao.sqlite"),
        }
    )

    assert config == GatewayRuntimeConfig(
        token=TOKEN,
        lock_path=tmp_path / "gateway.lock",
        database_path=tmp_path / "kakao.sqlite",
    )
    assert TOKEN not in repr(config)
