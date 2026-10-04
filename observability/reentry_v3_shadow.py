"""Re-entry v3: campaign re-entry decided before the close (ledger, recheck and the LIVE hand-off).

This module writes no DB and sends no Telegram. With REENTRY_V3_LIVE_ENABLED on, approved rechecks of the
decision run are handed to prism_core/reentry_v3_live, which places the real buy in an isolated
subprocess through the tracker's normal entry path (kill switch, daily cap, idempotency, hours).

Enrolment is the v2 one (observability.reentry_shadow.candidates with include_blocked=True:
STOP_EXIT / LOCATION_SKIP / ENTER_BLOCKED, 70-day window, point-in-time dedupe). Each watch
becomes a campaign around one level L (prism_core/reentry_campaign.py); the whole ledger is
replayed from the anchor on every run, so reruns are idempotent and the only stored inputs are
the live decisions.

* ``phase="intraday"`` (KR 14:00 KST, US 13:50 New York: before the afternoon batches at KR 14:46 /
  US 14:30 ET and before the KR closing auction at 15:20): for campaigns that may open a position
  today, read the live quote, evaluate each campaign rule's triggers (R1C/R2S, or SHAKEOUT_RECLAIM
  inside a shakeout window) with that price as the close proxy and the projected full-day volume,
  record the decision (decision_price, decision_time, low so far), freeze the recheck inputs of a
  fired trigger and run the LLM recheck.
* ``phase="close"`` (after the close): refresh completed bars; the replay applies recorded
  decisions, exits, shakeout windows, watch ends (L97 and SS), outcomes and the pivot control.
  Days without a live decision (before enrolment, missed runs) are evaluated on the close
  (BACKFILL, no LLM).

Design and evidence: docs/REENTRY_V3_LIVE_ko.md.
"""
from __future__ import annotations

import bisect
import contextlib
import fcntl
import hashlib
import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from observability import reentry_v2_shadow as V2
from observability import reentry_v3_recheck as RC3
from observability.events import emit_event
from observability.reentry_recheck_inputs import trend_fact_lines
from observability.reentry_shadow import _atomic, candidates
from prism_core import reentry_campaign as C
from prism_core import reentry_v3_live as LIVE

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "trading/config/reentry_v3_shadow.json"
POLICY = {"mode": "SHADOW", "policy_version": C.POLICY_VERSION, "markets": ["KR", "US"], "enabled": True,
          "llm_recheck": True}
STATE_DIR = ROOT / "runtime"
DB_PATH = ROOT / "stock_tracking_db.sqlite"
ARCHIVE_DB = ROOT / "archive.db"
SCHEMA_VERSION = 1
LOOKBACK_DAYS = V2.LOOKBACK_DAYS      # 70 calendar days, same enrolment window as v2
ARCHIVE_AFTER_DAYS = 150
MIN_HISTORY = 60                      # sessions before the anchor (prior-60 high, MA20, ATR14)
MAX_QUOTES = 80                       # bounded live reads per intraday run
INPUT_CONTRACT = "reentry_v3_recheck_input_v1"
TZ = {"KR": "Asia/Seoul", "US": "America/New_York"}
FINAL = {"CLOSED", "MISSING_FINAL"}


def _flag(name, default):
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def enabled(market):
    try:
        policy = json.loads(POLICY_PATH.read_text())
        return (policy.get("mode") == "SHADOW" and policy.get("enabled") is True
                and policy.get("policy_version") == C.POLICY_VERSION and market in policy.get("markets", [])
                and _flag("REENTRY_V3_SHADOW_ENABLED", "true"))
    except (OSError, ValueError):
        return False


def llm_recheck_enabled():
    """Opt-in: the BUY recheck spends LLM quota, so it runs only with REENTRY_V3_LLM_RECHECK=true."""
    try:
        policy = json.loads(POLICY_PATH.read_text())
    except (OSError, ValueError):
        return False
    return policy.get("llm_recheck") is True and _flag("REENTRY_V3_LLM_RECHECK", "false")


def paths(market, root=STATE_DIR):
    stem = Path(root) / f"reentry_v3_state_{market.lower()}"
    return {"state": stem.with_suffix(".json"), "lock": stem.with_suffix(".lock"),
            "archive": Path(f"{stem}_archive.jsonl"),
            "inputs": Path(root) / f"reentry_v3_recheck_inputs_{market.lower()}.jsonl",
            "results": Path(root) / f"reentry_v3_recheck_results_{market.lower()}.jsonl",
            "live": Path(root) / f"reentry_v3_live_{market.lower()}.jsonl"}


