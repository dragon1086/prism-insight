from __future__ import annotations

import ast
import copy
import logging
import os
import traceback
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import AsyncMock

import pytest


PROJECT_ROOT = Path(__file__).parent.parent
AGENT_PATH = PROJECT_ROOT / "prism-us" / "us_stock_tracking_agent.py"


def _load_real_method(name: str):
    """Compile one real method without importing the agent's broker/LLM stack."""
    source = AGENT_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(AGENT_PATH))
    class_node = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "USStockTrackingAgent"
    )
    method = copy.deepcopy(
        next(
            node
            for node in class_node.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        )
    )
    method.decorator_list = []
    module = ast.Module(body=[method], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {
        "Any": Any,
        "Dict": Dict,
        "List": List,
        "Optional": Optional,
        "Tuple": Tuple,
        "logger": logging.getLogger("us-held-candidate-test"),
        "os": os,
        "traceback": traceback,
    }
    exec(compile(module, str(AGENT_PATH), "exec"), namespace)
    return namespace[name]


@pytest.mark.asyncio
async def test_pilot_frozen_held_candidate_still_queues_one_decision():
    process_reports = _load_real_method("process_reports")
    agent = SimpleNamespace(
        account_configs=[{"name": "primary"}],
        active_account=None,
        max_slots=10,
        message_queue=[],
        _msg_types=[],
    )
    analysis = {
        "success": True,
        "ticker": "NVDA",
        "company_name": "NVIDIA Corporation",
        "current_price": 223.26,
        "scenario": {
            "buy_score": 8,
            "min_score": 4,
            "market_condition": "strong_bull",
            "rationale": "기존 보유분의 추세는 유지 중입니다.",
        },
        "decision": "entry",
        "sector": "Technology",
    }

    agent._analyze_report_core = AsyncMock(return_value=analysis)
    agent.update_holdings = AsyncMock(return_value=[])
    agent._is_ticker_in_holdings = AsyncMock(return_value=True)
    agent._save_watchlist_item = AsyncMock(return_value=True)
    agent._set_active_account = lambda account: setattr(agent, "active_account", account)
    agent._safe_account_log_label = lambda account: account["name"]
    agent._regime_policy_mod = lambda: SimpleNamespace(
        pilot_reexposure_active=lambda market: market == "us"
    )
    agent._queue_existing_holding_decision = types.MethodType(
        _load_real_method("_queue_existing_holding_decision"), agent
    )

    buy_count, sell_count = await process_reports(agent, ["reports/NVDA.pdf"])

    assert (buy_count, sell_count) == (0, 0)
    assert agent._msg_types == ["analysis"]
    assert len(agent.message_queue) == 1
    assert "NVDA" in agent.message_queue[0]
    assert "기존 보유" in agent.message_queue[0]
    assert "추가매수" in agent.message_queue[0]
    agent._save_watchlist_item.assert_not_awaited()
