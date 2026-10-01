"""Pure-function checks for tools/replay_kr_intraday_volume_pace.py (no KIS, no DB)."""
import importlib.util
import json
from pathlib import Path

import pytest

_SOURCE = Path(__file__).resolve().parents[1] / "tools" / "replay_kr_intraday_volume_pace.py"
_SPEC = importlib.util.spec_from_file_location("replay_kr_intraday_volume_pace", _SOURCE)
tool = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(tool)


def _daily(day, final, gap=0.0, prev_close=100.0):
    rows = [{"date": f"202609{d:02d}", "open": prev_close, "close": prev_close, "volume": 1000.0}
            for d in range(1, 21)]
    rows.append({"date": day, "open": prev_close * (1 + gap), "close": prev_close, "volume": final})
    return rows


def test_cumulative_counts_only_bars_started_before_the_minute():
    cum = tool.cumulative({"090000": 10, "090100": 5, "100700": 7, "100800": 100})
    assert cum[0] == 0 and cum[1] == 10 and cum[2] == 15
    m = tool.minute_index("100857")       # decision at 10:08:57
    assert m == 68 and cum[m] == 22       # the 10:08 bar is still in progress


def test_day_features_use_prior_twenty_sessions_and_gap_bucket():
    row = {"analyzed_date": "2026-09-30 10:08:57"}
    feature, reason = tool.day_features(row, _daily("20260930", 980.0, gap=0.035), {"090000": 600, "100000": 25})
    assert reason is None
    assert feature["a20"] == 1000.0 and feature["v_final"] == 980.0 and feature["v_prev"] == 1000.0
    assert feature["bucket"] == "GAP" and feature["v_cum"] == 625
    assert tool.day_features(row, _daily("20260930", 980.0), {"error": "Unavailable"}) == (None, "no_bars")
    assert tool.day_features(row, _daily("20260929", 980.0), {"090000": 1})[1] == "no_daily_history"


def test_curves_come_from_training_rows_and_projection_uses_the_bucket():
    def feature(bucket, early_share):
        cum = [0] + [early_share * 100] * 60 + [100] * (tool.SESSION_MINUTES - 60)
        return {"bucket": bucket, "cum": cum, "v_final": 100.0}

    curves = tool.share_curves([feature("GAP", 0.6), feature("GAP", 0.7), feature("NORMAL", 0.25)])
    assert curves["GAP"][30] == pytest.approx(0.65) and curves["NORMAL"][30] == 0.25
    probe = {"bucket": "GAP", "minute": 30, "v_cum": 65.0}
    assert tool.project(probe, curves) == pytest.approx(100.0)
    assert tool.project({**probe, "bucket": "NORMAL"}, curves) == pytest.approx(260.0)


def test_accuracy_and_verdict_thresholds():
    good = [{"v_hat": 2100, "v_final": 2050, "a20": 1000, "v_cum": 1900}] * 12 + \
           [{"v_hat": 900, "v_final": 920, "a20": 1000, "v_cum": 850}] * 20
    m = tool.accuracy(good)
    assert (m["actual_surge"], m["pred_surge"], m["false_positive_rate"], m["recall"]) == (12, 12, 0.0, 1.0)
    assert m["lower_bound_recall"] == 0.0
    assert tool.accuracy_verdict(m) == "ADOPT_CANDIDATE"
    few = tool.accuracy(good[:5] + good[12:])
    assert tool.accuracy_verdict(few) == "INSUFFICIENT"
    noisy = tool.accuracy(good + [{"v_hat": 2500, "v_final": 1200, "a20": 1000, "v_cum": 800}] * 3)
    assert tool.accuracy_verdict(noisy) == "RETIRE"


def test_outcome_verdict_requires_holdout_ci_and_discovery_direction():
    up = {"enough": True, "diff": 0.02, "ci90": [0.005, 0.03]}
    assert tool.outcome_verdict(up, up) == "ADOPT_CANDIDATE"
    assert tool.outcome_verdict({**up, "diff": -0.01}, up) == "RETIRE"
    assert tool.outcome_verdict(up, {**up, "ci90": [-0.01, 0.03]}) == "RETIRE"
    assert tool.outcome_verdict({**up, "enough": False}, up) == "INSUFFICIENT"


def test_replay_end_to_end_on_synthetic_inputs(tmp_path):
    rows, minutes, daily = [], {}, {}
    for i in range(8):
        ticker = f"00000{i}"
        for day, mode, stamp in (("20260415", "morning", "10:00:00"), ("20260810", "afternoon", "14:50:00")):
            rows.append({"id": len(rows), "ticker": ticker, "trigger_type": "x", "trigger_mode": mode,
                         "analyzed_date": f"{day[:4]}-{day[4:6]}-{day[6:]} {stamp}", "analyzed_price": 100,
                         "decision": "Skip", "tracked_7d_return": 0.01 * i, "tracked_14d_return": None})
            minutes[f"{ticker}_{day}"] = {"090000": 300, "120000": 300, "150000": 400}
        daily[ticker] = [{"date": f"2026{d:04d}", "open": 100.0, "close": 100.0, "volume": 1000.0}
                         for d in range(301, 321)]
        daily[ticker] += [{"date": "20260415", "open": 100.0, "close": 100.0, "volume": 1000.0}]
        daily[ticker] += [{"date": f"2026{d:04d}", "open": 100.0, "close": 100.0, "volume": 1000.0}
                          for d in range(716, 736)]
        daily[ticker] += [{"date": "20260810", "open": 100.0, "close": 100.0, "volume": 1000.0}]
    (tmp_path / "d.json").write_text(json.dumps({"rows": rows}))
    (tmp_path / "b.json").write_text(json.dumps({"bars": {"minutes": minutes, "daily": daily}}))
    tool.replay(tmp_path / "d.json", tmp_path / "b.json", tmp_path / "o.json")
    summary = json.loads((tmp_path / "o.json").read_text())["summary"]
    assert summary["used"] == 16 and summary["discovery_n"] == 8 and summary["holdout_n"] == 8
    assert summary["H1"]["verdict"] == "INSUFFICIENT"
    assert summary["H3"]["b_projected_surge"]["verdict"] in ("INSUFFICIENT", "RETIRE_UPPER_BOUND_NOT_ADOPTED")
