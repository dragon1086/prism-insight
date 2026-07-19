import asyncio
import logging
import sqlite3
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import trading.domestic_stock_trading as domestic_trading
from prism_core.order_intents import IntentStore
from prism_core.positions import InvalidPositionTransition, PositionStore
from stock_tracking_agent import StockTrackingAgent
from tracking.db_schema import TABLE_STOCK_HOLDINGS


def _entry_state(db_path: Path) -> tuple[int, str | None, str | None]:
    with sqlite3.connect(db_path) as connection:
        holding_count = connection.execute(
            "SELECT COUNT(*) FROM stock_holdings WHERE ticker='005930'"
        ).fetchone()[0]
        intent = connection.execute(
            "SELECT status FROM order_intents "
            "WHERE market='KR' AND account_id='vps:kr-primary:01' "
            "AND symbol='005930' AND side='BUY'"
        ).fetchone()
        position = connection.execute(
            "SELECT status FROM positions "
            "WHERE market='KR' AND account_id='vps:kr-primary:01' "
            "AND symbol='005930'"
        ).fetchone()
    return (
        holding_count,
        intent[0] if intent else None,
        position[0] if position else None,
    )


def _entry_row_counts(db_path: Path) -> tuple[int, int, int, int]:
    with sqlite3.connect(db_path) as connection:
        holding_count = connection.execute(
            "SELECT COUNT(*) FROM stock_holdings "
            "WHERE account_key='vps:kr-primary:01' AND ticker='005930'"
        ).fetchone()[0]
        intent_count = connection.execute(
            "SELECT COUNT(*) FROM order_intents "
            "WHERE market='KR' AND account_id='vps:kr-primary:01' "
            "AND symbol='005930' AND side='BUY'"
        ).fetchone()[0]
        position_count = connection.execute(
            "SELECT COUNT(*) FROM positions "
            "WHERE market='KR' AND account_id='vps:kr-primary:01' "
            "AND symbol='005930'"
        ).fetchone()[0]
        broker_order_count = connection.execute(
            "SELECT COUNT(*) FROM broker_orders orders "
            "JOIN order_intents intents ON intents.id=orders.intent_id "
            "WHERE intents.market='KR' "
            "AND intents.account_id='vps:kr-primary:01' "
            "AND intents.symbol='005930' AND intents.side='BUY'"
        ).fetchone()[0]
    return holding_count, intent_count, position_count, broker_order_count


def _pending_entry_agent(db_path: Path):
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute(TABLE_STOCK_HOLDINGS)
    PositionStore(connection).ensure_schema()
    connection.commit()
    IntentStore(db_path)

    agent = StockTrackingAgent.__new__(StockTrackingAgent)
    agent.db_path = str(db_path)
    agent.conn = connection
    agent.cursor = connection.cursor()
    agent.account_configs = [
        {"name": "kr-primary", "account_key": "vps:kr-primary:01"}
    ]
    agent.active_account = None
    agent.max_slots = 10
    agent.message_queue = []
    agent._msg_types = []
    agent.position_ledger_shadow_enabled = True
    agent._position_pending_kr_ready = True
    agent.trigger_info_map = {}
    agent._get_trigger_win_rate = lambda _trigger: ""

    async def analyze_report(_report_path):
        return {
            "success": True,
            "ticker": "005930",
            "company_name": "Samsung Electronics",
            "current_price": 70000,
            "scenario": {
                "buy_score": 8,
                "min_score": 7,
                "sector": "Technology",
                "target_price": 80000,
                "stop_loss": 65000,
            },
            "decision": "Enter",
            "sector": "Technology",
            "rank_change_msg": "Up",
        }

    agent._analyze_report_core = analyze_report
    agent.update_holdings = AsyncMock(return_value=[])
    agent._is_ticker_in_holdings = AsyncMock(return_value=False)
    agent._get_current_slots_count = AsyncMock(return_value=0)
    agent._check_sector_diversity = AsyncMock(return_value=True)
    agent._save_watchlist_item = AsyncMock(return_value=True)
    return agent, connection


