"""Re-entry v3 LIVE: real buys for approved re-entry triggers through the normal tracker entry path.

User decision (2026-10-04): no SHADOW phase; re-entry v3 trades the real accounts and is
maintained while live. The deterministic ledger and the LLM recheck stay as they are
(observability/reentry_v3_shadow.py); when the recheck approves (decision = 진입) at the
decision run (KR 14:00 KST, US 13:50 ET) every deterministic check of the normal entry runs
again inside the tracker (``enter_reentry_candidate`` on the KR enhanced / US agents, which
reuse the batch's ``_enter_eligible_candidate``: micro-split B3 sizing + add_plan, final buy
gate, holdings row and scenario, order intents, Telegram buy message, Redis/GCP publish).

Safety (this module): kill switch REENTRY_V3_LIVE_ENABLED (default off) and
REENTRY_V3_LIVE_MARKETS (default KR,US); at most DAILY_CAP re-entry orders per market per
session; regular hours only, never in an auction window; one idempotency key per watch and
session written (fsync) before the order and never retried the same day, whatever happened;
any exception is logged, emitted and contained. The order runs in an isolated subprocess
(tools/run_reentry_v3_entry.py, same pattern as the micro-split LIVE adds).
After the buy the holding is managed by the existing sell logic like any other position.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
from datetime import datetime, time as dtime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
LIVE_VERSION = "reentry_v3"
DAILY_CAP = 2
# Continuous regular session with a buffer: KR 09:00-15:20 (closing auction 15:20-15:30),
# US 09:30-16:00 ET (closing-auction order cutoffs from 15:50).
SAFE_WINDOWS = {"KR": ("Asia/Seoul", dtime(9, 5), dtime(15, 15)),
                "US": ("America/New_York", dtime(9, 35), dtime(15, 45))}
SIGNALS = {"R1C": "REBREAK", "R2S": "RETEST", "SHAKEOUT_RECLAIM": "SHAKEOUT_RECLAIM"}
SIGNAL_KO = {"REBREAK": "기준 가격 재돌파 매수", "RETEST": "기준 가격 눌림 지지 매수",
             "SHAKEOUT_RECLAIM": "흔들기 후 회복 매수"}
SIGNAL_WHY_KO = {
    "REBREAK": "지난번 손절·보류 뒤 첫 매수 때 돌파했던 가격대({level})를 다시 넘어섰습니다",
    "RETEST": "첫 매수 때 돌파했던 가격대({level})까지 내려왔다가 지지를 받고 버텼습니다",
    "SHAKEOUT_RECLAIM": "첫 매수 때 돌파했던 가격대({level}) 아래로 크게 흔들린 뒤 거래량을 동반해 다시 회복했습니다",
}
SOURCE_KO = {"STOP_EXIT": "손절", "LOCATION_SKIP": "자리 보류", "ENTER_BLOCKED": "게이트 차단"}
APPROVE = {"진입", "enter", "entry"}
SUBPROCESS_TIMEOUT = 600


def _flag(name, default):
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def live_markets():
    return {m.strip().upper() for m in os.getenv("REENTRY_V3_LIVE_MARKETS", "KR,US").split(",") if m.strip()}


def live_enabled(market):
    """Kill switch: REENTRY_V3_LIVE_ENABLED=true (default false) and the market in REENTRY_V3_LIVE_MARKETS."""
    return _flag("REENTRY_V3_LIVE_ENABLED", "false") and str(market).upper() in live_markets()


def in_safe_window(market, now=None):
    """True inside the market's continuous regular session (weekday, buffered, no auction)."""
    zone, start, end = SAFE_WINDOWS[market]
    local = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(zone))
    return local.weekday() < 5 and start <= local.time() < end


def idempotency_key(watch_id, day):
    return hashlib.sha256(f"{LIVE_VERSION}|{watch_id}|{day}".encode()).hexdigest()[:32]


def trigger_label(market, signal):
    """holdings.trigger_type: re-entries stay separable in messages, the dashboard and win-rate stats."""
    return f"재진입({SIGNAL_KO[signal]})" if market == "KR" else f"Re-entry ({signal})"


def approved(record):
    return (record.get("status") == "OK" and record.get("approved") is True
            and str((record.get("scenario") or {}).get("decision", "")).strip().lower() in APPROVE)


