from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram.ext import CommandHandler, ConversationHandler

from telegram_ai_bot import TelegramAIBot


class RecordingApplication:
    def __init__(self):
        self.handlers = []

    def add_handler(self, handler, group=0):
        self.handlers.append((group, handler))

    def add_error_handler(self, callback):
        self.error_handler = callback


def test_report_commands_share_one_reentrant_conversation_handler():
    bot = object.__new__(TelegramAIBot)
    bot.application = RecordingApplication()

    bot.setup_handlers()

    report_conversations = []
    for _, handler in bot.application.handlers:
        if not isinstance(handler, ConversationHandler):
            continue
        commands = {
            command
            for entry_point in handler.entry_points
            if isinstance(entry_point, CommandHandler)
            for command in entry_point.commands
        }
        if commands & {"report", "us_report"}:
            report_conversations.append((handler, commands))

    assert len(report_conversations) == 1
    handler, commands = report_conversations[0]
    assert {"report", "us_report"} <= commands
    assert handler.allow_reentry is True


@pytest.mark.asyncio
async def test_domestic_report_state_routes_us_ticker_to_us_report_handler():
    bot = object.__new__(TelegramAIBot)
    bot.get_stock_code = AsyncMock(
        return_value=(None, None, "국내 종목을 찾을 수 없습니다.")
    )
    bot.handle_us_report_ticker_input = AsyncMock(
        return_value=ConversationHandler.END
    )
    update = SimpleNamespace(
        message=SimpleNamespace(text="IONQ", reply_text=AsyncMock()),
        effective_user=SimpleNamespace(id=123),
        effective_chat=SimpleNamespace(id=456),
    )

    result = await TelegramAIBot.handle_report_ticker_input(
        bot, update, SimpleNamespace()
    )

    assert result == ConversationHandler.END
    bot.handle_us_report_ticker_input.assert_awaited_once()
    update.message.reply_text.assert_not_awaited()
