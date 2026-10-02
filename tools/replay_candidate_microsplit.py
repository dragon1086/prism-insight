#!/usr/bin/env python3
"""Exploratory offline replay: can micro-split style entry make mediocre-score candidates profitable?

Research only — never imported by the trading path, never places orders.

  extract  --db stock_tracking_db.sqlite --out cands.json
      Read analysis_performance_tracker (KR+US), join watchlist_history for scenario,
      prefer traded rows from trading_history, dedupe one per ticker-day, write JSON.

  fetch    --cands cands.json --cache runtime/research/cand_ms --out bars.json
      Fetch KR bars via KisSource, US bars via yfinance; cache per ticker; output JSON.

  replay   --cands cands.json --bars bars.json --out result.json
      Three arms: FULL / MS_INIT / MS_SLOW.  Print compact Korean markdown summary.

Methodology note: this is exploratory, not preregistered.  Numbers carry caveats:
  - Entry assumed at analyzed_price on analysis_date (not necessarily fillable).
  - Exit approximation is daily (no intraday fill modelling).
  - Both markets pooled in the same arms for brevity; KR and US reported separately.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median as _median

RULE_VERSION = "candidate-microsplit-explore-v1"
SAMPLE_START = "2025-10-01"
SAMPLE_END = "2026-09-30 23:59:59"
HOLDOUT_START = "2026-07-01"

# Arm constants
ROUND_TRIP_COST = 0.0025   # 0.25 % round trip (initial)
ADD_COST_UNIT = 0.0025     # 0.25 % * add_size per add

# MS_SLOW add parameters
MS_ADD_STEP = 0.15
MS_ADD_VOL_MULT = 1.3
MS_MIN_SESSIONS_BEFORE_ADD = 3
MS_MIN_SESSIONS_BETWEEN_ADDS = 3
MS_CAP = 1.0

# Exits
EXIT_STOP_BUFFER = 0.995   # low <= stop * 0.995
HARD_STOP_FRAC = 0.93      # entry * 0.93
TREND_EXIT_MIN_SESSIONS = 5
TIME_EXIT_SESSION = 40

# Bands / bootstrap
BOOT = 2000
SEED = 20261005


# ------------------------------------------------------------------ helpers

def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def _median_val(xs):
    if not xs:
        return None
    return _median(xs)


def _win_rate(xs):
    if not xs:
        return None
    return sum(1 for x in xs if x > 0) / len(xs)


def _profit_factor(xs):
    gains = sum(x for x in xs if x > 0)
    losses = sum(-x for x in xs if x < 0)
    if losses == 0:
        return None
    return gains / losses


def _top10_share(xs):
    """Share of total positive profit from the top 10 % trades."""
    if not xs:
        return None
    positives = sorted([x for x in xs if x > 0], reverse=True)
    if not positives:
        return None
    top_n = max(1, len(xs) // 10)
    top_sum = sum(positives[:top_n])
    total = sum(positives)
    return top_sum / total if total > 0 else None


def mdd_equity(returns_by_date):
    """MDD of a slot-return ÷ 10 equity path, sorted by date."""
    level = peak = worst = 0.0
    for _, ret in sorted(returns_by_date):
        level += ret / 10
        peak = max(peak, level)
        worst = min(worst, level - peak)
    return worst


def atr14(prior_bars):
    """14-period ATR from last 15 bars [{high,low,close}]."""
    seg = prior_bars[-15:]
    if len(seg) < 15:
        return None
    trs = [
        max(b["high"] - b["low"],
            abs(b["high"] - a["close"]),
            abs(b["low"] - a["close"]))
        for a, b in zip(seg, seg[1:])
    ]
    return sum(trs) / len(trs)


def sma(closes, n):
    if len(closes) < n:
        return None
    return sum(closes[-n:]) / n


def risk_clip(legs, entry, stop, add_price, nominal_add):
    """Clip add size so total loss-at-stop <= (entry-stop)/entry per 1 slot."""
    if entry <= 0 or stop <= 0 or add_price <= 0 or stop >= entry:
        return nominal_add
    limit = (entry - stop) / entry
    base = sum(f * (1.0 - stop / p) for f, p in legs)
    marginal = 1.0 - stop / add_price
    if marginal <= 0:
        return 0.0
    if base >= limit:
        return 0.0
    return min(nominal_add, (limit - base) / marginal)


# ------------------------------------------------------------------ regime

def _parse_scenario(text):
    try:
        return json.loads(text or "{}")
    except (TypeError, ValueError):
        return {}


def classify_regime(text):
    t = str(text or "").strip()
    tl = t.lower()
    if (tl.startswith(("parabolic", "strong_bull", "moderate_bull"))
            or "상승추세" in t or "강세" in t):
        return "bull"
    if tl.startswith("sideways") or "횡보" in t:
        return "sideways"
    if tl.startswith(("bear", "weak_bear", "strong_bear")) or "약세" in t:
        return "bear"
    return "unknown"


def score_band(buy_score):
    if buy_score is None:
        return "unknown"
    if buy_score >= 7:
        return "7+"
    if buy_score >= 5:
        return "5-6"
    if buy_score >= 4:
        return "4"
    return "<=3"


# ------------------------------------------------------------------ extract

_SQL_KR_APT = """
SELECT
    apt.ticker,
    apt.analyzed_date AS entry_date,
    apt.analyzed_price AS entry_price,
    apt.buy_score,
    apt.min_score,
    apt.decision,
    apt.was_traded,
    apt.stop_loss AS apt_stop,
    apt.target_price,
    apt.watchlist_id,
    wh.scenario AS wh_scenario
