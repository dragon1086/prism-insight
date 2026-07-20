import asyncio
import sqlite3
from types import SimpleNamespace

import pytest
from telegram.error import TelegramError

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
