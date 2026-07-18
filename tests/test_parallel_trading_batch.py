"""
tests/test_parallel_trading_batch.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Tests for the parallel buy-analysis pre-pass in the KR trading batch
(feat/parallel-trading-batch).

What we protect:
  1. Concurrency: the expensive, holdings-order-INDEPENDENT `_analyze_report_core`
     runs concurrently in a pre-pass (total ≈ max single call, not the sum) and
     respects the `TRADING_ANALYSIS_CONCURRENCY` Semaphore cap.
  2. Order-sensitive semantics preserved: the sequential decision loop still runs
     the holdings-dependent gates (`_is_ticker_in_holdings`, `_check_sector_diversity`)
     AFTER the parallel phase, in original path order, so a second same-sector
     candidate is gated once the first has been "bought".
  3. Resilience: one failing core analysis does not abort the whole batch.

NOTE: intentionally NO module-level sys.exit (some repo test files do that, which
breaks pytest collection — we do not copy that pattern).
"""
from __future__ import annotations

import asyncio
import sys
import time
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import stock_tracking_enhanced_agent as enh_mod
from stock_tracking_enhanced_agent import EnhancedStockTrackingAgent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_agent() -> EnhancedStockTrackingAgent:
    """Build an agent without running __init__ (no real sqlite / KIS config)."""
    agent = EnhancedStockTrackingAgent.__new__(EnhancedStockTrackingAgent)
    agent.max_slots = 10
    agent.active_account = None
    agent._account_scope = MagicMock(return_value=("vps:kr-primary:01", None))
    agent.update_holdings = AsyncMock(return_value=[])
    agent.message_queue = []
    agent._msg_types = []
    agent.trigger_info_map = {}
    agent._get_trigger_win_rate = MagicMock(return_value="")
    agent._save_watchlist_item = AsyncMock(return_value=True)
    return agent


def _core_ok(ticker: str, company: str, *, decision: str = "Skip",
             sector: str = "Tech", buy_score: int = 9, min_score: int = 5) -> dict:
    return {
        "success": True,
        "ticker": ticker,
        "company_name": company,
        "current_price": 70000,
        "scenario": {"buy_score": buy_score, "min_score": min_score,
                     "sector": sector, "market_condition": "strong_bull",
                     "rationale": "t"},
        "decision": decision,
        "sector": sector,
        "rank_change_percentage": 1.0,
        "rank_change_msg": "up",
    }


# ---------------------------------------------------------------------------
# 1. Concurrency + Semaphore cap
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_prepass_runs_cores_concurrently(monkeypatch):
    """With cap >= N, all cores overlap: elapsed ≈ one sleep, peak == N."""
    monkeypatch.setattr(enh_mod, "TRADING_ANALYSIS_CONCURRENCY", 8)
    agent = _make_agent()

    SLEEP = 0.15
    N = 5
    state = {"current": 0, "peak": 0}

    async def core_stub(path):
        state["current"] += 1
        state["peak"] = max(state["peak"], state["current"])
        await asyncio.sleep(SLEEP)
        state["current"] -= 1
        return _core_ok("005930", "Samsung")

    agent._analyze_report_core = core_stub
    # Neutralize the sequential loop — we only measure the parallel pre-pass here.
    agent.analyze_report = AsyncMock(return_value={"success": False, "error": "noop"})

    paths = [f"reports/{i:06d}_x_20260101_morning.pdf" for i in range(N)]
    t0 = time.perf_counter()
    await agent.process_reports(paths)
    elapsed = time.perf_counter() - t0

    assert state["peak"] == N, f"expected all {N} to overlap, peak={state['peak']}"
    # Concurrent runtime ≈ 1 sleep; serial would be N*SLEEP. Allow generous slack.
    assert elapsed < (N - 1) * SLEEP, f"pre-pass not concurrent: {elapsed:.3f}s"


@pytest.mark.asyncio
async def test_prepass_respects_semaphore_cap(monkeypatch):
    """With cap < N, peak concurrency is bounded by the cap."""
    CAP = 2
    monkeypatch.setattr(enh_mod, "TRADING_ANALYSIS_CONCURRENCY", CAP)
    agent = _make_agent()

    SLEEP = 0.12
    N = 6
    state = {"current": 0, "peak": 0}

    async def core_stub(path):
        state["current"] += 1
        state["peak"] = max(state["peak"], state["current"])
        await asyncio.sleep(SLEEP)
        state["current"] -= 1
        return _core_ok("005930", "Samsung")

    agent._analyze_report_core = core_stub
    agent.analyze_report = AsyncMock(return_value={"success": False, "error": "noop"})

    paths = [f"reports/{i:06d}_x_20260101_morning.pdf" for i in range(N)]
    t0 = time.perf_counter()
    await agent.process_reports(paths)
    elapsed = time.perf_counter() - t0

    assert state["peak"] == CAP, f"cap not respected, peak={state['peak']} cap={CAP}"
    # Still faster than fully serial (proves batching by ceil(N/CAP) waves).
    assert elapsed < (N - 1) * SLEEP, f"not batched under cap: {elapsed:.3f}s"


