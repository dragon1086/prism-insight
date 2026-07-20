"""Tests for Hardstop high-frequency hard-stop loop (tools/hardstop_seller.py).

Hardstop reuses the batch's sell path so the simulator, the real KIS account and
Telegram stay consistent. Safety-critical guards covered:
  - SHADOW (default): touches NO agent and places NO order, only logs.
  - LIVE: runs sell_stock (sim) -> async_sell_stock (KIS) -> send_telegram_message
    (telegram), in that order; reconciles qty against KIS first.
  - Pyramided tickers (>1 row) are skipped (batch owns fractional sells).
  - owner_lock exclusivity + inflight guard prevent double-selling.
  - TIER1-only: a winner is never sold by Hardstop.

Run in the KR (root) pytest session.
"""
import asyncio
import os
import sys
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
import tools.hardstop_seller as la  # noqa: E402


# ── Fakes ──────────────────────────────────────────────────────────────────────
class FakeTrader:
    def __init__(
        self,
        prices,
        holding_qty=None,
        sell_result=None,
        calls=None,
        holding_check=None,
    ):
        self._prices = prices
        self._holding_qty = holding_qty or {}
        self._sell_result = sell_result or {"success": True, "order_no": "ORD1", "message": "ok"}
        self.calls = calls if calls is not None else []
        self._holding_check = holding_check

    def get_current_price(self, ticker, exchange=None):
        return {"current_price": self._prices.get(ticker, 0)}

    def get_holding_quantity(self, ticker):
        return self._holding_qty.get(ticker, 0)

    def get_holding_quantity_checked(self, ticker):
        if self._holding_check is not None:
            return self._holding_check
        quantity = int(self._holding_qty.get(ticker, 0) or 0)
        return ("HELD", quantity) if quantity > 0 else ("FLAT", 0)

    async def async_sell_stock(self, ticker, exchange=None, timeout=30.0,
                               limit_price=None, use_moo=False, quantity=None):
        self.calls.append(f"kis:{ticker}:{quantity}")
        return self._sell_result


class FakeCtx:
    def __init__(self, trader):
        self._trader = trader

    async def __aenter__(self):
        return self._trader

    async def __aexit__(self, *a):
        return False


class FakeAgent:
    def __init__(self, calls):
        self.calls = calls
        self.conn = None

    async def sell_stock(self, stock_data, sell_reason, **kwargs):
        self.calls.append(f"sim:{stock_data.get('ticker')}")
        return True

    def _link_position_exit_intent(self, **kwargs):
        self.calls.append(f"link:{kwargs.get('legacy_holding_id')}")
        return True

    @staticmethod
    def _position_pending_kr_enabled():
        return os.getenv("POSITION_PENDING_KR_ENABLED", "false").lower() == "true"

    async def send_telegram_message(self, chat_id, language="ko", **kwargs):
        self.calls.append("tg")
        return True


