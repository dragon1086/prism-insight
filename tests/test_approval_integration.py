"""End-to-end tests for the approval/ → trading/ wire-up.

These exercise `trading/approval_integration.py` — the glue that converts
Buy/Sell Specialist signals into TradeProposals, dispatches via
ApprovalManager, and (on approve) routes through a single executor that
calls KIS and republishes the downstream Redis/GCP signals.

KIS itself is replaced with `tests/mock_kis_server.py` so the tests
exercise the real code path end-to-end without credentials. The
`AsyncTradingContext` is monkey-patched to a lightweight stub so we
don't need pandas/PyKRX or a real KIS account.

Telegram is mocked at the Bot/CallbackQuery level (same FakeBot/
FakeCallbackQuery stubs used in test_approval_handler.py).
"""
from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

# Patch the keyboard builder so neither approval.message nor approval.handler
# imports the real `telegram` package. handler.py imports the symbol at
# module-load time, so both bindings need replacement.
import approval.message as _msg_mod  # noqa: E402
_msg_mod.build_approval_keyboard = lambda proposal: None  # type: ignore
import approval.handler as _h_mod  # noqa: E402
_h_mod.build_approval_keyboard = lambda proposal: None  # type: ignore

from approval.handler import ApprovalManager  # noqa: E402
from approval.message import build_callback_data, ACTION_APPROVE  # noqa: E402
from approval.models import ApprovalDecision, TradeSide  # noqa: E402

from trading import approval_integration as appr  # noqa: E402


# ---------------------------------------------------------------- stubs


@dataclass
class FakeMessage:
    message_id: int = 200


@dataclass
class FakeUser:
    id: int = 42


@dataclass
class FakeBot:
    sent: List[dict] = field(default_factory=list)
    edited: List[dict] = field(default_factory=list)
    _msg_id: int = 200

    async def send_message(self, chat_id, text, reply_markup=None, parse_mode=None, **kw):
        self._msg_id += 1
        self.sent.append({"chat_id": chat_id, "text": text})
        return FakeMessage(message_id=self._msg_id)

    async def edit_message_text(self, chat_id, message_id, text, **kw):
        self.edited.append({"chat_id": chat_id, "message_id": message_id, "text": text})


@dataclass
class FakeCallbackQuery:
    data: str
    bot: FakeBot
    chat_id: int = 99
    message_id: int = 201
    from_user: FakeUser = field(default_factory=FakeUser)
    answered: List[Any] = field(default_factory=list)
    edits: List[str] = field(default_factory=list)

    async def answer(self, text: Optional[str] = None, show_alert: bool = False):
        self.answered.append({"text": text, "alert": show_alert})

    async def edit_message_text(self, text: str, **kw):
        self.edits.append(text)


class FakeTradingContext:
    """Stub AsyncTradingContext that records the call and returns canned KIS dicts."""

    last_calls: List[Dict[str, Any]] = []

    def __init__(self, account_name: Optional[str] = None, **_):
        self.account_name = account_name

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def async_buy_stock(self, *, stock_code: str, limit_price: int):
        FakeTradingContext.last_calls.append(
            {"side": "BUY", "ticker": stock_code, "price": limit_price,
             "account": self.account_name}
        )
        return {
            "success": True, "order_no": f"BUY-{stock_code}-001",
            "quantity": 5, "fill_price": limit_price,
            "message": f"market buy completed ({stock_code})",
        }

    async def async_sell_stock(self, *, stock_code: str, limit_price: int):
        FakeTradingContext.last_calls.append(
            {"side": "SELL", "ticker": stock_code, "price": limit_price,
             "account": self.account_name}
        )
        return {
            "success": True, "order_no": f"SELL-{stock_code}-001",
            "quantity": 5, "fill_price": limit_price,
            "message": f"market sell completed ({stock_code})",
        }


# ---------------------------------------------------------------- fixtures


@pytest.fixture(autouse=True)
def _isolate_singletons(tmp_path, monkeypatch):
    """Each test gets a fresh ApprovalStore and ApprovalManager singleton."""
    monkeypatch.setenv("APPROVAL_DB_PATH", str(tmp_path / "approvals.db"))
    monkeypatch.setenv("ENABLE_TRADE_APPROVAL", "true")
    monkeypatch.setenv("APPROVAL_TIMEOUT_SECONDS", "3600")
    monkeypatch.delenv("AUTO_STOP_LOSS_BYPASS", raising=False)
    appr.reset_for_tests()
    yield
    appr.reset_for_tests()