def _install_pending_entry_runtime(
    monkeypatch,
    *,
    agent,
    db_path: Path,
    broker_result: dict | None = None,
    broker_error: BaseException | None = None,
    broker_started: asyncio.Event | None = None,
    broker_release: asyncio.Event | None = None,
):
    broker_calls = []
    publish_states = []
    redis_calls = []
    gcp_calls = []

    class BrokerContext:
        def __init__(self, account_name=None, **_kwargs):
            self.account_name = account_name

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def async_buy_stock(
            self, stock_code, limit_price=None, buy_amount=None
        ):
            broker_calls.append(
                {
                    "stock_code": stock_code,
                    "limit_price": limit_price,
                    "buy_amount": buy_amount,
                    "state": _entry_state(db_path),
                    "message_count": len(agent.message_queue),
                }
            )
            if broker_started is not None:
                broker_started.set()
            if broker_release is not None:
                await broker_release.wait()
            if broker_error is not None:
                raise broker_error
            return broker_result or {
                "success": True,
                "message": "submitted",
                "order_no": "KR-ORDER-1",
            }

    redis_module = types.ModuleType("messaging.redis_signal_publisher")
    gcp_module = types.ModuleType("messaging.gcp_pubsub_signal_publisher")

    async def publish_redis(**kwargs):
        redis_calls.append(kwargs)
        publish_states.append((_entry_state(db_path), len(agent.message_queue)))

    async def publish_gcp(**kwargs):
        gcp_calls.append(kwargs)
        publish_states.append((_entry_state(db_path), len(agent.message_queue)))

    redis_module.publish_buy_signal = publish_redis
    gcp_module.publish_buy_signal = publish_gcp
    monkeypatch.setitem(sys.modules, "messaging.redis_signal_publisher", redis_module)
    monkeypatch.setitem(
        sys.modules, "messaging.gcp_pubsub_signal_publisher", gcp_module
    )
    monkeypatch.setattr(domestic_trading, "AsyncTradingContext", BrokerContext)
    monkeypatch.setenv("POSITION_PENDING_KR_ENABLED", "true")
    monkeypatch.setenv("POSITION_LEDGER_SHADOW_ENABLED", "true")
    return broker_calls, publish_states, redis_calls, gcp_calls


@pytest.mark.asyncio
async def test_concurrent_agents_create_one_pending_entry_and_one_broker_order(
    monkeypatch, tmp_path
):
    db_path = tmp_path / "concurrent-pending-entry.sqlite"
    first_agent, first_connection = _pending_entry_agent(db_path)
    second_agent, second_connection = _pending_entry_agent(db_path)
    broker_started = asyncio.Event()
    broker_release = asyncio.Event()
    broker_calls, _, _, _ = _install_pending_entry_runtime(
        monkeypatch,
        agent=first_agent,
        db_path=db_path,
        broker_started=broker_started,
        broker_release=broker_release,
    )

    try:
        first_task = asyncio.create_task(
            StockTrackingAgent.process_reports(first_agent, ["same-report.pdf"])
        )
        await asyncio.wait_for(broker_started.wait(), timeout=1)
        second_result = await StockTrackingAgent.process_reports(
            second_agent, ["same-report.pdf"]
        )
        broker_release.set()
        first_result = await asyncio.wait_for(first_task, timeout=1)
        counts = _entry_row_counts(db_path)
    finally:
        broker_release.set()
        first_connection.close()
        second_connection.close()

    assert sorted((first_result, second_result)) == [(0, 0), (1, 0)]
    assert len(broker_calls) == 1
    assert counts == (1, 1, 1, 1)


