"""One-shot, ORDER-FREE end-to-end preview using public BTC data and OAuth.

Example: PYTHONPATH=prism-btc:. python -m live.scenario_preview --equity 10000
Equity is an explicitly hypothetical test value, never an exchange balance.
No production DB, broker credentials, Telegram send, cron or order is accessed.
"""
from __future__ import annotations

import argparse
import json
import math
import time
import uuid

import pandas as pd

from core.llm_scenario import validate_scenario
from engine.scenario_snapshot import TIMEFRAME_MS, build_scenario_snapshot
from live.scenario_llm import propose


def response_contract(context):
    return {
        "schema_version": 1,
        "scenario_id": context.get("scenario_id") or "choose-a-new-short-unique-id",
        "revision": context["revision"] + 1,
        "input_id": context["input_id"],
        "action_id": "choose-a-new-short-unique-id",
        "action": "WAIT | OPEN | ADJUST | EXIT",
        "side": "LONG | SHORT (required for OPEN/ADJUST)",
        "confidence": "number 0..1 (NOT calibrated win probability)",
        "expires_at": "Unix seconds >now and <=now+3600",
        "hard_stop": "positive price required for OPEN/ADJUST; no widening",
        "entries": [{"id":"unique", "price":"positive number", "quantity":"positive BTC number"}],
        "take_profits": [{"id":"unique", "price":"positive number", "fraction":"number >0..1"}],
        "partial_stops": [{"id":"unique", "price":"positive number", "fraction":"number >0..1"}],
        "cancel_entry_ids": [],
        "chase": {"max_bps":"number 0..50", "max_reprices":"integer 0..3"},
        "rationale": "short Korean evidence/invalidation explanation",
        "leverage": 10,
        "rules": ["Do not include this rules field in the response.",
                  "WAIT/EXIT require empty entries/take_profits/partial_stops; OMIT hard_stop, side and chase entirely.",
                  "OPEN only when no active scenario; ADJUST/EXIT only when active.",
                  "Each exit list fractions sum <=1; leftover may be runner protected by hard stop.",
                  "Budget=initial_equity*0.02. Per BTC loss=abs(entry-stop)+entry*(estimated_cost_rate+slippage_bps/10000).",
                  "Total risk includes prior losses+fees+funding and all current/pending/new orders.",
                  "New order risk <= original budget*confidence AND remaining budget. Round BTC quantity DOWN to .001."]}


def collect_snapshot(*, fetch=None, clock=time.time):
    if fetch is None:
        from collector.bybit_public import _get_klines
        from engine.config import TF_INTERVAL_MAP
        fetch = lambda tf: _get_klines(TF_INTERVAL_MAP[tf], limit=100, retries=1)
    started = clock()
    frames = {}
    received = {}
    for tf in ("30m", "1h", "4h", "12h", "1d"):
        rows = fetch(tf)
        received[tf] = clock()
        if not rows:
            raise ValueError("empty_public_data")
        frame = pd.DataFrame(rows, columns=["open_time","open","high","low","close","volume","turnover"])
        frame.index = pd.to_datetime(pd.to_numeric(frame.pop("open_time")), unit="ms", utc=True)
        frames[tf] = frame.apply(pd.to_numeric).sort_index()
    completed = clock()
    if not 0 <= completed-started <= 60:
        raise ValueError("collection_too_slow")
    # Never promote an observed forming candle because collection crossed its
    # close. A subsequent tick must obtain the actual final candle instead.
    for tf in frames:
        duration = TIMEFRAME_MS[tf]/1000
        if int(started//duration) != int(completed//duration):
            raise ValueError("candle_boundary_crossed_during_collection")
    now_ms = int(completed*1000)
    history, forming = {}, {}
    for tf, frame in frames.items():
        opens = frame.index.asi8 // 1_000_000
        if any(opens > now_ms):
            raise ValueError("future_public_candle")
        history[tf] = frame[opens + TIMEFRAME_MS[tf] <= now_ms]
        forming[tf] = frame[(opens <= now_ms) & (opens + TIMEFRAME_MS[tf] > now_ms)]
    return build_scenario_snapshot(history, now_ms, provisional_tf_data=forming,
                                   observed_at_by_tf_ms={tf:int(at*1000) for tf,at in received.items()})


def preview(equity, *, snapshot=None, generate=None, clock=time.time):
    if type(equity) not in (float, int) or not math.isfinite(equity) or equity <= 0:
        raise ValueError("hypothetical_equity_required")
    snapshot = snapshot if snapshot is not None else collect_snapshot(clock=clock)
    ctx = dict(now=clock(), input_id=uuid.uuid4().hex,
               input_captured_at=snapshot["as_of_ms"]/1000, max_input_age_seconds=120,
               scenario_id=None,revision=0,seen_action_ids=[],initial_equity=equity,
               positions=[],pending_entries=[],previous_hard_stop=None,realized_loss=0,
               fees_paid=0,funding_paid=0,estimated_cost_rate=0.0012,slippage_bps=10,
               new_risk_blocked=False)
    payload = propose(snapshot, ctx, response_contract(ctx), generate=generate, clock=clock)
    ctx["now"] = clock()
    validated = validate_scenario(payload, ctx)
    return {"mode":"ORDER_FREE_PREVIEW", "account_source":"HYPOTHETICAL_NOT_BROKER",
            "orders_submitted":0, "model":"gpt-6-luna", "effort":"high", "tier":"fast",
            "as_of_ms":snapshot["as_of_ms"], "proposal":validated}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--equity", type=float, required=True,
                        help="hypothetical demo equity for ORDER-FREE preview, not actual account")
    args = parser.parse_args()
    try:
        print(json.dumps(preview(args.equity), ensure_ascii=False, allow_nan=False))
    except Exception as exc:
        print(json.dumps({"mode":"ORDER_FREE_PREVIEW", "status":"failed",
                          "error_type":type(exc).__name__, "orders_submitted":0}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