def _new_state(market):
    return {"schema_version": SCHEMA_VERSION, "policy_version": C.POLICY_VERSION, "market": market, "watches": []}


def _load(path, market):
    if not path.exists():
        return _new_state(market)
    if path.stat().st_size > 8_000_000:
        raise ValueError("oversized_state")
    state = json.loads(path.read_text())
    if state.get("schema_version") != SCHEMA_VERSION or state.get("policy_version") != C.POLICY_VERSION \
            or state.get("market") != market:
        raise ValueError("state_version")
    return state


def _watch_id(market, row):
    raw = json.dumps([C.POLICY_VERSION, market, row["source"], row["account_key"], row["ticker"],
                      row["entry_date"], row["exit_date"]], separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _hash(*parts):
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:32]


class MarketContext:
    """Deterministic regime (production cores.data_prefetch._compute_kr_regime on the regime
    benchmark: KOSPI for KR, SPY as the S&P 500 proxy for US) after the BUY distribution-day
    caution (cores.buy_gate.effective_buy_regime), and the MarketPulse state per ticker benchmark,
    both as of the session before a given day."""

    def __init__(self, regime_rows):
        self.regime_rows, self._regimes, self._pulses = regime_rows or [], {}, {}

    def regime_detail(self, day):
        if day not in self._regimes:
            base = V2.kr_regime(self.regime_rows, day) if self.regime_rows else None
            detail = {"base": base, "effective": base, "distribution_days": None, "caution": False}
            if base:
                from cores.buy_gate import effective_buy_regime
                from observability.reentry_recheck_inputs import market_facts
                days = market_facts(self.regime_rows, day)["distribution_days"]
                effective, caution = effective_buy_regime(base, days)
                detail.update(effective=effective, distribution_days=days, caution=caution)
            self._regimes[day] = detail
        return self._regimes[day]

    def regime_at(self, day):
        return self.regime_detail(day)["effective"]

    def pulse_fn(self, bench):
        if not bench:
            return lambda day: None
        key = (len(bench), bench[0]["date"], bench[-1]["date"], bench[-1]["close"])
        if key not in self._pulses:
            from cores.market_pulse import DailyBar, MarketPulse
            pulse, dates, states = MarketPulse(), [], []
            for bar in bench:
                dates.append(bar["date"])
                states.append(pulse.feed(DailyBar(date=bar["date"], close=bar["close"],
                                                  volume=bar["volume"] if bar["volume"] > 1 else None)))
            self._pulses[key] = (dates, states)
        dates, states = self._pulses[key]

        def at(day):
            k = bisect.bisect_left(dates, day)
            return states[k - 1] if k else None
        return at


def _frames_for(watch, frames, completed):
    bars = [b for b in (frames.get(watch["ticker"]) or []) if b["date"] <= completed]
    bench = [b for b in ((frames.get("__benchmark_rows") or {}).get(watch["ticker"]) or []) if b["date"] <= completed]
    return bars, bench


def _refresh(watch, frames, completed, market, ctx):
    """Enrol (first time) and replay the campaign over completed bars. True when replayed."""
    bars, bench = _frames_for(watch, frames, completed)
    if not bars or bars[-1]["date"] != completed:
        watch["last_missing"] = completed
        return False
    row = watch["row"]
    anchor = next((k for k, b in enumerate(bars) if b["date"] == row["exit_date"]), None)
    if watch["status"] == "PENDING_ENROLL":
        price = float(row["exit_price"] or 0)
        if anchor is None or not (bars[anchor]["low"] * 0.97 <= price <= bars[anchor]["high"] * 1.03):
            watch.update(status="MISSING_FINAL", reason="price_basis_mismatch", ended_on=row["exit_date"])
            return False
        if anchor < MIN_HISTORY:
            watch.update(status="MISSING_FINAL", reason="short_history", ended_on=row["exit_date"])
            return False
        setup = C.make_setup(row["source"], row.get("key_levels"), row["entry_price"], price, row["exit_date"])
        if setup is None:
            watch.update(status="MISSING_FINAL", reason="no_level", ended_on=row["exit_date"])
            return False
        watch.update(status="ACTIVE", setup=setup)
    if anchor is None:
        watch["last_missing"] = completed
        return False
    ledger = C.replay(watch["setup"], bars, anchor, decisions=watch.get("decisions"), regime_at=ctx.regime_at,
                      pulse_at=ctx.pulse_fn(bench), stop_dates=watch.get("stop_dates") or (), market=market)
    watch["ledger"], watch["asof"] = ledger, completed
    camps = ledger["campaigns"]
    primary = camps[C.POLICY["primary_rule"]]
    if primary["status"] == "ENDED":
        watch["watch_ended"] = primary["end_date"]       # point-in-time dedupe (V2._blocked) follows L97
    real_open = any(att.get("real") and att["status"] == "OPEN" for c in camps.values() for att in c["attempts"])
    if all(c["status"] == "ENDED" for c in camps.values()) and not real_open:
        watch["status"] = "CLOSED"
    return True


