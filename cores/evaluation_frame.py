"""PRISM decision frame for the bot's /evaluate and /us_evaluate answers.

The bot's evaluation agent used to reason from a style guide alone. This block
gives it the trading loop's own facts and thresholds, computed with the same
functions: the six-step regime, the O'Neil Market Pulse and distribution days,
the trend facts (MA20/50/200, T1/T2, ATR, 52-week high), the holding rules of
the SELL prompt (-7% stop, 50-day loss exit, regime trailing band, 3-condition
trend weakening) and the BUY gate's regime R/R floor. Calculation stays here;
the agent only judges and phrases.

A user's position has no stored peak, so the trailing line uses the highest
close since the holding period started (labelled approximate). Anything that
cannot be computed is printed as unknown, never filled. Fail-open: errors
return "" so /evaluate keeps working without the frame.
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

BULL = frozenset({"parabolic", "strong_bull", "moderate_bull"})
REGIME_KO = {
    "parabolic": "과열 강세장", "strong_bull": "강한 강세장", "moderate_bull": "보통 강세장",
    "sideways": "횡보장", "moderate_bear": "보통 약세장", "strong_bear": "강한 약세장",
}
PULSE_KO = {"UPTREND": "상승 확인", "UNDER_PRESSURE": "상승 압박", "CORRECTION": "조정장"}
_SESSION = {"KR": ("Asia/Seoul", time(15, 30)), "US": ("America/New_York", time(16, 0))}
HARD_STOP_PCT = 7.0          # SELL prompt Core-4 step 1
TRAIL_ACTIVATE = 1.05        # Core-3: peak >= entry x 1.05
TRAIL_BAND = {True: 0.92, False: 0.95}   # Core-2: bull x0.92, other regimes x0.95
MA50_EXIT_BUFFER = 0.995     # Core-4 system rule: losing and close <= MA50 x 0.995


def _daily(market: str, ticker: str, days: int = 400):
    end = datetime.now().date()
    start = end - timedelta(days=days)
    if market == "US":
        from prism_core.us_market_data import daily_history
        return daily_history(ticker, start, end, adjusted=True)
    from cores.market_data import get_market_ohlcv_by_date
    return get_market_ohlcv_by_date(start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), ticker, adjusted=True)


def live_regime(market: str) -> str | None:
    """Six-step regime computed like tools/trend_exit_seller._compute_live_regime."""
    try:
        end = datetime.now().date()
        start = end - timedelta(days=400)
        if market == "US":
            from prism_core.oneil_runtime_inputs import _root_module
            from prism_core.us_market_data import daily_history
            prefetch = _root_module("_evaluation_us_prefetch", "prism-us/cores/data_prefetch.py")
            sp = daily_history("^GSPC", start, end)
            try:
                ndx = daily_history("^IXIC", start, end)
            except Exception:  # noqa: BLE001 - S&P alone still gives a regime, as in the seller
                ndx = None
            computed = prefetch._compute_us_regime(sp, ndx)
        else:
            from cores import market_data
            from cores.data_prefetch import _compute_kr_regime
            kospi = market_data.get_index_ohlcv_by_date(start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), "1001")
            computed = _compute_kr_regime({i.strftime("%Y-%m-%d"): r.to_dict() for i, r in kospi.iterrows()})
        regime = (computed or {}).get("market_regime")
        return str(regime) if regime else None
    except Exception as e:  # noqa: BLE001
        logger.warning("[EvaluationFrame] %s regime failed: %s", market, e)
        return None


def _pct(a, b):
    return None if a is None or not b else (a / b - 1.0) * 100.0


def _fmt(v, suffix="%"):
    return "확인 불가" if v is None else f"{v:+.1f}{suffix}"


def holding_facts(df, *, market: str, avg_price: float, period_months: float, regime: str | None,
                  now: datetime | None = None) -> dict:
    """Pure: the SELL prompt's deterministic checks for a user position."""
    zone, close_t = _SESSION[market]
    now = now or datetime.now(ZoneInfo(zone)).replace(tzinfo=None)
    close_s = df["Close"].astype(float)
    close = float(close_s.iloc[-1])

    def ma(n):
        s = close_s.rolling(n).mean()
        v = s.iloc[-1]
        up = None if len(s) <= 5 or s.iloc[-6] != s.iloc[-6] else bool(v > s.iloc[-6])
        return (None, None) if v != v else (float(v), up)

    (ma20, ma20_up), (ma50, ma50_up), (ma200, ma200_up) = ma(20), ma(50), ma(200)
    highs, lows = df["High"].astype(float).tail(21).tolist(), df["Low"].astype(float).tail(21).tolist()
    prev = close_s.tail(21).tolist()
    tr = [max(h - l, abs(h - p), abs(l - p)) for h, l, p in zip(highs[1:], lows[1:], prev[:-1])]
    atr20 = sum(tr) / len(tr) / close * 100.0 if tr and close else None

    profit = _pct(close, avg_price)
    start = (now - timedelta(days=max(float(period_months or 0), 0) * 30.44)).date()
    since = close_s[[d.date() >= start for d in df.index]]
    peak = float(since.max()) if len(since) else None
    bull = regime in BULL
    trail_active = peak is not None and avg_price > 0 and peak >= avg_price * TRAIL_ACTIVATE
    trail_line = peak * TRAIL_BAND[bull] if trail_active else None

    last4 = close_s.tail(4).tolist()
    vol = df["Volume"].astype(float) if "Volume" in df.columns else None
    vol_ratio = (float(vol.iloc[-1]) / float(vol.iloc[-21:-1].mean())
                 if vol is not None and len(vol) > 21 and vol.iloc[-21:-1].mean() > 0 else None)

    from prism_core.breakout_facts import compute_high52_facts
    high52 = compute_high52_facts([d.date() for d in df.index], df["High"].tolist(), price=close,
                                  now_local=now, session_close=close_t)

    signals = []
    if profit is not None and profit <= -HARD_STOP_PCT:
        signals.append(f"평단 대비 {profit:.1f}%로 -7% 손절선 도달")
    if profit is not None and profit < 0 and ma50 is not None and close <= ma50 * MA50_EXIT_BUFFER:
        signals.append("손실 중 50일선 아래(시스템 자동 매도 조건)")
    if trail_line is not None and close < trail_line:
        signals.append(f"이익 보호선 {trail_line:,.{0 if market == 'KR' else 2}f} 아래")
    return dict(close=close, profit=profit, ma20=ma20, ma20_up=ma20_up, ma50=ma50, ma50_up=ma50_up,
                ma200=ma200, ma200_up=ma200_up, atr20=atr20, peak=peak, peak_start=start.isoformat(),
                trail_active=trail_active, trail_line=trail_line, bull=bull,
                t1=ma50 is not None and close < ma50,
                t2=ma20 is not None and ma20_up is False and close <= ma20 * 0.95,
                down3=len(last4) == 4 and all(b < a for a, b in zip(last4, last4[1:])),
                vol_ratio=vol_ratio, high52=high52, sell_signals=signals)


