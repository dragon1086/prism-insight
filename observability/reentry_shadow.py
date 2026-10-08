"""After-close re-entry SHADOW. Reads the trade DB read-only; no orders, holdings writes or LLMs.

Watches two pre-registered sources on completed daily bars (policy
``stopout_reentry_v1`` in ``prism_core/stopout_reentry.py``):

* STOP_EXIT      - positions closed with exit_kind='stop'.
* LOCATION_SKIP  - analysed but not entered, fundamentals passed and the
                   score within two points of the regime minimum ("everything
                   fine except the entry location").

Each technical event records a benchmark re-check (close above its MA50) and a
hypothetical next-open trade. Controls are stored beside it: holding the
stopped position without the stop, and buying the skipped candidate at the
next open. Every failure is explicit MISSING and never affects trading.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import sqlite3
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from observability.events import emit_event
from prism_core import stopout_reentry as R

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "trading/config/reentry_shadow.json"
STATE_DIR = ROOT / "runtime"
DB_PATH = ROOT / "stock_tracking_db.sqlite"
POLICY = {"mode": "SHADOW", "policy_version": R.POLICY_VERSION, "markets": ["KR", "US"], "enabled": True}
LOOKBACK_DAYS = 45          # calendar days of stop exits / analyses to enrol
MAX_ACTIVE = 80             # per market, oldest dropped as MISSING(capacity)
ARCHIVE_AFTER_DAYS = 60     # finished watches beyond the enrolment lookback move to the archive
SCORE_GAP = 2               # LOCATION_SKIP: min_score - buy_score <= 2
TABLES = {"KR": ("trading_history", "watchlist_history"), "US": ("us_trading_history", "us_watchlist_history")}
_STOP_COLUMNS = ("SELECT account_key, ticker, company_name, buy_date, buy_price, sell_date, sell_price, "
                 "profit_rate, trigger_type, exit_kind, scenario FROM {} WHERE exit_kind = 'stop' "
                 "AND substr(sell_date, 1, 10) >= ? AND substr(sell_date, 1, 10) <= ?")
_SKIP_COLUMNS = ("SELECT id, ticker, company_name, analyzed_date, current_price, buy_score, min_score, decision, "
                 "skip_reason, trigger_type, scenario FROM {} WHERE substr(analyzed_date, 1, 10) >= ? "
                 "AND substr(analyzed_date, 1, 10) <= ? AND COALESCE(was_traded, 0) = 0")
# Complete statements fixed at import time from the constant table map; no runtime interpolation.
STOP_SQL = {m: _STOP_COLUMNS.format(t[0]) for m, t in TABLES.items()}
SKIP_SQL = {m: _SKIP_COLUMNS.format(t[1]) for m, t in TABLES.items()}
SKIP_CATEGORIES = (
    ("trend_gate", ("T1", "T2", "이동평균", "MA20", "MA50", "MA60", "MA200", "추세", "trend")),
    ("risk_reward", ("R/R", "손익비", "loss width", "expected_loss", "손절", "stop")),
    ("extension", ("과열", "이격", "RSI", "급등", "extended", "overheat")),
    ("sector_or_slots", ("Sector concentration", "섹터 집중", "slots", "슬롯")),
    ("score", ("점수 부족", "Insufficient score", "effective score")),
)


def enabled(market):
    try:
        policy = json.loads(POLICY_PATH.read_text())
        return (policy.get("mode") == "SHADOW" and policy.get("enabled") is True
                and policy.get("policy_version") == R.POLICY_VERSION and market in policy.get("markets", [])
                and os.getenv("REENTRY_SHADOW_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"})
    except (OSError, ValueError):
        return False


def _emit(event, market, **kwargs):
    return emit_event(event, service="prism-" + market.lower() + "-reentry-shadow", market=market, **kwargs)


def state_path(market):
    return STATE_DIR / f"reentry_shadow_state_{market.lower()}_v1.json"


def archive_path(path):
    return Path(path).with_name(Path(path).stem + "_archive.jsonl")


def archive_finished(state, path, completed):
    """Append finished watches past the lookback to a JSONL archive, then drop them.

    They can no longer be re-enrolled (older than LOOKBACK_DAYS), and the evidence
    packet reads the archive, so the state file stays small without losing evidence.
    """
    cutoff = (date.fromisoformat(completed) - timedelta(days=ARCHIVE_AFTER_DAYS)).isoformat()
    done = [w for w in state["watches"] if w["status"] in {"CLOSED", "MISSING_FINAL"}
            and w.get("row", {}).get("exit_date", "") < cutoff]
    if not done:
        return 0
    with archive_path(path).open("a", encoding="utf-8") as handle:
        for watch in done:
            handle.write(json.dumps(watch, ensure_ascii=False, allow_nan=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    ids = {w["watch_id"] for w in done}
    state["watches"] = [w for w in state["watches"] if w["watch_id"] not in ids]
    return len(done)


def _load(path, market):
    if not path.exists():
        return {"schema_version": 1, "policy_version": R.POLICY_VERSION, "market": market, "watches": []}
    if path.stat().st_size > 5_000_000:
        raise ValueError("oversized_state")
    state = json.loads(path.read_text())
    if (state.get("schema_version") != 1 or state.get("policy_version") != R.POLICY_VERSION
            or state.get("market") != market):
        raise ValueError("state_version")
    return state


def _atomic(path, state):
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, prefix=".reentry-", delete=False) as file:
            name = file.name
            json.dump(state, file, allow_nan=False, ensure_ascii=False)
            file.flush()
            os.fsync(file.fileno())
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def skip_category(text):
    text = text or ""
    return [name for name, keys in SKIP_CATEGORIES if any(k in text for k in keys)] or ["other"]


def session_date(stamp, market):
    """DB timestamps are server-local KST; a US row belongs to its New York session date."""
    text = str(stamp)
    if market != "US" or len(text) < 16:
        return text[:10]
    local = datetime.fromisoformat(text[:19].replace("T", " ")).replace(tzinfo=ZoneInfo("Asia/Seoul"))
    return local.astimezone(ZoneInfo("America/New_York")).date().isoformat()


def _scenario(raw):
    try:
        value = json.loads(raw or "{}")
        return value if isinstance(value, dict) else {}
    except ValueError:
        return {}


def _int(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


KEY_LEVELS = ("primary_support", "secondary_support", "primary_resistance", "secondary_resistance")


def _level_price(value):
    """key_levels price: 1700 / "1,700" / "1700~1800" (range midpoint), else None."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) if value > 0 else None
    if not isinstance(value, str):
        return None
    try:
        nums = [float(part.strip().replace(",", "")) for part in value.split("~") if part.strip()]
    except ValueError:
        return None
    return round(sum(nums) / len(nums), 4) if nums and min(nums) > 0 else None


