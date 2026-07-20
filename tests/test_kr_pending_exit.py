import asyncio
import sqlite3
import sys
import threading
import types
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

import trading.domestic_stock_trading as domestic_trading
from prism_core.execution_service import OrderOutcomeUnknown
from prism_core.exit_effects import EXIT_EFFECT_TYPES, ExitEffectStore
from prism_core.order_intents import IntentStore
from prism_core.positions import InvalidPositionTransition, PositionStore
from stock_tracking_agent import StockTrackingAgent
from tracking.db_schema import TABLE_STOCK_HOLDINGS, TABLE_TRADING_HISTORY


ACCOUNT_ID = "vps:kr-primary:01"
TICKER = "005930"


def _pending_exit_agent(db_path: Path):
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute(TABLE_STOCK_HOLDINGS)
    connection.execute(TABLE_TRADING_HISTORY)
    PositionStore(connection).ensure_schema()
    ExitEffectStore(connection).ensure_schema()
    connection.commit()
    IntentStore(db_path)

    agent = StockTrackingAgent.__new__(StockTrackingAgent)
    agent.db_path = str(db_path)
    agent.conn = connection
    agent.cursor = connection.cursor()
    agent.account_configs = [
        {"name": "kr-primary", "account_key": ACCOUNT_ID}
    ]
    agent.active_account = None
    agent.message_queue = []
    agent._msg_types = []
    agent.position_ledger_shadow_enabled = True
    agent._position_pending_kr_ready = True
    agent._get_trigger_win_rate = lambda _trigger: ""
    return agent, connection


def _insert_open_holding(
    connection: sqlite3.Connection,
    *,
    buy_price: float = 70000,
    buy_date: str = "2026-07-01 09:00:00",
) -> dict:
    cursor = connection.execute(
        """
        INSERT INTO stock_holdings
        (account_key, account_name, ticker, company_name, buy_price, buy_date,
         current_price, scenario, trigger_type, trigger_mode, sector)
        VALUES (?, 'kr-primary', ?, 'Samsung Electronics', ?, ?, 72000,
                '{}', 'AI Analysis', 'live', 'Technology')
        """,
        (ACCOUNT_ID, TICKER, buy_price, buy_date),
    )
    legacy_holding_id = int(cursor.lastrowid)
    PositionStore(connection).open_legacy_position(
        market="KR",
        legacy_holding_id=legacy_holding_id,
        account_id=ACCOUNT_ID,
        account_name="kr-primary",
        symbol=TICKER,
        entry_price=buy_price,
        opened_at=buy_date,
    )
    connection.commit()
    row = connection.execute(
        "SELECT * FROM stock_holdings WHERE id=?", (legacy_holding_id,)
    ).fetchone()
    return dict(row)


def _prepare(agent: StockTrackingAgent, stock: dict, **kwargs):
    return agent._prepare_pending_kr_exit(
        stock_data={**stock, "current_price": 72000},
        sell_reason="risk exit",
        exit_kind="stop",
        source="kr_batch",
        quantity=3,
        **kwargs,
    )


def _set_intent_status(db_path: Path, intent_id: str, status: str) -> None:
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE order_intents SET status=? WHERE id=?", (status, intent_id)
        )


def _state(db_path: Path, intent_id: str) -> tuple[int, int, str, str]:
    with sqlite3.connect(db_path) as connection:
        holdings = connection.execute(
            "SELECT COUNT(*) FROM stock_holdings WHERE ticker=?", (TICKER,)
        ).fetchone()[0]
        history = connection.execute(
            "SELECT COUNT(*) FROM trading_history WHERE ticker=?", (TICKER,)
        ).fetchone()[0]
        intent_status = connection.execute(
            "SELECT status FROM order_intents WHERE id=?", (intent_id,)
        ).fetchone()[0]
        position_status = connection.execute(
            "SELECT status FROM positions WHERE exit_intent_id=?", (intent_id,)
        ).fetchone()[0]
    return holdings, history, intent_status, position_status


def _effects(connection: sqlite3.Connection, intent_id: str) -> list[dict]:
    return ExitEffectStore(connection).list_for_intent(intent_id)