def advance(state, rows, frames, completed, market, ctx):
    watches = {w["watch_id"]: w for w in state["watches"]}
    state.setdefault("started_session", completed)
    stops = {}
    for row in rows:
        if row["source"] == "STOP_EXIT":
            stops.setdefault(row["ticker"], set()).add(row["exit_date"])
    for watch in watches.values():
        if watch["ticker"] in stops:
            watch["stop_dates"] = sorted(set(watch.get("stop_dates") or []) | stops[watch["ticker"]])
    for watch in watches.values():
        if watch["status"] not in FINAL:
            _refresh(watch, frames, completed, market, ctx)
    enrolled = 0
    for row in sorted(rows, key=lambda r: (r["exit_date"], r["entry_date"], r["ticker"])):
        wid = _watch_id(market, row)
        if wid in watches or V2._blocked(watches, row):
            continue
        watches[wid] = {"watch_id": wid, "status": "PENDING_ENROLL", "row": row, "market": market,
                        "source": row["source"], "ticker": row["ticker"], "enrolled_at": completed,
                        "enrollment": "PROSPECTIVE" if row["exit_date"] >= state["started_session"] else "LATE",
                        "stop_dates": sorted(stops.get(row["ticker"], ())), "decisions": {}, "rechecks": {}}
        enrolled += 1
        _refresh(watches[wid], frames, completed, market, ctx)
    state["watches"] = list(watches.values())
    return enrolled


# ---------------------------------------------------------------- intraday decision
def _local_time(iso, market):
    return datetime.fromisoformat(iso).astimezone(ZoneInfo(TZ[market])).strftime("%Y-%m-%d %H:%M %Z")


def decide(watch, quote, frames, completed, decision_day, market, ctx):
    """Record the decision-time evaluation for one watch, per campaign rule (a rule inside a shakeout
    window and only checks SHAKEOUT_RECLAIM). Returns the decision; "trigger" is the primary (L97) rule's
    fired trigger or None (an SS-only trigger stays in by_rule as a parallel record)."""
    bars, bench = _frames_for(watch, frames, completed)
    ledger = watch["ledger"]
    share = quote.get("volume_share")
    projected = C.projected_volume(quote.get("volume"), market, share)
    regime = ctx.regime_detail(decision_day)
    eligible = {rule: info for rule, info in ledger["next"].items() if info["eligible"]}
    by_rule, checks, results = {}, {}, {}
    for rule, info in eligible.items():
        window = info.get("window")
        key = (window["R"], window["start_date"]) if window else None
        if key not in results:
            results[key] = C.evaluate(watch["setup"], bars=bars, i=len(bars), flags=ledger["flags"],
                                      price=quote["price"], day_low=quote.get("low"), regime=regime["effective"],
                                      window=window, volume_projected=projected)
        by_rule[rule], checks[rule] = results[key]["trigger"], results[key]["checks"]
    primary = C.POLICY["primary_rule"]
    trigger = by_rule.get(primary)       # only the primary L97 rule is rechecked / traded; SS is a record
    first = next(iter(results.values()))
    decision = {"phase": "intraday", "decision_day": decision_day, "decision_time": quote["observed_at"],
                "decision_price": quote["price"], "day_low": quote.get("low"),
                "quote": {k: quote.get(k) for k in ("price", "low", "open", "high", "volume", "observed_at", "source")},
                "volume_share": share or C.VOLUME_SHARE_DEFAULT[market],
                "volume_share_source": "ticker_profile" if share else "market_default",
                "volume_projected": projected, "by_rule": by_rule, "trigger": trigger, "checks": checks,
                "prev_close": first["prev_close"], "prior_high": first["prior_high"], "regime": first["regime"],
                "regime_detail": regime, "regime_missing": first["regime_missing"],
                "market_pulse": ctx.pulse_fn(bench)(decision_day),
                "eligible_rules": {rule: info["attempt"] for rule, info in eligible.items()},
                "windows": {rule: info.get("window") for rule, info in eligible.items() if info.get("window")}}
    watch.setdefault("decisions", {})[decision_day] = decision
    return decision


