"""Dataclasses and enums used across the approval layer."""
from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional


class ApprovalDecision(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    MODIFY_REQUESTED = "MODIFY_REQUESTED"
    EXPIRED = "EXPIRED"
    AUTO_EXECUTED = "AUTO_EXECUTED"  # used for emergency stop-loss path


class TradeSide(str, enum.Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass
class TradeProposal:
    """An AI-generated trade proposal awaiting human approval."""

    ticker: str
    stock_name: str
    side: TradeSide
    entry_price: float
    proposed_amount_krw: int
    stop_loss: Optional[float] = None
    target_price: Optional[float] = None
    score: Optional[int] = None
    confidence: Optional[float] = None
    rationale: List[str] = field(default_factory=list)
    trigger_type: Optional[str] = None
    # If True, the manager will bypass the approval step (emergency stop-loss).
    auto_execute: bool = False
    proposed_at: datetime = field(default_factory=datetime.now)
    approval_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def short_id(self) -> str:
        return self.approval_id[:12]

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["side"] = self.side.value
        d["proposed_at"] = self.proposed_at.isoformat()
        return d


@dataclass
class ExecutionResult:
    """Outcome of the downstream order execution call."""

    success: bool
    order_no: Optional[str] = None
    quantity: int = 0
    fill_price: Optional[float] = None
    message: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ApprovalRecord:
    """Persisted state of a proposal in the trade_approvals table."""

    approval_id: str
    ticker: str
    stock_name: str
    side: str  # TradeSide value
    proposed_amount_krw: int
    final_amount_krw: int
    entry_price: float
    stop_loss: Optional[float]
    target_price: Optional[float]
    score: Optional[int]
    rationale_json: str
    trigger_type: Optional[str]
    proposed_at: str  # ISO
    decision: str  # ApprovalDecision value
    decided_at: Optional[str] = None
    decided_by: Optional[str] = None  # telegram user id
    order_no: Optional[str] = None
    execution_result_json: Optional[str] = None
    pnl_amount: Optional[float] = None
    pnl_rate: Optional[float] = None
    chat_id: Optional[int] = None
    message_id: Optional[int] = None
    auto_executed: bool = False