def test_prepare_persists_created_pending_exit_without_external_effects(
    monkeypatch, tmp_path
):
    db_path = tmp_path / "prepare.sqlite"
    agent, connection = _pending_exit_agent(db_path)
    stock = _insert_open_holding(connection)
    journal_calls = []
    broker_context_calls = []

    def unexpected_broker_context(**kwargs):
        broker_context_calls.append(kwargs)
        raise AssertionError("prepare must not enter the broker context")

    monkeypatch.setattr(
        "stock_tracking_agent.ExecutionService.domestic",
        unexpected_broker_context,
    )

    async def journal(**kwargs):
        journal_calls.append(kwargs)

    agent._create_journal_entry = journal
    try:
        prepared = _prepare(agent, stock)
        state = _state(db_path, prepared.intent.id)
        source_position_id = connection.execute(
            "SELECT source_position_id FROM order_intents WHERE id=?",
            (prepared.intent.id,),
        ).fetchone()[0]
        broker_order_count = connection.execute(
            "SELECT COUNT(*) FROM broker_orders WHERE intent_id=?",
            (prepared.intent.id,),
        ).fetchone()[0]
    finally:
        connection.close()

    assert state == (1, 0, "CREATED", "PENDING_EXIT")
    assert source_position_id == f"legacy:KR:{stock['id']}"
    assert prepared.intent_store is not None
    assert prepared.reservation["id"] == prepared.intent.id
    assert prepared.order_style == "smart"
    assert prepared.limit_price == 72000
    assert broker_order_count == 0
    assert broker_context_calls == []
    assert agent.message_queue == []
    assert journal_calls == []


@pytest.mark.parametrize(
    ("order_style", "limit_price", "expected_limit"),
    [("smart", "default", 72000), ("market", None, None)],
)
@pytest.mark.asyncio
async def test_pending_exit_preserves_smart_and_market_broker_contracts(
    monkeypatch, tmp_path, order_style, limit_price, expected_limit
):
    db_path = tmp_path / f"{order_style}-order.sqlite"
    agent, connection = _pending_exit_agent(db_path)
    stock = _insert_open_holding(connection)
    calls = []

    class ExecutionContext:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def execute_pre_reserved_sell(self, **kwargs):
            calls.append(kwargs)
            return {"success": True, "intent_status": "SUBMITTED"}

    monkeypatch.setattr(
        "stock_tracking_agent.ExecutionService.domestic",
        lambda **_kwargs: ExecutionContext(),
    )
    try:
        prepare_kwargs = {"order_style": order_style}
        if limit_price != "default":
            prepare_kwargs["limit_price"] = limit_price
        prepared = _prepare(agent, stock, **prepare_kwargs)
        result = await agent._execute_pending_kr_exit(prepared)
    finally:
        connection.close()

    assert result["intent_status"] == "SUBMITTED"
    assert prepared.order_style == prepared.intent.order_style == order_style
    assert prepared.limit_price == expected_limit
    if expected_limit is None:
        assert prepared.intent.limit_price is None
    else:
        assert float(prepared.intent.limit_price) == expected_limit
    assert len(calls) == 1
    if expected_limit is None:
        assert "limit_price" not in calls[0]
    else:
        assert calls[0]["limit_price"] == expected_limit


def test_explicit_failed_exit_reopens_without_legacy_effects(tmp_path):
    db_path = tmp_path / "failed.sqlite"
    agent, connection = _pending_exit_agent(db_path)
    stock = _insert_open_holding(connection)
    try:
        prepared = _prepare(agent, stock)
        _set_intent_status(db_path, prepared.intent.id, "FAILED")
        agent._fail_pending_kr_exit(prepared)
        state = _state(db_path, prepared.intent.id)
    finally:
        connection.close()

    assert state == (1, 0, "FAILED", "OPEN")
    assert agent.message_queue == []


@pytest.mark.parametrize("intent_status", ["UNKNOWN", "QUEUED", "SUBMITTING"])
def test_uncertain_exit_is_quarantined_without_legacy_effects(
    tmp_path, intent_status
):
    db_path = tmp_path / f"quarantine-{intent_status.lower()}.sqlite"
    agent, connection = _pending_exit_agent(db_path)
    stock = _insert_open_holding(connection)
    try:
        prepared = _prepare(agent, stock)
        _set_intent_status(db_path, prepared.intent.id, intent_status)
        agent._quarantine_pending_kr_exit(prepared)
        state = _state(db_path, prepared.intent.id)
    finally:
        connection.close()

    assert state == (1, 0, intent_status, "EXIT_UNKNOWN")
    assert agent.message_queue == []


def test_complete_exit_rolls_back_legacy_writes_when_position_finalize_fails(
    monkeypatch, tmp_path
):
    db_path = tmp_path / "complete-rollback.sqlite"
    agent, connection = _pending_exit_agent(db_path)
    stock = _insert_open_holding(connection)
    prepared = _prepare(agent, stock)
    _set_intent_status(db_path, prepared.intent.id, "SUBMITTED")

    def fail_complete(_store, **_kwargs):
        raise sqlite3.OperationalError("injected CLOSED finalize failure")

    monkeypatch.setattr(PositionStore, "complete_exit_many", fail_complete)
    try:
        with pytest.raises(sqlite3.OperationalError):
            agent._complete_pending_kr_exit(prepared)
        state = _state(db_path, prepared.intent.id)
        effects = _effects(connection, prepared.intent.id)
    finally:
        connection.close()

    assert state == (1, 0, "SUBMITTED", "PENDING_EXIT")
    assert effects == []
    assert agent.message_queue == []


