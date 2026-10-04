"""Re-entry v3 LIVE: real buys for approved re-entry triggers through the normal tracker entry path.

User decision (2026-10-04): no SHADOW phase; re-entry v3 trades the real accounts and is
maintained while live. The deterministic ledger and the LLM recheck stay as they are
(observability/reentry_v3_shadow.py); when the recheck approves (decision = 진입) at the
decision run (KR 14:00 KST, US 13:50 ET) every deterministic check of the normal entry runs
again inside the tracker (``enter_reentry_candidate`` on the KR enhanced / US agents, which
reuse the batch's ``_enter_eligible_candidate``: micro-split B3 sizing + add_plan, final buy
gate, holdings row and scenario, order intents, Telegram buy message, Redis/GCP publish).

Safety (this module): kill switch REENTRY_V3_LIVE_ENABLED (default off) and
REENTRY_V3_LIVE_MARKETS (default KR,US); only the primary L97 rule's signals; at most DAILY_CAP
re-entry orders per market per session and one per ticker per session; regular hours only and
before the order deadline (KR 14:40 KST, US 14:25 ET); one idempotency key per watch and session
written (fsync) before the order and never retried the same day, whatever happened; the signal
price band is checked again on a fresh quote right before the order; any exception is logged,
emitted and contained, and an ERROR/timeout is reconciled with the holdings tables. The order runs
in an isolated subprocess (tools/run_reentry_v3_entry.py, same pattern as the micro-split LIVE adds)
under the market entry lock shared with the batch entry (prism_core/entry_lock.py).
After the buy the holding is managed by the existing sell logic like any other position.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
from datetime import datetime, timezone
from datetime import time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
LIVE_VERSION = "reentry_v3"
DAILY_CAP = 2
PRIMARY_RULE = "L97"
# Continuous regular session with a buffer: KR 09:00-15:20 (closing auction 15:20-15:30),
# US 09:30-16:00 ET (closing-auction order cutoffs from 15:50).
SAFE_WINDOWS = {"KR": ("Asia/Seoul", dtime(9, 5), dtime(15, 15)),
                "US": ("America/New_York", dtime(9, 35), dtime(15, 45))}
# No re-entry order after this local time (before the regular afternoon batches KR 14:46 / US 14:30 ET).
ORDER_DEADLINES = {"KR": ("Asia/Seoul", dtime(14, 40)), "US": ("America/New_York", dtime(14, 25))}
SIGNALS = {"R1C": "REBREAK", "R2S": "RETEST", "SHAKEOUT_RECLAIM": "SHAKEOUT_RECLAIM"}
SIGNAL_KO = {"REBREAK": "기준 가격 재돌파 매수", "RETEST": "기준 가격 눌림 지지 매수",
             "SHAKEOUT_RECLAIM": "흔들기 후 회복 매수"}
SIGNAL_WHY_KO = {
    "REBREAK": "{level} 위로 다시 올라섰습니다",
    "RETEST": "{level}까지 내려왔다가 지지를 받고 버텼습니다",
    "SHAKEOUT_RECLAIM": "{level} 아래로 크게 흔들린 뒤 거래량을 동반해 다시 회복했습니다",
}
SOURCE_SENTENCE_KO = {"STOP_EXIT": "이전에 손절했던 종목입니다.",
                      "LOCATION_SKIP": "이전 분석에서 매수를 보류했던 종목입니다.",
                      "ENTER_BLOCKED": "이전에 매수 조건에 막혔던 종목입니다."}
APPROVE = {"진입", "enter", "entry"}
SUBPROCESS_TIMEOUT = 600
# Status of an outcome reason (the rest is BOUGHT / NOT_BOUGHT / ERROR).
REASON_STATUS = {"deadline": "SKIPPED_DEADLINE", "no_micro_plan": "SKIPPED_NO_MICRO_PLAN",
                 "outside_band": "SKIPPED_BAND", "entry_lock_busy": "SKIPPED_LOCK"}


def _flag(name, default):
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def live_markets():
    return {m.strip().upper() for m in os.getenv("REENTRY_V3_LIVE_MARKETS", "KR,US").split(",") if m.strip()}


def live_enabled(market):
    """Kill switch: REENTRY_V3_LIVE_ENABLED=true (default false) and the market in REENTRY_V3_LIVE_MARKETS."""
    return _flag("REENTRY_V3_LIVE_ENABLED", "false") and str(market).upper() in live_markets()


def _local(market, now, table):
    zone = table[market][0]
    return (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(zone))


def in_safe_window(market, now=None):
    """True inside the market's continuous regular session (weekday, buffered, no auction)."""
    _, start, end = SAFE_WINDOWS[market]
    local = _local(market, now, SAFE_WINDOWS)
    return local.weekday() < 5 and start <= local.time() < end


