"""2026-10-01: gpt-6 Chat Completions calls failed (max_tokens / max_output_tokens 400).

The journal saved an "Analysis parsing failed" placeholder and broadcast
translations went out empty. Both now use the Responses LLM like the trading
agents, and an empty answer is never persisted or sent.
"""
import asyncio
import sqlite3

import pytest

import cores.agents.trading_journal_agent as journal_agent_module
import cores.llm.openai_responses_llm as responses_module
from tracking.db_schema import (
    TABLE_TRADING_JOURNAL,
    migrate_trading_journal_exit_intent,
)
from tracking.journal import JournalManager


class FakeLLM:
    def __init__(self, reply):
        self.reply = reply

    async def generate_str(self, message, request_params=None):
        return self.reply


class FakeAgent:
    def __init__(self, reply, attached):
        self.reply, self.attached = reply, attached

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def attach_llm(self, llm_class):
        self.attached.append(llm_class)
        return FakeLLM(self.reply)


def _manager():
    connection = sqlite3.connect(":memory:")
    connection.execute(TABLE_TRADING_JOURNAL)
    migrate_trading_journal_exit_intent(connection.cursor(), connection)
    return connection, JournalManager(cursor=connection.cursor(), conn=connection, enable_journal=True)


@pytest.mark.parametrize("reply", ["", "   "])
def test_empty_journal_response_is_not_saved_as_placeholder(monkeypatch, reply):
    attached = []
    monkeypatch.delenv("LLM_BACKEND", raising=False)
    monkeypatch.setattr(journal_agent_module, "create_trading_journal_agent",
                        lambda *a, **kw: FakeAgent(reply, attached))
    connection, manager = _manager()

    created = asyncio.run(manager.create_entry(
        stock_data={"ticker": "327260", "company_name": "RF머트리얼즈", "buy_price": 51500,
                    "buy_date": "2026-09-30 10:08:00", "scenario": "{}"},
        sell_price=48500, profit_rate=-5.83, holding_days=0, sell_reason="TIER1_STOPLOSS",
    ))

    assert created is False
    assert attached == [responses_module.OpenAIResponsesLLM]
    assert connection.execute("SELECT COUNT(*) FROM trading_journal").fetchone()[0] == 0


def _patch_translator(monkeypatch, reply):
    import cores.agents.telegram_translator_agent as translator

    attached = []
    monkeypatch.setattr(translator, "create_telegram_translator_agent",
                        lambda **kw: FakeAgent(reply, attached))
    return translator, attached


def test_translator_uses_responses_llm(monkeypatch):
    translator, attached = _patch_translator(monkeypatch, " Stop-loss sell \n")
    assert asyncio.run(translator.translate_telegram_message("손절 매도")) == "Stop-loss sell"
    assert attached == [responses_module.OpenAIResponsesLLM]


def test_empty_translation_never_becomes_an_empty_broadcast(monkeypatch):
    translator, _ = _patch_translator(monkeypatch, "")
    assert asyncio.run(translator.translate_telegram_message("손절 매도")) == "손절 매도"
    with pytest.raises(ValueError):
        asyncio.run(translator.translate_telegram_message("손절 매도", raise_on_error=True))
