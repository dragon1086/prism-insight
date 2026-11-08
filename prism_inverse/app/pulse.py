"""Market Pulse states for an underlying, reusing the production state machine unchanged.

``cores/market_pulse.py`` is pure (no I/O). It is loaded by file path because
``prism-us/cores`` can shadow the root ``cores`` package (same reason as
``cores/regime_policy._load_root_cores``).
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Optional, Sequence

from prism_inverse.domain.models import Bar

_PULSE_PATH = Path(__file__).resolve().parents[2] / "cores" / "market_pulse.py"


@lru_cache(maxsize=1)
def _module():
    spec = importlib.util.spec_from_file_location("prism_inverse_market_pulse", _PULSE_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(mod)
    return mod


def pulse_series(bars: Sequence[Bar]) -> dict[date, str]:
    mp = _module()
    machine = mp.MarketPulse()
    return {b.day: machine.feed(mp.DailyBar(b.day.isoformat(), b.close, b.volume)) for b in bars}


def today_and_yesterday(series: dict[date, str], day: date) -> tuple[Optional[str], Optional[str]]:
    days = sorted(d for d in series if d <= day)
    if not days or days[-1] != day:
        return None, None  # no bar for the decision day: unknown, not "unchanged"
    return series[days[-1]], (series[days[-2]] if len(days) > 1 else None)
