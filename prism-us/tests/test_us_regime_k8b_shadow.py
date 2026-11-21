"""K8b US regime label (SHADOW only, H-1110-us-regime-v3).

Synthetic series cover every stage branch and the missing-data paths; the
research-label match runs only where the local research data exists.
"""
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cores.data_prefetch as dp  # noqa: E402
from cores.data_prefetch import _compute_us_regime, _k8b_index_label  # noqa: E402

K8B_KEYS = {"regime_k8b_shadow", "regime_k8b_shadow_sp", "regime_k8b_shadow_ndx",
            "k8b_ibd_state", "k8b_shadow_missing_reason", "k8b_mode", "legacy_regime",
            "k8b_live_applied"}


def _df(closes):
    idx = pd.bdate_range("2024-01-01", periods=len(closes))
    return pd.DataFrame({"Close": closes, "Volume": np.full(len(closes), 1e9)}, index=idx)


RALLY = list(np.linspace(100, 200, 200))
SERIES = {
    "strong_bull": np.linspace(100, 200, 300),
    "sideways": np.tile(np.r_[np.linspace(100, 110, 13), np.linspace(110, 100, 12)], 12),
    "strong_bear": np.array(RALLY + list(np.linspace(200, 130, 60))),
    # -25% but already +25% off the low: not strong_bear, no higher high yet
    "moderate_bear_rebound": np.array(RALLY + list(np.linspace(200, 120, 40)) + list(np.linspace(120, 150, 20))),
    "moderate_bear": np.array(RALLY + list(np.linspace(200, 175, 60))),
    # -11% from the high but trending up again (higher high, above MA50): K8b drops moderate_bear
    "up_in_drawdown": np.array(RALLY + list(np.linspace(200, 150, 10)) + [150.0] * 30 + list(np.linspace(150, 178, 25))),
}


@pytest.mark.parametrize("name,ibd_up,expected", [
    ("strong_bull", True, "strong_bull"),
    ("strong_bull", False, "moderate_bull"),
    ("sideways", True, "sideways"),
    ("strong_bear", True, "strong_bear"),
    ("moderate_bear_rebound", True, "moderate_bear"),
    ("moderate_bear", True, "moderate_bear"),
    ("up_in_drawdown", False, "moderate_bull"),
])
def test_index_label_branches(name, ibd_up, expected):
    assert _k8b_index_label(SERIES[name], ibd_up) == expected


def test_off_by_default_leaves_result_unchanged(monkeypatch):
    sp, nd = _df(SERIES["strong_bull"]), _df(SERIES["strong_bull"])
    monkeypatch.delenv("US_REGIME_K8B_MODE", raising=False)
    off = _compute_us_regime(sp, nd, None)
    assert not K8B_KEYS & (set(off) | set(off["index_summary"]))
    monkeypatch.setenv("US_REGIME_K8B_MODE", "shadow")
    shadow = _compute_us_regime(sp, nd, None)
    stripped = {k: v for k, v in shadow.items() if k not in K8B_KEYS}
    stripped["index_summary"] = {k: v for k, v in shadow["index_summary"].items() if k not in K8B_KEYS}
    assert stripped == off  # no existing key or value changes


def test_live_uses_k8b_label_everywhere(monkeypatch):
    sp, nd = _df(SERIES["strong_bull"]), _df(SERIES["sideways"])
    monkeypatch.setenv("US_REGIME_K8B_MODE", "shadow")
    legacy = _compute_us_regime(sp, nd, None)["market_regime"]
    monkeypatch.setenv("US_REGIME_K8B_MODE", "live")
    r = _compute_us_regime(sp, nd, None)
    assert legacy != "sideways" and r["legacy_regime"] == legacy
    for key in ("market_regime", "primary_trend_regime", "effective_entry_regime"):
        assert r[key] == "sideways"
    assert r["index_summary"]["effective_entry_regime"] == "sideways"
    assert r["k8b_live_applied"] is True


def test_live_falls_back_to_legacy_when_k8b_unknown(monkeypatch):
    monkeypatch.setenv("US_REGIME_K8B_MODE", "live")
    r = _compute_us_regime(_df(SERIES["strong_bull"]), None, None)
    assert r["regime_k8b_shadow"] is None
    assert r["market_regime"] == r["legacy_regime"] and r["k8b_live_applied"] is False


def test_shadow_replays_market_pulse(monkeypatch):
    monkeypatch.setenv("US_REGIME_K8B_MODE", "shadow")
    r = _compute_us_regime(_df(SERIES["strong_bull"]), _df(SERIES["sideways"]), None)
    assert r["k8b_ibd_state"] == "UPTREND"
    assert (r["regime_k8b_shadow_sp"], r["regime_k8b_shadow_ndx"]) == ("strong_bull", "sideways")
    assert r["regime_k8b_shadow"] == "sideways"  # the weaker index wins
    assert r["k8b_shadow_missing_reason"] is None
    assert r["index_summary"]["regime_k8b_shadow"] == "sideways"


@pytest.mark.parametrize("nasdaq,closes,reason", [
    (None, SERIES["strong_bull"], "nasdaq_missing"),
    ("same", SERIES["strong_bull"][-251:], "history_short"),
])
def test_missing_data_stays_unknown(monkeypatch, nasdaq, closes, reason):
    monkeypatch.setenv("US_REGIME_K8B_MODE", "shadow")
    nd = _df(closes) if nasdaq else None
    r = _compute_us_regime(_df(closes), nd, None)
    assert r["regime_k8b_shadow"] is None and r["regime_k8b_shadow_sp"] is None
    assert r["k8b_shadow_missing_reason"].startswith(reason)


def test_ibd_unavailable_caps_at_moderate_bull(monkeypatch):
    monkeypatch.setenv("US_REGIME_K8B_MODE", "shadow")

    def boom(*_a, **_k):
        raise RuntimeError("no pulse")

    monkeypatch.setattr(dp, "_k8b_ibd_state", boom)
    r = _compute_us_regime(_df(SERIES["strong_bull"]), _df(SERIES["strong_bull"]), None)
    assert r["k8b_ibd_state"] is None
    assert r["regime_k8b_shadow"] == "moderate_bull"
    assert r["k8b_shadow_missing_reason"].startswith("ibd_state_unavailable")


RESEARCH = Path(os.environ.get(
    "PRISM_K8B_RESEARCH_DIR",
    Path(__file__).resolve().parents[2] / "tasks" / "research-20261110" / "us-regime-v3",
))


@pytest.mark.skipif(not (RESEARCH / "idx.pkl").exists(), reason="local research data only")
@pytest.mark.parametrize("date", [
    "2026-07-15", "2026-05-14", "2022-01-20", "2022-06-16", "2022-10-12",
    "2009-03-18", "2009-04-20", "2009-04-22", "2009-07-22",
])
def test_matches_research_labels_with_production_window(date):
    data = pd.read_pickle(RESEARCH / "idx.pkl")
    labels = pd.read_csv(RESEARCH / "k8b_labels_daily.csv", index_col=0, parse_dates=True)
    d = pd.Timestamp(date)
    lo = d - pd.Timedelta(days=400)  # prefetch_us_macro_intelligence_data history_start
    sp, nd = (data[t].dropna(subset=["Close"])[["Close", "Volume"]].loc[lo:d] for t in ("^GSPC", "^IXIC"))
    out = dp._compute_us_regime_k8b_shadow(sp, "Close", nd)
    row = labels.loc[d]
    assert (out["regime_k8b_shadow_sp"], out["regime_k8b_shadow_ndx"], out["regime_k8b_shadow"]) == (
        row.sp_label, row.ndx_label, row.combined_label)