def scenario_key_levels(scenario):
    """The BUY scenario's support/resistance levels as numbers; None when it has none."""
    levels = ((scenario or {}).get("trading_scenarios") or {}).get("key_levels") or {}
    out = {name: _level_price(levels.get(name)) for name in KEY_LEVELS}
    return out if any(v is not None for v in out.values()) else None


def candidates(db_path, market, completed, lookback_days=LOOKBACK_DAYS, include_blocked=False):
    """Read-only enrolment rows; the DB is never written.

    include_blocked adds ENTER_BLOCKED: the BUY agent chose entry but a deterministic gate
    stopped it (used by re-entry v2; v1 keeps its original sources).
    """
    since = (date.fromisoformat(completed) - timedelta(days=lookback_days)).isoformat()
    until = (date.fromisoformat(completed) + timedelta(days=1)).isoformat()  # KST stamps of US rows run ahead
    uri = "file:" + str(db_path) + "?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        out = []
        for row in conn.execute(STOP_SQL[market], (since, until)):
            # Account identifiers never leave the DB; only a stable one-way reference.
            account_ref = "acct-" + hashlib.sha256(str(row["account_key"]).encode()).hexdigest()[:12]
            out.append({"source": "STOP_EXIT", "account_key": account_ref, "ticker": str(row["ticker"]),
                        "company_name": row["company_name"], "entry_date": session_date(row["buy_date"], market),
                        "entry_price": row["buy_price"], "exit_date": session_date(row["sell_date"], market),
                        "exit_price": row["sell_price"], "trigger_type": row["trigger_type"],
                        "exit_kind": row["exit_kind"], "realized_pct": row["profit_rate"],
                        "decision_id": _scenario(row["scenario"]).get("_decision_id"),
                        "key_levels": scenario_key_levels(_scenario(row["scenario"]))})
        for row in conn.execute(SKIP_SQL[market], (since, until)):
            scenario = _scenario(row["scenario"])
            fundamentals = (scenario.get("fundamental_check") or {}).get("all_passed")
            score, minimum = _int(row["buy_score"]), _int(row["min_score"])
            blocked = include_blocked and fundamentals is not False and \
                str(row["decision"] or "").strip().lower() in {"enter", "진입", "entry"}
            if not blocked and (fundamentals is not True or score is None or minimum is None
                                or minimum - score > SCORE_GAP):
                continue
            price = row["current_price"]
            if not price:
                continue
            out.append({"source": "ENTER_BLOCKED" if blocked else "LOCATION_SKIP", "account_key": "analysis",
                        "ticker": str(row["ticker"]),
                        "company_name": row["company_name"], "entry_date": session_date(row["analyzed_date"], market),
                        "entry_price": price, "exit_date": session_date(row["analyzed_date"], market),
                        "exit_price": price,
                        "trigger_type": row["trigger_type"], "exit_kind": None, "analysis_id": row["id"],
                        "decision": row["decision"], "buy_score": score, "min_score": minimum,
                        "skip_categories": skip_category(row["skip_reason"]),
                        "decision_id": scenario.get("_decision_id"), "skip_reason": row["skip_reason"],
                        "key_levels": scenario_key_levels(scenario)})
        # Only rows whose session is complete; a forming session is enrolled next run.
        return [r for r in out if r["exit_date"] <= completed]


