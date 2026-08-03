"""Answer a user's message: handle the command, reply within the token's life.

The callback token expires five minutes after the message arrives, so this
path only validates and enqueues; the report is delivered later by the outbox.

Handling runs on the event loop rather than in a thread. It is a handful of
indexed SQLite reads plus one insert — orders of magnitude shorter than the
~41s heartbeat interval — and keeping it here means the repository is only
ever touched from one thread in the Gateway process.
"""

from __future__ import annotations

import logging

from kakao_bot.adapters.kakao.command_renderer import render_command_outcome
from kakao_bot.application.command_service import CommandService
from kakao_bot.domain.models import InboundMessage
from kakao_bot.ports.repositories import KakaoCallbackSender

logger = logging.getLogger(__name__)


class MessageCommandHandler:
    def __init__(
        self,
        service: CommandService,
        sender: KakaoCallbackSender,
    ) -> None:
        self._service = service
        self._sender = sender

    async def __call__(self, message: InboundMessage) -> None:
        try:
            outcome = self._service.handle(message)
        except Exception:  # noqa: BLE001 - never break the Gateway session
            logger.exception("Command handling failed for room %s", message.room_id)
            return

        if not outcome.should_reply:
            logger.info("No reply for unrecognized utterance in %s", message.room_id)
            return

        if not message.callback_token:
            # Kakao marks callbackToken optional; without one there is no way
            # to answer this message, and send_message would be a new bubble
            # rather than a reply.
            logger.warning("No callback token; cannot reply in %s", message.room_id)
            return

        try:
            response = render_command_outcome(outcome)
            result = await self._sender.callback(message.callback_token, response)
        except Exception:  # noqa: BLE001
            logger.exception("Reply failed for room %s", message.room_id)
            return

        logger.info(
            "Command reply (kind=%s, success=%s, http=%s)",
            outcome.kind.value,
            result.success,
            result.status_code,
        )
