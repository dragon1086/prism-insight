"""prism_core.us_regime_k8b: the shared K8b label and the bull-age replay (H-1110)."""
import numpy as np

from prism_core import us_regime_k8b as K


def _rising(n=320):
    return np.linspace(100, 200, n)


def test_index_label_rising_series_is_bull_and_short_history_is_missing():
    assert K.index_label(_rising(), True) == "strong_bull"
    assert K.index_label(_rising(), False) == "moderate_bull"
    assert K.combined_label(_rising(), _rising(), True) == "strong_bull"
    assert K.combined_label(_rising(), _rising(100), True) is None
    crash = np.concatenate([_rising(300), np.full(20, 140.0)])     # -30% from the 252-session peak
    assert K.index_label(crash, True) == "strong_bear"
    assert K.combined_label(_rising(), crash, True) == "strong_bear"   # the lower of the two indexes


def test_bull_age_counts_trailing_bull_sessions_up_to_the_horizon():
    labels = ["sideways"] * 10 + ["moderate_bull"] * 3 + ["strong_bull"] * 4
    fn = lambda sp, nd, up: labels[len(sp) - 1]  # noqa: E731
    sp = list(range(len(labels))); states = ["UPTREND"] * len(labels)
    assert K.bull_age(sp, sp, states, label_fn=fn) == {"label": "strong_bull", "bull_age": 7}
    assert K.bull_age(sp[:10], sp[:10], states[:10], label_fn=fn) == {"label": "sideways", "bull_age": 0}
    assert K.bull_age(sp, sp, states, horizon=5, label_fn=fn)["bull_age"] == 5
    assert K.bull_age(sp, sp, states, label_fn=lambda *a: None) == {"label": None, "bull_age": None}
    assert K.bull_age([], [], [], label_fn=fn) == {"label": None, "bull_age": None}
