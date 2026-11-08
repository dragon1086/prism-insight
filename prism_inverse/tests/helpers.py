"""Synthetic series for lane tests."""

from __future__ import annotations

from datetime import date, timedelta

from prism_inverse.domain.models import Bar, EtfSpec, Instrument

START = date(2020, 1, 1)


def bars(closes, volumes=None, start=START, spread=0.01):
    out, day = [], start
    for i, c in enumerate(closes):
        while day.weekday() >= 5:
            day += timedelta(days=1)
        v = volumes[i] if volumes is not None else 1_000_000.0
        prev = closes[i - 1] if i else c
        out.append(Bar(day, prev, max(prev, c) * (1 + spread), min(prev, c) * (1 - spread), c, v))
        day += timedelta(days=1)
    return out


def run_up_then_break(n_up=150, n_down=120, peak=200.0):
    """Doubles over ``n_up`` sessions, then slides 40% on heavier volume."""
    up = [100 + (peak - 100) * i / (n_up - 1) for i in range(n_up)]
    down = [peak * (1 - 0.4 * (i + 1) / n_down) for i in range(n_down)]
    vols = [1_000_000.0] * n_up + [2_000_000.0] * n_down
    return up + down, vols


def spec(code="114800", market="KR", mult=-1, scope="index", underlying="069500", kind="etf", exchange=None,
         listed=date(2000, 1, 1)):
    return EtfSpec(code, market, code, mult, scope, Instrument(market, underlying, kind, exchange), listed, exchange)
