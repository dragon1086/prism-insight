"""US K8b regime label (H-1110-us-regime-v3) shared by the US prefetch and the re-entry runner.

Pure functions over close arrays; the caller supplies the combined IBD Market Pulse states
(cores.regime_policy.combined_us_states), one per S&P 500 session.
"""
K8B_ORDER = ("strong_bear", "moderate_bear", "sideways", "moderate_bull", "strong_bull")
K8B_MIN_SESSIONS = 252
BULL = {"moderate_bull", "strong_bull"}


def index_label(closes, ibd_uptrend):
    """K8b stage for the last close (caller guarantees >= 252 closes)."""
    import numpy as np
    closes = np.asarray(closes, float)
    cur = closes[-1]
    window = closes[-K8B_MIN_SESSIONS:]
    p = int(np.argmax(window))
    peak = window[p]
    if cur <= peak * 0.8 and cur < window[p:].min() * 1.2:
        return "strong_bear"
    ma50, ma200 = closes[-50:].mean(), closes[-200:].mean()
    # Dow higher high: max close of last 25 sessions > max of the 25 before.
    up = closes[-25:].max() > closes[-50:-25].max() and cur > ma50
    if cur / peak - 1 <= -0.10 and not up:
        return "moderate_bear"
    if up and ma50 > ma200 and ibd_uptrend:
        return "strong_bull"
    return "moderate_bull" if up else "sideways"


def combined_label(sp_closes, nd_closes, ibd_uptrend):
    """The lower of the S&P 500 and NASDAQ stages, or None when either history is short."""
    if len(sp_closes) < K8B_MIN_SESSIONS or len(nd_closes) < K8B_MIN_SESSIONS:
        return None
    return min(index_label(sp_closes, ibd_uptrend), index_label(nd_closes, ibd_uptrend), key=K8B_ORDER.index)


def bull_age(sp_closes, nd_closes, ibd_states, *, horizon=60, label_fn=combined_label):
    """{"label", "bull_age"} for the last session: consecutive sessions the combined label has been bull.

    ``nd_closes`` and ``ibd_states`` are aligned to ``sp_closes`` (one per S&P session). ``bull_age`` is 0
    when the last label is not bull, counts up to ``horizon`` (a longer run reports ``horizon``), and is
    None (MISSING) when the label itself is unavailable.
    """
    n = len(sp_closes)
    last = label_fn(sp_closes, nd_closes, ibd_states[-1] == "UPTREND") if n else None
    if last is None:
        return {"label": None, "bull_age": None}
    age = 0
    for i in range(n - 1, max(n - 1 - horizon, -1), -1):
        label = last if i == n - 1 else label_fn(sp_closes[:i + 1], nd_closes[:i + 1], ibd_states[i] == "UPTREND")
        if label not in BULL:
            break
        age += 1
    return {"label": last, "bull_age": age}
