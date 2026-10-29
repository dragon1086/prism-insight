"""Operations watchdog: failed or missing scheduled jobs -> the private maintenance chat (2026-10-21).

Why: US re-entry orders failed on every signal from 10/13 to 10/20 and nobody noticed. The sell
loops log failed orders and skipped markets only as WARNING lines, and a loop that stops running
writes nothing at all. This reads the job logs incrementally (read-only) every few minutes and sends
one grouped message per run:

- [장애] program errors (Traceback/CRITICAL), failed sell/amend/cancel orders, a market a loop skipped
  (trading context or holdings failed), failed re-entry orders, a loop that has not completed a run
  inside its session window, a daily job that missed its deadline.
- [주의] data a check could not get (price, daily bars, live regime, sell facts) and other ERROR lines.

The same problem is held back for REPEAT_HOURS and then re-sent with its count. A stopped job that
runs again sends [복구]. Once a day (SUMMARY_AT, KST) a summary shows what ran, which also proves the
watchdog itself is alive: no summary in the morning means the watchdog is down.

Jobs that run under systemd (the B3 worker, which also runs the LIVE micro-split adds) log to journald;
they are read with journalctl after a stored cursor. Without journalctl (CI, macOS) or a readable journal
such a job is skipped for that run: no failure scan and no stopped-job alert.

Never touches orders, the DB or the jobs, and never raises into cron. Rollback: OPS_WATCHDOG_ENABLED=false.
State: runtime/ops_watchdog_state.json (log offsets, journal cursors, last runs, sent alerts).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess  # nosec B404 - fixed journalctl argv, no shell
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(os.getenv("PRISM_REPO_ROOT") or Path(__file__).resolve().parents[1])
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

KST, ET = ZoneInfo("Asia/Seoul"), ZoneInfo("America/New_York")
TZ = {"KR": KST, "US": ET}
MARKET_KO = {"KR": "한국", "US": "미국"}
STATE_PATH = ROOT / "runtime" / "ops_watchdog_state.json"
REPEAT_HOURS = float(os.getenv("OPS_WATCHDOG_REPEAT_HOURS", "6"))
SUMMARY_AT = os.getenv("OPS_WATCHDOG_SUMMARY_AT", "08:30")      # KST
MAX_READ_BYTES = 50 * 1024 * 1024
EXAMPLES_PER_GROUP = 3
MESSAGE_LIMIT = 3400
DISK_ALERT_PCT = float(os.getenv("OPS_WATCHDOG_DISK_ALERT_PCT", "90"))
JOURNAL_MAX_ENTRIES = 5000             # per unit and run (oldest first); the rest is read on the next run
JOURNAL_TIMEOUT_S = 30
B3_CONTRACT = '"contract": "b3-ae-worker-v1"'   # the B3 worker's tick line (printed every minute, all day)


@dataclass(frozen=True)
class Job:
    key: str
    label: str
    market: str | None
    log: str                       # repo-relative; {date} = KST YYYYMMDD (today and yesterday are read)
    heartbeat: str | None = None   # regex of the line every completed run writes
    every_min: int | None = None   # loop spacing inside ``window`` (market local time)
    window: tuple[str, str] | None = None
    deadline: str | None = None    # daily job: a heartbeat by this market-local time on session days
    unit: str | None = None        # systemd unit: read from journald instead of ``log``


# Heartbeat strings are the loops' own summary lines (pinned in tests/test_ops_watchdog.py).
JOBS = (
    Job("hardstop_kr", "손절 감시", "KR", "logs/loop_a_hardstop.log", r"Hardstop KR summary", 10, ("09:10", "15:20")),
    Job("hardstop_us", "손절 감시", "US", "logs/loop_a_hardstop.log", r"Hardstop US summary", 10, ("09:45", "16:00")),
    Job("trend_exit_kr", "추세 청산", "KR", "logs/loop_b_trend_exit.log", r"Trend-exit KR summary", 10,
        ("09:10", "15:25")),
    Job("trend_exit_us", "추세 청산", "US", "logs/loop_b_trend_exit.log", r"Trend-exit US summary", 10,
        ("09:45", "16:00")),
    Job("fill_chaser_kr", "미체결 정정", "KR", "logs/loop_c_fill_chaser.log", r"Fill-chaser KR summary", 2,
        ("09:05", "15:30")),
    Job("fill_chaser_us", "미체결 정정", "US", "logs/loop_c_fill_chaser.log", r"Fill-chaser US summary", 2,
        ("09:35", "16:00")),
    Job("reentry_kr", "재진입", "KR", "logs/reentry_v3_shadow_kr.log", r'"phase": "intraday"', deadline="14:30"),
    Job("reentry_us", "재진입", "US", "logs/reentry_v3_shadow_us.log", r'"phase": "intraday"', deadline="14:20"),
    # Record-only core long-term holding observer (tools/run_core_hold_observer.py, cron 16:40 KST / 17:10 ET).
    Job("core_hold_kr", "장기 보유 기록", "KR", "logs/core_hold_observer_kr.log", r"Core-hold KR summary", deadline="17:00"),
    Job("core_hold_us", "장기 보유 기록", "US", "logs/core_hold_observer_us.log", r"Core-hold US summary", deadline="17:30"),
    # Failure scan only (no heartbeat): analysis/trading batches and their AI buy/sell decisions.
    Job("kr_batch", "분석·매매 배치", "KR", "orchestrator_{date}.log"),
    Job("kr_tracking", "매매 판단", "KR", "enhanced_stock_tracking_{date}.log"),
    Job("kr_tracking_base", "매매 판단", "KR", "stock_tracking_{date}.log"),
    Job("us_batch", "분석·매매 배치", "US", "us_orchestrator_{date}.log"),
    Job("us_tracking", "매매 판단", "US", "us_stock_tracking_{date}.log"),
    Job("us_morning", "분석·매매 배치", "US", "logs/us_morning.log"),
    Job("us_afternoon", "분석·매매 배치", "US", "logs/us_afternoon.log"),
    Job("kr_morning", "분석·매매 배치", "KR", "logs/stock_analysis_morning.log"),
    Job("kr_afternoon", "분석·매매 배치", "KR", "logs/stock_analysis_afternoon.log"),
    # journald: the B3 worker prints B3_CONTRACT every minute all day and runs the LIVE in-slot adds
    # (tools/run_micro_split_add.py); it alerts failed add orders itself (prism_core.ops_alert).
    Job("b3_kr", "증액 집행", "KR", "", B3_CONTRACT, 1, ("08:30", "16:00"), unit="prism-b3-ae-shadow@kr.service"),
    Job("b3_us", "증액 집행", "US", "", B3_CONTRACT, 1, ("09:00", "16:30"), unit="prism-b3-ae-shadow@us.service"),
)

# First match wins: (pattern, label, severity). The specific sell/order lines come before the generic ERROR.
FAILURES = (
    (re.compile(r" - CRITICAL - "), "심각 오류", "장애"),
    (re.compile(r"sell success=False|KIS sell failed|sell action failed|sell_stock \(sim\) failed"), "매도 주문 실패", "장애"),
    (re.compile(r"(amend|cancel) success=False|(amend|cancel) failed:"), "정정·취소 실패", "장애"),
    (re.compile(r"trading context failed|holdings load failed|open-order inquiry failed"), "시장 전체 건너뜀", "장애"),
    (re.compile(r"\[REENTRY_V3_LIVE\]\[\w+\] (entry|executor) failed"), "재진입 주문 실패", "장애"),
    (re.compile(r"subprocess for market=\S+ failed"), "작업 실패", "장애"),
    (re.compile(r"price fetch failed|price_history: no source could answer|ma_50 fetch failed"
                r"|live regime compute failed|\[sell_facts\]"), "데이터 조회 실패", "주의"),
    (re.compile(r" - ERROR - "), "오류 기록", "주의"),
)
# Known non-failures: yfinance's delisted/unknown-symbol notices (a peer candidate Yahoo lacks is dropped, LC 10/27),
# the local OAuth proxy's timeout the OpenAI client retries (10/27), and skips we chose (one share over budget, 10/16;
# its later sell finds no shares in the account, LLY 10/30).
IGNORED = re.compile(r"possibly delisted|Quote not found for symbol|aiohttp\.server - ERROR - Error handling request"
                     r"|below one share|quantity is 0|not found in portfolio|budget unavailable")
_EXCEPTION_LINE = re.compile(r"^[A-Za-z_][\w.]*(Error|Exception|Interrupt|Exit|Timeout)\b")
_MARKET_TAG = re.compile(r"\[(KR|US)\]|\((KR|US)\)|market=(KR|US)\b|\b(KR|US) (summary|trading context)")
# Broker client lines carry no market tag; their logger names it (a shared log's owner is the KR job, 2026-10-24).
_LOGGER_MARKET = ((re.compile(r" - trading\.us_stock_trading - "), "US"),
                  (re.compile(r" - trading\.domestic_stock_trading - "), "KR"))
_STAMP = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
_REASON_CODE = re.compile(r"^[A-Z][A-Z0-9_]+$")


def _signature(job_key, label, text):
    body = re.sub(r"^\d{4}-\d{2}-\d{2} [\d:,]+ - ", "", text)
    body = re.sub(r"0x[0-9a-f]+|\d+(\.\d+)?", "N", body)
    return f"{job_key}|{label}|{body[:160]}"


_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2} [\d:,]+ - [\w.]+ - [A-Z]+ - ")


def _clean(text):
    """The log message without its timestamp/logger prefix, bot tokens redacted, bounded."""
    from prism_core.log_redaction import redact
    return redact(_PREFIX.sub("", text.strip()))[:220]


def _market_of(line, job):
    m = _MARKET_TAG.search(line)
    found = next((g for g in (m.groups() if m else ()) if g in ("KR", "US")), None)
    return found or next((market for pattern, market in _LOGGER_MARKET if pattern.search(line)), job.market)


def _stamp(line, now_kst):
    m = _STAMP.match(line)
    if not m:
        return now_kst
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
    except ValueError:
        return now_kst


def scan_lines(job, lines, now_kst, *, path=""):
    """(heartbeats, failures, reentry_results) found in new log lines. Pure: no state, no I/O."""
    beats, failures, reentry = [], [], []
    beat = re.compile(job.heartbeat) if job.heartbeat else None
    in_traceback = skip_traceback = logging_error = False
    for raw in lines:
        line = raw.rstrip("\n")
        if job.heartbeat and beat.search(line) and not line.startswith("{"):
            beats.append(_stamp(line, now_kst).isoformat())
            continue
        if line.startswith("--- Logging error ---"):
            logging_error = True     # a logging-format slip (until the next stamped line), not a job failure
            continue
        if logging_error and _STAMP.match(line):
            logging_error = False
        if line.startswith("Traceback (most recent call last)"):
            in_traceback, skip_traceback = True, logging_error
            continue
        if in_traceback:
            if line.startswith((" ", "\t")) or not line.strip():
                continue
            if _EXCEPTION_LINE.match(line):
                in_traceback = False
                if not skip_traceback:
                    failures.append({"job": job.key, "market": job.market, "label": "프로그램 오류",
                                     "severity": "장애", "text": _clean(line), "at": now_kst.isoformat(),
                                     "log": path})
                continue
            in_traceback = False
        if line.startswith("{") and B3_CONTRACT in line:       # the B3 worker's tick result
            if beat and beat.search(line):
                beats.append(_stamp(line, now_kst).isoformat())
            try:
                tick = json.loads(line)
            except ValueError:
                continue
            # A failed tick or one campaign's failed step (failed LIVE add orders the worker alerts itself).
            # A bare reason code (e.g. ValueError QUOTE_STALE_OR_FUTURE) is missing input data, not a bug.
            for item in [tick] + [row for row in tick.get("rows") or [] if isinstance(row, dict)]:
                if item.get("status") == "ERROR":
                    data = bool(_REASON_CODE.match(str(item.get("detail"))))
                    failures.append({"job": job.key, "market": tick.get("market") or job.market,
                                     "label": "데이터 조회 실패" if data else "프로그램 오류",
                                     "severity": "주의" if data else "장애", "log": path,
                                     "text": _clean(f"{item.get('error_type')}: {item.get('detail')}"),
                                     "at": now_kst.isoformat()})
            continue
        if beat and beat.search(line):          # the re-entry runner's JSON summary line
            beats.append(_stamp(line, now_kst).isoformat())
            try:
                reentry.append(json.loads(line))
            except ValueError:
                pass
        if line.startswith("{") and '"live_results"' in line:
            try:
                summary = json.loads(line)
            except ValueError:
                summary = None
            errors = int(((summary or {}).get("live_results") or {}).get("ERROR") or 0)
            if errors:
                failures.append({"job": job.key, "market": job.market, "label": "재진입 주문 실패",
                                 "severity": "장애", "at": now_kst.isoformat(), "log": path,
                                 "text": f"{summary.get('decision_day')} 신호 {summary.get('new_triggers')}건 중 "
                                         f"주문 오류 {errors}건 (결과 {summary.get('live_results')})"})
            continue
        if IGNORED.search(line):
            continue
        for pattern, label, severity in FAILURES:
            if pattern.search(line):
                failures.append({"job": job.key, "market": _market_of(line, job), "label": label,
                                 "severity": severity, "text": _clean(line),
                                 "at": _stamp(line, now_kst).isoformat(), "log": path})
                break
    return beats, failures, reentry


def _is_session(market, day):
    try:
        from prism_core.add_plan import is_session
        return is_session(market, day)
    except Exception:  # noqa: BLE001 - calendar unavailable: weekday rule
        return day.weekday() < 5


def _hm(text):
    hour, minute = text.split(":")
    return int(hour), int(minute)


def stale_jobs(state, now_kst, jobs=JOBS):
    """[(job, last_run_iso or None, minutes)] for loops that have not completed inside their window."""
    out = []
    started = datetime.fromisoformat(state.get("started") or now_kst.isoformat())
    for job in jobs:
        if not job.heartbeat or job.market not in TZ:
            continue
        local = now_kst.astimezone(TZ[job.market])
        if not _is_session(job.market, local.date()):
            continue
        last_iso = (state.get("last_run") or {}).get(job.key)
        last = datetime.fromisoformat(last_iso) if last_iso else started
        if job.window and job.every_min:
            start = local.replace(hour=_hm(job.window[0])[0], minute=_hm(job.window[0])[1], second=0, microsecond=0)
            end = local.replace(hour=_hm(job.window[1])[0], minute=_hm(job.window[1])[1], second=0, microsecond=0)
            limit = timedelta(minutes=max(3 * job.every_min, 20))
            if start + limit <= local <= end and local - max(last.astimezone(TZ[job.market]), start) > limit:
                out.append((job, last_iso, int((local - last.astimezone(TZ[job.market])).total_seconds() // 60)))
        elif job.deadline:
            due = local.replace(hour=_hm(job.deadline)[0], minute=_hm(job.deadline)[1], second=0, microsecond=0)
            missed = (last.astimezone(TZ[job.market]).date() < local.date() if last_iso
                      else started.astimezone(TZ[job.market]) < due)     # watching since before the deadline
            if local >= due and missed:
                out.append((job, last_iso, int((local - due).total_seconds() // 60)))
    return out


def _paths(job, now_kst):
    if "{date}" not in job.log:
        return [ROOT / job.log]
    days = (now_kst.date() - timedelta(days=1), now_kst.date())
    return [ROOT / job.log.format(date=d.strftime("%Y%m%d")) for d in days]


def _read_new(path, state, first_run):
    """New lines since the stored offset; a new file starts at 0, a known file at the first run at EOF."""
    offsets = state.setdefault("offsets", {})
    key = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)
    try:
        size = path.stat().st_size
    except OSError:
        return key, []
    if key not in offsets and first_run:
        offsets[key] = size
        return key, []
    start = offsets.get(key, 0)
    if size < start:                     # rotated or truncated
        start = 0
    start = max(start, size - MAX_READ_BYTES)
    with path.open("rb") as handle:
        handle.seek(start)
        data = handle.read(size - start)
    cut = data.rfind(b"\n") + 1          # keep a partial last line for the next run
    offsets[key] = start + cut
    return key, data[:cut].decode("utf-8", errors="replace").replace("\x00", "").splitlines()


def _journalctl(unit, cursor):
    """(message lines, last cursor) of ``unit`` after ``cursor``; None when journalctl is missing or times out.

    Without a cursor only the newest entry's cursor is taken (start at the end, like a known log on the
    first run). ``-n`` after ``--after-cursor`` keeps the oldest entries (checked on systemd 239), so a
    backlog is read over several runs. A failed call with a cursor (e.g. "Failed to seek to cursor" after
    the journal was vacuumed) returns no cursor, so the next run starts again at the end.
    """
    argv = ["journalctl", "-u", unit, "-o", "json", "--no-pager", "-q"]
    argv += ["--after-cursor", cursor, "-n", str(JOURNAL_MAX_ENTRIES)] if cursor else ["-n", "1"]
    try:
        done = subprocess.run(argv, capture_output=True, timeout=JOURNAL_TIMEOUT_S, check=False)  # nosec B603 B607  # nosemgrep
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return ([], None) if cursor else None
    lines, last = [], cursor
    for raw in done.stdout.splitlines()[:JOURNAL_MAX_ENTRIES]:
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        message = entry.get("MESSAGE")
        if isinstance(message, list):        # journald gives a non-UTF-8 message as a byte array
            message = bytes(message).decode("utf-8", errors="replace")
        last = entry.get("__CURSOR") or last
        if cursor and isinstance(message, str):
            lines.extend(message.replace("\x00", "").splitlines())
    return lines, last


def _read_journal(job, state):
    """New message lines of ``job.unit``, or None when its journal cannot be read on this run.

    The first read of a unit (no stored cursor) only sets the cursor and counts as not read, so a
    missing journal or an absent unit never raises a stopped-job alert.
    """
    cursors = state.setdefault("journal_cursors", {})
    cursor = cursors.get(job.unit)
    try:
        result = _journalctl(job.unit, cursor)
    except Exception:  # noqa: BLE001 - fail open: a broken reader skips the job, never the run
        result = None
    if result is None:
        return None
    lines, last = result
    if not last:
        cursors.pop(job.unit, None)
        return None
    cursors[job.unit] = last
    return lines if cursor else None


def _job_name(job, market=None):
    market = market or job.market
    return f"{job.label}({MARKET_KO.get(market, market)})" if market in MARKET_KO else job.label


def build_alert(failures, stale, recovered, now_kst, jobs=JOBS):
    """One message, or None. ``failures`` are already de-duplicated: [(failure, count, repeats)]."""
    if not failures and not stale and not recovered:
        return None
    by_key = {job.key: job for job in jobs}
    severe = bool(stale) or any(f["severity"] == "장애" for f, _, _ in failures)
    lines = [f"[{'장애' if severe else '주의'}] PRISM 운영 감시 {now_kst:%m/%d %H:%M} KST"]
    for job, last_iso, minutes in stale:
        last = f"마지막 정상 실행 {datetime.fromisoformat(last_iso).astimezone(KST):%m/%d %H:%M}" if last_iso \
            else "감시 시작 후 실행 기록 없음"
        what = "정해진 시각까지 실행 안 됨" if job.deadline else f"{minutes}분째 실행 안 됨"
        lines.append(f"■ {_job_name(job)} — 작업 멈춤: {what} ({last})")
    for severity in ("장애", "주의"):
        groups = {}
        for failure, count, repeats in failures:
            if failure["severity"] != severity:
                continue
            group = groups.setdefault((failure["job"], failure["market"], failure["label"]),
                                      {"items": [], "count": 0, "repeats": 0, "log": failure.get("log")})
            group["count"] += count
            group["repeats"] = max(group["repeats"], repeats)
            if len(group["items"]) < EXAMPLES_PER_GROUP:
                group["items"].append(failure)
        if groups and severity == "주의" and severe:
            lines.append("[주의]")
        for (job_key, market, label), group in groups.items():
            again = f", {group['repeats']}번째 알림" if group["repeats"] > 1 else ""
            lines.append(f"■ {_job_name(by_key[job_key], market)} — {label} {group['count']}건{again}")
            for item in group["items"]:
                lines.append(f"  {datetime.fromisoformat(item['at']).astimezone(KST):%H:%M} {item['text']}")
            if group["log"]:
                lines.append(f"  로그: {group['log']}")
    for job in recovered:
        lines.append(f"[복구] {_job_name(job)} 다시 정상 실행 중")
    lines.append(f"같은 오류는 {REPEAT_HOURS:g}시간 동안 다시 보내지 않습니다.")
    text = "\n".join(lines)
    return text if len(text) <= MESSAGE_LIMIT else text[:MESSAGE_LIMIT - 20] + "\n…(로그에서 전체 확인)"


def build_summary(state, now_kst, jobs=JOBS):
    """Daily line per loop: completed runs, problems and re-entry results since the last summary."""
    tally = state.get("tally") or {}
    since = state.get("summary_since") or state.get("started")
    lines = [f"[운영 점검] 감시 정상 작동 — {datetime.fromisoformat(since).astimezone(KST):%m/%d %H:%M} 이후"]
    for job in jobs:
        t = tally.get(job.key) or {}
        if not job.heartbeat and not t.get("fail"):
            continue
        part = f"- {_job_name(job)}: 실행 {t.get('runs', 0)}회"
        if job.key.startswith("reentry"):
            part += f", 신호 {t.get('signals', 0)}건 → 매수 {t.get('bought', 0)}건, 주문 오류 {t.get('errors', 0)}건"
        problems = t.get("fail", 0) + t.get("stale", 0)
        lines.append(part + (f", 문제 {problems}건" if problems else ""))
    if len(lines) == 1:
        lines.append("- 기록된 실행 없음")
    return "\n".join(lines)


def _tally(state, key, field, amount=1):
    entry = state.setdefault("tally", {}).setdefault(key, {})
    entry[field] = entry.get(field, 0) + amount


def run(now_kst=None, *, state_path=STATE_PATH, sender=None, dry_run=False, jobs=JOBS):
    now_kst = now_kst or datetime.now(KST)
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    first_run = "started" not in state
    if first_run:                        # the first daily summary comes on the next SUMMARY_AT
        hour, minute = _hm(SUMMARY_AT)
        if now_kst >= now_kst.replace(hour=hour, minute=minute, second=0, microsecond=0):
            state["summary_day"] = now_kst.date().isoformat()
    state.setdefault("started", now_kst.isoformat())
    state.setdefault("summary_since", now_kst.isoformat())
    sent = state.setdefault("sent", {})
    found = []
    read = {}
    blind = set()                        # journald jobs whose journal could not be read on this run
    for job in jobs:
        owner = _owner(jobs, _source(job))
        if job.unit:
            lines = _read_journal(job, state)
            if lines is None:
                blind.add(job.key)
                continue
            path = f"journalctl -u {job.unit}"
            sources = [(path, lines)]
        else:
            sources = []
            for file in _paths(job, now_kst):
                key = str(file.relative_to(ROOT))
                if key not in read:
                    read[key] = _read_new(file, state, first_run)[1]
                sources.append((key, read[key]))
        for key, lines in sources:
            beats, failures, reentry = scan_lines(job, lines, now_kst, path=key)
            if beats:
                state.setdefault("last_run", {})[job.key] = max(beats + [state.get("last_run", {}).get(job.key, "")])
                _tally(state, job.key, "runs", len(beats))
            for summary in reentry:
                results = summary.get("live_results") or {}
                _tally(state, job.key, "signals", int(summary.get("new_triggers") or 0))
                _tally(state, job.key, "bought", sum(v for k, v in results.items() if str(k).startswith("BOUGHT")))
                _tally(state, job.key, "errors", int(results.get("ERROR") or 0))
            if job is not owner:
                continue                 # a shared log's failures are counted once, by its first job
            for failure in failures:     # ...and filed under the job of the market the line names
                failure["job"] = next((j.key for j in jobs if _source(j) == _source(job)
                                       and j.market == failure["market"]), owner.key)
            found.extend(failures)

    # De-duplicate: one alert per signature per REPEAT_HOURS, then again with the count.
    grouped = {}
    for failure in found:
        sig = _signature(failure["job"], failure["label"], failure["text"])
        grouped.setdefault(sig, [failure, 0])[1] += 1
        _tally(state, failure["job"], "fail")
    to_send = []
    for sig, (failure, count) in grouped.items():
        record = sent.setdefault(sig, {"count": 0, "alerts": 0, "last": None})
        record["count"] += count
        last = datetime.fromisoformat(record["last"]) if record["last"] else None
        if last is None or now_kst - last >= timedelta(hours=REPEAT_HOURS):
            record["alerts"] += 1
            record["last"] = now_kst.isoformat()
            to_send.append((failure, record["count"] if record["alerts"] > 1 else count, record["alerts"]))
            record["count"] = 0

    stale_now = stale_jobs(state, now_kst, [job for job in jobs if job.key not in blind])
    open_stale = state.setdefault("stale", {})
    stale_new = [s for s in stale_now if s[0].key not in open_stale]
    for job, _, _ in stale_new:
        open_stale[job.key] = now_kst.isoformat()
        _tally(state, job.key, "stale")
    stale_keys = {job.key for job, _, _ in stale_now}
    recovered = [job for job in jobs if job.key in open_stale and job.key not in stale_keys
                 and (state.get("last_run") or {}).get(job.key, "") > open_stale[job.key]]
    for job in recovered:
        open_stale.pop(job.key, None)

    # Forget signatures not seen for a week so the state stays small.
    week_ago = now_kst - timedelta(days=7)
    for sig in [s for s, r in sent.items() if r.get("last") and datetime.fromisoformat(r["last"]) < week_ago]:
        sent.pop(sig)

    messages = []
    disk = disk_alert(state, now_kst)
    if disk:
        messages.append(disk)
    alert = build_alert(to_send, stale_new, recovered, now_kst, jobs)
    if alert:
        messages.append(alert)
    hour, minute = _hm(SUMMARY_AT)
    due = now_kst.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if now_kst >= due and state.get("summary_day") != now_kst.date().isoformat() and not first_run:
        messages.append(build_summary(state, now_kst, jobs))
        state["summary_day"] = now_kst.date().isoformat()
        state["summary_since"] = now_kst.isoformat()
        state["tally"] = {}

    for text in messages:
        if dry_run:
            print(text)
            continue
        ok = (sender or _send)(text)
        print(f"{now_kst:%Y-%m-%d %H:%M:%S} ops alert sent={ok} first_line={text.splitlines()[0]!r}", flush=True)
    if not dry_run:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(state_path)
    return messages


def disk_used_pct():
    usage = shutil.disk_usage(ROOT)
    # Same as df Use%: root's reserved blocks are not free space for the jobs (83% here when total said 80%).
    return 100 * usage.used / (usage.used + usage.free)


def disk_alert(state, now_kst):
    """A full disk stopped Codex and MCP startups for a day (db-server, 2026-10-28): warn before that."""
    pct = disk_used_pct()
    last = state.get("disk_alert")
    if pct < DISK_ALERT_PCT:
        state.pop("disk_alert", None)
        return None
    if last and now_kst - datetime.fromisoformat(last) < timedelta(hours=REPEAT_HOURS):
        return None
    state["disk_alert"] = now_kst.isoformat()
    free_gb = shutil.disk_usage(ROOT).free / 1e9
    return (f"[장애] PRISM 서버 저장공간 부족 {now_kst:%m/%d %H:%M} KST\n"
            f"■ 디스크 {pct:.0f}% 사용 (남은 공간 {free_gb:.1f}GB, 기준 {DISK_ALERT_PCT:g}%)\n"
            f"  가득 차면 AI 판단·뉴스 검색이 시작되지 않습니다. 큰 파일: du -xh --max-depth=1 / | sort -rh | head\n"
            f"{REPEAT_HOURS:g}시간 뒤에도 기준 이상이면 다시 보냅니다.")


def _source(job):
    """Where a job's lines come from: its log file or its systemd unit's journal."""
    return f"journal:{job.unit}" if job.unit else job.log


def _owner(jobs, source):
    """The first job declared for a log file or unit; it owns lines whose market cannot be told."""
    return next(job for job in jobs if _source(job) == source)


def _send(text):
    from prism_core.ops_alert import send_ops_alert_sync
    return send_ops_alert_sync(text)


def enabled():
    return os.getenv("OPS_WATCHDOG_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="print the messages, send nothing, keep the state")
    args = parser.parse_args(argv)
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env", override=False)
    except Exception as error:  # noqa: BLE001 - the environment may already carry the settings
        print(f".env not loaded: {type(error).__name__}", file=sys.stderr)
    if not enabled():
        print("OPS_WATCHDOG_ENABLED=false -> skipped")
        return 0
    try:
        run(dry_run=args.dry_run)
    except Exception as error:  # noqa: BLE001 - the watchdog must never break cron
        print(f"ops watchdog failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
