import asyncio
import sqlite3
from types import SimpleNamespace

import pytest
from telegram.error import TelegramError
from telegram.error import TimedOut, NetworkError
from telegram.error import BadRequest, RetryAfter

from prism_core.exit_effects import ExitEffectStore
from stock_tracking_agent import StockTrackingAgent


INTENT_ID = "intent-telegram-1"


def _seed(path):
    with sqlite3.connect(path) as connection:
        store = ExitEffectStore(connection)
        store.ensure_schema()
        connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        store.enqueue_exit_effects(
            intent_id=INTENT_ID,
            market="KR",
            account_id="vps:kr-primary:01",
            symbol="005930",
            source="kr_batch",
            payload={"event_id": INTENT_ID, "message": "sold"},
        )
        connection.commit()


def _telegram_row(path):
    with sqlite3.connect(path) as connection:
        return ExitEffectStore(connection).list_for_intent(INTENT_ID)[1]


def _agent(path):
    agent = StockTrackingAgent.__new__(StockTrackingAgent)
    agent.db_path = str(path)
    agent.message_queue = []
    agent._msg_types = []
    agent._msg_effect_ids = []
    agent._broadcast_task = None
    agent.telegram_bot = object()

    async def summary():
        return "portfolio"

    agent.generate_report_summary = summary
    agent._schedule_firebase = lambda *_args, **_kwargs: asyncio.create_task(
        asyncio.sleep(0)
    )
    return agent


@pytest.mark.asyncio
async def test_pending_exit_telegram_effect_completes_after_real_send(
    monkeypatch, tmp_path
):
    db_path = tmp_path / "telegram.sqlite"
    _seed(db_path)
    agent = _agent(db_path)
    sent = []

    async def send(chat_id, text):
        sent.append((chat_id, text))
        return SimpleNamespace(message_id=731)

    agent._send_with_retry = send
    agent._queue_message(
        "sold",
        "analysis",
        effect_id=f"{INTENT_ID}:telegram",
    )
    monkeypatch.setattr(
        "portfolio_broadcast.should_send_portfolio", lambda *_a, **_k: False
    )

    result = await agent.send_telegram_message("channel-1")
    row = _telegram_row(db_path)

    assert result is True
    assert sent == [("channel-1", f"sold\n\n[exit-event: {INTENT_ID}]")]
    assert row["status"] == "DELIVERED"
    assert row["remote_id"] == "731"
    assert agent.message_queue == []
    assert agent._msg_effect_ids == []


@pytest.mark.asyncio
@pytest.mark.parametrize('error', [BadRequest('rejected'), RetryAfter(1)])
async def test_partial_exit_delivery_cannot_replay_entire_message(monkeypatch, tmp_path, error):
    db_path = tmp_path / 'partial.sqlite'
    _seed(db_path)
    agent = _agent(db_path)
    calls = []

    async def send(chat_id, text):
        calls.append(text)
        if len(calls) == 1:
            return SimpleNamespace(message_id=123)
        raise error

    agent._send_with_retry = send
    monkeypatch.setattr('portfolio_broadcast.should_send_portfolio', lambda *_a, **_k: False)
    message = 'x' * 2500 + '\n' + 'y' * 2500
    agent._queue_message(message, 'analysis', effect_id=f'{INTENT_ID}:telegram')
    assert await agent.send_telegram_message('chat') is False
    assert _telegram_row(db_path)['status'] == 'DEAD'
    assert _telegram_row(db_path)['last_error'] == 'TelegramDeliveryUnknown'
    agent._queue_message(message, 'analysis', effect_id=f'{INTENT_ID}:telegram')
    assert await agent.send_telegram_message('chat') is False
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_missing_chat_id_does_not_complete_telegram_effect(tmp_path):
    db_path = tmp_path / "telegram-no-chat.sqlite"
    _seed(db_path)
    agent = _agent(db_path)
    agent._queue_message(
        "sold",
        "analysis",
        effect_id=f"{INTENT_ID}:telegram",
    )

    result = await agent.send_telegram_message(None)

    assert result is True
    assert _telegram_row(db_path)["status"] == "PENDING"
    assert agent._msg_effect_ids == []


