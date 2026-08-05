"""The 200-day MA must be stated, or its absence must be stated.

2026-08-04, US afternoon batch, PLTR skip: "다만 하락 중인 50·60일선과 200일선 저항이
남아 있고". The 50- and 60-day parts were right. The 200-day part was invented, and
backwards — PLTR closed at 162.66 against a 200-day of 152.54, i.e. 6.6% *above* it.

The cause was not a wrong number. It was a missing one. `_get_trend_facts` fetched
6 months (US) / 120 days (KR) of bars, which cannot produce a 200-day average, so the
facts block simply had no MA200 line. The agent is instructed to reason from that
block, and a line that is absent reads as "not worth mentioning" rather than
"unknown" — so it filled the gap, plausibly and wrongly. The market context in the
same prompt does carry a 200-day, but the index's, which is a tempting thing to
borrow.

Price above the 200-day is one of O'Neil's own criteria, so this is not decoration.
These tests hold the two halves of the fix: the window is long enough to compute it,
and when it still cannot be computed the block says so out loud.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

for _n in [
    "mcp_agent",
    "mcp_agent.app",
    "mcp_agent.workflows",
    "mcp_agent.workflows.llm",
    "mcp_agent.workflows.llm.augmented_llm",
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
    "cores.stock_chart",
]:
    sys.modules.setdefault(_n, MagicMock())


def _bars(n: int, start: float = 100.0, step: float = 0.5) -> pd.DataFrame:
    """Steadily rising closes — enough structure for a rising 200-day."""
    dates = pd.date_range("2025-01-01", periods=n, freq="B")
    close = [start + i * step for i in range(n)]
    return pd.DataFrame(
        {
            "Close": close,
            "Open": [c - 1 for c in close],
            "High": [c + 1 for c in close],
            "Low": [c - 2 for c in close],
            "Volume": [1_000_000] * n,
        },
        index=dates,
    )


def _kr_agent(tmp_path, name):
    from stock_tracking_agent import StockTrackingAgent

    return StockTrackingAgent(db_path=str(tmp_path / f"{name}.sqlite"))


def _configure_kr(frame):
    sc = sys.modules["cores.stock_chart"]
    sc.get_market_ohlcv_by_date.reset_mock()
    sc.get_market_ohlcv_by_date.return_value = frame
    sc.get_index_ohlcv_by_date.return_value = None
    sc._detect_index_ticker.return_value = "^KS11"
    return sc


def _kr_facts(tmp_path, name, frame):
    _configure_kr(frame)
    agent = _kr_agent(tmp_path, name)
    with patch("cores.regime_policy.get_market_pulse_detail", return_value=None):
        return agent._get_trend_facts("005930")


# --------------------------------------------------------------------------- KR

def test_kr_states_the_200_day_when_it_can_be_computed(tmp_path):
    facts = _kr_facts(tmp_path, "kr_present", _bars(260))

    assert "MA200" in facts, f"200-day missing from the facts block:\n{facts}"
    ma200_line = next(l for l in facts.splitlines() if "MA200" in l)
    # Rising series: the last close is above its own 200-day, and the average rises.
    assert "위" in ma200_line, ma200_line
    assert "상승" in ma200_line, ma200_line


def test_kr_says_so_when_the_200_day_cannot_be_computed(tmp_path):
    """Silence is what the agent filled in. Absence has to be explicit."""
    facts = _kr_facts(tmp_path, "kr_absent", _bars(70))

    assert "MA200" in facts, f"the absent 200-day still needs a line:\n{facts}"
    ma200_line = next(l for l in facts.splitlines() if "MA200" in l)
    assert "데이터 없음" in ma200_line, ma200_line
    assert "서술하지" in ma200_line, (
        f"the block must tell the agent not to narrate it:\n{ma200_line}"
    )


def test_kr_requests_enough_history_for_a_200_day(tmp_path):
    """120 calendar days (~82 sessions) can never yield a 200-day average."""
    sc = _configure_kr(_bars(260))
    agent = _kr_agent(tmp_path, "kr_window")
    with patch("cores.regime_policy.get_market_pulse_detail", return_value=None):
        agent._get_trend_facts("005930")

    start, end = sc.get_market_ohlcv_by_date.call_args[0][:2]
    span_days = (pd.Timestamp(end) - pd.Timestamp(start)).days
    assert span_days >= 400, (
        f"requested only {span_days} calendar days; a 200-session average needs ~280+"
    )


def test_kr_keeps_the_shorter_averages(tmp_path):
    """The fix adds a line; it must not disturb the ones the gate already uses."""
    facts = _kr_facts(tmp_path, "kr_others", _bars(260))
    for label in ("MA20", "MA50", "MA60", "T1_hit", "T2_hit"):
        assert label in facts, f"{label} disappeared from the facts block:\n{facts}"


# --------------------------------------------------------------------------- US

def test_us_requests_two_years_of_bars():
    """`period="6mo"` yielded ~124 sessions — short of 200 by construction.

    Scoped to the ticker's own bars: the S&P 500 fetch a few lines below still
    asks for 6mo, and rightly so — it only feeds a 60-session relative-strength
    comparison and needs no 200-day.
    """
    source = (PROJECT_ROOT / "prism-us" / "us_stock_tracking_agent.py").read_text()
    assert 'client.get_ohlcv(ticker, period="6mo")' not in source, (
        "6mo cannot produce a 200-day average; the PLTR invention started here"
    )
    assert 'client.get_ohlcv(ticker, period="2y")' in source, (
        "expected the trend-facts fetch to ask for 2y of the ticker's own bars"
    )


def test_us_facts_block_carries_the_200_day():
    source = (PROJECT_ROOT / "prism-us" / "us_stock_tracking_agent.py").read_text()
    assert "ma200_s = close_s.rolling(window=200).mean()" in source
    assert "_ma200_line()" in source, "the MA200 line must be in the emitted block"
    assert "데이터 없음" in source, "absence must be stated, not left silent"


# ------------------------------------------------------------------- prompt guard

@pytest.mark.parametrize(
    "path",
    [
        "cores/agents/trading_agents.py",
        "prism-us/cores/agents/trading_agents.py",
    ],
)
def test_prompt_forbids_citing_absent_moving_averages(path):
    """Belt and braces: a newly listed name will still have no 200-day."""
    # The prompts are wrapped prose, so line breaks fall in different places in
    # each file. Compare on collapsed whitespace rather than on the wrapping.
    source = " ".join((PROJECT_ROOT / path).read_text().split())
    assert "Only cite a moving average that appears in the facts block" in source, (
        f"{path} does not forbid citing averages that were never provided"
    )
    assert "index's 200-day MA is the index's" in source, (
        f"{path} does not warn against borrowing the index's 200-day for the stock"
    )
