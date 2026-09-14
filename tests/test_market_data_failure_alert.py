from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from telegram_config import send_market_data_failure_alert


@pytest.mark.asyncio
async def test_kr_data_failure_names_only_the_active_provider(monkeypatch):
    bot = SimpleNamespace(send_message=AsyncMock())
    monkeypatch.setattr('telegram.Bot', lambda **kwargs: bot)
    config = SimpleNamespace(use_telegram=True, bot_token='test', channel_id='test')
    await send_market_data_failure_alert(config, 'morning')
    message = bot.send_message.await_args.kwargs['text']
    assert 'KIS' in message
    assert 'KRX' not in message
    assert '네이버' not in message
    assert 'NAVER' not in message
    assert '누락' in message


@pytest.mark.asyncio
async def test_disabled_alert_does_not_create_transport(monkeypatch):
    def forbidden(**kwargs):
        pytest.fail('disabled alert created transport')
    monkeypatch.setattr('telegram.Bot', forbidden)
    await send_market_data_failure_alert(SimpleNamespace(use_telegram=False), 'morning')
