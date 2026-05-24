"""SQLite schema for the trade_approvals table.

Follows the same flat-DDL style as tracking/db_schema.py.
"""
from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger(__name__)


TABLE_TRADE_APPROVALS = """
CREATE TABLE IF NOT EXISTS trade_approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    approval_id TEXT NOT NULL UNIQUE,
    ticker TEXT NOT NULL,
    stock_name TEXT NOT NULL,
    side TEXT NOT NULL,
    proposed_amount_krw INTEGER NOT NULL,
    final_amount_krw INTEGER NOT NULL,
    entry_price REAL NOT NULL,
    stop_loss REAL,
    target_price REAL,
    score INTEGER,
    rationale_json TEXT,
    trigger_type TEXT,
    proposed_at TEXT NOT NULL,
    decision TEXT NOT NULL,
    decided_at TEXT,
    decided_by TEXT,
    order_no TEXT,
    execution_result_json TEXT,
    pnl_amount REAL,
    pnl_rate REAL,
    chat_id INTEGER,
    message_id INTEGER,
    auto_executed INTEGER NOT NULL DEFAULT 0
)
"""

INDEX_TRADE_APPROVALS_DECISION = """
CREATE INDEX IF NOT EXISTS idx_trade_approvals_decision
ON trade_approvals(decision)
"""

INDEX_TRADE_APPROVALS_PROPOSED_AT = """
CREATE INDEX IF NOT EXISTS idx_trade_approvals_proposed_at
ON trade_approvals(proposed_at DESC)
"""


def create_table(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute(TABLE_TRADE_APPROVALS)
    cur.execute(INDEX_TRADE_APPROVALS_DECISION)
    cur.execute(INDEX_TRADE_APPROVALS_PROPOSED_AT)
    conn.commit()
    logger.info("trade_approvals table ready")