class PendingFakeAgent(FakeAgent):
    def __init__(
        self,
        calls,
        *,
        status="SUBMITTED",
        failure=None,
        result_intent_id="intent-hardstop-1",
    ):
        super().__init__(calls)
        self.status = status
        self.failure = failure
        self.result_intent_id = result_intent_id
        self.prepared = SimpleNamespace(
            intent=SimpleNamespace(id="intent-hardstop-1")
        )
        self.prepare_kwargs = None

    def _prepare_pending_kr_exit(self, **kwargs):
        self.prepare_kwargs = kwargs
        self.calls.append(f"prepare:{kwargs.get('quantity')}")
        if self.failure == "prepare":
            raise RuntimeError("prepare failed")
        return self.prepared

    async def _execute_pending_kr_exit(self, prepared):
        self.calls.append("broker")
        if self.failure == "cancel":
            raise asyncio.CancelledError
        if self.failure == "outcome_unknown":
            from prism_core.execution_service import OrderOutcomeUnknown

            raise OrderOutcomeUnknown(
                prepared.intent.id,
                broker_result={"order_no": "UNCERTAIN-1"},
            )
        return {
            "success": self.status == "SUBMITTED",
            "intent_id": self.result_intent_id,
            "intent_status": self.status,
            "order_no": "ORDER-1",
        }

    async def _execute_pending_kr_local_flat_exit(self, prepared):
        self.calls.append("local-flat")
        return {
            "success": True,
            "local_flat": True,
            "quantity": 0,
            "intent_id": self.result_intent_id,
            "intent_status": self.status,
        }

    def _complete_pending_kr_exit(self, prepared):
        self.calls.append("complete")
        if self.failure == "finalize":
            raise RuntimeError("finalize failed")

    def _fail_pending_kr_exit(self, prepared):
        self.calls.append("fail")

    def _quarantine_pending_kr_exit(self, prepared):
        self.calls.append("quarantine")

    async def _run_pending_kr_exit_post_commit(self, prepared):
        self.calls.append("postcommit")

    async def _deliver_pending_kr_exit_publish_effects(
        self, prepared, trade_result
    ):
        self.calls.append("effects")
        return {"REDIS": "delivered", "GCP": "delivered"}


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db = tmp_path / "t.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE stock_holdings (id INTEGER PRIMARY KEY, ticker TEXT, company_name TEXT, "
        "buy_price REAL, buy_date TEXT, scenario TEXT, target_price REAL, stop_loss REAL, "
        "account_key TEXT, account_name TEXT)"
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(la, "DB_PATH", str(db))
    return str(db)


