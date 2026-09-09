"""Fixed three-arm minute-close stop approximation; no live rule changes."""
from datetime import timedelta
from zoneinfo import ZoneInfo

from prism_core.trend_quality_research import timestamp

MINUTES = {4, 8, 14, 18, 24, 28, 34, 38, 44, 48, 54, 58}
ARMS = ("scheduled_immediate", "completed_5m", "continuous_two_scheduled")
COST_BPS = (0, 10, 30)


def scheduled(at):
    local = at.astimezone(ZoneInfo("America/New_York"))
    return local.weekday() < 5 and 9 <= local.hour <= 16 and local.minute in MINUTES and local.second == 0


def complete_five_minute(at, one_minute, provider_five):
    end = at.replace(minute=at.minute - at.minute % 5, second=0, microsecond=0)
    if end in provider_five:
        return provider_five[end]["close"], "provider_5m"
    expected = [end - timedelta(minutes=n) for n in range(4, -1, -1)]
    if all(when in one_minute for when in expected):
        return one_minute[end]["close"], "complete_1m_bin"
    return None, "MISSING_COMPLETE_5M_BIN"


def run_noise_case(one_bars, five_bars, *, entry_at, cutoff, reference_entry, normal_threshold,
                   catastrophic_threshold, actual_exit_at, actual_exit_price):
    start, finish = timestamp(entry_at), timestamp(cutoff)
    if not 0 < catastrophic_threshold < normal_threshold < reference_entry:
        raise ValueError("invalid_reference_thresholds")
    one = {timestamp(b["bar_close_at"]): b for b in one_bars
           if start <= timestamp(b["provider_timestamp"]) and timestamp(b["bar_close_at"]) <= finish}
    five = {timestamp(b["bar_close_at"]): b for b in five_bars
            if start <= timestamp(b["provider_timestamp"]) and timestamp(b["bar_close_at"]) <= finish}
    if not one:
        return {"status": "DATA_UNAVAILABLE"}
    timeline = []
    now = start.replace(second=0, microsecond=0) + timedelta(minutes=1)
    while now <= finish:
        if scheduled(now):
            timeline.append(now)
        now += timedelta(minutes=1)
    result = {"status": "DISCOVERY_APPROXIMATION_ONLY", "case_count": 1,
              "reference_entry": reference_entry, "normal_threshold": normal_threshold,
              "catastrophic_threshold": catastrophic_threshold, "arms": {},
              "actual_reference_exit": {"at": actual_exit_at, "price": actual_exit_price,
                                        "not_simulated_fill": True},
              "limitations": ["Minute closes approximate cron quote observations, not tick truth",
                "Strategy reference is not a broker fill; provenance supplied separately",
                "Missing bars cannot prove sustained breach", "n=1 discovery has no population tail estimate",
                "No independent holdout or automatic promotion", "Mark-to-cutoff when no executable exit"]}
    mark_time = max(one)
    mark = one[mark_time]["close"]
    for arm in ARMS:
        previous_breach = None
        decision = reason = execution = None
        price = None
        missed = missing_five = derived_five = 0
        for at in timeline:
            if at not in one:
                missed += 1
                previous_breach = None
                continue
            quote = one[at]["close"]
            breach = quote <= normal_threshold
            if quote <= catastrophic_threshold:
                reason = "catastrophic"
            elif arm == "scheduled_immediate" and breach:
                reason = "ordinary"
            elif arm == "completed_5m" and breach:
                confirmed, source = complete_five_minute(at, one, five)
                missing_five += confirmed is None
                derived_five += source == "complete_1m_bin"
                if confirmed is not None and confirmed <= normal_threshold:
                    reason = "ordinary_5m_confirmed"
            elif arm == "continuous_two_scheduled" and breach and previous_breach:
                cursor = previous_breach + timedelta(minutes=1)
                complete = True
                while cursor <= at:
                    if cursor not in one or one[cursor]["close"] > normal_threshold:
                        complete = False
                        break
                    cursor += timedelta(minutes=1)
                if complete:
                    reason = "ordinary_continuous_confirmed"
            previous_breach = at if breach else None
            if reason:
                decision = at
                next_bars = sorted((timestamp(b["provider_timestamp"]), b) for b in one.values()
                                   if timestamp(b["provider_timestamp"]) > at)
                if next_bars:
                    execution, bar = next_bars[0]
                    price = bar["open"]
                break
        used_price = price if price is not None else mark
        stop_at = execution if execution else mark_time
        held = [b for b in one.values() if timestamp(b["provider_timestamp"]) < stop_at]
        net = {str(cost): (used_price * (1 - cost / 10000) / (reference_entry * (1 + cost / 10000)) - 1) * 100 for cost in COST_BPS}
        result["arms"][arm] = {"decision_at": decision.isoformat() if decision else None,
            "execution_at": execution.isoformat() if execution else None, "exit_price": price,
            "reason": reason, "valuation": "EXECUTED_NEXT_AVAILABLE_OPEN" if price is not None else (
                "MISSING_NEXT_EXECUTABLE_BAR_MARK_ONLY" if decision else "COMMON_CUTOFF_MARK"),
            "mark_at": mark_time.isoformat(), "mark_price": mark,
            "execution_delay_seconds": (execution - decision).total_seconds() if execution else None,
            "return_pct_by_cost_bps_each_side": net, "missed_scheduled_observations": missed,
            "missing_five_minute_confirmations": missing_five, "derived_complete_five_minute_bins": derived_five,
            "mfe_pct": (max(reference_entry, *(b["high"] for b in held)) / reference_entry - 1) * 100 if held else None,
            "mae_pct": (min(reference_entry, *(b["low"] for b in held)) / reference_entry - 1) * 100 if held else None,
            "observed_window_max_excursion_pct": (max(b["high"] for b in held) / reference_entry - 1) * 100 if held else None,
            "observed_window_min_excursion_pct": (min(b["low"] for b in held) / reference_entry - 1) * 100 if held else None}
    after = [b for at, b in sorted(one.items()) if at > timestamp(actual_exit_at)]
    result["post_actual_exit_path"] = {"available_minute_bars": len(after),
        "first_close_above_normal_at": next((b["bar_close_at"] for b in after if b["close"] > normal_threshold), None),
        "maximum_close_recovery_from_actual_exit_pct": (max(b["close"] for b in after) / actual_exit_price - 1) * 100 if after and actual_exit_price else None,
        "worst_low_from_actual_exit_pct": (min(b["low"] for b in after) / actual_exit_price - 1) * 100 if after and actual_exit_price else None,
        "population_adverse_tail_estimate": None}
    return result
