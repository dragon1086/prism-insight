"""Recheck inputs for re-entry v2: latest report, trigger-time facts and the BUY instruction.

Shared by the SHADOW runner (freezes these inputs at the trigger and runs the recheck,
observability/reentry_v2_recheck.py) and tools/replay_reentry_llm_recheck.py.
"""
from __future__ import annotations

import re
from pathlib import Path

from prism_core.decision_input_features import compute, render_facts_block

REPORT_DIRS = {"KR": "reports", "US": "prism-us/reports"}
TRANSLATED = re.compile(r"_(en|ja|zh|es)\.md$")
# Report .md files embed their charts as base64 (~90% of the characters). The production BUY
# reads text extracted from the PDF, so the recheck drops the image payloads the same way.
EMBEDDED_IMAGE = re.compile(r"data:image/[A-Za-z0-9.+-]+;base64,[A-Za-z0-9+/=]+")


def strip_embedded_images(text):
    """Report text without base64 image payloads (the hash of the raw report is kept separately)."""
    return EMBEDDED_IMAGE.sub("[차트 이미지 생략]", text)
RECHECK_KO = """

## 재진입 재점검 모드 (이번 요청에만 적용)

이 종목은 과거에 분석되어 보류·차단되었거나 손절된 종목입니다. 오늘 장중에 베이스의 피벗(저항선)을
거래량을 동반해 돌파했거나, 앞선 돌파를 재점검에서 미진입한 뒤 지지선까지 눌렸다가 반등했습니다(입력의
트리거 종류와 이전 재점검 기록 참조). 입력의 보고서는 트리거 전날까지 작성된 가장 최근 보고서이며 작성일을 확인하십시오.
- 원래 보류·차단·손절 사유가 현재 기술적 사실(추세, 위치, 거래량)과 시장 상태로 해소됐는지 재점검하십시오.
- 원래 시나리오 가격 수준(1·2차 지지·저항)과 이번 돌파 가격을 비교해, 당시 막혔던 저항을 넘었는지와
  지지 구조가 유지되는지를 판단 근거로 쓰십시오. 이미 넘어선 과거 저항은 새 지지 후보입니다.
- 재무 F1~F4는 보고서 기준 판단을 유지합니다. 보고서 이후의 새 정보는 없으므로 추정하지 마십시오.
- 진입 가격은 입력의 트리거 가격 기준입니다. 추격(돌파는 피벗 +5%, 눌림 반등은 지지선 +8% 초과)은 이미 배제됐습니다.
- 되돌림을 기다려야 한다고 판단하면 미진입으로 하고 rejection_reason에 구체적으로 적으십시오. 기다릴 지지선은
  trading_scenarios.key_levels.primary_support에 적으십시오. 시스템은 그 지지선까지 눌렸다가 반등하는지와
  재돌파를 계속 감시하고, 그때 다시 재점검을 요청합니다.
- 출력 JSON 형식과 채점 규칙은 기존과 동일합니다.
"""


class ArchivedReport:
    """Report text from archive.db (read-only), shaped like a Path for the caller."""

    def __init__(self, name, text):
        self.name, self._text = name, text

    def read_text(self, encoding="utf-8"):
        return self._text


def archived_report(archive_db, market, ticker, day):
    """Latest ko report written strictly before `day` (a same-day report may postdate an intraday trigger)."""
    import sqlite3
    with sqlite3.connect("file:" + str(archive_db) + "?mode=ro", uri=True) as conn:
        row = conn.execute(
            "SELECT report_date, mode, model, content FROM report_archive WHERE market = ? AND ticker = ? "
            "AND language = 'ko' AND replace(report_date, '-', '') < ? ORDER BY replace(report_date, '-', '') DESC, "
            "CASE mode WHEN 'afternoon' THEN 1 ELSE 0 END DESC LIMIT 1",
            (market.lower(), ticker, day.replace("-", ""))).fetchone()
    if not row:
        return None
    stamp = str(row[0]).replace("-", "")
    return ArchivedReport(f"{ticker}_archive_{stamp}_{row[1] or 'morning'}_{row[2] or 'na'}.md", row[3])


def latest_report(root, market, ticker, day):
    """Latest ko report file dated strictly before `day` (see archived_report)."""
    folder = Path(root) / REPORT_DIRS[market]
    stamp = day.replace("-", "")
    best = None
    for path in folder.glob(f"{ticker}_*.md"):
        if TRANSLATED.search(path.name):
            continue
        match = re.search(r"_(\d{8})_(morning|afternoon)", path.name)
        if not match or match.group(1) >= stamp:
            continue
        key = (match.group(1), match.group(2) == "afternoon")
        if best is None or key > best[0]:
            best = (key, path)
    return best[1] if best else None


