#!/usr/bin/env python3
"""Market Pulse V1 backtest — 6y index replay + pre-registered auto-judgment.

Replays :class:`cores.market_pulse.MarketPulse` over KOSPI (^KS11 via yfinance,
the same fetch tools/regime_backtest.py uses successfully on db-server) and
S&P 500 (^GSPC via yfinance), applying the IDENTICAL O'Neil rules to both
markets (no per-market tuning = robustness evidence).

Produces a deterministic markdown report (also printed to console) with:
  (a) per-year state distribution % + transition counts,
  (b) CORRECTION episode timeline (start/end/duration/days-to-FTD),
  (c) pre-registered C1-C5 auto-judgment per
      tasks/market_pulse/00_VALIDATION_PLAN.md §3 (V1), PASS/FAIL + overall.

Usage:
    .venv/bin/python tools/market_pulse_backtest.py [--market {kr,us,both}]
                                                     [--years N] [--out PATH]

The heavy 6y data replay is intended to run on db-server (network for yfinance).
Importing this module performs no I/O beyond dotenv; data fetch is lazy.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

# Insert repo ROOT at sys.path[0] so root `cores` wins (avoid the prism-us cores
# shadowing trap: we do NOT add prism-us to sys.path).
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) != sys.path[0]:
    sys.path.insert(0, str(PROJECT_ROOT))

try:  # dotenv is optional at import time; must never crash the import.
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
except Exception:  # noqa: BLE001
    pass

from cores.market_pulse import (  # noqa: E402
    CORRECTION,
    UNDER_PRESSURE,
    UPTREND,
    DailyBar,
    MarketPulse,
)

STATES = (UPTREND, UNDER_PRESSURE, CORRECTION)

# C5 window (KR crash the live account bled through — spec §3 V1 C5).
C5_START = "2026-06-01"
C5_END = "2026-07-09"


# --------------------------------------------------------------------------- #
# Data fetch (lazy; network/auth only when actually called)                    #
# --------------------------------------------------------------------------- #
def fetch_kr_bars(years: int) -> List[DailyBar]:
    """KOSPI index (1001) daily OHLCV via the authenticated KRX client.

    market_pulse는 거래량이 필수(DD 판정)다. yfinance ^KS11은 거래량이
    결측/0인 구간이 많아(2026년 등) DD가 전혀 안 잡히는 무의미한 재생이
    나온다(regime_backtest는 종가만 써서 ^KS11로 충분했던 것과 다름).
    → 운영과 동일한 krx_data_client 기반 ``get_index_ohlcv_by_date``('1001')를
    쓰되, 6년 단일요청이 INVALIDPERIOD2로 실패하므로 **연 단위 청크**로 나눠
    받아 합친다.
    """
    import pandas as pd
    from datetime import datetime, timedelta
    from cores.stock_chart import get_index_ohlcv_by_date

    end_dt = datetime.now()
    start_dt = end_dt - timedelta(days=int(years * 365.25) + 10)
    chunks = []
    y = start_dt.year
    while y <= end_dt.year:
        s = max(start_dt, datetime(y, 1, 1)).strftime("%Y%m%d")
        e = min(end_dt, datetime(y, 12, 31)).strftime("%Y%m%d")
        cdf = get_index_ohlcv_by_date(s, e, "1001")
        if cdf is not None and len(cdf):
            chunks.append(cdf)
        y += 1
    if not chunks:
        raise RuntimeError("KOSPI(1001) KRX fetch returned empty for all chunks")
    df = pd.concat(chunks)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    close_col = "종가" if "종가" in df.columns else "Close"
    vol_col = ("거래량" if "거래량" in df.columns
               else ("Volume" if "Volume" in df.columns else None))
    if vol_col is None:
        raise RuntimeError("KOSPI(1001) frame has no volume column — DD 판정 불가")
    return _df_to_bars(df, close_col, vol_col)


def fetch_us_bars(years: int) -> List[DailyBar]:
    """S&P 500 (^GSPC) daily via yfinance (same approach as regime_backtest)."""
    import pandas as pd
    import yfinance as yf

    df = yf.download("^GSPC", period=f"{years}y", interval="1d",
                     auto_adjust=True, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.dropna(how="all")
    if df is None or len(df) == 0:
        raise RuntimeError("^GSPC fetch returned empty")
    vol_col = "Volume" if "Volume" in df.columns else None
    return _df_to_bars(df.sort_index(), "Close", vol_col)


def _df_to_bars(df, close_col: str, vol_col: Optional[str]) -> List[DailyBar]:
    import pandas as pd

    bars: List[DailyBar] = []
    for idx, row in df.iterrows():
        c = float(row[close_col])
        if c <= 0:
            continue
        v: Optional[float] = None
        if vol_col is not None:
            raw = row[vol_col]
            if raw is not None and not pd.isna(raw):
                v = float(raw)
                if v <= 0:
                    v = None
        bars.append(DailyBar(date=idx.strftime("%Y-%m-%d"), close=c, volume=v))
    return bars


# --------------------------------------------------------------------------- #
# Analysis                                                                     #
# --------------------------------------------------------------------------- #
def _year(date: str) -> int:
    return int(date[:4])


def _month(date: str) -> str:
    return date[:7]


def yearly_distribution(rows) -> List[tuple]:
    """[(year, {state: pct}, transitions), ...]."""
    by_year: dict = {}
    for date, state, _dd in rows:
        by_year.setdefault(_year(date), []).append(state)
    out = []
    for y in sorted(by_year):
        seq = by_year[y]
        n = len(seq) or 1
        pct = {s: round(100.0 * seq.count(s) / n, 1) for s in STATES}
        trans = sum(1 for i in range(1, len(seq)) if seq[i] != seq[i - 1])
        out.append((y, pct, trans))
    return out


def correction_episodes(rows) -> List[dict]:
    """Contiguous CORRECTION runs with start/end/duration/days-to-FTD."""
    episodes: List[dict] = []
    start_i: Optional[int] = None
    for i, (date, state, _dd) in enumerate(rows):
        if state == CORRECTION and start_i is None:
            start_i = i
        elif state != CORRECTION and start_i is not None:
            # exit day i is the Follow-Through Day (only way out).
            episodes.append({
                "start": rows[start_i][0],
                "end": rows[i - 1][0],
                "duration": i - start_i,
                "days_to_ftd": i - start_i,   # start..FTD inclusive count
                "ftd_date": rows[i][0],
            })
            start_i = None
    if start_i is not None:  # still in correction at series end
        episodes.append({
            "start": rows[start_i][0],
            "end": rows[-1][0],
            "duration": len(rows) - start_i,
            "days_to_ftd": None,
            "ftd_date": None,
        })
    return episodes


def monthly_returns_from_bars(bars: List[DailyBar]) -> List[Tuple[str, float]]:
    """Month-over-month return using each month's last close. Skips first month."""
    last_close: dict = {}
    order: List[str] = []
    for b in bars:
        m = b.date[:7]
        if m not in last_close:
            order.append(m)
        last_close[m] = b.close
    out: List[Tuple[str, float]] = []
    for i, m in enumerate(order):
        if i == 0:
            continue
        prev = last_close[order[i - 1]]
        if prev and prev > 0:
            out.append((m, (last_close[m] - prev) / prev * 100.0))
    return out


