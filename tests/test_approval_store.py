"""Unit tests for approval/store.py — SQLite DAO."""
from __future__ import annotations

import json
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from approval.models import (
    ApprovalDecision,
    ExecutionResult,
    TradeProposal,
    TradeSide,
)
from approval.store import ApprovalStore


@pytest.fixture
def store(tmp_path):
    db = tmp_path / "approvals.db"
    s = ApprovalStore(str(db))
    yield s
    s.close()


def _proposal(**overrides) -> TradeProposal:
    base = dict(
        ticker="005930",
        stock_name="삼성전자",
        side=TradeSide.BUY,
        entry_price=70000.0,
        proposed_amount_krw=500_000,
        stop_loss=66500.0,
        target_price=78000.0,
        score=82,
        rationale=["RSI 30 (oversold)", "거래량 폭증", "지지선 반등"],
        trigger_type="intraday_surge",
    )
    base.update(overrides)
    return TradeProposal(**base)


def test_insert_proposal_creates_pending_record(store):
    proposal = _proposal()
    record = store.insert_proposal(proposal, chat_id=12345, message_id=67890)

    assert record.approval_id == proposal.approval_id
    assert record.decision == ApprovalDecision.PENDING.value
    assert record.final_amount_krw == record.proposed_amount_krw
    assert record.chat_id == 12345
    assert record.message_id == 67890

    fetched = store.get(proposal.approval_id)
    assert fetched is not None
    assert fetched.ticker == "005930"
    assert json.loads(fetched.rationale_json)[0].startswith("RSI")


def test_insert_proposal_rejects_duplicate(store):
    proposal = _proposal()
    store.insert_proposal(proposal)
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        store.insert_proposal(proposal)


def test_update_decision_marks_approved_and_records_executor_result(store):
    proposal = _proposal()
    store.insert_proposal(proposal)
    execution = ExecutionResult(
        success=True, order_no="20260524000001",
        quantity=7, fill_price=70100.0, message="ok",
    )
    store.update_decision(
        proposal.approval_id, ApprovalDecision.APPROVED,
        decided_by="42", final_amount_krw=490_000, execution=execution,
    )
    fetched = store.get(proposal.approval_id)
    assert fetched.decision == ApprovalDecision.APPROVED.value
    assert fetched.decided_by == "42"
    assert fetched.final_amount_krw == 490_000
    assert fetched.order_no == "20260524000001"
    raw = json.loads(fetched.execution_result_json)
    assert raw["success"] is True
    assert raw["quantity"] == 7


def test_list_pending_returns_only_pending(store):
    p1 = _proposal()
    p2 = _proposal()  # different uuid by construction
    store.insert_proposal(p1)
    store.insert_proposal(p2)
    store.update_decision(p1.approval_id, ApprovalDecision.REJECTED, decided_by="1")

    pending = store.list_pending()
    assert len(pending) == 1
    assert pending[0].approval_id == p2.approval_id


def test_attach_pnl(store):
    proposal = _proposal()
    store.insert_proposal(proposal)
    store.attach_pnl(proposal.approval_id, pnl_amount=12_500.0, pnl_rate=2.5)
    rec = store.get(proposal.approval_id)
    assert rec.pnl_amount == 12_500.0
    assert rec.pnl_rate == 2.5


def test_list_recent_orders_by_proposed_at_desc(store):
    from datetime import datetime, timedelta

    older = _proposal()
    older.proposed_at = datetime.now() - timedelta(hours=2)
    newer = _proposal()
    newer.proposed_at = datetime.now()

    store.insert_proposal(older)
    store.insert_proposal(newer)

    recent = store.list_recent(limit=10)
    assert recent[0].approval_id == newer.approval_id
    assert recent[1].approval_id == older.approval_id
