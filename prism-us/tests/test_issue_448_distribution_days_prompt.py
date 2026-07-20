"""Regression tests for issue #448: distribution_days count in US buy-decision prompt.

Mirror of tests/test_issue_448_distribution_days_prompt.py for the US agent.
TDD protocol: written BEFORE the fix. Pre-fix: all tests fail.
Post-fix: all tests pass.

prism-us shadowing note
-----------------------
``prism-us/cores/`` shadows the main project's ``cores/`` on sys.path, so a plain
``import cores.regime_policy`` resolves to the prism-us copy (which has no
regime_policy). Production loads the ROOT module by file path via
``us_stock_tracking_agent._import_from_main_cores`` under the module name
``prism_root_regime_policy`` and re-execs it fresh on every call. These tests
therefore (a) load the root module by file path once at module-import time for
direct-function assertions (Part A), and (b) patch ``_import_from_main_cores``
itself for the agent-output tests (Part B) — the only reliable seam, since the
module is re-loaded inside the method under test.

Import isolation
----------------
``us_stock_tracking_agent`` loads ``openai_responses_llm.py`` from the ROOT
``cores/llm/`` dir by file path at import time.  That real file in turn does::

    from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM

so ``mcp_agent.workflows.llm.augmented_llm_openai`` must be stubbed before the
first ``import us_stock_tracking_agent`` is executed.  The stub block below
handles this (and mirrors the KR test's stub block).

Run with:
    cd prism-us && python -m pytest tests/test_issue_448_distribution_days_prompt.py -v
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import patch, MagicMock

import pandas as pd

PRISM_US_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = PRISM_US_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PRISM_US_DIR))

# ---------------------------------------------------------------------------
# Stub heavy/unavailable modules so us_stock_tracking_agent imports in CI/local.
# (mcp_agent, KIS auth, Crypto, seaborn, US data client are not needed to
# exercise _get_trend_facts, which is what Part B tests assert on).
# Mirrors the KR test's stub block.
# ---------------------------------------------------------------------------
for _n in [
    "mcp_agent",
    "mcp_agent.app",
    "mcp_agent.agents",
    "mcp_agent.agents.agent",               # telegram_translator_agent.py imports this
    "mcp_agent.workflows",
    "mcp_agent.workflows.llm",
    "mcp_agent.workflows.llm.augmented_llm",
    "mcp_agent.workflows.llm.augmented_llm_openai",  # openai_responses_llm.py loads this at exec time
    "cores.llm",
    "cores.llm.openai_responses_llm",
    "cores.agents.trading_agents",
    "Crypto",
    "Crypto.Cipher",
    "Crypto.Cipher.AES",
    "Crypto.Util",
    "Crypto.Util.Padding",
    "trading.kis_auth",
    "seaborn",
    "cores.us_data_client",  # provides get_us_data_client(); configured per-test in Part B
    "cores.openai_error_logging",   # telegram_translator_agent.py imports this at exec time
    "telegram",                     # us_stock_tracking_agent top-level imports
    "telegram.error",
]:
    sys.modules.setdefault(_n, MagicMock())

# ---------------------------------------------------------------------------
# Prevent trading/kis_auth.py from executing at us_stock_tracking_agent import
# time.  The agent loads it by file path:
#   spec_from_file_location("kis_auth", PROJECT_ROOT / "trading/kis_auth.py")
#   exec_module(ka)   ← opens a YAML config absent from this worktree → FileNotFoundError
# Patching spec_from_file_location to return a no-op spec for that path stops
# exec_module from running the real file.  All other calls pass through.
# ---------------------------------------------------------------------------
_real_spec_from_file_location = importlib.util.spec_from_file_location


def _kis_auth_safe_spec(name, location=None, *args, **kwargs):
    if location is not None and "kis_auth" in str(location):
        stub = MagicMock()
        stub.name = name
        stub.loader.exec_module = lambda m: None  # no-op: skip YAML config read
        return stub
    return _real_spec_from_file_location(name, location, *args, **kwargs)


importlib.util.spec_from_file_location = _kis_auth_safe_spec


def _load_root_regime(module_name: str = "prism_root_regime_policy_test"):
    """Load ROOT cores/regime_policy.py by file path (bypasses prism-us/cores).

    The module MUST be registered in sys.modules BEFORE exec_module so the
    frozen @dataclass resolves sys.modules[cls.__module__].__dict__ during
    class creation (Python 3.12+ dataclass impl).  Mirrors the same guard in
    us_stock_tracking_agent._import_from_main_cores.

    Called ONCE at module-import time via ``_RP`` below; do not re-call per
    test — re-execing the module rebinds the frozen MarketPulseDetail dataclass
    and can break the sys.modules[cls.__module__] reference.
    """
    spec = importlib.util.spec_from_file_location(
        module_name, PROJECT_ROOT / "cores" / "regime_policy.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


# Load the root regime_policy module exactly once for the whole test module.
_RP = _load_root_regime()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_bars(n_flat: int, n_dd: int):
    """Synthetic bars: n_flat flat sessions then n_dd distribution days.

    MarketPulse.feed duck-types each bar (.date/.close/.volume), so a plain
    SimpleNamespace works without depending on the (shadowed) cores.market_pulse
    DailyBar class.
    """
    bars = []
    close = 100.0
    vol = 1_000.0
    for i in range(n_flat):
        bars.append(types.SimpleNamespace(
            date=f"2023-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}", close=close, volume=vol))
    for j in range(n_dd):
        close = round(close * 0.99, 6)
        vol += 100.0
        bars.append(types.SimpleNamespace(
            date=f"2024-{(j // 28) + 1:02d}-{(j % 28) + 1:02d}", close=close, volume=vol))
    return bars


def _fake_ohlcv_us(n: int = 70) -> pd.DataFrame:
    """Minimal OHLCV DataFrame for patching cores.us_data_client.get_us_data_client."""
    dates = pd.date_range("2026-01-01", periods=n)
    return pd.DataFrame({
        "Close":  [150.0 + i * 0.5 for i in range(n)],
        "Open":   [149.0 + i * 0.5 for i in range(n)],
        "High":   [151.0 + i * 0.5 for i in range(n)],
        "Low":    [148.0 + i * 0.5 for i in range(n)],
        "Volume": [5_000_000] * n,
        "close":  [150.0 + i * 0.5 for i in range(n)],
    }, index=dates)


# ---------------------------------------------------------------------------
# Part A: get_market_pulse_detail on the ROOT cores/regime_policy.py
# ---------------------------------------------------------------------------

def test_get_market_pulse_detail_exists_in_root_regime_policy():
    """FAILS pre-fix: get_market_pulse_detail / MarketPulseDetail don't exist yet."""
    rp = _RP
    assert hasattr(rp, "get_market_pulse_detail"), (
        "get_market_pulse_detail not found in root cores/regime_policy.py"
    )
    assert hasattr(rp, "MarketPulseDetail"), (
        "MarketPulseDetail not found in root cores/regime_policy.py"
    )