@pytest.mark.asyncio
async def test_open_entry_blocks_new_standard_decision_before_broker(
    monkeypatch, tmp_path
):
    db_path = tmp_path / "open-retry-block.sqlite"
    agent, connection = _pending_entry_agent(db_path)
    broker_calls, _, _, _ = _install_pending_entry_runtime(
        monkeypatch, agent=agent, db_path=db_path
    )

    try:
        first_result = await StockTrackingAgent.process_reports(
            agent, ["original-report.pdf"]
        )
        new_decision_result = await StockTrackingAgent.process_reports(
            agent, ["new-report.pdf"]
        )
        counts = _entry_row_counts(db_path)
    finally:
        connection.close()

    assert first_result == (1, 0)
    assert new_decision_result == (0, 0)
    assert len(broker_calls) == 1
    assert counts == (1, 1, 1, 1)


@pytest.mark.asyncio
async def test_enhanced_add_allows_matching_open_count_once_and_blocks_stale_retry(
    monkeypatch, tmp_path
):
    db_path = tmp_path / "enhanced-add-open-count.sqlite"
    agent, connection = _pending_entry_agent(db_path)
    broker_calls, _, _, _ = _install_pending_entry_runtime(
        monkeypatch, agent=agent, db_path=db_path
    )

    try:
        assert await StockTrackingAgent.process_reports(
            agent, ["original-report.pdf"]
        ) == (1, 0)
        prepared = agent._prepare_pending_kr_entry(
            ticker="005930",
            company_name="Samsung Electronics",
            current_price=71000,
            scenario={"target_price": 81000, "stop_loss": 66000},
            rank_change_msg="",
            source_decision_id="enhanced-add:first",
            source="kr_enhanced_batch",
            is_add=True,
            expected_open_count=1,
        )
        trade_result = await agent._execute_pending_kr_entry(
            prepared, current_price=71000
        )
        assert trade_result["intent_status"] == "SUBMITTED"
        agent._complete_pending_kr_entry(prepared)

        with pytest.raises(InvalidPositionTransition, match="changed OPEN.*expected 1"):
            agent._prepare_pending_kr_entry(
                ticker="005930",
                company_name="Samsung Electronics",
                current_price=72000,
                scenario={"target_price": 82000, "stop_loss": 67000},
                rank_change_msg="",
                source_decision_id="enhanced-add:stale",
                source="kr_enhanced_batch",
                is_add=True,
                expected_open_count=1,
            )
        counts = _entry_row_counts(db_path)
    finally:
        connection.close()

    assert len(broker_calls) == 2
    assert counts == (2, 2, 2, 2)


@pytest.mark.asyncio
async def test_unknown_entry_blocks_same_and_new_report_decisions_before_broker(
    monkeypatch, tmp_path
):
    db_path = tmp_path / "unknown-retry-block.sqlite"
    agent, connection = _pending_entry_agent(db_path)
    broker_calls, _, _, _ = _install_pending_entry_runtime(
        monkeypatch,
        agent=agent,
        db_path=db_path,
        broker_error=TimeoutError("broker response timed out"),
    )

    try:
        first_result = await StockTrackingAgent.process_reports(
            agent, ["original-report.pdf"]
        )
        counts_after_unknown = _entry_row_counts(db_path)
        same_decision_result = await StockTrackingAgent.process_reports(
            agent, ["original-report.pdf"]
        )
        new_decision_result = await StockTrackingAgent.process_reports(
            agent, ["new-report.pdf"]
        )
        counts_after_retries = _entry_row_counts(db_path)
    finally:
        connection.close()

    assert first_result == same_decision_result == new_decision_result == (0, 0)
    assert len(broker_calls) == 1
    assert counts_after_unknown == (1, 1, 1, 1)
    assert counts_after_retries == counts_after_unknown