def build_scenario(item, record, market):
    """The recheck's BUY JSON plus re-entry metadata, the capped re-entry stop and the BUY-rule target."""
    scenario = dict(record.get("scenario") or {})
    signal = SIGNALS[item["trigger"]]
    primary = next(iter(item.get("attempts") or {}), None)
    attempt = (item.get("attempts") or {}).get(primary, {}).get("attempt", 1)
    scenario["reentry"] = {
        "version": LIVE_VERSION, "signal": signal, "signal_ko": SIGNAL_KO[signal], "attempt": attempt,
        "max_attempts": 3, "attempt_label": f"{attempt}/3", "level": item["level"]["L"],
        "level_basis": item["level"]["basis"], "watch_id": item["watch_ref"], "source": item["source"],
        "trigger_date": item["trigger_date"], "decision_price": item["decision_price"],
        "decision_time": item["decision_time"], "event_id": item["event_id"],
        "stop_rule": item.get("stop_rule"), "target_rule": item.get("target_rule"),
    }
    price = float(item["decision_price"])
    scenario["stop_loss"] = item["stop"]
    if item.get("target"):
        scenario["target_price"] = item["target"]
    # Keep the reported arithmetic consistent with the replaced stop/target (the final buy gate checks it).
    scenario["entry_price"] = price
    scenario.pop("_analysis_entry_price", None)
    target, stop = scenario.get("target_price"), scenario["stop_loss"]
    if isinstance(target, (int, float)) and target > price > stop > 0:
        scenario["expected_return_pct"] = round((target / price - 1) * 100, 4)
        scenario["expected_loss_pct"] = round((1 - stop / price) * 100, 4)
        scenario["risk_reward_ratio"] = round((target - price) / (price - stop), 4)
    scenario["trigger_type"] = trigger_label(market, signal)
    scenario["_decision_id"] = f"reentry_v3:{item['event_id']}"
    return scenario


def entry_message_line(scenario, market):
    """Plain-language re-entry line for the Telegram buy message ('' for every other entry)."""
    meta = (scenario or {}).get("reentry") if isinstance(scenario, dict) else None
    if not isinstance(meta, dict) or meta.get("version") != LIVE_VERSION:
        return ""
    level = meta.get("level")
    level_text = (f"{level:,.0f}원" if market == "KR" else f"${level:,.2f}") if isinstance(level, (int, float)) else "-"
    signal = meta.get("signal")
    why = SIGNAL_WHY_KO.get(signal, "").format(level=level_text)
    source = SOURCE_KO.get(meta.get("source"), "")
    return (f"🔁 재진입 매수 ({SIGNAL_KO.get(signal, signal)}, {meta.get('attempt_label')}번째 시도)\n"
            f"이 종목은 이전에 {source}했던 종목입니다. {why}.\n")


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


def plan(market, decision_day, records, items, journal, now):
    """(to_submit, skipped): approved recheck records of this session that may be ordered now."""
    submitted = {r["key"] for r in journal if r.get("phase") == "submit"}
    used = len({r["key"] for r in journal if r.get("phase") == "submit" and r.get("day") == decision_day})
    to_submit, skipped = [], []
    for record in records:
        item = items.get(record.get("event_id"))
        if item is None or item.get("trigger_date") != decision_day or not approved(record):
            continue
        key = idempotency_key(item["watch_ref"], decision_day)
        reason = None
        if key in submitted or key in {e["key"] for e in to_submit}:
            reason = "duplicate"
        elif used + len(to_submit) >= DAILY_CAP:
            reason = "daily_cap"
        elif not in_safe_window(market, now):
            reason = "outside_regular_hours"
        entry = {"key": key, "day": decision_day, "market": market, "watch_id": item["watch_ref"],
                 "event_id": item["event_id"], "ticker": item["ticker"], "signal": SIGNALS[item["trigger"]],
                 "price": item["decision_price"], "item": item, "record": record}
        (skipped if reason else to_submit).append(dict(entry, reason=reason) if reason else entry)
    return to_submit, skipped