def render(market: str, regime: str | None, pulse, facts: dict, rr: dict) -> str:
    money = (lambda v: "확인 불가" if v is None else f"{v:,.0f}원") if market == "KR" else \
            (lambda v: "확인 불가" if v is None else f"${v:,.2f}")
    side = lambda v, line: "확인 불가" if line is None else ("위" if v >= line else "아래")  # noqa: E731
    slope = lambda up: "확인 불가" if up is None else ("상승" if up else "하락")  # noqa: E731
    f = facts
    pulse_line = ("확인 불가" if pulse is None else
                  f"{pulse.state}({PULSE_KO.get(pulse.state, pulse.state)}), 최근 {pulse.window}세션 분산일 {pulse.distribution_days}일")
    h52 = f["high52"]
    lines = [
        "## PRISM 판단 틀 (시스템 계산값 · 본 매매 로직과 같은 기준)",
        "### 시장",
        f"- 6단계 국면: {regime or '확인 불가'}({REGIME_KO.get(regime or '', '확인 불가')})",
        f"- 오닐 Market Pulse: {pulse_line}",
        f"- 신규매수 적용 국면: {rr.get('effective_regime') or '확인 불가'}"
        + (" (분산일 누적으로 한 단계 낮춤)" if rr.get("distribution_step_down") else ""),
        "### 종목 추세",
        f"- 현재가 {money(f['close'])} (장중이면 오늘 미완성 봉) · 평단 대비 {_fmt(f['profit'])}",
        f"- 20일선 {side(f['close'], f['ma20'])}({_fmt(_pct(f['close'], f['ma20']))}, 기울기 {slope(f['ma20_up'])})"
        f" · 50일선 {side(f['close'], f['ma50'])}({_fmt(_pct(f['close'], f['ma50']))}, 기울기 {slope(f['ma50_up'])})"
        f" · 200일선 {side(f['close'], f['ma200'])}({_fmt(_pct(f['close'], f['ma200']))}, 기울기 {slope(f['ma200_up'])})",
        f"- 50일선 이탈(오닐 10주선): {f['t1']} · 20일선 급붕괴(20일선 하락+5% 아래): {f['t2']}",
        "- 하루 평균 변동폭(ATR20): " + ("확인 불가" if f["atr20"] is None else f"{f['atr20']:.1f}%"),
        ("- 52주 최고가: 확인 불가" if h52["high"] is None else
         f"- 52주 확정 최고가 {money(h52['high'])}, 현재가는 그 {h52['ratio'] * 100:.1f}%"),
        f"- 최근 3거래일 연속 종가 하락: {f['down3']} · 오늘 거래량/20일 평균: "
        + ("확인 불가" if f["vol_ratio"] is None else f"{f['vol_ratio']:.2f}배"),
        "### 보유 판단 기준 (본로직 매도 지침)",
        "- 평단 대비 -7% 또는 손절가 이탈: 무조건 매도",
        "- 손실 중 50일선×0.995 아래: 시스템 자동 매도",
        f"- 이익 보호선: 보유 기간 고점이 평단 +5% 이상이면 고점×{TRAIL_BAND[f['bull']]}"
        f"({'강세장' if f['bull'] else '횡보·약세장'}) 아래에서 매도. "
        + (f"보유 시작({f['peak_start']}) 이후 최고 종가 {money(f['peak'])} → 보호선 {money(f['trail_line'])} (근사값)"
           if f["trail_active"] else
           f"보유 시작({f['peak_start']}) 이후 최고 종가 {money(f['peak'])}, 평단 +5% 미달로 아직 비활성 (근사값)"),
        "- 추세 약화 매도: 3거래일 연속 하락 + 20일 평균 이상 거래량 + 20일선 종가 이탈, 셋 다일 때만",
        "- 목표가 도달·보유 기간 경과는 매도 사유가 아님. 추세가 살아 있으면 보유",
        "- 손실 종목 물타기(평단 낮추기)는 하지 않음. 추가매수는 수익 중이고 추세가 강할 때만",
        "- 이 기준의 매도 신호: " + ("; ".join(f["sell_signals"]) if f["sell_signals"] else "없음"),
        "### 신규·추가 매수 기준 (본로직 매수 관문)",
    ]
    if rr.get("status") == "OK":
        lines += [
            f"- 이 국면의 최대 손절폭 {rr['max_loss_pct']:.1f}%, 손익비 하한 {rr['rr_floor']}",
            f"- 지금 가격에서 사려면: 손절 {money(rr['max_stop_price'])}일 때 목표(확정 저항)가 "
            f"{money(rr['min_target_at_max_stop'])} 이상이어야 함",
            "- 확정 저항은 오늘 장중 고가가 아닌 과거 확정 고점·매물대. 저항까지 여력이 손절폭×하한보다 작으면 매수 보류",
        ]
    else:
        lines.append("- 국면을 계산하지 못해 손익비 하한 확인 불가")
    return "\n".join(lines)


def build_evaluation_frame(market: str, ticker: str, avg_price: float, period_months: float) -> str:
    """Blocking; call through asyncio.to_thread with a timeout."""
    market = market.upper()
    try:
        df = _daily(market, ticker)
        if df is None or len(df) < 25 or "Close" not in df.columns:
            return ""
        regime = live_regime(market)
        from cores.regime_policy import get_market_pulse_detail
        pulse = get_market_pulse_detail(market.lower(), use_cache=False)  # the bot process lives for days
        facts = holding_facts(df, market=market, avg_price=float(avg_price), period_months=period_months,
                              regime=regime)
        from prism_core.buy_risk_facts import TICK, stop_rr_facts
        dd_text = "" if pulse is None else f"distribution days: {pulse.distribution_days}"
        rr = stop_rr_facts(regime, dd_text, facts["close"], TICK[market])
        return render(market, regime, pulse, facts, rr)
    except Exception as e:  # noqa: BLE001 - /evaluate must work without the frame
        logger.warning("[EvaluationFrame] %s %s failed: %s", market, ticker, e)
        return ""