def test_complete_exit_rolls_back_closed_state_when_effect_enqueue_fails(
    monkeypatch, tmp_path
):
    db_path = tmp_path / "complete-outbox-rollback.sqlite"
    agent, connection = _pending_exit_agent(db_path)
    stock = _insert_open_holding(connection)
    prepared = _prepare(agent, stock)
    _set_intent_status(db_path, prepared.intent.id, "SUBMITTED")

    def fail_enqueue(_store, **_kwargs):
        raise sqlite3.OperationalError("injected effect enqueue failure")

    monkeypatch.setattr(ExitEffectStore, "enqueue_exit_effects", fail_enqueue)
    try:
        with pytest.raises(sqlite3.OperationalError):
            agent._complete_pending_kr_exit(prepared)
        state = _state(db_path, prepared.intent.id)
        effects = _effects(connection, prepared.intent.id)
    finally:
        connection.close()

    assert state == (1, 0, "SUBMITTED", "PENDING_EXIT")
    assert effects == []
    assert agent.message_queue == []


def test_complete_exit_deletes_only_target_pyramid_row_and_queues_message(tmp_path):
    db_path = tmp_path / "complete-pyramid.sqlite"
    agent, connection = _pending_exit_agent(db_path)
    target = _insert_open_holding(connection, buy_price=70000)
    sibling = _insert_open_holding(connection, buy_price=68000)
    try:
        prepared = _prepare(agent, target)
        _set_intent_status(db_path, prepared.intent.id, "SUBMITTED")
        agent._complete_pending_kr_exit(prepared)
        remaining_ids = [
            row[0]
            for row in connection.execute(
                "SELECT id FROM stock_holdings ORDER BY id"
            ).fetchall()
        ]
        history = connection.execute(
            "SELECT buy_price, sell_price, exit_kind FROM trading_history"
        ).fetchone()
        state = connection.execute(
            "SELECT status FROM positions WHERE id=?",
            (f"legacy:KR:{target['id']}",),
        ).fetchone()[0]
        effects = _effects(connection, prepared.intent.id)
    finally:
        connection.close()

    assert remaining_ids == [sibling["id"]]
    assert tuple(history) == (70000, 72000, "stop")
    assert state == "CLOSED"
    assert [effect["effect_type"] for effect in effects] == list(EXIT_EFFECT_TYPES)
    assert {effect["status"] for effect in effects} == {"PENDING"}
    assert all(
        effect["payload"]["event_id"] == prepared.intent.id for effect in effects
    )
    assert len(agent.message_queue) == 1
    assert agent._msg_types == ["analysis"]


def test_prepare_blocks_when_same_symbol_sibling_is_unresolved(tmp_path):
    db_path = tmp_path / "sibling-guard.sqlite"
    agent, connection = _pending_exit_agent(db_path)
    target = _insert_open_holding(connection, buy_price=70000)
    sibling = _insert_open_holding(connection, buy_price=68000)
    connection.execute(
        "UPDATE positions SET status='EXIT_UNKNOWN' WHERE id=?",
        (f"legacy:KR:{sibling['id']}",),
    )
    connection.commit()
    try:
        with pytest.raises(InvalidPositionTransition, match="exit attempt"):
            _prepare(agent, target)
        counts = connection.execute(
            "SELECT COUNT(*) FROM order_intents"
        ).fetchone()[0]
    finally:
        connection.close()

    assert counts == 0
    assert agent.message_queue == []