def facts_text(bars, quote, decision_day, market, bench):
    """Trend/Pulse facts as of the previous completed session plus the decision-input features with
    today's forming bar (cumulative volume is never treated as a completed session)."""
    from prism_core.decision_input_features import compute, render_facts_block
    price = quote["price"]
    forming = {"date": decision_day, "open": quote.get("open") or price,
               "high": max(quote.get("high") or price, price), "low": quote.get("low") or price, "close": price,
               "volume": float(quote.get("volume") or 0)}
    series = bars + [forming]
    lines = trend_fact_lines(series, len(bars), bench)
    observed = datetime.fromisoformat(quote["observed_at"])
    result = compute(series, market=market, observed_at=observed, current_price=price)
    facts, _ = render_facts_block(result, None, None, market=market, language="ko")
    return "\n".join(lines) + "\n\n" + facts


def freeze_inputs(market, watch, decision, frames, completed, reports_root, archive_db):
    """Everything the LLM recheck needs, fixed at the decision. Report text by reference + hash."""
    bars, bench = _frames_for(watch, frames, completed)
    setup, ledger, trigger, row = watch["setup"], watch["ledger"], decision["trigger"], watch["row"]
    day = decision["decision_day"]
    atr = C.atr14(bars)
    alloc, _ = C.b3_allocation(decision["decision_price"], atr)
    attempts = {}
    for rule, number in decision["eligible_rules"].items():
        if not decision["by_rule"].get(rule):
            continue
        prior = [{k: t.get(k) for k in ("date", "trigger", "entry", "exit_date", "exit_reason", "ret", "mode",
                                        "real")}
                 for t in ledger["campaigns"][rule]["attempts"]]
        attempts[rule] = {"attempt": number, "max": C.POLICY["max_attempts"], "prior": prior}
    broke = ledger["flags"]["above"]
    reclaim = C.reclaim_level(setup, broke)
    end_levels = {rule: C.CAMPAIGN_RULES[rule](setup, broke) for rule in C.POLICY["campaign_rules"]}
    item = {
        "contract": INPUT_CONTRACT, "policy_version": C.POLICY_VERSION, "market": market,
        "event_id": _hash(watch["watch_id"], day, trigger["trigger"]), "watch_ref": watch["watch_id"],
        "ticker": watch["ticker"], "source": watch["source"], "trigger_date": day, "trigger": trigger["trigger"], "live_rule": C.POLICY["primary_rule"],
        "trigger_label": trigger["label"], "entry": decision["decision_price"],
        "decision_price": decision["decision_price"], "decision_time": decision["decision_time"],
        "decision_time_local": _local_time(decision["decision_time"], market), "day_low": decision["day_low"],
        "prev_close": decision["prev_close"], "level": {"L": setup["L"], "basis": setup["level_basis"]},
        "target": trigger["target"], "target_source": trigger["target_source"], "target_rule": trigger["target_rule"],
        "resistance": trigger["resistance"], "add_condition": trigger["add_condition"],
        "target_unsupported": trigger["target_unsupported"], "stop": trigger["stop"],
        "stop_rule": trigger["stop_rule"], "max_stop": trigger["max_stop"], "rr": trigger["rr"],
        "rr_floor": trigger["rr_floor"],
        "shakeout": ({k: trigger.get(k) for k in ("shakeout_low", "window_start", "volume_ratio")}
                     if trigger["trigger"] == "SHAKEOUT_RECLAIM" else None),
        "volume_projected": decision["volume_projected"], "volume_share": decision["volume_share"],
        "volume_share_source": decision["volume_share_source"],
        "regime": decision["regime"], "regime_detail": decision["regime_detail"],
        "regime_missing": decision["regime_missing"],
        "market_pulse": decision["market_pulse"], "attempts": attempts,
        "campaign": {"rules": list(C.POLICY["campaign_rules"]), "eligible_rules": sorted(decision["eligible_rules"]),
                     "fired_rules": sorted(attempts), "anchor_date": setup["anchor_date"],
                     "elapsed": ledger["elapsed"], "horizon": C.POLICY["horizon"],
                     "end_levels": {k: (round(v, 4) if v else None) for k, v in end_levels.items()},
                     "reclaim_level": reclaim,
                     "deep_level": round(reclaim * C.SHAKEOUT_DEEP, 4) if reclaim else None,
                     "shakeout_window_sessions": C.SHAKEOUT_WINDOW, "windows": decision["windows"]},
        "rule_ids": ledger["rule_ids"], "allocation": alloc, "atr14": round(atr, 6) if atr else None,
        "gates": {"G1_step16": C.g1_blocked(set(watch.get("stop_dates") or [])
                                            | {d for c in ledger["campaigns"].values()
                                               for d in c["virtual_stop_dates"]}
                                            | ({setup["anchor_date"]} if watch["source"] == "STOP_EXIT" else set()),
                                            bars + [{"date": day}], len(bars)),
                  "G2_v2_one_retry": max(a["attempt"] for a in attempts.values()) >= 2},
        "report_ref": V2.report_reference(market, watch["ticker"], day, reports_root, archive_db),
        "facts_text": facts_text(bars, decision["quote"], day, market, bench),
        "original": {"decided_on": row["exit_date"],
                     "reason": row.get("skip_reason") or (f"stopped out ({row.get('realized_pct')}%)"
                                                          if watch["source"] == "STOP_EXIT" else None),
                     "buy_score": row.get("buy_score"), "min_score": row.get("min_score"),
                     "decision_id": row.get("decision_id"), "key_levels": row.get("key_levels"),
                     "entry_price": row.get("entry_price"), "exit_price": row.get("exit_price"),
                     "realized_pct": row.get("realized_pct")},
        "llm_recheck": "NOT_EVALUATED", "frozen_at": datetime.now(timezone.utc).isoformat(),
    }
    appendix = RC3.micro_split_appendix(market, alloc)
    item["appendix_text"] = appendix
    item["appendix_sha256"] = hashlib.sha256(appendix.encode()).hexdigest() if appendix else None
    return item


