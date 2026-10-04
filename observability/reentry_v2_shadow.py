"""Re-entry v2 SHADOW (pivot breakout). No orders and no DB writes.

After the close, for stop exits, near-miss skips and gate-blocked entries
(observability.reentry_shadow.candidates with include_blocked=True):
  * pivot/base and breakout triggers from prism_core.pivot_reentry, market gate by the
    production MarketPulse state machine over the ticker's benchmark;
  * a hypothetical trade under the production exit rules (simulate_production) plus the
    ORIGINAL and READY_OPEN controls;
  * on each new trigger, the full LLM recheck input is FROZEN (report reference + hash,
    trigger-time technical/market facts, fresh price levels, original reason) into an
    append-only JSONL;
  * forward triggers (session after the shadow started) get one BUY-agent recheck on those
    frozen inputs (observability/reentry_v2_recheck.py), results in a second JSONL.

Design and evidence: docs/REENTRY_V2_DESIGN_20260927_ko.md (sections 11-15).
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from observability.events import emit_event
from observability.reentry_recheck_inputs import archived_report, latest_report, technical_block
from observability import reentry_v2_recheck as RC
from observability.reentry_shadow import _atomic, candidates, scenario_key_levels
from prism_core import pivot_reentry as P

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "trading/config/reentry_v2_shadow.json"
POLICY = {"mode": "SHADOW", "policy_version": P.POLICY_VERSION, "markets": ["KR", "US"], "enabled": True,
          "llm_recheck": True}
STATE_DIR = ROOT / "runtime"
DB_PATH = ROOT / "stock_tracking_db.sqlite"
ARCHIVE_DB = ROOT / "archive.db"
LOOKBACK_DAYS = 70          # enrolment window; a watch lives up to 30 sessions (~45 calendar days)
ARCHIVE_AFTER_DAYS = 150    # finished watches (incl. 60-bar exit horizon) move to the archive
INPUT_CONTRACT = "reentry_v2_recheck_input_v4"
REPORT_MAX_AGE_DAYS = 30     # older reports are flagged stale, not regenerated (user decision 2026-09-27)


def enabled(market):
    try:
        policy = json.loads(POLICY_PATH.read_text())
        return (policy.get("mode") == "SHADOW" and policy.get("enabled") is True
                and policy.get("policy_version") == P.POLICY_VERSION and market in policy.get("markets", [])
                and os.getenv("REENTRY_V2_SHADOW_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"})
    except (OSError, ValueError):
        return False


def llm_recheck_enabled():
    """Opt-in: the BUY-agent recheck spends LLM quota, so it runs only with REENTRY_V2_LLM_RECHECK=true."""
    try:
        policy = json.loads(POLICY_PATH.read_text())
    except (OSError, ValueError):
        return False
    return (policy.get("llm_recheck") is True
            and os.getenv("REENTRY_V2_LLM_RECHECK", "false").strip().lower() in {"1", "true", "yes", "on"})


def paths(market, root=STATE_DIR):
    stem = Path(root) / f"reentry_v2_state_{market.lower()}"
    return {"state": stem.with_suffix(".json"), "archive": Path(f"{stem}_archive.jsonl"),
            "inputs": Path(root) / f"reentry_v2_recheck_inputs_{market.lower()}.jsonl",
            "results": Path(root) / f"reentry_v2_recheck_results_{market.lower()}.jsonl"}


def _load(path, market):
    if not path.exists():
        return {"schema_version": 1, "policy_version": P.POLICY_VERSION, "market": market, "watches": []}
    if path.stat().st_size > 5_000_000:
        raise ValueError("oversized_state")
    state = json.loads(path.read_text())
    if state.get("schema_version") != 1 or state.get("policy_version") != P.POLICY_VERSION \
            or state.get("market") != market:
        raise ValueError("state_version")
    return state


def _watch_id(market, row):
    raw = json.dumps([P.POLICY_VERSION, market, row["source"], row["account_key"], row["ticker"],
                      row["entry_date"], row["exit_date"]], separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def pulse_gate(bench_rows):
    from cores.market_pulse import CORRECTION, DailyBar, MarketPulse
    pulse, states = MarketPulse(), {}
    for bar in bench_rows:
        states[bar["date"]] = pulse.feed(DailyBar(date=bar["date"], close=bar["close"],
                                                  volume=bar["volume"] if bar["volume"] > 1 else None))
    return lambda day: None if day not in states else states[day] != CORRECTION


def bull_fn(bench_rows):
    closes, flags = [], {}
    for bar in bench_rows:
        closes.append(bar["close"])
        flags[bar["date"]] = len(closes) >= 50 and closes[-1] > sum(closes[-50:]) / 50
    return lambda day: flags.get(day, True)


def kr_regime(bench_rows, day):
    """Production KR regime function on the benchmark up to the session before `day`."""
    try:
        from cores.data_prefetch import _compute_kr_regime
        prior = {b["date"]: {"Close": b["close"]} for b in bench_rows if b["date"] < day}
        return _compute_kr_regime(prior).get("market_regime")
    except Exception:  # noqa: BLE001 - optional context stays explicitly missing
        return None


def fresh_levels(entry, base):
    """Trigger-time levels that do not depend on a report written before the breakout."""
    pivot, low = base["pivot"], base["base_low"]
    # Breakout: measured move (base height over the pivot). Pullback bounce: the post-breakout high.
    target = base.get("target") or pivot + (pivot - low)
    stop = max(entry * (1 - P.PROD_STOP), low)
    return {"support_pivot": pivot, "support_base_low": low, "measured_move_target": round(target, 4),
            "stop_cap_7pct": round(entry * (1 - P.PROD_STOP), 4), "stop_used": round(stop, 4),
            "reward_pct": round((target / entry - 1) * 100, 2), "risk_pct": round((1 - stop / entry) * 100, 2),
            "rr": round((target - entry) / (entry - stop), 2) if entry > stop else None}


def levels_text(levels):
    return ("\n### 📏 트리거 시점 가격 수준 (결정론적 계산, 보고서 이후 새로 산출)\n"
            f"- 1차 지지: {levels['support_pivot']:,.2f} (돌파한 피벗 또는 눌림 지지선) / "
            f"2차 지지: {levels['support_base_low']:,.2f} (베이스 저점 또는 눌림 저점)\n"
            f"- 목표(돌파: 베이스 높이만큼 피벗 위, 눌림 반등: 돌파 후 고점): {levels['measured_move_target']:,.2f} "
            f"(진입 대비 {levels['reward_pct']:+.2f}%)\n"
            f"- 손절 후보: 7% 상한 {levels['stop_cap_7pct']:,.2f}, 적용 {levels['stop_used']:,.2f} "
            f"(진입 대비 -{levels['risk_pct']:.2f}%) → 손익비 {levels['rr']}\n"
            "- 보고서의 과거 저항·목표가가 이 가격 아래에 있으면 돌파로 무효화된 수준입니다.\n")


def report_reference(market, ticker, trigger_date, reports_root, archive_db):
    """Latest report before the trigger day by reference + hash (file first, else archive.db); None if absent.

    Shared with re-entry v3 (observability/reentry_v3_shadow.py).
    """
    report = latest_report(reports_root, market, ticker, trigger_date)
    if report is None and archive_db and Path(archive_db).exists():
        report = archived_report(archive_db, market, ticker, trigger_date)
        kind = "archive"
    else:
        kind = "file"
    if report is None:
        return None
    text = report.read_text(encoding="utf-8")
    stamp = re.search(r"_(\d{8})_", report.name).group(1)
    age = (date.fromisoformat(trigger_date) - date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:]))).days
    return {"kind": kind, "name": report.name, "sha256": hashlib.sha256(text.encode()).hexdigest(),
            "report_date": f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:]}", "age_days": age,
            "stale": age > REPORT_MAX_AGE_DAYS}


def freeze_inputs(market, watch, result, bars, bench_rows, reports_root, archive_db):
    """Everything the later LLM recheck needs, fixed at the trigger. Report text by reference+hash."""
    i, entry, day = result["index"], result["day"]["entry"], result["day"]
    declined = watch.get("declined") or []
    if day["trigger"] == "PULLBACK_BOUNCE":
        since = next((k for k, b in enumerate(bars) if b["date"] == declined[-1]["date"]), i - 1)
        base = {"pivot": day["support"], "base_low": min(b["low"] for b in bars[since + 1:i]),
                "target": max(b["high"] for b in bars[since:i])}
    else:
        # A follow-through entry has no base as of today (yesterday made the new high): use yesterday's.
        base = P.find_pivot(bars, i, market) or P.find_pivot(bars, i - 1, market) or {}
    if not base:
        base = {"pivot": day["pivot"], "base_low": min(b["low"] for b in bars[max(0, i - 30):i])}
    trigger_date = bars[i]["date"]
    report_ref = report_reference(market, watch["ticker"], trigger_date, reports_root, archive_db)
    levels = fresh_levels(entry, base)
    row = watch["row"]
    return {"contract": INPUT_CONTRACT, "policy_version": P.POLICY_VERSION, "market": market,
            "watch_ref": watch["watch_id"], "ticker": watch["ticker"], "source": watch["source"],
            "trigger_date": trigger_date, "trigger": result["day"]["trigger"], "entry": entry,
            "base": base, "levels": levels, "report_ref": report_ref,
            "deterministic_market_regime": kr_regime(bench_rows, trigger_date) if market == "KR" else None,
            "facts_text": technical_block(bars, i, entry, base["pivot"], market, bench_rows,
                                          trigger=day["trigger"]) + levels_text(levels),
            "declined": [{k: d.get(k) for k in ("date", "trigger", "entry", "support", "reason")} for d in declined],
            "original": {"decided_on": row["exit_date"], "reason": (row.get("skip_reason") or
                                                                  (f"stopped out ({row.get('realized_pct')}%)"
                                                                   if watch["source"] == "STOP_EXIT" else None)),
                         "buy_score": row.get("buy_score"), "min_score": row.get("min_score"),
                         "decision_id": row.get("decision_id"), "key_levels": row.get("key_levels")},
            "llm_recheck": "NOT_EVALUATED", "frozen_at": datetime.now(timezone.utc).isoformat()}


def _ended_on(watch):
    if watch["status"] in {"MISSING_FINAL"}:
        return watch.get("ended_on") or watch["row"]["exit_date"]
    return watch.get("watch_ended")


def _blocked(watches, row):
    for other in watches.values():
        if (other["source"], other["ticker"]) != (row["source"], row["ticker"]) or \
                other["row"]["exit_date"] > row["exit_date"]:
            continue
        ended = _ended_on(other)
        if ended is None or ended >= row["exit_date"]:
            return True
    return False


def _closed(trade):
    return (trade or {}).get("status") in {"CLOSED", "MISSING"}


def _refresh(watch, frames, completed, market, reports_root, archive_db):
    bars = [b for b in (frames.get(watch["ticker"]) or []) if b["date"] <= completed]
    bench = [b for b in ((frames.get("__benchmark_rows") or {}).get(watch["ticker"]) or []) if b["date"] <= completed]
    if not bars:
        watch["last_missing"] = completed
        return None
    row = watch["row"]
    if watch["status"] == "PENDING_ENROLL":
        anchor = next((b for b in bars if b["date"] == row["entry_date"]), None)
        if anchor is None or not (anchor["low"] * 0.97 <= float(row["entry_price"]) <= anchor["high"] * 1.03):
            watch.update(status="MISSING_FINAL", reason="price_basis_mismatch")
            return None
        watch["status"] = "WATCHING"
    start = next((k for k, b in enumerate(bars) if b["date"] > row["exit_date"]), None)
    if start is None:
        return None
    gate = pulse_gate(bench) if bench else None
    bull = bull_fn(bench) if bench else None

    def exit_fn(b, i, entry, intraday=True):
        return P.simulate_production(b, i, entry, intraday=intraday, bull=bull)

    result = P.run_watch(bars, start, market, market_ok=gate, exit_fn=exit_fn, rejections=watch.get("declined"))
    watch["asof"] = completed
    watch["events_seen"] = result["events"]
    frozen = None
    if result["status"] == "TRIGGERED":
        trigger_date = bars[result["index"]]["date"]
        watch.update(status="TRIGGERED", trigger_date=trigger_date, trigger=result["day"]["trigger"],
                     entry=result["day"]["entry"], trade=result["trade"], watch_ended=trigger_date,
                     market_ok=result.get("market_ok"))
        event_id = hashlib.sha256(f"{watch['watch_id']}|{trigger_date}".encode()).hexdigest()[:32]
        if watch.get("trigger_event_id") != event_id:
            frozen = freeze_inputs(market, watch, result, bars, bench, reports_root, archive_db)
            frozen["event_id"] = event_id
            watch["trigger_event_id"] = event_id
            watch.pop("recheck", None)
    else:
        watch["status"] = result["status"]          # WATCHING/PENDING, EXPIRED or INVALIDATED
        if result["status"] in {"EXPIRED", "INVALIDATED"}:
            watch["watch_ended"] = bars[result["index"]]["date"]
    controls = {}
    if watch["source"] != "STOP_EXIT":
        controls["ORIGINAL"] = exit_fn(bars, start, bars[start]["open"], intraday=False)
    if result.get("ready_control"):
        k = result["ready_control"]["index"]
        controls["READY_OPEN"] = exit_fn(bars, k, bars[k]["open"], intraday=False)
    if watch.get("declined"):
        # Mechanical entry at the first (declined) trigger, to compare with the rechecked path.
        first = watch["declined"][0]
        k = next((n for n, b in enumerate(bars) if b["date"] == first["date"]), None)
        if k is not None:
            controls["FIRST_TRIGGER"] = exit_fn(bars, k, first["entry"], intraday=first["trigger"] != "FOLLOW_THROUGH")
    watch["controls"] = controls
    finished = watch["status"] in {"EXPIRED", "INVALIDATED"} or \
        (watch["status"] == "TRIGGERED" and _closed(watch.get("trade")))
    if finished and all(_closed(c) for c in controls.values()):
        watch["status_final"], watch["status"] = watch["status"], "CLOSED"
    return frozen


def advance(state, rows, frames, completed, market, reports_root=ROOT, archive_db=ARCHIVE_DB):
    watches = {w["watch_id"]: w for w in state["watches"]}
    started = state.setdefault("started_session", completed)
    frozen = []
    live = {"CLOSED", "MISSING_FINAL"}
    # Watches enrolled before key_levels were captured pick them up from today's read of the same row.
    fresh = {_watch_id(market, row): row for row in rows}
    for wid, watch in watches.items():
        if "key_levels" not in watch["row"] and wid in fresh:
            watch["row"]["key_levels"] = fresh[wid].get("key_levels")
    for watch in list(watches.values()):
        if watch["status"] not in live:
            item = _refresh(watch, frames, completed, market, reports_root, archive_db)
            if item:
                frozen.append((watch, item))
    for row in sorted(rows, key=lambda r: (r["exit_date"], r["entry_date"], r["ticker"])):
        wid = _watch_id(market, row)
        if wid in watches or _blocked(watches, row):
            continue
        watches[wid] = {"watch_id": wid, "status": "PENDING_ENROLL", "row": row, "market": market,
                        "source": row["source"], "ticker": row["ticker"], "enrolled_at": completed,
                        "enrollment": "PROSPECTIVE" if row["exit_date"] >= started else "LATE"}
        item = _refresh(watches[wid], frames, completed, market, reports_root, archive_db)
        if item:
            frozen.append((watches[wid], item))
    state["watches"] = list(watches.values())
    return state, frozen


def archive_finished(state, archive_path, completed):
    cutoff = (date.fromisoformat(completed) - timedelta(days=ARCHIVE_AFTER_DAYS)).isoformat()
    done = [w for w in state["watches"] if w["status"] in {"CLOSED", "MISSING_FINAL"}
            and w["row"]["exit_date"] < cutoff]
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


def run(market, completed, *, collector, db_path=DB_PATH, root=STATE_DIR, reports_root=ROOT,
        archive_db=ARCHIVE_DB, dry_run=False, llm_recheck=None, llm=None):
    p = paths(market, root)
    p["state"].parent.mkdir(parents=True, exist_ok=True)
    with p["state"].with_suffix(".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"mode": "SHADOW", "trading_impact": "none", "skipped": "lock_held", "market": market}
        state = _load(p["state"], market)
        state.setdefault("started_session", completed)
        rows = candidates(db_path, market, completed, lookback_days=LOOKBACK_DAYS, include_blocked=True)
        tickers = list(dict.fromkeys([w["ticker"] for w in state["watches"]
                                      if w["status"] not in {"CLOSED", "MISSING_FINAL"}] + [r["ticker"] for r in rows]))
        frames = collector(tickers, completed) if tickers else {}
        state, frozen = advance(state, rows, frames, completed, market, reports_root, archive_db)
        state["last_completed"] = completed
        archived = 0
        sent = 0
        if not dry_run:
            # Freeze before the state records the event id as delivered (append-only, may repeat).
            with p["inputs"].open("a", encoding="utf-8") as handle:
                for _, item in frozen:
                    handle.write(json.dumps(item, ensure_ascii=False, allow_nan=False) + "\n")
            archived = archive_finished(state, p["archive"], completed)
            _atomic(p["state"], state)
            now = datetime.now(timezone.utc)
            for watch, item in frozen:
                attrs = {"mode": "SHADOW", "trading_impact": "none", "llm_recheck": "NOT_EVALUATED",
                         "policy_version": P.POLICY_VERSION, "source": watch["source"],
                         "enrollment": watch["enrollment"], "watch_ref": watch["watch_id"],
                         "trigger": item["trigger"], "trigger_date": item["trigger_date"], "entry": item["entry"],
                         "levels": item["levels"], "report_available": item["report_ref"] is not None,
                         "report_stale": (item["report_ref"] or {}).get("stale"),
                         "market_regime_label": item["deterministic_market_regime"]}
                if emit_event("reentry_v2.shadow_trigger", service=f"prism-{market.lower()}-reentry-v2-shadow",
                              event_id=item["event_id"], market=market, ticker=watch["ticker"],
                              attributes=attrs, event_time=now) is not None:
                    sent += 1
        rechecked = []
        if not dry_run and (llm_recheck if llm_recheck is not None else llm_recheck_enabled()):
            rechecked = _run_rechecks(state, market, completed, p, reports_root, archive_db, llm)
        counts = {}
        for watch in state["watches"]:
            key = watch.get("status_final", watch["status"]) if watch["status"] == "CLOSED" else watch["status"]
            counts[key] = counts.get(key, 0) + 1
        llm_calls = sum(r["status"] in {"OK", "PARSE_ERROR", "ERROR"} for r in rechecked)
        summary = {"mode": "SHADOW", "trading_impact": "none", "llm_calls": llm_calls,
                   "policy_version": P.POLICY_VERSION,
                   "completed_market_day": completed, "enrol_rows": len(rows), "symbols": len(tickers),
                   "collected": len([t for t in tickers if frames.get(t)]), "new_triggers": len(frozen),
                   "trigger_events_emitted": sent, "archived": archived, "status_counts": counts, "dry_run": dry_run,
                   "rechecks": len(rechecked), "recheck_status": _tally(r["status"] for r in rechecked)}
        if not dry_run:
            emit_event("reentry_v2.shadow_run", service=f"prism-{market.lower()}-reentry-v2-shadow", market=market,
                       attributes=summary, event_time=datetime.now(timezone.utc))
        return summary


def _tally(values):
    out = {}
    for value in values:
        out[value] = out.get(value, 0) + 1
    return out


def _run_rechecks(state, market, completed, p, reports_root, archive_db, llm):
    """One BUY recheck per forward trigger; the result is appended before the state marks it done."""
    todo = [w for w in state["watches"] if RC.eligible(w, state.get("started_session"))
            and (w.get("recheck") or {}).get("last") != completed]
    if not todo or not p["inputs"].exists():
        return []
    wanted = {w["trigger_event_id"] for w in todo}
    items = {}
    with p["inputs"].open(encoding="utf-8") as handle:
        for line in handle:
            item = json.loads(line)
            if item.get("event_id") in wanted:
                items[item["event_id"]] = item
    instruction = None
    results = []
    for watch in todo:
        item = items.get(watch["trigger_event_id"])
        if item is None:
            continue
        if instruction is None and item.get("report_ref"):
            instruction = RC.recheck_instruction(market)
        record = RC.recheck(item, reports_root=reports_root, archive_db=archive_db, llm=llm, instruction=instruction)
        attempts = (watch.get("recheck") or {}).get("attempts", 0) + (record["status"] not in {"NO_REPORT"})
        record["attempt"] = attempts
        with p["results"].open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
        watch["recheck"] = {"status": record["status"], "attempts": attempts, "last": completed,
                            "approved": record.get("approved")}
        if record["status"] == "OK" and not record.get("approved"):
            _decline(watch, item, record)
        _atomic(p["state"], state)
        attrs = {"mode": "SHADOW", "trading_impact": "none", "policy_version": P.POLICY_VERSION,
                 "watch_ref": watch["watch_id"], "trigger_date": item["trigger_date"], "status": record["status"],
                 "approved": record.get("approved"), "decision": record.get("decision"),
                 "buy_score": record.get("buy_score"), "report_stale": record.get("report_stale"),
                 "model": record.get("model"), "reasoning_effort": record.get("reasoning_effort"),
                 "latency_s": record.get("latency_s"), "attempt": attempts, "trigger": item["trigger"],
                 "declined_before": len(item.get("declined") or [])}
        emit_event("reentry_v2.shadow_recheck", service=f"prism-{market.lower()}-reentry-v2-shadow",
                   event_id=hashlib.sha256(f"{item['event_id']}|recheck|{attempts}".encode()).hexdigest()[:32],
                   market=market, ticker=watch["ticker"], attributes=attrs, event_time=datetime.now(timezone.utc))
        results.append(record)
    return results


def _decline(watch, item, record):
    """The recheck said no: keep watching for a pullback bounce or re-breakout (run_watch rejections)."""
    support = (scenario_key_levels(record.get("scenario")) or {}).get("primary_support")
    watch.setdefault("declined", []).append({
        "date": item["trigger_date"], "trigger": item["trigger"], "entry": item["entry"], "support": support,
        "event_id": item["event_id"], "reason": str(record.get("rejection_reason") or "")[:300]})
    for key in ("trigger_date", "trigger", "entry", "trade", "watch_ended", "market_ok", "trigger_event_id"):
        watch.pop(key, None)
    watch["status"] = "WATCHING"