def test_get_market_pulse_detail_returns_state_and_dd_us():
    """Post-fix: US index branch returns MarketPulseDetail(state/distribution_days/window)."""
    rp = _RP
    rp._reset_state_cache()
    bars = _make_bars(n_flat=60, n_dd=2)

    with patch.object(rp, "_fetch_us_bars", return_value=bars):
        detail = rp.get_market_pulse_detail("us", use_cache=False)

    assert detail is not None, "get_market_pulse_detail returned None for valid US bars"
    assert isinstance(detail.state, str), f"state should be str, got {type(detail.state)}"
    assert isinstance(detail.distribution_days, int), (
        f"distribution_days should be int, got {type(detail.distribution_days)}"
    )
    assert detail.distribution_days >= 0
    assert isinstance(detail.window, int), (
        f"window should be int, got {type(detail.window)}"
    )
    assert detail.window > 0


def test_get_market_pulse_detail_fail_open_returns_none_us():
    """Post-fix: exception in the US branch -> fail-open None (never raises)."""
    rp = _RP
    rp._reset_state_cache()

    with patch.object(rp, "_fetch_us_bars", side_effect=RuntimeError("simulated network failure")):
        detail = rp.get_market_pulse_detail("us", use_cache=False)

    assert detail is None, "fail-open: should return None on exception, not raise"