def before_deadline(market, now=None):
    """True before the re-entry order deadline (KR 14:40 KST, US 14:25 ET)."""
    return _local(market, now, ORDER_DEADLINES).time() < ORDER_DEADLINES[market][1]


def price_in_band(signal, level, price):
    """The signal's price band on a fresh quote: REBREAK L<p<=1.03L, RETEST L<=p<=1.05L, SHAKEOUT L<p<=1.05L."""
    if not level or not price:
        return False
    if signal == "REBREAK":
        return level < price <= level * 1.03
    if signal == "RETEST":
        return level <= price <= level * 1.05
    if signal == "SHAKEOUT_RECLAIM":
        return level < price <= level * 1.05
    return False


def idempotency_key(watch_id, day):
    return hashlib.sha256(f"{LIVE_VERSION}|{watch_id}|{day}".encode()).hexdigest()[:32]


def trigger_label(market, signal):
    """holdings.trigger_type: re-entries stay separable in messages, the dashboard and win-rate stats."""
    return f"재진입({SIGNAL_KO[signal]})" if market == "KR" else f"Re-entry ({signal})"


def approved(record):
    return (record.get("status") == "OK" and record.get("approved") is True
            and str((record.get("scenario") or {}).get("decision", "")).strip().lower() in APPROVE)


def live_rank_key(item):
    """Deterministic order for the daily cap: earlier attempt, then higher deterministic R/R, then ticker."""
    attempt = (item.get("attempts") or {}).get(PRIMARY_RULE, {}).get("attempt", 9)
    rr = item.get("rr")
    return (attempt, -(rr if isinstance(rr, (int, float)) else -1.0), str(item.get("ticker")))


def _price_fields(scenario, price):
    target, stop = scenario.get("target_price"), scenario.get("stop_loss")
    scenario["entry_price"] = price
    scenario.pop("_analysis_entry_price", None)
    if isinstance(target, (int, float)) and isinstance(stop, (int, float)) and target > price > stop > 0:
        scenario["expected_return_pct"] = round((target / price - 1) * 100, 4)
        scenario["expected_loss_pct"] = round((1 - stop / price) * 100, 4)
        scenario["risk_reward_ratio"] = round((target - price) / (price - stop), 4)
    return scenario


def build_scenario(item, record, market):
    """The recheck's BUY JSON plus re-entry metadata, the capped re-entry stop and the BUY-rule target."""
    scenario = dict(record.get("scenario") or {})
    signal = SIGNALS[item["trigger"]]
    attempt = (item.get("attempts") or {}).get(PRIMARY_RULE, {}).get("attempt", 1)
    window = ((item.get("campaign") or {}).get("windows") or {}).get(PRIMARY_RULE) or {}
    scenario["reentry"] = {
        "version": LIVE_VERSION, "signal": signal, "signal_ko": SIGNAL_KO[signal], "attempt": attempt,
        "max_attempts": 3, "attempt_label": f"{attempt}/3", "level": item["level"]["L"],
        "level_basis": item["level"]["basis"], "watch_id": item["watch_ref"], "source": item["source"],
        "trigger_date": item["trigger_date"], "decision_price": item["decision_price"],
        "decision_time": item["decision_time"], "event_id": item["event_id"], "rule": PRIMARY_RULE,
        "stop_rule": item.get("stop_rule"), "target_rule": item.get("target_rule"),
        "band_level": window.get("R") or item["level"]["L"],
        "band_basis": (item.get("campaign") or {}).get("reclaim_basis") if window.get("R") else "L",
    }
    scenario["stop_loss"] = item["stop"]
    if item.get("target"):
        scenario["target_price"] = item["target"]
    _price_fields(scenario, float(item["decision_price"]))    # the final buy gate checks this arithmetic
    scenario["trigger_type"] = trigger_label(market, signal)
    scenario["_decision_id"] = f"reentry_v3:{item['event_id']}"
    return scenario


