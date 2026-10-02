"""Fail-open hooks for the B3 all-entries SHADOW (KR/US). Never affects the real trade.

Entry: freeze a v3-ae plan from the actual entry (price, time, stop) and ATR14 of
the decision-time daily bars the BUY path already captured. Exit: close the
virtual campaign with the original strategy exit. OFF unless B3_AE_SHADOW_ENABLED.
"""
from __future__ import annotations

import logging
import math
import os
from datetime import date, datetime, timezone
from itertools import pairwise
from pathlib import Path

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
PRICE_BASIS = {"KR": "kis-domestic-raw-krw", "US": "yfinance-unadjusted-5m-actions-checked-v1"}


def enabled():
    return os.getenv("B3_AE_SHADOW_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def store_path():
    return Path(os.getenv("B3_AE_SHADOW_DB") or ROOT / "runtime/b3-ae-shadow.sqlite")


def _store():
    from prism_core.b3_ae_shadow import B3AeShadowStore
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return B3AeShadowStore(path)


def atr14(bars, before):
    """Mean true range of the last 14 completed sessions strictly before ``before``."""
    rows = [b for b in bars if date.fromisoformat(b["date"]) < before
            and all(isinstance(b.get(k), (int, float)) and math.isfinite(b[k]) and b[k] > 0
                    for k in ("high", "low", "close"))]
    if len(rows) < 15:
        raise ValueError("ATR14_HISTORY_SHORT")
    window = rows[-15:]
    ranges = [max(cur["high"] - cur["low"], abs(cur["high"] - prev["close"]), abs(cur["low"] - prev["close"]))
              for prev, cur in pairwise(window)]
    return sum(ranges) / 14, window[-1]["date"]


def capture_entry(agent, *, market, ticker, account_key, position_id, entry_price, stop_loss,
                  decision_ref, entered_at=None):
    """Open the SHADOW campaign for an actual first entry; returns the state or None."""
    if not enabled():
        return None
    try:
        from prism_core.b3_ae_shadow import plan_for_entry
        from prism_core.oneil_adaptive_policy import MARKETS
        captured = (getattr(agent, "_decision_input_bars", {}) or {}).get(ticker)
        if not captured or captured.get("market") != market:
            raise ValueError("DECISION_BARS_UNAVAILABLE")
        entered_at = entered_at or datetime.now(timezone.utc).isoformat()
        local_day = datetime.fromisoformat(entered_at).astimezone(MARKETS[market][0]).date()
        atr, last_trade_date = atr14(captured["bars"], local_day)
        plan = plan_for_entry(
            market=market, symbol=ticker, entry_price=entry_price, initial_stop=stop_loss,
            decision_ref=decision_ref, entered_at=entered_at, atr14=round(atr, 6),
            atr14_source_ref=f"decision-input-bars:{market}:{ticker}:{captured['captured_at']}",
            atr14_as_of=captured["captured_at"], atr14_last_trade_date=last_trade_date,
            price_basis_ref=PRICE_BASIS[market])
        state = _store().open_campaign(account_key=str(account_key), position_id=str(position_id),
                                       plan=plan, entered_at=entered_at)
        logger.info("[B3_AE][%s] %s opened initial=%s", market, ticker, plan["initial_nominal"])
        return state
    except Exception as error:  # noqa: BLE001 - SHADOW never affects the entry
        logger.warning("[B3_AE][%s] %s entry capture skipped: %s", market, ticker, error)
        return None


def capture_exit(*, market, account_key, position_ids, exit_price, exit_at=None, reason=""):
    """Close campaigns of the exited legacy rows with the same price/time."""
    if not enabled():
        return
    try:
        store = _store()
        exit_at = exit_at or datetime.now(timezone.utc).isoformat()
        for position_id in position_ids:
            store.close(market=market, account_key=str(account_key), position_id=str(position_id),
                        exit_price=exit_price, exit_at=exit_at, reason=reason)
    except Exception as error:  # noqa: BLE001 - SHADOW never affects the exit
        logger.warning("[B3_AE][%s] exit capture skipped: %s", market, error)