# ---------------------------------------------------------------------------
# Part B: US _get_trend_facts — distribution_days number in prompt output
#
# _get_trend_facts calls _import_from_main_cores("prism_root_regime_policy", ...)
# to load regime_policy fresh. We intercept that call via a side_effect and
# return a controlled fake module, delegating all other module loads to the
# real _import_from_main_cores so us_stock_tracking_agent initialises normally.
# ---------------------------------------------------------------------------

def _make_regime_patcher(us_mod, get_detail_return):
    """Build a side_effect for patching us_mod._import_from_main_cores.

    Returns a controlled fake module when called with ``"prism_root_regime_policy"``;
    delegates every other module load to the real implementation.
    Uses ``_RP.MarketPulseDetail`` (the once-loaded class) so the frozen dataclass
    sys.modules reference is always valid.
    """
    real_import = us_mod._import_from_main_cores  # capture before patch

    def _side_effect(module_name, relative_path):
        if module_name == "prism_root_regime_policy":
            fake_rp = MagicMock()
            fake_rp.MarketPulseDetail = _RP.MarketPulseDetail
            fake_rp.get_market_pulse_detail.return_value = get_detail_return
            return fake_rp
        return real_import(module_name, relative_path)

    return _side_effect


def test_us_trend_facts_contains_distribution_days_number(tmp_path):
    """Post-fix: US _get_trend_facts output includes the numeric dd count.

    FAILS pre-fix: the Market Pulse injection block is absent, so neither "5"
    nor the "分散日"/"distribution days" label appear in the output.
    """
    fake_detail = _RP.MarketPulseDetail(state="UPTREND", distribution_days=5, window=25)
    fake_ohlcv = _fake_ohlcv_us(70)

    import us_stock_tracking_agent as us_mod
    from us_stock_tracking_agent import USStockTrackingAgent

    mock_client = MagicMock()
    mock_client.get_ohlcv.return_value = fake_ohlcv
    mock_client.get_index_data.return_value = None

    db_path = str(tmp_path / "test_us_trend.sqlite")
    agent = USStockTrackingAgent(db_path=db_path)

    with patch.object(sys.modules["cores.us_data_client"], "get_us_data_client",
                      return_value=mock_client), \
         patch.object(us_mod, "_import_from_main_cores",
                      side_effect=_make_regime_patcher(us_mod, fake_detail)):
        result = agent._get_trend_facts("AAPL")

    assert result, "US trend_facts should not be empty"
    assert "5" in result, (
        f"distribution_days count (5) not found in US trend_facts output.\n"
        f"Output:\n{result}"
    )
    assert "분산일" in result or "distribution days" in result, (
        f"distribution_days label not found in US trend_facts output.\n"
        f"Output:\n{result}"
    )


def test_us_trend_facts_omits_market_pulse_line_when_detail_none(tmp_path):
    """Post-fix: when get_market_pulse_detail returns None, Market Pulse line is omitted.

    FAILS pre-fix (function doesn't exist, so any patch attempt raises AttributeError).
    Post-fix: fail-open — the Market Pulse line is silently skipped.
    """
    fake_ohlcv = _fake_ohlcv_us(70)

    import us_stock_tracking_agent as us_mod
    from us_stock_tracking_agent import USStockTrackingAgent

    mock_client = MagicMock()
    mock_client.get_ohlcv.return_value = fake_ohlcv
    mock_client.get_index_data.return_value = None

    db_path = str(tmp_path / "test_us_trend_none.sqlite")
    agent = USStockTrackingAgent(db_path=db_path)

    with patch.object(sys.modules["cores.us_data_client"], "get_us_data_client",
                      return_value=mock_client), \
         patch.object(us_mod, "_import_from_main_cores",
                      side_effect=_make_regime_patcher(us_mod, None)):
        result = agent._get_trend_facts("AAPL")

    assert result, "US trend_facts should not be empty (fail-open on None detail)"
    assert "Market Pulse:" not in result, (
        f"Market Pulse line should be omitted when detail=None.\nOutput:\n{result}"
    )