# ---------------------------------------------------------------------------
# 2. Order-sensitive gates stay in the sequential phase
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_gates_run_sequentially_after_parallel_phase(monkeypatch):
    """
    Cores are computed in the parallel pre-pass; then the REAL analyze_report runs
    the holdings/sector gates sequentially in original order. A second same-sector
    candidate is gated only after the first is 'bought' — proving gates see mutated
    DB state (which is exactly why they must not be parallelized).
    """
    monkeypatch.setattr(enh_mod, "TRADING_ANALYSIS_CONCURRENCY", 4)

    # Force the enhanced buy path's account trade + re-entry cooldown to be inert.
    import trading.domestic_stock_trading as domestic_trading

    class _FakeTradingCtx:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def async_buy_stock(self, stock_code, limit_price=None, buy_amount=None):
            return {"success": True, "message": "ok"}

    monkeypatch.setattr(domestic_trading, "AsyncTradingContext", _FakeTradingCtx)
    fake_reentry = types.ModuleType("reentry_cooldown")
    fake_reentry.reentry_block = lambda *a, **k: None
    fake_reentry.COOLDOWN_LIVE = False
    fake_reentry.COOLDOWN_RISK_EXIT_LIVE = False
    monkeypatch.setitem(sys.modules, "reentry_cooldown", fake_reentry)

    # Keep optional signal publishers inert (they are import-and-call in the buy path).
    redis_mod = types.ModuleType("messaging.redis_signal_publisher")
    redis_mod.publish_buy_signal = AsyncMock(return_value=None)
    gcp_mod = types.ModuleType("messaging.gcp_pubsub_signal_publisher")
    gcp_mod.publish_buy_signal = AsyncMock(return_value=None)
    monkeypatch.setitem(sys.modules, "messaging.redis_signal_publisher", redis_mod)
    monkeypatch.setitem(sys.modules, "messaging.gcp_pubsub_signal_publisher", gcp_mod)

    agent = _make_agent()

    path_a = "reports/005930_A_20260101_morning.pdf"
    path_b = "reports/000660_B_20260101_morning.pdf"
    tickers = {path_a: ("005930", "A"), path_b: ("000660", "B")}

    events: list[tuple[str, str]] = []
    bought_sectors: set[str] = set()

    async def core_stub(path):
        events.append(("core", tickers[path][0]))
        await asyncio.sleep(0.01)
        t, c = tickers[path]
        return _core_ok(t, c, decision="Enter", sector="Tech")

    async def fake_extract_ticker_info(path):
        return tickers[path]

    async def fake_is_holding(ticker):
        events.append(("holdings_check", ticker))
        return False

    async def fake_sector_diversity(sector):
        events.append(("sector_check", sector))
        # First Tech candidate is diverse; once one is bought, block the rest.
        return sector not in bought_sectors

    async def fake_buy_stock(ticker, company_name, current_price, scenario,
                             rank_change_msg="", is_add=False):
        events.append(("buy", ticker))
        bought_sectors.add(scenario.get("sector", "Tech"))
        return True

    agent._analyze_report_core = core_stub
    agent._extract_ticker_info = fake_extract_ticker_info
    agent._is_ticker_in_holdings = fake_is_holding
    agent._check_sector_diversity = fake_sector_diversity
    agent.buy_stock = fake_buy_stock

    buy_count, _ = await agent.process_reports([path_a, path_b])

    # --- Parallel phase completes before any sequential gate runs ---
    core_idx = [i for i, e in enumerate(events) if e[0] == "core"]
    gate_idx = [i for i, e in enumerate(events)
                if e[0] in ("holdings_check", "sector_check", "buy")]
    assert core_idx, "core pre-pass never ran"
    assert gate_idx, "sequential gates never ran"
    assert max(core_idx) < min(gate_idx), (
        f"gates interleaved with parallel core phase: {events}")

    # --- Both cores computed in the pre-pass ---
    assert {e[1] for e in events if e[0] == "core"} == {"005930", "000660"}

    # --- Sequential loop keeps original path order (A before B) ---
    first_a = next(i for i, e in enumerate(events)
                   if e == ("sector_check", "Tech"))
    a_buy = events.index(("buy", "005930"))
    assert first_a < a_buy

    # --- Order-sensitive gating: A bought, B blocked by sector concentration ---
    assert ("buy", "005930") in events
    assert ("buy", "000660") not in events, "B should be gated (same sector as A)"
    assert buy_count == 1


# ---------------------------------------------------------------------------
# 3. One core failure does not kill the batch
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_one_core_failure_does_not_abort_batch(monkeypatch):
    monkeypatch.setattr(enh_mod, "TRADING_ANALYSIS_CONCURRENCY", 4)
    agent = _make_agent()

    path_bad = "reports/111111_BAD_20260101_morning.pdf"
    path_good = "reports/222222_GOOD_20260101_morning.pdf"
    tickers = {path_bad: ("111111", "BAD"), path_good: ("222222", "GOOD")}

    core_calls: list[str] = []
    gate_calls: list[str] = []

    async def core_stub(path):
        core_calls.append(tickers[path][0])
        if path == path_bad:
            raise RuntimeError("boom in core analysis")
        return _core_ok(*tickers[path], decision="Skip")

    async def fake_extract_ticker_info(path):
        return tickers[path]

    async def fake_is_holding(ticker):
        return False

    async def fake_sector_diversity(sector):
        gate_calls.append(sector)
        return True

    agent._analyze_report_core = core_stub
    agent._extract_ticker_info = fake_extract_ticker_info
    agent._is_ticker_in_holdings = fake_is_holding
    agent._check_sector_diversity = fake_sector_diversity

    # Must not raise despite the bad core.
    buy_count, sell_count = await agent.process_reports([path_bad, path_good])

    assert (buy_count, sell_count) == (0, 0)
    # Both cores were attempted in the pre-pass...
    assert set(core_calls) == {"111111", "222222"}
    # ...and the good report still reached the sequential gate phase.
    assert "Tech" in gate_calls
