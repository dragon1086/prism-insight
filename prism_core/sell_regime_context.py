"""LIVE market regime block for the KR AI sell prompt.

The deterministic exit loop (cores.oneil_fallback TIER2/TIER3) already trails on the
system regime every cycle. The AI sell decision must use the same regime and bands so
its stop-loss adjustment matches the rule that actually protects the position.
"""

from cores.oneil_fallback import BULL_REGIMES, TRAIL_ACTIVATION_PCT, TRAIL_DROP_UNKNOWN, trail_params

_KO_LABELS = {
    "parabolic": "폭주 강세장", "strong_bull": "강한 강세장", "moderate_bull": "보통 강세장",
    "sideways": "횡보장", "moderate_bear": "보통 약세장", "strong_bear": "강한 약세장",
}


def kr_live_regime_block(regime, context=None, language="ko", entry_regime=""):
    """Return the prompt block; regime None means the system value is unavailable."""
    ko = language == "ko"
    if not regime:
        # 2026-11-11 prompt diet (M4/X4): no AI self-judged regime; the code fallback (x0.90, no stop raise) applies.
        return ("### 현재 시장 국면 (시스템 계산 불가)\n"
                "시스템 국면을 계산하지 못했습니다. 국면을 직접 판단하지 마세요.\n" if ko else
                "### Current Market Regime (system value unavailable)\n"
                "The system regime could not be computed. Do not judge the regime yourself.\n") + trailing_band_line(None, language, entry_regime)
    bull = regime in BULL_REGIMES
    summary = (context or {}).get("index_summary") or {}
    facts = []
    if summary.get("kospi_current") is not None and summary.get("kospi_20d_ma") is not None:
        facts.append(f"KOSPI {summary['kospi_current']} / 20일선 {summary['kospi_20d_ma']}" if ko else
                     f"KOSPI {summary['kospi_current']} vs 20MA {summary['kospi_20d_ma']}")
    if summary.get("kospi_2w_change_pct") is not None:
        facts.append(f"2주 변화 {summary['kospi_2w_change_pct']}%" if ko else
                     f"2w change {summary['kospi_2w_change_pct']}%")
    facts = (" · ".join(facts) + "\n") if facts else ""
    if ko:
        mode = "A) 강세장 모드" if bull else "B) 약세장/횡보장 모드"
        return (f"### 현재 시장 국면 (시스템 실시간 계산 — 우선 적용)\n"
                f"{_KO_LABELS.get(regime, regime)}\n{facts}"
                f"시장 국면은 이 값을 그대로 따르고 {mode}를 적용하세요. 매수 시점 시나리오의 시장 상황보다 우선합니다.\n"
                + trailing_band_line(regime, language, entry_regime))
    mode = "A) bull-market mode" if bull else "B) bear/sideways mode"
    return (f"### Current Market Regime (LIVE, system-computed — authoritative)\n"
            f"{regime}\n{facts}"
            f"Use this value as the market regime and apply {mode}; it overrides the buy-time scenario.\n"
            + trailing_band_line(regime, language, entry_regime))


def trailing_band_line(regime, language="ko", entry_regime=""):
    """The trailing band the automatic exit uses for this regime and this position's buy-time regime
    (regime None = unavailable)."""
    ko = language == "ko"
    act, drop = trail_params(regime or "", bool(regime), entry_regime)
    start = "" if act == TRAIL_ACTIVATION_PCT else (
        f"이 종목은 매수 시점 국면({entry_regime})이 강세가 아니어서 고점이 매수가 대비 +{act:g}% 이상이면 트레일링이 켜집니다. " if ko else
        f"This position was bought in a non-bull regime ({entry_regime}), so the trailing stop activates once the peak "
        f"is +{act:g}% above entry. ")
    if not regime:
        return start + (f"자동 청산은 이때 진입 후 최고가 × {TRAIL_DROP_UNKNOWN:.2f}로 넓게 잡습니다. 오늘은 손절선을 trailing 수준으로 "
                        "올리지 마세요.\n" if ko else
                        f"The automatic exit then trails wider at highest price since entry × {TRAIL_DROP_UNKNOWN:.2f}. Do not raise "
                        "stop_loss to a trailing level today.\n")
    return start + (f"트레일링 스탑은 진입 후 최고가 × {drop:.2f}로 계산하세요. 자동 청산 규칙과 같은 기준이므로 이보다 좁게 잡지 마세요.\n"
                    if ko else
                    f"Compute the trailing stop as highest price since entry × {drop:.2f}. This matches the automatic exit rule, so "
                    "do not set it tighter.\n")