@pytest.mark.asyncio
async def test_failed_entry_blocks_new_batch_and_enhanced_add_attempts(
    monkeypatch, tmp_path
):
    db_path = tmp_path / "failed-retry-block.sqlite"
    agent, connection = _pending_entry_agent(db_path)
    broker_calls, _, _, _ = _install_pending_entry_runtime(
        monkeypatch,
        agent=agent,
        db_path=db_path,
        broker_result={"success": False, "message": "order rejected"},
    )

    try:
        first_result = await StockTrackingAgent.process_reports(
            agent, ["original-report.pdf"]
        )
        counts_after_failure = _entry_row_counts(db_path)
        new_decision_result = await StockTrackingAgent.process_reports(
            agent, ["new-report.pdf"]
        )
        with pytest.raises(InvalidPositionTransition, match="ENTRY_FAILED"):
            agent._prepare_pending_kr_entry(
                ticker="005930",
                company_name="Samsung Electronics",
                current_price=70000,
                scenario={"target_price": 80000, "stop_loss": 65000},
                rank_change_msg="",
                source_decision_id="enhanced-add:new-report.pdf",
                source="kr_enhanced_batch",
            )
        counts_after_retries = _entry_row_counts(db_path)
    finally:
        connection.close()

    assert first_result == new_decision_result == (0, 0)
    assert len(broker_calls) == 1
    assert counts_after_failure == (0, 1, 1, 1)
    assert counts_after_retries == counts_after_failure


@pytest.mark.asyncio
async def test_pending_entry_message_exactly_matches_complex_legacy_buy_message(
    tmp_path,
):
    db_path = tmp_path / "pending-entry-message-parity.sqlite"
    agent, connection = _pending_entry_agent(db_path)
    agent.active_account = agent.account_configs[0]
    scenario = {
        "target_price": 81234,
        "stop_loss": 65432,
        "investment_period": "중기",
        "sector": "반도체",
        "valuation_analysis": "선행 PER이 업종 평균보다 낮음",
        "sector_outlook": "AI 서버 수요로 업황 개선",
        "rationale": "실적과 수급이 동시에 개선",
        "journal_reflection": {
            "recent_exit_caution": "직전 돌파 실패 구간을 확인",
            "applied_lessons": "거래량 확인 후 진입",
        },
        "score_adjustment": {
            "value": 2,
            "reasons": ["동일 패턴 승률", "수급 개선"],
        },
        "trading_scenarios": {
            "key_levels": {
                "primary_resistance": "80,500원",
                "secondary_resistance": 83500,
                "primary_support": "68,000",
                "secondary_support": 65100,
                "volume_baseline": "20일 평균의 150%",
            },
            "sell_triggers": [
                "target resistance breakout",
                "support decline",
                "time sideways",
                "외국인 순매도 전환",
            ],
            "hold_conditions": ["20일선 유지", "영업이익 추정치 유지"],
            "portfolio_context": "반도체 비중 상한 내 신규 편입",
        },
    }
    agent.trigger_info_map = {
        "005930": {"trigger_type": "Earnings Momentum", "trigger_mode": "live"}
    }
    agent._get_trigger_win_rate = lambda trigger: (
        "트리거 승률: 73%" if trigger == "Earnings Momentum" else ""
    )

    try:
        legacy_result = await agent._buy_stock_with_position(
            "005930",
            "Samsung Electronics",
            70123,
            scenario,
            "거래대금 순위 18→4위",
        )
        pending_message = agent._build_pending_kr_entry_message(
            ticker="005930",
            company_name="Samsung Electronics",
            current_price=70123,
            scenario=scenario,
            rank_change_msg="거래대금 순위 18→4위",
            trigger_type="Earnings Momentum",
        )
    finally:
        connection.close()

    assert legacy_result.success is True
    assert agent._msg_types == ["analysis"]
    assert agent.message_queue == [pending_message]


