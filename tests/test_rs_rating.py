"""
tests/test_rs_rating.py — O'Neil RS Rating 공용 모듈 단위 테스트

Covers:
  (a) oneil_weighted_return: 알려진 시계열로 기대값 정확 비교, 히스토리 부족 시 None
  (b) percentile_ratings: 순서/1~99/동점/단일=50
  (c) SHADOW 기본(RS_RATING_ENABLED 미설정)에서 기존 rs_score 경로 유지
  (d) LIVE 플래그에서 rs_score가 oneil 백분위로, oneil_raw=None은 fallback

네트워크 의존 없음 — 순수 계산 함수만 테스트.
"""

import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from cores.rs_rating import oneil_weighted_return, percentile_ratings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_closes(n: int, base: float = 100.0, growth_rate: float = 0.001) -> pd.Series:
    """n 개 종가 시계열 (기하급수 성장). 날짜 인덱스 포함."""
    prices = [base * (1.0 + growth_rate) ** i for i in range(n)]
    idx = pd.date_range("2023-01-01", periods=n, freq="B")
    return pd.Series(prices, index=idx)


# ---------------------------------------------------------------------------
# (a) oneil_weighted_return
# ---------------------------------------------------------------------------

class TestOneilWeightedReturn:
    def test_insufficient_history_252_returns_none(self):
        """len == 252 → None (252 이하면 근사 없이 None)."""
        closes = _make_closes(252)
        assert oneil_weighted_return(closes) is None

    def test_insufficient_history_below_252_returns_none(self):
        """len < 252 → None."""
        closes = _make_closes(100)
        assert oneil_weighted_return(closes) is None

    def test_253_points_returns_float(self):
        """253 개는 충분 — float 반환."""
        closes = _make_closes(253)
        result = oneil_weighted_return(closes)
        assert isinstance(result, float)

    def test_flat_series_raw_zero(self):
        """모든 종가 동일 → R_n = 0 → raw = 0.0."""
        closes = _make_closes(300, base=100.0, growth_rate=0.0)
        result = oneil_weighted_return(closes)
        assert result is not None
        assert abs(result) < 1e-9

    def test_rising_series_positive(self):
        """상승 시계열 → raw > 0."""
        closes = _make_closes(300, base=100.0, growth_rate=0.002)
        result = oneil_weighted_return(closes)
        assert result is not None
        assert result > 0

    def test_falling_series_negative(self):
        """하락 시계열 → raw < 0."""
        closes = _make_closes(300, base=200.0, growth_rate=-0.001)
        result = oneil_weighted_return(closes)
        assert result is not None
        assert result < 0

    def test_known_formula_exact(self):
        """2*R63 + R126 + R189 + R252 수식 정확 비교."""
        n = 300
        closes = _make_closes(n, base=100.0, growth_rate=0.001)
        arr = closes.sort_index().values
        p0 = arr[-1]

        def r(k):
            p_k = arr[-1 - k]
            return (p0 - p_k) / p_k if p_k > 0 else 0.0

        expected = 2.0 * r(63) + r(126) + r(189) + r(252)
        result = oneil_weighted_return(closes)
        assert result is not None
        assert abs(result - expected) < 1e-9

    def test_defensive_sort_out_of_order(self):
        """비정렬 입력도 sort_index() 후 동일 결과."""
        closes_ordered = _make_closes(300, base=100.0, growth_rate=0.001)
        closes_shuffled = closes_ordered.sample(frac=1, random_state=42)
        r_ord = oneil_weighted_return(closes_ordered)
        r_shuf = oneil_weighted_return(closes_shuffled)
        assert r_ord is not None and r_shuf is not None
        assert abs(r_ord - r_shuf) < 1e-9


# ---------------------------------------------------------------------------
# (b) percentile_ratings
# ---------------------------------------------------------------------------

class TestPercentileRatings:
    def test_empty_input_returns_empty(self):
        assert percentile_ratings({}) == {}

    def test_single_item_returns_50(self):
        result = percentile_ratings({"AAPL": 1.5})
        assert result == {"AAPL": 50.0}

    def test_all_values_in_1_to_99(self):
        raw = {f"T{i}": float(i) for i in range(20)}
        result = percentile_ratings(raw)
        for v in result.values():
            assert 1.0 <= v <= 99.0, f"Out of range: {v}"

    def test_ordering_preserved(self):
        """높은 raw → 높은 백분위."""
        raw = {"A": 10.0, "B": 20.0, "C": 30.0}
        result = percentile_ratings(raw)
        assert result["C"] > result["B"] > result["A"]

    def test_ties_get_same_percentile(self):
        """동점은 같은 백분위."""
        raw = {"A": 10.0, "B": 10.0, "C": 20.0}
        result = percentile_ratings(raw)
        assert result["A"] == result["B"]
        assert result["C"] > result["A"]

    def test_two_items(self):
        """2개: 낮은 쪽 < 높은 쪽, 모두 1~99."""
        result = percentile_ratings({"A": 5.0, "B": 10.0})
        assert result["B"] > result["A"]
        assert 1.0 <= result["A"] <= 99.0
        assert 1.0 <= result["B"] <= 99.0

    def test_high_performer_close_to_99(self):
        """많은 종목 중 최고값 → 99에 근접."""
        raw = {f"T{i}": float(i) for i in range(100)}
        result = percentile_ratings(raw)
        assert result["T99"] == 99.0