def apply_stop_cap(scenario, price, regime):
    """Tighten the stop to the tracker regime's maximum stop (never widen) and refresh the price fields.

    The ledger caps the stop with its own deterministic regime; the tracker's final gate checks the
    stop width with its regime. Taking the tighter of both keeps the stop inside both caps.
    """
    from prism_core.reentry_campaign import max_stop
    stop = scenario.get("stop_loss")
    if regime and isinstance(stop, (int, float)) and price:
        capped = price * (1 - max_stop(regime))
        if capped > stop:
            scenario["stop_loss"] = round(capped, 6)
            meta = scenario.get("reentry")
            if isinstance(meta, dict):
                meta["stop_capped_by_tracker_regime"] = regime
    return _price_fields(scenario, price)


SUPPORT_KO = {"primary_support": "1차 지지선", "secondary_support": "2차 지지선"}


def _money(value, market):
    return (f"{value:,.0f}원" if market == "KR" else f"${value:,.2f}") if isinstance(value, (int, float)) else "-"


def level_phrase(meta, market):
    """The price the message refers to: the shakeout reclaim level (band_level) for SHAKEOUT_RECLAIM,
    which is a support for held-off/blocked names that never closed above the level, else the level."""
    level, band = meta.get("level"), meta.get("band_level")
    if meta.get("signal") == "SHAKEOUT_RECLAIM" and isinstance(band, (int, float)) and band != level:
        name = SUPPORT_KO.get(meta.get("band_basis"), "지지선")
        return f"분석 당시 {name}({_money(band, market)})"
    text = _money(level, market)
    if meta.get("source") == "STOP_EXIT" and meta.get("level_basis") != "primary_resistance_fallback":
        return f"첫 매수 때 돌파했던 가격대({text})"
    return f"분석 당시 기준 가격(1차 저항, {text})"


SUPPORT_EN = {"primary_support": "first support", "secondary_support": "second support"}
SIGNAL_EN = {"REBREAK": "re-break of the reference price", "RETEST": "pullback holding the reference price",
             "SHAKEOUT_RECLAIM": "reclaim after a shakeout"}
SIGNAL_WHY_EN = {
    "REBREAK": "It moved back above {level}",
    "RETEST": "It pulled back to {level} and held there",
    "SHAKEOUT_RECLAIM": "It dropped sharply below {level}, then reclaimed it on rising volume",
}
SOURCE_SENTENCE_EN = {"STOP_EXIT": "We stopped out of this stock earlier.",
                      "LOCATION_SKIP": "An earlier analysis held off on buying this stock.",
                      "ENTER_BLOCKED": "An earlier buy was blocked by the entry checks."}


def level_phrase_en(meta, market):
    """English counterpart of level_phrase for US trade texts."""
    level, band = meta.get("level"), meta.get("band_level")
    if meta.get("signal") == "SHAKEOUT_RECLAIM" and isinstance(band, (int, float)) and band != level:
        name = SUPPORT_EN.get(meta.get("band_basis"), "support")
        return f"the {name} from the original analysis ({_money(band, market)})"
    text = _money(level, market)
    if meta.get("source") == "STOP_EXIT" and meta.get("level_basis") != "primary_resistance_fallback":
        return f"the level it broke out of at the first buy ({text})"
    return f"the reference price from the original analysis (first resistance, {text})"


def _live_meta(scenario):
    if isinstance(scenario, str):
        try:
            scenario = json.loads(scenario or "{}")
        except ValueError:
            return None
    meta = scenario.get("reentry") if isinstance(scenario, dict) else None
    return meta if isinstance(meta, dict) and meta.get("version") == LIVE_VERSION else None