@pytest.mark.asyncio
async def test_pending_kr_buy_opens_position_before_publishing_submitted_order(
    monkeypatch, tmp_path, caplog
):
    db_path = tmp_path / "pending-entry.sqlite"
    agent, connection = _pending_entry_agent(db_path)
    broker_calls, publish_states, redis_calls, gcp_calls = (
        _install_pending_entry_runtime(
            monkeypatch,
            agent=agent,
            db_path=db_path,
        )
    )
    caplog.set_level(logging.CRITICAL)

    try:
        result = await StockTrackingAgent.process_reports(agent, ["report-a.pdf"])
    finally:
        connection.close()

    assert result == (1, 0)
    assert len(broker_calls) == 1
    assert broker_calls[0]["state"] == (1, "SUBMITTING", "PENDING_ENTRY")
    assert broker_calls[0]["message_count"] == 0
    assert publish_states == [
        ((1, "SUBMITTED", "OPEN"), 1),
        ((1, "SUBMITTED", "OPEN"), 1),
    ]
    assert len(agent.message_queue) == 1
    assert len(redis_calls) == 1
    assert len(gcp_calls) == 1
    assert not [record for record in caplog.records if record.levelno >= logging.CRITICAL]


@pytest.mark.asyncio
async def test_pending_kr_buy_keeps_submitting_when_broker_result_persistence_fails(
    monkeypatch, tmp_path, caplog
):
    db_path = tmp_path / "result-persistence-failed-entry.sqlite"
    agent, connection = _pending_entry_agent(db_path)
    originating_store = IntentStore(db_path)
    monkeypatch.setattr(
        agent, "_require_pending_entry_ready", lambda: originating_store
    )
    broker_calls, publish_states, redis_calls, gcp_calls = (
        _install_pending_entry_runtime(
            monkeypatch,
            agent=agent,
            db_path=db_path,
        )
    )
    record_result_calls = []

    def fail_record_result(*args, **kwargs):
        record_result_calls.append((args, kwargs))
        raise sqlite3.OperationalError("injected broker result persistence failure")

    monkeypatch.setattr(originating_store, "record_result", fail_record_result)
    caplog.set_level(logging.CRITICAL)

    try:
        result = await StockTrackingAgent.process_reports(agent, ["report-a.pdf"])
        state = _entry_state(db_path)
    finally:
        connection.close()

    assert result == (0, 0)
    assert state == (1, "SUBMITTING", "PENDING_ENTRY")
    assert len(broker_calls) == 1
    assert len(record_result_calls) == 1
    assert agent.message_queue == []
    assert publish_states == []
    assert redis_calls == []
    assert gcp_calls == []
    critical_records = [
        record for record in caplog.records if record.levelno >= logging.CRITICAL
    ]
    assert critical_records
    assert len(
        [
            record
            for record in critical_records
            if "[POSITION-PENDING][KR] entry unresolved" in record.getMessage()
        ]
    ) == 1


@pytest.mark.asyncio
async def test_pending_kr_buy_marks_explicit_broker_failure_without_publishing(
    monkeypatch, tmp_path, caplog
):
    db_path = tmp_path / "failed-entry.sqlite"
    agent, connection = _pending_entry_agent(db_path)
    broker_calls, publish_states, redis_calls, gcp_calls = (
        _install_pending_entry_runtime(
            monkeypatch,
            agent=agent,
            db_path=db_path,
            broker_result={"success": False, "message": "order rejected"},
        )
    )
    caplog.set_level(logging.CRITICAL)

    try:
        result = await StockTrackingAgent.process_reports(agent, ["report-a.pdf"])
        state = _entry_state(db_path)
    finally:
        connection.close()

    assert result == (0, 0)
    assert state == (0, "FAILED", "ENTRY_FAILED")
    assert len(broker_calls) == 1
    assert agent.message_queue == []
    assert publish_states == []
    assert redis_calls == []
    assert gcp_calls == []
    assert len(
        [record for record in caplog.records if record.levelno >= logging.CRITICAL]
    ) == 1


