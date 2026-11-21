"""Structured trend/volume facts at a decision, the same keys for every stage (observation only).

Prompts carry these facts as text (trend_facts); the ledger needs them as numbers so later analysis
can slice decisions (e.g. "below a falling 50-day line") without parsing prose. Pure: bars in,
dict out. A value that cannot be computed is None and named in ``missing`` - never 0.

``bars``: completed daily bars {date, open, high, low, close, volume} sorted by date, ending at the
last completed session before the decision. ``price``: the decision price (an intraday quote or the
close); facts are measured at that price against completed history only.
"""
from __future__ import annotations

import math

CONTRACT = "decision_snapshot_v1"
SLOPE_LOOKBACK = 10      # MA slope: MA now vs MA ten completed bars earlier
RS_LOOKBACK = 60


def _num(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _pct(a, b):
    return None if a is None or not b else round((a / b - 1) * 100, 4)


def _ma(closes, length, end=None):
    end = len(closes) if end is None else end
    return sum(closes[end - length:end]) / length if end >= length else None


def snapshot(bars, *, price=None, bench=None):
    """Facts at ``price`` (default: last close). ``bench``: index bars for relative strength."""
    rows = [b for b in (bars or []) if _num(b.get("close")) and _num(b.get("high")) and _num(b.get("low"))]
    out, missing = {"contract": CONTRACT}, {}
    if not rows:
        return {**out, "status": "MISSING", "missing": {"bars": 0}}
    closes = [float(b["close"]) for b in rows]
    price = _num(price) or closes[-1]
    out.update(price=round(price, 6), last_completed_date=str(rows[-1]["date"])[:10],
               prev_close=round(closes[-1], 6), move_vs_prev_close_pct=_pct(price, closes[-1]),
               completed_bars=len(rows))
    for length in (10, 20, 50, 200):  # ma10 added 2026-11-09 (O'Neil 10-day line, re-entry research)
        ma = _ma(closes, length)
        before = _ma(closes, length, len(closes) - SLOPE_LOOKBACK) if len(closes) - SLOPE_LOOKBACK >= length else None
        if ma is None:
            missing[f"ma{length}"] = f"needs_{length}_bars"
        out[f"ma{length}"] = round(ma, 6) if ma is not None else None
        out[f"dist_ma{length}_pct"] = _pct(price, ma)
        out[f"ma{length}_slope_{SLOPE_LOOKBACK}d_pct"] = _pct(ma, before)
    out["above_ma50"] = None if out["ma50"] is None else price > out["ma50"]
    slope = out[f"ma50_slope_{SLOPE_LOOKBACK}d_pct"]
    out["ma50_rising"] = None if slope is None else slope > 0
    if len(rows) >= 15:
        ranges = [max(b["high"] - b["low"], abs(b["high"] - p["close"]), abs(b["low"] - p["close"]))
                  for p, b in zip(rows[-15:-1], rows[-14:])]
        out["atr14_pct"] = round(sum(ranges) / 14 / closes[-1] * 100, 4)
    else:
        out["atr14_pct"] = None
        missing["atr14_pct"] = "needs_15_bars"
    for length, key in ((60, "high_60d"), (252, "high_252d")):
        if len(rows) >= length:
            high = max(float(b["high"]) for b in rows[-length:])
            out[key], out[f"dist_{key}_pct"] = round(high, 6), _pct(price, high)
        else:
            out[key] = out[f"dist_{key}_pct"] = None
            missing[key] = f"needs_{length}_bars"
    volumes = [_num(b.get("volume")) for b in rows]
    if len(rows) >= 51 and all(v is not None for v in volumes[-51:]):
        avg = sum(volumes[-51:-1]) / 50
        out["rvol_last_vs_50d"] = round(volumes[-1] / avg, 4) if avg > 0 else None
        up = sum(volumes[k] for k in range(len(rows) - 50, len(rows)) if closes[k] > closes[k - 1])
        down = sum(volumes[k] for k in range(len(rows) - 50, len(rows)) if closes[k] < closes[k - 1])
        out["updown_volume_ratio_50"] = round(up / down, 4) if down > 0 else None
    else:
        out["rvol_last_vs_50d"] = out["updown_volume_ratio_50"] = None
        missing["volume_50d"] = "needs_51_bars_with_volume"
    bench_closes = [float(b["close"]) for b in (bench or []) if _num(b.get("close"))]
    if len(closes) > RS_LOOKBACK and len(bench_closes) > RS_LOOKBACK:
        own = price / closes[-RS_LOOKBACK - 1] - 1
        index = bench_closes[-1] / bench_closes[-RS_LOOKBACK - 1] - 1
        out["rs_60d_pctp"] = round((own - index) * 100, 4)
    else:
        out["rs_60d_pctp"] = None
        missing["rs_60d_pctp"] = "needs_61_bars_and_index"
    out["downtrend_rally"] = downtrend_rally(rows, bench)
    out["status"] = "OK" if not missing else "PARTIAL"
    out["missing"] = missing
    return out


DOWNTREND_RALLY_CONTRACT = "downtrend_rally_v1"
RALLY_FAIL_RECENT_BARS = 3   # research S4: a rally-fail fire on the judgement bar or the two before it
_DOWNTREND_RALLY_MIN_BARS = 26   # ATR20/MA20 plus the 5-bar MA20 slope


def downtrend_rally(rows, bench=None):
    """H-1114-nobuy-rally-in-downtrend (user decision 2026-11-15, record only, never a gate): the rally-fail
    short setup mirrored as 'do not buy' flags, on completed bars. S1 crash_switch (close 3 ATR20 under the
    20-day high, held until a close above MA20), S2 = S1 and MA20 below its value 5 bars ago, S3 = S2 and the
    close within 1 ATR20 of (or above) MA20, S4 = a KQ_RFAIL-style fire within RALLY_FAIL_RECENT_BARS; M2 =
    S2 on the index. Same functions as the inverse lane (prism_inverse.domain.signals), so a review can
    re-derive every flag from the bars. Research: tasks/research-20261114/bear-nobuy/filter.py ``states``."""
    from prism_inverse.domain import signals as S

    def states(bars):
        h, l, c = ([float(b[k]) for b in bars] for k in ("high", "low", "close"))
        a = S.ind([b.get("open") for b in bars], h, l, c)   # open is carried, never used
        i = len(c) - 1
        switch = S.crash_switch(a)[i]
        falling = i >= 5 and S._lt(a["ma20"][i], a["ma20"][i - 5])
        near = a["atr"][i] is not None and a["ma20"][i] is not None and c[i] >= a["ma20"][i] - a["atr"][i]
        fires = S.kq_rally_fail(a)
        return {"crash_switch": switch, "ma20_falling_5d": falling, "rally_in_downtrend": switch and falling,
                "rally_near_ma20": switch and falling and near,
                "rally_fail_fired_recent": any(i - f < RALLY_FAIL_RECENT_BARS for f in fires),
                "last_rally_fail_date": str(bars[fires[-1]]["date"])[:10] if fires else None}

    out = {"contract": DOWNTREND_RALLY_CONTRACT, "recent_bars": RALLY_FAIL_RECENT_BARS}
    if len(rows) < _DOWNTREND_RALLY_MIN_BARS:
        return {**out, "status": "MISSING", "missing": {"bars": f"needs_{_DOWNTREND_RALLY_MIN_BARS}_bars"}}
    out.update(states(rows))
    index = [b for b in (bench or []) if _num(b.get("high")) and _num(b.get("low")) and _num(b.get("close"))]
    if len(index) >= _DOWNTREND_RALLY_MIN_BARS:
        idx = states(index)
        out.update(index_crash_switch=idx["crash_switch"], index_ma20_falling_5d=idx["ma20_falling_5d"],
                   index_rally_in_downtrend=idx["rally_in_downtrend"], status="OK")
    else:
        out.update(index_crash_switch=None, index_ma20_falling_5d=None, index_rally_in_downtrend=None,
                   status="PARTIAL", missing={"index": f"needs_{_DOWNTREND_RALLY_MIN_BARS}_bars"})
    return out


def excursion(path, entry, exit_price=None):
    """Max gain / max loss vs ``entry`` over ``path`` bars (plus the exit price), in percent."""
    entry = _num(entry)
    highs = [float(b["high"]) for b in path or [] if _num(b.get("high"))]
    lows = [float(b["low"]) for b in path or [] if _num(b.get("low"))]
    exit_price = _num(exit_price)
    if exit_price is not None:
        highs.append(exit_price)
        lows.append(exit_price)
    if not entry or not highs:
        return {"max_gain_pct": None, "max_loss_pct": None}
    return {"max_gain_pct": round((max(highs) / entry - 1) * 100, 4),
            "max_loss_pct": round((min(lows) / entry - 1) * 100, 4)}


QUARTERLY_EPS_CONTRACT = "quarterly_eps_v1"


def quarterly_eps(latest, prior, *, source, asof, currency, eps_basis, statement_basis=None, reason=None):
    """Latest reported quarter's EPS vs the same quarter a year earlier, as numbers (observation only).

    ``latest``/``prior``: {"period": str, "eps": number|None}; ``prior`` None = the prior-year quarter is
    not in the provider table. ``reason`` names why ``latest`` is None. Reported quarters only, as
    published when collected (``asof``); estimates never enter. A growth rate exists only on a positive
    base: a loss or zero base gives ``yoy_pct=None`` and the sign change is in ``change``/``turnaround``.
    """
    out = {"contract": QUARTERLY_EPS_CONTRACT, "period": None, "eps": None, "prior_year_period": None,
           "prior_year_eps": None, "yoy_pct": None, "turnaround": None, "change": None,
           "currency": currency, "eps_basis": eps_basis, "statement_basis": statement_basis,
           "source": source, "asof": asof}
    if not latest or not latest.get("period"):
        return {**out, "status": "MISSING", "missing": {"latest_quarter": reason or "no_reported_quarter"}}
    missing = {}
    out["period"] = str(latest["period"])
    eps = out["eps"] = _num(latest.get("eps"))
    if eps is None:
        missing["eps"] = "not_reported"
    base = None
    if not prior or not prior.get("period"):
        missing["prior_year_quarter"] = "not_in_table"
    else:
        out["prior_year_period"] = str(prior["period"])
        base = out["prior_year_eps"] = _num(prior.get("eps"))
        if base is None:
            missing["prior_year_eps"] = "not_reported"
    status = "OK" if not missing else "PARTIAL"
    if eps is None or base is None:
        missing["yoy_pct"] = missing["turnaround"] = "inputs_missing"
    else:
        out["turnaround"] = base <= 0 < eps
        if base > 0:
            out["yoy_pct"] = round((eps / base - 1) * 100, 4)
            out["change"] = "yoy" if eps > 0 else ("turned_to_loss" if eps < 0 else "fell_to_zero")
        else:
            missing["yoy_pct"] = "base_not_positive"
            out["change"] = ("turned_to_profit" if eps > 0 else
                             "loss_continued" if base < 0 and eps < 0 else "zero_base")
    return {**out, "status": status, "missing": missing}
