"""
O'Neil(William O'Neil / CANSLIM) 추세추종 룰베이스 매도 알고리즘
=================================================================
KR / US 공용 — 시장 무관 순수 함수. 통화/표기만 호출측에서 포맷.

KR: stock_tracking_agent._analyze_sell_decision 의 1차 룰 로직.
US: us_stock_tracking_agent._fallback_sell_decision (AI 매도 실패 시 fallback).

설계 철학 (O'Neil 철칙):
  "손실은 빠르게 자른다(7~8%). 승자는 추세가 깨질 때까지 보유한다."
  기존 크루드 룰의 치명적 버그였던
    - profit >= 10% → 매도   (승자 조기 청산: 2026-06-04 MU +53%, ANET +24% 사례)
    - 시간 기반 매도(30/60/90일)  (추세와 무관한 캘린더 청산)
  를 전면 제거한다. 매도는 오직 '추세/가격' 신호로만 발생한다.

가용 입력(스키마 확인됨): buy_price, current_price, stop_loss, target_price,
  highest_price(진입 후 고점, 자동 갱신), live regime(매도 시점 계산값),
  scenario.key_levels(지지/저항).  ※ 20MA·VIX·거래량·연속하락일은 매도경로에
  직접 노출되지 않으므로 사용하지 않는다(과매도 방지).

순수 stdlib 만 사용(dataclass/typing/json) — 외부 의존성 없음.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Tuple
import json


# ── 튜닝 상수 (O'Neil 교범 기준) ───────────────────────────────
ABS_STOP_LOSS_PCT = -7.0        # 절대 손절: 진입가 대비 -7% (오닐 7~8% 룰)
# 매도경로는 '종가'가 없고 last-trade(current_price)만 가짐.
# O'Neil 손절은 '종가 기준' — 장중 wick 한 번의 터치로 팔지 않는다.
# 종가 미가용 환경에서 이 규율을 근사하기 위해 스톱선에 작은 버퍼를 둔다.
STOP_WICK_BUFFER = 0.005        # 스톱선 0.5% 아래로 '명확히' 이탈해야 매도
TRAIL_ACTIVATION_PCT = 5.0      # 진입가 대비 +5% 도달 후에만 트레일링 활성화
TRAIL_DROP_BULL = 0.92          # 강세장: 고점 대비 -8%
TRAIL_DROP_WEAK = 0.95          # 약세/횡보: 고점 대비 -5% (더 타이트)
TRAIL_DROP_UNKNOWN = 0.90       # regime 불명/stale: 고점 대비 -10% (불확실 시 과매도 방지)
BULL_REGIMES = {"parabolic", "strong_bull", "moderate_bull"}
WEAK_REGIMES = {"sideways", "moderate_bear", "strong_bear"}


@dataclass
class SellInputs:
    buy_price: float
    current_price: float
    stop_loss: float = 0.0
    target_price: float = 0.0
    highest_price: float = 0.0          # 진입 후 고점 (없으면 current/buy로 보정)
    market_condition: str = ""          # 레짐 문자열: '매도 시점의 LIVE 값'을 넣어야 함.
    #   stock_data.scenario 의 market_condition 은 '매수 시점 동결값(stale)'이므로
    #   그대로 쓰면 약세 전환을 못 잡는다. 호출측은 _compute_us_regime/_compute_kr_regime
    #   으로 매 사이클 1회 계산한 LIVE regime 을 from_stock_data(live_regime=...) 로 주입할 것.
    primary_support: float = 0.0        # key_levels.primary_support (선택)
    regime_is_live: bool = False        # True 면 trailing 밴드를 신뢰, False(=stale)면 보수적
    ma_50: float = 0.0                  # 개별종목 50일선(주입 시에만 TIER1.5 작동, 0이면 dormant)


def _normalize_regime(raw: str) -> str:
    r = (raw or "").lower()
    for k in ("parabolic", "strong_bull", "moderate_bull",
              "strong_bear", "moderate_bear", "sideways"):
        if k in r:
            return k
    # 한글/기타 표기 보정
    if "강세" in r or "bull" in r:
        return "moderate_bull"
    if "약세" in r or "bear" in r:
        return "moderate_bear"
    if "횡보" in r or "side" in r:
        return "sideways"
    return "moderate_bull"   # 정보 없으면 보수적으로 보유 우선(강세 가정)


def evaluate_oneil_sell(inp: SellInputs) -> Tuple[bool, str]:
    """O'Neil 추세추종 룰 기반 매도 판단.

    Returns: (should_sell, reason_key)
      reason_key 는 호출측에서 통화/언어로 포맷할 수 있는 구조화 문자열.
    매도 신호가 없으면 (False, "HOLD: ...").
    """
    bp, cp = inp.buy_price, inp.current_price
    if bp <= 0 or cp <= 0:
        return False, "HOLD: invalid price data"  # 데이터 불량 시 절대 매도 금지

    profit = (cp - bp) / bp * 100.0
    regime = _normalize_regime(inp.market_condition)
    peak = inp.highest_price if inp.highest_price and inp.highest_price > 0 else max(cp, bp)

    # ── TIER 1: 하드 스톱 (손실 차단, 예외 없음) ──────────────
    # 1A. 시나리오 손절가 이탈 (종가 미가용 → wick 버퍼로 '명확한' 이탈만)
    if inp.stop_loss > 0 and cp <= inp.stop_loss * (1.0 - STOP_WICK_BUFFER):
        return True, f"TIER1_STOPLOSS: price<=stop_loss({inp.stop_loss:.4f})"
    # 1B. 절대 손절 -7%
    if profit <= ABS_STOP_LOSS_PCT:
        return True, f"TIER1_ABS7: loss {profit:.2f}% <= {ABS_STOP_LOSS_PCT}%"

    # ── TIER 1.5: 50일선 이탈 + 손실 (보수적 손실차단) ─────────
    # 오닐 '기관 방어선(50일선)' 이탈을 손실 포지션에 한해 적용 → 승자는 절대 안 팔림.
    # ma_50 이 주입되지 않으면(0) 비활성(dormant) — 라이브 배선 전까지 동작 변화 없음.
    if inp.ma_50 and inp.ma_50 > 0 and profit < 0 and cp <= inp.ma_50 * (1.0 - STOP_WICK_BUFFER):
        return True, f"TIER1.5_MA50: below 50MA({inp.ma_50:.4f}) while losing ({profit:.2f}%)"

    # ── TIER 2: 트레일링 스톱 (승자 보호, +5% 활성화 이후만) ───
    activated = peak >= bp * (1.0 + TRAIL_ACTIVATION_PCT / 100.0)
    if activated:
        if not inp.regime_is_live:
            drop = TRAIL_DROP_UNKNOWN                 # stale/불명 → 보수적(-10%)
        elif regime in BULL_REGIMES:
            drop = TRAIL_DROP_BULL                    # live 강세 → -8%
        else:
            drop = TRAIL_DROP_WEAK                    # live 약세/횡보 → -5%
        # 트레일링도 종가 기준 → 동일 wick 버퍼로 '명확한' 이탈만 매도
        trail_line = peak * drop * (1.0 - STOP_WICK_BUFFER)
        if cp <= trail_line:
            band = int(round((1 - drop) * 100))
            return True, (f"TIER2_TRAIL: regime={regime} peak={peak:.4f} "
                          f"trail(-{band}%)={trail_line:.4f} >= price")

    # ── TIER 3: 목표가 = 매도 트리거 아님(강세) / 익절(약세만) ──
    # O'Neil: 목표가는 강세장에서 '트레일링 전환 마일스톤'이지 자동매도 아님.
    if inp.target_price > 0 and cp >= inp.target_price:
        if regime in WEAK_REGIMES:
            return True, f"TIER3_TARGET(weak): regime={regime} target reached"
        # 강세: 목표 도달해도 보유(트레일링이 청산을 관리)
        return False, f"HOLD: target hit but bull regime({regime}) -> let it run"

    # ── 기본: 보유 (추세 유지) ───────────────────────────────
    # 주의: profit>=10% 자동매도 / 시간기반 매도 전부 제거됨(안티-오닐).
    return False, f"HOLD: trend intact (profit {profit:.2f}%, regime {regime})"


def evaluate_tier1_hardstop(inp: SellInputs) -> Tuple[bool, str]:
    """TIER1-only catastrophic hard stop for the high-frequency loop (Loop A).

    Evaluates ONLY the two TIER1 rules from evaluate_oneil_sell — scenario
    stop-loss breach (1A) and the absolute -7% stop (1B) — and nothing else.
    Trailing (TIER2), 50MA (TIER1.5) and target (TIER3) are deliberately
    excluded: Loop A runs every few minutes on raw last-trade prices, where
    those slower trend signals would whipsaw on intraday noise. Uses the same
    wick buffer and constants as evaluate_oneil_sell so the two never diverge.

    Returns: (should_sell, reason_key). No trigger -> (False, "HOLD: ...").
    """
    bp, cp = inp.buy_price, inp.current_price
    if bp <= 0 or cp <= 0:
        return False, "HOLD: invalid price data"  # never sell on bad data
    # 1A. scenario stop-loss breach (clear break below, wick buffer)
    if inp.stop_loss > 0 and cp <= inp.stop_loss * (1.0 - STOP_WICK_BUFFER):
        return True, f"TIER1_STOPLOSS: price<=stop_loss({inp.stop_loss:.4f})"
    # 1B. absolute -7% stop
    profit = (cp - bp) / bp * 100.0
    if profit <= ABS_STOP_LOSS_PCT:
        return True, f"TIER1_ABS7: loss {profit:.2f}% <= {ABS_STOP_LOSS_PCT}%"
    return False, f"HOLD: above hard stop (profit {profit:.2f}%)"


# ── 호출측 어댑터 (stock_data dict → SellInputs) ────────────────
def from_stock_data(stock_data: dict, live_regime: Optional[str] = None,
                    ma_50: Optional[float] = None) -> SellInputs:
    """KR/US 의 stock_data dict 에서 SellInputs 추출.
    scenario 는 JSON 문자열 또는 dict 둘 다 허용.

    live_regime: 매도 사이클 시작 시 _compute_us_regime/_compute_kr_regime 으로
      계산한 '현재' 레짐 문자열. 주입되면 그것을 신뢰(regime_is_live=True).
      None 이면 scenario 의 매수시점 동결값으로 폴백하되 regime_is_live=False
      (→ trailing 밴드를 보수적 -10% 로 적용해 과매도 방지).
    """
    scen = stock_data.get("scenario", {}) or {}
    if isinstance(scen, str):
        try:
            scen = json.loads(scen)
        except Exception:
            scen = {}
    if not isinstance(scen, dict):
        scen = {}
    key_levels = (scen.get("trading_scenarios", {}) or {}).get("key_levels", {}) or {}
    # highest_price 는 scenario 우선, 없으면 stock_data 레벨
    highest = scen.get("highest_price") or stock_data.get("highest_price") or 0.0
    is_live = bool(live_regime)
    regime = live_regime if is_live else str(scen.get("market_condition", "") or "")

    def _f(v):
        try:
            return float(v or 0)
        except (TypeError, ValueError):
            return 0.0

    return SellInputs(
        buy_price=_f(stock_data.get("buy_price", 0)),
        current_price=_f(stock_data.get("current_price", 0)),
        stop_loss=_f(stock_data.get("stop_loss", 0) or scen.get("stop_loss", 0)),
        target_price=_f(stock_data.get("target_price", 0) or scen.get("target_price", 0)),
        highest_price=_f(highest),
        market_condition=str(regime or ""),
        primary_support=_f(key_levels.get("primary_support", 0)),
        regime_is_live=is_live,
        ma_50=_f(ma_50),
    )


if __name__ == "__main__":
    # 2026-06-04 사고 재현 검증: LIVE regime 이면 승자 4종목 보유, RL 만 정당한 트레일링 매도.
    cases = [
        ("VZ",  47.79, 46.51, 45.60, 51.58, 48.96, "moderate_bull"),
        ("ANET",142.10,175.89,0,     176.0, 178.0,  "strong_bull"),
        ("MU",  700.03,1071.21,955.82,741.85,1046.97,"strong_bull"),
        ("RL",  362.24,358.78,359.27,0,     392.10, "moderate_bull"),
        ("IBM", 241.86,307.19,297.67,277.68,330.84, "strong_bull"),
    ]
    print("--- LIVE regime ---")
    for t, bp, cp, sl, tp, hi, rg in cases:
        s, r = evaluate_oneil_sell(SellInputs(bp, cp, sl, tp, hi, rg, regime_is_live=True))
        print(f"{t:5} sell={s!s:5} | {r}")
    print("--- STALE regime (보수적 -10%) ---")
    for t, bp, cp, sl, tp, hi, rg in cases:
        s, r = evaluate_oneil_sell(SellInputs(bp, cp, sl, tp, hi, rg, regime_is_live=False))
        print(f"{t:5} sell={s!s:5} | {r}")