def intraday(state, market, decision_day, frames, completed, quote_fn, ctx, reports_root, archive_db):
    """Decision-time (KR 14:00 / US 13:50) evaluations for campaigns that may open a position today.
    Returns (frozen, counts)."""
    counts = {"candidates": 0, "quotes": 0, "quote_missing": 0, "quote_cap": 0, "decisions": 0, "triggers": 0}
    frozen = []
    for watch in state["watches"]:
        ledger = watch.get("ledger") or {}
        if watch["status"] != "ACTIVE" or watch.get("asof") != completed or decision_day <= completed \
                or not any(info["eligible"] for info in (ledger.get("next") or {}).values()) \
                or decision_day in (watch.get("decisions") or {}):
            continue
        counts["candidates"] += 1
        if counts["quotes"] >= MAX_QUOTES:
            counts["quote_cap"] += 1
            continue
        counts["quotes"] += 1
        quote = quote_fn(watch["ticker"])
        if not quote or not quote.get("price"):
            counts["quote_missing"] += 1
            watch["quote_missing"] = (watch.get("quote_missing") or [])[-9:] + [decision_day]
            continue    # no decision: the close phase falls back to the close (BACKFILL, no LLM)
        decision = decide(watch, quote, frames, completed, decision_day, market, ctx)
        counts["decisions"] += 1
        if decision["trigger"]:
            counts["triggers"] += 1
            item = freeze_inputs(market, watch, decision, frames, completed, reports_root, archive_db)
            decision["event_id"] = item["event_id"]
            watch.setdefault("rechecks", {})[item["event_id"]] = {"status": "PENDING", "attempts": 0, "last": None}
            frozen.append((watch, item))
    return frozen, counts