def test_concurrent_prepare_claims_exact_position_once(tmp_path):
    db_path = tmp_path / "concurrent-prepare.sqlite"
    _setup_agent, setup_connection = _pending_exit_agent(db_path)
    stock = _insert_open_holding(setup_connection)
    setup_connection.close()
    barrier = threading.Barrier(2)

    def contender():
        connection = sqlite3.connect(db_path, timeout=2)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=2000")
        agent = StockTrackingAgent.__new__(StockTrackingAgent)
        agent.db_path = str(db_path)
        agent.conn = connection
        agent.cursor = connection.cursor()
        agent.message_queue = []
        agent._msg_types = []
        agent.position_ledger_shadow_enabled = True
        agent._position_pending_kr_ready = True
        agent._account_scope = lambda: (ACCOUNT_ID, "kr-primary")
        agent._get_trigger_win_rate = lambda _trigger: ""
        try:
            barrier.wait(timeout=2)
            prepared = _prepare(agent, stock)
            return "claimed", prepared.intent.id
        except Exception as error:
            return "blocked", type(error).__name__
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: contender(), range(2)))

    with sqlite3.connect(db_path) as verify:
        intent_count = verify.execute(
            "SELECT COUNT(*) FROM order_intents"
        ).fetchone()[0]
        pending_count = verify.execute(
            "SELECT COUNT(*) FROM positions WHERE status='PENDING_EXIT'"
        ).fetchone()[0]

    assert sorted(result[0] for result in results) == ["blocked", "claimed"]
    assert intent_count == 1
    assert pending_count == 1


def test_pre_broker_created_exit_remains_pending_for_manual_review(tmp_path):
    db_path = tmp_path / "created-cancel.sqlite"
    agent, connection = _pending_exit_agent(db_path)
    stock = _insert_open_holding(connection)
    try:
        prepared = _prepare(agent, stock)
        with pytest.raises(InvalidPositionTransition, match="requires one of"):
            agent._quarantine_pending_kr_exit(prepared)
        state = _state(db_path, prepared.intent.id)
    finally:
        connection.close()

    assert state == (1, 0, "CREATED", "PENDING_EXIT")
    assert agent.message_queue == []


@pytest.mark.asyncio
async def test_post_commit_effects_create_journal_and_run_closed_hook(tmp_path):
    db_path = tmp_path / "post-commit.sqlite"
    agent, connection = _pending_exit_agent(db_path)
    stock = _insert_open_holding(connection)
    prepared = _prepare(agent, stock)
    _set_intent_status(db_path, prepared.intent.id, "SUBMITTED")
    journal_states = []
    hook_states = []

    async def journal(**_kwargs):
        journal_states.append(_state(db_path, prepared.intent.id))

    async def hook(value):
        hook_states.append((value, _state(db_path, prepared.intent.id)))

    agent._create_journal_entry = journal
    agent._after_pending_kr_exit_closed = hook
    try:
        agent._complete_pending_kr_exit(prepared)
        await agent._run_pending_kr_exit_post_commit(prepared)
    finally:
        connection.close()

    assert journal_states == [(0, 1, "SUBMITTED", "CLOSED")]
    assert hook_states == [(prepared, (0, 1, "SUBMITTED", "CLOSED"))]


@pytest.mark.asyncio
async def test_post_commit_effects_reject_pending_position_before_journal(tmp_path):
    db_path = tmp_path / "premature-post-commit.sqlite"
    agent, connection = _pending_exit_agent(db_path)
    stock = _insert_open_holding(connection)
    prepared = _prepare(agent, stock)
    journal_calls = []

    async def journal(**kwargs):
        journal_calls.append(kwargs)

    agent._create_journal_entry = journal
    try:
        with pytest.raises(RuntimeError, match="durable CLOSED"):
            await agent._run_pending_kr_exit_post_commit(prepared)
    finally:
        connection.close()

    assert journal_calls == []


@pytest.mark.asyncio
async def test_publish_effects_complete_independently_from_remote_message_ids(
    monkeypatch, tmp_path
):
    db_path = tmp_path / "publish-effects.sqlite"
    agent, connection = _pending_exit_agent(db_path)
    stock = _insert_open_holding(connection)
    prepared = _prepare(agent, stock)
    _set_intent_status(db_path, prepared.intent.id, "SUBMITTED")
    calls = []

    async def redis_publish(**kwargs):
        calls.append(("REDIS", kwargs))
        return "redis-message-1"

    async def gcp_publish(**kwargs):
        calls.append(("GCP", kwargs))
        return None

    monkeypatch.setattr(
        "messaging.redis_signal_publisher.publish_sell_signal", redis_publish
    )
    monkeypatch.setattr(
        "messaging.gcp_pubsub_signal_publisher.publish_sell_signal", gcp_publish
    )
    try:
        agent._complete_pending_kr_exit(prepared)
        outcomes = await agent._deliver_pending_kr_exit_publish_effects(
            prepared,
            {"success": True, "message": "submitted"},
        )
        rows = {
            row["effect_type"]: row
            for row in ExitEffectStore(connection).list_for_intent(
                prepared.intent.id
            )
        }
    finally:
        connection.close()

    assert outcomes == {"REDIS": "delivered", "GCP": "rescheduled"}
    assert rows["REDIS"]["status"] == "DELIVERED"
    assert rows["REDIS"]["remote_id"] == "redis-message-1"
    assert rows["GCP"]["status"] == "PENDING"
    assert rows["GCP"]["last_error"] == "DeliveryNotConfirmed"
    assert [effect for effect, _kwargs in calls] == ["REDIS", "GCP"]
    assert all(
        kwargs["event_id"] == prepared.intent.id for _effect, kwargs in calls
    )