def process(state, market, decision_day, records, items, journal_path, executor, *, entry_context, now=None,
            emit=None, save_state=None):
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
        except Exception as error:      # noqa: BLE001 - never crash the runner, never retry today
            logger.exception("[REENTRY_V3_LIVE][%s] executor failed", market)
            outcomes = {e["key"]: {"bought": False, "reason": f"executor_error:{type(error).__name__}"} for e in entries}
    results = []
    for entry in entries:
        outcome = outcomes.get(entry["key"]) or {"bought": False, "reason": "no_result"}
        status = "BOUGHT" if outcome.get("bought") else ("ERROR" if str(outcome.get("reason", "")).startswith(
            ("error", "executor_error", "no_result")) else "NOT_BOUGHT")
        live = watches[entry["watch_id"]]["live"][decision_day]
        live.update(status=status, reason=outcome.get("reason"), holding_ids=outcome.get("holding_ids") or [],
                    entry_price=outcome.get("entry_price"), account_refs=outcome.get("account_refs") or [])
        append_journal(journal_path, {"phase": "result", "key": entry["key"], "day": decision_day, "status": status,
                                      "reason": outcome.get("reason"), "at": datetime.now(timezone.utc).isoformat()})
        results.append({"key": entry["key"], "ticker": entry["ticker"], "signal": entry["signal"], "status": status,
                        "reason": outcome.get("reason")})
        if emit:
            emit("reentry_v3.live_entry", watches[entry["watch_id"]], entry["key"],
                 {"status": status, "reason": outcome.get("reason"), "signal": entry["signal"],
                  "decision_price": entry["price"], "attempt": entry["scenario"]["reentry"]["attempt"],
                  "stop_loss": entry["scenario"].get("stop_loss"), "target_price": entry["scenario"].get("target_price")})
    for entry in skipped:
        results.append({"key": entry["key"], "ticker": entry["ticker"], "signal": entry["signal"], "status": "SKIPPED",
                        "reason": entry["reason"]})
        if emit:
            emit("reentry_v3.live_skipped", watches[entry["watch_id"]], f"{entry['key']}|{entry['reason']}",
                 {"status": "SKIPPED", "reason": entry["reason"], "signal": entry["signal"]})
    if entries and save_state:
        save_state()
    return results


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
        info = getattr(agent, "trigger_info_map", None)
        if not isinstance(info, dict):
            info = agent.trigger_info_map = {}
        info[ticker] = {"trigger_type": entry["trigger_type"], "trigger_mode": "reentry_v3"}
        bars = getattr(agent, "_decision_input_bars", None)
        if not isinstance(bars, dict):
            bars = agent._decision_input_bars = {}
        bars[ticker] = {"market": market, "bars": list(entry.get("bars") or [])[-80:],
                        "captured_at": datetime.now(timezone.utc).isoformat()}
        kwargs = dict(ticker=ticker, company_name=entry.get("company_name") or ticker, current_price=entry["price"],
                      scenario=dict(entry["scenario"]), sector=entry.get("sector") or "Unknown",
                      source_decision_id=entry["scenario"]["_decision_id"])
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
    except Exception as error:  # noqa: BLE001 - one failed entry never stops the others
        logger.exception("[REENTRY_V3_LIVE][%s] entry failed for %s", market, ticker)
        return {"bought": False, "reason": f"error:{type(error).__name__}"}


def account_ref(account_key):
    """One-way reference like observability.reentry_shadow.candidates (account ids never leave the DB)."""
    return "acct-" + hashlib.sha256(str(account_key).encode()).hexdigest()[:12]


def find_real_exit(db_path, market, live, watch_id, day):
    """The sold re-entry position from trading_history (read-only): {date, price, profit_rate, exit_kind, stop}."""
    import sqlite3

    from observability.reentry_shadow import session_date
    table = "trading_history" if market == "KR" else "us_trading_history"
    refs = set(live.get("account_refs") or [])
    try:
        with sqlite3.connect("file:" + str(db_path) + "?mode=ro", uri=True, timeout=10) as conn:
            rows = conn.execute(f"SELECT account_key, sell_date, sell_price, profit_rate, exit_kind, scenario "  # nosec B608 - fixed table names
                                f"FROM {table} WHERE ticker = ? AND substr(buy_date, 1, 10) >= ?",
                                (live["ticker"], (day or "")[:10])).fetchall()
    except Exception:  # noqa: BLE001 - missing table/DB keeps the position open in the ledger
        return None
    for account_key, sell_date, sell_price, profit_rate, exit_kind, scenario in rows:
        try:
            meta = (json.loads(scenario or "{}") or {}).get("reentry") or {}
        except ValueError:
            meta = {}
        if meta.get("watch_id") != watch_id or meta.get("trigger_date") != day:
            continue
        if refs and account_ref(account_key) not in refs:
            continue
        stop = exit_kind == "stop" or (exit_kind is None and profit_rate is not None and profit_rate <= -3)
        return {"date": session_date(sell_date, market), "price": sell_price, "profit_rate": profit_rate,
                "exit_kind": exit_kind, "stop": bool(stop)}
    return None