@pytest.mark.asyncio
async def test_pending_kr_buy_rolls_back_failed_entry_compensation_when_finalize_fails(
    monkeypatch, tmp_path, caplog
):
    db_path = tmp_path / "failed-entry-compensation-rollback.sqlite"
    agent, connection = _pending_entry_agent(db_path)
    broker_calls, publish_states, redis_calls, gcp_calls = (
        _install_pending_entry_runtime(
            monkeypatch,
            agent=agent,
            db_path=db_path,
            broker_result={"success": False, "message": "order rejected"},
        )
    )

    def fail_entry_finalize(_store, **_identity):
        raise sqlite3.OperationalError("injected ENTRY_FAILED finalize failure")

    monkeypatch.setattr(PositionStore, "fail_entry", fail_entry_finalize)
    caplog.set_level(logging.CRITICAL)

    try:
        result = await StockTrackingAgent.process_reports(agent, ["report-a.pdf"])
        state = _entry_state(db_path)
    finally:
        connection.close()

    assert result == (0, 0)
    assert state == (1, "FAILED", "PENDING_ENTRY")
    assert len(broker_calls) == 1
    assert agent.message_queue == []
    assert publish_states == []
    assert redis_calls == []
    assert gcp_calls == []
    assert len(
        [record for record in caplog.records if record.levelno >= logging.CRITICAL]
    ) == 1


@pytest.mark.asyncio
async def test_pending_kr_buy_keeps_unknown_outcome_for_manual_review(
    monkeypatch, tmp_path, caplog
):
    db_path = tmp_path / "unknown-entry.sqlite"
    agent, connection = _pending_entry_agent(db_path)
    broker_calls, publish_states, redis_calls, gcp_calls = (
        _install_pending_entry_runtime(
            monkeypatch,
            agent=agent,
            db_path=db_path,
            broker_error=TimeoutError("broker response timed out"),
        )
    )
    caplog.set_level(logging.CRITICAL)

    try:
        result = await StockTrackingAgent.process_reports(agent, ["report-a.pdf"])
        state = _entry_state(db_path)
    finally:
        connection.close()

    assert result == (0, 0)
    assert state == (1, "UNKNOWN", "PENDING_ENTRY")
    assert len(broker_calls) == 1
    assert agent.message_queue == []
    assert publish_states == []
    assert redis_calls == []
    assert gcp_calls == []
    assert len(
        [record for record in caplog.records if record.levelno >= logging.CRITICAL]
    ) == 1


@pytest.mark.asyncio
async def test_pending_kr_buy_keeps_queued_order_pending_without_publishing(
    monkeypatch, tmp_path, caplog
):
    db_path = tmp_path / "queued-entry.sqlite"
    agent, connection = _pending_entry_agent(db_path)
    broker_calls, publish_states, redis_calls, gcp_calls = (
        _install_pending_entry_runtime(
            monkeypatch,
            agent=agent,
            db_path=db_path,
            broker_result={
                "success": True,
                "message": "Reserved buy order queued",
                "order_no": "PENDING-7",
                "order_type": "queued_buy",
            },
        )
    )
    caplog.set_level(logging.CRITICAL)

    try:
        result = await StockTrackingAgent.process_reports(agent, ["report-a.pdf"])
        state = _entry_state(db_path)
    finally:
        connection.close()

    assert result == (0, 0)
    assert state == (1, "QUEUED", "PENDING_ENTRY")
    assert len(broker_calls) == 1
    assert agent.message_queue == []
    assert publish_states == []
    assert redis_calls == []
    assert gcp_calls == []
    assert len(
        [record for record in caplog.records if record.levelno >= logging.CRITICAL]
    ) == 1