def _batch_agent(db_path: Path, *, rows: int = 1):
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute(TABLE_STOCK_HOLDINGS)
    for index in range(rows):
        connection.execute(
            """
            INSERT INTO stock_holdings
            (account_key, account_name, ticker, company_name, buy_price, buy_date,
             current_price, scenario, trigger_type, trigger_mode, sector)
            VALUES (?, 'kr-primary', ?, 'Samsung Electronics', ?,
                    '2026-07-01 09:00:00', 71000, '{}', 'AI Analysis',
                    'live', 'Technology')
            """,
            (ACCOUNT_ID, TICKER, 70000 - index * 1000),
        )
    connection.commit()

    agent = StockTrackingAgent.__new__(StockTrackingAgent)
    agent.db_path = str(db_path)
    agent.conn = connection
    agent.cursor = connection.cursor()
    agent.active_account = {
        "name": "kr-primary",
        "account_key": ACCOUNT_ID,
    }
    agent.message_queue = []
    agent._msg_types = []
    agent._get_live_regime_safe = lambda: None

    async def current_price(_ticker):
        return 72000

    async def sell_decision(_stock):
        return True, "risk exit"

    agent._get_current_stock_price = current_price
    agent._analyze_sell_decision = sell_decision
    return agent, connection


def _install_batch_runtime(
    monkeypatch,
    *,
    agent,
    checked_result=("HELD", 10),
    intent_status="SUBMITTED",
    execute_error: BaseException | None = None,
    complete_error: BaseException | None = None,
    prepare_error: BaseException | None = None,
):
    events = []
    prepared_quantities = []
    checked_calls = []
    broker_calls = []

    class CheckedContext:
        def __init__(self, account_name=None, **_kwargs):
            self.account_name = account_name

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def get_holding_quantity_checked(self, ticker):
            checked_calls.append(ticker)
            return checked_result

        def get_holding_quantity(self, _ticker):
            raise AssertionError("gate=true must use the checked quantity API")

        async def async_sell_stock(self, *args, **kwargs):
            broker_calls.append((args, kwargs))
            raise AssertionError("stubbed pending helper owns broker execution")

    monkeypatch.setattr(domestic_trading, "AsyncTradingContext", CheckedContext)
    monkeypatch.setenv("POSITION_PENDING_KR_ENABLED", "true")

    corporate_module = types.ModuleType("cores.corporate_status")

    async def fetch_status_codes(*_args, **_kwargs):
        return {}

    corporate_module.fetch_status_codes = fetch_status_codes
    monkeypatch.setitem(sys.modules, "cores.corporate_status", corporate_module)

    def prepare(**kwargs):
        events.append("prepare")
        prepared_quantities.append(kwargs["quantity"])
        if prepare_error is not None:
            raise prepare_error
        return SimpleNamespace(
            symbol=kwargs["stock_data"]["ticker"],
            intent=SimpleNamespace(id="intent-1"),
            sell_price=kwargs["stock_data"]["current_price"],
            profit_rate=(
                (kwargs["stock_data"]["current_price"] - kwargs["stock_data"]["buy_price"])
                / kwargs["stock_data"]["buy_price"]
                * 100
            ),
        )

    async def execute(prepared):
        events.append("execute")
        if execute_error is not None:
            raise execute_error
        return {
            "success": intent_status == "SUBMITTED",
            "intent_id": prepared.intent.id,
            "intent_status": intent_status,
            "message": intent_status.lower(),
        }

    async def local_flat(prepared):
        events.append("local-flat")
        return {
            "success": True,
            "local_flat": True,
            "intent_id": prepared.intent.id,
            "intent_status": "SUBMITTED",
            "message": "local flat",
        }

    def complete(_prepared):
        events.append("complete")
        if complete_error is not None:
            raise complete_error

    def fail(_prepared):
        events.append("fail")

    def quarantine(_prepared):
        events.append("quarantine")

    async def post_commit(_prepared):
        events.append("post-commit")

    agent._prepare_pending_kr_exit = prepare
    agent._execute_pending_kr_exit = execute
    agent._execute_pending_kr_local_flat_exit = local_flat
    agent._complete_pending_kr_exit = complete
    agent._fail_pending_kr_exit = fail
    agent._quarantine_pending_kr_exit = quarantine
    agent._run_pending_kr_exit_post_commit = post_commit

    redis_module = types.ModuleType("messaging.redis_signal_publisher")
    gcp_module = types.ModuleType("messaging.gcp_pubsub_signal_publisher")

    async def publish_redis(**_kwargs):
        events.append("redis")

    async def publish_gcp(**_kwargs):
        events.append("gcp")

    redis_module.publish_sell_signal = publish_redis
    gcp_module.publish_sell_signal = publish_gcp

    async def publish_effects(_prepared, _trade_result):
        await publish_redis()
        await publish_gcp()

    agent._deliver_pending_kr_exit_publish_effects = publish_effects
    monkeypatch.setitem(sys.modules, "messaging.redis_signal_publisher", redis_module)
    monkeypatch.setitem(
        sys.modules, "messaging.gcp_pubsub_signal_publisher", gcp_module
    )
    return events, prepared_quantities, checked_calls, broker_calls


