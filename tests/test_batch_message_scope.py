from unittest.mock import AsyncMock

import pytest

from telegram_bot_agent import TelegramBotAgent


@pytest.mark.asyncio
async def test_batch_sends_only_selected_paths(tmp_path, monkeypatch):
    monkeypatch.setattr('asyncio.sleep', AsyncMock())
    pending = tmp_path / 'pending'
    pending.mkdir()
    selected = pending / 'new_telegram.txt'
    stale = pending / 'old_telegram.txt'
    outside = tmp_path / 'outside_telegram.txt'
    selected.write_text('new')
    stale.write_text('old')
    outside.write_text('outside')
    link = pending / 'link_telegram.txt'
    link.symlink_to(outside)
    agent = TelegramBotAgent.__new__(TelegramBotAgent)
    agent.send_message = AsyncMock(return_value=True)
    assert await agent.process_messages_directory(
        pending, 'test', sent_dir=tmp_path / 'sent',
        message_paths=[selected, selected, outside, link],
    ) == 1
    assert agent.send_message.await_count == 1
    assert agent.send_message.call_args.args[1] == 'new'
    assert stale.exists() and outside.exists() and link.is_symlink()


@pytest.mark.asyncio
async def test_explicit_empty_batch_does_not_drain_queue(tmp_path):
    stale = tmp_path / 'old_telegram.txt'
    stale.write_text('old')
    agent = TelegramBotAgent.__new__(TelegramBotAgent)
    agent.send_message = AsyncMock()
    assert await agent.process_messages_directory(tmp_path, 'test', message_paths=[]) == 0
    agent.send_message.assert_not_called()
    assert stale.exists()
