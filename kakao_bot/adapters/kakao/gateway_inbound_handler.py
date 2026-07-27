"""Connect Gateway dispatches to the inbound application service."""

from __future__ import annotations

import logging

from kakao_bot.adapters.kakao.event_mapper import (
    GatewayEventMappingError,
    map_gateway_dispatch,
)
from kakao_bot.adapters.kakao.gateway_protocol import GatewayDispatch
from kakao_bot.application.gateway_inbound_service import GatewayInboundService

logger = logging.getLogger(__name__)


class GatewayDispatchHandler:
    def __init__(self, service: GatewayInboundService) -> None:
        self._service = service

    async def __call__(self, dispatch: GatewayDispatch) -> None:
        try:
            event = map_gateway_dispatch(dispatch)
        except GatewayEventMappingError:
            # A single malformed event must not tear down the Gateway
            # connection. Skip it and keep the session alive.
            logger.warning(
                "Skipping unmappable Kakao Gateway event (type=%s, sequence=%d)",
                dispatch.event_type,
                dispatch.sequence,
                exc_info=True,
            )
            return
        if event is None:
            logger.info(
                "Ignoring unsupported Kakao Gateway event (type=%s, sequence=%d)",
                dispatch.event_type,
                dispatch.sequence,
            )
            return
        created = self._service.handle(event)
        logger.info(
            "Applied Kakao Gateway event (type=%s, sequence=%d, duplicate=%s)",
            dispatch.event_type,
            dispatch.sequence,
            not created,
        )
