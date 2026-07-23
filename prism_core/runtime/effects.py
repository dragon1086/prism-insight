"""Pure authorization contracts for external effects.

This module does not perform network calls or import any broker implementation.
It issues immutable capability values only after deterministic policy checks.
"""

from __future__ import annotations

import re
from dataclasses import InitVar, dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Mapping

from prism_core.runtime.settings import PHASE1_MODES, ProductMode, RuntimeSettings


class RuntimeEnvironment(str, Enum):
    DEVELOPMENT = "development"
    TEST = "test"
    OPERATIONS = "operations"


class CapabilityDenied(RuntimeError):
    """The requested external effect is not authorized by runtime policy."""


_CAPABILITY_FACTORY_TOKEN = object()


def _require_factory_token(token: object | None) -> None:
    if token is not _CAPABILITY_FACTORY_TOKEN:
        raise CapabilityDenied("capability objects must be issued by an authorization factory")


AGENTNEWS_URLS: Mapping[str, str] = MappingProxyType(
    {
        "KR": "https://agentnews.md/finance-ko.md",
        "US": "https://agentnews.md/finance.md",
    }
)


@dataclass(frozen=True)
class EffectControls:
    rate_limiter_configured: bool
    dedupe_store_configured: bool
    audit_sink_configured: bool

    def __post_init__(self) -> None:
        values = (
            self.rate_limiter_configured,
            self.dedupe_store_configured,
            self.audit_sink_configured,
        )
        if any(type(value) is not bool for value in values):
            raise ValueError("effect controls must be boolean values")


@dataclass(frozen=True)
class TelegramInteractiveCapability:
    allowed_chat_id: str = field(repr=False)
    allowed_user_id: str = field(repr=False)
    _authorization_token: InitVar[object | None] = None

    def __post_init__(self, _authorization_token: object | None) -> None:
        _require_factory_token(_authorization_token)


@dataclass(frozen=True)
class TelegramTestSendCapability:
    environment: RuntimeEnvironment
    destination_chat_id: str = field(repr=False)
    inbound_user_id: str = field(repr=False)
    request_id: str
    dedupe_key: str
    payload: str = field(repr=False)
    rate_limit_required: bool = True
    audit_required: bool = True
    _authorization_token: InitVar[object | None] = None

    def __post_init__(self, _authorization_token: object | None) -> None:
        _require_factory_token(_authorization_token)


@dataclass(frozen=True)
class AgentNewsLiveFetchCapability:
    environment: RuntimeEnvironment
    url: str
    read_only: bool = True
    requires_credentials: bool = False
    _authorization_token: InitVar[object | None] = None

    def __post_init__(self, _authorization_token: object | None) -> None:
        _require_factory_token(_authorization_token)


@dataclass(frozen=True)
class BrokerEffectCapability:
    product_mode: ProductMode
    _authorization_token: InitVar[object | None] = None

    def __post_init__(self, _authorization_token: object | None) -> None:
        _require_factory_token(_authorization_token)


def _normalize_environment(
    environment: RuntimeEnvironment | str,
) -> RuntimeEnvironment:
    try:
        return RuntimeEnvironment(environment)
    except (TypeError, ValueError) as exc:
        raise CapabilityDenied("environment is not approved") from exc


def _is_safe_envelope_metadata(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value
    ) is not None


def authorize_telegram_interactive_startup(
    settings: RuntimeSettings,
) -> TelegramInteractiveCapability:
    """Require explicit transport activation and both allowlist dimensions."""

    if not settings.telegram_enabled:
        raise CapabilityDenied("Telegram transport is disabled")
    allowed_chat_id = settings.allowed_chat_id
    allowed_user_id = settings.allowed_user_id
    if (
        not isinstance(allowed_chat_id, str)
        or not allowed_chat_id.strip()
        or not isinstance(allowed_user_id, str)
        or not allowed_user_id.strip()
    ):
        raise CapabilityDenied("Telegram chat and user allowlist entries are required")
    return TelegramInteractiveCapability(
        allowed_chat_id=allowed_chat_id,
        allowed_user_id=allowed_user_id,
        _authorization_token=_CAPABILITY_FACTORY_TOKEN,
    )


def authorize_telegram_test_send(
    settings: RuntimeSettings,
    *,
    environment: RuntimeEnvironment | str,
    destination_chat_id: str,
    inbound_user_id: str,
    request_id: str,
    dedupe_key: str,
    message: str,
    controls: EffectControls,
) -> TelegramTestSendCapability:
    """Authorize one marked smoke payload; no transport is invoked here.

    Envelope metadata is validated. The free-form message body remains
    untrusted content and must not be parsed as authorization metadata.
    """

    normalized_environment = _normalize_environment(environment)
    interactive = authorize_telegram_interactive_startup(settings)
    if (
        destination_chat_id != interactive.allowed_chat_id
        or inbound_user_id != interactive.allowed_user_id
    ):
        raise CapabilityDenied("Telegram destination or inbound user is not allowlisted")
    if not controls.rate_limiter_configured:
        raise CapabilityDenied("Telegram test send requires a rate limiter")
    if not controls.dedupe_store_configured:
        raise CapabilityDenied("Telegram test send requires a dedupe store")
    if not controls.audit_sink_configured:
        raise CapabilityDenied("Telegram test send requires an audit sink")
    if not _is_safe_envelope_metadata(request_id) or not _is_safe_envelope_metadata(
        dedupe_key
    ):
        raise CapabilityDenied(
            "Telegram test send metadata must use strict identifier syntax"
        )

    payload = (
        f"[TEST] environment={normalized_environment.value} request_id={request_id} "
        f"dedupe_key={dedupe_key}\n{message}"
    )
    return TelegramTestSendCapability(
        environment=normalized_environment,
        destination_chat_id=destination_chat_id,
        inbound_user_id=inbound_user_id,
        request_id=request_id,
        dedupe_key=dedupe_key,
        payload=payload,
        _authorization_token=_CAPABILITY_FACTORY_TOKEN,
    )


def authorize_agentnews_live_fetch(
    *,
    environment: RuntimeEnvironment | str,
    url: str,
) -> AgentNewsLiveFetchCapability:
    """Authorize read-only fetches only for the two approved public boards."""

    normalized_environment = _normalize_environment(environment)
    if url not in AGENTNEWS_URLS.values():
        raise CapabilityDenied("URL is not an approved public AgentNews endpoint")
    return AgentNewsLiveFetchCapability(
        environment=normalized_environment,
        url=url,
        _authorization_token=_CAPABILITY_FACTORY_TOKEN,
    )


def authorize_broker_effect(settings: RuntimeSettings) -> BrokerEffectCapability:
    """Deny all Phase 1 broker effects without importing broker code."""

    if settings.product_mode in PHASE1_MODES:
        raise CapabilityDenied("broker effects are unavailable in Phase 1")
    if settings.product_mode is not ProductMode.BROKER_PAPER:
        raise CapabilityDenied("only broker paper may request broker capability")
    if not settings.broker_enabled:
        raise CapabilityDenied("broker capability is disabled")
    return BrokerEffectCapability(
        product_mode=settings.product_mode,
        _authorization_token=_CAPABILITY_FACTORY_TOKEN,
    )