# ---------------------------------------------------------------------------
# (c) SHADOW 기본: rs_score 경로 불변
# (d) LIVE 플래그: rs_score = oneil 백분위
# ---------------------------------------------------------------------------

class TestShadowLiveLogic:
    """SHADOW/LIVE 분기 로직을 순수 계산으로 테스트.
    실제 trigger_batch import 없이 동일 패턴을 검증.
    """

    def _build_screening(self, oneil_map: dict) -> dict:
        """ticker -> screening_signals 스텁 생성."""
        signals = {}
        base_return = 10.0
        for i, (ticker, oneil_raw) in enumerate(oneil_map.items()):
            signals[ticker] = {
                "extension_in_adr": 0.0,
                "extension_score": 1.0,
                "return_nd": base_return + i * 5.0,
                "oneil_raw": oneil_raw,
            }
        return signals

    def _compute_rs_score_map(self, screening_signals: dict) -> dict:
        """return_nd 기반 min-max 정규화 (기존 로직 모사)."""
        if not screening_signals:
            return {}
        returns = [s["return_nd"] for s in screening_signals.values()]
        r_min, r_max = min(returns), max(returns)
        r_range = r_max - r_min if r_max > r_min else 0.0
        return {t: ((s["return_nd"] - r_min) / r_range) if r_range > 0 else 0.5
                for t, s in screening_signals.items()}

    def _apply_shadow_live(self, rs_score_map: dict, screening_signals: dict,
                           enabled: bool) -> dict:
        """SHADOW/LIVE 분기 로직 (trigger_batch와 동일 패턴)."""
        oneil_raw_map = {t: s["oneil_raw"] for t, s in screening_signals.items()
                         if s.get("oneil_raw") is not None}
        oneil_pct_map = percentile_ratings(oneil_raw_map) if oneil_raw_map else {}
        result = dict(rs_score_map)
        if enabled:
            for t in list(screening_signals.keys()):
                if t in oneil_pct_map:
                    result[t] = oneil_pct_map[t] / 99.0
        return result, oneil_pct_map

    def test_shadow_rs_score_unchanged(self, monkeypatch):
        """RS_RATING_ENABLED 미설정 → rs_score_map 변화 없음."""
        monkeypatch.delenv("RS_RATING_ENABLED", raising=False)
        enabled = os.getenv("RS_RATING_ENABLED", "false").strip().lower() == "true"
        assert not enabled

        signals = self._build_screening({"AAPL": 1.5, "MSFT": 2.5, "NVDA": 3.0})
        baseline = self._compute_rs_score_map(signals)
        result, _ = self._apply_shadow_live(dict(baseline), signals, enabled)
        assert result == baseline

    def test_live_rs_score_replaced_with_oneil_pct(self, monkeypatch):
        """RS_RATING_ENABLED=true → rs_score = oneil_pct / 99.0."""
        monkeypatch.setenv("RS_RATING_ENABLED", "true")
        enabled = os.getenv("RS_RATING_ENABLED", "false").strip().lower() == "true"
        assert enabled

        signals = self._build_screening({"AAPL": 1.5, "MSFT": 2.5})
        rs_score_map = self._compute_rs_score_map(signals)
        result, oneil_pct_map = self._apply_shadow_live(dict(rs_score_map), signals, enabled)

        for ticker in signals:
            assert 0.0 <= result[ticker] <= 1.0
            expected = oneil_pct_map[ticker] / 99.0
            assert abs(result[ticker] - expected) < 1e-9

    def test_live_none_oneil_raw_keeps_fallback(self, monkeypatch):
        """LIVE: oneil_raw=None 종목은 return_nd 기반 rs_score 유지."""
        monkeypatch.setenv("RS_RATING_ENABLED", "true")
        enabled = os.getenv("RS_RATING_ENABLED", "false").strip().lower() == "true"

        # "NEW": 상장 이력 부족, "OLD": 충분한 이력
        signals = self._build_screening({"NEW": None, "OLD": 1.5})
        rs_score_map = self._compute_rs_score_map(signals)
        baseline_new = rs_score_map["NEW"]

        result, oneil_pct_map = self._apply_shadow_live(dict(rs_score_map), signals, enabled)

        # NEW(oneil_raw=None)는 기존 return_nd 기반 그대로
        assert abs(result["NEW"] - baseline_new) < 1e-9
        # OLD는 oneil 백분위로 교체
        assert abs(result["OLD"] - oneil_pct_map["OLD"] / 99.0) < 1e-9

    def test_live_all_none_rs_score_unchanged(self, monkeypatch):
        """LIVE이더라도 모두 oneil_raw=None → rs_score 전혀 변경 없음."""
        monkeypatch.setenv("RS_RATING_ENABLED", "true")
        enabled = os.getenv("RS_RATING_ENABLED", "false").strip().lower() == "true"

        signals = self._build_screening({"A": None, "B": None})
        rs_score_map = self._compute_rs_score_map(signals)
        baseline = dict(rs_score_map)

        result, _ = self._apply_shadow_live(dict(rs_score_map), signals, enabled)
        assert result == baseline
