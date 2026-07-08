#!/usr/bin/env python3
"""
buy_quality 게이트 검증 하네스 (SPEC §5). 룩어헤드 0.
=================================================================
표본 JSON(tools/buy_quality_sample_builder.py 산출) 을 입력으로, 각 (종목, 매수일 D)
에 대해 **D 시점**의 as-of O'Neil 차트를 렌더하고 프로덕션 함수를 그대로 호출한다:

    analysis = await analyze_base_oneil(ticker, regime=regime@D, end_date=D, market=..)
    verdict  = gate_verdict(analysis, regime@D)

수집 후 혼동행렬·bull 오차단율(H1)·손절 차단력(H2)·qscore 안정성(H4)·regime/시장별
분해표를 산출하고 사전등록 판정선 대비 PASS/FAIL 을 출력 + 마크다운 리포트로 떨군다.

편향 차단(SPEC §2):
- 차트 컷오프 = D (create_oneil_*_chart 의 end_date 인자). 미래봉 유입 0.
- regime@D = logs/regime_history.jsonl 에서 D 이하 최근 ts 조회, 없으면 당시 지수로
  재계산(_compute_kr_regime), 그것도 불가하면 'unknown'(문턱=기본 75). 미래 데이터 미사용.
- 프로덕션 함수 그대로 호출(재구현 금지).

비전 호출은 API 키 + pykrx 가 필요 → **db-server 에서 실행**. 로컬은 vision_available()
=False 라 analyze_base_oneil 이 None 을 반환하고 해당 행은 error 로 집계·스킵된다.

실행:
  cd /root/prism-insight && python tools/buy_quality_backtest.py \
      --sample tasks/buy_quality_validation/sample_from_history.json \
      [--repeats 3] [--limit N] [--market both] [--out PATH]
"""
import os
import sys
import json
import argparse
import asyncio
import logging
import statistics
from datetime import datetime, timedelta

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("buy_quality_backtest")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SAMPLE = os.path.join(
    ROOT, "tasks", "buy_quality_validation", "sample_from_history.json"
)
REGIME_HISTORY = os.path.join(ROOT, "logs", "regime_history.jsonl")

# bull 계열 regime(문턱 완화). SPEC §2: bull 오차단율이 최우선(H1).
_BULL_REGIMES = {"strong_bull", "moderate_bull", "bull"}
_UNKNOWN_REGIME = "unknown"

# 사전등록 판정선(SPEC §1). 사후조정 금지.
_H1_MIN_BULL_WINNER_PASS = 0.80   # bull 승자 통과율 ≥ 80%
_H2_MIN_LOSER_BLOCK = 0.70        # 손절주 차단율 ≥ 70%
_H4_MAX_QSCORE_STD = 8.0          # 반복 qscore 표준편차 ≤ 8


