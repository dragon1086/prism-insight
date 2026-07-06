"""고변동·낙폭 regime override 단위테스트 (KR cores).

목적: 장기이평 위에 '가격 레벨이 지연되어' 떠 있으나 실제로는 급락형 고변동(whipsaw)인
국면이 strong/moderate_bull 로 관대하게 분류돼 고점매수→손절이 반복되던 것을 방지.

검증 포인트:
  1) 순수 헬퍼 _high_vol_drawdown_override 의 발동/미발동 경계
  2) melt-up(신고가 부근 고변동, 낙폭≈0)은 절대 강등되지 않음 (과최적화/조기청산 경계)
  3) 평온한 상승은 moderate_bull 유지
  4) _compute_kr_regime 통합: 실제 7월형 급락 구조 → sideways 강등 + index_summary 사유 기록
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cores.data_prefetch import (  # noqa: E402
    _high_vol_drawdown_override,
    _compute_kr_regime,
    HIVOL_DD_VOL_PCT,
    HIVOL_DD_DRAWDOWN_PCT,
)


# ── 순수 헬퍼 테스트 ──────────────────────────────────────────────────────

def _rally_then(tail):
    """1년 랠리(3100->8300) 뒤 최근 종가 tail 을 붙인 close 배열."""
    base = list(np.linspace(3100, 8300, 110))
    return np.array(base + list(tail), dtype=float)


def test_helper_highvol_drawdown_downgrades_bull_to_sideways():
    # 급락형 휩쏘: 8600 고점 -> 7900 (낙폭 ~8%+), 큰 일간 진폭
    closes = _rally_then([8600, 8550, 8500, 8450, 8400, 8380, 8350, 8303, 7648, 7900])
    reg, conf, reason = _high_vol_drawdown_override(closes, "moderate_bull", 0.78)
    assert reg == "sideways"
    assert conf <= 0.55
    assert reason is not None and "sideways" in reason


def test_helper_meltup_not_downgraded():
    # melt-up: 신고가 부근 고변동(큰 상승 진폭)이지만 낙폭≈0 → 강등 금지
    closes = _rally_then([8000, 7600, 8100, 7700, 8300, 7900, 8500, 8100, 8700, 8650])
    reg, conf, reason = _high_vol_drawdown_override(closes, "moderate_bull", 0.78)
    assert reg == "moderate_bull"
    assert reason is None


def test_helper_calm_uptrend_unchanged():
    # 저변동 완만 상승, 낙폭 작음 → 유지
    closes = _rally_then([8250, 8270, 8290, 8310, 8330, 8350, 8370, 8390, 8410, 8430])
    reg, conf, reason = _high_vol_drawdown_override(closes, "moderate_bull", 0.78)
    assert reg == "moderate_bull"
    assert reason is None


def test_helper_lowvol_but_drawdown_not_downgraded():
    # 낙폭은 크지만 변동성이 낮으면(느린 하락) 발동 안 함 — vol AND dd 동시조건
    base = list(np.linspace(3100, 8300, 110))
    # 8300 -> 7500 완만 하락(하루 약 -1% 미만), 낙폭 ~9%지만 저변동
    tail = list(np.linspace(8300, 7500, 10))
    closes = np.array(base + tail, dtype=float)
    reg, conf, reason = _high_vol_drawdown_override(closes, "moderate_bull", 0.78)
    assert reg == "moderate_bull"
    assert reason is None


def test_helper_non_bull_regime_untouched():
    closes = _rally_then([8600, 8550, 8500, 8450, 8400, 8380, 8350, 8303, 7648, 7900])
    for r in ("sideways", "moderate_bear", "strong_bear"):
        reg, conf, reason = _high_vol_drawdown_override(closes, r, 0.6)
        assert reg == r
        assert reason is None


def test_helper_short_series_safe():
    reg, conf, reason = _high_vol_drawdown_override(np.array([100.0, 99.0]), "moderate_bull", 0.78)
    assert reg == "moderate_bull" and reason is None


# ── _compute_kr_regime 통합 테스트 ────────────────────────────────────────

def _ohlcv(closes):
    import datetime as dt
    d = {}
    day = dt.date(2026, 1, 1)
    for i, c in enumerate(closes):
        d[(day + dt.timedelta(days=i)).strftime("%Y-%m-%d")] = {
            "종가": float(c), "거래량": 1000.0 + i}
    return d


_JULY_CRASH = list(np.linspace(3100, 8300, 110)) + \
    [8600, 8550, 8500, 8450, 8400, 8380, 8350, 8303, 7648, 7900]


def test_integration_active_mode_downgrades():
    # active 모드: 실제 7월형 급락 휩쏘 → sideways 로 강등
    os.environ["REGIME_HIVOL_OVERRIDE"] = "active"
    try:
        r = _compute_kr_regime(_ohlcv(_JULY_CRASH), None)
    finally:
        os.environ.pop("REGIME_HIVOL_OVERRIDE", None)
    assert r["market_regime"] == "sideways"
    assert r["index_summary"]["highvol_drawdown_override"] is not None


def test_integration_shadow_mode_logs_but_does_not_apply():
    # shadow(기본): 강등 '판단'은 기록하되 regime 은 그대로(매매 무영향)
    os.environ.pop("REGIME_HIVOL_OVERRIDE", None)  # 기본값 = shadow
    r = _compute_kr_regime(_ohlcv(_JULY_CRASH), None)
    assert r["market_regime"] == "moderate_bull"          # 미적용
    assert r["index_summary"]["highvol_override_mode"] == "shadow"
    assert r["index_summary"]["highvol_drawdown_override"] is not None  # 사유는 기록


def test_integration_off_mode_no_field():
    os.environ["REGIME_HIVOL_OVERRIDE"] = "off"
    try:
        r = _compute_kr_regime(_ohlcv(_JULY_CRASH), None)
    finally:
        os.environ.pop("REGIME_HIVOL_OVERRIDE", None)
    assert r["market_regime"] == "moderate_bull"
    assert r["index_summary"]["highvol_drawdown_override"] is None


def test_integration_calm_bull_stays_moderate_bull():
    # 저변동 완만 상승 → moderate_bull 유지, override 미발동
    closes = list(np.linspace(3100, 8300, 110)) + \
        [8250, 8270, 8290, 8310, 8330, 8350, 8370, 8390, 8410, 8430]
    r = _compute_kr_regime(_ohlcv(closes), None)
    assert r["market_regime"] in ("moderate_bull", "strong_bull")
    assert r["index_summary"]["highvol_drawdown_override"] is None