def state_pct_by_month(rows) -> dict:
    """{month: {state: pct, 'n': days}}."""
    by_month: dict = {}
    for date, state, _dd in rows:
        by_month.setdefault(_month(date), []).append(state)
    out = {}
    for m, seq in by_month.items():
        n = len(seq) or 1
        out[m] = {s: 100.0 * seq.count(s) / n for s in STATES}
        out[m]["n"] = len(seq)
    return out


def judge(market: str, bars: List[DailyBar], rows) -> Tuple[List[dict], bool]:
    """Pre-registered C1-C5 auto-judgment. Returns (results, overall_pass)."""
    results: List[dict] = []
    mret = monthly_returns_from_bars(bars)
    spm = state_pct_by_month(rows)

    # C1: months with return <= -5% -> non-UPTREND days >= 50%.
    c1_months = []
    c1_pass = True
    for m, r in mret:
        if r <= -5.0 and m in spm:
            non_up = spm[m][UNDER_PRESSURE] + spm[m][CORRECTION]
            ok = non_up >= 50.0
            c1_pass = c1_pass and ok
            c1_months.append((m, round(r, 1), round(non_up, 1), ok))
    results.append({"id": "C1", "desc": "급락월(≤-5%) 비-UPTREND ≥50%",
                    "pass": c1_pass if c1_months else True,
                    "detail": c1_months, "na": not c1_months})

    # C2: months with return >= +3% -> UPTREND days >= 70% (official metric).
    # 참고: also record the non-CORRECTION day ratio (UPTREND + UNDER_PRESSURE),
    # since policy allows buys in UNDER_PRESSURE (top-down 0 only) — §7 Rev.2.
    # This is reported alongside but does NOT affect the official PASS/FAIL.
    c2_months = []
    c2_pass = True
    for m, r in mret:
        if r >= 3.0 and m in spm:
            up = spm[m][UPTREND]
            non_corr = 100.0 - spm[m][CORRECTION]  # non-CORRECTION ratio (참고)
            ok = up >= 70.0
            c2_pass = c2_pass and ok
            c2_months.append((m, round(r, 1), round(up, 1), round(non_corr, 1), ok))
    results.append({"id": "C2", "desc": "강세월(≥+3%) UPTREND ≥70%",
                    "pass": c2_pass if c2_months else True,
                    "detail": c2_months, "na": not c2_months})

    # C3: overall CORRECTION ratio in [10, 35] %.
    n = len(rows) or 1
    corr_pct = 100.0 * sum(1 for _d, s, _dd in rows if s == CORRECTION) / n
    c3_pass = 10.0 <= corr_pct <= 35.0
    results.append({"id": "C3", "desc": "전체 CORRECTION 비율 10~35%",
                    "pass": c3_pass, "detail": round(corr_pct, 1), "na": False})

    # C4: state transitions <= 20 / year (every year).
    yd = yearly_distribution(rows)
    c4_bad = [(y, t) for (y, _p, t) in yd if t > 20]
    c4_pass = not c4_bad
    results.append({"id": "C4", "desc": "상태전환 ≤20회/년",
                    "pass": c4_pass, "detail": [(y, t) for (y, _p, t) in yd],
                    "na": False})

    # C5: KR must hit CORRECTION during 2026-06-01..2026-07-09.
    if market == "kr":
        hit = any(s == CORRECTION and C5_START <= d <= C5_END for d, s, _dd in rows)
        results.append({"id": "C5", "desc": f"KR {C5_START}~{C5_END} CORRECTION 발동",
                        "pass": hit, "detail": hit, "na": False})
    else:
        results.append({"id": "C5", "desc": "KR 전용 (US 해당없음)",
                        "pass": True, "detail": None, "na": True})

    overall = all(r["pass"] for r in results if not r.get("na"))
    return results, overall


