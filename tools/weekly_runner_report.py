#!/usr/bin/env python3
"""Weekly leader-stock ("주도주") report for the owner's private alert chat (KR and US).

Reads the tracking DB read-only and reviews recent entries and holdings against the
north star in docs/TRADING_CHANGE_REVIEW_HARNESS.md: big-winner capture and staircase
growth, loss and drawdown, trade frequency versus stop drag, and screening precision.

Prices for the max favorable excursion (MFE) come from the repo's existing data paths
(KR: cores.stock_chart, US: prism-us USDataClient) with a hard cap on calls; DART is
never used. Delivery goes only to the private maintenance chat (prism_core.ops_alert),
never to a public channel. ``--dry-run`` prints only.

Usage:
    python tools/weekly_runner_report.py --dry-run
    python tools/weekly_runner_report.py --market kr --weeks 8
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import logging
import os
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(os.getenv("PRISM_REPO_ROOT") or Path(__file__).resolve().parents[1])
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prism_core.micro_split_live import record as micro_split_record  # noqa: E402
from prism_core.micro_split_live import weighted_entry  # noqa: E402
from prism_core.slot_weight import slot_fraction  # noqa: E402

logger = logging.getLogger(__name__)

LEADER_PCT = 20.0          # O'Neil first milestone: a "leader" reached at least +20% after entry
BIG_PCT = 30.0             # a missed winner rose at least +30% after we skipped or sold it
BOOK_SLOTS = 10            # capital is sized as a fixed 10-slot book (MAX_SLOTS in the trackers)
MESSAGE_LIMIT = 3400       # prism_core.ops_alert truncates at 3500 characters
TOP_ROWS = 5
EXIT_LABELS = {"stop": "손절 규칙", "trend_exit": "추세 이탈", None: "매도 판단"}

# Fixed SQL per market: only literals, values go through ? parameters.
SQL = {
    "KR": {
        "holdings": "SELECT id, ticker, company_name, buy_price, buy_date, current_price, scenario, trigger_type "
                    "FROM stock_holdings ORDER BY id",
        "history": "SELECT id, ticker, company_name, buy_price, buy_date, sell_price, sell_date, profit_rate, "
                   "scenario, trigger_type, exit_kind FROM trading_history "
                   "WHERE substr(buy_date, 1, 10) >= ? OR substr(sell_date, 1, 10) >= ? ORDER BY id",
        "watch": "SELECT ticker, company_name, analyzed_date, current_price, buy_score, min_score, skip_reason, "
                 "trigger_type FROM watchlist_history "
                 "WHERE substr(analyzed_date, 1, 10) >= ? AND COALESCE(was_traded, 0) = 0 ORDER BY analyzed_date",
        "hint": "SELECT ticker, MAX(COALESCE(tracked_7d_return, -999), COALESCE(tracked_14d_return, -999), "
                "COALESCE(tracked_30d_return, -999)) FROM analysis_performance_tracker "
                "WHERE substr(analyzed_date, 1, 10) >= ?",
        "decisions": "SELECT ticker, decision_date, sell_reason FROM holding_decisions "
                     "WHERE should_sell = 1 AND substr(decision_date, 1, 10) >= ? ORDER BY decision_date",
    },
    "US": {
        "holdings": "SELECT id, ticker, company_name, buy_price, buy_date, current_price, scenario, trigger_type "
                    "FROM us_stock_holdings ORDER BY id",
        "history": "SELECT id, ticker, company_name, buy_price, buy_date, sell_price, sell_date, profit_rate, "
                   "scenario, trigger_type, exit_kind FROM us_trading_history "
                   "WHERE substr(buy_date, 1, 10) >= ? OR substr(sell_date, 1, 10) >= ? ORDER BY id",
        "watch": "SELECT ticker, company_name, analyzed_date, current_price, buy_score, min_score, skip_reason, "
                 "trigger_type FROM us_watchlist_history "
                 "WHERE substr(analyzed_date, 1, 10) >= ? AND COALESCE(was_traded, 0) = 0 ORDER BY analyzed_date",
        "hint": "SELECT ticker, MAX(COALESCE(return_7d, -999), COALESCE(return_14d, -999), "
                "COALESCE(return_30d, -999)) FROM us_analysis_performance_tracker "
                "WHERE substr(analysis_date, 1, 10) >= ?",
        "decisions": "SELECT ticker, decision_date, sell_reason FROM us_holding_decisions "
                     "WHERE should_sell = 1 AND substr(decision_date, 1, 10) >= ? ORDER BY decision_date",
    },
}
TABLE_EXISTS = "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?"
HOLDINGS_TABLE = {"KR": "stock_holdings", "US": "us_stock_holdings"}


@dataclass
class Trade:
    market: str
    ticker: str
    name: str
    buy_date: str
    sell_date: str | None
    entry: float
    last: float
    ret: float
    fraction: float
    trigger: str
    exit_kind: str | None
    path: list[int]
    reentry: dict
    runner: str | None
    highest: float | None
    max_slots: int = BOOK_SLOTS
    mfe: float | None = None
    exit_reason: str = ""

    @property
    def is_open(self) -> bool:
        return self.sell_date is None

    @property
    def label(self) -> str:
        return f"{self.name}({self.ticker})"

    @property
    def contribution(self) -> float:
        """Account-level return in %p: slot-weighted return over the book size."""
        return self.ret * self.fraction / self.max_slots


@dataclass
class Missed:
    ticker: str
    name: str
    kind: str          # "미진입" or "조기청산"
    when: str
    gain: float
    reason: str


@dataclass
class Analysis:
    market: str
    as_of: date
    weeks: int
    trades: list[Trade] = field(default_factory=list)
    missed: list[Missed] = field(default_factory=list)
    candidates: int = 0
    max_slots: int = BOOK_SLOTS
    regime_cap: int | None = None    # market-based holding cap from the latest scenario
    price_gaps: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def cutoff(self) -> str:
        return (self.as_of - timedelta(days=7 * self.weeks)).isoformat()

    @property
    def week_start(self) -> str:
        return (self.as_of - timedelta(days=7)).isoformat()

    def in_week(self, day: str | None) -> bool:
        return bool(day) and self.week_start < day <= self.as_of.isoformat()

    @property
    def cohort(self) -> list[Trade]:
        """Entries made inside the review window."""
        return [t for t in self.trades if t.buy_date >= self.cutoff]

    @property
    def exits(self) -> list[Trade]:
        """Exits made inside the review window, oldest first."""
        return sorted((t for t in self.trades if t.sell_date and t.sell_date >= self.cutoff),
                      key=lambda t: t.sell_date)


# ---------------------------------------------------------------- loading

def connect_readonly(path: str) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _scenario(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _path(scenario: dict) -> list[int]:
    """Cumulative allocation (percent of one slot) after each micro-split leg; [100] for legacy rows."""
    block = micro_split_record(scenario)
    if block is None:
        return [100]
    total, steps = 0.0, []
    for leg in block.get("legs") or []:
        total += float(leg.get("allocation") or 0)
        steps.append(round(min(total, 1.0) * 100))
    return steps or [100]


def runner_label(runner) -> str | None:
    """Describe scenario["runner"] (runner-hold flags) without depending on its final layout."""
    if not isinstance(runner, dict) or not runner:
        return None
    state = runner.get("state") or runner.get("status")
    if state:
        return f"적용({state})"
    if any(runner.get(key) for key in ("active", "applied", "enabled", "hold")):
        return "적용"
    return "기록만 있음"


def _day(value) -> str:
    return str(value or "")[:10]


def _clip(text, limit=60) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[:limit - 1].rstrip() + "…"


def _build_trade(market, row, closed, max_slots) -> Trade | None:
    scenario = _scenario(row["scenario"])
    block = micro_split_record(scenario)
    entry = float(weighted_entry(block["legs"])) if block else float(row["buy_price"] or 0)
    if entry <= 0:
        return None
    last = float(row["sell_price"]) if closed else float(row["current_price"] or entry)
    ret = float(row["profit_rate"]) if closed else (last / entry - 1) * 100
    highest = scenario.get("highest_price")
    reentry = scenario.get("reentry")
    return Trade(
        market=market, ticker=str(row["ticker"]), name=str(row["company_name"] or row["ticker"]),
        buy_date=_day(row["buy_date"]), sell_date=_day(row["sell_date"]) if closed else None,
        entry=entry, last=last, ret=ret, fraction=slot_fraction(scenario),
        trigger=str(row["trigger_type"] or "기타"), exit_kind=row["exit_kind"] if closed else None,
        path=_path(scenario), reentry=reentry if isinstance(reentry, dict) else {},
        runner=runner_label(scenario.get("runner")),
        highest=float(highest) if isinstance(highest, (int, float)) and highest > 0 else None,
        max_slots=max_slots)


def load_trades(conn, market, cutoff) -> tuple[list[Trade], int]:
    """Open holdings plus history rows entered or exited since cutoff; one row per (ticker, buy date)."""
    sql = SQL[market]
    holdings = conn.execute(sql["holdings"]).fetchall()
    sizes = [_scenario(row["scenario"]).get("max_portfolio_size") for row in holdings]
    sizes = [size for size in sizes if isinstance(size, int) and size > 0]
    regime_cap = sizes[-1] if sizes else None
    pairs = [(row, False) for row in holdings]
    pairs += [(row, True) for row in conn.execute(sql["history"], (cutoff, cutoff)).fetchall()]
    seen, trades = set(), []
    for row, closed in pairs:
        key = (row["ticker"], _day(row["buy_date"]))     # multi-account fan-out repeats the same entry
        if key in seen:
            continue
        seen.add(key)
        trade = _build_trade(market, row, closed, BOOK_SLOTS)
        if trade is not None:
            trades.append(trade)
    return trades, regime_cap


def attach_exit_reasons(conn, market, trades, cutoff):
    """Latest AI sell-decision text within three days before each exit (explains non-stop exits)."""
    reasons = {}
    for ticker, decided, reason in conn.execute(SQL[market]["decisions"], (cutoff,)).fetchall():
        reasons.setdefault(ticker, []).append((_day(decided), _clip(reason)))
    for trade in trades:
        if not trade.sell_date:
            continue
        earliest = (date.fromisoformat(trade.sell_date) - timedelta(days=3)).isoformat()
        near = [text for day, text in reasons.get(trade.ticker, []) if earliest <= day <= trade.sell_date]
        trade.exit_reason = near[-1] if near else ""


# ---------------------------------------------------------------- prices

def _frame_bars(frame, high_col, close_col):
    if frame is None or frame.empty or high_col not in frame.columns:
        return []
    return [{"date": index.strftime("%Y-%m-%d"), "high": float(row[high_col]), "close": float(row[close_col])}
            for index, row in frame.iterrows()]


class LivePrices:
    """KR via the repo's resilient chart wrapper, US via the prism-us yfinance client; failures give []."""

    def __init__(self, pause=0.2):
        self.pause = pause
        self._us_client = None

    def __call__(self, market, ticker, start, end):
        try:
            if market == "KR":
                from cores.stock_chart import get_market_ohlcv_by_date
                time.sleep(self.pause)
                frame = get_market_ohlcv_by_date(start.replace("-", ""), end.replace("-", ""), ticker, adjusted=True)
                return _frame_bars(frame, "High", "Close")
            if self._us_client is None:
                path = ROOT / "prism-us" / "cores" / "us_data_client.py"
                spec = importlib.util.spec_from_file_location("weekly_report_us_data_client", path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                self._us_client = module.USDataClient()
            stop = (date.fromisoformat(end) + timedelta(days=1)).isoformat()
            return _frame_bars(self._us_client.get_ohlcv(ticker, start=start, end=stop, interval="1d"),
                               "high", "close")
        except Exception as exc:  # noqa: BLE001 - price data is best effort; the report states the gap
            logger.warning("price fetch failed for %s %s: %s", market, ticker, type(exc).__name__)
            return []


def fetch_prices(fetcher, market, needs, as_of, max_calls):
    """needs: ordered {ticker: earliest date}. Looks up at most max_calls tickers; returns (bars, gaps)."""
    books = {}
    for ticker, start in list(needs.items())[:max_calls]:
        books[ticker] = fetcher(market, ticker, start, as_of.isoformat())
    gaps = len(needs) - len(books) + sum(1 for bars in books.values() if not bars)
    return books, gaps


def _peak(bars, start, end=None, after=False):
    highs = [b["high"] for b in bars if (b["date"] > start if after else b["date"] >= start)
             and (end is None or b["date"] <= end)]
    return max(highs) if highs else None


# ---------------------------------------------------------------- analysis

def _price_needs(trades, watch, hints, cutoff):
    """Earliest date to price per ticker, in priority order: open holdings, recent trades, skipped candidates."""
    needs = {}
    for trade in sorted(trades, key=lambda t: (not t.is_open, t.sell_date or "")):
        if trade.is_open or trade.buy_date >= cutoff or (trade.sell_date or "") >= cutoff:
            needs[trade.ticker] = min(needs.get(trade.ticker, trade.buy_date), trade.buy_date)
    for ticker, row in sorted(watch.items(), key=lambda item: -hints.get(item[0], -999)):
        analyzed = _day(row["analyzed_date"])
        needs[ticker] = min(needs.get(ticker, analyzed), analyzed)
    return needs


def analyze(conn, market, as_of, weeks, fetcher, max_calls) -> Analysis:
    result = Analysis(market=market, as_of=as_of, weeks=weeks)
    if conn.execute(TABLE_EXISTS, (HOLDINGS_TABLE[market],)).fetchone() is None:
        result.notes.append(f"{market} 보유 테이블이 없어 이 시장은 건너뜁니다.")
        return result
    conn.row_factory = sqlite3.Row
    trades, result.regime_cap = load_trades(conn, market, result.cutoff)
    result.trades = trades
    attach_exit_reasons(conn, market, trades, result.cutoff)
    entries = {}
    for trade in trades:
        entries.setdefault(trade.ticker, []).append(trade.buy_date)
    watch = {}
    for row in conn.execute(SQL[market]["watch"], (result.cutoff,)).fetchall():
        watch.setdefault(row["ticker"], row)     # earliest skip of the window per ticker
    result.candidates = len(set(watch) | {t.ticker for t in result.cohort})
    hints = {}
    for ticker, hint in conn.execute(SQL[market]["hint"], (result.cutoff,)).fetchall():
        if ticker is not None and hint is not None:
            hints[ticker] = max(hints.get(ticker, -999), hint)

    books, result.price_gaps = fetch_prices(fetcher, market, _price_needs(trades, watch, hints, result.cutoff),
                                            as_of, max_calls)
    for trade in trades:
        bars = books.get(trade.ticker, [])
        values = [trade.ret]
        peak = _peak(bars, trade.buy_date, trade.sell_date)
        if peak:
            values.append((peak / trade.entry - 1) * 100)
        if trade.highest:
            values.append((trade.highest / trade.entry - 1) * 100)
        trade.mfe = max(values) if (peak or trade.highest or trade.sell_date) else None
        if not trade.sell_date or trade.sell_date < result.cutoff or trade.last <= 0:
            continue
        later = _peak(bars, trade.sell_date, after=True)
        regained = any(day >= trade.sell_date for day in entries[trade.ticker])
        if later and (later / trade.last - 1) * 100 >= BIG_PCT and not regained:
            cause = EXIT_LABELS.get(trade.exit_kind, "매도 판단")
            result.missed.append(Missed(trade.ticker, trade.name, "조기청산", trade.sell_date,
                                        (later / trade.last - 1) * 100, f"{cause}으로 {trade.ret:+.1f}% 청산 후 상승"))
    for ticker, row in watch.items():
        analyzed, price = _day(row["analyzed_date"]), float(row["current_price"] or 0)
        if price <= 0 or any(day >= analyzed for day in entries.get(ticker, [])):
            continue
        later = _peak(books.get(ticker, []), analyzed, after=True)
        gain = (later / price - 1) * 100 if later else hints.get(ticker)
        if gain is not None and gain >= BIG_PCT:
            result.missed.append(Missed(ticker, str(row["company_name"] or ticker), "미진입", analyzed, gain,
                                        _clip(row["skip_reason"] or "사유 없음")))
    best = {}
    for item in sorted(result.missed, key=lambda m: -m.gain):
        best.setdefault(item.ticker, item)         # one line per winner even if skipped and sold earlier
    result.missed = list(best.values())
    return result


# ---------------------------------------------------------------- rendering

def _p(value, digits=1):
    return f"{value:+.{digits}f}%"


def _md(day):
    return f"{int(day[5:7])}/{int(day[8:10])}"


def _path_text(trade):
    return "1슬롯 일괄" if trade.path == [100] else "→".join(f"{step}%" for step in trade.path)


def _exit_cause(trade):
    return EXIT_LABELS.get(trade.exit_kind, "매도 판단")


def max_drawdown(exits: list[Trade]) -> float:
    """Worst peak-to-trough of the cumulative account contribution of exits (%p, <= 0)."""
    equity = peak = worst = 0.0
    for trade in exits:
        equity += trade.contribution
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return worst


def loss_streaks(exits: list[Trade]) -> tuple[int, int]:
    """(longest, current) run of consecutive losing exits."""
    longest = current = 0
    for trade in exits:
        current = current + 1 if trade.ret < 0 else 0
        longest = max(longest, current)
    return longest, current


def section_summary(a: Analysis) -> str:
    opened = [t for t in a.trades if a.in_week(t.buy_date)]
    closed = [t for t in a.trades if a.in_week(t.sell_date)]
    losses = [t for t in closed if t.ret < 0]
    stops = [t for t in losses if t.exit_kind == "stop"]
    holding = [t for t in a.trades if t.is_open]
    used = sum(t.fraction for t in holding)
    lines = ["1) 이번 주 요약",
             f"- 신규 진입 {len(opened)}건, 청산 {len(closed)}건(손실 청산 {len(losses)}건, 그중 손절 규칙 {len(stops)}건)입니다."]
    if closed:
        lines.append(f"- 청산 실현 손익은 평균 {_p(sum(t.ret for t in closed) / len(closed))}, "
                     f"계좌 기여 {sum(t.contribution for t in closed):+.2f}%p(슬롯 가중, 계좌 {a.max_slots}슬롯 기준)입니다.")
    else:
        lines.append("- 이번 주 청산 거래가 없어 실현 손익은 없습니다.")
    cap = f", 시장 국면상 최대 {a.regime_cap}종목" if a.regime_cap and a.regime_cap != a.max_slots else ""
    lines.append(f"- 보유 {len(holding)}종목, 슬롯 사용 {used:.1f}/{a.max_slots}({used / a.max_slots:.0%}{cap})입니다.")
    return "\n".join(lines)


def section_leaders(a: Analysis) -> str:
    recent = [t for t in a.trades if t.is_open or t.buy_date >= a.cutoff or (t.sell_date or "") >= a.cutoff]
    leaders = sorted((t for t in recent if t.mfe is not None and t.mfe >= LEADER_PCT), key=lambda t: -t.mfe)
    holding = [t for t in a.trades if t.is_open]
    held_leaders = sum(1 for t in holding if t.mfe is not None and t.mfe >= LEADER_PCT)
    lines = ["2) 주도주 현황",
             f"- 보유 {len(holding)}종목 중 진입 후 +{LEADER_PCT:.0f}% 이상 오른 종목은 {held_leaders}개입니다."]
    if not leaders:
        lines.append(f"- 최근 {a.weeks}주 진입 종목 가운데 +{LEADER_PCT:.0f}% 이상 오른 종목은 아직 없습니다.")
    for t in leaders[:TOP_ROWS]:
        state = (f"보유 중 {_p(t.ret)}, 비중 {t.fraction:.0%}" if t.is_open
                 else f"{_md(t.sell_date)} 청산 {_p(t.ret)}({_exit_cause(t)})")
        extra = [f"배분 {_path_text(t)}"]
        if t.reentry:
            extra.append(f"재진입 {t.reentry.get('attempt_label') or t.reentry.get('attempt', '')}")
        extra.append(f"보유 규칙 {t.runner}" if t.runner else "보유 규칙 미적용")
        lines.append(f"- {t.label} 최고 {_p(t.mfe)}, {state} / {', '.join(extra)}")
    if len(leaders) > TOP_ROWS:
        lines.append(f"- 그 외 {len(leaders) - TOP_ROWS}종목은 생략했습니다.")
    return "\n".join(lines)


def section_missed(a: Analysis) -> str:
    lines = [f"3) 놓친 대박 (최근 {a.weeks}주, 이후 +{BIG_PCT:.0f}% 이상)"]
    if not a.missed:
        lines.append("- 조건에 맞는 종목이 없습니다.")
    for m in a.missed[:TOP_ROWS]:
        lines.append(f"- {m.name}({m.ticker}) {m.kind} {_md(m.when)}, 이후 최고 {_p(m.gain, 0)} / 사유: {m.reason}")
    if len(a.missed) > TOP_ROWS:
        lines.append(f"- 그 외 {len(a.missed) - TOP_ROWS}종목은 생략했습니다.")
    return "\n".join(lines)


def section_stop_cost(a: Analysis) -> str:
    exits = a.exits
    losses = [t for t in exits if t.ret < 0]
    stops = [t for t in losses if t.exit_kind == "stop"]
    longest, current = loss_streaks(exits)
    lines = [f"4) 손절 비용 (최근 {a.weeks}주 청산 {len(exits)}건 기준)"]
    if not exits:
        return "\n".join(lines + ["- 청산 거래가 없습니다."])
    lost = sum(t.contribution for t in losses)
    average = _p(sum(t.ret for t in losses) / len(losses)) if losses else "0.0%"
    lines.append(f"- 손실 청산 {len(losses)}건(손절 규칙 {len(stops)}건), 계좌 기여 합계 {lost:+.2f}%p, 평균 {average}입니다.")
    lines.append(f"- 연속 손실 청산은 최대 {longest}건이고, 현재 연속 {current}건입니다.")
    redo = [t for t in a.cohort if t.reentry]
    if redo:
        gained = sum(t.contribution for t in redo)
        lines.append(f"- 재진입 {len(redo)}건(보유 중 {sum(1 for t in redo if t.is_open)}건)의 손익 합계는 "
                     f"계좌 기여 {gained:+.2f}%p로, 손실 청산 합계 {lost:+.2f}%p 대비 "
                     f"{'만회하고 있습니다' if gained > 0 else '아직 만회하지 못했습니다'}.")
    else:
        lines.append("- 이 기간 재진입 기록은 없습니다.")
    return "\n".join(lines)


def trigger_rows(a: Analysis) -> list[tuple[str, int, int, float, float]]:
    groups: dict[str, list[Trade]] = {}
    for t in a.cohort:
        groups.setdefault(t.trigger, []).append(t)
    rows = [(name, len(ts), sum(1 for t in ts if t.mfe is not None and t.mfe >= LEADER_PCT),
             sum(t.ret for t in ts) / len(ts), sum(t.contribution for t in ts)) for name, ts in groups.items()]
    return sorted(rows, key=lambda r: (-r[1], r[0]))


def section_triggers(a: Analysis) -> str:
    rows = trigger_rows(a)
    lines = [f"5) 트리거별 성적 (최근 {a.weeks}주 진입, 대박은 최고 +{LEADER_PCT:.0f}% 도달)"]
    if not rows:
        return "\n".join(lines + ["- 진입 기록이 없습니다."])
    for name, count, big, avg, contribution in rows[:6]:
        lines.append(f"- {name}: 진입 {count}건, 대박 {big}건({big / count:.0%}), 평균 {_p(avg)}, 계좌 기여 {contribution:+.2f}%p")
    return "\n".join(lines)


def north_star(a: Analysis) -> list[str]:
    cohort = a.cohort
    leaders = [t for t in cohort if t.mfe is not None and t.mfe >= LEADER_PCT]
    exits = a.exits
    gains = sum(t.contribution for t in exits if t.ret >= 0)
    losses = sum(t.contribution for t in exits if t.ret < 0)
    underweight = [t for t in leaders if t.fraction < 0.5]
    lines = []
    if not cohort:
        lines.append("- 대박 포착: 진입 기록이 없어 평가할 수 없습니다.")
    else:
        verdict = ("대박 종목이 아직 없습니다" if not leaders else
                   f"비중이 절반 미만이던 대박 종목이 {len(underweight)}개 있어 증액 규칙 점검이 필요합니다" if underweight
                   else "대박 종목에서 비중이 충분했습니다")
        lines.append(f"- 대박 포착: 진입 {len(cohort)}건 중 {len(leaders)}건({len(leaders) / len(cohort):.0%})이 "
                     f"+{LEADER_PCT:.0f}%에 도달했습니다. {verdict}.")
    lines.append(f"- 손실·낙폭: 청산 기준 최대 낙폭 {max_drawdown(exits):+.2f}%p, 손실 합계 {losses:+.2f}%p이고 "
                 f"이익은 {gains:+.2f}%p로 {'이익이 손실을 웃돕니다' if gains + losses > 0 else '손실이 이익을 앞섭니다'}.")
    ratio = -losses / gains if gains > 0 else None
    drag = ("이익 청산이 없어 비율을 계산할 수 없습니다" if ratio is None else
            "손절 비용이 이익을 크게 갉아먹고 있습니다" if ratio >= 0.7 else
            "손절 비용이 이익의 상당 부분을 차지합니다" if ratio >= 0.4 else "손절 비용은 관리 가능한 수준입니다")
    lines.append(f"- 거래 빈도·손절: 주당 평균 {len(cohort) / a.weeks:.1f}건 진입, 손실 청산 "
                 f"{sum(1 for t in exits if t.ret < 0)}건이며 {drag}.")
    if a.candidates:
        caught = f"{len(leaders) / len(cohort):.0%}" if cohort else "-"
        lines.append(f"- 스크리닝 정확도: 분석 후보 {a.candidates}종목 중 진입 {len(cohort)}건({len(cohort) / a.candidates:.0%}), "
                     f"진입 대비 대박 {caught}, 놓친 대박 {len(a.missed)}건입니다.")
    else:
        lines.append("- 스크리닝 정확도: 후보 기록이 없어 평가할 수 없습니다.")
    return lines


def log_picks(a: Analysis) -> list[str]:
    """Up to three logs worth reading together: a missed winner, a loss, a give-back, then spares."""
    missed = [f"{_md(m.when)} {m.name}({m.ticker}) {m.kind} 후 {_p(m.gain, 0)}: {m.reason}" for m in a.missed[:2]]
    losers = sorted((t for t in a.exits if t.ret < 0), key=lambda t: t.ret)
    loss = [f"{_md(t.sell_date)} {t.label} 손실 청산 {_p(t.ret)}"
            f"({_exit_cause(t)}{': ' + t.exit_reason if t.exit_reason else ''})" for t in losers[:2]]
    gave = sorted((t for t in a.trades if t.mfe is not None and t.mfe >= LEADER_PCT and t.mfe > t.ret),
                  key=lambda t: t.ret - t.mfe)
    giveback = [f"{t.label} 최고 {_p(t.mfe)}에서 {'현재' if t.is_open else '청산'} {_p(t.ret)}로 반납" for t in gave[:2]]
    redo = [f"{_md(t.buy_date)} {t.label} 재진입 {t.reentry.get('attempt_label', '')} 결과 {_p(t.ret)}"
            for t in a.cohort if t.reentry][:1]
    firsts = [group[0] for group in (missed, loss, giveback) if group]
    spares = [text for group in (missed, loss, giveback) for text in group[1:]] + redo
    return (firsts + spares)[:3]


def section_direction(a: Analysis) -> str:
    lines = ["6) 방향 점검 (북극성 4지표)"] + north_star(a) + ["같이 볼 로그"]
    picks = log_picks(a)
    lines += [f"  {i}. {text}" for i, text in enumerate(picks, 1)] or ["  기록할 만한 거래가 없습니다."]
    return "\n".join(lines)


def build_sections(a: Analysis) -> list[str]:
    if a.notes:
        return list(a.notes)
    sections = [section_summary(a), section_leaders(a), section_missed(a), section_stop_cost(a),
                section_triggers(a), section_direction(a)]
    if a.price_gaps:
        sections.append(f"참고: 가격 조회 한도·실패로 {a.price_gaps}종목의 최고가를 확인하지 못했습니다.")
    return sections


def pack_messages(header: str, sections: list[str], limit: int = MESSAGE_LIMIT) -> list[str]:
    """Greedy-pack sections into messages under limit; an oversized section is cut at line boundaries."""
    room = limit - 20                               # space for the "(i/n)" footer
    pieces = []
    for section in sections:
        lines = section.split("\n")
        while len("\n".join(lines)) > room:
            cut = len(lines) - 1
            while cut > 1 and len("\n".join(lines[:cut])) > room:
                cut -= 1
            pieces.append("\n".join(lines[:cut]))
            lines = lines[cut:]
        pieces.append("\n".join(lines))
    chunks, current = [], header
    for piece in pieces:
        if len(current) + len(piece) + 2 > room and current != header:
            chunks.append(current)
            current = header
        current = f"{current}\n\n{piece}"
    chunks.append(current)
    total = len(chunks)
    return [f"{text}\n({i}/{total})" if total > 1 else text for i, text in enumerate(chunks, 1)]


def render(a: Analysis) -> list[str]:
    header = f"[PRISM 주도주 리포트] {a.market} · {a.as_of.isoformat()} 기준 (최근 {a.weeks}주)"
    return pack_messages(header, build_sections(a))


# ---------------------------------------------------------------- delivery

def send_private(messages: list[str]) -> bool:
    """Send to the private maintenance chat only (prism_core.ops_alert never falls back to a public channel)."""
    from prism_core.ops_alert import send_ops_alert

    async def _run():
        return [await send_ops_alert(text) for text in messages]

    return all(asyncio.run(_run()))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--market", choices=["kr", "us", "both"], default="both")
    parser.add_argument("--weeks", type=int, default=8)
    parser.add_argument("--as-of", help="YYYY-MM-DD (default: today in KST)")
    parser.add_argument("--db", default=os.getenv("STOCK_TRACKING_DB") or str(ROOT / "stock_tracking_db.sqlite"))
    parser.add_argument("--max-price-calls", type=int, default=80, help="price lookups per market")
    parser.add_argument("--no-prices", action="store_true", help="skip price lookups (DB fields only)")
    parser.add_argument("--dry-run", action="store_true", help="print only, never send to Telegram")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)

    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    as_of = date.fromisoformat(args.as_of) if args.as_of else datetime.now(ZoneInfo("Asia/Seoul")).date()
    fetcher = (lambda *_: []) if args.no_prices else LivePrices()
    conn = connect_readonly(args.db)
    messages = []
    for market in (["KR", "US"] if args.market == "both" else [args.market.upper()]):
        messages += render(analyze(conn, market, as_of, args.weeks, fetcher, args.max_price_calls))
    conn.close()

    for text in messages:
        print(text)
        print("-" * 40)
    if args.dry_run:
        return 0
    if os.getenv("PRISM_DISABLE_SIGNAL_PUBLISH") == "1":
        print("PRISM_DISABLE_SIGNAL_PUBLISH=1: 발송을 건너뜁니다.", file=sys.stderr)
        return 0
    return 0 if send_private(messages) else 1


if __name__ == "__main__":
    raise SystemExit(main())
