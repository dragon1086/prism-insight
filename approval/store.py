"""SQLite-backed persistence for approval records."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from typing import Iterable, List, Optional

from approval.db_schema import create_table
from approval.models import (
    ApprovalDecision,
    ApprovalRecord,
    ExecutionResult,
    TradeProposal,
)


def init_schema(conn: sqlite3.Connection) -> None:
    """Create the trade_approvals table if missing. Idempotent."""
    create_table(conn)


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


class ApprovalStore:
    """Thin DAO over the trade_approvals table.

    Designed for in-process use by ApprovalManager. Connections are reused;
    a single lock serialises writes so the store is safe under asyncio
    callbacks that may be triggered from multiple Telegram updates.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn = _connect(db_path)
        self._lock = threading.RLock()
        init_schema(self._conn)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ----- writes -----

    def insert_proposal(self, proposal: TradeProposal, *, chat_id: int | None = None,
                        message_id: int | None = None) -> ApprovalRecord:
        record = ApprovalRecord(
            approval_id=proposal.approval_id,
            ticker=proposal.ticker,
            stock_name=proposal.stock_name,
            side=proposal.side.value,
            proposed_amount_krw=proposal.proposed_amount_krw,
            final_amount_krw=proposal.proposed_amount_krw,
            entry_price=proposal.entry_price,
            stop_loss=proposal.stop_loss,
            target_price=proposal.target_price,
            score=proposal.score,
            rationale_json=json.dumps(proposal.rationale, ensure_ascii=False),
            trigger_type=proposal.trigger_type,
            proposed_at=proposal.proposed_at.isoformat(),
            decision=ApprovalDecision.PENDING.value,
            chat_id=chat_id,
            message_id=message_id,
            auto_executed=proposal.auto_execute,
        )
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO trade_approvals (
                    approval_id, ticker, stock_name, side,
                    proposed_amount_krw, final_amount_krw,
                    entry_price, stop_loss, target_price, score,
                    rationale_json, trigger_type, proposed_at, decision,
                    chat_id, message_id, auto_executed
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.approval_id, record.ticker, record.stock_name, record.side,
                    record.proposed_amount_krw, record.final_amount_krw,
                    record.entry_price, record.stop_loss, record.target_price, record.score,
                    record.rationale_json, record.trigger_type, record.proposed_at, record.decision,
                    record.chat_id, record.message_id, 1 if record.auto_executed else 0,
                ),
            )
        return record

    def update_decision(
        self,
        approval_id: str,
        decision: ApprovalDecision,
        *,
        decided_by: str | None = None,
        final_amount_krw: int | None = None,
        execution: ExecutionResult | None = None,
    ) -> None:
        decided_at = datetime.now().isoformat()
        fields = ["decision = ?", "decided_at = ?"]
        params: list = [decision.value, decided_at]
        if decided_by is not None:
            fields.append("decided_by = ?")
            params.append(decided_by)
        if final_amount_krw is not None:
            fields.append("final_amount_krw = ?")
            params.append(final_amount_krw)
        if execution is not None:
            fields.extend(["order_no = ?", "execution_result_json = ?"])
            params.extend([
                execution.order_no,
                json.dumps({
                    "success": execution.success,
                    "quantity": execution.quantity,
                    "fill_price": execution.fill_price,
                    "message": execution.message,
                    "raw": execution.raw,
                }, ensure_ascii=False, default=str),
            ])
        params.append(approval_id)
        sql = f"UPDATE trade_approvals SET {', '.join(fields)} WHERE approval_id = ?"
        with self._lock:
            self._conn.execute(sql, params)

    def attach_pnl(self, approval_id: str, *, pnl_amount: float, pnl_rate: float) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE trade_approvals SET pnl_amount = ?, pnl_rate = ? WHERE approval_id = ?",
                (pnl_amount, pnl_rate, approval_id),
            )

    # ----- reads -----

    def get(self, approval_id: str) -> Optional[ApprovalRecord]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM trade_approvals WHERE approval_id = ?", (approval_id,)
            ).fetchone()
        return _row_to_record(row) if row else None

    def list_pending(self) -> List[ApprovalRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM trade_approvals WHERE decision = ? ORDER BY proposed_at",
                (ApprovalDecision.PENDING.value,),
            ).fetchall()
        return [_row_to_record(r) for r in rows]

    def list_recent(self, limit: int = 20) -> List[ApprovalRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM trade_approvals ORDER BY proposed_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [_row_to_record(r) for r in rows]


def _row_to_record(row: sqlite3.Row) -> ApprovalRecord:
    return ApprovalRecord(
        approval_id=row["approval_id"],
        ticker=row["ticker"],
        stock_name=row["stock_name"],
        side=row["side"],
        proposed_amount_krw=row["proposed_amount_krw"],
        final_amount_krw=row["final_amount_krw"],
        entry_price=row["entry_price"],
        stop_loss=row["stop_loss"],
        target_price=row["target_price"],
        score=row["score"],
        rationale_json=row["rationale_json"],
        trigger_type=row["trigger_type"],
        proposed_at=row["proposed_at"],
        decision=row["decision"],
        decided_at=row["decided_at"],
        decided_by=row["decided_by"],
        order_no=row["order_no"],
        execution_result_json=row["execution_result_json"],
        pnl_amount=row["pnl_amount"],
        pnl_rate=row["pnl_rate"],
        chat_id=row["chat_id"],
        message_id=row["message_id"],
        auto_executed=bool(row["auto_executed"]),
    )