# --------------------------------------------------------------------------- #
# regime@D 산출 (룩어헤드 차단)                                                 #
# --------------------------------------------------------------------------- #
def _regime_from_history(market: str, d: str) -> str | None:
    """logs/regime_history.jsonl 에서 D 이하(<=) 최근 ts 의 regime. 없으면 None."""
    if not os.path.exists(REGIME_HISTORY):
        return None
    best_ts, best_regime = None, None
    try:
        with open(REGIME_HISTORY, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if str(rec.get("market", "")).upper() != market.upper():
                    continue
                ts = str(rec.get("ts", ""))
                ts_date = ts.split(" ")[0].split("T")[0]
                if not ts_date or ts_date > d:  # 미래 유입 차단(<= D 만)
                    continue
                if best_ts is None or ts > best_ts:
                    best_ts, best_regime = ts, rec.get("regime")
    except OSError as exc:
        logger.warning("regime_history read failed: %s", exc)
        return None
    return best_regime


def _regime_recompute_kr(d: str) -> str | None:
    """당시(<=D) KOSPI 지수로 _compute_kr_regime 재계산. pykrx 필요(db-server)."""
    try:
        from krx_data_client import get_index_ohlcv_by_date
        from cores.data_prefetch import _compute_kr_regime

        end_s = d.replace("-", "")
        start_s = (datetime.strptime(d, "%Y-%m-%d") - timedelta(days=370)).strftime(
            "%Y%m%d"
        )
        idf = get_index_ohlcv_by_date(start_s, end_s, "1001")  # KOSPI 종합
        if idf is None or len(idf) == 0:
            return None
        recs = {}
        for idx, row in idf.iterrows():
            key = idx.strftime("%Y%m%d") if hasattr(idx, "strftime") else str(idx)

            def _g(*names):
                for n in names:
                    if n in row:
                        return float(row[n])
                return 0.0

            recs[key] = {
                "Open": _g("Open", "시가"),
                "High": _g("High", "고가"),
                "Low": _g("Low", "저가"),
                "Close": _g("Close", "종가"),
                "Volume": _g("Volume", "거래량"),
            }
        res = _compute_kr_regime(recs)
        return res.get("market_regime")
    except Exception as exc:  # noqa: BLE001  (pykrx/네트워크 부재 등)
        logger.warning("KR regime recompute failed for %s: %s", d, exc)
        return None


def regime_at_date(market: str, d: str) -> str:
    """D 시점 regime. history → (KR)재계산 → 'unknown' 순. 절대 미래 데이터 미사용."""
    r = _regime_from_history(market, d)
    if r:
        return r
    if market.upper() == "KR":
        r = _regime_recompute_kr(d)
        if r:
            return r
    # US 재계산(yfinance ^GSPC/^VIX)은 db-server 별도 확장 여지. 현재는 unknown.
    return _UNKNOWN_REGIME


# --------------------------------------------------------------------------- #
# 단일 셋업 평가                                                                #
# --------------------------------------------------------------------------- #
async def _evaluate_row(row: dict, repeats: int) -> dict:
    """한 (종목, D) 셋업을 regime@D 로 repeats 회 평가. 프로덕션 함수 그대로 호출."""
    from cores.llm.features.buy_quality import (
        analyze_base_oneil,
        gate_verdict,
        REGIME_THRESHOLDS,
        _DEFAULT_THRESHOLD,
    )

    ticker = row["ticker"]
    d = row["buy_date"]
    market = row.get("market", "KR")
    regime = regime_at_date(market, d)
    threshold = REGIME_THRESHOLDS.get(regime, _DEFAULT_THRESHOLD)

    qscores: list[int] = []
    would_buys: list[bool] = []
    reasons: list[str] = []
    n_ok = 0
    last_error = None

    for _ in range(max(1, repeats)):
        try:
            analysis = await analyze_base_oneil(
                ticker,
                company_name=row.get("company_name"),
                regime=regime,
                market=None,  # KR: 자동 index 감지. (KOSPI/KOSDAQ 힌트 불필요)
                end_date=d,   # ← as-of 컷오프. 룩어헤드 차단의 핵심.
            )
        except Exception as exc:  # noqa: BLE001  (analyze 는 원래 raise 안 함)
            last_error = f"{type(exc).__name__}: {exc}"
            analysis = None

        if analysis is None:
            last_error = last_error or "analyze_base_oneil returned None (vision off / chart fail)"
            continue

        verdict = gate_verdict(analysis, regime)
        n_ok += 1
        qscores.append(int(verdict["quality_score"]))
        would_buys.append(bool(verdict["would_buy"]))
        reasons.append(str(verdict["reason"]))

    qscore_mean = round(statistics.fmean(qscores), 2) if qscores else None
    qscore_std = round(statistics.stdev(qscores), 3) if len(qscores) >= 2 else 0.0
    # would_buy: 반복 다수결(동수면 보수적으로 False).
    if would_buys:
        would_buy = sum(would_buys) > (len(would_buys) / 2)
    else:
        would_buy = None

    return {
        "ticker": ticker,
        "company_name": row.get("company_name"),
        "D": d,
        "label": row.get("label"),
        "market": market,
        "profit_rate": row.get("profit_rate"),
        "regime": regime,
        "threshold": threshold,
        "qscore": qscore_mean,
        "qscore_std": qscore_std,
        "would_buy": would_buy,
        "n_ok": n_ok,
        "repeats": max(1, repeats),
        "reason": reasons[0] if reasons else None,
        "error": None if n_ok > 0 else last_error,
    }


# --------------------------------------------------------------------------- #
# 집계 & 리포트                                                                 #
# --------------------------------------------------------------------------- #
def _pct(num: int, den: int) -> str:
    return f"{(100.0 * num / den):.1f}%" if den else "n/a"


def summarize(results: list[dict]) -> dict:
    ok = [r for r in results if r["n_ok"] > 0 and r["would_buy"] is not None]
    errored = [r for r in results if r["n_ok"] == 0]

    winners = [r for r in ok if r["label"] == "win"]
    losers = [r for r in ok if r["label"] == "loss"]

    # 혼동행렬(승자→pass 기대 / 손절→block 기대)
    win_pass = sum(1 for r in winners if r["would_buy"])
    win_block = len(winners) - win_pass
    loss_pass = sum(1 for r in losers if r["would_buy"])
    loss_block = len(losers) - loss_pass

    # H1: bull 승자 통과율 (오차단율 = 1 - 통과율)
    bull_winners = [r for r in winners if r["regime"] in _BULL_REGIMES]
    bull_win_pass = sum(1 for r in bull_winners if r["would_buy"])
    h1_rate = (bull_win_pass / len(bull_winners)) if bull_winners else None

    # H2: 손절 차단율
    h2_rate = (loss_block / len(losers)) if losers else None

    # H4: 반복 qscore 표준편차 (repeats>1 인 셋업만 의미)
    stds = [r["qscore_std"] for r in ok if r["repeats"] > 1 and r["qscore"] is not None]
    h4_std_mean = round(statistics.fmean(stds), 3) if stds else None
    h4_std_max = round(max(stds), 3) if stds else None

    # regime / market 분해
    by_regime: dict[str, dict] = {}
    for r in ok:
        b = by_regime.setdefault(r["regime"], {"n": 0, "pass": 0})
        b["n"] += 1
        b["pass"] += 1 if r["would_buy"] else 0
    by_market: dict[str, dict] = {}
    for r in ok:
        b = by_market.setdefault(r["market"], {"n": 0, "pass": 0})
        b["n"] += 1
        b["pass"] += 1 if r["would_buy"] else 0

    # 판정 (표본/데이터 없으면 미판정 → None; FAIL 로 잘못 낙인찍지 않음)
    h1_pass = None if h1_rate is None else (h1_rate >= _H1_MIN_BULL_WINNER_PASS)
    h2_pass = None if h2_rate is None else (h2_rate >= _H2_MIN_LOSER_BLOCK)
    h4_pass = None if h4_std_max is None else (h4_std_max <= _H4_MAX_QSCORE_STD)

    return {
        "n_total": len(results),
        "n_ok": len(ok),
        "n_error": len(errored),
        "confusion": {
            "win_pass": win_pass, "win_block": win_block,
            "loss_pass": loss_pass, "loss_block": loss_block,
            "n_win": len(winners), "n_loss": len(losers),
        },
        "H1_bull_winner_pass_rate": h1_rate,
        "H1_bull_winners_n": len(bull_winners),
        "H2_loser_block_rate": h2_rate,
        "H4_qscore_std_mean": h4_std_mean,
        "H4_qscore_std_max": h4_std_max,
        "by_regime": by_regime,
        "by_market": by_market,
        "verdict": {"H1": h1_pass, "H2": h2_pass, "H4": h4_pass},
        "errored_rows": errored,
    }


def render_markdown(results: list[dict], summ: dict, meta: dict) -> str:
    L: list[str] = []
    A = L.append
    A(f"# buy_quality 게이트 검증 결과 ({meta['run_date']})")
    A("")
    A(f"- 표본: `{meta['sample']}`  | market 필터: `{meta['market']}` "
      f"| repeats: {meta['repeats']} | limit: {meta['limit']}")
    A(f"- 총 {summ['n_total']}행 중 vision 성공 {summ['n_ok']}행 / "
      f"error·skip {summ['n_error']}행")
    A("")
    c = summ["confusion"]
    A("## 혼동행렬 (성공행 기준)")
    A("")
    A("| 실제\\게이트 | would_buy=True(pass) | would_buy=False(block) | 계 |")
    A("|---|---|---|---|")
    A(f"| 승자(win) | {c['win_pass']} | {c['win_block']} | {c['n_win']} |")
    A(f"| 손절(loss) | {c['loss_pass']} | {c['loss_block']} | {c['n_loss']} |")
    A("")
    A("## 사전등록 판정선 (SPEC §1)")
    A("")
    h1 = summ["H1_bull_winner_pass_rate"]
    h1s = f"{h1*100:.1f}%" if h1 is not None else "n/a"
    h2 = summ["H2_loser_block_rate"]
    h2s = f"{h2*100:.1f}%" if h2 is not None else "n/a"
    v = summ["verdict"]

    def _mark(x):
        return "n/a" if x is None else ("PASS" if x else "FAIL")

    A(f"- **H1 (핵심·오차단)**: bull 승자 통과율 = {h1s} "
      f"(n={summ['H1_bull_winners_n']}) — 기준 ≥{_H1_MIN_BULL_WINNER_PASS*100:.0f}% "
      f"→ **{_mark(v['H1'])}**")
    A(f"  - bull 오차단율(false-block) = "
      f"{(100.0 - h1*100):.1f}%" if h1 is not None else "  - bull 오차단율 = n/a")
    A(f"- **H2 (차단력)**: 손절 차단율 = {h2s} — 기준 ≥{_H2_MIN_LOSER_BLOCK*100:.0f}% "
      f"→ **{_mark(v['H2'])}**")
    A(f"- **H4 (안정성)**: qscore std mean={summ['H4_qscore_std_mean']} "
      f"max={summ['H4_qscore_std_max']} — 기준 max ≤{_H4_MAX_QSCORE_STD:.0f} "
      f"→ **{_mark(v['H4'])}**")
    A("")
    overall = all(x for x in v.values() if x is not None) and any(
        x is not None for x in v.values()
    )
    A(f"### 종합: **{'PASS (활성화 상신 가능)' if overall else 'FAIL / 미판정 — 재검증 필요'}**")
    A("")
    A("## regime별 통과율")
    A("")
    A("| regime | n | pass | 통과율 |")
    A("|---|---|---|---|")
    for reg, b in sorted(summ["by_regime"].items()):
        A(f"| {reg} | {b['n']} | {b['pass']} | {_pct(b['pass'], b['n'])} |")
    A("")
    A("## 시장별 통과율")
    A("")
    A("| market | n | pass | 통과율 |")
    A("|---|---|---|---|")
    for mk, b in sorted(summ["by_market"].items()):
        A(f"| {mk} | {b['n']} | {b['pass']} | {_pct(b['pass'], b['n'])} |")
    A("")
    A("## 행별 상세")
    A("")
    A("| ticker | D | label | market | regime | thr | qscore | std | would_buy | profit% | note |")
    A("|---|---|---|---|---|---|---|---|---|---|---|")
    for r in results:
        wb = "-" if r["would_buy"] is None else ("True" if r["would_buy"] else "False")
        note = "" if r["n_ok"] > 0 else (r["error"] or "skip")
        A(f"| {r['ticker']} | {r['D']} | {r['label']} | {r['market']} | "
          f"{r['regime']} | {r['threshold']} | {r['qscore']} | {r['qscore_std']} | "
          f"{wb} | {r['profit_rate']} | {note} |")
    A("")
    if summ["n_error"]:
        A(f"> 참고: {summ['n_error']}행은 vision 미수행(로컬 키/pykrx 부재 또는 차트 결측)."
          " db-server 에서 재실행 요망.")
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------- #
# main                                                                         #
# --------------------------------------------------------------------------- #
def _load_sample(path: str, market_filter: str, limit: int | None) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    rows = payload.get("rows", payload if isinstance(payload, list) else [])
    mf = market_filter.lower()
    if mf in ("kr", "us"):
        rows = [r for r in rows if str(r.get("market", "")).lower() == mf]
    if limit:
        rows = rows[:limit]
    return rows


async def _run(rows: list[dict], repeats: int) -> list[dict]:
    results = []
    for i, row in enumerate(rows, 1):
        logger.info("[%d/%d] %s @ %s", i, len(rows), row.get("ticker"), row.get("buy_date"))
        results.append(await _evaluate_row(row, repeats))
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description="buy_quality 게이트 검증 하네스 (as-of).")
    ap.add_argument("--sample", default=DEFAULT_SAMPLE, help="표본 JSON 경로")
    ap.add_argument("--repeats", type=int, default=1, help="셋업당 반복 호출 수(H4 안정성)")
    ap.add_argument("--limit", type=int, default=None, help="처리할 최대 행 수(스모크)")
    ap.add_argument("--market", choices=["kr", "us", "both"], default="both",
                    help="시장 필터")
    ap.add_argument("--out", default=None, help="리포트 md 경로(기본: results_<date>.md)")
    args = ap.parse_args()

    sys.path.insert(0, ROOT)
    if os.path.isdir(os.path.join(ROOT, "prism-us")):
        sys.path.insert(0, os.path.join(ROOT, "prism-us"))

    if not os.path.exists(args.sample):
        logger.error("sample not found: %s (먼저 buy_quality_sample_builder 실행)",
                     args.sample)
        return 1

    rows = _load_sample(args.sample, args.market, args.limit)
    if not rows:
        logger.error("no rows after filter (market=%s, limit=%s)", args.market, args.limit)
        return 2
    logger.info("evaluating %d rows (repeats=%d)", len(rows), args.repeats)

    results = asyncio.run(_run(rows, args.repeats))
    summ = summarize(results)

    run_date = datetime.now().strftime("%Y%m%d")
    out = args.out or os.path.join(
        ROOT, "tasks", "buy_quality_validation", f"results_{run_date}.md"
    )
    meta = {
        "run_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "sample": args.sample, "market": args.market,
        "repeats": args.repeats, "limit": args.limit,
    }
    md = render_markdown(results, summ, meta)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(md)

    # 콘솔 요약
    print("\n" + md)
    logger.info(
        "done: ok=%d error=%d | H1=%s H2=%s H4=%s | report → %s",
        summ["n_ok"], summ["n_error"],
        summ["verdict"]["H1"], summ["verdict"]["H2"], summ["verdict"]["H4"], out,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