def entry_message_line(scenario, market):
    """Plain-language re-entry line for the Telegram buy message ('' for every other entry).

    KR in Korean; US in English like the other US trade texts."""
    meta = _live_meta(scenario)
    if meta is None:
        return ""
    signal = meta.get("signal")
    if str(market).upper() == "US":
        why = SIGNAL_WHY_EN.get(signal, "").format(level=level_phrase_en(meta, market))
        source = SOURCE_SENTENCE_EN.get(meta.get("source"), "")
        return (f"🔁 Re-entry Buy ({SIGNAL_EN.get(signal, signal)}, attempt {meta.get('attempt_label')})\n"
                f"{source} {why}.\n")
    why = SIGNAL_WHY_KO.get(signal, "").format(level=level_phrase(meta, market))
    source = SOURCE_SENTENCE_KO.get(meta.get("source"), "")
    return (f"🔁 재진입 매수 ({SIGNAL_KO.get(signal, signal)}, {meta.get('attempt_label')}번째 시도)\n"
            f"{source} {why}.\n")


def holding_tag(scenario, market, *, indent="", language=None):
    """One-line re-entry tag for the portfolio summary and the sell message ('' for other holdings).

    ``language`` ("ko"/"en") defaults by market (the US portfolio summary is Korean)."""
    meta = _live_meta(scenario)
    if meta is None:
        return ""
    signal = meta.get("signal")
    if (language or ("en" if str(market).upper() == "US" else "ko")) == "en":
        return f"{indent}🔁 Re-entry position ({SIGNAL_EN.get(signal, signal)}, attempt {meta.get('attempt_label')})\n"
    return f"{indent}🔁 재진입 종목 ({SIGNAL_KO.get(signal, signal)}, {meta.get('attempt_label')}번째 시도)\n"


# ---------------------------------------------------------------- journal (idempotency, daily cap)
def load_journal(path):
    path = Path(path)
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def append_journal(path, record):
    with Path(path).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, allow_nan=False, default=str) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def cap_remaining(journal, decision_day):
    used = len({r["key"] for r in journal if r.get("phase") == "submit" and r.get("day") == decision_day})
    return max(0, DAILY_CAP - used)


def plan(market, decision_day, records, items, journal, now):
    """(to_submit, skipped): approved primary-rule recheck records of this session that may be ordered now,
    in live_rank_key order."""
    submits = [r for r in journal if r.get("phase") == "submit"]
    submitted = {r["key"] for r in submits}
    tickers_today = {r.get("ticker") for r in submits if r.get("day") == decision_day}
    remaining = cap_remaining(journal, decision_day)
    candidates = []
    for record in records:
        item = items.get(record.get("event_id"))
        if item is None or item.get("trigger_date") != decision_day or not approved(record):
            continue
        if item.get("live_rule") != PRIMARY_RULE:
            continue                     # SS-rule signals are a parallel record only
        candidates.append((live_rank_key(item), item, record))
    to_submit, skipped = [], []
    for _, item, record in sorted(candidates, key=lambda c: c[0]):
        key = idempotency_key(item["watch_ref"], decision_day)
        reason = None
        if key in submitted or key in {e["key"] for e in to_submit}:
            reason = "duplicate"
        elif item["ticker"] in tickers_today or item["ticker"] in {e["ticker"] for e in to_submit}:
            reason = "ticker_already_today"
        elif len(to_submit) >= remaining:
            reason = "daily_cap"
        elif not in_safe_window(market, now):
            reason = "outside_regular_hours"
        elif not before_deadline(market, now):
            reason = "deadline"
        entry = {"key": key, "day": decision_day, "market": market, "watch_id": item["watch_ref"],
                 "event_id": item["event_id"], "ticker": item["ticker"], "signal": SIGNALS[item["trigger"]],
                 "price": item["decision_price"], "item": item, "record": record}
        (skipped if reason else to_submit).append(dict(entry, reason=reason) if reason else entry)
    return to_submit, skipped


def outcome_status(outcome):
    if outcome.get("bought"):
        return "BOUGHT_RECONCILED" if outcome.get("reconciled") else "BOUGHT"
    reason = str(outcome.get("reason", ""))
    for prefix, status in REASON_STATUS.items():
        if reason.startswith(prefix):
            return status
    return "ERROR" if reason.startswith(("error", "executor_error", "no_result", "timeout")) else "NOT_BOUGHT"