def _bench_ok(bench, day):
    closes = [b["close"] for b in bench if b["date"] <= day]
    if len(closes) < 55:
        return None
    ma50, ma50_prev = sum(closes[-50:]) / 50, sum(closes[-55:-5]) / 50
    return {"close_above_ma50": closes[-1] > ma50, "ma50_rising": ma50 > ma50_prev,
            "ok": closes[-1] > ma50}


def _basis_ok(bars, entry_date, price):
    day = next((b for b in bars if b["date"] == entry_date), None)
    return day is not None and day["low"] * 0.97 <= float(price) <= day["high"] * 1.03


def _ended_on(watch):
    """Last date the watch was live; None while it is (or may still be) live."""
    status = watch.get("status_final") or watch["status"]
    if watch["status"] == "MISSING_FINAL":
        return watch.get("ended_on") or watch["row"]["exit_date"]
    if status in R.TERMINAL:
        return watch.get("asof")
    return None


def _blocked(watches, row):
    """Point-in-time: skip a row only if an earlier watch of the same ticker/source was live on its date."""
    for other in watches.values():
        if (other["source"], other["ticker"]) != (row["source"], row["ticker"]) or other["row"]["exit_date"] > row["exit_date"]:
            continue
        ended = _ended_on(other)
        if ended is None or ended >= row["exit_date"]:
            return True
    return False


def _refresh(watch, frames, completed, market):
    """Enrol (freeze levels) if needed and re-evaluate on completed bars; return new events."""
    bars = frames.get(watch["ticker"])
    if not bars:
        watch["last_missing"] = completed
        return []
    bars = [b for b in bars if b["date"] <= completed]
    if watch["status"] == "PENDING_ENROLL":
        row = watch["row"]
        if not _basis_ok(bars, row["entry_date"], row["entry_price"]):
            watch.update(status="MISSING_FINAL", reason="price_basis_mismatch")
            return []
        frozen = R.enroll(market, row["account_key"], row["ticker"], row["entry_date"], row["entry_price"],
                          row["exit_date"], row["exit_price"], bars, trigger_type=row.get("trigger_type"),
                          exit_kind=row.get("exit_kind"), source=row["source"])
        watch.update(frozen=frozen, status=frozen["status"])
        if frozen["status"] == "MISSING":
            watch.update(status="MISSING_FINAL", reason=frozen.get("reason"))
            return []
    result = R.evaluate(watch["frozen"], bars)
    bench_rows = (frames.get("__benchmark_rows") or {}).get(watch["ticker"]) or []
    for event in result["events"]:
        event["market_check"] = _bench_ok(bench_rows, event["date"]) if bench_rows else None
    known = {e["event_id"] for e in watch.get("events", [])}
    fresh = [(watch, e) for e in result["events"] if e["event_id"] not in known]
    watch["events"] = result["events"]
    watch["status"], watch["reason"], watch["asof"] = result["status"], result.get("reason"), result.get("asof")
    if watch["source"] == "STOP_EXIT":
        watch["control"] = {"kind": "HOLD_WITHOUT_STOP", **R.hold_counterfactual(watch["frozen"], bars)}
    else:
        watch["control"] = {"kind": "IMMEDIATE_NEXT_OPEN", **R.simulate(bars, {
            "kind": "IMMEDIATE", "date": watch["frozen"]["entry_date"], "swing_low": 0, "reclaim_level": 0})}
    done = result["status"] in R.TERMINAL and all(
        e["trade"].get("status") in {"CLOSED", "SKIPPED", "MISSING"} for e in result["events"]) \
        and watch["control"].get("status") != "PENDING"
    if done:
        watch["status_final"] = watch["status"]
        watch["status"] = "CLOSED"
    return fresh


