import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram.error import BadRequest, NetworkError, RetryAfter, TelegramError, TimedOut


ROOT = Path(__file__).resolve().parents[1]


def tracking_sender(path, name='_send_with_retry'):
    tree = ast.parse((ROOT / path).read_text())
    method = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == name)
    import asyncio
    import logging
    namespace = dict(asyncio=asyncio, logger=logging.getLogger(__name__), RetryAfter=RetryAfter,
                     TimedOut=TimedOut, NetworkError=NetworkError, TelegramError=TelegramError,
                     require_execution_runtime=lambda _agent: None)
    exec(compile(ast.Module(body=[method], type_ignores=[]), path, 'exec'), namespace)
    return namespace[name]


@pytest.mark.asyncio
@pytest.mark.parametrize('path', ['stock_tracking_agent.py', 'prism-us/us_stock_tracking_agent.py'])
@pytest.mark.parametrize('error', [TimedOut(), NetworkError('secret transport detail')])
async def test_tracking_never_retries_unknown_delivery(path, error, monkeypatch):
    monkeypatch.setattr('asyncio.sleep', AsyncMock())
    bot = SimpleNamespace(send_message=AsyncMock(side_effect=error))
    with pytest.raises(Exception):
        await tracking_sender(path)(SimpleNamespace(telegram_bot=bot), 'private-chat', 'private-message')
    assert bot.send_message.await_count == 1
    assert bot.send_message.call_args.kwargs['read_timeout'] == 30


@pytest.mark.asyncio
@pytest.mark.parametrize('path', ['stock_tracking_agent.py', 'prism-us/us_stock_tracking_agent.py'])
async def test_tracking_retries_explicit_rate_rejection(path, monkeypatch):
    monkeypatch.setattr('asyncio.sleep', AsyncMock())
    bot = SimpleNamespace(send_message=AsyncMock(side_effect=[RetryAfter(1), SimpleNamespace(message_id=1)]))
    assert (await tracking_sender(path)(SimpleNamespace(telegram_bot=bot), 'chat', 'message')).message_id == 1
    assert bot.send_message.await_count == 2


@pytest.mark.asyncio
async def test_common_bot_unknown_is_not_success_or_retried(monkeypatch, caplog):
    from telegram_bot_agent import TelegramBotAgent
    monkeypatch.setattr('asyncio.sleep', AsyncMock())
    agent = TelegramBotAgent.__new__(TelegramBotAgent)
    agent.bot = SimpleNamespace(send_message=AsyncMock(side_effect=TimedOut('private-secret')))
    assert await agent.send_message('private-chat', 'private-message') is False
    assert agent.bot.send_message.await_count == 1
    assert 'UNKNOWN' in caplog.text
    assert 'private-secret' not in caplog.text


@pytest.mark.asyncio
async def test_explicit_parse_rejection_may_fallback_but_unknown_must_stop(monkeypatch, caplog):
    from telegram_bot_agent import TelegramBotAgent
    monkeypatch.setattr('asyncio.sleep', AsyncMock())
    agent = TelegramBotAgent.__new__(TelegramBotAgent)
    agent.bot = SimpleNamespace(send_message=AsyncMock(side_effect=[BadRequest("can't parse entities"), TimedOut('private-secret')]))
    assert await agent.send_message('private-chat', 'private-message') is False
    assert agent.bot.send_message.await_count == 2
    assert 'UNKNOWN' in caplog.text
    assert 'private-secret' not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize('method', ['send_document', 'send_photo_bytes'])
async def test_media_unknown_does_not_auto_resend(method, tmp_path, monkeypatch):
    from telegram_bot_agent import TelegramBotAgent
    monkeypatch.setattr('asyncio.sleep', AsyncMock())
    agent = TelegramBotAgent.__new__(TelegramBotAgent)
    call = AsyncMock(side_effect=TimedOut())
    agent.bot = SimpleNamespace(send_document=call, send_photo=call)
    # This test module itself is an existing readable file, never sent over network.
    argument = __file__ if method == 'send_document' else b'test'
    assert await getattr(agent, method)('chat', argument) is False
    assert call.await_count == 1


def test_applied_adjustment_distinguishes_ai_original_from_db_result():
    source = (ROOT / 'stock_tracking_enhanced_agent.py').read_text()
    assert '처리 상태: 프로그램이 위 변경을 전략 원장 DB에 반영했습니다.' in source
    assert 'AI 제안 근거: {adjustment_reason}' in source