def process(state, market, decision_day, records, items, journal_path, executor, *, entry_context, now=None,
            emit=None, save_state=None, db_path=None):
    """Plan, journal (before the order), execute and record the LIVE re-entry orders of this run."""
    now = now or datetime.now(timezone.utc)
    journal = load_journal(journal_path)
    to_submit, skipped = plan(market, decision_day, records, items, journal, now)
    watches = {w["watch_id"]: w for w in state["watches"]}
    entries = []
    for entry in to_submit:
        context = entry_context(entry)
        if context is None:
            skipped.append(dict(entry, reason="context_unavailable"))
            continue
        payload = {k: entry[k] for k in ("key", "day", "market", "watch_id", "event_id", "ticker", "signal", "price")}
        payload.update(context, scenario=build_scenario(entry["item"], entry["record"], market),
                       trigger_type=trigger_label(market, entry["signal"]))
        entries.append(payload)
        append_journal(journal_path, {"phase": "submit", "key": entry["key"], "day": decision_day,
                                      "market": market, "watch_id": entry["watch_id"], "ticker": entry["ticker"],
                                      "signal": entry["signal"], "at": now.isoformat()})
        live = watches[entry["watch_id"]].setdefault("live", {})
        live[decision_day] = {"key": entry["key"], "status": "SUBMITTING", "signal": entry["signal"],
                              "decision_price": entry["price"], "ticker": entry["ticker"]}
        decision = (watches[entry["watch_id"]].get("decisions") or {}).get(decision_day)
        if decision is not None:
            decision["live"] = live[decision_day]
    if entries and save_state:
        save_state()                     # the key is durable before any order leaves the process
    outcomes = {}
    if entries:
        try:
            outcomes = executor(market, entries) or {}
        except Exception as error:
            logger.exception("[REENTRY_V3_LIVE][%s] executor failed", market)
            reason = "timeout" if "Timeout" in type(error).__name__ else f"executor_error:{type(error).__name__}"
            outcomes = {e["key"]: {"bought": False, "reason": reason} for e in entries}
    results = []
    for entry in entries:
        outcome = outcomes.get(entry["key"]) or {"bought": False, "reason": "no_result"}
        if outcome_status(outcome) == "ERROR" and db_path:
            # An order may have left before a crash/timeout: the holdings tables are the truth.
            found = reconcile_holdings(db_path, market, entry)
            if found:
                outcome = dict(found, bought=True, reconciled=True, reason=f"reconciled_after:{outcome.get('reason')}")
        status = outcome_status(outcome)
        live = watches[entry["watch_id"]]["live"][decision_day]
        live.update(status="BOUGHT" if status.startswith("BOUGHT") else status, result=status,
                    reason=outcome.get("reason"), holding_ids=outcome.get("holding_ids") or [],
                    entry_price=outcome.get("entry_price"), account_refs=outcome.get("account_refs") or [])
        append_journal(journal_path, {"phase": "result", "key": entry["key"], "day": decision_day, "status": status,
                                      "reason": outcome.get("reason"), "at": datetime.now(timezone.utc).isoformat()})
        results.append({"key": entry["key"], "ticker": entry["ticker"], "signal": entry["signal"], "status": status,
                        "reason": outcome.get("reason")})
        if emit:
            emit("reentry_v3.live_entry", watches[entry["watch_id"]], entry["key"],
                 {"status": status, "reason": outcome.get("reason"), "signal": entry["signal"],
                  "decision_price": entry["price"], "attempt": entry["scenario"]["reentry"]["attempt"],
                  "stop_loss": entry["scenario"].get("stop_loss"), "target_price": entry["scenario"].get("target_price"),
                  "entry_price": outcome.get("entry_price"), "holding_count": len(outcome.get("holding_ids") or [])})
    for entry in skipped:
        status = REASON_STATUS.get(entry["reason"], "SKIPPED")
        results.append({"key": entry["key"], "ticker": entry["ticker"], "signal": entry["signal"], "status": status,
                        "reason": entry["reason"]})
        if emit:
            emit("reentry_v3.live_skipped", watches[entry["watch_id"]], f"{entry['key']}|{entry['reason']}",
                 {"status": status, "reason": entry["reason"], "signal": entry["signal"],
                  "decision_price": entry.get("price")})
    if entries and save_state:
        save_state()
    return results


# ---------------------------------------------------------------- read-only DB helpers
def account_ref(account_key):
    """One-way reference like observability.reentry_shadow.candidates (account ids never leave the DB)."""
    return "acct-" + hashlib.sha256(str(account_key).encode()).hexdigest()[:12]