@pytest.fixture
def fake_trading_module(monkeypatch):
    """Patch `from trading.domestic_stock_trading import AsyncTradingContext`."""
    FakeTradingContext.last_calls.clear()
    # The integration module does a lazy `import` inside _kis_executor, so we
    # need to install the stub in sys.modules before it's resolved.
    fake_module = type(sys)("trading.domestic_stock_trading")
    fake_module.AsyncTradingContext = FakeTradingContext  # type: ignore
    monkeypatch.setitem(sys.modules, "trading.domestic_stock_trading", fake_module)
    return FakeTradingContext


@pytest.fixture
def bot():
    return FakeBot()


# ---------------------------------------------------------------- tests


def test_is_enabled_respects_env(monkeypatch):
    monkeypatch.setenv("ENABLE_TRADE_APPROVAL", "true")
    assert appr.is_enabled() is True
    monkeypatch.setenv("ENABLE_TRADE_APPROVAL", "false")
    assert appr.is_enabled() is False
    monkeypatch.delenv("ENABLE_TRADE_APPROVAL", raising=False)
    assert appr.is_enabled() is False


def test_build_buy_proposal_captures_scenario_and_account():
    proposal = appr.build_buy_proposal(
        ticker="005930", company_name="삼성전자", current_price=70_000,
        scenario={"buy_score": 82, "stop_loss": 66_500, "target_price": 78_000,
                  "buy_amount_krw": 500_000, "rationale": "거래량 폭증"},
        rank_change_msg="rank ↑5", account_name="primary",
    )
    assert proposal.ticker == "005930"
    assert proposal.side == TradeSide.BUY
    assert proposal.entry_price == 70_000
    assert proposal.proposed_amount_krw == 500_000
    assert proposal.stop_loss == 66_500
    assert proposal.score == 82
    assert proposal.rationale[0] == "rank ↑5"
    assert proposal.metadata["account_name"] == "primary"
    assert proposal.metadata["scenario"]["buy_score"] == 82
    assert proposal.auto_execute is False  # buys never bypass


def test_build_sell_proposal_marks_stop_loss_for_auto_execute():
    proposal = appr.build_sell_proposal(
        ticker="005930", company_name="삼성전자", current_price=66_000,
        sell_reason="손절 임계 도달", buy_price=70_000, is_stop_loss=True,
        holding_qty=10, account_name="primary",
    )
    assert proposal.side == TradeSide.SELL
    assert proposal.auto_execute is True  # stop-loss path
    assert proposal.trigger_type == "stop_loss"
    assert proposal.proposed_amount_krw == 660_000
    assert proposal.metadata["holding_qty"] == 10
    assert proposal.metadata["buy_price"] == 70_000


@pytest.mark.asyncio
async def test_request_buy_then_approve_calls_kis(fake_trading_module, bot, monkeypatch):
    proposal = await appr.request_buy_approval(
        bot, chat_id=99,
        ticker="005930", company_name="삼성전자",
        current_price=70_000,
        scenario={"buy_score": 82, "buy_amount_krw": 500_000},
        account_name="primary",
    )
    assert proposal is not None
    assert len(bot.sent) == 1
    assert "매수 승인 요청" in bot.sent[0]["text"]
    # Stub the signal publishers so no Redis/GCP module is imported.
    monkeypatch.setattr(appr, "_publish_signal",
                        lambda *a, **kw: asyncio.sleep(0))

    query = FakeCallbackQuery(
        data=build_callback_data(ACTION_APPROVE, proposal.short_id()), bot=bot,
    )
    await appr.get_manager().handle_callback(query)

    # The fake executor was called with the right account + ticker.
    assert len(FakeTradingContext.last_calls) == 1
    call = FakeTradingContext.last_calls[0]
    assert call == {"side": "BUY", "ticker": "005930", "price": 70_000, "account": "primary"}

    # SQLite store reflects APPROVED + order_no.
    record = appr.get_store().get(proposal.approval_id)
    assert record.decision == ApprovalDecision.APPROVED.value
    assert record.order_no == "BUY-005930-001"