@pytest.mark.asyncio
async def test_telegram_error_reschedules_only_telegram_effect(monkeypatch, tmp_path):
    db_path = tmp_path / "telegram-fail.sqlite"
    _seed(db_path)
    agent = _agent(db_path)

    async def fail(**_kwargs):
        raise TelegramError("transport detail")

    agent._send_with_retry = fail
    agent._queue_message(
        "sold",
        "analysis",
        effect_id=f"{INTENT_ID}:telegram",
    )
    monkeypatch.setattr(
        "portfolio_broadcast.should_send_portfolio", lambda *_a, **_k: False
    )

    result = await agent.send_telegram_message("channel-1")
    with sqlite3.connect(db_path) as connection:
        rows = ExitEffectStore(connection).list_for_intent(INTENT_ID)

    assert result is False
    assert rows[1]["status"] == "PENDING"
    assert rows[1]["last_error"] == "TelegramError"
    assert all(row["status"] == "PENDING" for row in (rows[0], rows[2], rows[3]))


@pytest.mark.asyncio
@pytest.mark.parametrize('error', [TimedOut('secret'), NetworkError('secret'), TimeoutError()])
async def test_ambiguous_telegram_is_quarantined_not_replayed(tmp_path, error):
    from prism_core.exit_effect_replay import deliver_exit_effect_once
    from datetime import datetime, timedelta, timezone
    db_path = tmp_path / 'unknown.sqlite'
    _seed(db_path)
    calls = 0

    async def send(_payload):
        nonlocal calls
        calls += 1
        raise error

    now = datetime.now(timezone.utc)
    first = await deliver_exit_effect_once(db_path, effect_id=f'{INTENT_ID}:telegram',
        effect_type='TELEGRAM', handler=send, owner='first', now=lambda: now)
    second = await deliver_exit_effect_once(db_path, effect_id=f'{INTENT_ID}:telegram',
        effect_type='TELEGRAM', handler=send, owner='second', now=lambda: now + timedelta(days=1))
    assert first.status == 'dead'
    assert second.status == 'not_ready'
    assert calls == 1
    row = _telegram_row(db_path)
    assert row['last_error'] == 'TelegramDeliveryUnknown'
    assert row['remote_id'] is None


@pytest.mark.parametrize('batch', [False, True])
def test_expired_telegram_lease_is_not_permission_to_resend(tmp_path, batch):
    from datetime import datetime, timedelta, timezone
    db_path = tmp_path / 'crash.sqlite'
    _seed(db_path)
    now = datetime.now(timezone.utc)
    with sqlite3.connect(db_path) as connection:
        store = ExitEffectStore(connection)
        connection.execute('BEGIN IMMEDIATE')
        assert store.claim_effect(effect_id=f'{INTENT_ID}:telegram', owner='crashed', now=now)
        connection.commit()
        connection.execute('BEGIN IMMEDIATE')
        if batch:
            assert store.claim_ready_effects(owner='new', limit=1, effect_types=['TELEGRAM'], now=now + timedelta(days=1)) == []
        else:
            assert store.claim_effect(effect_id=f'{INTENT_ID}:telegram', owner='new', now=now + timedelta(days=1)) is None
        connection.commit()
    assert _telegram_row(db_path)['last_error'] == 'TelegramDeliveryUnknown'
    assert _telegram_row(db_path)['status'] == 'DEAD'


@pytest.mark.asyncio
async def test_portfolio_delivery_log_contains_type_and_message_id(
    monkeypatch, tmp_path, caplog
):
    agent = _agent(tmp_path / "portfolio-log.sqlite")

    async def send(chat_id, text):
        assert chat_id == "channel-1"
        assert text == "portfolio"
        return SimpleNamespace(message_id=842)

    agent._send_with_retry = send
    monkeypatch.setattr(
        "portfolio_broadcast.should_send_portfolio", lambda *_a, **_k: True
    )

    with caplog.at_level("INFO"):
        result = await agent.send_telegram_message(
            "channel-1", portfolio_force=True
        )

    assert result is True
    assert "type=portfolio" in caplog.text
    assert "message_id=842" in caplog.text
