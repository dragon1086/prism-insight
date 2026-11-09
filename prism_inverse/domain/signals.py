"""KR inverse-index signals (S4 KQ_F1, S6 driver sector, S11 KQ_X10), ported from the research engine.

Source: tasks/research-20261109/inverse-s3/s4_track.py (``ind``, ``second_breaks``) and
s6_driver.py (``group_frame``, KR driver selection). Daily series aligned to one calendar,
oldest first; ``None`` is pandas' NaN (unknown), so every comparison with it is False.
"""

from __future__ import annotations

import math
from typing import Callable, Optional, Sequence

Series = Sequence[Optional[float]]

FAMILIES = ("KQ_F1", "KQ_DRIVER", "K200_DRIVER", "KQ_X10")
WINDOW, EPISODE = 100, 120          # re-break must come within 100 sessions; episode ends after 120
LEADER_GAIN, LEADER_RANK, LEADER_LOOKBACK = 0.15, 2 / 3, 120
CORR_WINDOW, CORR_MIN = 60, 48


def rolling_mean(xs: Series, n: int) -> list[Optional[float]]:
    """pandas ``rolling(n).mean()``: needs ``n`` known values in the window."""
    out = []
    for i in range(len(xs)):
        w = xs[max(0, i - n + 1): i + 1]
        out.append(sum(w) / n if len(w) == n and None not in w else None)
    return out


def rolling_max(xs: Series, n: int, min_periods: int) -> list[Optional[float]]:
    out = []
    for i in range(len(xs)):
        w = [x for x in xs[max(0, i - n + 1): i + 1] if x is not None]
        out.append(max(w) if len(w) >= min_periods else None)
    return out


def ind(o: Series, h: Series, l: Series, c: Series) -> dict:
    """s4_track.ind: ATR20 (mean true range), MA10, MA50, PEAK120 (min 60 sessions)."""
    tr = []
    for i in range(len(c)):
        if None in (h[i], l[i]):
            tr.append(None)
            continue
        parts = [h[i] - l[i]]
        if i and c[i - 1] is not None:
            parts += [abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])]
        tr.append(max(parts))
    return {"o": list(o), "c": list(c), "atr": rolling_mean(tr, 20), "ma10": rolling_mean(c, 10), "ma50": rolling_mean(c, 50),
            "peak": rolling_max(c, 120, 60)}


def _lt(a, b) -> bool:
    return a is not None and b is not None and a < b


def second_breaks(c: Series, ma: Series, pk: Series, ok_break: Callable[[int], bool],
                  weak: Optional[Sequence[bool]] = None, window: int = WINDOW, rearm: bool = False) -> list[int]:
    """Break -> reclaim above MA50 -> re-break within ``window``. Returns fire indices.

    Same state machine as research, which skipped the last bar only because it needed the next
    open for entry; live evaluates through today, so the last bar is included.
    """
    out, st, b1, pkb = [], None, 0, 0.0
    for i in range(len(c)):
        if c[i] is None or ma[i] is None:
            continue
        if st is None:
            if ok_break(i):
                st, b1, pkb = "B", i, pk[i]
        elif c[i] >= pkb or i - b1 > EPISODE:
            st = None
        elif (st == "B" or (rearm and st == "D")) and c[i] > ma[i]:
            st = "R"
        elif st == "R" and c[i] < ma[i] and (weak is None or weak[i]) and i - b1 <= window:
            st = "D"
            out.append(i)
    return out


def kq_f1(a: dict) -> list[int]:
    """KOSDAQ index second break: break = below MA50 and at least 3 ATR under the 120-day peak."""
    c, ma, pk, at = a["c"], a["ma50"], a["peak"], a["atr"]
    return second_breaks(c, ma, pk, lambda i: _lt(c[i], ma[i]) and pk[i] is not None and at[i] is not None
                         and c[i] <= pk[i] - 3 * at[i])


def kq_x10(a: dict) -> list[int]:
    """S11 (2026-11-09, exploratory): first close below the 10-day line (the day before was not below it)."""
    c, m = a["c"], a["ma10"]
    return [i for i in range(1, len(c)) if _lt(c[i], m[i]) and c[i - 1] is not None and m[i - 1] is not None
            and not c[i - 1] < m[i - 1]]