def _ro(db_path):
    import sqlite3
    return sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True, timeout=10)


# Fixed, literal queries per market / table (no SQL is built from strings at run time).
_HOLDINGS_ROWS_SQL = {
    "KR": "SELECT id, account_key, buy_price, scenario FROM stock_holdings WHERE ticker = ?",
    "US": "SELECT id, account_key, buy_price, scenario FROM us_stock_holdings WHERE ticker = ?",
}
_HELD_COUNT_SQL = {
    "stock_holdings": "SELECT COUNT(*) FROM stock_holdings WHERE ticker = ? AND account_key = ?",
    "us_stock_holdings": "SELECT COUNT(*) FROM us_stock_holdings WHERE ticker = ? AND account_key = ?",
}
_SLOT_COUNT_SQL = {
    "stock_holdings": "SELECT COUNT(*) FROM stock_holdings WHERE account_key = ?",
    "us_stock_holdings": "SELECT COUNT(*) FROM us_stock_holdings WHERE account_key = ?",
}
_EXIT_SECTOR_SQL = {
    "KR": ("SELECT sector FROM trading_history WHERE ticker = ? AND substr(sell_date, 1, 10) <= ? "
           "ORDER BY sell_date DESC LIMIT 1"),
    "US": ("SELECT sector FROM us_trading_history WHERE ticker = ? AND substr(sell_date, 1, 10) <= ? "
           "ORDER BY sell_date DESC LIMIT 1"),
}
_DECISION_SECTOR_SQL = {
    "KR": "SELECT sector FROM watchlist_history WHERE id = ?",
    "US": "SELECT sector FROM us_watchlist_history WHERE id = ?",
}
_REAL_EXIT_SQL = {
    "KR": ("SELECT account_key, sell_date, sell_price, profit_rate, exit_kind, scenario FROM trading_history "
           "WHERE ticker = ? AND substr(buy_date, 1, 10) >= ?"),
    "US": ("SELECT account_key, sell_date, sell_price, profit_rate, exit_kind, scenario FROM us_trading_history "
           "WHERE ticker = ? AND substr(buy_date, 1, 10) >= ?"),
}


def _meta(raw):
    try:
        return (json.loads(raw or "{}") or {}).get("reentry") or {}
    except (TypeError, ValueError):
        return {}


def reconcile_holdings(db_path, market, entry):
    """The holdings row(s) this re-entry created (scenario reentry.watch_id and trigger_date), else None."""
    try:
        with _ro(db_path) as conn:
            rows = conn.execute(_HOLDINGS_ROWS_SQL[market], (entry["ticker"],)).fetchall()
    except Exception:  # noqa: BLE001 - unknown stays ERROR
        return None
    found = [(r[0], r[1], r[2]) for r in rows
             if _meta(r[3]).get("watch_id") == entry["watch_id"] and _meta(r[3]).get("trigger_date") == entry["day"]]
    if not found:
        return None
    return {"holding_ids": [int(f[0]) for f in found], "entry_price": found[0][2],
            "account_refs": [account_ref(f[1]) for f in found]}


def strict_position_counts(cursor, table, ticker, account_key):
    """(held, slots) for one account; raises on any DB error or unknown table (the re-entry path fails closed)."""
    held = cursor.execute(_HELD_COUNT_SQL[table], (ticker, account_key)).fetchone()[0]
    slots = cursor.execute(_SLOT_COUNT_SQL[table], (account_key,)).fetchone()[0]
    return int(held) > 0, int(slots)


def original_sector(db_path, market, row):
    """Sector of the original decision (trading_history / watchlist_history row), else None."""
    market = "KR" if market == "KR" else "US"
    try:
        with _ro(db_path) as conn:
            if row.get("source") == "STOP_EXIT":
                found = conn.execute(_EXIT_SECTOR_SQL[market],
                                     (row["ticker"], (row.get("exit_date") or "9999")[:10] + "~")).fetchone()
            else:
                found = conn.execute(_DECISION_SECTOR_SQL[market], (row.get("analysis_id"),)).fetchone()
    except Exception:  # noqa: BLE001 - no sector -> caller falls back
        return None
    value = (found or [None])[0]
    return value if isinstance(value, str) and value.strip() and value != "Unknown" else None