@pytest.mark.asyncio
async def test_request_sell_then_approve_calls_kis(fake_trading_module, bot, monkeypatch):
    proposal = await appr.request_sell_approval(
        bot, chat_id=99,
        ticker="005930", company_name="삼성전자",
        current_price=72_000, sell_reason="목표가 도달",
        buy_price=70_000, holding_qty=10, account_name="primary",
    )
    assert proposal is not None
    monkeypatch.setattr(appr, "_publish_signal",
                        lambda *a, **kw: asyncio.sleep(0))

    query = FakeCallbackQuery(
        data=build_callback_data(ACTION_APPROVE, proposal.short_id()), bot=bot,
    )
    await appr.get_manager().handle_callback(query)

    assert FakeTradingContext.last_calls[0]["side"] == "SELL"
    assert FakeTradingContext.last_calls[0]["ticker"] == "005930"


@pytest.mark.asyncio
async def test_request_returns_none_when_disabled(monkeypatch, fake_trading_module, bot):
    monkeypatch.setenv("ENABLE_TRADE_APPROVAL", "false")
    appr.reset_for_tests()
    proposal = await appr.request_buy_approval(
        bot, chat_id=99,
        ticker="005930", company_name="삼성전자",
        current_price=70_000, scenario={"buy_score": 82},
        account_name="primary",
    )
    assert proposal is None
    assert bot.sent == []
    assert FakeTradingContext.last_calls == []


@pytest.mark.asyncio
async def test_auto_stop_loss_bypasses_telegram(monkeypatch, fake_trading_module, bot):
    monkeypatch.setenv("AUTO_STOP_LOSS_BYPASS", "true")
    appr.reset_for_tests()
    monkeypatch.setattr(appr, "_publish_signal",
                        lambda *a, **kw: asyncio.sleep(0))

    proposal = await appr.request_sell_approval(
        bot, chat_id=99,
        ticker="005930", company_name="삼성전자",
        current_price=64_000, sell_reason="-8% 손절",
        buy_price=70_000, is_stop_loss=True, holding_qty=10,
        account_name="primary",
    )
    # No Telegram message sent — bypass took the auto-execute path.
    assert bot.sent == []
    # Executor fired synchronously.
    assert FakeTradingContext.last_calls[0]["side"] == "SELL"

    record = appr.get_store().get(proposal.approval_id)
    assert record.decision == ApprovalDecision.AUTO_EXECUTED.value


@pytest.mark.asyncio
async def test_telegram_callback_handler_dispatches_to_manager(monkeypatch, fake_trading_module, bot):
    """`telegram_callback_handler` is the entry point registered with
    CallbackQueryHandler(pattern=r"^apv:") — it must forward to the singleton
    manager and tolerate the python-telegram-bot update/context shape."""
    proposal = await appr.request_buy_approval(
        bot, chat_id=99,
        ticker="005930", company_name="삼성전자",
        current_price=70_000, scenario={"buy_score": 82},
        account_name="primary",
    )
    assert proposal is not None
    monkeypatch.setattr(appr, "_publish_signal",
                        lambda *a, **kw: asyncio.sleep(0))

    query = FakeCallbackQuery(
        data=build_callback_data(ACTION_APPROVE, proposal.short_id()), bot=bot,
    )
    # python-telegram-bot passes Update + ContextTypes.DEFAULT_TYPE — only
    # `update.callback_query` is read.
    update = type("Update", (), {"callback_query": query})()
    context = type("Context", (), {})()
    await appr.telegram_callback_handler(update, context)

    record = appr.get_store().get(proposal.approval_id)
    assert record.decision == ApprovalDecision.APPROVED.value