# --------------------------------------------------------------------------- #
# Report                                                                       #
# --------------------------------------------------------------------------- #
def render_market(market: str, bars: List[DailyBar]) -> str:
    mp = MarketPulse()
    rows = mp.replay(bars)
    lines: List[str] = []
    name = {"kr": "KOSPI (KRX 1001)", "us": "S&P 500 (^GSPC)"}[market]
    lines.append(f"## {name} — {len(rows)} sessions "
                 f"({rows[0][0]} → {rows[-1][0]})")
    lines.append("")

    # (a) per-year distribution + transitions
    lines.append("### (a) 연도별 상태 분포 / 전환 횟수")
    lines.append("")
    lines.append("| 연도 | UPTREND% | UNDER_PRESSURE% | CORRECTION% | 전환 |")
    lines.append("|---|---|---|---|---|")
    for y, pct, trans in yearly_distribution(rows):
        lines.append(f"| {y} | {pct[UPTREND]} | {pct[UNDER_PRESSURE]} | "
                     f"{pct[CORRECTION]} | {trans} |")
    lines.append("")

    # (b) correction episodes
    lines.append("### (b) CORRECTION 에피소드")
    lines.append("")
    eps = correction_episodes(rows)
    if not eps:
        lines.append("_에피소드 없음_")
    else:
        lines.append("| 시작 | 종료 | 기간(일) | days-to-FTD | FTD일 |")
        lines.append("|---|---|---|---|---|")
        for e in eps:
            ftd = e["ftd_date"] or "진행중"
            dtf = e["days_to_ftd"] if e["days_to_ftd"] is not None else "—"
            lines.append(f"| {e['start']} | {e['end']} | {e['duration']} | "
                         f"{dtf} | {ftd} |")
    lines.append("")

    # (c) auto-judgment
    results, overall = judge(market, bars, rows)
    lines.append("### (c) 사전등록 기준 자동판정 (V1)")
    lines.append("")
    for r in results:
        verdict = "N/A" if r.get("na") else ("PASS" if r["pass"] else "FAIL")
        lines.append(f"- **{r['id']} [{verdict}]** {r['desc']}")
        d = r["detail"]
        if r["id"] == "C1" and d:
            for m, ret, val, ok in d:
                tag = "OK" if ok else "MISS"
                lines.append(f"    - {m}: 월수익률 {ret}%, 비-UPTREND {val}% [{tag}]")
        elif r["id"] == "C2" and d:
            for m, ret, up, non_corr, ok in d:
                tag = "OK" if ok else "MISS"
                lines.append(f"    - {m}: 월수익률 {ret}%, UPTREND {up}% [{tag}] "
                             f"(참고 비-CORRECTION {non_corr}%)")
        elif r["id"] == "C3":
            lines.append(f"    - 전체 CORRECTION 비율: {d}%")
        elif r["id"] == "C4":
            lines.append(f"    - 연도별 전환: {d}")
        elif r["id"] == "C5" and not r.get("na"):
            lines.append(f"    - 발동 여부: {d}")
    lines.append("")
    lines.append(f"### {name} 종합 판정: **{'PASS' if overall else 'FAIL'}**")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Market Pulse V1 backtest")
    ap.add_argument("--market", choices=["kr", "us", "both"], default="both")
    ap.add_argument("--years", type=int, default=6)
    ap.add_argument("--out", default=str(PROJECT_ROOT / "tasks" / "market_pulse" / "results_v1.md"))
    args = ap.parse_args()

    markets = ["kr", "us"] if args.market == "both" else [args.market]
    header = [
        "# Market Pulse V1 — 6년 지수 재생 결과",
        "",
        f"_생성: {datetime.now().strftime('%Y-%m-%d %H:%M')} · years={args.years} · "
        "규칙 무수정 양시장 적용 (사전등록 §3 V1)_",
        "",
    ]
    sections: List[str] = []
    for m in markets:
        try:
            bars = fetch_kr_bars(args.years) if m == "kr" else fetch_us_bars(args.years)
            if len(bars) < 30:
                raise RuntimeError(f"insufficient bars: {len(bars)}")
            sections.append(render_market(m, bars))
        except Exception as e:  # noqa: BLE001
            sections.append(f"## {m.upper()} — 데이터 실패 (deferred)\n\n"
                            f"`{type(e).__name__}: {e}`\n\n"
                            "6년 데이터 재생은 db-server(네트워크+krx auth)에서 실행. 로컬 지연.\n")

    report = "\n".join(header + sections)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    print(report)
    print(f"\n[written] {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
