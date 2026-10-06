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
from engine.scenario_snapshot import TIMEFRAME_MS, build_scenario_snapshot, candle_start
from live.scenario_llm import propose


def response_contract(context):
    from live.scenario_contract import identity_fields, response_schema
    contract = {
        **identity_fields(context),
        "action": " | ".join(response_schema(context)["properties"]["action"]["enum"]),
        "side": "LONG | SHORT for OPEN/ADJUST; null for WAIT/EXIT",
        "confidence": "number 0..1 (NOT calibrated win probability)",
        "expires_at": context["now"] + 300,
        "hard_stop": "positive price for OPEN/ADJUST; no widening; null for WAIT/EXIT",
        "entries": [{"id":"unique", "price":"positive number", "quantity":"positive BTC number"}],
        "take_profits": [{"id":"unique", "price":"positive number", "fraction":"number >0..1"}],
        "partial_stops": [{"id":"unique", "price":"positive number", "fraction":"number >0..1"}],
        "cancel_entry_ids": [],
        "chase": {"max_bps":"number 0..50", "max_reprices":"integer 0..3"},
        "rationale": "short Korean evidence/invalidation explanation",
        "leverage": 10,
        "rules": ["Do not include this rules field in the response.",
                  "expires_at above is the recommended Unix-seconds response validity deadline (request now+300), including WAIT/EXIT. It leaves room for the model's 75-second timeout. Every action must still satisfy fresh validation now < expires_at <= now+3600. Do not copy an old current_plan expiry or use now/now+1. Never exceed the 3600-second limit.",
                  "A WAIT/EXIT response deadline does not extend existing entry deadlines or change chase. OPEN/ADJUST also use expires_at as their new plan's entry/chase deadline; choose a justified duration within the validity limit, allowing response latency. Expired entries do not require closing a held position. The host does not repair invalid timestamps.",
                  "All schema fields are required. WAIT/EXIT require empty entries/take_profits/partial_stops and explicit null hard_stop, side and chase. OPEN/ADJUST require non-null side, hard_stop and chase.",
                  "OPEN only when no active scenario; ADJUST/EXIT only when active.",
                  "cancel_entry_ids defaults to []. Only a unique subset of CURRENT contract_context.pending_entries[].id may be selected; never exchange/historical IDs. Cancel-only uses WAIT.",
                  "ADJUST entries are incremental NEW orders, not copies of retained pending orders. Cancel requests retain reserved risk until confirmed. All new order IDs must be unique across all three lists and disjoint from current pending IDs, even if cancellation was requested.",
                  "For OPEN the exit reference is quantity-weighted entry price; for active ADJUST it is current mark_price, NOT average entry. LONG TP must be strictly above reference, SHORT TP below. Partial SL must lie strictly between reference and hard_stop.",
                  "Each exit list separately has fractions sum <=1; do not sum TP and partial SL lists together. Leftover may be a hard-stop-protected runner. target_qty=floor_down(max(0,min(remaining_capacity,revision_allocation*fraction-same_intent_target_fills)),quantity_step), not (allocation-fills)*fraction. New ADJUST uses a new remaining-position basis. Skip below-minimum quantities; never round up.",
                  "Budget=host initial_equity*0.02, fixed from accepted OPEN including unfilled entries until reconciled flat. Profits do not enlarge it.",
                  "For each lot D=price-hard_stop for LONG, hard_stop-price for SHORT. New/pending entries require D>0; filled positions allow profitable stops using max(0,D). Lot risk=quantity*(max(0,D)+price*estimated_cost_rate+hard_stop*slippage_bps/10000).",
                  "Total risk=realized_loss+fees_paid+funding_paid+filled lot risk+ALL current pending lot risk+new lot risk. For new entries total risk <= budget; requested cancellation does not remove pending risk. Unknown costs are not zero.",
                  "New order risk <= ORIGINAL budget*confidence AND remaining budget, not remaining budget*confidence. Round quantity DOWN to the supplied quantity_step and obey minimum_quantity/minimum_notional and price_tick. Code is the final risk/precision authority."]}
    if context.get("review_contract_version") == 1:
        contract["review"] = {"conditions": [{"source": "MARK_PRICE", "operator": "ge | le", "price": "positive numeric threshold"}],
                              "acknowledgements": [{"id": "exact review_memory host ID", "disposition": "hold | replace", "reason": "1..240 characters explaining decision"}]}
        contract["rules"].append("review is advisory, nullable, max 3 conditions and 3 acknowledgements. Empty/missing review does not clear prior conditions. A reached condition stays reached; acknowledge its exact host ID and explain hold or replacement, not silent goalpost movement. Only MARK_PRICE ge/le thresholds are executable observations, not candle closes or exchange orders.")
    if context.get("conditional_entry_version") == 1:
        contract["entries"][0]["trigger_price"] = "null for ordinary LIMIT; positive MarkPrice crossing for conditional stop-LIMIT"
        contract["reservation_expires_at"] = (int(context.get("input_captured_at", context["now"])) // 300 + 1) * 300
        contract["rules"].append("reservation_expires_at is host input only; do not return this field. Conditional LONG trigger must exceed current mark, limit price>=trigger; SHORT trigger below mark, limit price<=trigger. Use expires_at<=reservation_expires_at, chase zero. All conditional risk is reserved before trigger. Deadline never extends; cancellation is asynchronous, not a guaranteed exact expiry. Do not pre-buffer conditional entry limits.")
    fraction = context.get("scenario_risk_fraction", .02)
    contract["rules"] = [rule.replace("initial_equity*0.02", f"initial_equity*{fraction:g}") for rule in contract["rules"]]
    if context.get("recovery_contract_version") == 1:
        contract["recovery"] = {"decision": "OBSERVE | PROBE", "reason": "1..600 characters",
            "changed_evidence": ["exact numeric paths from contract_context.recovery.changed_evidence"],
            "counterevidence": "1..600 characters", "invalidation": "1..600 characters"}
        info = context.get("recovery") or {}
        if info.get("phase") == "OBSERVING" and info.get("entry_deadline"):
            contract["expires_at"] = min(contract["expires_at"], info["entry_deadline"])
        if info.get("phase") == "OBSERVING":
            contract["rules"].append("Only while OBSERVING: observation is not order permission. WAIT requires recovery.decision=OBSERVE; OPEN needs PROBE with true changed_evidence paths, reasons, counterevidence and invalidation. First observation is WAIT only. Host may authorize exactly one original OPEN batch, 0.005 of actual initial_equity including all costs/pending, zero chase, expires_at no later than recovery.entry_deadline. No later adds/retries or automatic normal resume.")
        else:
            contract["rules"].append("For a CONSUMED holding probe, recovery may be null. WAIT/ADJUST/EXIT response expires_at must remain fresh using current now, even after the original entry deadline. This never renews the original permit or authorizes additional entries. Preserve 0.005 scenario budget; protection/EXIT remain available.")
    return contract


def collect_snapshot(*, fetch=None, clock=time.time):
    if fetch is None:
        from collector.bybit_public import _get_klines
        from engine.config import TF_INTERVAL_MAP
        intervals = {**TF_INTERVAL_MAP, "15m": "15"}
        def fetch(tf):
            return _get_klines(intervals[tf], limit=1000 if tf=="15m" else 100,
                               retries=3, retry_rate_limit_only=True)
    started = clock()
    frames = {}
    received = {}
    for tf in TIMEFRAME_MS:
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
        duration = TIMEFRAME_MS[tf]
        if candle_start(int(started*1000), duration) != candle_start(int(completed*1000), duration):
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
    from live.scenario_runtime import snapshot_input_time
    ctx = dict(now=clock(), input_id=uuid.uuid4().hex,
               input_captured_at=snapshot_input_time(snapshot), max_input_age_seconds=120,
               scenario_id=None,revision=0,seen_action_ids=[],initial_equity=equity,
               positions=[],pending_entries=[],previous_hard_stop=None,realized_loss=0,
               fees_paid=0,funding_paid=0,estimated_cost_rate=0.0012,slippage_bps=10,
               new_risk_blocked=False,conditional_entry_version=1,
               mark_price=snapshot.get("timeframes",{}).get("15m",{}).get("forming",{}).get("ohlcv",{}).get("close"))
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