@pytest.mark.asyncio
async def test_telegram_retry_handler_resubmits_with_new_amount(monkeypatch, fake_trading_module, bot):
    """`telegram_retry_handler` parses `/retry_<id> <amount>` and triggers a
    fresh approval round with the user-supplied amount."""
    monkeypatch.setattr(appr, "_publish_signal",
                        lambda *a, **kw: asyncio.sleep(0))

    original = await appr.request_buy_approval(
        bot, chat_id=99,
        ticker="005930", company_name="삼성전자",
        current_price=70_000,
        scenario={"buy_score": 82, "buy_amount_krw": 500_000},
        account_name="primary",
    )
    assert original is not None

    # User taps 📝 (modify) — must stash the proposal in the manager.
    from approval.message import ACTION_MODIFY
    modify_query = FakeCallbackQuery(
        data=build_callback_data(ACTION_MODIFY, original.short_id()), bot=bot,
    )
    update = type("Update", (), {"callback_query": modify_query})()
    context = type("Context", (), {})()
    await appr.telegram_callback_handler(update, context)

    # Simulate user typing `/retry_<short_id> 300,000`
    @dataclass
    class FakeChat:
        id: int = 99

    @dataclass
    class FakeReply:
        replies: List[str] = field(default_factory=list)

        async def reply_text(self, text: str, **kw):
            self.replies.append(text)

    msg = type("Msg", (), {})()
    msg.text = f"/retry_{original.short_id()} 300,000"
    msg.chat = FakeChat()
    replies: List[str] = []

    async def _reply(text, **kw):
        replies.append(text)
    msg.reply_text = _reply
    msg.get_bot = lambda: bot

    update_msg = type("UpdateMsg", (), {"message": msg})()
    context_msg = type("Ctx", (), {"bot": bot})()
    sent_before = len(bot.sent)
    await appr.telegram_retry_handler(update_msg, context_msg)

    # A fresh approval card was sent; no error reply.
    assert len(bot.sent) == sent_before + 1
    assert replies == []
    # The new proposal in SQLite has the retried amount.
    pendings = appr.get_store().list_pending()
    assert len(pendings) == 1
    assert pendings[0].proposed_amount_krw == 300_000


@pytest.mark.asyncio
async def test_telegram_retry_handler_missing_amount_replies_with_usage(monkeypatch, bot):
    """No amount → polite usage hint, no new approval card."""
    msg = type("Msg", (), {})()
    msg.text = "/retry_abcdef012345"
    msg.chat = type("C", (), {"id": 99})()
    replies: List[str] = []

    async def _reply(text, **kw):
        replies.append(text)
    msg.reply_text = _reply
    msg.get_bot = lambda: bot

    update_msg = type("UpdateMsg", (), {"message": msg})()
    context_msg = type("Ctx", (), {"bot": bot})()
    await appr.telegram_retry_handler(update_msg, context_msg)

    assert any("금액" in r for r in replies)
    assert bot.sent == []


@pytest.mark.asyncio
async def test_telegram_retry_handler_unknown_id_reports_expired(monkeypatch, bot):
    """Unknown short_id → 'not found / expired' message, no crash."""
    msg = type("Msg", (), {})()
    msg.text = "/retry_deadbeefcafe 500000"
    msg.chat = type("C", (), {"id": 99})()
    replies: List[str] = []

    async def _reply(text, **kw):
        replies.append(text)
    msg.reply_text = _reply
    msg.get_bot = lambda: bot

    update_msg = type("UpdateMsg", (), {"message": msg})()
    context_msg = type("Ctx", (), {"bot": bot})()
    await appr.telegram_retry_handler(update_msg, context_msg)

    assert any("만료" in r or "찾을 수 없" in r for r in replies)
    assert bot.sent == []


@pytest.mark.asyncio
async def test_executor_publishes_signals_after_kis_call(fake_trading_module, bot):
    """When `_kis_executor` runs successfully, it calls the Redis + GCP
    publishers (best-effort, swallows exceptions)."""
    publish_calls: List[str] = []

    async def fake_publish(proposal, result, metadata):
        publish_calls.append(f"{proposal.side.value}:{proposal.ticker}")

    import trading.approval_integration as m
    m._publish_signal = fake_publish  # type: ignore

    proposal = await appr.request_buy_approval(
        bot, chat_id=99,
        ticker="005930", company_name="삼성전자",
        current_price=70_000, scenario={"buy_score": 82},
        account_name="primary",
    )
    query = FakeCallbackQuery(
        data=build_callback_data(ACTION_APPROVE, proposal.short_id()), bot=bot,
    )
    await appr.get_manager().handle_callback(query)

    assert publish_calls == ["BUY:005930"]
