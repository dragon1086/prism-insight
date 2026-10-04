#!/usr/bin/env python3
"""Two-week review of the changes that went LIVE on 2026-10-02..10-04 (KR and US).

One report, one section per change, in plain investor Korean: how often each rule fired,
what happened, the counterfactual where the evidence allows it, failures/anomalies, and
the north-star scorecard (docs/TRADING_CHANGE_REVIEW_HARNESS.md). What is read and why
is documented in docs/TWO_WEEK_REVIEW_ko.md.

Read-only: the tracking DB and the B3 store are opened with ``mode=ro``; the event spool
(logs/prism_events.jsonl), runtime state files and logs are only read. Price lookups use
the weekly report's data paths (KR cores.stock_chart, US prism-us USDataClient) with a
hard cap per market; DART is never called. ``--dry-run`` prints only. Otherwise the
report goes to the private maintenance chat (prism_core.ops_alert, split <= 3400 chars)
and the full text is written to logs/reviews/.

Usage:
    python tools/two_week_review.py --dry-run
    python tools/two_week_review.py --start 2026-10-05 --end 2026-10-18
"""
from __future__ import annotations

import argparse
import gzip
import json
import logging
import os
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(os.getenv("PRISM_REPO_ROOT") or Path(__file__).resolve().parents[1])
for _path in (ROOT, ROOT / "tools", Path(__file__).resolve().parent):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import weekly_runner_report as WR  # noqa: E402

logger = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")
MARKETS = ("KR", "US")
DEFAULT_DAYS = 14
LEADER_PCT = 20.0
BOOK_SLOTS = WR.BOOK_SLOTS
STRONG_TRIGGERS = {"KR": {"일중 상승률 상위주", "갭 상승 모멘텀 상위주"},
                   "US": {"Intraday Rise Top", "Gap Up Momentum Top"}}
SKIP_LOG_PREFIXES = ("btc_", "kakao_", "krx_openapi")

# Fixed SQL (literals only; values go through ? parameters).
WATCH_SQL = {
    "KR": "SELECT ticker, company_name, analyzed_date, current_price, buy_score, min_score, decision, skip_reason, "
          "scenario, trigger_type, trigger_mode FROM watchlist_history "
          "WHERE substr(analyzed_date, 1, 10) >= ? AND substr(analyzed_date, 1, 10) <= ? ORDER BY analyzed_date",
    "US": "SELECT ticker, company_name, analyzed_date, current_price, buy_score, min_score, decision, skip_reason, "
          "scenario, trigger_type, trigger_mode FROM us_watchlist_history "
          "WHERE substr(analyzed_date, 1, 10) >= ? AND substr(analyzed_date, 1, 10) <= ? ORDER BY analyzed_date",
}
WATCH_TABLE = {"KR": "watchlist_history", "US": "us_watchlist_history"}
B3_EVENTS_SQL = "SELECT kind, at FROM b3_events WHERE substr(at, 1, 10) >= ? AND substr(at, 1, 10) <= ?"

# Log lines worth counting: (key, label, regex). Matched only inside the review window.
LOG_PATTERNS = (
    ("fallback_kr", "AI 매도 판단 실패 → 옛 규칙 대체(KR)", r"falling back to legacy algorithm|Error in AI sell analysis"),
    ("fallback_us", "AI 매도 판단 실패 → 규칙 대체(US)", r"falling back to rule-based decision"),
    ("fallback_oneil_err", "미국 대체 규칙 오류 → 옛 +10% 규칙", r"O'Neil fallback error"),
    ("codex_fast_fallback", "빠른 매도 판단 실패 → 기본 경로", r"Codex Fast sell (?:parse failed|unavailable)"),
    ("kis_rate_limit", "KIS 초당 호출 한도 초과(EGW00201)", r"EGW00201|초당 거래건수"),
    ("kis_retry", "KIS 조회 재시도", r"Inquiry (?:failed|error), retrying"),
    ("kis_token", "KIS 토큰 발급 실패", r"Token request failed|Authentication failed: HTTP"),
    ("oauth_api", "ChatGPT 응답 오류(400/401/429 등)", r"ChatGPT API error \(\d+\)"),
    ("oauth_refresh", "ChatGPT 로그인 갱신 실패", r"Token refresh failed"),
    ("oauth_proxy", "ChatGPT 프록시 시작 실패 → 표준 API", r"OAuth proxy (?:failed to start|setup error)"),
    ("oauth_passthrough", "Codex 경유 오류", r"Codex passthrough (?:upstream error|connection failed|authentication)"),
    ("oauth_alert", "OAuth 상태 경보", r"\[oauth-health\] ALERT"),
    ("screen_878", "스크리닝: 전일 종가 아래 제외(#878)", r"\[SCREENING-FILTER\].*close_below_previous_close"),
    ("f2_facts", "미국 자본잠식 F2 사실 블록(#882)", r"\[NEG_EQUITY_F2\] \S+ equity="),
    ("f2_missing", "미국 F2 사실 조회 실패·시간 초과", r"\[NEG_EQUITY_F2\].*(?:facts unavailable|timed out)"),
    ("stale_879", "구독자 오래된 신호 건너뜀(#879)", r"\[STALE_SIGNAL\] skipped"),
    ("ms_relaxed", "초분할 최소 점수 5점 적용", r"\[MICRO_SPLIT_SCORE\].*min_score \S+->"),
    ("ms_no_plan", "초분할 계획 불가(진입 건너뜀·1슬롯)", r"plan unavailable at order time|unavailable, legacy full slot"),
    ("cooldown", "재매수 쿨다운 차단", r"\[REENTRY_COOLDOWN\]\[LIVE\]"),
    ("slots_full", "보유 한도로 매수 불가", r"Holdings already at maximum"),
    ("buy_failed", "매수 주문 실패", r"Purchase failed"),
    ("runner_block", "주도주 매도 보류(로그)", r"\[RUNNER_HOLD\] blocked sell"),
    ("tq_no_guarantee", "트리거 보장 제외(로그)", r"\[TRIGGER_QUALITY\].*no guaranteed pick"),
)
_LOG_RX = [(key, label, re.compile(rx)) for key, label, rx in LOG_PATTERNS]
_LOG_PREFILTER = re.compile("|".join(f"(?:{rx})" for _, _, rx in LOG_PATTERNS))
_DATED_NAME = re.compile(r"(\d{8})\.log(?:\.gz)?$")
_LEGACY_SELL = re.compile(r"Return over 10%|Return exceeds 10%|수익률 10% 이상|Holding 30\+ days|Holding 60\+ days|"
                          r"Target price achieved|Long-term investment loss cleanup")


