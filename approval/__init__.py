"""Human-in-the-Loop approval layer for prism-insight trade execution.

This package wraps every AI-generated buy/sell proposal in a Telegram approval
step before the order hits KIS. See `kis_prism_plan.md` §2 (Phase 2) for the
overall design.

Public API:
    - TradeProposal: dataclass describing a proposed trade
    - ApprovalRecord: persisted state of a proposal
    - ApprovalDecision: enum of terminal states
    - ApprovalManager: orchestrator (request → wait → execute or expire)
    - build_approval_message / build_approval_keyboard: UI builders
    - init_schema: create the trade_approvals SQLite table
"""

from approval.models import (
    ApprovalDecision,
    ApprovalRecord,
    ExecutionResult,
    TradeProposal,
)
from approval.store import ApprovalStore, init_schema
from approval.message import (
    build_approval_keyboard,
    build_approval_message,
    parse_callback_data,
)
from approval.handler import ApprovalManager, ApprovalManagerConfig

__all__ = [
    "ApprovalDecision",
    "ApprovalManager",
    "ApprovalManagerConfig",
    "ApprovalRecord",
    "ApprovalStore",
    "ExecutionResult",
    "TradeProposal",
    "build_approval_keyboard",
    "build_approval_message",
    "init_schema",
    "parse_callback_data",
]