# ---------------------------------------------------------------- run
def archive_finished(state, archive_path, completed):
    cutoff = (date.fromisoformat(completed) - timedelta(days=ARCHIVE_AFTER_DAYS)).isoformat()
    done = [w for w in state["watches"] if w["status"] in FINAL and w["row"]["exit_date"] < cutoff]
    if not done:
        return 0
    with archive_path.open("a", encoding="utf-8") as handle:
        for watch in done:
            handle.write(json.dumps(watch, ensure_ascii=False, allow_nan=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    ids = {w["watch_id"] for w in done}
    state["watches"] = [w for w in state["watches"] if w["watch_id"] not in ids]
    return len(done)


def _service(market):
    return f"prism-{market.lower()}-reentry-v3-shadow"


def _ledger_events(state, market):
    """Newly closed attempts and campaign ends since the last run (keys kept on the watch)."""
    out = []
    for watch in state["watches"]:
        seen = set(watch.get("emitted") or [])
        for rule, camp in ((watch.get("ledger") or {}).get("campaigns") or {}).items():
            for att in camp["attempts"]:
                key = f"exit|{rule}|{att['attempt']}|{att['date']}"
                if att["status"] == "CLOSED" and key not in seen:
                    seen.add(key)
                    out.append(("reentry_v3.shadow_exit", watch, key, {
                        "rule": rule, "attempt": att["attempt"], "trigger": att["trigger"], "mode": att["mode"],
                        "entry_date": att["date"], "exit_date": att["exit_date"], "exit_reason": att["exit_reason"],
                        "exit_rule": att["exit_rule"], "stop_rule": att["stop_rule"], "ret": att["ret"],
                        "alloc": att["alloc"], "slot": att["slot"], "gates": att["gates"]}))
            key = f"end|{rule}"
            if camp["status"] == "ENDED" and key not in seen:
                seen.add(key)
                out.append(("reentry_v3.shadow_campaign_end", watch, key, {
                    "rule": rule, "end_date": camp.get("end_date"), "end_reason": camp.get("end_reason"),
                    "end_rule": camp.get("end_rule"), **C.campaign_summary(camp)}))
        watch["emitted"] = sorted(seen)
    return out


def _tally(values):
    out = {}
    for value in values:
        out[value] = out.get(value, 0) + 1
    return out


def _sync_live_exits(state, market, db_path):
    """Link sold LIVE re-entry positions (trading_history, read-only) to their decision for the replay."""
    for watch in state["watches"]:
        for day, live in (watch.get("live") or {}).items():
            if live.get("status") == "BOUGHT" and not live.get("exit"):
                found = LIVE.find_real_exit(db_path, market, live, watch["watch_id"], day)
                if found:
                    live["exit"] = found
            decision = (watch.get("decisions") or {}).get(day)
            if decision is not None:
                decision["live"] = dict(live)


def _items_for(p, records):
    wanted = {r.get("event_id") for r in records}
    items = {}
    if p["inputs"].exists():
        with p["inputs"].open(encoding="utf-8") as handle:
            for line in handle:
                item = json.loads(line)
                if item.get("event_id") in wanted:
                    items[item["event_id"]] = item
    return items


def _entry_context(entry, state, frames, completed, reports_root, market, db_path=None):
    """Everything the tracker entry needs beyond the scenario: name, sector, decision bars, report path.

    Sector: the recheck scenario's, else the original decision row's (trading/watchlist history), else
    "Unknown" (which the tracker's sector limit does not count).
    """
    from observability.reentry_recheck_inputs import REPORT_DIRS
    watch = next((w for w in state["watches"] if w["watch_id"] == entry["watch_id"]), None)
    if watch is None:
        return None
    bars, _ = _frames_for(watch, frames, completed)
    ref = entry["item"].get("report_ref") or {}
    report_path = str(Path(reports_root) / REPORT_DIRS[market] / ref["name"]) if ref.get("kind") == "file" else None
    return {"company_name": watch["row"].get("company_name") or watch["ticker"],
            "sector": ((entry["record"].get("scenario") or {}).get("sector")
                       or (LIVE.original_sector(db_path, market, watch["row"]) if db_path else None) or "Unknown"),
            "bars": bars[-80:], "report_path": report_path}


def run(market, completed, *, collector, phase="close", decision_day=None, quote_fn=None, db_path=DB_PATH,
        root=STATE_DIR, reports_root=ROOT, archive_db=ARCHIVE_DB, dry_run=False, llm_recheck=None, llm=None,
        live_executor=None, now_fn=None):
    p = paths(market, root)
    if not dry_run:
        p["state"].parent.mkdir(parents=True, exist_ok=True)
    # A dry run writes nothing (not even the lock file); it never orders, so it needs no run lock.
    with (contextlib.nullcontext(None) if dry_run else p["lock"].open("a")) as lock:
        if lock is not None:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {"mode": "SHADOW", "trading_impact": "none", "skipped": "lock_held", "market": market}
        state = _load(p["state"], market)
        _sync_live_exits(state, market, db_path)
        rows = candidates(db_path, market, completed, lookback_days=LOOKBACK_DAYS, include_blocked=True)
        tickers = list(dict.fromkeys([w["ticker"] for w in state["watches"] if w["status"] not in FINAL]
                                     + [r["ticker"] for r in rows]))
        frames = collector(tickers, completed) if tickers else {}
        ctx = MarketContext(frames.get("__regime_rows"))
        enrolled = advance(state, rows, frames, completed, market, ctx)
        state["last_completed"] = completed
        frozen, counts = [], {}
        if phase == "intraday":
            frozen, counts = intraday(state, market, decision_day, frames, completed, quote_fn, ctx, reports_root,
                                      archive_db)
            state["last_intraday"] = decision_day
        archived, sent, ledger_events = 0, 0, []
        if not dry_run:
            # Freeze before the state records the event as pending (append-only, may repeat on a crash).
            if frozen:
                with p["inputs"].open("a", encoding="utf-8") as handle:
                    for _, item in frozen:
                        handle.write(json.dumps(item, ensure_ascii=False, allow_nan=False) + "\n")
            ledger_events = _ledger_events(state, market)
            archived = archive_finished(state, p["archive"], completed)
            _atomic(p["state"], state)
            now = datetime.now(timezone.utc)
            for watch, item in frozen:
                attrs = {"mode": "SHADOW", "trading_impact": "none", "llm_recheck": "NOT_EVALUATED",
                         "policy_version": C.POLICY_VERSION, "source": watch["source"],
                         "enrollment": watch["enrollment"], "watch_ref": watch["watch_id"],
                         "trigger": item["trigger"], "trigger_date": item["trigger_date"],
                         "decision_price": item["decision_price"], "decision_time": item["decision_time"],
                         "level": item["level"]["L"], "stop": item["stop"], "target": item["target"], "rr": item["rr"],
                         "rr_floor": item["rr_floor"], "target_rule": item["target_rule"], "regime": item["regime"],
                         "market_pulse": item["market_pulse"], "attempts": {r: a["attempt"] for r, a in
                                                                            item["attempts"].items()},
                         "gates": item["gates"], "allocation": item["allocation"],
                         "report_available": item["report_ref"] is not None,
                         "micro_split_appendix": bool(item["appendix_text"])}
                if emit_event("reentry_v3.shadow_trigger", service=_service(market), event_id=item["event_id"],
                              market=market, ticker=watch["ticker"], attributes=attrs, event_time=now) is not None:
                    sent += 1
            for name, watch, key, attrs in ledger_events:
                emit_event(name, service=_service(market), event_id=_hash(watch["watch_id"], key), market=market,
                           ticker=watch["ticker"], attributes={"mode": "SHADOW", "trading_impact": "none",
                                                               "policy_version": C.POLICY_VERSION,
                                                               "watch_ref": watch["watch_id"],
                                                               "source": watch["source"], **attrs}, event_time=now)
        rechecked = []
        live_enabled = LIVE.live_enabled(market)
        if not dry_run and (llm_recheck if llm_recheck is not None else llm_recheck_enabled()):
            cap = {}
            if phase == "intraday" and live_enabled:
                # Today's triggers: recheck at most the remaining daily cap + 1, in LIVE rank order.
                remaining = LIVE.cap_remaining(LIVE.load_journal(p["live"]), decision_day)
                cap = {"only_day": decision_day, "limit": remaining + 1 if remaining else 0,
                       "stop_after_approvals": remaining}
            rechecked = _run_rechecks(state, market, completed, p, reports_root, archive_db, llm, **cap)
        live_results = []
        if phase == "intraday" and not dry_run and live_enabled and rechecked:
            def _emit_live(name, watch, key, attrs):
                emit_event(name, service=_service(market), event_id=_hash(watch["watch_id"], key), market=market,
                           ticker=watch["ticker"], attributes={"mode": "LIVE", "trading_impact": "order",
                                                               "policy_version": C.POLICY_VERSION,
                                                               "watch_ref": watch["watch_id"],
                                                               "source": watch["source"], **attrs},
                           event_time=datetime.now(timezone.utc))
            live_results = LIVE.process(
                state, market, decision_day, rechecked, _items_for(p, rechecked), p["live"],
                live_executor or LIVE.subprocess_executor,
                entry_context=lambda e: _entry_context(e, state, frames, completed, reports_root, market, db_path),
                now=now_fn() if now_fn else None, emit=_emit_live, save_state=lambda: _atomic(p["state"], state),
                db_path=db_path)
        status = {}
        attempts = {"open": 0, "closed": 0}
        for watch in state["watches"]:
            status[watch["status"]] = status.get(watch["status"], 0) + 1
            for camp in ((watch.get("ledger") or {}).get("campaigns") or {}).values():
                for att in camp["attempts"]:
                    attempts["open" if att["status"] == "OPEN" else "closed"] += 1
        summary = {"mode": "LIVE" if live_enabled else "SHADOW",
                   "trading_impact": "orders" if live_enabled else "none", "phase": phase,
                   "policy_version": C.POLICY_VERSION, "live_results": _tally(r["status"] for r in live_results),
                   "completed_market_day": completed, "decision_day": decision_day, "enrol_rows": len(rows),
                   "newly_enrolled": enrolled, "symbols": len(tickers),
                   "collected": len([t for t in tickers if frames.get(t)]), "status_counts": status,
                   "attempt_counts": attempts, "intraday": counts, "new_triggers": len(frozen),
                   "trigger_events_emitted": sent, "ledger_events": len(ledger_events), "archived": archived,
                   "dry_run": dry_run, "rechecks": len(rechecked),
                   "llm_calls": sum(r["status"] in {"OK", "PARSE_ERROR", "ERROR"} for r in rechecked),
                   "recheck_status": _tally(r["status"] for r in rechecked)}
        if not dry_run:
            emit_event("reentry_v3.shadow_run", service=_service(market), market=market, attributes=summary,
                       event_time=datetime.now(timezone.utc))
        return summary


def _run_rechecks(state, market, completed, p, reports_root, archive_db, llm, only_day=None, limit=None,
                  stop_after_approvals=None):
    """One BUY recheck per live trigger, retried once after a failure on a later run (or phase).

    With LIVE on, the decision run passes only_day/limit/stop_after_approvals: today's triggers are
    rechecked in prism_core.reentry_v3_live.live_rank_key order, at most limit of them, and none after
    the approvals fill the remaining daily cap; the rest are marked SKIPPED_CAP (final, no LLM call).
    """
    todo = [(watch, event_id, rc) for watch in state["watches"]
            for event_id, rc in (watch.get("rechecks") or {}).items()
            if rc["status"] not in RC3.FINAL and rc["status"] != "SKIPPED_CAP"
            and rc["attempts"] < RC3.MAX_ATTEMPTS and rc.get("last") != completed]
    if not todo or not p["inputs"].exists():
        return []
    wanted = {event_id for _, event_id, _ in todo}
    items = {}
    with p["inputs"].open(encoding="utf-8") as handle:
        for line in handle:
            item = json.loads(line)
            if item.get("event_id") in wanted:
                items[item["event_id"]] = item
    if only_day is not None:
        todo = sorted((t for t in todo if (items.get(t[1]) or {}).get("trigger_date") == only_day),
                      key=lambda t: LIVE.live_rank_key(items[t[1]]))
    system, results, approvals = None, [], 0
    for position, (watch, event_id, rc) in enumerate(todo):
        item = items.get(event_id)
        if item is None:
            continue
        if limit is not None and (position >= limit or (stop_after_approvals is not None
                                                        and approvals >= stop_after_approvals)):
            rc.update(status="SKIPPED_CAP", last=completed)
            _atomic(p["state"], state)
            continue
        if system is None and item.get("report_ref"):
            system = RC3.instruction(market)
        record = RC3.recheck(item, reports_root=reports_root, archive_db=archive_db, llm=llm, system=system or "")
        attempts = rc["attempts"] + (record["status"] != "NO_REPORT")
        record.update(attempt=attempts, trigger=item["trigger"], decision_price=item["decision_price"],
                      decision_time=item["decision_time"], level=item["level"]["L"],
                      add_plan=(record.get("scenario") or {}).get("add_plan"))
        with p["results"].open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
        rc.update(status=record["status"], attempts=attempts, last=completed, approved=record.get("approved"))
        approvals += int(LIVE.approved(record))
        _atomic(p["state"], state)
        attrs = {"mode": "SHADOW", "trading_impact": "none", "policy_version": C.POLICY_VERSION,
                 "watch_ref": watch["watch_id"], "trigger_date": item["trigger_date"], "trigger": item["trigger"],
                 "status": record["status"], "approved": record.get("approved"), "decision": record.get("decision"),
                 "buy_score": record.get("buy_score"), "report_stale": record.get("report_stale"),
                 "model": record.get("model"), "reasoning_effort": record.get("reasoning_effort"),
                 "latency_s": record.get("latency_s"), "attempt": attempts,
                 "micro_split_appendix": bool(item.get("appendix_text"))}
        emit_event("reentry_v3.shadow_recheck", service=_service(market),
                   event_id=_hash(event_id, "recheck", attempts), market=market, ticker=watch["ticker"],
                   attributes=attrs, event_time=datetime.now(timezone.utc))
        results.append(record)
    return results