def _pct_rank(values: dict) -> dict:
    """pandas ``rank(pct=True)`` (average ties) over the known values."""
    known = sorted(v for v in values.values() if v is not None)
    n = len(known)
    out = {}
    for g, v in values.items():
        if v is None:
            out[g] = None
            continue
        below = sum(1 for x in known if x < v)
        ties = sum(1 for x in known if x == v)
        out[g] = (below + (ties + 1) / 2) / n
    return out


def sector_panel(closes: dict, bench: Series) -> dict:
    """s6_driver.group_frame: per sector ma50, peak, leader, weak (dict of lists, same calendar)."""
    groups = list(closes)
    T = len(bench)
    ma = {g: rolling_mean(closes[g], 50) for g in groups}
    peak = {g: rolling_max(closes[g], 120, 60) for g in groups}
    raw = {g: [False] * T for g in groups}
    for t in range(T):
        r126 = {}
        for g in groups:
            c = closes[g]
            r126[g] = c[t] / c[t - 126] - 1 if t >= 126 and c[t] is not None and c[t - 126] is not None else None
        rank = _pct_rank(r126)
        for g in groups:
            raw[g][t] = rank[g] is not None and rank[g] >= LEADER_RANK and r126[g] >= LEADER_GAIN
    leader = {g: [any(raw[g][max(0, t - LEADER_LOOKBACK + 1): t + 1]) for t in range(T)] for g in groups}
    weak = {}
    for g in groups:
        rs = [c / m if c is not None and m else None for c, m in zip(closes[g], bench)]
        rs_ma = rolling_mean(rs, 50)
        weak[g] = [_lt(r, s) for r, s in zip(rs, rs_ma)]
    return {"ma50": ma, "peak": peak, "leader": leader, "weak": weak}


def sector_breaks(closes: dict, panel: dict, rearm: bool = True) -> dict:
    """Leader-sector second breaks: break = leader, below MA50, 10% under peak, weak vs benchmark."""
    out = {}
    for g, c in closes.items():
        ma, pk, ld, wk = (panel[k][g] for k in ("ma50", "peak", "leader", "weak"))
        ok = lambda i, c=c, ma=ma, pk=pk, ld=ld, wk=wk: (ld[i] and _lt(c[i], ma[i]) and pk[i] is not None
                                                         and c[i] <= 0.9 * pk[i] and wk[i])
        out[g] = set(second_breaks(c, ma, pk, ok, wk, rearm=rearm))
    return out


def returns(c: Series) -> list[Optional[float]]:
    return [None] + [b / a - 1 if a and b is not None else None for a, b in zip(c[:-1], c[1:])]


def corr_at(x: Series, y: Series, t: int, n: int = CORR_WINDOW, min_periods: int = CORR_MIN) -> Optional[float]:
    """pandas rolling(n, min_periods).corr at row t: Pearson over pairs both known."""
    pairs = [(a, b) for a, b in zip(x[max(0, t - n + 1): t + 1], y[max(0, t - n + 1): t + 1])
             if a is not None and b is not None]
    if len(pairs) < min_periods:
        return None
    k = len(pairs)
    ma, mb = sum(a for a, _ in pairs) / k, sum(b for _, b in pairs) / k
    cov = sum((a - ma) * (b - mb) for a, b in pairs)
    va, vb = sum((a - ma) ** 2 for a, _ in pairs), sum((b - mb) ** 2 for _, b in pairs)
    if va <= 0 or vb <= 0:
        return None
    r = cov / math.sqrt(va * vb)
    return r if math.isfinite(r) else None


def driver_at(closes: dict, leader: dict, index_close: Series, t: int) -> tuple[Optional[str], Optional[float]]:
    """Leader sector most correlated (60-day daily returns) with the index on day t; first wins ties."""
    xr = returns(index_close)
    best, best_r = None, None
    for g, c in closes.items():
        if not leader[g][t]:
            continue
        r = corr_at(returns(c), xr, t)
        if r is not None and (best_r is None or r > best_r):
            best, best_r = g, r
    return best, best_r