FROM analysis_performance_tracker apt
LEFT JOIN watchlist_history wh ON wh.id = apt.watchlist_id
WHERE apt.analyzed_date >= ? AND apt.analyzed_date <= ?
ORDER BY apt.analyzed_date, apt.ticker
"""

_SQL_KR_TH = """
SELECT ticker, buy_date, buy_price, scenario
FROM trading_history
WHERE buy_date >= ? AND buy_date <= ?
"""

_SQL_US_APT = """
SELECT
    apt.ticker,
    apt.analysis_date AS entry_date,
    apt.analysis_price AS entry_price,
    apt.buy_score,
    apt.decision,
    apt.was_traded,
    apt.stop_loss AS apt_stop,
    apt.watchlist_id,
    wh.scenario AS wh_scenario
FROM us_analysis_performance_tracker apt
LEFT JOIN us_watchlist_history wh ON wh.id = apt.watchlist_id
WHERE apt.analysis_date >= ? AND apt.analysis_date <= ?
ORDER BY apt.analysis_date, apt.ticker
"""

_SQL_US_TH = """
SELECT ticker, buy_date, buy_price, scenario
FROM us_trading_history
WHERE buy_date >= ? AND buy_date <= ?
"""


def _stop_from_row(apt_stop, scenario_text, entry_price):
    """Return stop price: prefer scenario stop_loss, fallback entry*0.93."""
    s = _parse_scenario(scenario_text)
    raw = s.get("stop_loss") or apt_stop
    try:
        stop = float(raw) if raw is not None else None
    except (TypeError, ValueError):
        stop = None
    if stop and 0 < stop < entry_price:
        return stop
    return entry_price * HARD_STOP_FRAC


def _extract_market(conn, market, apt_sql, th_sql):
    """Return list of candidate dicts for one market."""
    # Build trading_history lookup: (ticker, date) -> {buy_price, scenario}
    traded = {}
    try:
        for row in conn.execute(th_sql, (SAMPLE_START, SAMPLE_END)):
            ticker, buy_date, buy_price, scenario = row
            day = buy_date[:10] if buy_date else None
            if day:
                key = (ticker, day)
                # prefer first (oldest buy) if duplicate
                if key not in traded:
                    traded[key] = {"buy_price": float(buy_price or 0),
                                   "scenario": scenario}
    except sqlite3.OperationalError:
        pass  # table may not exist

    rows = []
    seen = {}  # (ticker, day) -> index in rows; dedupe: prefer traded
    try:
        for row in conn.execute(apt_sql, (SAMPLE_START, SAMPLE_END)):
            if market == "KR":
                (ticker, entry_date, entry_price, buy_score,
                 min_score, decision, was_traded, apt_stop,
                 target_price, watchlist_id, wh_scenario) = row
                min_score = min_score
            else:
                (ticker, entry_date, entry_price, buy_score,
                 decision, was_traded, apt_stop,
                 watchlist_id, wh_scenario) = row
                min_score = None
                target_price = None

            day = (entry_date or "")[:10]
            if not day or not ticker or not entry_price:
                continue
            try:
                ep = float(entry_price)
            except (TypeError, ValueError):
                continue
            if ep <= 0:
                continue

            scenario_text = wh_scenario
            th_key = (ticker, day)
            th = traded.get(th_key)
            is_traded = bool(was_traded) or (th is not None)

            # scenario from trading_history overrides if traded
            if th and th.get("scenario"):
                scenario_text = th["scenario"]

            stop = _stop_from_row(apt_stop, scenario_text, ep)

            s = _parse_scenario(scenario_text)
            regime_raw = s.get("market_regime") or s.get("market_condition")

            try:
                bscore = float(buy_score) if buy_score is not None else None
            except (TypeError, ValueError):
                bscore = None

            rec = {
                "ticker": ticker,
                "market": market,
                "entry_date": day,
                "entry_price": ep,
                "buy_score": bscore,
                "min_score": min_score,
                "decision": decision,
                "was_traded": is_traded,
                "stop": stop,
                "target_price": target_price,
                "regime": regime_raw,
                "regime_class": classify_regime(regime_raw),
                "band": score_band(bscore),
            }

            key = (ticker, day)
            if key in seen:
                # replace only if this row is traded and the existing one is not
                if is_traded and not rows[seen[key]]["was_traded"]:
                    rows[seen[key]] = rec
            else:
                seen[key] = len(rows)
                rows.append(rec)

    except sqlite3.OperationalError as e:
        print(f"WARNING: {market} APT query failed: {e}", file=sys.stderr)

    return rows


def extract(db, out):
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    kr = _extract_market(conn, "KR", _SQL_KR_APT, _SQL_KR_TH)
    us = _extract_market(conn, "US", _SQL_US_APT, _SQL_US_TH)
    conn.close()
    rows = kr + us
    Path(out).write_text(
        json.dumps({"rule_version": RULE_VERSION, "rows": rows},
                   ensure_ascii=False))
    print(f"KR={len(kr)} US={len(us)} total={len(rows)} -> {out}")


# ------------------------------------------------------------------ fetch bars

def _fetch_kr_bars(source, ticker, start_ymd, end_ymd):
    """Return list of bar dicts from KisSource."""
    frame = source.price_history(ticker, start_ymd, end_ymd, adjusted=False)
    return [
        {
            "date": ix.strftime("%Y-%m-%d"),
            "high": float(x["High"]),
            "low": float(x["Low"]),
            "close": float(x["Close"]),
            "vol": int(x.get("Volume", x.get("volume", 0)) or 0),
        }
        for ix, x in frame.iterrows()
    ]


def _fetch_us_bars(ticker, start_iso, end_iso):
    """Return list of bar dicts from yfinance (no pandas in logic)."""
    import yfinance as yf
    t = yf.Ticker(ticker)
    df = t.history(start=start_iso, end=end_iso, auto_adjust=False)
    rows = []
    for ts, row in df.iterrows():
        rows.append({
            "date": ts.strftime("%Y-%m-%d"),
            "high": float(row["High"]),
            "low": float(row["Low"]),
            "close": float(row["Close"]),
            "vol": int(row.get("Volume", 0) or 0),
        })
    return rows


def fetch(cands_file, cache_dir, out, pause=0.3):
    rows = json.loads(Path(cands_file).read_text())["rows"]
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)

    # Compute spans per (market, ticker)
    spans = {}
    for r in rows:
        key = (r["market"], r["ticker"])
        day = r["entry_date"]
        a, b = spans.get(key, (day, day))
        spans[key] = (min(a, day), max(b, day))

    bars = {}
    kr_source = None

    for (market, ticker), (lo, hi) in sorted(spans.items()):
        mkey = f"{market}:{ticker}"
        # Need bars from ~45 sessions before lo through 40+ sessions after hi
        if market == "KR":
            lo_dt = datetime.strptime(lo, "%Y-%m-%d")
            hi_dt = datetime.strptime(hi, "%Y-%m-%d")
            start_ymd = (lo_dt - timedelta(days=90)).strftime("%Y%m%d")
            end_ymd = (hi_dt + timedelta(days=70)).strftime("%Y%m%d")
            cache_path = cache / f"cands_kr_{ticker}_{start_ymd}_{end_ymd}.json"
            if cache_path.exists():
                bars[mkey] = json.loads(cache_path.read_text())
                continue
            if kr_source is None:
                from cores.market_data.kis_source import KisSource
                kr_source = KisSource()
            try:
                data = _fetch_kr_bars(kr_source, ticker, start_ymd, end_ymd)
            except Exception as err:  # noqa: BLE001
                data = {"error": type(err).__name__, "detail": str(err)[:120]}
            cache_path.write_text(json.dumps(data))
            time.sleep(pause)
            bars[mkey] = data
        else:
            lo_dt = datetime.strptime(lo, "%Y-%m-%d")
            hi_dt = datetime.strptime(hi, "%Y-%m-%d")
            start_iso = (lo_dt - timedelta(days=90)).strftime("%Y-%m-%d")
            end_iso = (hi_dt + timedelta(days=70)).strftime("%Y-%m-%d")
            safe_ticker = ticker.replace("/", "_")
            cache_path = cache / f"cands_us_{safe_ticker}_{lo}_{hi}.json"
            if cache_path.exists():
                bars[mkey] = json.loads(cache_path.read_text())
                continue
            try:
                data = _fetch_us_bars(ticker, start_iso, end_iso)
            except Exception as err:  # noqa: BLE001
                data = {"error": type(err).__name__, "detail": str(err)[:120]}
            cache_path.write_text(json.dumps(data))
            time.sleep(pause)
            bars[mkey] = data

    Path(out).write_text(json.dumps({"rule_version": RULE_VERSION, "bars": bars}))
    print(f"tickers={len(bars)} -> {out}")


# ------------------------------------------------------------------ simulation

def _simulate_one(cand, ticker_bars):
    """Simulate FULL / MS_INIT / MS_SLOW for one candidate.

    Returns dict or None if insufficient data.
    entry_date is the analysis date; first evaluable session is the NEXT session.
    """
    if not isinstance(ticker_bars, list) or not ticker_bars:
        return None

    entry_date = cand["entry_date"]
    entry_price = cand["entry_price"]
    stop = cand["stop"]

    sessions = sorted(b["date"] for b in ticker_bars)
    bar_by = {b["date"]: b for b in ticker_bars}

    # Sessions strictly after entry_date (first evaluable = next session)
    future = [d for d in sessions if d > entry_date]
    if not future:
        return None

    # Need >=30 sessions prior for ATR14/SMA20
    prior_sessions = [d for d in sessions if d <= entry_date]
    if len(prior_sessions) < 30:
        return None

    prior_bars = [bar_by[d] for d in prior_sessions]
    atr_val = atr14(prior_bars)
    if atr_val is None:
        return None

    # initial_sizing from oneil_adaptive_policy
    try:
        from prism_core.oneil_adaptive_policy import initial_sizing
        _, init_frac_dec = initial_sizing(entry_price, atr_val)
        init_frac = float(init_frac_dec)
    except Exception:  # noqa: BLE001
        # Fallback: replicate the formula directly
        proxy = min(max(1.5 * atr_val / entry_price, 0.04), 0.10)
        init_frac = min(max(0.5 * 0.07 / proxy, 0.30), 0.80)

    # ---- helpers for exit logic ----
    def _sma20_at(idx):
        if idx < 20:
            return None
        closes = [bar_by[sessions[j]]["close"] for j in range(idx - 19, idx + 1)]
        return sum(closes) / 20

    def _vol20_avg_at(idx):
        if idx < 20:
            return None
        vols = [bar_by[sessions[j]].get("vol", 0) for j in range(idx - 20, idx)]
        return sum(vols) / 20

    # ---- simulate one arm ----
    def _run_arm(arm):
        """arm: 'FULL' | 'MS_INIT' | 'MS_SLOW'"""
        if arm == "FULL":
            legs = [(1.0, entry_price)]
        else:
            legs = [(init_frac, entry_price)]

        sessions_held = 0
        sessions_since_last_add = 0
        post_entry_high_close = None  # for MS_SLOW new-high tracking
        exit_date = None
        exit_price_val = None
        exit_reason = None

        for i, day in enumerate(sessions):
            if day <= entry_date:
                continue  # skip up to and including entry_date

            b = bar_by[day]
            bar_low = b["low"]
            bar_close = b["close"]
            bar_open = b.get("open", bar_close)
            bar_high = b["high"]

            sessions_held += 1
            sessions_since_last_add += 1

            total_frac = sum(f for f, _ in legs)
            avg_cost = sum(f * p for f, p in legs) / total_frac if total_frac > 0 else entry_price

            # Compute SMA20 using sessions up to and including THIS bar
            # (i is index in `sessions`; bars[i] is current)
            sma20_val = _sma20_at(i)

            # --- EXIT checks (evaluated before add) ---
            # 1. Stop: bar_low <= stop*0.995 or bar_low <= entry*0.93
            eff_stop = stop
            hard = entry_price * HARD_STOP_FRAC
            if bar_low <= max(eff_stop * EXIT_STOP_BUFFER, hard):
                trigger = max(eff_stop, hard)
                fill = min(bar_open, trigger) if bar_open < trigger else trigger
                exit_date, exit_price_val, exit_reason = day, fill, "stop"
                break

            # 2. Trend exit: after >=5 sessions, close < SMA20
            if sessions_held >= TREND_EXIT_MIN_SESSIONS and sma20_val is not None:
                if bar_close < sma20_val:
                    exit_date, exit_price_val, exit_reason = day, bar_close, "trend"
                    break

            # 3. Time exit: 40th session
            if sessions_held >= TIME_EXIT_SESSION:
                exit_date, exit_price_val, exit_reason = day, bar_close, "time"
                break

            # --- MS_SLOW add logic ---
            if arm == "MS_SLOW" and total_frac < MS_CAP:
                if sessions_since_last_add >= MS_MIN_SESSIONS_BETWEEN_ADDS:
                    # Condition: new post-entry closing high
                    is_new_high = (post_entry_high_close is None
                                   or bar_close > post_entry_high_close)
                    if is_new_high:
                        # Volume condition
                        vol20 = _vol20_avg_at(i)
                        vol_ok = vol20 is not None and b.get("vol", 0) >= MS_ADD_VOL_MULT * vol20
                        # SMA20 and cost conditions
                        sma_ok = sma20_val is not None and bar_close > sma20_val
                        cost_ok = bar_close > avg_cost
                        if vol_ok and sma_ok and cost_ok:
                            nominal = MS_ADD_STEP
                            # Never larger than previous add
                            if len(legs) >= 2:
                                prev_add = legs[-1][0]
                                nominal = min(nominal, prev_add)
                            # Cap at 1.0
                            nominal = min(nominal, MS_CAP - total_frac)
                            if nominal > 1e-9:
                                actual = risk_clip(legs, entry_price, stop,
                                                   bar_close, nominal)
                                if actual > 1e-9:
                                    legs.append((actual, bar_close))
                                    sessions_since_last_add = 0

            # Update post-entry high close
            if post_entry_high_close is None or bar_close > post_entry_high_close:
                post_entry_high_close = bar_close

        else:
            # Exhausted sessions without hitting any exit: exit at last bar close
            if future:
                last_day = future[-1] if future[-1] in bar_by else None
                if last_day:
                    exit_date = last_day
                    exit_price_val = bar_by[last_day]["close"]
                    exit_reason = "data_end"

        if exit_price_val is None or exit_price_val <= 0:
            return None

        total_frac = sum(f for f, _ in legs)
        gross = sum(f * (exit_price_val / p - 1) for f, p in legs)
        # Costs: round trip on initial + add_size * add_cost per add
        initial_cost = legs[0][0] * ROUND_TRIP_COST
        add_costs = sum(f * ADD_COST_UNIT for f, _ in legs[1:])
        net = gross - initial_cost - add_costs

        return {
            "exit_date": exit_date,
            "exit_price": exit_price_val,
            "exit_reason": exit_reason,
            "n_adds": len(legs) - 1,
            "final_frac": total_frac,
            "gross": gross,
            "net": net,
        }

    full = _run_arm("FULL")
    ms_init = _run_arm("MS_INIT")
    ms_slow = _run_arm("MS_SLOW")

    if full is None and ms_init is None and ms_slow is None:
        return None

    return {
        "FULL": full,
        "MS_INIT": ms_init,
        "MS_SLOW": ms_slow,
    }


# ------------------------------------------------------------------ statistics

def _boot_ci_mean(values, tickers, seed=SEED, n=BOOT):
    """Bootstrap 90% CI for mean of `values`, clustered by ticker."""
    if not values:
        return [None, None]
    by_ticker = {}
    for i, tk in enumerate(tickers):
        by_ticker.setdefault(tk, []).append(i)
    rng = random.Random(seed)
    all_tickers = sorted(by_ticker)
    boot_means = []
    for _ in range(n):
        chosen = [rng.choice(all_tickers) for _ in all_tickers]
        sample = [values[i] for t in chosen for i in by_ticker[t]]
        if sample:
            boot_means.append(sum(sample) / len(sample))
    boot_means.sort()
    k = len(boot_means)
    return [boot_means[int(0.05 * k)], boot_means[int(0.95 * k) - 1]] if k >= 2 else [None, None]


def _boot_ci_diff(vals_a, vals_b, tickers, seed=SEED, n=BOOT):
    """Bootstrap 90% CI for mean(b - a), clustered by ticker."""
    if not vals_a or len(vals_a) != len(vals_b):
        return [None, None]
    by_ticker = {}
    for i, tk in enumerate(tickers):
        by_ticker.setdefault(tk, []).append(i)
    rng = random.Random(seed)
    all_tickers = sorted(by_ticker)
    boot_diffs = []
    for _ in range(n):
        chosen = [rng.choice(all_tickers) for _ in all_tickers]
        sample_a, sample_b = [], []
        for t in chosen:
            for i in by_ticker[t]:
                sample_a.append(vals_a[i])
                sample_b.append(vals_b[i])
        if sample_a:
            boot_diffs.append(_mean([b - a for a, b in zip(sample_a, sample_b)]))
    boot_diffs.sort()
    k = len(boot_diffs)
    return [boot_diffs[int(0.05 * k)], boot_diffs[int(0.95 * k) - 1]] if k >= 2 else [None, None]


def _arm_metrics(records, arm, ref_arm=None):
    """Compute per-arm metrics from list of result dicts."""
    nets = []
    tickers = []
    sell_dates = []
    for r in records:
        arm_res = r.get(arm)
        if arm_res is None:
            continue
        nets.append(arm_res["net"])
        tickers.append(r["ticker"])
        sell_dates.append(arm_res["exit_date"])

    if not nets:
        return {"n": 0}

    ref_nets = None
    if ref_arm is not None:
        ref_nets = []
        for r in records:
            a = r.get(arm)
            b = r.get(ref_arm)
            if a is not None and b is not None:
                ref_nets.append(b["net"])
            elif a is not None:
                ref_nets = None
                break

    final_fracs = [r[arm]["final_frac"] for r in records if r.get(arm) is not None]
    n_adds_list = [r[arm]["n_adds"] for r in records if r.get(arm) is not None]

    metrics = {
        "n": len(nets),
        "mean": _mean(nets),
        "median": _median_val(nets),
        "win_rate": _win_rate(nets),
        "profit_factor": _profit_factor(nets),
        "top10_share": _top10_share(nets),
        "worst": min(nets),
        "mdd": mdd_equity(list(zip(sell_dates, nets))),
        "avg_final_alloc": _mean(final_fracs),
        "avg_adds": _mean(n_adds_list),
        "ci90_mean": _boot_ci_mean(nets, tickers),
    }
    if ref_nets is not None and len(ref_nets) == len(nets):
        diffs = [b - a for a, b in zip(ref_nets, nets)]
        metrics["mean_diff_vs_" + ref_arm] = _mean(diffs)
        metrics["ci90_diff_vs_" + ref_arm] = _boot_ci_diff(ref_nets, nets, tickers)

    return metrics


def _group_records(records, key_fn):
    """Group records by key_fn result; return {key: list}."""
    groups = {}
    for r in records:
        k = key_fn(r)
        groups.setdefault(k, []).append(r)
    return groups


def _summarize_group(records, label):
    """Return per-arm metrics for a group."""
    return {
        "label": label,
        "n": len(records),
        "FULL": _arm_metrics(records, "FULL"),
        "MS_INIT": _arm_metrics(records, "MS_INIT", ref_arm="FULL"),
        "MS_SLOW": _arm_metrics(records, "MS_SLOW", ref_arm="FULL"),
    }


# ------------------------------------------------------------------ Korean summary

def _fmt(v, pct=False, decimals=2):
    if v is None:
        return "n/a"
    if pct:
        return f"{v*100:+.{decimals}f}%"
    return f"{v:.{decimals}f}"


def _korean_summary(result_rows, summary):
    lines = [
        "## 후보군 마이크로스플릿 탐색 결과 (v1, 탐색적)",
        "",
        "> **주의**: 이 분석은 탐색적입니다. 진입가격은 분석일 당일 analyzed_price로 가정하며,",
        "> 실제 체결 가능성, 유동성, 슬리피지는 미반영입니다.",
        "",
        f"| 항목 | 값 |",
        f"|------|----|",
        f"| 전체 후보 | {summary['n_cands']}건 |",
        f"| 분석 기간 | {SAMPLE_START} ~ {SAMPLE_END[:10]} |",
        f"| KR 후보 | {summary['n_kr']}건 |",
        f"| US 후보 | {summary['n_us']}건 |",
        f"| 시뮬레이션 성공 | {summary['n_simulated']}건 |",
        f"| 실패(bars 없음/부족) | {summary['n_failed']}건 |",
        "",
    ]

    for group_key, gdata in summary.get("by_band", {}).items():
        n = gdata.get("n", 0)
        if n == 0:
            continue
        full = gdata.get("FULL", {})
        ms_slow = gdata.get("MS_SLOW", {})
        lines.append(f"### 점수대: {group_key}  (n={n})")
        lines.append("")
        lines.append("| 지표 | FULL | MS_SLOW | 차이(MS_SLOW-FULL) |")
        lines.append("|------|------|---------|-------------------|")
        diff = ms_slow.get("mean_diff_vs_FULL")
        ci = ms_slow.get("ci90_diff_vs_FULL") or [None, None]
        lines.append(f"| 평균 슬롯수익 | {_fmt(full.get('mean'),True)} | {_fmt(ms_slow.get('mean'),True)} | {_fmt(diff,True)} (90%CI {_fmt(ci[0],True)}..{_fmt(ci[1],True)}) |")
        lines.append(f"| 중앙값 | {_fmt(full.get('median'),True)} | {_fmt(ms_slow.get('median'),True)} | - |")
        lines.append(f"| 승률 | {_fmt(full.get('win_rate'),True,1)} | {_fmt(ms_slow.get('win_rate'),True,1)} | - |")
        lines.append(f"| 손익비 | {_fmt(full.get('profit_factor'),decimals=2)} | {_fmt(ms_slow.get('profit_factor'),decimals=2)} | - |")
        lines.append(f"| 최악 | {_fmt(full.get('worst'),True)} | {_fmt(ms_slow.get('worst'),True)} | - |")
        lines.append(f"| MDD | {_fmt(full.get('mdd'),True)} | {_fmt(ms_slow.get('mdd'),True)} | - |")
        lines.append(f"| 평균최종배분 | - | {_fmt(ms_slow.get('avg_final_alloc'),decimals=2)} | - |")
        lines.append("")

    return "\n".join(lines)


# ------------------------------------------------------------------ replay

def replay(cands_file, bars_file, out):
    rows = json.loads(Path(cands_file).read_text())["rows"]
    bars = json.loads(Path(bars_file).read_text())["bars"]

    result_rows = []
    n_failed = 0

    for cand in rows:
        mkey = f"{cand['market']}:{cand['ticker']}"
        ticker_bars = bars.get(mkey)
        sim = _simulate_one(cand, ticker_bars)
        if sim is None:
            n_failed += 1
            continue
        result_rows.append({**cand, **sim})

    # Time split
    early = [r for r in result_rows if r["entry_date"] < HOLDOUT_START]
    late = [r for r in result_rows if r["entry_date"] >= HOLDOUT_START]

    def _band_regime_splits(records):
        band_groups = _group_records(records, lambda r: r["band"])
        regime_groups = _group_records(records, lambda r: r["regime_class"])
        market_groups = _group_records(records, lambda r: r["market"])
        return {
            "overall": _summarize_group(records, "overall"),
            "by_band": {k: _summarize_group(v, k) for k, v in sorted(band_groups.items())},
            "by_regime": {k: _summarize_group(v, k) for k, v in sorted(regime_groups.items())},
            "by_market": {k: _summarize_group(v, k) for k, v in sorted(market_groups.items())},
        }

    summary = {
        "rule_version": RULE_VERSION,
        "inputs_sha256": hashlib.sha256(
            Path(cands_file).read_bytes() + Path(bars_file).read_bytes()
        ).hexdigest(),
        "n_cands": len(rows),
        "n_kr": sum(1 for r in rows if r["market"] == "KR"),
        "n_us": sum(1 for r in rows if r["market"] == "US"),
        "n_simulated": len(result_rows),
        "n_failed": n_failed,
        "all": _band_regime_splits(result_rows),
        "early_2025_2026_06": _band_regime_splits(early),
        "late_2026_07_plus": _band_regime_splits(late),
    }

    # Also expose by_band at top level for Korean summary convenience
    summary["by_band"] = summary["all"]["by_band"]

    Path(out).write_text(
        json.dumps({"summary": summary, "rows": result_rows},
                   ensure_ascii=False, indent=1, default=str)
    )

    korean_md = _korean_summary(result_rows, summary)
    print(korean_md)
    print(f"\n-> {out}")


# ------------------------------------------------------------------ CLI

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("extract", help="Extract candidates from DB -> JSON")
    e.add_argument("--db", default="stock_tracking_db.sqlite")
    e.add_argument("--out", required=True)

    f = sub.add_parser("fetch", help="Fetch daily bars for candidates -> JSON (cached)")
    f.add_argument("--cands", required=True)
    f.add_argument("--cache", default="runtime/research/cand_ms")
    f.add_argument("--out", required=True)
    f.add_argument("--pause", type=float, default=0.3)

    r = sub.add_parser("replay", help="Run FULL/MS_INIT/MS_SLOW simulation -> JSON + Korean summary")
    r.add_argument("--cands", required=True)
    r.add_argument("--bars", required=True)
    r.add_argument("--out", required=True)

    a = p.parse_args(argv)
    if a.cmd == "extract":
        extract(a.db, a.out)
    elif a.cmd == "fetch":
        fetch(a.cands, a.cache, a.out, a.pause)
    else:
        replay(a.cands, a.bars, a.out)


if __name__ == "__main__":
    sys.exit(main())
