"""Map Kakao Gateway payloads into transport-neutral domain events."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import TypeAlias

from kakao_bot.adapters.kakao.gateway_protocol import GatewayDispatch
from kakao_bot.domain.models import (
    InboundMessage,
    RoomLifecycleEvent,
    RoomLifecycleType,
)

GatewayInboundEvent: TypeAlias = InboundMessage | RoomLifecycleEvent


class GatewayEventMappingError(ValueError):
    """Raised when a known Gateway event violates the isolated wire contract."""


def map_gateway_dispatch(
    dispatch: GatewayDispatch,
) -> GatewayInboundEvent | None:
    """Return a domain event, or ``None`` for intentionally ignored event types."""

    event_type = dispatch.event_type.strip().upper()
    lifecycle_types = {member.value: member for member in RoomLifecycleType}
    if event_type == "MESSAGE_CREATE":
        return _map_message(dispatch)
    if event_type in lifecycle_types:
        return _map_lifecycle(dispatch, lifecycle_types[event_type])
    return None


def _map_message(dispatch: GatewayDispatch) -> InboundMessage:
    data = dispatch.data
    sender = _mapping(data.get("sender"))
    content = data.get("content")
    content_mapping = _mapping(content)
    text_value = content if isinstance(content, str) else content_mapping.get("text")
    text = text_value if isinstance(text_value, str) else ""
    user_id = _first_nonempty_str(
        data.get("userKey"),
        data.get("user_id"),
        sender.get("userKey"),
        sender.get("id"),
    )
    if user_id is None:
        raise GatewayEventMappingError("MESSAGE_CREATE requires a user key")
    nickname = _first_nonempty_str(
        data.get("nickname"),
        sender.get("nickname"),
        sender.get("name"),
    )
    callback_token = _first_nonempty_str(
        data.get("callbackToken"),
        data.get("callback_token"),
    )
    return InboundMessage(
        event_id=_event_id(data),
        sequence=dispatch.sequence,
        room_id=_room_id(data),
        user_id=user_id,
        nickname=nickname,
        text=text,
        callback_token=callback_token,
        occurred_at=_occurred_at(data),
    )


def _map_lifecycle(
    dispatch: GatewayDispatch,
    event_type: RoomLifecycleType,
) -> RoomLifecycleEvent:
    return RoomLifecycleEvent(
        event_id=_event_id(dispatch.data),
        sequence=dispatch.sequence,
        room_id=_room_id(dispatch.data),
        event_type=event_type,
        occurred_at=_occurred_at(dispatch.data),
    )


def _event_id(data: Mapping[str, object]) -> str:
    event_id = _first_nonempty_str(data.get("id"), data.get("eventId"))
    if event_id is None:
        raise GatewayEventMappingError("Gateway event requires an event id")
    return event_id


def _room_id(data: Mapping[str, object]) -> str:
    room_id = _first_nonempty_str(
        data.get("botGroupKey"),
        data.get("botUserKey"),
        data.get("roomId"),
    )
    if room_id is None:
        raise GatewayEventMappingError("Gateway event requires a room key")
    return room_id


def _occurred_at(data: Mapping[str, object]) -> datetime:
    raw = data.get("timestamp")
    if isinstance(raw, bool):
        raise GatewayEventMappingError("Gateway timestamp must not be boolean")
    if isinstance(raw, (int, float)):
        seconds = float(raw)
        if seconds > 10_000_000_000:
            seconds /= 1_000
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError) as exc:
            raise GatewayEventMappingError("Gateway timestamp is invalid") from exc
    if isinstance(raw, str) and raw.strip():
        normalized = raw.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise GatewayEventMappingError("Gateway timestamp is invalid") from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    raise GatewayEventMappingError("Gateway event requires a timestamp")


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _first_nonempty_str(*values: object) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None