def advance(state, rows, frames, completed, market):
    """Re-evaluate watches, enrol new rows in date order, return (state, new_events).

    Enrolment is point-in-time: a row is skipped only when an earlier watch of the
    same ticker and source was still live on the row's date, so the outcome does not
    depend on how many runs happened. A watch without bars stays PENDING_ENROLL and
    conservatively blocks later rows until it can be evaluated.
    """
    watches = {w["watch_id"]: w for w in state["watches"]}
    started = state.setdefault("started_session", completed)
    fresh = []
    for watch in list(watches.values()):
        if watch["status"] not in {"CLOSED", "MISSING_FINAL"}:
            fresh.extend(_refresh(watch, frames, completed, market))
    for row in sorted(rows, key=lambda r: (r["exit_date"], r["entry_date"], r["ticker"])):
        wid = R.watch_id(market, row["account_key"], row["ticker"], row["entry_date"], row["exit_date"])
        if wid in watches or _blocked(watches, row):
            continue
        # Rows that finished before this SHADOW first ran are backfill, not forward evidence.
        watches[wid] = {"watch_id": wid, "status": "PENDING_ENROLL", "row": row, "market": market,
                        "source": row["source"], "ticker": row["ticker"], "enrolled_at": completed,
                        "enrollment": "PROSPECTIVE" if row["exit_date"] >= started else "LATE"}
        fresh.extend(_refresh(watches[wid], frames, completed, market))
    live = [w for w in watches.values() if w["status"] not in {"CLOSED", "MISSING_FINAL"}]
    live.sort(key=lambda w: w["row"]["exit_date"])
    for watch in live[:-MAX_ACTIVE] if len(live) > MAX_ACTIVE else []:
        watch.update(status="MISSING_FINAL", reason="capacity", ended_on=completed)
    state["watches"] = list(watches.values())
    return state, fresh


def symbols_needed(state, rows):
    tickers = [w["ticker"] for w in state["watches"] if w["status"] not in {"CLOSED", "MISSING_FINAL"}]
    return list(dict.fromkeys(tickers + [r["ticker"] for r in rows]))


def run(market, completed, *, collector, db_path=DB_PATH, path=None, dry_run=False):
    """One idempotent pass for the completed session. Returns a summary dict."""
    path = Path(path) if path else state_path(market)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"mode": "SHADOW", "trading_impact": "none", "skipped": "lock_held", "market": market}
        state = _load(path, market)
        state.setdefault("started_session", completed)
        rows = candidates(db_path, market, completed)
        tickers = symbols_needed(state, rows)
        frames = collector(tickers, completed) if tickers else {}
        state, fresh = advance(state, rows, frames, completed, market)
        state["last_completed"] = completed
        archived = 0
        if not dry_run:
            archived = archive_finished(state, path, completed)
            _atomic(path, state)
        now = datetime.now(timezone.utc)
        sent = 0
        for watch, event in fresh:
            payload = {"mode": "SHADOW", "trading_impact": "none", "eligibility": "NOT_EVALUATED",
                       "enrollment": watch.get("enrollment"),
                       "policy_version": R.POLICY_VERSION, "source": watch["source"],
                       "watch_ref": watch["watch_id"], "kind": event["kind"], "signal_date": event["date"],
                       "market_check": event.get("market_check"), "volume_ratio": event.get("volume_ratio"),
                       "reclaim_level": event.get("reclaim_level"), "trade": event.get("trade"),
                       "skip_categories": watch["row"].get("skip_categories"),
                       "decision_id": watch["row"].get("decision_id")}
            if not dry_run and _emit("reentry.shadow_signal", market, event_id=event["event_id"],
                                     ticker=watch["ticker"], attributes=payload, event_time=now) is not None:
                sent += 1
        counts = {}
        for watch in state["watches"]:
            counts[watch["status"]] = counts.get(watch["status"], 0) + 1
        summary = {"mode": "SHADOW", "trading_impact": "none", "policy_version": R.POLICY_VERSION,
                   # Event keys containing "session" are redacted by the sanitizer.
                   "completed_market_day": completed, "enrol_rows": len(rows), "symbols": len(tickers),
                   "collected": len([t for t in tickers if frames.get(t)]), "new_signals": len(fresh),
                   "signals_emitted": sent, "status_counts": counts, "archived": archived, "dry_run": dry_run}
        if not dry_run:
            _emit("reentry.shadow_run", market, attributes=summary, event_time=now)
        return summary
