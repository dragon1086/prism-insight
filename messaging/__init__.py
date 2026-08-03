"""
PRISM-INSIGHT Messaging Module

Trading signal and batch campaign publishing module.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from messaging.batch_campaign_publisher import (
        BatchCampaignPublisher,
        build_batch_campaign_event,
        campaign_id_for,
        publish_batch_campaign_best_effort,
    )
    from messaging.redis_signal_publisher import (
        SignalPublisher,
        get_signal_publisher,
        publish_buy_signal,
        publish_sell_signal,
    )

__all__ = [
    "SignalPublisher",
    "get_signal_publisher",
    "publish_buy_signal",
    "publish_sell_signal",
    "BatchCampaignPublisher",
    "build_batch_campaign_event",
    "campaign_id_for",
    "publish_batch_campaign_best_effort",
]

_EXPORT_MODULES = {
    "SignalPublisher": "messaging.redis_signal_publisher",
    "get_signal_publisher": "messaging.redis_signal_publisher",
    "publish_buy_signal": "messaging.redis_signal_publisher",
    "publish_sell_signal": "messaging.redis_signal_publisher",
    "BatchCampaignPublisher": "messaging.batch_campaign_publisher",
    "build_batch_campaign_event": "messaging.batch_campaign_publisher",
    "campaign_id_for": "messaging.batch_campaign_publisher",
    "publish_batch_campaign_best_effort": "messaging.batch_campaign_publisher",
}


def __getattr__(name: str) -> Any:
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
