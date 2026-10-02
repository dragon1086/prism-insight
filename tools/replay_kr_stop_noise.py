#!/usr/bin/env python3
"""Offline replay for docs/entry-quality-experiments/kr-stop-noise-v1.md.

Research only: never imported by the trading path, never places orders.

  extract-trades  (db-server, read-only): KR trading_history rows -> JSON
  fetch-daily     (db-server, KIS reads): unadjusted daily bars per ticker -> JSON (cached)
  fetch-minutes   (db-server, KIS reads): 1-min bars for trigger-eligible days -> JSON (cached)
  replay          (anywhere): simulate arms A/H/W/B -> JSON

Definitions, sample and verdict thresholds are fixed by the preregistration.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util as _ilu
import json
import random
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

# ------------------------------------------------------------------ shared utilities from b3 tool

_B3_PATH = Path(__file__).resolve().parent / "replay_kr_micro_split_b3.py"
_b3_spec = _ilu.spec_from_file_location("_replay_kr_micro_split_b3", _B3_PATH)
_b3_mod = _ilu.module_from_spec(_b3_spec)
_b3_spec.loader.exec_module(_b3_mod)
drop_pyramid_adds = _b3_mod.drop_pyramid_adds
atr14 = _b3_mod.atr14
mdd = _b3_mod.mdd

# ------------------------------------------------------------------ constants

RULE_VERSION = "kr-stop-noise-v1"
SAMPLE_START, SAMPLE_END = "2025-10-01", "2026-09-30 23:59:59"
HOLDOUT_START = "2026-07-01"
BOOT, SEED = 2000, 20261003
MIN_HOLDOUT = 30
COST = 0.0025

_CHECK_MIN_OFFSETS = (0, 6, 10, 16, 20, 26, 30, 36, 40, 46, 50, 56)
# A/W/B check times: :00/:06/:10/:16/:20/:26/:30/:36/:40/:46/:50/:56 from 09:00 to 15:20 inclusive
CHECK_TIMES_A = tuple(
    f"{h:02d}{m:02d}00"
    for h in range(9, 16)
    for m in _CHECK_MIN_OFFSETS
    if h * 60 + m <= 15 * 60 + 20
)
# H check times: 10:00 11:00 12:00 13:00 14:00 15:00
CHECK_TIMES_H = tuple(f"{h:02d}0000" for h in range(10, 16))
ARMS = ("A", "H", "W", "B")

_MINUTE_URL = "/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice"
_MINUTE_TR = "FHKST03010230"
_SQL = (
    "SELECT id, ticker, buy_date, buy_price, sell_date, sell_price, exit_kind, trigger_type, scenario "
    "FROM trading_history "
    "WHERE buy_date >= ? AND buy_date <= ? AND sell_date IS NOT NULL ORDER BY buy_date, id"
)


# ------------------------------------------------------------------ extract

def extract_trades(db, out):
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = []
    for r in conn.execute(_SQL, (SAMPLE_START, SAMPLE_END)):
        id_, ticker, buy_date, buy_price, sell_date, sell_price, exit_kind, trigger_type, scenario_text = r
        try:
            scenario = json.loads(scenario_text or "{}")
        except (TypeError, ValueError):
            scenario = {}
        raw_stop = scenario.get("stop_loss")
        try:
            stop_loss_val = float(raw_stop) if raw_stop is not None else None
        except (TypeError, ValueError):
            stop_loss_val = None
        valid_stop = (
            stop_loss_val is not None
            and stop_loss_val > 0
            and float(buy_price) > 0
            and stop_loss_val < float(buy_price)
        )
        rows.append({
            "id": id_,
            "ticker": ticker,
            "buy_date": buy_date,
            "buy_price": float(buy_price),
            "sell_date": sell_date,
            "sell_price": float(sell_price),
            "exit_kind": exit_kind,
            "trigger_type": trigger_type,
            "stop_loss": stop_loss_val,
            "stop_loss_valid": valid_stop,
        })
    conn.close()
    excluded = sum(1 for r in rows if not r["stop_loss_valid"])
    Path(out).write_text(
        json.dumps({"rule_version": RULE_VERSION, "rows": rows,
                    "excluded_no_valid_stop": excluded}, ensure_ascii=False)
    )
    print(f"rows={len(rows)} excluded_no_valid_stop={excluded} -> {out}")


# ------------------------------------------------------------------ fetch daily

def fetch_daily(trades, cache_dir, out, pause=0.35):
    from cores.market_data.kis_source import KisSource

    source = KisSource()
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    rows = json.loads(Path(trades).read_text())["rows"]
    today = date.today()
    spans = {}
    for r in rows:
        buy = r["buy_date"][:10]
        sell = r["sell_date"][:10]
        a, b = spans.get(r["ticker"], (buy, sell))
        spans[r["ticker"]] = (min(a, buy), max(b, sell))
    bars = {}
    for ticker, (lo, hi) in sorted(spans.items()):
        # 40 calendar days before first buy for ATR14 history
        start = (datetime.strptime(lo, "%Y-%m-%d") - timedelta(days=40)).strftime("%Y%m%d")
        # 30 sessions (~60 calendar days) after last sell for timeout exit window; cap at today
        end_dt = min(today, datetime.strptime(hi, "%Y-%m-%d").date() + timedelta(days=60))
        end = end_dt.strftime("%Y%m%d")
        lo_c, hi_c = lo.replace("-", ""), hi.replace("-", "")
        path = cache / f"snoise_daily_{ticker}_{lo_c}_{hi_c}.json"
        if not path.exists():
            try:
                frame = source.price_history(ticker, start, end, adjusted=False)
                data = [{"date": ix.strftime("%Y-%m-%d"), "high": float(x["High"]),
                         "low": float(x["Low"]), "close": float(x["Close"])}
                        for ix, x in frame.iterrows()]
            except Exception as error:  # noqa: BLE001
                data = {"error": type(error).__name__}
            path.write_text(json.dumps(data))
            time.sleep(pause)
        bars[ticker] = json.loads(path.read_text())
    Path(out).write_text(json.dumps({"rule_version": RULE_VERSION, "bars": bars}))
    print(f"tickers={len(bars)} -> {out}")


# ------------------------------------------------------------------ fetch minutes

def _minutes_full(source, ticker, day, pause):
    """Return {HHMMSS: {close, low, high, open, vol}} for one session, paging back from 15:30."""
    out, hour = {}, "153000"
    for _ in range(14):
        body = source._fetch(_MINUTE_URL, _MINUTE_TR, {
            "FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker,
            "FID_INPUT_HOUR_1": hour, "FID_INPUT_DATE_1": day,
            "FID_PW_DATA_INCU_YN": "N", "FID_FAKE_TICK_INCU_YN": "",
        })
        rows = [r for r in (getattr(body, "output2", None) or []) if r.get("stck_bsop_date") == day]
        fresh = [r for r in rows if r["stck_cntg_hour"] not in out]
        time.sleep(pause)
        if not fresh:
            break
        for r in fresh:
            out[r["stck_cntg_hour"]] = {
                "close": float(r.get("stck_prpr") or 0),
                "low": float(r.get("stck_lwpr") or 0),
                "high": float(r.get("stck_hgpr") or 0),
                "open": float(r.get("stck_oprc") or 0),
                "vol": int(float(r.get("cntg_vol") or 0)),
            }
        hour = min(out)
        if hour <= "090000":
            break
    return out


def _ahb_trigger(trade):
    """Highest threshold that can trigger arm A, H, or B."""
    return max(trade["stop_loss"] * 0.995, trade["buy_price"] * 0.93)


def _w_trigger(trade, daily_bars):
    """W arm trigger = entry*(1-stop_proxy)*0.995; None if ATR14 unavailable."""
    from prism_core.oneil_adaptive_policy import initial_sizing
    buy_day = trade["buy_date"][:10]
    prior = [b for b in daily_bars if b["date"] < buy_day]
    a = atr14(prior)
    if a is None:
        return None
    stop_proxy, _ = initial_sizing(trade["buy_price"], a)
    return trade["buy_price"] * (1 - float(stop_proxy)) * 0.995


def _session_list(daily_bars):
    return sorted(b["date"] for b in daily_bars)


def fetch_minutes(trades, daily_file, cache_dir, out, pause=0.35):
    from cores.market_data.kis_source import KisSource

    source = KisSource()
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    rows = json.loads(Path(trades).read_text())["rows"]
    daily = json.loads(Path(daily_file).read_text())["bars"]
    needed: set[tuple[str, str]] = set()

    for t in rows:
        if not t.get("stop_loss_valid"):
            continue
        ticker = t["ticker"]
        bars = daily.get(ticker)
        if not isinstance(bars, list):
            continue
        sessions = _session_list(bars)
        bar_by = {b["date"]: b for b in bars}
        buy_day = t["buy_date"][:10]
        sell_day = t["sell_date"][:10]
        try:
            ei = sessions.index(buy_day)
        except ValueError:
            continue
        session_20 = sessions[ei + 20] if ei + 20 < len(sessions) else sessions[-1]
        end_day = max(sell_day, session_20)

        ahb = _ahb_trigger(t)
        wt = _w_trigger(t, bars)
        max_trig = max(ahb, wt) if wt is not None else ahb

        for day in sessions:
            if day < buy_day or day > end_day:
                continue
            b = bar_by.get(day)
            if b and b.get("low", float("inf")) <= max_trig:
                needed.add((ticker, day.replace("-", "")))

    minutes: dict[str, object] = {}
    fetched = 0
    for ticker, day_compact in sorted(needed):
        key = f"{ticker}_{day_compact}"
        path = cache / f"snoise_min_{key}.json"
        if not path.exists():
            try:
                data = _minutes_full(source, ticker, day_compact, pause)
            except Exception as error:  # noqa: BLE001
                data = {"error": type(error).__name__}
            path.write_text(json.dumps(data))
            fetched += 1
        minutes[key] = json.loads(path.read_text())

    Path(out).write_text(json.dumps({"rule_version": RULE_VERSION, "minutes": minutes}))
    print(f"needed={len(needed)} fetched={fetched} -> {out}")


# ------------------------------------------------------------------ simulation

def _simulate_arm(trade, sessions, bar_by_date, minutes_data, arm, weight, trigger):
    """Simulate one arm for one trade.

    Returns dict with exit_price/exit_date/stopped keys, or {"missing": reason}.
    """
    buy_day = trade["buy_date"][:10]
    sell_day = trade["sell_date"][:10]
    # Extract buy time as HHMMSS for entry-day check filtering
    ts = trade["buy_date"]
    if len(ts) >= 19:
        buy_time = ts[11:13] + ts[14:16] + ts[17:19]
    else:
        buy_time = "000000"

    check_times = CHECK_TIMES_H if arm == "H" else CHECK_TIMES_A
    is_actual_stop = (trade.get("exit_kind") == "stop")

    try:
        entry_idx = sessions.index(buy_day)
    except ValueError:
        return {"missing": "no_entry_session"}

    session_20_idx = entry_idx + 20
    session_20 = sessions[session_20_idx] if session_20_idx < len(sessions) else sessions[-1]
    end_day = session_20 if is_actual_stop else sell_day

    for day in sessions:
        if day < buy_day or day > end_day:
            continue

        daily_bar = bar_by_date.get(day)
        day_low = daily_bar["low"] if daily_bar else float("inf")

        if day_low <= trigger:
            min_key = f"{trade['ticker']}_{day.replace('-', '')}"
            minute_data = minutes_data.get(min_key)
            if not isinstance(minute_data, dict) or not minute_data or "error" in minute_data:
                return {"missing": f"no_minutes:{day}"}

            for ct in check_times:
                # On entry day: skip times at or before the buy execution time
                if day == buy_day and ct <= buy_time:
                    continue
                row = minute_data.get(ct)
                if row is None:
                    continue
                close = row.get("close") if isinstance(row, dict) else None
                if close and close <= trigger:
                    return {"arm": arm, "weight": weight,
                            "exit_price": close, "exit_date": day, "stopped": True}

        # Non-stop actual exit reached without prior arm stop
        if not is_actual_stop and day == sell_day:
            return {"arm": arm, "weight": weight,
                    "exit_price": trade["sell_price"], "exit_date": sell_day, "stopped": False}

    # Loop exhausted
    if not is_actual_stop:
        return {"arm": arm, "weight": weight,
                "exit_price": trade["sell_price"], "exit_date": sell_day, "stopped": False}

    # Actual was stop but arm didn't trigger: use session_20 close
    s20_bar = bar_by_date.get(session_20)
    if s20_bar:
        return {"arm": arm, "weight": weight,
                "exit_price": s20_bar["close"], "exit_date": session_20, "stopped": False}
    return {"missing": "no_session_20_bar"}


# ------------------------------------------------------------------ statistics

def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def _compute_whipsaw(period_rows, arm, daily):
    """Among stopped arm exits, fraction where close within next 5 sessions >= entry."""
    stopped = [r for r in period_rows if r.get(f"stopped_{arm}") is True]
    if not stopped:
        return None
    count = 0
    for r in stopped:
        bars = daily.get(r["ticker"])
        if not isinstance(bars, list):
            continue
        sessions = _session_list(bars)
        bar_by = {b["date"]: b for b in bars}
        exit_day = r.get(f"exit_date_{arm}")
        if not exit_day:
            continue
        try:
            ei = sessions.index(exit_day)
        except ValueError:
            continue
        entry = r["buy_price"]
        for d in sessions[ei + 1: ei + 6]:
            b = bar_by.get(d)
            if b and b["close"] >= entry:
                count += 1
                break
    return count / len(stopped)


def _compute_a_reproduction(period_rows):
    """Among actual-stop trades with valid A simulation, fraction where A also stopped same day."""
    actual_stops = [r for r in period_rows
                    if r.get("exit_kind") == "stop" and r.get("slot_A") is not None]
    if not actual_stops:
        return None
    same_day = sum(
        1 for r in actual_stops
        if r.get("stopped_A") is True and r.get("exit_date_A") == r["sell_date"][:10]
    )
    return same_day / len(actual_stops)


def _boot_ci(paired_diffs_by_ticker, rng):
    """90% CI of mean paired diff via ticker-cluster bootstrap."""
    tickers = sorted(paired_diffs_by_ticker)
    if not tickers:
        return [None, None]
    boot = []
    for _ in range(BOOT):
        sample = [d for t in (rng.choice(tickers) for _ in tickers)
                  for d in paired_diffs_by_ticker[t]]
        if sample:
            boot.append(_mean(sample))
    boot.sort()
    if not boot:
        return [None, None]
    return [boot[int(0.05 * len(boot))], boot[int(0.95 * len(boot)) - 1]]


def _summarize(period_rows, daily, rng):
    if not period_rows:
        return {"n": 0}
    s: dict = {"n": len(period_rows)}

    for arm in ARMS:
        valid = [r for r in period_rows if r.get(f"slot_{arm}") is not None]
        slots = [r[f"slot_{arm}"] for r in valid]
        s[f"n_{arm}"] = len(valid)
        s[f"mean_slot_{arm}"] = _mean(slots)
        s[f"mean_slot_{arm}_cost"] = _mean([r[f"slot_{arm}_cost"] for r in valid])
        s[f"worst_{arm}"] = min(slots) if slots else None
        if valid:
            mdd_input = [{"sell_date": r.get(f"exit_date_{arm}") or r["sell_date"],
                          "v": r[f"slot_{arm}"]} for r in valid]
            s[f"mdd_{arm}"] = mdd(mdd_input, "v")
        else:
            s[f"mdd_{arm}"] = None
        loss = [sl for sl in slots if sl < 0]
        s[f"loss_avg_{arm}"] = _mean(loss)
        s[f"n_stopped_{arm}"] = sum(1 for r in valid if r.get(f"stopped_{arm}") is True)

    for arm in ("H", "W", "B"):
        paired = [(r["ticker"], r[f"slot_{arm}"] - r["slot_A"])
                  for r in period_rows
                  if r.get("slot_A") is not None and r.get(f"slot_{arm}") is not None]
        s[f"diff_A_{arm}_n"] = len(paired)
        s[f"diff_A_{arm}_mean"] = _mean([d for _, d in paired])
        by_ticker: dict = {}
        for ticker, d in paired:
            by_ticker.setdefault(ticker, []).append(d)
        s[f"diff_A_{arm}_ci90"] = _boot_ci(by_ticker, rng)

    s["a_reproduction"] = _compute_a_reproduction(period_rows)
    for arm in ARMS:
        s[f"whipsaw_{arm}"] = _compute_whipsaw(period_rows, arm, daily)

    return s


def _verdicts(hold):
    a_repro = hold.get("a_reproduction")
    n_hold = hold.get("n", 0)
    if a_repro is not None and a_repro < 0.80:
        return {"H": "INCONCLUSIVE", "W": "INCONCLUSIVE", "B": "REFERENCE",
                "note": f"A reproduction {a_repro:.1%} < 80%"}
    if n_hold < MIN_HOLDOUT:
        return {"H": "CONTINUE_CAPTURE", "W": "CONTINUE_CAPTURE", "B": "REFERENCE"}

    # H candidate
    h_diff = hold.get("diff_A_H_mean")
    h_ci = hold.get("diff_A_H_ci90") or [None, None]
    mdd_a = hold.get("mdd_A")
    mdd_h = hold.get("mdd_H")
    la = hold.get("loss_avg_A")
    lh = hold.get("loss_avg_H")
    h_ok = (
        h_diff is not None and h_diff >= 0.002
        and h_ci[0] is not None and h_ci[0] > 0
        and mdd_a is not None and mdd_h is not None and mdd_h >= mdd_a
        and (la is None or lh is None or lh >= la * 1.10)
    )

    # W candidate
    w_diff = hold.get("diff_A_W_mean")
    mdd_w = hold.get("mdd_W")
    lw = hold.get("loss_avg_W")
    wh_a = hold.get("whipsaw_A")
    wh_w = hold.get("whipsaw_W")
    w_ok = (
        w_diff is not None and w_diff >= -0.002
        and la is not None and lw is not None and la < 0 and lw >= la * 0.75
        and mdd_a is not None and mdd_w is not None and mdd_w > mdd_a
        and wh_a is not None and wh_w is not None and wh_w < wh_a
    )

    return {
        "H": "H_CANDIDATE" if h_ok else "RETIRE",
        "W": "LIMITED_LIVE_REVIEW" if w_ok else "RETIRE",
        "B": "REFERENCE",
    }


# ------------------------------------------------------------------ replay

def replay(trades_file, daily_file, minutes_file, out, sizing=None):
    if sizing is None:
        from prism_core.oneil_adaptive_policy import initial_sizing as sizing

    raw = json.loads(Path(trades_file).read_text())
    all_trades = raw["rows"]
    daily = json.loads(Path(daily_file).read_text())["bars"]
    minutes_store = json.loads(Path(minutes_file).read_text())["minutes"]

    valid_trades = [t for t in all_trades if t.get("stop_loss_valid")]
    kept = drop_pyramid_adds(valid_trades)

    missing_counts = {
        "no_valid_stop": len(all_trades) - len(valid_trades),
        "pyramid_add": len(valid_trades) - len(kept),
        "no_bars": 0,
        "no_entry_session": 0,
    }
    arm_missing = {arm: 0 for arm in ARMS}
    results = []

    for trade in kept:
        ticker = trade["ticker"]
        bars = daily.get(ticker)
        if not isinstance(bars, list):
            missing_counts["no_bars"] += 1
            continue

        bar_by_date = {b["date"]: b for b in bars}
        sessions = sorted(bar_by_date)
        buy_day = trade["buy_date"][:10]
        if buy_day not in bar_by_date:
            missing_counts["no_entry_session"] += 1
            continue

        # Pre-compute ATR14 and W/B sizing
        prior = [b for b in bars if b["date"] < buy_day]
        a = atr14(prior)
        if a is not None:
            stop_proxy, initial = sizing(trade["buy_price"], a)
            wb_weight = float(initial)
            w_trig = trade["buy_price"] * (1 - float(stop_proxy)) * 0.995
        else:
            wb_weight = None
            w_trig = None

        ahb_trig = max(trade["stop_loss"] * 0.995, trade["buy_price"] * 0.93)

        row: dict = {
            "id": trade["id"], "ticker": ticker,
            "buy_date": trade["buy_date"], "buy_price": trade["buy_price"],
            "sell_date": trade["sell_date"], "sell_price": trade["sell_price"],
            "exit_kind": trade.get("exit_kind"),
            "trigger_type": trade.get("trigger_type"),
            "stop_loss": trade["stop_loss"],
        }

        for arm in ARMS:
            if arm in ("W", "B") and wb_weight is None:
                arm_missing[arm] += 1
                row[f"slot_{arm}"] = None
                row[f"slot_{arm}_cost"] = None
                row[f"exit_price_{arm}"] = None
                row[f"exit_date_{arm}"] = None
                row[f"stopped_{arm}"] = None
                continue

            weight = 1.0 if arm in ("A", "H") else wb_weight
            trigger = (w_trig if arm == "W" else ahb_trig)

            res = _simulate_arm(trade, sessions, bar_by_date, minutes_store, arm, weight, trigger)
            if "missing" in res:
                arm_missing[arm] += 1
                row[f"slot_{arm}"] = None
                row[f"slot_{arm}_cost"] = None
                row[f"exit_price_{arm}"] = None
                row[f"exit_date_{arm}"] = None
                row[f"stopped_{arm}"] = None
            else:
                slot = res["weight"] * (res["exit_price"] / trade["buy_price"] - 1)
                row[f"slot_{arm}"] = slot
                row[f"slot_{arm}_cost"] = slot - res["weight"] * COST
                row[f"exit_price_{arm}"] = res["exit_price"]
                row[f"exit_date_{arm}"] = res["exit_date"]
                row[f"stopped_{arm}"] = res["stopped"]

        results.append(row)

    disc = [r for r in results if r["buy_date"][:10] < HOLDOUT_START]
    hold = [r for r in results if r["buy_date"][:10] >= HOLDOUT_START]

    rng = random.Random(SEED)
    disc_s = _summarize(disc, daily, rng)
    hold_s = _summarize(hold, daily, rng)
    all_s = _summarize(results, daily, rng)

    summary = {
        "rule_version": RULE_VERSION,
        "inputs_sha256": hashlib.sha256(
            Path(trades_file).read_bytes()
            + Path(daily_file).read_bytes()
            + Path(minutes_file).read_bytes()
        ).hexdigest(),
        "rows_total": len(all_trades), "rows_used": len(results),
        "missing": missing_counts, "arm_missing": arm_missing,
        "discovery": disc_s, "holdout": hold_s, "all": all_s,
        "verdicts": _verdicts(hold_s),
    }
    Path(out).write_text(
        json.dumps({"summary": summary, "rows": results},
                   ensure_ascii=False, indent=1, default=str)
    )
    print(json.dumps(summary, ensure_ascii=False, indent=1, default=str))


# ------------------------------------------------------------------ CLI

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("extract-trades")
    e.add_argument("--db", default="stock_tracking_db.sqlite")
    e.add_argument("--out", required=True)

    fd = sub.add_parser("fetch-daily")
    fd.add_argument("--trades", required=True)
    fd.add_argument("--cache", default="runtime/research/kr_stop_noise")
    fd.add_argument("--out", required=True)
    fd.add_argument("--pause", type=float, default=0.35)

    fm = sub.add_parser("fetch-minutes")
    fm.add_argument("--trades", required=True)
    fm.add_argument("--daily", required=True)
    fm.add_argument("--cache", default="runtime/research/kr_stop_noise")
    fm.add_argument("--out", required=True)
    fm.add_argument("--pause", type=float, default=0.35)

    r = sub.add_parser("replay")
    r.add_argument("--trades", required=True)
    r.add_argument("--daily", required=True)
    r.add_argument("--minutes", required=True)
    r.add_argument("--out", required=True)

    a = p.parse_args(argv)
    if a.cmd == "extract-trades":
        extract_trades(a.db, a.out)
    elif a.cmd == "fetch-daily":
        fetch_daily(a.trades, a.cache, a.out, a.pause)
    elif a.cmd == "fetch-minutes":
        fetch_minutes(a.trades, a.daily, a.cache, a.out, a.pause)
    else:
        replay(a.trades, a.daily, a.minutes, a.out)


if __name__ == "__main__":
    sys.exit(main())