@pytest.mark.asyncio
async def test_directory_unknown_is_quarantined_across_new_agent(tmp_path, monkeypatch):
    from telegram_bot_agent import TelegramBotAgent
    monkeypatch.setattr('asyncio.sleep', AsyncMock())
    message_file = tmp_path / 'notice_telegram.txt'
    message_file.write_text('notice')
    bot = SimpleNamespace(send_message=AsyncMock(side_effect=TimedOut()))
    for _ in range(2):
        agent = TelegramBotAgent.__new__(TelegramBotAgent)
        agent.bot = bot
        assert await agent.process_messages_directory(tmp_path, 'chat') == 0
    assert bot.send_message.await_count == 1
    assert not message_file.exists()
    quarantined = list(tmp_path.glob('notice_telegram.txt.*.delivery_unknown'))
    assert len(quarantined) == 1
    assert quarantined[0].read_text() == 'notice'
    assert not list(tmp_path.glob('*_sent*'))


@pytest.mark.asyncio
@pytest.mark.parametrize('error', [BadRequest('rejected'), RetryAfter(1)])
async def test_us_partial_delivery_is_reported_unknown(monkeypatch, caplog, error):
    monkeypatch.setattr('asyncio.sleep', AsyncMock())
    monkeypatch.setattr('portfolio_broadcast.should_send_portfolio', lambda *_a, **_k: False)
    send = AsyncMock(side_effect=[SimpleNamespace(message_id=1), error])
    agent = SimpleNamespace(message_queue=['x' * 2500 + '\n' + 'y' * 2500],
                            _msg_types=['analysis'], telegram_bot=object(), _send_with_retry=send)
    result = await tracking_sender('prism-us/us_stock_tracking_agent.py', 'send_telegram_message')(agent, 'chat')
    assert result is False
    assert send.await_count == 2
    assert 'TELEGRAM_DELIVERY_UNKNOWN' in caplog.text


@pytest.mark.asyncio
async def test_directory_cancelled_claim_is_not_automatically_retried(tmp_path, monkeypatch):
    import asyncio
    from telegram_bot_agent import TelegramBotAgent
    monkeypatch.setattr('asyncio.sleep', AsyncMock())
    (tmp_path / 'notice_telegram.txt').write_text('notice')
    agent = TelegramBotAgent.__new__(TelegramBotAgent)
    agent.bot = SimpleNamespace(send_message=AsyncMock(side_effect=asyncio.CancelledError()))
    with pytest.raises(asyncio.CancelledError):
        await agent.process_messages_directory(tmp_path, 'chat')
    assert await agent.process_messages_directory(tmp_path, 'chat') == 0
    assert agent.bot.send_message.await_count == 1
    assert len(list(tmp_path.glob('*.sending'))) == 1


@pytest.mark.asyncio
async def test_directory_explicit_rejection_can_be_retried(tmp_path, monkeypatch):
    from telegram_bot_agent import TelegramBotAgent
    monkeypatch.setattr('asyncio.sleep', AsyncMock())
    message_file = tmp_path / 'notice_telegram.txt'
    message_file.write_text('notice')
    agent = TelegramBotAgent.__new__(TelegramBotAgent)
    agent.bot = SimpleNamespace(send_message=AsyncMock(side_effect=BadRequest('rejected')))
    assert await agent.process_messages_directory(tmp_path, 'chat') == 0
    assert message_file.read_text() == 'notice'
    assert not list(tmp_path.glob('*.sending'))


@pytest.mark.asyncio
@pytest.mark.parametrize('move_sent', [False, True])
async def test_directory_confirmed_success_keeps_existing_sent_behavior(tmp_path, monkeypatch, move_sent):
    from telegram_bot_agent import TelegramBotAgent
    monkeypatch.setattr('asyncio.sleep', AsyncMock())
    monkeypatch.setattr('firebase_bridge.notify', AsyncMock())
    message_file = tmp_path / 'notice_telegram.txt'
    message_file.write_text('notice')
    agent = TelegramBotAgent.__new__(TelegramBotAgent)
    agent.bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=1)))
    sent_dir = tmp_path / 'sent' if move_sent else None
    assert await agent.process_messages_directory(tmp_path, 'chat', sent_dir=sent_dir) == 1
    assert await agent.process_messages_directory(tmp_path, 'chat', sent_dir=sent_dir) == 0
    assert agent.bot.send_message.await_count == 1
    sent_file = sent_dir / message_file.name if move_sent else tmp_path / 'notice_telegram_sent.txt'
    assert sent_file.read_text() == 'notice'
    assert not list(tmp_path.glob('*.sending'))
    assert not list(tmp_path.glob('*.delivery_unknown'))