# ---------------------------------------------------------------- small helpers

def _scenario(raw) -> dict:
    return WR._scenario(raw)


def _f(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _day(value) -> str:
    return str(value or "")[:10]


def _p(value, digits=1) -> str:
    return "-" if value is None else f"{value:+.{digits}f}%"


def _pp(value) -> str:
    return "-" if value is None else f"{value:+.2f}%p"


def _avg(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def _top(counter: Counter, n=4) -> str:
    return ", ".join(f"{k} {v}건" for k, v in counter.most_common(n)) or "없음"


def _clip(text, limit=50) -> str:
    return WR._clip(text, limit)


def event_day(event) -> str:
    """KST calendar day of an event (spool timestamps are UTC)."""
    stamp = str(event.get("timestamp") or "")
    try:
        moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return stamp[:10]
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(KST).date().isoformat()


def connect_readonly(path) -> sqlite3.Connection:
    conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------- window and sources

@dataclass
class Window:
    start: date
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def has(self, day: str | None) -> bool:
        return bool(day) and self.start.isoformat() <= day[:10] <= self.end.isoformat()

    @property
    def prior(self) -> "Window":
        return Window(self.start - timedelta(days=self.days), self.start - timedelta(days=1))


def load_events(path: Path, window: Window) -> dict[str, list[dict]]:
    """Events of the window grouped by type (KST day); a missing spool gives {}."""
    out: dict[str, list[dict]] = defaultdict(list)
    if not path.exists():
        return out
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and window.has(event_day(event)):
                out[str(event.get("event_type"))].append(event)
    return out


def _attrs(event) -> dict:
    value = event.get("attributes")
    return value if isinstance(value, dict) else {}


def _log_files(root: Path, window: Window) -> list[Path]:
    floor = datetime.combine(window.start - timedelta(days=1), datetime.min.time()).timestamp()
    files = []
    for folder in (root, root / "logs", root / "prism-us", root / "prism-us" / "logs"):
        if not folder.is_dir():
            continue
        for path in sorted(folder.iterdir()):
            name = path.name
            if not path.is_file() or not (name.endswith(".log") or name.endswith(".log.gz")):
                continue
            if name.startswith(SKIP_LOG_PREFIXES):
                continue
            dated = _DATED_NAME.search(name)
            if dated:
                day = f"{dated[1][:4]}-{dated[1][4:6]}-{dated[1][6:]}"
                lo, hi = (window.start - timedelta(days=1)).isoformat(), (window.end + timedelta(days=1)).isoformat()
                if not lo <= day <= hi:
                    continue
            elif path.stat().st_mtime < floor:
                continue
            files.append(path)
    return files


def scan_logs(root: Path, window: Window) -> dict:
    """Pattern counts inside the window. Undated files need line timestamps; lines before any stamp are skipped."""
    counts: Counter = Counter()
    by_file: dict[str, Counter] = defaultdict(Counter)
    filtered_878 = 0
    status_codes: Counter = Counter()
    files = _log_files(root, window)
    skipped_unstamped = 0
    for path in files:
        dated = _DATED_NAME.search(path.name)
        file_day = f"{dated[1][:4]}-{dated[1][4:6]}-{dated[1][6:]}" if dated else None
        current = file_day
        opener = gzip.open if path.name.endswith(".gz") else open
        with opener(path, "rt", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if len(line) > 10 and line[4] == "-" and line[7] == "-" and line[:4].isdigit():
                    current = line[:10]
                if not _LOG_PREFILTER.search(line):
                    continue
                if current is None:
                    skipped_unstamped += 1
                    continue
                if not window.has(current):
                    continue
                for key, _, rx in _LOG_RX:
                    if rx.search(line):
                        counts[key] += 1
                        by_file[key][path.name] += 1
                        if key == "screen_878":
                            match = re.search(r"rejected=(\d+)", line)
                            filtered_878 += int(match[1]) if match else 0
                        elif key == "oauth_api":
                            match = re.search(r"ChatGPT API error \((\d+)\)", line)
                            status_codes[match[1] if match else "?"] += 1
    return {"counts": counts, "by_file": by_file, "files": len(files), "filtered_878": filtered_878,
            "status_codes": status_codes, "unstamped": skipped_unstamped}


def load_reentry(runtime: Path, market: str) -> dict | None:
    path = runtime / f"reentry_v3_state_{market.lower()}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict):
                rows.append(item)
    return rows


def load_b3_events(path: Path, window: Window) -> Counter:
    if not path.exists():
        return Counter()
    try:
        conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
    except sqlite3.Error:
        return Counter()
    try:
        rows = conn.execute(B3_EVENTS_SQL, (window.start.isoformat(), window.end.isoformat())).fetchall()
    except sqlite3.Error:
        rows = []
    finally:
        conn.close()
    return Counter(kind for kind, _ in rows)


# ---------------------------------------------------------------- prices

class PriceBook:
    """Cached, capped daily bars [{date, high, close}] per (market, ticker)."""

    def __init__(self, fetcher, end: date, budget: int):
        self.fetcher, self.end, self.budget = fetcher, end, budget
        self.cache: dict[tuple, tuple[str, list]] = {}
        self.calls: Counter = Counter()
        self.denied: Counter = Counter()

    def bars(self, market, ticker, start) -> list[dict]:
        key = (market, str(ticker))
        have = self.cache.get(key)
        if have and have[0] <= start:
            return [b for b in have[1] if b["date"] >= start]
        if self.calls[market] >= self.budget:
            self.denied[market] += 1
            return [b for b in have[1] if b["date"] >= start] if have else []
        self.calls[market] += 1
        data = self.fetcher(market, str(ticker), start, self.end.isoformat()) or []
        self.cache[key] = (start, data)
        return data

    def fetch(self, market, ticker, start, _end=None):
        return self.bars(market, ticker, start)


def later_move(book: PriceBook, market, ticker, day, ref):
    """(latest close return %, best high return % after the day) from ref, or (None, None)."""
    ref = _f(ref)
    if not ref or ref <= 0 or not day:
        return None, None
    bars = [b for b in book.bars(market, ticker, day[:10]) if b["date"] >= day[:10]]
    if not bars:
        return None, None
    after = [b["high"] for b in bars if b["date"] > day[:10]]
    best = (max(after) / ref - 1) * 100 if after else None
    return (bars[-1]["close"] / ref - 1) * 100, best


# ---------------------------------------------------------------- DB loading

@dataclass
class Item:
    trade: WR.Trade
    scenario: dict
    row_id: int | None = None


@dataclass
class MarketData:
    market: str
    items: list[Item] = field(default_factory=list)
    watch: list[dict] = field(default_factory=list)
    prior_items: list[Item] = field(default_factory=list)
    analysis: WR.Analysis | None = None
    notes: list[str] = field(default_factory=list)


def _table_exists(conn, name) -> bool:
    return conn.execute(WR.TABLE_EXISTS, (name,)).fetchone() is not None


def load_items(conn, market, cutoff) -> list[Item]:
    """Open holdings plus history entered/exited since cutoff; one item per (ticker, buy day) across accounts."""
    sql = WR.SQL[market]
    pairs = [(row, False) for row in conn.execute(sql["holdings"]).fetchall()]
    pairs += [(row, True) for row in conn.execute(sql["history"], (cutoff, cutoff)).fetchall()]
    seen, items = set(), []
    for row, closed in pairs:
        key = (row["ticker"], _day(row["buy_date"]))
        if key in seen:
            continue
        seen.add(key)
        trade = WR._build_trade(market, row, closed, BOOK_SLOTS)
        if trade is not None:
            items.append(Item(trade, _scenario(row["scenario"]), row["id"]))
    return items


def load_watch(conn, market, window: Window) -> list[dict]:
    if not _table_exists(conn, WATCH_TABLE[market]):
        return []
    rows = conn.execute(WATCH_SQL[market], (window.start.isoformat(), window.end.isoformat())).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["scenario"] = _scenario(item.get("scenario"))
        if str(item.get("decision")) in {"Already holding", "보유 중"}:
            continue                                  # held tickers re-analyzed with score 0, not a decision
        out.append(item)
    return out


def load_market(conn, market, window: Window, book: PriceBook, max_calls: int) -> MarketData:
    data = MarketData(market)
    if not _table_exists(conn, WR.HOLDINGS_TABLE[market]):
        data.notes.append(f"{market} 보유 테이블이 없어 건너뜁니다.")
        return data
    data.items = load_items(conn, market, window.start.isoformat())
    data.prior_items = load_items(conn, market, window.prior.start.isoformat())
    data.watch = load_watch(conn, market, window)
    # weeks such that the weekly report's cutoff equals the window start
    data.analysis = WR.analyze(conn, market, window.end, (window.days - 1) / 7, book.fetch, max_calls)
    conn.row_factory = sqlite3.Row
    best = {(t.ticker, t.buy_date): t.mfe for t in data.analysis.trades}
    for item in data.items + data.prior_items:          # max favorable excursion from the weekly report
        item.trade.mfe = best.get((item.trade.ticker, item.trade.buy_date))
    return data


def micro(item: Item) -> dict | None:
    return WR.micro_split_record(item.scenario)


def legs(item: Item) -> list[dict]:
    block = micro(item)
    return list(block.get("legs") or []) if block else []


def _leg_return(item: Item, leg) -> float | None:
    price = _f(leg.get("price"))
    return (item.trade.last / price - 1) * 100 if price and item.trade.last else None


def cohort(data: MarketData, window: Window) -> list[Item]:
    return [i for i in data.items if window.has(i.trade.buy_date)]


def touched(data: MarketData, window: Window) -> list[Item]:
    """Entered, exited or still open during the window."""
    return [i for i in data.items if i.trade.is_open or window.has(i.trade.buy_date) or window.has(i.trade.sell_date)]


# ---------------------------------------------------------------- sections

def section_micro_split(datas, window, events) -> str:
    lines = ["1) 초분할 첫 매수 비중 · 최소 점수 5점 · 7종목 이상 6점 규칙"]
    for data in datas:
        new = cohort(data, window)
        split = [i for i in new if micro(i)]
        lines.append(f"[{data.market}] 신규 진입 {len(new)}건 중 초분할(처음에 일부만 매수) {len(split)}건, "
                     f"1슬롯 일괄 {len(new) - len(split)}건입니다.")
        first = [float(legs(i)[0].get("allocation") or 0) * 100 for i in split if legs(i)]
        if first:
            actual = sum(i.trade.contribution for i in split)
            full = sum(((i.trade.last / float(legs(i)[0]["price"]) - 1) * 100) / BOOK_SLOTS
                       for i in split if legs(i) and _f(legs(i)[0].get("price")))
            lines.append(f"- 첫 매수 비중 평균 {_avg(first):.0f}%(최소 {min(first):.0f}%, 최대 {max(first):.0f}%). "
                         f"계좌 기여 {actual:+.2f}%p, 같은 종목을 처음부터 1슬롯 샀다면 {full:+.2f}%p"
                         f"(차이 {actual - full:+.2f}%p, 손실 방어는 +, 수익 축소는 −).")
        relaxed = [i for i in new if (i.scenario.get("_entry_score_policy") or {}).get("mode") == "micro_split_floor"
                   and _f(i.scenario.get("buy_score")) is not None
                   and _f(i.scenario.get("buy_score")) < _f((i.scenario.get("_entry_score_policy") or {})
                                                           .get("legacy_required_score") or 99)]
        if relaxed:
            lines.append(f"- 최소 점수 5점 덕분에 들어간 진입(예전 기준 미달) {len(relaxed)}건: 평균 "
                         f"{_p(_avg([i.trade.ret for i in relaxed]))}, 계좌 기여 "
                         f"{sum(i.trade.contribution for i in relaxed):+.2f}%p, +20% 도달 "
                         f"{sum(1 for i in relaxed if (i.trade.mfe or i.trade.ret) >= LEADER_PCT)}건.")
        else:
            lines.append("- 최소 점수 5점 덕분에 들어간 진입(예전 기준 미달)은 없습니다.")
        crowded_skip = [w for w in data.watch if _slots(w["scenario"]) >= 7 and _f(w.get("buy_score")) == 5]
        crowded_entry = [i for i in new if _slots(i.scenario) >= 7 and (_f(i.scenario.get("buy_score")) or 99) < 6]
        lines.append(f"- 보유 7종목 이상일 때 5점 후보 보류 {len(crowded_skip)}건, 같은 상황에서 6점 미만 진입 "
                     f"{len(crowded_entry)}건" + (f"(규칙 위반 의심: {', '.join(i.trade.label for i in crowded_entry[:3])})"
                                                 if crowded_entry else "(규칙 위반 없음)") + ".")
    counts = events.get("_logs", {}).get("counts", Counter())
    lines.append(f"- 이상 징후: 초분할 계획을 못 세워 건너뛰거나 1슬롯으로 산 경우(로그) {counts['ms_no_plan']}건, "
                 f"5점 하한 적용 로그 {counts['ms_relaxed']}건.")
    return "\n".join(lines)


def _slots(scenario) -> int:
    value = (scenario.get("_decision_context") or {}).get("slots_used") if isinstance(scenario, dict) else None
    number = _f(value)
    return int(number) if number is not None else -1


def section_adds(datas, window, events, book) -> str:
    lines = ["2) 증액 시나리오 · 가속 구간 두 번째 증액 · 위험 한도"]
    for data in datas:
        market = data.market
        mine = [e for e in events.get("micro_split.add_planned", []) if e.get("market") == market]
        status = Counter(str(_attrs(e).get("plan_status")) for e in mine)
        dropped = Counter(r for e in mine for r in _attrs(e).get("dropped") or [])
        executed = [e for e in events.get("micro_split.add_executed", []) if e.get("market") == market]
        accel = [e for e in executed if _attrs(e).get("rail") == "ACCELERATION"]
        failed = [e for e in executed if not _attrs(e).get("broker_success")]
        qualified = [e for e in events.get("micro_split.add_plan_qualified", []) if e.get("market") == market]
        clipped = [e for e in qualified if _attrs(e).get("risk_clipped")]
        invalid = [e for e in events.get("micro_split.add_plan_invalidated", []) if e.get("market") == market]
        blocked = [e for e in events.get("micro_split.add_blocked", []) if e.get("market") == market]
        lines.append(f"[{market}] 증액 계획 {len(mine)}건({_top(status, 3)}), 버린 시나리오 사유: {_top(dropped, 3)}.")
        lines.append(f"- 증액 주문 {len(executed)}건(주문 실패·미체결 {len(failed)}건), 그중 가속 구간 두 번째 증액 "
                     f"{len(accel)}건, 계획 취소 {len(invalid)}건.")
        for event in accel[:3]:
            a, acc = _attrs(event), _attrs(event).get("acceleration") or {}
            lines.append(f"  · {event.get('ticker')} 두 번째 증액: 최초가 대비 {_p(_f(acc.get('gain_pct')))}, "
                         f"거래량 {acc.get('volume_pace') or '-'}배, 매수가 {a.get('add_price')}, "
                         f"비중 {_pct(a.get('allocation_before'))}→{_pct(a.get('allocation_after'))}")
        add_items = [(i, leg) for i in touched(data, window) for leg in legs(i)
                     if leg.get("kind") == "ADD" and window.has(_day(leg.get("at")))]
        if add_items:
            total = sum(float(leg.get("allocation") or 0) * (_leg_return(i, leg) or 0) / BOOK_SLOTS
                        for i, leg in add_items)
            acc_total = sum(float(leg.get("allocation") or 0) * (_leg_return(i, leg) or 0) / BOOK_SLOTS
                            for i, leg in add_items if leg.get("rail") == "ACCELERATION")
            names = {i.trade.ticker for i, _ in add_items}
            losers = [i for i in {id(i): i for i, _ in add_items}.values() if i.trade.ret < 0]
            lines.append(f"- 증액분 손익(장부 기준, 증액가→현재가·청산가): 계좌 기여 {total:+.2f}%p"
                         f"(가속 증액분 {acc_total:+.2f}%p). 증액이 없었다면 그만큼 덜 벌었거나 덜 잃었습니다. "
                         f"증액 종목 {len(names)}개 중 현재 손실 {len(losers)}개.")
        else:
            lines.append("- 장부에 기록된 증액이 없습니다.")
        lines.append(f"- 위험 한도: 한도에 걸려 줄어든 증액 {len(clipped)}건, 조건은 맞았지만 안전장치로 막힌 증액 "
                     f"{len(blocked)}건({_top(Counter(str(_attrs(e).get('block')) for e in blocked), 3)}).")
        moves = []
        for event in _dedupe(blocked, ("ticker", "scenario_id")):
            latest, _ = later_move(book, market, event.get("ticker"), event_day(event), _attrs(event).get("price"))
            if latest is not None:
                moves.append(latest)
        if moves:
            lines.append(f"  · 막힌 증액의 그 뒤 가격: 평균 {_p(_avg(moves))}(오른 경우 {sum(m > 0 for m in moves)}/"
                         f"{len(moves)}건, 오르면 막은 것이 기회 손실).")
    b3 = events.get("_b3", Counter())
    if b3:
        lines.append(f"- 증액 집행기 감사 기록(교차 확인): 조건 충족 {b3['add_plan.qualified']}건, 집행 "
                     f"{b3['add_plan.executed']}건, 안전장치 차단 {b3['add_plan.rail_blocked']}건, 취소 "
                     f"{b3['add_plan.invalidated']}건.")
    return "\n".join(lines)


def _pct(value) -> str:
    number = _f(value)
    return "-" if number is None else f"{number * 100:.0f}%"


def _dedupe(events, keys, day=True):
    seen, out = set(), []
    for event in events:
        attrs = _attrs(event)
        key = tuple(event.get(k) if k in ("ticker", "market") else attrs.get(k) for k in keys)
        key = key + ((event_day(event),) if day else ())
        if key not in seen:
            seen.add(key)
            out.append(event)
    return out


def section_conviction(datas, window) -> str:
    lines = ["3) 상위 셋업 가중(8점 이상 + 강한 트리거면 첫 비중 +20%p)"]
    for data in datas:
        tilted = [i for i in touched(data, window) if (micro(i) or {}).get("conviction_tilt")
                  and window.has(i.trade.buy_date)]
        if not tilted:
            lines.append(f"[{data.market}] 가중이 적용된 진입이 없습니다.")
            continue
        rows, total = [], 0.0
        for item in tilted:
            tilt = micro(item)["conviction_tilt"]
            extra = (_f(tilt.get("tilted")) or 0) - (_f(tilt.get("base")) or 0)
            first = legs(item)[0] if legs(item) else {}
            move = _leg_return(item, first)
            diff = extra * (move or 0) / BOOK_SLOTS
            total += diff
            rows.append((diff, f"  · {item.trade.label}: 기본 {_pct(tilt.get('base'))}→가중 {_pct(tilt.get('tilted'))}, "
                               f"첫 매수가 대비 {_p(move)}, 가중분 손익 {diff:+.2f}%p"))
        wins = sum(1 for d, _ in rows if d > 0)
        lines.append(f"[{data.market}] 가중 진입 {len(tilted)}건, 가중분(기본 비중 대비 더 산 몫) 손익 합계 "
                     f"{total:+.2f}%p(이득 {wins}건, 손해 {len(rows) - wins}건).")
        lines += [text for _, text in sorted(rows, key=lambda r: -abs(r[0]))[:3]]
    return "\n".join(lines)


def section_runner(datas, window, events, book) -> str:
    lines = ["4) 주도주 보유 규칙(+20% 간 종목은 50일선·본전 아래 마감 전까지 보유)"]
    for data in datas:
        market = data.market

        def mine(kind):
            return [e for e in events.get(kind, []) if e.get("market") == market]

        detected = {e.get("ticker") for e in mine("runner.detected")}
        excluded = {e.get("ticker") for e in mine("runner.spike_excluded")}
        resets = mine("runner.stop_reset_to_entry") + mine("runner.stop_raised")
        forced = mine("runner.exit_forced")
        ignored = mine("runner.stop_adjust_ignored")
        blocked = _dedupe(mine("runner.sell_blocked"), ("ticker", "source"))
        lines.append(f"[{market}] 주도주 판정 {len(detected)}종목, 급등형이라 제외 {len(excluded)}종목, 손절가를 "
                     f"매수가로 재설정 {len(resets)}건, AI 손절가 변경 무시 {len(ignored)}건, 규칙 매도 {len(forced)}건.")
        by_source = Counter(str(_attrs(e).get("source")) for e in blocked)
        by_code = Counter(str(_attrs(e).get("code")) for e in blocked)
        lines.append(f"- 매도 보류 {len(blocked)}건(종목·날짜·경로별 1건): 경로 {_top(by_source, 3)} / 사유 {_top(by_code, 3)}.")
        sold = {i.trade.ticker: i for i in data.items if i.trade.sell_date}
        moves, missing = [], 0
        for event in blocked:
            attrs = _attrs(event)
            price = _f(attrs.get("current_price"))
            if price is None:
                missing += 1
                continue
            latest, _ = later_move(book, market, event.get("ticker"), event_day(event), price)
            exit_item = sold.get(event.get("ticker"))
            if exit_item and exit_item.trade.sell_date >= event_day(event):
                latest = (exit_item.trade.last / price - 1) * 100
            if latest is not None:
                moves.append((latest, event))
        if moves:
            helped = sum(1 for m, _ in moves if m > 0)
            lines.append(f"- 보류 시점 가격 대비 지금(청산했으면 청산가): 평균 {_p(_avg([m for m, _ in moves]))}, "
                         f"보류가 도움 {helped}건 / 손해 {len(moves) - helped}건.")
            for move, event in sorted(moves, key=lambda m: m[0])[:2]:
                lines.append(f"  · {event_day(event)[5:]} {event.get('ticker')} {_attrs(event).get('code')} 보류 → "
                             f"{_p(move)}")
        if missing:
            lines.append(f"- 보류 시점 가격이 없는 기록 {missing}건(10/4 이전 형식)은 반사실 계산에서 뺐습니다.")
        for event in forced[:3]:
            attrs = _attrs(event)
            lines.append(f"  · 규칙 매도 {event_day(event)[5:]} {event.get('ticker')} {attrs.get('code')}: 종가 "
                         f"{attrs.get('close')}, 매수가 대비 {_p(_f(attrs.get('gain_now_pct')))}")
        runners = [i for i in data.items if (i.scenario.get("runner") or {}).get("status") == "RUNNER"
                   and (i.trade.is_open or window.has(i.trade.sell_date))]
        if runners:
            gave = [f"{i.trade.label} 최고 {_p(i.trade.mfe)}→{'현재' if i.trade.is_open else '청산'} {_p(i.trade.ret)}"
                    for i in sorted(runners, key=lambda i: -(i.trade.mfe or 0))[:3]]
            lines.append(f"- 주도주 {len(runners)}종목: " + "; ".join(gave))
    return "\n".join(lines)


def section_reentry(datas, window, runtime: Path, events) -> str:
    lines = ["5) 재진입 신호(손절·보류 종목이 기준 가격을 회복하면 AI 재점검 후 다시 매수)"]
    for data in datas:
        market = data.market
        state = load_reentry(runtime, market)
        if state is None:
            lines.append(f"[{market}] 상태 파일이 없어 평가할 수 없습니다.")
            continue
        watches = state.get("watches") or []
        active = sum(1 for w in watches if w.get("status") == "ACTIVE")
        evaluated, signals, verdicts, live = 0, [], Counter(), Counter()
        reasons, approved_rets, rejected_rets = Counter(), [], []
        for watch in watches:
            rechecks = watch.get("rechecks") or {}
            by_day = {}
            for day, decision in (watch.get("decisions") or {}).items():
                if not window.has(day):
                    continue
                evaluated += 1
                if decision.get("trigger"):
                    rc = rechecks.get(decision.get("event_id")) or {}
                    by_day[day] = rc
                    signals.append((day, watch, decision, rc))
                    verdicts[str(rc.get("status") or "PENDING")] += 1
            for day, info in (watch.get("live") or {}).items():
                if window.has(day):
                    live[str(info.get("result") or info.get("status"))] += 1
            camp = ((watch.get("ledger") or {}).get("campaigns") or {}).get("L97") or {}
            for att in camp.get("attempts") or []:
                if window.has(att.get("date")) and att.get("status") == "CLOSED":
                    approved_flag = (by_day.get(att.get("date")) or {}).get("approved")
                    ret = _f(att.get("ret"))           # ledger stores a fraction
                    (approved_rets if approved_flag else rejected_rets).append(None if ret is None else ret * 100)
        for event in events.get("reentry_v3.shadow_recheck", []):
            if event.get("market") == market and _attrs(event).get("approved") is False:
                reasons[_clip(_attrs(event).get("rejection_reason") or "사유 없음", 30)] += 1
        approved = sum(1 for _, _, _, rc in signals if rc.get("approved"))
        lines.append(f"[{market}] 감시 {len(watches)}종목(진행 중 {active}), 기간 내 판단 {evaluated}회, 신호 {len(signals)}건, "
                     f"AI 재점검 결과 {_top(verdicts, 4)}, 승인 {approved}건.")
        lines.append(f"- 주문 단계: {_top(live, 5)}.")
        real = [i for i in data.items if (i.scenario.get("reentry") or {}).get("version") == "reentry_v3"
                and window.has(i.trade.buy_date)]
        if real:
            lines.append(f"- 실제 재진입 {len(real)}건: 평균 {_p(_avg([i.trade.ret for i in real]))}, 계좌 기여 "
                         f"{sum(i.trade.contribution for i in real):+.2f}%p, 손절 "
                         f"{sum(1 for i in real if i.trade.exit_kind == 'stop')}건, +20% 도달 "
                         f"{sum(1 for i in real if (i.trade.mfe or i.trade.ret) >= LEADER_PCT)}건.")
        else:
            lines.append("- 실제 재진입 매수는 없습니다.")
        if approved_rets or rejected_rets:
            lines.append(f"- 장부상 가상 결과(같은 규칙으로 샀다고 가정): AI 승인 신호 평균 {_p(_avg(approved_rets))}"
                         f"({len(approved_rets)}건), AI가 거절·미평가한 신호 평균 {_p(_avg(rejected_rets))}"
                         f"({len(rejected_rets)}건). 거절 쪽이 더 좋으면 재점검이 기회를 막은 것입니다.")
        if reasons:
            lines.append(f"- AI 거절 사유 상위: {_top(reasons, 3)}")
    return "\n".join(lines)


def section_trigger_quality(datas, window, events, book) -> str:
    lines = ["6) 트리거 품질 우선순위(약한 발굴 조건은 1등 보장 대신 점수 경쟁)"]
    for data in datas:
        market = data.market
        batches = [e for e in events.get("trigger_quality.selection", []) if e.get("market") == market]
        excluded = Counter(t for e in batches for t in _attrs(e).get("excluded_triggers") or [])
        displaced = [(e, c) for e in batches for c in _attrs(e).get("displaced") or []]
        picks = [(e, c) for e in batches for c in _attrs(e).get("fill_picks") or []]
        lines.append(f"[{market}] 기록된 선발 {len(batches)}회, 보장에서 빠진 트리거: {_top(excluded, 3)}.")

        def judge(rows):
            latest, best, big = [], [], 0
            for event, cand in rows:
                day = str(_attrs(event).get("trade_date") or event_day(event))
                day = f"{day[:4]}-{day[4:6]}-{day[6:8]}" if len(day) == 8 and day.isdigit() else day[:10]
                now, top = later_move(book, market, cand.get("ticker"), day, cand.get("reference_price"))
                if now is not None:
                    latest.append(now)
                if top is not None:
                    best.append(top)
                    big += top >= LEADER_PCT
            return latest, best, big

        d_now, d_best, d_big = judge(displaced)
        p_now, p_best, p_big = judge(picks)
        lines.append(f"- 자리를 잃은 후보 {len(displaced)}건: 그 뒤 현재가 평균 {_p(_avg(d_now))}, 최고 평균 "
                     f"{_p(_avg(d_best))}, +20% 도달 {d_big}건.")
        lines.append(f"- 대신 점수 경쟁으로 뽑힌 후보 {len(picks)}건: 현재가 평균 {_p(_avg(p_now))}, 최고 평균 "
                     f"{_p(_avg(p_best))}, +20% 도달 {p_big}건. (선발 기준가 대비, 손절 미반영)")
        strong = STRONG_TRIGGERS[market]
        now_entries = cohort(data, window)
        before = [i for i in data.prior_items if window.prior.has(i.trade.buy_date)]

        def share(items):
            return (sum(1 for i in items if i.trade.trigger in strong), len(items))

        a, b = share(now_entries), share(before)
        lines.append(f"- 강한 트리거 진입 비중: 이번 {a[0]}/{a[1]}건, 직전 같은 기간 {b[0]}/{b[1]}건.")
    return "\n".join(lines)


def classify_skip(row) -> tuple[str, bool]:
    """(category, reason_missing) of one watchlist row."""
    text = str(row.get("skip_reason") or "")
    scenario = row.get("scenario") or {}
    rejection = str(scenario.get("rejection_reason") or "").strip()
    if "결정론적 게이트" in text or "Deterministic gate" in text:
        return "결정론 게이트", False
    if "점수 부족" in text or "Insufficient score" in text:
        return "점수 부족", False
    if "섹터 집중" in text or "Sector concentration" in text:
        return "업종 한도", False
    if "cooldown" in text.lower():
        return "재매수 쿨다운", False
    if "AI 판단" in text or "AI judgment" in text:
        return "AI 거절", not rejection and " / " not in text
    return ("기타", not text.strip() and not rejection)


def section_buy(datas, window, events) -> str:
    lines = ["7) 매수 판단(채점표·초분할 문구) · 스크리닝 필터 · 미국 자본잠식 F2 · 구독자 오래된 신호"]
    for data in datas:
        new = cohort(data, window)
        scores = Counter()
        for value in [_f(w.get("buy_score")) for w in data.watch] + [_f(i.scenario.get("buy_score")) for i in new]:
            if value is None:
                continue
            scores["≤3" if value <= 3 else "8+" if value >= 8 else str(int(value))] += 1
        dist = ", ".join(f"{k}점 {scores[k]}" for k in ("≤3", "4", "5", "6", "7", "8+"))
        cats, missing = Counter(), []
        batches = defaultdict(lambda: [0, 0])     # day -> [held back, entered]
        for row in data.watch:
            category, absent = classify_skip(row)
            cats[category] += 1
            if absent:
                missing.append(row)
            batches[_day(row.get("analyzed_date"))][0] += 1
        for item in new:
            batches[item.trade.buy_date][1] += 1
        total = len(data.watch) + len(new)
        lines.append(f"[{data.market}] 분석 {total}건 중 진입 {len(new)}건({len(new) / total:.0%}), 점수 분포: {dist}."
                     if total else f"[{data.market}] 기간 내 분석 기록이 없습니다.")
        if data.watch:
            lines.append(f"- 보류 사유: {_top(cats, 5)}. 사유 글이 없는 보류 {len(missing)}건"
                         + (f"({', '.join(str(r['ticker']) for r in missing[:4])})" if missing else "") + ".")
        busiest = sorted(batches.items(), key=lambda kv: -(kv[1][0] + kv[1][1]))[:3]
        if busiest:
            lines.append("- 분석이 많았던 날(보류/진입): " + ", ".join(f"{d[5:]} {s}/{e}" for d, (s, e) in busiest))
        prior = [i for i in data.prior_items if window.prior.has(i.trade.buy_date)]
        lines.append(f"- 직전 같은 기간 진입 {len(prior)}건 → 이번 {len(new)}건.")
        if data.analysis is not None:
            missed = [m for m in data.analysis.missed if m.kind == "미진입"]
            if missed:
                lines.append(f"- 보류했는데 이후 +{WR.BIG_PCT:.0f}% 이상 간 종목 {len(missed)}개: "
                             + "; ".join(f"{m.name}({m.ticker}) {_p(m.gain, 0)} — {_clip(m.reason, 40)}"
                                         for m in missed[:3]))
    logs = events.get("_logs", {})
    counts = logs.get("counts", Counter())
    lines.append(f"- 스크리닝 필터(#878): 전일 종가 아래 제외 로그 {counts['screen_878']}회, 제외 종목 합계 "
                 f"{logs.get('filtered_878', 0)}건.")
    lines.append(f"- 미국 자본잠식 F2(#882): 사실 블록 첨부 {counts['f2_facts']}회, 조회 실패·시간 초과 {counts['f2_missing']}회.")
    lines.append(f"- 구독자 오래된 신호 건너뜀(#879): {counts['stale_879']}건"
                 " (구독자는 맥미니에서 돌아 이 서버 로그에 없으면 0으로 보입니다. 맥미니 logs/subscriber_*.log 확인).")
    return "\n".join(lines)


def section_errors(datas, events) -> str:
    lines = ["8) 오류·대체 경로"]
    fallbacks = events.get("sell.fallback_used", [])
    for market in [d.market for d in datas]:
        mine = [e for e in fallbacks if e.get("market") == market]
        ten = sum(1 for e in mine if _attrs(e).get("legacy_ten_pct_rule"))
        sells = sum(1 for e in mine if _attrs(e).get("should_sell"))
        legacy_exits = [e for e in events.get("exit.executed", []) if e.get("market") == market and
                        _LEGACY_SELL.search(str((_attrs(e).get("decision_context") or {}).get("sell_reason") or ""))]
        lines.append(f"[{market}] AI 매도 판단 대체 규칙 사용 {len(mine)}회(매도로 이어짐 {sells}회, 옛 +10% 익절 규칙 {ten}회), "
                     f"청산 기록 중 옛 규칙 문구 {len(legacy_exits)}건.")
    rate = events.get("kis.rate_limited", [])
    lines.append(f"- KIS 초당 호출 한도 초과 이벤트 {len(rate)}건(경로 상위: "
                 f"{_top(Counter(str(_attrs(e).get('path')).rsplit('/', 1)[-1] for e in rate), 3)}).")
    counts = events.get("_logs", {}).get("counts", Counter())
    shown = ("fallback_kr", "fallback_us", "fallback_oneil_err", "codex_fast_fallback", "kis_rate_limit", "kis_retry",
             "kis_token", "oauth_api", "oauth_refresh", "oauth_proxy", "oauth_passthrough", "oauth_alert",
             "cooldown", "slots_full", "buy_failed")
    labels = {key: label for key, label, _ in LOG_PATTERNS}
    parts = [f"{labels[k]} {counts[k]}" for k in shown if counts[k]]
    lines.append("- 로그 집계: " + (", ".join(parts) if parts else "해당 오류 로그가 없습니다."))
    codes = events.get("_logs", {}).get("status_codes") or Counter()
    if codes:
        lines.append(f"- ChatGPT 응답 오류 코드별: {_top(codes, 4)} (429는 쿼터·속도 제한, 401은 로그인 문제).")
    errors = Counter(t for t, items in events.items() if not t.startswith("_")
                     for e in items if str(e.get("severity")) in {"ERROR", "CRITICAL"})
    lines.append(f"- 심각도 ERROR 이벤트: {_top(errors, 4)}.")
    delivery = Counter(str(_attrs(e).get("status")) for e in events.get("telegram.delivery_result", []))
    unconfirmed = sum(v for k, v in delivery.items() if k != "ACKNOWLEDGED")
    lines.append(f"- 텔레그램 발송 {sum(delivery.values())}건 중 수신 확인 안 된 발송 {unconfirmed}건.")
    return "\n".join(lines)


def section_north_star(datas, window) -> str:
    lines = [f"9) 북극성 성적표 ({window.start.isoformat()}~{window.end.isoformat()}, {window.days}일)"]
    for data in datas:
        a = data.analysis
        if a is None:
            continue
        lines.append(f"[{data.market}]")
        lines += WR.north_star(a)
        leaders = sorted((t for t in a.cohort if t.mfe is not None and t.mfe >= LEADER_PCT), key=lambda t: -t.mfe)
        for t in leaders[:3]:
            lines.append(f"  · 큰 수익 {t.label}: 최고 {_p(t.mfe)}, {'보유 중' if t.is_open else '청산'} {_p(t.ret)}, "
                         f"비중 {WR._path_text(t)}")
        prior = [i.trade for i in data.prior_items if window.prior.has(i.trade.sell_date)]
        now = a.exits
        lines.append(f"- 직전 같은 기간과 비교(청산 기준): 청산 {len(prior)}→{len(now)}건, 손실 청산 "
                     f"{sum(t.ret < 0 for t in prior)}→{sum(t.ret < 0 for t in now)}건, 손절 규칙 "
                     f"{sum(t.exit_kind == 'stop' for t in prior)}→{sum(t.exit_kind == 'stop' for t in now)}건, 계좌 기여 "
                     f"{sum(t.contribution for t in prior):+.2f}→{sum(t.contribution for t in now):+.2f}%p.")
    return "\n".join(lines)


def section_sources(datas, events, book, logs, window) -> str:
    kinds = sum(len(v) for k, v in events.items() if not k.startswith("_"))
    lines = ["0) 근거 자료 상태",
             f"- 관측 이벤트 {kinds}건(종류 {sum(1 for k in events if not k.startswith('_'))}개), 로그 파일 "
             f"{logs.get('files', 0)}개 확인(타임스탬프 없는 줄 {logs.get('unstamped', 0)}개 제외)."]
    for data in datas:
        lines.append(f"- [{data.market}] 거래 {len(data.items)}건(보유+기간 내 청산), 보류 기록 {len(data.watch)}건, "
                     f"가격 조회 {book.calls[data.market]}회(한도 초과로 생략 {book.denied[data.market]}회)."
                     + (" " + " ".join(data.notes) if data.notes else ""))
    missing = [name for name in ("trigger_quality.selection", "runner.sell_blocked", "micro_split.add_executed",
                                 "reentry_v3.shadow_run") if not events.get(name)]
    if missing:
        lines.append(f"- 기간 내 기록이 없는 이벤트: {', '.join(missing)} (아직 발생 안 함 또는 배포 전).")
    return "\n".join(lines)


# ---------------------------------------------------------------- orchestration

def priority_prices(book: PriceBook, events, markets):
    """Fetch the counterfactual prices first so the cap never drops them."""
    for kind, ref_key in (("runner.sell_blocked", "current_price"), ("micro_split.add_blocked", "price")):
        for event in events.get(kind, []):
            if event.get("market") in markets and _f(_attrs(event).get(ref_key)):
                book.bars(event["market"], event.get("ticker"), event_day(event))
    for event in events.get("trigger_quality.selection", []):
        if event.get("market") not in markets:
            continue
        day = str(_attrs(event).get("trade_date") or event_day(event))
        day = f"{day[:4]}-{day[4:6]}-{day[6:8]}" if len(day) == 8 and day.isdigit() else day[:10]
        for cand in (_attrs(event).get("displaced") or []) + (_attrs(event).get("fill_picks") or []):
            book.bars(event["market"], cand.get("ticker"), day)


def build_report(*, conn, window: Window, markets, events, logs, runtime: Path, book: PriceBook,
                 max_calls: int) -> list[str]:
    events = dict(events)
    events["_logs"] = logs
    events["_b3"] = load_b3_events(runtime / "b3-ae-shadow.sqlite", window)
    priority_prices(book, events, markets)
    datas = [load_market(conn, market, window, book, max_calls) for market in markets]
    return [
        section_sources(datas, events, book, logs, window),
        section_micro_split(datas, window, events),
        section_adds(datas, window, events, book),
        section_conviction(datas, window),
        section_runner(datas, window, events, book),
        section_reentry(datas, window, runtime, events),
        section_trigger_quality(datas, window, events, book),
        section_buy(datas, window, events),
        section_errors(datas, events),
        section_north_star(datas, window),
    ]


def write_report(out_dir: Path, window: Window, header: str, sections: list[str]) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"two_week_review_{window.start.isoformat()}_{window.end.isoformat()}.txt"
    path.write_text(header + "\n\n" + "\n\n".join(sections) + "\n", encoding="utf-8")
    return path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", help="YYYY-MM-DD (default: end - 13 days)")
    parser.add_argument("--end", help="YYYY-MM-DD (default: today in KST)")
    parser.add_argument("--market", choices=["kr", "us", "both"], default="both")
    parser.add_argument("--db", default=os.getenv("STOCK_TRACKING_DB") or str(ROOT / "stock_tracking_db.sqlite"))
    parser.add_argument("--events", default=os.getenv("PRISM_OBSERVABILITY_SPOOL") or str(ROOT / "logs" / "prism_events.jsonl"))
    parser.add_argument("--runtime", default=str(ROOT / "runtime"))
    parser.add_argument("--log-root", default=str(ROOT))
    parser.add_argument("--out-dir", default=str(ROOT / "logs" / "reviews"))
    parser.add_argument("--max-price-calls", type=int, default=120, help="price lookups per market")
    parser.add_argument("--no-prices", action="store_true", help="skip price lookups (DB and events only)")
    parser.add_argument("--no-logs", action="store_true", help="skip the log scan")
    parser.add_argument("--dry-run", action="store_true", help="print only; no Telegram, no file")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)

    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    end = date.fromisoformat(args.end) if args.end else datetime.now(KST).date()
    start = date.fromisoformat(args.start) if args.start else end - timedelta(days=DEFAULT_DAYS - 1)
    window = Window(start, end)
    markets = list(MARKETS) if args.market == "both" else [args.market.upper()]
    fetcher = (lambda *_: []) if args.no_prices else WR.LivePrices()
    book = PriceBook(fetcher, end, args.max_price_calls)
    events = load_events(Path(args.events), window)
    logs = {"counts": Counter(), "files": 0} if args.no_logs else scan_logs(Path(args.log_root), window)
    conn = connect_readonly(args.db)
    try:
        sections = build_report(conn=conn, window=window, markets=markets, events=events, logs=logs,
                                runtime=Path(args.runtime), book=book, max_calls=args.max_price_calls)
    finally:
        conn.close()
    header = f"[PRISM 2주 점검] {start.isoformat()}~{end.isoformat()} · {'/'.join(markets)}"
    messages = WR.pack_messages(header, sections)
    for text in messages:
        print(text)
        print("-" * 40)
    if args.dry_run:
        return 0
    path = write_report(Path(args.out_dir), window, header, sections)
    print(f"전체 보고서: {path}", file=sys.stderr)
    if os.getenv("PRISM_DISABLE_SIGNAL_PUBLISH") == "1":
        print("PRISM_DISABLE_SIGNAL_PUBLISH=1: 발송을 건너뜁니다.", file=sys.stderr)
        return 0
    return 0 if WR.send_private(messages) else 1


if __name__ == "__main__":
    raise SystemExit(main())