@pytest.mark.asyncio
async def test_pending_kr_buy_keeps_submitted_position_pending_when_open_finalize_fails(
    monkeypatch, tmp_path, caplog
):
    db_path = tmp_path / "open-finalize-failed-entry.sqlite"
    agent, connection = _pending_entry_agent(db_path)
    broker_calls, publish_states, redis_calls, gcp_calls = (
        _install_pending_entry_runtime(
            monkeypatch,
            agent=agent,
            db_path=db_path,
        )
    )

    def fail_complete_entry(_store, **_identity):
        raise sqlite3.OperationalError("injected OPEN finalize failure")

    monkeypatch.setattr(PositionStore, "complete_entry", fail_complete_entry)
    caplog.set_level(logging.CRITICAL)

    try:
        result = await StockTrackingAgent.process_reports(agent, ["report-a.pdf"])
        state = _entry_state(db_path)
    finally:
        connection.close()

    assert result == (0, 0)
    assert state == (1, "SUBMITTED", "PENDING_ENTRY")
    assert len(broker_calls) == 1
    assert agent.message_queue == []
    assert publish_states == []
    assert redis_calls == []
    assert gcp_calls == []
    assert len(
        [record for record in caplog.records if record.levelno >= logging.CRITICAL]
    ) == 1


@pytest.mark.asyncio
async def test_pending_kr_buy_cancellation_is_unknown_and_propagates_without_publishing(
    monkeypatch, tmp_path, caplog
):
    db_path = tmp_path / "cancelled-entry.sqlite"
    agent, connection = _pending_entry_agent(db_path)
    broker_calls, publish_states, redis_calls, gcp_calls = (
        _install_pending_entry_runtime(
            monkeypatch,
            agent=agent,
            db_path=db_path,
            broker_error=asyncio.CancelledError(),
        )
    )
    caplog.set_level(logging.CRITICAL)

    try:
        with pytest.raises(asyncio.CancelledError):
            await StockTrackingAgent.process_reports(agent, ["report-a.pdf"])
        state = _entry_state(db_path)
    finally:
        connection.close()

    assert state == (1, "UNKNOWN", "PENDING_ENTRY")
    assert len(broker_calls) == 1
    assert agent.message_queue == []
    assert publish_states == []
    assert redis_calls == []
    assert gcp_calls == []
    assert len(
        [record for record in caplog.records if record.levelno >= logging.CRITICAL]
    ) == 1


@pytest.mark.asyncio
async def test_pending_kr_buy_rolls_back_when_position_prepare_fails(
    monkeypatch, tmp_path, caplog
):
    db_path = tmp_path / "prepare-failed-entry.sqlite"
    agent, connection = _pending_entry_agent(db_path)
    broker_calls, publish_states, redis_calls, gcp_calls = (
        _install_pending_entry_runtime(
            monkeypatch,
            agent=agent,
            db_path=db_path,
        )
    )

    def fail_prepare_entry(_store, **_kwargs):
        raise RuntimeError("injected position prepare failure")

    monkeypatch.setattr(PositionStore, "prepare_entry", fail_prepare_entry)
    caplog.set_level(logging.CRITICAL)

    try:
        result = await StockTrackingAgent.process_reports(agent, ["report-a.pdf"])
        state = _entry_state(db_path)
    finally:
        connection.close()

    assert result == (0, 0)
    assert state == (0, None, None)
    assert broker_calls == []
    assert agent.message_queue == []
    assert publish_states == []
    assert redis_calls == []
    assert gcp_calls == []
    assert len(
        [record for record in caplog.records if record.levelno >= logging.CRITICAL]
    ) == 1


@pytest.mark.parametrize("readiness", [None, False])
def test_pending_entry_requires_explicit_successful_ledger_readiness(
    monkeypatch, tmp_path, readiness
):
    db_path = tmp_path / "readiness.sqlite"
    connection = sqlite3.connect(db_path)
    agent = StockTrackingAgent.__new__(StockTrackingAgent)
    agent.db_path = str(db_path)
    agent.conn = connection
    agent.position_ledger_shadow_enabled = True
    if readiness is not None:
        agent._position_pending_kr_ready = readiness
    monkeypatch.setenv("POSITION_PENDING_KR_ENABLED", "true")

    try:
        with pytest.raises(RuntimeError, match="initialization is not ready"):
            agent._require_pending_entry_ready()
    finally:
        connection.close()
