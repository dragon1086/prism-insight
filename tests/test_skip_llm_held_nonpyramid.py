"""
tests/test_skip_llm_held_nonpyramid.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Tests for the "skip scenario LLM for a held stock that cannot pyramid-add" fast
path (#288-preserving latency optimization).

Two layers:
  1. tracking.helpers.pyramid_add_possible_ignoring_regime — the regime-independent
     cheap pre-gate, and that evaluate_pyramid_add_gate still delegates to it
     (behavior preserved for the full gate).
  2. StockTrackingAgent.analyze_report — a held stock that fails the cheap pre-gate
     returns "Already holding" WITHOUT calling the expensive _analyze_report_core
     (LLM); a held winner with room, and a non-held stock, still run it.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import stock_tracking_agent as sta_mod
from stock_tracking_agent import StockTrackingAgent
from tracking.helpers import (
    pyramid_add_possible_ignoring_regime,
    evaluate_pyramid_add_gate,
    PYRAMID_MIN_PROFIT_PCT,
    PYRAMID_MAX_ROWS,
)


# ---------------------------------------------------------------------------
# Layer 1: cheap pre-gate semantics
# ---------------------------------------------------------------------------

def test_pregate_winner_with_room_allowed():
    ok, _ = pyramid_add_possible_ignoring_regime(100.0, 110.0, 1)
    assert ok is True


def test_pregate_flat_or_underwater_blocked():
    ok, why = pyramid_add_possible_ignoring_regime(100.0, 100.0, 1)
    assert ok is False and "profit" in why
    ok2, _ = pyramid_add_possible_ignoring_regime(100.0, 90.0, 1)
    assert ok2 is False


def test_pregate_max_rows_blocked():
    ok, why = pyramid_add_possible_ignoring_regime(100.0, 200.0, PYRAMID_MAX_ROWS)
    assert ok is False and "row count" in why


def test_pregate_insufficient_price_data_blocked():
    ok, why = pyramid_add_possible_ignoring_regime(0.0, 110.0, 1)
    assert ok is False and "insufficient" in why


def test_full_gate_still_delegates_and_preserves_outcomes():
    # regime blocked even when the cheap conditions pass
    ok, why = evaluate_pyramid_add_gate("sideways", 100.0, 110.0, 1)
    assert ok is False and "regime" in why
    # allowed regime + winner + room -> allowed, reason preserves the legacy shape
    ok2, why2 = evaluate_pyramid_add_gate("strong_bull", 100.0, 110.0, 1)
    assert ok2 is True
    assert why2.startswith("add allowed (regime=strong_bull,")
    # allowed regime but cheap condition fails -> blocked with the cheap reason
    ok3, why3 = evaluate_pyramid_add_gate("strong_bull", 100.0, 110.0, PYRAMID_MAX_ROWS)
    assert ok3 is False and "row count" in why3


# ---------------------------------------------------------------------------
# Layer 2: analyze_report skips the LLM for held non-pyramid stocks
# ---------------------------------------------------------------------------

def _make_agent(*, holding: bool, position: dict, price: float):
    agent = StockTrackingAgent.__new__(StockTrackingAgent)
    agent.cursor = MagicMock()
    agent._extract_ticker_info = AsyncMock(return_value=("009150", "삼성전기"))
    agent._is_ticker_in_holdings = AsyncMock(return_value=holding)
    agent._account_scope = MagicMock(return_value=("vps:kr-primary:01", None))
    agent._get_current_stock_price = AsyncMock(return_value=price)
    # The expensive call we want to avoid for held non-pyramid stocks:
    agent._analyze_report_core = AsyncMock(return_value={
        "success": True, "ticker": "009150", "company_name": "삼성전기",
        "current_price": price, "scenario": {"market_condition": "strong_bull"},
        "decision": "Enter", "sector": "IT",
    })
    return agent


@pytest.mark.asyncio
async def test_held_nonpyramid_skips_llm(monkeypatch):
    # Held + flat (profit 0%) => cannot pyramid => must NOT call _analyze_report_core.
    monkeypatch.setattr(sta_mod, "get_existing_position_for_ticker",
                        lambda *a, **k: {"avg_buy_price": 100.0, "row_count": 1})
    agent = _make_agent(holding=True, position={"avg_buy_price": 100.0, "row_count": 1}, price=100.0)
    result = await agent.analyze_report("reports/009150_삼성전기_20260630_morning.pdf")
    assert result["decision"] == "Already holding"
    assert result["ticker"] == "009150"
    agent._analyze_report_core.assert_not_awaited()


@pytest.mark.asyncio
async def test_held_pyramid_candidate_runs_llm(monkeypatch):
    # Held + winner (+10%) + room => pyramid candidate => MUST run _analyze_report_core.
    monkeypatch.setattr(sta_mod, "get_existing_position_for_ticker",
                        lambda *a, **k: {"avg_buy_price": 100.0, "row_count": 1})
    agent = _make_agent(holding=True, position={"avg_buy_price": 100.0, "row_count": 1}, price=110.0)
    await agent.analyze_report("reports/009150_삼성전기_20260630_morning.pdf")
    agent._analyze_report_core.assert_awaited_once()


@pytest.mark.asyncio
async def test_not_held_runs_llm(monkeypatch):
    # Not held => normal full analysis => MUST run _analyze_report_core.
    monkeypatch.setattr(sta_mod, "get_existing_position_for_ticker",
                        lambda *a, **k: {"avg_buy_price": 0.0, "row_count": 0})
    agent = _make_agent(holding=False, position={}, price=110.0)
    await agent.analyze_report("reports/009150_삼성전기_20260630_morning.pdf")
    agent._analyze_report_core.assert_awaited_once()
