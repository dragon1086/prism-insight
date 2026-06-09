#!/usr/bin/env python3
"""
US 매매일지 경량 백필 (one-off).
=================================================================
US journal 이 그동안 미기록(enable_journal OFF + agent market 버그)되어, 이미 청산된
최근 US 거래(us_trading_history)를 trading_journal(market='US')에 소급 기록한다.

라이브 저널은 LLM 회고(situation/judgment/lessons)를 생성하지만, 백필은 MCPApp
컨텍스트가 없어 LLM 에이전트를 못 돌린다. 따라서 피드백에 필요한 핵심 필드
(ticker·profit·holding·trade_date(recency)·lessons·pattern_tags·one_line_summary)를
거래 결과 기반 '템플릿'으로 직접 INSERT 한다. (라이브 신규 기록은 LLM 회고 포함)

trade_date 는 실제 sell_date 로 기록 → get_context_for_ticker 의
'⚠️ exited Nd ago — be cautious chasing a re-entry' recency 가 정확히 동작.

실행: cd /root/prism-insight && python tools/backfill_us_journal.py --days 60 [--dry-run]
"""
import os
import sys
import json
import argparse
import sqlite3
from datetime import datetime, timedelta

ROOT = "/root/prism-insight"
DB = os.path.join(ROOT, "stock_tracking_db.sqlite")


def build_reflection(ticker, pr, hd, scenario_json):
    """거래 결과 기반 템플릿 회고 필드 생성."""
    try:
        scen = json.loads(scenario_json) if isinstance(scenario_json, str) else {}
    except Exception:
        scen = {}
    mkt = scen.get("market_condition", "") if isinstance(scen, dict) else ""
    loss = (pr or 0) < 0
    short = (hd or 0) <= 5
    if loss and short:
        tags = ["fomo_entry", "short_hold_stop", "volatility_whipsaw"]
        lessons = [{
            "condition": "변동성 높은 횡보장(VIX 상승·지수 20일선 이탈)에서 단기 모멘텀 진입",
            "action": "동일 종목 재진입 시 50/200일선 추세 정렬과 시장 regime(강세 확인) 충족 전까지 진입 보류 — 추격 금지",
            "reason": f"{ticker} 진입 {hd}일 만에 {pr:.1f}% 손절(변동성 피탈)",
            "priority": "high",
        }]
        one = f"{ticker} {pr:.1f}% 손절({hd}일) — 변동성 구간 조급한 진입 후 단기 피탈"
    elif loss:
        tags = ["delayed_stop_loss", "trend_reversal"]
        lessons = [{
            "condition": "보유 중 추세 약화/지지 이탈",
            "action": "진입 근거가 훼손되면 손절가 도달 전이라도 비중 축소 검토",
            "reason": f"{ticker} {hd}일 보유 후 {pr:.1f}% 손절",
            "priority": "medium",
        }]
        one = f"{ticker} {pr:.1f}% 손절({hd}일) — 추세 약화 구간 청산"
    else:
        tags = ["trend_following", "disciplined_exit"]
        lessons = [{
            "condition": "추세 추종 진입이 성공해 목표/트레일링 구간 도달",
            "action": "검증된 진입 패턴/원칙 유지, 승자는 추세 지속 시 보유",
            "reason": f"{ticker} {hd}일 보유 후 +{pr:.1f}% 수익 청산",
            "priority": "low",
        }]
        one = f"{ticker} +{pr:.1f}% 수익({hd}일) — 추세 추종 성공"
    situation = {"summary": f"[백필] 보유 {hd}일, 실현 {pr:.2f}%. 매수 시 regime: {mkt or 'N/A'}"}
    judgment = {"summary": "[백필] 과거 거래 소급 기록 — 상세 LLM 회고 없음(신규 거래는 회고 포함)"}
    return mkt, situation, judgment, lessons, tags, one


def run(days, dry):
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    rows = cur.execute(
        """SELECT ticker, company_name, buy_price, buy_date, sell_price, sell_date,
                  profit_rate, holding_days, scenario
           FROM us_trading_history
           WHERE sell_date IS NOT NULL AND sell_date >= ?
           ORDER BY sell_date""", (cutoff,)).fetchall()
    existing = set(
        (r[0], (r[1] or "")[:10])
        for r in cur.execute(
            "SELECT ticker, trade_date FROM trading_journal WHERE market='US'").fetchall())
    print(f"candidates (last {days}d): {len(rows)} | existing US journal rows: {len(existing)}")
    created = skipped = 0
    for (ticker, name, bp, bd, sp, sd, pr, hd, scen) in rows:
        if (ticker, (sd or "")[:10]) in existing:
            skipped += 1
            continue
        mkt, situation, judgment, lessons, tags, one = build_reflection(ticker, pr, hd, scen)
        line = f"{ticker:6} {(sd or '')[:10]} {pr:7.2f}% {hd}d -> {one}"
        if dry:
            print("DRY  " + line)
            continue
        cur.execute(
            """INSERT INTO trading_journal
               (ticker, company_name, trade_date, trade_type, buy_price, buy_date,
                buy_scenario, buy_market_context, sell_price, sell_reason, profit_rate,
                holding_days, situation_analysis, judgment_evaluation, lessons, pattern_tags,
                one_line_summary, confidence_score, compression_layer, created_at, market)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (ticker, name, sd, 'sell', bp, bd,
             scen or '{}', json.dumps(mkt, ensure_ascii=False), sp,
             f"[Backfilled] {'Stop-loss' if (pr or 0) < 0 else 'Take-profit'} {pr:.2f}% (held {hd}d)",
             pr, hd,
             json.dumps(situation, ensure_ascii=False), json.dumps(judgment, ensure_ascii=False),
             json.dumps(lessons, ensure_ascii=False), json.dumps(tags, ensure_ascii=False),
             one, 0.4, 1, sd, 'US'))
        created += 1
        print("OK   " + line)
    conn.commit()
    print(f"\n== created={created} skipped={skipped} ==")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    run(a.days, a.dry_run)


if __name__ == "__main__":
    main()
