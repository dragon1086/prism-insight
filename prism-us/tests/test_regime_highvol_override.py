"""US 고변동·낙폭 regime override 스모크 테스트 (prism-us cores).

KR cores 와 동일 구현의 병렬 포크. 급락형 휩쏘 → sideways 강등, melt-up 미발동,
평온 상승 유지, 그리고 _compute_us_regime 통합(200MA 브랜치)을 합성데이터로 검증.
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cores.data_prefetch import (  # noqa: E402
    _high_vol_drawdown_override,
    _compute_us_regime,
)


def _rally_then(tail):
    base = list(np.linspace(3000, 6000, 210))  # 장기 랠리 → 50/200선 위
    return np.array(base + list(tail), dtype=float)


def test_helper_highvol_drawdown_downgrades():
    closes = _rally_then([6200, 6150, 6100, 6050, 6000, 5980, 5950, 5900, 5500, 5650])
    reg, conf, reason = _high_vol_drawdown_override(closes, "moderate_bull", 0.78)
    assert reg == "sideways" and reason is not None and conf <= 0.55


def test_helper_meltup_not_downgraded():
    closes = _rally_then([5600, 5300, 5700, 5350, 5800, 5450, 5900, 5600, 6100, 6050])
    reg, conf, reason = _high_vol_drawdown_override(closes, "moderate_bull", 0.78)
    assert reg == "moderate_bull" and reason is None


def test_helper_calm_uptrend_unchanged():
    closes = _rally_then([5960, 5970, 5980, 5990, 6000, 6010, 6020, 6030, 6040, 6050])
    reg, conf, reason = _high_vol_drawdown_override(closes, "moderate_bull", 0.78)
    assert reg == "moderate_bull" and reason is None


def _sp_df(closes):
    idx = pd.date_range("2025-01-01", periods=len(closes), freq="D")
    return pd.DataFrame({"Close": closes, "Volume": np.arange(len(closes)) + 1000.0}, index=idx)


def test_integration_us_crash_whipsaw_downgraded():
    # override가 가치를 더하는 좁은 사각지대: 최근 급등(spike)으로 50일선 '위'를 유지하면서
    # 그 고점 대비 순하락(net<=-3%)·고변동으로 되밀리는 경우. (순하락이 크면 보통 50선을
    # 깨고 기존 로직이 sideways 처리하므로, spike 로 50MA 를 낮게 눌러둔 구성)
    # 고점(peak)은 11~20일 전, 최근 11일은 net<=-3%로 되밀림 → override 발동(검증된 계열).
    closes = list(np.linspace(3000, 5600, 200)) + \
        [5750, 6000, 6250, 6400, 6500] + \
        [6250, 6450, 6000, 6300, 5800, 6150, 5650, 6000, 5650, 5950]
    r = _compute_us_regime(_sp_df(closes), None, None)
    assert r["index_summary"]["highvol_drawdown_override"] is not None
    assert r["market_regime"] == "sideways"


def test_integration_us_calm_bull_stays():
    closes = list(np.linspace(3000, 6000, 210)) + \
        [5960, 5970, 5980, 5990, 6000, 6010, 6020, 6030, 6040, 6050]
    r = _compute_us_regime(_sp_df(closes), None, None)
    assert r["market_regime"] in ("moderate_bull", "strong_bull")
    assert r["index_summary"]["highvol_drawdown_override"] is None