@pytest.mark.asyncio
async def test_batch_pending_exit_submitted_completes_before_publish(
    monkeypatch, tmp_path
):
    agent, connection = _batch_agent(tmp_path / "batch-submitted.sqlite")
    events, quantities, checked, direct_broker = _install_batch_runtime(
        monkeypatch, agent=agent
    )
    try:
        sold = await agent.update_holdings()
    finally:
        connection.close()

    assert [item["ticker"] for item in sold] == [TICKER]
    assert quantities == [None]
    assert checked == [TICKER]
    assert direct_broker == []
    assert events == [
        "prepare",
        "execute",
        "complete",
        "post-commit",
        "redis",
        "gcp",
    ]


@pytest.mark.asyncio
async def test_batch_publish_effect_failure_does_not_change_closed_trade_result(
    monkeypatch, tmp_path
):
    agent, connection = _batch_agent(tmp_path / "batch-effect-failure.sqlite")
    events, _, _, _ = _install_batch_runtime(monkeypatch, agent=agent)

    async def fail_effects(_prepared, _trade_result):
        events.append("effects-failed")
        raise RuntimeError("transport setup failed")

    agent._deliver_pending_kr_exit_publish_effects = fail_effects
    try:
        sold = await agent.update_holdings()
    finally:
        connection.close()

    assert [item["ticker"] for item in sold] == [TICKER]
    assert events == [
        "prepare",
        "execute",
        "complete",
        "post-commit",
        "effects-failed",
    ]


@pytest.mark.asyncio
async def test_batch_post_closed_cancellation_does_not_quarantine(
    monkeypatch, tmp_path
):
    agent, connection = _batch_agent(tmp_path / "batch-post-closed-cancel.sqlite")
    events, _, _, _ = _install_batch_runtime(monkeypatch, agent=agent)

    async def cancel_post_commit(_prepared):
        events.append("post-commit")
        raise asyncio.CancelledError

    agent._run_pending_kr_exit_post_commit = cancel_post_commit
    try:
        with pytest.raises(asyncio.CancelledError):
            await agent.update_holdings()
    finally:
        connection.close()

    assert events == ["prepare", "execute", "complete", "post-commit"]


@pytest.mark.asyncio
async def test_post_closed_cancellation_keeps_durable_effect_candidates(tmp_path):
    db_path = tmp_path / "post-closed-effect-candidates.sqlite"
    agent, connection = _pending_exit_agent(db_path)
    stock = _insert_open_holding(connection)
    prepared = _prepare(agent, stock)
    _set_intent_status(db_path, prepared.intent.id, "SUBMITTED")
    agent._complete_pending_kr_exit(prepared)

    async def cancel_post_commit(_prepared):
        raise asyncio.CancelledError

    agent._run_pending_kr_exit_post_commit = cancel_post_commit
    try:
        with pytest.raises(asyncio.CancelledError):
            await agent._run_pending_kr_exit_post_commit(prepared)
        state = _state(db_path, prepared.intent.id)
        effects = _effects(connection, prepared.intent.id)
    finally:
        connection.close()

    assert state == (0, 1, "SUBMITTED", "CLOSED")
    assert [effect["effect_type"] for effect in effects] == list(EXIT_EFFECT_TYPES)
    assert {effect["status"] for effect in effects} == {"PENDING"}


@pytest.mark.parametrize(
    ("intent_status", "terminal_event"),
    [("FAILED", "fail"), ("UNKNOWN", "quarantine"), ("SUBMITTING", "quarantine")],
)
@pytest.mark.asyncio
async def test_batch_pending_exit_non_submitted_has_no_publish_or_sold(
    monkeypatch, tmp_path, intent_status, terminal_event
):
    agent, connection = _batch_agent(
        tmp_path / f"batch-{intent_status.lower()}.sqlite"
    )
    events, _, _, _ = _install_batch_runtime(
        monkeypatch, agent=agent, intent_status=intent_status
    )
    try:
        sold = await agent.update_holdings()
    finally:
        connection.close()

    assert sold == []
    assert events == ["prepare", "execute", terminal_event]