def _seed(db, rows):
    conn = sqlite3.connect(db)
    conn.executemany(
        "INSERT INTO stock_holdings (id, ticker, company_name, buy_price, buy_date, scenario, "
        "target_price, stop_loss, account_key, account_name) VALUES (?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()


def _row(id_, ticker, buy_price, stop_loss=0.0):
    return (id_, ticker, ticker, buy_price, "2026-06-01 10:00:00", "{}", 0.0,
            stop_loss, "acc1", "primary")


def _patch(monkeypatch, trader, agent_holder=None, make_agent_counter=None):
    from prism_core.execution_service import ExecutionService
    from prism_core.order_intents import IntentStore

    monkeypatch.setattr(
        la,
        "_open_context",
        lambda market, account_name=None: ExecutionService(
            FakeCtx(trader), intent_store=IntentStore(la.DB_PATH)
        ),
    )

    async def _fake_make_agent(market):
        if make_agent_counter is not None:
            make_agent_counter.append(1)
        return agent_holder

    monkeypatch.setattr(la, "_make_agent", _fake_make_agent)


def _inflight(db, status=None):
    conn = sqlite3.connect(db)
    try:
        if status:
            return conn.execute(
                "SELECT COUNT(*) FROM loop_a_inflight_orders WHERE status=?", (status,)
            ).fetchone()[0]
        return conn.execute("SELECT COUNT(*) FROM loop_a_inflight_orders").fetchone()[0]
    finally:
        conn.close()


def _owner_state(db):
    with sqlite3.connect(db) as conn:
        return conn.execute(
            "SELECT state FROM loop_a_position_state "
            "WHERE ticker='005930' AND market='KR'"
        ).fetchone()[0]


def _enable_pending_live(monkeypatch):
    monkeypatch.setenv("POSITION_PENDING_KR_ENABLED", "true")
    monkeypatch.setattr(la, "HARDSTOP_LIVE", True)
    monkeypatch.setattr(la, "HARDSTOP_ENABLED", True)


def test_shadow_touches_no_agent_and_no_order(tmp_db, monkeypatch):
    monkeypatch.setattr(la, "HARDSTOP_LIVE", False)
    monkeypatch.setattr(la, "HARDSTOP_ENABLED", True)
    _seed(tmp_db, [_row(1, "005930", 100.0)])  # buy 100, price 92 => -8% TIER1
    calls = []
    trader = FakeTrader({"005930": 92.0}, calls=calls)
    made = []
    _patch(monkeypatch, trader, agent_holder=FakeAgent(calls), make_agent_counter=made)

    summary = asyncio.run(la.run_market("KR", "run1"))

    assert summary["triggered"] == 1 and summary["shadow"] == 1
    assert made == []                 # agent NEVER created in shadow
    assert calls == []                # no sim, no kis, no telegram
    assert _inflight(tmp_db, "SHADOW") == 1


def test_live_order_is_sim_then_kis_then_telegram(tmp_db, monkeypatch):
    monkeypatch.setenv("POSITION_PENDING_KR_ENABLED", "false")
    monkeypatch.setattr(la, "HARDSTOP_LIVE", True)
    monkeypatch.setattr(la, "HARDSTOP_ENABLED", True)
    monkeypatch.setattr(la, "CHAT_ID", "chat1")
    _seed(tmp_db, [_row(1, "005930", 100.0)])
    calls = []
    trader = FakeTrader({"005930": 92.0}, holding_qty={"005930": 10}, calls=calls)
    _patch(monkeypatch, trader, agent_holder=FakeAgent(calls))

    async def publish_loop_sell(**_kwargs):
        calls.append("publish")

    monkeypatch.setattr("sell_broadcast.publish_loop_sell", publish_loop_sell)

    summary = asyncio.run(la.run_market("KR", "run1"))

    assert summary["sold"] == 1
    # per-sell flush + run-end flush each invoke send_telegram_message; the run-end
    # portfolio summary is de-duplicated (portfolio_broadcast) so only ONE actual
    # portfolio message goes out in prod (see tests/test_portfolio_broadcast.py).
    assert calls == [
        "sim:005930",
        "kis:005930:10",
        "link:1",
        "tg",
        "publish",
        "tg",
    ]  # exact order
    assert _inflight(tmp_db, "FILLED") == 1


def test_pending_kr_gate_does_not_change_us_legacy_path(tmp_db, monkeypatch):
    monkeypatch.setenv("POSITION_PENDING_KR_ENABLED", "true")
    monkeypatch.setattr(la, "HARDSTOP_LIVE", True)
    calls = []
    trader = FakeTrader({"AAPL": 92.0}, holding_qty={"AAPL": 4}, calls=calls)
    agent = FakeAgent(calls)
    _patch(monkeypatch, trader, agent_holder=agent)

    async def publish_loop_sell(**_kwargs):
        calls.append("publish")

    monkeypatch.setattr("sell_broadcast.publish_loop_sell", publish_loop_sell)
    stock = {
        "id": 1,
        "ticker": "AAPL",
        "company_name": "Apple",
        "buy_price": 100.0,
        "current_price": 92.0,
        "account_key": "us-acc",
        "account_name": "primary",
    }
    summary = {
        "market": "US",
        "checked": 1,
        "triggered": 1,
        "sold": 0,
        "shadow": 0,
        "skipped": 0,
        "pyramided_skipped": 0,
    }
    with sqlite3.connect(tmp_db) as connection:
        la._ensure_schema(connection)
        asyncio.run(
            la._act_on_trigger(
                connection,
                "US",
                "AAPL",
                stock,
                "hard stop",
                "run-us",
                {"ref": agent},
                summary,
            )
        )

    assert summary["sold"] == 1
    assert calls == ["sim:AAPL", "kis:AAPL:4", "link:1", "tg", "publish"]
    assert not any(call.startswith("prepare:") for call in calls)


@pytest.mark.parametrize(
    ("intent_status", "expected_calls", "inflight", "owner", "sold"),
    [
        (
            "SUBMITTED",
            [
                "prepare:7",
                "broker",
                "complete",
                "postcommit",
                "tg",
                "effects",
                "tg",
            ],
            "FILLED",
            "SOLD",
            1,
        ),
        ("FAILED", ["prepare:7", "broker", "fail"], "REJECTED", "HOLDING", 0),
        (
            "UNKNOWN",
            ["prepare:7", "broker", "quarantine"],
            "UNKNOWN",
            "QUARANTINED",
            0,
        ),
        (
            "SUBMITTING",
            ["prepare:7", "broker", "quarantine"],
            "UNKNOWN",
            "QUARANTINED",
            0,
        ),
        ("QUEUED", ["prepare:7", "broker"], "QUEUED", "QUARANTINED", 0),
    ],
)
def test_pending_kr_live_maps_intent_status_without_early_effects(
    tmp_db,
    monkeypatch,
    intent_status,
    expected_calls,
    inflight,
    owner,
    sold,
):
    _enable_pending_live(monkeypatch)
    _seed(tmp_db, [_row(1, "005930", 100.0)])
    calls = []
    trader = FakeTrader(
        {"005930": 92.0}, holding_check=("HELD", 7), calls=calls
    )
    pending_agent = PendingFakeAgent(calls, status=intent_status)
    _patch(monkeypatch, trader, agent_holder=pending_agent)

    async def publish_loop_sell(**_kwargs):
        calls.append("publish")

    monkeypatch.setattr("sell_broadcast.publish_loop_sell", publish_loop_sell)

    summary = asyncio.run(la.run_market("KR", f"run-{intent_status.lower()}"))

    assert summary["sold"] == sold
    assert calls == expected_calls
    assert not any(call.startswith("sim:") for call in calls)
    assert not any(call.startswith("link:") for call in calls)
    assert pending_agent.prepare_kwargs["order_style"] == "market"
    assert pending_agent.prepare_kwargs["limit_price"] is None
    assert _inflight(tmp_db, inflight) == 1
    assert _owner_state(tmp_db) == owner


def test_pending_kr_live_local_flat_closes_without_broker(tmp_db, monkeypatch):
    _enable_pending_live(monkeypatch)
    _seed(tmp_db, [_row(1, "005930", 100.0)])
    calls = []
    trader = FakeTrader(
        {"005930": 92.0}, holding_check=("FLAT", 0), calls=calls
    )
    pending_agent = PendingFakeAgent(calls, status="SUBMITTED")
    _patch(
        monkeypatch,
        trader,
        agent_holder=pending_agent,
    )

    async def publish_loop_sell(**_kwargs):
        calls.append("publish")

    monkeypatch.setattr("sell_broadcast.publish_loop_sell", publish_loop_sell)

    summary = asyncio.run(la.run_market("KR", "run-flat"))

    assert summary["sold"] == 1
    assert calls == [
        "prepare:None",
        "local-flat",
        "complete",
        "postcommit",
        "tg",
        "effects",
        "tg",
    ]
    assert "broker" not in calls
    assert pending_agent.prepare_kwargs["order_style"] == "market"
    assert pending_agent.prepare_kwargs["limit_price"] is None
    assert _inflight(tmp_db, "FILLED") == 1
    assert _owner_state(tmp_db) == "SOLD"


def test_pending_kr_unknown_balance_retries_without_prepare_or_effects(
    tmp_db, monkeypatch
):
    _enable_pending_live(monkeypatch)
    _seed(tmp_db, [_row(1, "005930", 100.0)])
    calls = []
    trader = FakeTrader(
        {"005930": 92.0}, holding_check=("UNKNOWN", None), calls=calls
    )
    _patch(monkeypatch, trader, agent_holder=PendingFakeAgent(calls))

    summary = asyncio.run(la.run_market("KR", "run-balance-unknown"))

    assert summary["sold"] == 0
    assert calls == []
    assert _inflight(tmp_db) == 0
    assert _owner_state(tmp_db) == "HOLDING"


def test_pending_kr_malformed_flat_quantity_never_closes(tmp_db, monkeypatch):
    _enable_pending_live(monkeypatch)
    _seed(tmp_db, [_row(1, "005930", 100.0)])
    calls = []
    trader = FakeTrader(
        {"005930": 92.0}, holding_check=("FLAT", 7), calls=calls
    )
    pending_agent = PendingFakeAgent(calls)
    _patch(monkeypatch, trader, agent_holder=pending_agent)

    summary = asyncio.run(la.run_market("KR", "run-malformed-flat"))

    assert summary["sold"] == 0
    assert calls == []
    assert pending_agent.prepare_kwargs is None
    assert _inflight(tmp_db) == 0
    assert _owner_state(tmp_db) == "HOLDING"


def test_pending_kr_prepare_failure_has_no_broker_or_effects(tmp_db, monkeypatch):
    _enable_pending_live(monkeypatch)
    _seed(tmp_db, [_row(1, "005930", 100.0)])
    calls = []
    trader = FakeTrader(
        {"005930": 92.0}, holding_check=("HELD", 7), calls=calls
    )
    _patch(
        monkeypatch,
        trader,
        agent_holder=PendingFakeAgent(calls, failure="prepare"),
    )

    summary = asyncio.run(la.run_market("KR", "run-prepare-failed"))

    assert summary["sold"] == 0
    assert calls == ["prepare:7"]
    assert _inflight(tmp_db) == 0
    assert _owner_state(tmp_db) == "HOLDING"


def test_pending_kr_mismatched_intent_id_quarantines_without_effects(
    tmp_db, monkeypatch
):
    _enable_pending_live(monkeypatch)
    _seed(tmp_db, [_row(1, "005930", 100.0)])
    calls = []
    trader = FakeTrader(
        {"005930": 92.0}, holding_check=("HELD", 7), calls=calls
    )
    _patch(
        monkeypatch,
        trader,
        agent_holder=PendingFakeAgent(
            calls, status="SUBMITTED", result_intent_id="foreign-intent"
        ),
    )

    summary = asyncio.run(la.run_market("KR", "run-intent-mismatch"))

    assert summary["sold"] == 0
    assert calls == ["prepare:7", "broker", "quarantine"]
    assert _inflight(tmp_db, "UNKNOWN") == 1
    assert _owner_state(tmp_db) == "QUARANTINED"


@pytest.mark.parametrize("failure", ["outcome_unknown", "finalize"])
def test_pending_kr_uncertain_exception_quarantines_without_effects(
    tmp_db, monkeypatch, failure
):
    _enable_pending_live(monkeypatch)
    _seed(tmp_db, [_row(1, "005930", 100.0)])
    calls = []
    trader = FakeTrader(
        {"005930": 92.0}, holding_check=("HELD", 7), calls=calls
    )
    _patch(
        monkeypatch,
        trader,
        agent_holder=PendingFakeAgent(calls, failure=failure),
    )

    summary = asyncio.run(la.run_market("KR", f"run-{failure}"))

    assert summary["sold"] == 0
    assert calls[-1] == "quarantine"
    assert "tg" not in calls and "publish" not in calls
    assert _inflight(tmp_db, "UNKNOWN") == 1
    assert _owner_state(tmp_db) == "QUARANTINED"


def test_pending_kr_non_dict_outcome_unknown_still_quarantines(
    tmp_db, monkeypatch
):
    from prism_core.execution_service import OrderOutcomeUnknown

    _enable_pending_live(monkeypatch)
    _seed(tmp_db, [_row(1, "005930", 100.0)])
    calls = []
    trader = FakeTrader(
        {"005930": 92.0}, holding_check=("HELD", 7), calls=calls
    )
    pending_agent = PendingFakeAgent(calls)

    async def raise_non_dict(_prepared):
        calls.append("broker")
        raise OrderOutcomeUnknown(
            "intent-hardstop-1", broker_result="opaque broker response"
        )

    pending_agent._execute_pending_kr_exit = raise_non_dict
    _patch(monkeypatch, trader, agent_holder=pending_agent)

    summary = asyncio.run(la.run_market("KR", "run-opaque-unknown"))

    assert summary["sold"] == 0
    assert calls == ["prepare:7", "broker", "quarantine"]
    assert _inflight(tmp_db, "UNKNOWN") == 1
    assert _owner_state(tmp_db) == "QUARANTINED"


def test_pending_kr_cancellation_quarantines_and_reraises(tmp_db, monkeypatch):
    _enable_pending_live(monkeypatch)
    _seed(tmp_db, [_row(1, "005930", 100.0)])
    calls = []
    trader = FakeTrader(
        {"005930": 92.0}, holding_check=("HELD", 7), calls=calls
    )
    _patch(
        monkeypatch,
        trader,
        agent_holder=PendingFakeAgent(calls, failure="cancel"),
    )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(la.run_market("KR", "run-cancel"))

    assert calls == ["prepare:7", "broker", "quarantine"]
    assert _inflight(tmp_db, "UNKNOWN") == 1
    assert _owner_state(tmp_db) == "QUARANTINED"


def test_pending_kr_quarantine_blocks_next_cycle_without_state_downgrade(
    tmp_db, monkeypatch
):
    _enable_pending_live(monkeypatch)
    _seed(tmp_db, [_row(1, "005930", 100.0)])
    calls = []
    trader = FakeTrader(
        {"005930": 92.0}, holding_check=("HELD", 7), calls=calls
    )
    pending_agent = PendingFakeAgent(calls, status="UNKNOWN")
    _patch(monkeypatch, trader, agent_holder=pending_agent)

    first = asyncio.run(la.run_market("KR", "run-unknown-first"))
    first_calls = list(calls)
    second = asyncio.run(la.run_market("KR", "run-unknown-second"))

    assert first["sold"] == 0 and second["sold"] == 0
    assert calls == first_calls
    assert _owner_state(tmp_db) == "QUARANTINED"
    assert _inflight(tmp_db, "UNKNOWN") == 1


def test_pending_kr_post_closed_cancellation_keeps_sold_state(
    tmp_db, monkeypatch
):
    _enable_pending_live(monkeypatch)
    _seed(tmp_db, [_row(1, "005930", 100.0)])
    calls = []
    trader = FakeTrader(
        {"005930": 92.0}, holding_check=("HELD", 7), calls=calls
    )
    pending_agent = PendingFakeAgent(calls, status="SUBMITTED")

    async def cancel_post_commit(_prepared):
        calls.append("postcommit")
        raise asyncio.CancelledError

    pending_agent._run_pending_kr_exit_post_commit = cancel_post_commit
    _patch(monkeypatch, trader, agent_holder=pending_agent)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(la.run_market("KR", "run-post-closed-cancel"))

    assert "quarantine" not in calls
    assert _inflight(tmp_db, "FILLED") == 1
    assert _owner_state(tmp_db) == "SOLD"


def test_ledger_failure_after_broker_success_records_unknown(tmp_db, monkeypatch):
    from prism_core.order_intents import IntentStore

    monkeypatch.setattr(la, "HARDSTOP_LIVE", True)
    monkeypatch.setattr(la, "HARDSTOP_ENABLED", True)
    _seed(tmp_db, [_row(1, "005930", 100.0)])
    calls = []
    trader = FakeTrader({"005930": 92.0}, holding_qty={"005930": 10}, calls=calls)
    _patch(monkeypatch, trader, agent_holder=FakeAgent(calls))

    def fail_result_persistence(*args, **kwargs):
        raise sqlite3.OperationalError("simulated ledger write failure")

    monkeypatch.setattr(IntentStore, "record_result", fail_result_persistence)

    summary = asyncio.run(la.run_market("KR", "run1"))

    assert summary["sold"] == 1
    assert _inflight(tmp_db, "UNKNOWN") == 1
    assert _inflight(tmp_db, "REJECTED") == 0


def test_timeout_result_records_unknown_inflight(tmp_db, monkeypatch):
    monkeypatch.setattr(la, "HARDSTOP_LIVE", True)
    monkeypatch.setattr(la, "HARDSTOP_ENABLED", True)
    _seed(tmp_db, [_row(1, "005930", 100.0)])
    calls = []
    trader = FakeTrader(
        {"005930": 92.0},
        holding_qty={"005930": 10},
        sell_result={"success": False, "message": "Sell request timeout (30s)"},
        calls=calls,
    )
    _patch(monkeypatch, trader, agent_holder=FakeAgent(calls))

    summary = asyncio.run(la.run_market("KR", "run1"))

    assert summary["sold"] == 1
    assert _inflight(tmp_db, "UNKNOWN") == 1
    assert _inflight(tmp_db, "REJECTED") == 0


def test_live_skips_kis_when_flat_but_still_closes_sim(tmp_db, monkeypatch):
    # KIS says qty 0 (batch already sold real) -> no KIS order, sim still closed.
    monkeypatch.setattr(la, "HARDSTOP_LIVE", True)
    monkeypatch.setattr(la, "HARDSTOP_ENABLED", True)
    _seed(tmp_db, [_row(1, "005930", 100.0)])
    calls = []
    trader = FakeTrader({"005930": 92.0}, holding_qty={"005930": 0}, calls=calls)
    _patch(monkeypatch, trader, agent_holder=FakeAgent(calls))

    asyncio.run(la.run_market("KR", "run1"))

    assert "sim:005930" in calls
    assert not any(c.startswith("kis:") for c in calls)   # no real order placed


def test_pyramided_ticker_is_skipped(tmp_db, monkeypatch):
    monkeypatch.setattr(la, "HARDSTOP_LIVE", False)
    monkeypatch.setattr(la, "HARDSTOP_ENABLED", True)
    _seed(tmp_db, [_row(1, "005930", 100.0), _row(2, "005930", 110.0)])  # 2 rows = pyramided
    calls = []
    trader = FakeTrader({"005930": 80.0}, calls=calls)  # deep loss, but must be skipped
    _patch(monkeypatch, trader, agent_holder=FakeAgent(calls))

    summary = asyncio.run(la.run_market("KR", "run1"))

    assert summary["pyramided_skipped"] == 1
    assert summary["checked"] == 0 and summary["triggered"] == 0
    assert calls == []


def test_shadow_record_does_not_block_second_trigger(tmp_db, monkeypatch):
    monkeypatch.setattr(la, "HARDSTOP_LIVE", False)
    monkeypatch.setattr(la, "HARDSTOP_ENABLED", True)
    _seed(tmp_db, [_row(1, "005930", 100.0)])
    calls = []
    trader = FakeTrader({"005930": 92.0}, calls=calls)
    _patch(monkeypatch, trader, agent_holder=FakeAgent(calls))

    asyncio.run(la.run_market("KR", "run1"))
    summary2 = asyncio.run(la.run_market("KR", "run2"))

    assert summary2["skipped"] == 0
    assert summary2["shadow"] == 1
    assert _inflight(tmp_db) == 2


def test_owner_lock_is_exclusive(tmp_db):
    conn = la._connect()
    la._ensure_schema(conn)
    assert la.claim_lock(conn, "005930", "KR", "runA") is True
    assert la.claim_lock(conn, "005930", "KR", "runB") is False
    la.release_lock(conn, "005930", "KR", "runA")
    assert la.claim_lock(conn, "005930", "KR", "runB") is True
    conn.close()


def test_winner_not_sold_tier1_only(tmp_db, monkeypatch):
    monkeypatch.setattr(la, "HARDSTOP_LIVE", False)
    monkeypatch.setattr(la, "HARDSTOP_ENABLED", True)
    _seed(tmp_db, [_row(1, "005930", 100.0)])
    calls = []
    trader = FakeTrader({"005930": 120.0}, calls=calls)  # +20% winner
    _patch(monkeypatch, trader, agent_holder=FakeAgent(calls))

    summary = asyncio.run(la.run_market("KR", "run1"))

    assert summary["triggered"] == 0
    assert calls == []


def test_disabled_flag_is_noop(tmp_db, monkeypatch):
    monkeypatch.setattr(la, "HARDSTOP_ENABLED", False)
    rc = asyncio.run(la.main_async(["KR"]))
    assert rc == 0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
