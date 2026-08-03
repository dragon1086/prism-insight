"""Pure Kakao Bot Gateway wire protocol and state transitions.

The live Kakao smoke test is still pending, so all wire field names and
payload builders intentionally live in this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, IntEnum
from typing import Mapping, TypeAlias

from kakao_bot.ports.gateway_state import GatewayState

OP_FIELD = "op"
DATA_FIELD = "d"
SEQUENCE_FIELD = "s"
EVENT_TYPE_FIELD = "t"
HEARTBEAT_INTERVAL_FIELD = "heartbeat_interval"
TOKEN_FIELD = "token"
SESSION_ID_FIELD = "session_id"
RESUME_SEQUENCE_FIELD = "sequence"

DEFAULT_HEARTBEAT_INTERVAL_MS = 41_250


class GatewayOpcode(IntEnum):
    DISPATCH = 0
    HEARTBEAT = 1
    IDENTIFY = 2
    READY = 3
    RESUME = 6
    HELLO = 10
    HEARTBEAT_ACK = 11


class GatewayPhase(str, Enum):
    WAITING_HELLO = "WAITING_HELLO"
    AUTHENTICATING = "AUTHENTICATING"
    READY = "READY"


class CloseDisposition(str, Enum):
    RESUME = "RESUME"
    IDENTIFY = "IDENTIFY"
    FATAL = "FATAL"


class GatewayProtocolError(ValueError):
    """Raised when a Gateway frame violates the centralized contract."""


class HeartbeatAckTimeout(TimeoutError):
    """Raised when Kakao does not acknowledge a heartbeat before its deadline."""


@dataclass(frozen=True, repr=False)
class GatewayDispatch:
    sequence: int
    event_type: str
    data: Mapping[str, object]

    def __repr__(self) -> str:
        return (
            "GatewayDispatch("
            f"sequence={self.sequence!r}, event_type={self.event_type!r}, "
            "data=<redacted>)"
        )


@dataclass(frozen=True, repr=False)
class SendCommand:
    """An outbound protocol command.

    ``repr`` deliberately excludes the payload because IDENTIFY and RESUME
    contain the bot token.
    """

    payload: Mapping[str, object]

    def __repr__(self) -> str:
        return f"SendCommand(op={self.payload.get(OP_FIELD)!r}, payload=<redacted>)"


@dataclass(frozen=True)
class HeartbeatConfigured:
    interval_seconds: float


@dataclass(frozen=True)
class ReadyReceived:
    session_id: str
    resumed: bool


@dataclass(frozen=True)
class DispatchReceived:
    dispatch: GatewayDispatch


ProtocolAction: TypeAlias = (
    SendCommand | HeartbeatConfigured | ReadyReceived | DispatchReceived
)


def identify_payload(token: str) -> dict[str, object]:
    return {
        OP_FIELD: int(GatewayOpcode.IDENTIFY),
        DATA_FIELD: {TOKEN_FIELD: token},
    }


def resume_payload(token: str, state: GatewayState) -> dict[str, object]:
    if not state.can_resume:
        raise GatewayProtocolError("RESUME requires session_id and sequence")
    return {
        OP_FIELD: int(GatewayOpcode.RESUME),
        DATA_FIELD: {
            TOKEN_FIELD: token,
            SESSION_ID_FIELD: state.session_id,
            RESUME_SEQUENCE_FIELD: state.sequence,
        },
    }


def heartbeat_payload() -> dict[str, object]:
    return {OP_FIELD: int(GatewayOpcode.HEARTBEAT)}


def classify_close(code: int | None) -> CloseDisposition:
    if code == 4009:
        return CloseDisposition.IDENTIFY
    if code in {4001, 4003, 4004}:
        return CloseDisposition.FATAL
    return CloseDisposition.RESUME


class GatewayProtocol:
    """State machine for one websocket connection.

    It performs no I/O. Callers execute returned actions and acknowledge a
    dispatch with :meth:`confirm_dispatch` only after application work commits.
    """

    def __init__(self, token: str, state: GatewayState | None = None) -> None:
        if not token:
            raise ValueError("Kakao bot token must not be empty")
        self._token = token
        self._resume_state = state or GatewayState()
        self._phase = GatewayPhase.WAITING_HELLO
        self._heartbeat_interval_seconds: float | None = None
        self._awaiting_heartbeat_ack = False
        self._heartbeat_deadline: float | None = None
        self._session_id = self._resume_state.session_id
        self._sequence = self._resume_state.sequence
        self._resuming = False

    def __repr__(self) -> str:
        return (
            "GatewayProtocol("
            f"phase={self._phase.value!r}, session_id={self._session_id!r}, "
            f"sequence={self._sequence!r}, token=<redacted>)"
        )

    @property
    def phase(self) -> GatewayPhase:
        return self._phase

    @property
    def heartbeat_interval_seconds(self) -> float | None:
        return self._heartbeat_interval_seconds

    @property
    def awaiting_heartbeat_ack(self) -> bool:
        return self._awaiting_heartbeat_ack

    @property
    def heartbeat_deadline(self) -> float | None:
        return self._heartbeat_deadline

    @property
    def current_state(self) -> GatewayState:
        return GatewayState(session_id=self._session_id, sequence=self._sequence)

    def receive(
        self,
        frame: Mapping[str, object],
        *,
        now: float,
    ) -> tuple[ProtocolAction, ...]:
        opcode = _parse_opcode(frame)
        if opcode is GatewayOpcode.HELLO:
            return self._on_hello(frame)
        if opcode is GatewayOpcode.READY:
            return self._on_ready(frame)
        if opcode is GatewayOpcode.HEARTBEAT_ACK:
            if self._heartbeat_interval_seconds is None:
                raise GatewayProtocolError("HEARTBEAT_ACK received before HELLO")
            self._awaiting_heartbeat_ack = False
            self._heartbeat_deadline = None
            return ()
        if opcode is GatewayOpcode.DISPATCH:
            if self._phase is not GatewayPhase.READY:
                raise GatewayProtocolError("DISPATCH received before READY")
            return (DispatchReceived(_parse_dispatch(frame)),)
        raise GatewayProtocolError(f"unexpected server opcode: {int(opcode)}")

    def issue_heartbeat(self, *, now: float) -> SendCommand | None:
        if self._heartbeat_interval_seconds is None:
            raise GatewayProtocolError("cannot heartbeat before HELLO")
        if self._awaiting_heartbeat_ack:
            deadline = self._heartbeat_deadline
            if deadline is not None and now >= deadline:
                raise HeartbeatAckTimeout("Gateway heartbeat ACK deadline exceeded")
            return None
        self._awaiting_heartbeat_ack = True
        self._heartbeat_deadline = now + self._heartbeat_interval_seconds
        return SendCommand(heartbeat_payload())

    def confirm_dispatch(self, sequence: int) -> GatewayState:
        if isinstance(sequence, bool) or not isinstance(sequence, int):
            raise GatewayProtocolError("dispatch sequence must be an integer")
        if self._session_id is None:
            raise GatewayProtocolError("cannot commit dispatch before READY")
        if self._sequence is None or sequence > self._sequence:
            self._sequence = sequence
        return self.current_state

    def _on_hello(self, frame: Mapping[str, object]) -> tuple[ProtocolAction, ...]:
        if self._phase is not GatewayPhase.WAITING_HELLO:
            raise GatewayProtocolError("HELLO received more than once")
        data = _mapping_field(frame, DATA_FIELD)
        interval_ms = data.get(HEARTBEAT_INTERVAL_FIELD, DEFAULT_HEARTBEAT_INTERVAL_MS)
        if (
            isinstance(interval_ms, bool)
            or not isinstance(interval_ms, (int, float))
            or interval_ms <= 0
        ):
            raise GatewayProtocolError("heartbeat_interval must be positive")
        interval_seconds = float(interval_ms) / 1_000
        self._heartbeat_interval_seconds = interval_seconds
        self._phase = GatewayPhase.AUTHENTICATING
        self._resuming = self._resume_state.can_resume
        command = (
            resume_payload(self._token, self._resume_state)
            if self._resuming
            else identify_payload(self._token)
        )
        return (
            HeartbeatConfigured(interval_seconds),
            SendCommand(command),
        )

    def _on_ready(self, frame: Mapping[str, object]) -> tuple[ProtocolAction, ...]:
        if self._phase is not GatewayPhase.AUTHENTICATING:
            raise GatewayProtocolError("READY received before authentication")
        data = _mapping_field(frame, DATA_FIELD)
        session_id = data.get(SESSION_ID_FIELD)
        if not isinstance(session_id, str) or not session_id:
            raise GatewayProtocolError("READY requires a non-empty session_id")
        self._session_id = session_id
        if not self._resuming:
            self._sequence = None
        self._phase = GatewayPhase.READY
        return (ReadyReceived(session_id=session_id, resumed=self._resuming),)


def _parse_opcode(frame: Mapping[str, object]) -> GatewayOpcode:
    raw_opcode = frame.get(OP_FIELD)
    if isinstance(raw_opcode, bool) or not isinstance(raw_opcode, int):
        raise GatewayProtocolError("Gateway opcode must be an integer")
    try:
        return GatewayOpcode(raw_opcode)
    except ValueError as exc:
        raise GatewayProtocolError(f"unknown Gateway opcode: {raw_opcode}") from exc


def _mapping_field(frame: Mapping[str, object], field: str) -> Mapping[str, object]:
    value = frame.get(field)
    if not isinstance(value, Mapping):
        raise GatewayProtocolError(f"Gateway field {field!r} must be an object")
    return value


def _parse_dispatch(frame: Mapping[str, object]) -> GatewayDispatch:
    sequence = frame.get(SEQUENCE_FIELD)
    event_type = frame.get(EVENT_TYPE_FIELD)
    if isinstance(sequence, bool) or not isinstance(sequence, int):
        raise GatewayProtocolError("DISPATCH requires an integer sequence")
    if not isinstance(event_type, str) or not event_type:
        raise GatewayProtocolError("DISPATCH requires a non-empty event type")
    return GatewayDispatch(
        sequence=sequence,
        event_type=event_type,
        data=_mapping_field(frame, DATA_FIELD),
    )