@pytest.mark.asyncio
async def test_batch_pending_exit_queued_stays_pending_without_effects(
    monkeypatch, tmp_path
):
    agent, connection = _batch_agent(tmp_path / "batch-queued.sqlite")
    events, _, _, _ = _install_batch_runtime(
        monkeypatch, agent=agent, intent_status="QUEUED"
    )
    try:
        sold = await agent.update_holdings()
    finally:
        connection.close()

    assert sold == []
    assert events == ["prepare", "execute"]


@pytest.mark.asyncio
async def test_batch_pending_exit_local_flat_uses_reconciliation_not_broker(
    monkeypatch, tmp_path
):
    agent, connection = _batch_agent(tmp_path / "batch-flat.sqlite")
    events, quantities, checked, direct_broker = _install_batch_runtime(
        monkeypatch, agent=agent, checked_result=("FLAT", 0)
    )
    try:
        sold = await agent.update_holdings()
    finally:
        connection.close()

    assert [item["ticker"] for item in sold] == [TICKER]
    assert quantities == [None]
    assert checked == [TICKER]
    assert direct_broker == []
    assert events == [
        "prepare",
        "local-flat",
        "complete",
        "post-commit",
        "redis",
        "gcp",
    ]


@pytest.mark.asyncio
async def test_batch_checked_unknown_stops_before_prepare_and_effects(
    monkeypatch, tmp_path
):
    agent, connection = _batch_agent(tmp_path / "batch-checked-unknown.sqlite")
    events, _, checked, direct_broker = _install_batch_runtime(
        monkeypatch, agent=agent, checked_result=("UNKNOWN", None)
    )
    try:
        sold = await agent.update_holdings()
    finally:
        connection.close()

    assert sold == []
    assert events == []
    assert checked == [TICKER]
    assert direct_broker == []


@pytest.mark.asyncio
async def test_batch_malformed_flat_quantity_stops_before_prepare_and_effects(
    monkeypatch, tmp_path
):
    agent, connection = _batch_agent(tmp_path / "batch-malformed-flat.sqlite")
    events, _, checked, direct_broker = _install_batch_runtime(
        monkeypatch, agent=agent, checked_result=("FLAT", 7)
    )
    try:
        sold = await agent.update_holdings()
    finally:
        connection.close()

    assert sold == []
    assert events == []
    assert checked == [TICKER]
    assert direct_broker == []


@pytest.mark.asyncio
async def test_batch_opaque_result_is_quarantined_without_effects(
    monkeypatch, tmp_path
):
    agent, connection = _batch_agent(tmp_path / "batch-opaque-result.sqlite")
    events, _, _, _ = _install_batch_runtime(monkeypatch, agent=agent)

    async def opaque_result(_prepared):
        events.append("execute")
        return "opaque broker response"

    agent._execute_pending_kr_exit = opaque_result
    try:
        sold = await agent.update_holdings()
    finally:
        connection.close()

    assert sold == []
    assert events == ["prepare", "execute", "quarantine"]


@pytest.mark.asyncio
async def test_batch_pyramided_ticker_is_blocked_before_balance_and_broker(
    monkeypatch, tmp_path
):
    agent, connection = _batch_agent(tmp_path / "batch-pyramid-blocked.sqlite", rows=2)
    events, quantities, checked, _ = _install_batch_runtime(
        monkeypatch, agent=agent, intent_status="SUBMITTED"
    )
    try:
        sold = await agent.update_holdings()
    finally:
        connection.close()

    assert sold == []
    assert quantities == []
    assert checked == []
    assert events == []


@pytest.mark.asyncio
async def test_batch_finalize_failure_quarantines_without_publish(
    monkeypatch, tmp_path
):
    agent, connection = _batch_agent(tmp_path / "batch-finalize-fail.sqlite")
    events, _, _, _ = _install_batch_runtime(
        monkeypatch,
        agent=agent,
        complete_error=sqlite3.OperationalError("injected finalize rollback"),
    )
    try:
        sold = await agent.update_holdings()
    finally:
        connection.close()

    assert sold == []
    assert events == ["prepare", "execute", "complete", "quarantine"]