def recheck_instruction(market, root=None, section=RECHECK_KO):
    """The production BUY agent instruction (current prompt flags) plus a re-entry recheck section
    (v2 section by default; re-entry v3 passes its own)."""
    if market == "KR":
        from cores.agents.trading_agents import create_trading_scenario_agent
        return create_trading_scenario_agent(language="ko").instruction + section
    import importlib.util
    root = Path(root) if root else Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("us_agents_recheck", root / "prism-us/cores/agents/trading_agents.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.create_us_trading_scenario_agent(language="ko").instruction + section


def market_facts(bench_bars, day):
    from cores.market_pulse import DailyBar, MarketPulse
    pulse, state = MarketPulse(), None
    for bar in bench_bars:
        if bar["date"] >= day:
            break
        state = pulse.feed(DailyBar(date=bar["date"], close=bar["close"],
                                    volume=bar["volume"] if bar["volume"] > 1 else None))
    return {"state": state, "distribution_days": int(getattr(pulse, "distribution_days", 0) or 0)}


def _pct(a, b):
    return None if a is None or not b else (a / b - 1) * 100


def _fmt(v, suffix="%"):
    return "결측" if v is None else f"{v:+.2f}{suffix}"


def trend_fact_lines(bars, i, bench_bars):
    """Trend, RS and Market Pulse lines as of the session before day i (completed bars only).

    bars[i] may be a forming bar (re-entry v3 intraday); only its date is read.
    """
    closes = [b["close"] for b in bars[:i]]

    def ma(n):
        return sum(closes[-n:]) / n if len(closes) >= n else None

    ma20, ma50, ma200 = ma(20), ma(50), ma(200)
    ma20_prev = sum(closes[-25:-5]) / 20 if len(closes) >= 25 else None
    bench = [b for b in bench_bars if b["date"] < bars[i]["date"]]
    rs = None
    if len(closes) > 61 and len(bench) > 61:
        rs = (closes[-1] / closes[-61] - 1 - (bench[-1]["close"] / bench[-61]["close"] - 1)) * 100
    mkt = market_facts(bench_bars, bars[i]["date"])
    t1 = ma50 is not None and closes[-1] < ma50
    t2 = ma20 is not None and ma20_prev is not None and ma20 < ma20_prev and closes[-1] <= ma20 * 0.95
    return [
        f"### 📉 개별 추세 팩트 (재진입 트리거일 {bars[i]['date']} 기준, 직전 확정일 {bars[i - 1]['date']})",
        f"- 직전 종가 {closes[-1]:,.2f}: MA20 대비 {_fmt(_pct(closes[-1], ma20))}, MA50 대비 {_fmt(_pct(closes[-1], ma50))}, "
        f"MA200 대비 {_fmt(_pct(closes[-1], ma200))}",
        f"- MA20 기울기: {'상승' if ma20 and ma20_prev and ma20 > ma20_prev else '하락'} / T1_hit: {t1} / T2_hit: {t2}",
        f"- RS(60일, 종목-지수): {_fmt(rs, '%p')}",
        f"- Market Pulse(지수 재생): {mkt['state']} | 분산일 {mkt['distribution_days']}",
    ]


def technical_block(bars, i, entry, pivot, market, bench_bars, trigger="INTRADAY_BREAKOUT"):
    """Facts as of the trigger: completed bars before day i plus the breakout price."""
    lines = trend_fact_lines(bars, i, bench_bars) + [
        "",
        "### 🚀 재진입 트리거",
        (f"- 눌림 반등: 지지선 {pivot:,.2f} 부근까지 되돌린 뒤 전일 고가 {bars[i - 1]['high']:,.2f} 돌파, "
         f"진입 기준가 {entry:,.2f} (지지선 대비 {_fmt(_pct(entry, pivot))})" if trigger == "PULLBACK_BOUNCE" else
         f"- 피벗(베이스 저항선) {pivot:,.2f} 돌파({trigger}), 진입 기준가 {entry:,.2f} (피벗 대비 {_fmt(_pct(entry, pivot))})"),
        f"- 돌파일 거래량: 20일 평균의 {bars[i]['volume'] / (sum(b['volume'] for b in bars[i - 20:i]) / 20):.2f}배",
    ]
    result = compute(bars[:i], market=market, observed_at=_as_dt(bars[i - 1]["date"]), current_price=entry)
    facts, _ = render_facts_block(result, None, None, market=market, language="ko")
    return "\n".join(lines) + "\n\n" + facts


def _as_dt(day):
    from datetime import datetime, timezone
    return datetime.fromisoformat(day + "T23:00:00").replace(tzinfo=timezone.utc)
