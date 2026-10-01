"""Pure-function checks for tools/replay_kr_micro_split_b3.py (no KIS, no DB)."""
import importlib.util
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from prism_core.oneil_adaptive_policy import initial_sizing

_SOURCE = Path(__file__).resolve().parents[1] / "tools" / "replay_kr_micro_split_b3.py"
_SPEC = importlib.util.spec_from_file_location("replay_kr_micro_split_b3", _SOURCE)
tool = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(tool)


def _bars(closes, start="2026-08-01", spread=2.0):
    day = date.fromisoformat(start)
    out = []
    for c in closes:
        out.append({"date": day.isoformat(), "high": c + spread / 2, "low": c - spread / 2, "close": c})
        day += timedelta(days=1)
    return out


def _trade(buy_idx, sell_idx, bars, buy_price, sell_price, regime="strong_bull: KOSPI"):
    return {"id": 1, "ticker": "000001", "buy_date": bars[buy_idx]["date"] + " 10:00:00",
            "sell_date": bars[sell_idx]["date"] + " 09:30:00", "buy_price": buy_price,
            "sell_price": sell_price, "regime": regime}


def test_bull_regime_mapping_matches_preregistration():
    assert tool.is_bull("parabolic: KOSPI가 20일선 위")
    assert tool.is_bull("상승추세(강세장): KOSPI 2026")
    assert not tool.is_bull("sideways")
    assert not tool.is_bull("moderate_bear - 보고서상")
    assert not tool.is_bull(None)


def test_pyramid_add_rows_are_dropped():
    rows = [{"id": 1, "ticker": "A", "buy_date": "2026-01-02 10:00", "sell_date": "2026-01-20 10:00"},
            {"id": 2, "ticker": "A", "buy_date": "2026-01-10 10:00", "sell_date": "2026-01-20 10:00"},
            {"id": 3, "ticker": "A", "buy_date": "2026-01-21 10:00", "sell_date": "2026-01-25 10:00"}]
    assert [r["id"] for r in tool.drop_pyramid_adds(rows)] == [1, 3]


def test_losing_trade_keeps_initial_only_and_loses_less():
    bars = _bars([100.0] * 30 + [98, 97, 95, 93])
    trade = _trade(30, 33, bars, 100.0, 92.0)
    result, reason = tool.simulate(trade, bars, initial_sizing)
    assert reason is None and result["adds"] == 0
    assert result["full"] == pytest.approx(-0.08)
    assert result["b3"] == pytest.approx(result["initial"] * -0.08)
    assert 0.35 <= result["initial"] <= 0.80


def test_winner_adds_to_100_in_bull_but_not_in_sideways():
    closes = [100.0] * 30 + [101, 102.5, 104.5, 106, 108]
    bars = _bars(closes)
    bull, _ = tool.simulate(_trade(30, 34, bars, 100.0, 110.0), bars, initial_sizing)
    side, _ = tool.simulate(_trade(30, 34, bars, 100.0, 110.0, regime="sideways"), bars, initial_sizing)
    assert bull["final_fraction"] == pytest.approx(1.0) and bull["adds"] >= 1
    assert side["adds"] == 0 and side["final_fraction"] == pytest.approx(side["initial"])
    assert bull["b3"] < bull["full"]           # adds at higher prices earn less than a full first entry
    assert bull["b3_net"] < bull["b3"]         # add cost is charged


def test_add_needs_close_above_sma20_and_within_band():
    closes = [120.0] * 20 + [100.0] * 10 + [104.5, 104.6]   # SMA20 still above 104 -> no add
    bars = _bars(closes)
    result, _ = tool.simulate(_trade(30, 31, bars, 100.0, 105.0), bars, initial_sizing)
    assert result["adds"] == 0
    bars = _bars([100.0] * 31 + [112.0, 113.0])              # day after entry is above the +10% band
    result, _ = tool.simulate(_trade(30, 32, bars, 100.0, 113.0), bars, initial_sizing)
    assert result["adds"] == 0


def test_verdict_rules():
    base = {"n": 31, "loss_reduction": 0.4, "mdd_b3": -0.01, "mdd_full": -0.03,
            "diff_net": -0.001, "diff_net_without_best": -0.0015}
    assert tool.verdict(base) == "LIMITED_LIVE_REVIEW"
    assert tool.verdict({**base, "n": 12}) == "CONTINUE_CAPTURE"
    assert tool.verdict({**base, "loss_reduction": 0.1}) == "RETIRE"
    assert tool.verdict({**base, "diff_net": -0.004}) == "RETIRE"
    assert tool.verdict({**base, "mdd_b3": -0.05}) == "RETIRE"


def test_replay_end_to_end(tmp_path):
    bars = _bars([100.0] * 30 + [98, 95, 93, 101, 103, 105])
    rows = [_trade(30, 32, bars, 100.0, 93.0), {**_trade(33, 35, bars, 101.0, 105.0), "id": 2}]
    (tmp_path / "t.json").write_text(json.dumps({"rows": rows}))
    (tmp_path / "b.json").write_text(json.dumps({"bars": {"000001": bars}}))
    tool.replay(tmp_path / "t.json", tmp_path / "b.json", tmp_path / "o.json")
    summary = json.loads((tmp_path / "o.json").read_text())["summary"]
    assert summary["used"] == 2 and summary["holdout"]["n"] == 2
    assert summary["verdict"] in ("CONTINUE_CAPTURE", "RETIRE", "LIMITED_LIVE_REVIEW")