@pytest.mark.asyncio
async def test_batch_order_outcome_unknown_quarantines_without_effects(
    monkeypatch, tmp_path
):
    agent, connection = _batch_agent(tmp_path / "batch-order-unknown.sqlite")
    events, _, _, _ = _install_batch_runtime(
        monkeypatch,
        agent=agent,
        execute_error=OrderOutcomeUnknown("intent-1"),
    )
    try:
        sold = await agent.update_holdings()
    finally:
        connection.close()

    assert sold == []
    assert events == ["prepare", "execute", "quarantine"]


@pytest.mark.asyncio
async def test_batch_prepare_conflict_stops_before_broker(
    monkeypatch, tmp_path
):
    agent, connection = _batch_agent(tmp_path / "batch-prepare-conflict.sqlite")
    events, _, _, _ = _install_batch_runtime(
        monkeypatch,
        agent=agent,
        prepare_error=InvalidPositionTransition("failed exit tombstone"),
    )
    try:
        sold = await agent.update_holdings()
    finally:
        connection.close()

    assert sold == []
    assert events == ["prepare"]


@pytest.mark.asyncio
async def test_batch_cancellation_quarantines_when_possible_then_reraises(
    monkeypatch, tmp_path
):
    agent, connection = _batch_agent(tmp_path / "batch-cancel.sqlite")
    events, _, _, _ = _install_batch_runtime(
        monkeypatch, agent=agent, execute_error=asyncio.CancelledError()
    )
    try:
        with pytest.raises(asyncio.CancelledError):
            await agent.update_holdings()
    finally:
        connection.close()

    assert events == ["prepare", "execute", "quarantine"]


@pytest.mark.parametrize(
    ("checked_result", "expected_broker_calls"),
    [(('HELD', 10), 1), (('FLAT', 0), 0)],
    ids=["held", "local-flat"],
)
@pytest.mark.asyncio
async def test_batch_pending_exit_real_lifecycle_reaches_closed(
    monkeypatch, tmp_path, checked_result, expected_broker_calls
):
    db_path = tmp_path / f"real-{checked_result[0].lower()}.sqlite"
    agent, connection = _pending_exit_agent(db_path)
    _insert_open_holding(connection)
    agent.active_account = agent.account_configs[0]
    agent._get_live_regime_safe = lambda: None
    broker_calls = []

    async def current_price(_ticker):
        return 72000

    async def sell_decision(_stock):
        return True, "risk exit"

    async def journal(**_kwargs):
        return True

    agent._get_current_stock_price = current_price
    agent._analyze_sell_decision = sell_decision
    agent._create_journal_entry = journal

    class BrokerContext:
        def __init__(self, account_name=None, **_kwargs):
            self.account_name = account_name

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def get_holding_quantity_checked(self, _ticker):
            return checked_result

        async def async_sell_stock(self, **kwargs):
            broker_calls.append(kwargs)
            return {
                "success": True,
                "message": "submitted",
                "order_no": "KR-1",
            }

    monkeypatch.setattr(domestic_trading, "AsyncTradingContext", BrokerContext)
    monkeypatch.setenv("POSITION_PENDING_KR_ENABLED", "true")
    monkeypatch.setenv("POSITION_LEDGER_SHADOW_ENABLED", "true")
    corporate_module = types.ModuleType("cores.corporate_status")

    async def fetch_status_codes(*_args, **_kwargs):
        return {}

    corporate_module.fetch_status_codes = fetch_status_codes
    monkeypatch.setitem(sys.modules, "cores.corporate_status", corporate_module)
    redis_module = types.ModuleType("messaging.redis_signal_publisher")
    gcp_module = types.ModuleType("messaging.gcp_pubsub_signal_publisher")
    signal_calls = []

    async def publish(**_kwargs):
        signal_calls.append(_kwargs)

    redis_module.publish_sell_signal = publish
    gcp_module.publish_sell_signal = publish
    monkeypatch.setitem(sys.modules, "messaging.redis_signal_publisher", redis_module)
    monkeypatch.setitem(
        sys.modules, "messaging.gcp_pubsub_signal_publisher", gcp_module
    )

    try:
        sold = await agent.update_holdings()
        lifecycle = connection.execute(
            """
            SELECT i.status, p.status,
                   (SELECT COUNT(*) FROM stock_holdings),
                   (SELECT COUNT(*) FROM trading_history),
                   (SELECT COUNT(*) FROM broker_orders)
            FROM order_intents i
            JOIN positions p ON p.exit_intent_id=i.id
            WHERE i.side='SELL'
            """
        ).fetchone()
    finally:
        connection.close()

    assert [item["ticker"] for item in sold] == [TICKER]
    assert tuple(lifecycle) == ("SUBMITTED", "CLOSED", 0, 1, 1)
    assert len(broker_calls) == expected_broker_calls
    assert len(signal_calls) == 2
