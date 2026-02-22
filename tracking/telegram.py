"""
Telegram Sender for Stock Tracking

Handles Telegram message sending and translation.
Extracted from stock_tracking_agent.py for LLM context efficiency.
"""

import asyncio
import logging
from typing import List, Optional

from telegram import Bot
from telegram.error import TelegramError

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 4096


class TelegramSender:
    """Handles Telegram message sending operations."""

    def __init__(self, bot: Optional[Bot], config=None):
        """
        Initialize TelegramSender.

        Args:
            bot: Telegram Bot instance (or None if not configured)
            config: TelegramConfig object for multi-language support
        """
        self.bot = bot
        self.config = config

    async def send_messages(
        self,
        chat_id: str,
        messages: List[str],
        language: str = "ko",
        msg_types: Optional[list] = None
    ) -> bool:
        """
        Send messages to Telegram channel.

        Args:
            chat_id: Telegram channel ID
            messages: List of messages to send
            language: Message language (ko/en)

        Returns:
            bool: Send success status
        """
        if not chat_id:
            logger.info("No Telegram channel ID. Skipping message send")
            for message in messages:
                logger.info(f"[Message (not sent)] {message[:100]}...")
            return True

        if not self.bot:
            logger.warning("Telegram bot not initialized")
            for message in messages:
                logger.info(f"[Message (bot not initialized)] {message[:100]}...")
            return False

        # Translate if needed
        if language == "en":
            messages = await self._translate_messages(messages, "en")

        success = True
        for idx, message in enumerate(messages):
            msg_type = msg_types[idx] if msg_types and idx < len(msg_types) else None
            try:
                await self._send_single_message(chat_id, message, msg_type=msg_type)
                logger.info(f"Telegram message sent: {chat_id}")
            except TelegramError as e:
                logger.error(f"Telegram message send failed: {e}")
                success = False

            await asyncio.sleep(1)

        return success

    async def _send_single_message(self, chat_id: str, message: str, msg_type=None):
        """Send a single message, splitting if too long."""
        if len(message) <= MAX_MESSAGE_LENGTH:
            result = await self.bot.send_message(chat_id=chat_id, text=message)
            # Firebase Bridge
            try:
                from firebase_bridge import notify
                await notify(
                    message=message,
                    telegram_message_id=result.message_id,
                    channel_id=chat_id,
                    msg_type=msg_type,
                )
            except Exception as e:
                logger.debug(f"Firebase bridge: {e}")
        else:
            parts = self._split_message(message)
            for i, part in enumerate(parts, 1):
                await self.bot.send_message(
                    chat_id=chat_id,
                    text=f"[{i}/{len(parts)}]\n{part}"
                )
                await asyncio.sleep(0.5)

    async def _translate_messages(self, messages: List[str], to_lang: str) -> List[str]:
        """Translate messages to target language."""
        try:
            from cores.agents.telegram_translator_agent import translate_telegram_message

            logger.info(f"Translating {len(messages)} messages to {to_lang}")
            translated = []
            for idx, message in enumerate(messages, 1):
                logger.info(f"Translating message {idx}/{len(messages)}")
                result = await translate_telegram_message(message, model="gpt-5-nano")
                translated.append(result)
            logger.info("All messages translated successfully")
            return translated
        except Exception as e:
            logger.error(f"Translation failed: {str(e)}. Using original messages.")
            return messages

    async def send_to_translation_channels(self, messages: List[str]):
        """Send messages to broadcast translation channels.

        Note: Callers should wrap with asyncio.create_task() for non-blocking execution.
        """
        if not self.config or not self.config.broadcast_languages:
            return

        try:
            from cores.agents.telegram_translator_agent import translate_telegram_message

            for lang in self.config.broadcast_languages:
                try:
                    channel_id = self.config.get_broadcast_channel_id(lang)
                    if not channel_id:
                        logger.warning(f"No channel ID for language: {lang}")
                        continue

                    logger.info(f"Sending tracking messages to {lang} channel")

                    for message in messages:
                        try:
                            translated = await translate_telegram_message(
                                message, model="gpt-5-nano",
                                from_lang="ko", to_lang=lang
                            )
                            await self._send_single_message(channel_id, translated)
                            logger.info(f"Message sent to {lang} channel")
                            await asyncio.sleep(1)
                        except Exception as e:
                            logger.error(f"Error sending to {lang}: {str(e)}")

                except Exception as e:
                    logger.error(f"Error processing language {lang}: {str(e)}")

        except Exception as e:
            logger.error(f"Error in send_to_translation_channels: {str(e)}")

    @staticmethod
    def _split_message(message: str) -> List[str]:
        """Split a long message into parts that fit Telegram limits."""
        parts = []
        current_part = ""

        for line in message.split('\n'):
            if len(current_part) + len(line) + 1 <= MAX_MESSAGE_LENGTH:
                current_part += line + '\n'
            else:
                if current_part:
                    parts.append(current_part.rstrip())
                current_part = line + '\n'

        if current_part:
            parts.append(current_part.rstrip())

        return parts