def find_real_exit(db_path, market, live, watch_id, day):
    """The sold re-entry position from trading_history (read-only): {date, price, profit_rate, exit_kind, stop}."""
    from observability.reentry_shadow import session_date
    query = _REAL_EXIT_SQL["KR" if market == "KR" else "US"]
    refs = set(live.get("account_refs") or [])
    try:
        with _ro(db_path) as conn:
            rows = conn.execute(query, (live["ticker"], (day or "")[:10])).fetchall()
    except Exception:  # noqa: BLE001 - missing table/DB keeps the position open in the ledger
        return None
    for account_key, sell_date, sell_price, profit_rate, exit_kind, scenario in rows:
        meta = _meta(scenario)
        if meta.get("watch_id") != watch_id or meta.get("trigger_date") != day:
            continue
        if refs and account_ref(account_key) not in refs:
            continue
        stop = exit_kind == "stop" or (exit_kind is None and profit_rate is not None and profit_rate <= -3)
        return {"date": session_date(sell_date, market), "price": sell_price, "profit_rate": profit_rate,
                "exit_kind": exit_kind, "stop": bool(stop)}
    return None


# ---------------------------------------------------------------- executor (isolated subprocess)
def subprocess_executor(market, entries):
    """Run tools/run_reentry_v3_entry.py with the entries on stdin; {key: outcome}."""
    import subprocess  # nosec B404 - fixed argv below, no shell
    argv = {"KR": "kr", "US": "us"}[market]
    command = [sys.executable, str(ROOT / "tools/run_reentry_v3_entry.py"), "--market", argv]
    done = subprocess.run(  # nosec B603  # nosemgrep - static interpreter/script argv, data via stdin
        command, input=json.dumps({"entries": entries}, default=str), capture_output=True, text=True,
        timeout=SUBPROCESS_TIMEOUT, cwd=str(ROOT), check=False)
    lines = [line for line in done.stdout.splitlines() if line.startswith("{")]
    if not lines:
        raise RuntimeError(f"entry subprocess returned nothing: {done.stderr[-300:]}")
    return json.loads(lines[-1]).get("outcomes") or {}


async def enter_with_agent(agent, market, entry, *, now=None):
    """One re-entry through the agent's normal entry pipeline; never raises."""
    ticker = entry["ticker"]
    try:
        if not in_safe_window(market, now):
            return {"bought": False, "reason": "outside_regular_hours"}
        if not before_deadline(market, now):
            return {"bought": False, "reason": "deadline"}
        info = getattr(agent, "trigger_info_map", None)
        if not isinstance(info, dict):
            info = agent.trigger_info_map = {}
        info[ticker] = {"trigger_type": entry["trigger_type"], "trigger_mode": "reentry_v3"}
        bars = getattr(agent, "_decision_input_bars", None)
        if not isinstance(bars, dict):
            bars = agent._decision_input_bars = {}
        bars[ticker] = {"market": market, "bars": list(entry.get("bars") or [])[-80:],
                        "captured_at": datetime.now(timezone.utc).isoformat()}
        kwargs = {"ticker": ticker, "company_name": entry.get("company_name") or ticker,
                  "current_price": entry["price"], "scenario": dict(entry["scenario"]),
                  "sector": entry.get("sector") or "Unknown", "source_decision_id": entry["scenario"]["_decision_id"]}
        if market == "KR":
            return await agent.enter_reentry_candidate(**kwargs)
        outcomes = []
        for account in list(getattr(agent, "account_configs", None) or []):
            outcomes.append(await agent.enter_reentry_candidate(account=account, report_path=entry.get("report_path"),
                                                                **kwargs))
        bought = [o for o in outcomes if o.get("bought")]
        return {"bought": bool(bought), "reason": "bought" if bought else
                ";".join(sorted({str(o.get("reason")) for o in outcomes})) or "no_account",
                "holding_ids": [h for o in bought for h in (o.get("holding_ids") or [])],
                "entry_price": next((o.get("entry_price") for o in bought), None),
                "account_refs": [r for o in bought for r in (o.get("account_refs") or [])]}
    except Exception as error:
        logger.exception("[REENTRY_V3_LIVE][%s] entry failed for %s", market, ticker)
        return {"bought": False, "reason": f"error:{type(error).__name__}"}
