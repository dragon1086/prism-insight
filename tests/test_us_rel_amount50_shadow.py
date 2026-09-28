"""#822 step 3 SHADOW: turnover vs own 50-session average is recorded, never scored."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "prism-us"), str(ROOT)]


def _batch():
    with patch("dotenv.load_dotenv", return_value=False):
        import us_trigger_batch as batch
    return batch


def _history(last_date="2026-09-14", sessions=61, today_amount_multiple=3.0):
    index = pd.bdate_range(end=last_date, periods=sessions)
    close = np.full(sessions, 10.0)
    volume = np.full(sessions, 1_000_000.0)
    volume[-1] *= today_amount_multiple
    return pd.DataFrame({"Open": close, "High": close + 1, "Low": close - 1,
                         "Close": close, "Volume": volume}, index=index)


def test_ratio_uses_the_previous_50_sessions_and_requires_the_trade_date_bar():
    batch = _batch()
    assert batch._rel_amount50(_history(), "20260914") == 3.0
    assert batch._rel_amount50(_history(), "20260915") is None          # today's bar missing
    assert batch._rel_amount50(_history(sessions=30), "20260914") is None  # < 40 prior sessions


def test_capture_records_live_order_without_changing_final_scores():
    batch = _batch()
    frame = pd.DataFrame({"Close": [10.0, 10.0, 10.0], "Open": 9.5, "High": 10.5, "Low": 9.0,
                          "Volume": 1e6, "Amount": 1e7, "CompositeScore": [0.9, 0.5, 0.1]},
                         index=["AAA", "BBB", "CCC"])
    histories = {"AAA": _history(today_amount_multiple=1.0), "BBB": _history(today_amount_multiple=4.0),
                 "CCC": _history(last_date="2026-09-11")}
    agent = lambda df, *a, **k: df.copy()
    with patch.object(batch, "get_multi_day_ohlcv", side_effect=lambda t, *a, **k: histories[t]), \
         patch.object(batch, "score_candidates_by_agent_criteria", side_effect=agent), \
         patch.object(batch, "get_us_sector_map", return_value={}):
        baseline = batch.select_final_tickers({"Volume Surge Top": frame.copy()}, trade_date="20260914")
        capture = {}
        shadow = batch.select_final_tickers({"Volume Surge Top": frame.copy()}, trade_date="20260914",
                                            rel_amount_capture=capture)
    assert {k: list(v.index) for k, v in baseline.items()} == {k: list(v.index) for k, v in shadow.items()}
    rows = capture["Volume Surge Top"]
    assert [r[0] for r in rows] == sorted([r[0] for r in rows], key=lambda t: -[x for x in rows if x[0] == t][0][1])
    ratios = {ticker: ratio for ticker, _score, ratio in rows}
    assert ratios == {"AAA": 1.0, "BBB": 4.0, "CCC": None}


def test_elapsed_minutes_is_none_for_replays():
    batch = _batch()
    assert batch._session_elapsed_minutes("20260914", "20260914") is None
