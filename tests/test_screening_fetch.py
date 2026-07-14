"""
tests/test_screening_fetch.py — 스크리닝 OHLCV fetch 통합 테스트

Covers:
  (1) 골든 불변: 동일 합성 OHLCV에 대해 "옛 2-fetch 방식"과 "새 1-fetch 방식"의
      return_nd / extension_in_adr / extension_score 가 부동소수 오차 내 동일.
  (2) fallback: KRX(get_market_ohlcv_by_date) 빈 df / 예외 → FDR 데이터 반환.
      FDR도 실패 → 빈 df.
  (3) 데이터 부족(<260행) 종목: oneil_raw=None 가능, return_nd/extension 정상.

네트워크 의존 없음 — monkeypatch / unittest.mock 으로 스텁.
"""

import sys
import math
from pathlib import Path
from unittest.mock import patch, MagicMock

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import trigger_batch
from trigger_batch import (
    calculate_screening_signals,
    get_multi_day_ohlcv,
    SCREENING_SIGNAL_LOOKBACK_DAYS,
    RS_RATING_LOOKBACK_DAYS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ohlcv(n: int, start_close: float = 100.0, end_close: float = 110.0) -> pd.DataFrame:
    """n 행짜리 합성 OHLCV DataFrame (영문 컬럼). 종가는 선형 보간."""
    idx = pd.date_range("2023-01-01", periods=n, freq="B")
    closes = np.linspace(start_close, end_close, n)
    highs = closes * 1.01
    lows = closes * 0.99
    opens = closes * 0.995
    volumes = [1_000_000] * n
    return pd.DataFrame(
        {"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": volumes},
        index=idx,
    )


def _old_calculate_screening_signals(ticker, current_price, trade_date,
                                     lookback_days=SCREENING_SIGNAL_LOOKBACK_DAYS,
                                     df60=None, df260=None):
    """이전 2-fetch 방식 시뮬레이션 (monkeypatch 없이 직접 계산).

    df60: 60일 fetch 결과 (return_nd / extension 용)
    df260: 260일 fetch 결과 (oneil_raw 용)
    """
    from trigger_batch import _compute_extension_score
    from cores.rs_rating import oneil_weighted_return

    result = {"extension_in_adr": 0.0, "extension_score": 1.0, "return_nd": 0.0, "oneil_raw": None}
    if current_price <= 0:
        return result

    df = df60
    if df is None or df.empty or len(df) < 5:
        return result

    high_col = "High" if "High" in df.columns else "고가"
    low_col = "Low" if "Low" in df.columns else "저가"
    close_col = "Close" if "Close" in df.columns else "종가"
    if close_col not in df.columns:
        return result

    closes = df[close_col][df[close_col] > 0]
    if closes.empty:
        return result

    first_close = float(closes.iloc[0])
    if first_close > 0:
        result["return_nd"] = (current_price - first_close) / first_close * 100

    recent_closes = closes.tail(20)
    ma20 = float(recent_closes.mean()) if len(recent_closes) > 0 else 0.0
    if ma20 > 0 and high_col in df.columns and low_col in df.columns:
        hl = df.tail(20)
        valid = hl[(hl[high_col] > 0) & (hl[low_col] > 0)]
        if not valid.empty:
            adr_pct = float(((valid[high_col] / valid[low_col] - 1.0) * 100).mean())
            if adr_pct > 0:
                ext = ((current_price - ma20) / ma20 * 100) / adr_pct
                result["extension_in_adr"] = float(ext)
                result["extension_score"] = _compute_extension_score(ext)

    if df260 is not None and not df260.empty:
        c260 = "Close" if "Close" in df260.columns else "종가"
        if c260 in df260.columns:
            cl260 = df260[c260][df260[c260] > 0]
            result["oneil_raw"] = oneil_weighted_return(cl260)

    return result


# ---------------------------------------------------------------------------
# (1) 골든 불변: 새 1-fetch 방식 == 옛 2-fetch 방식
# ---------------------------------------------------------------------------

class TestGoldenInvariant:
    """return_nd / extension_in_adr / extension_score 값이 리팩토링 전후 동일."""

    def _run(self, n_rows: int, start_close: float, end_close: float, current_price: float):
        """n_rows ≥ 260 인 합성 데이터로 두 방식 비교."""
        df_full = _make_ohlcv(n_rows, start_close=start_close, end_close=end_close)
        df60 = df_full.tail(SCREENING_SIGNAL_LOOKBACK_DAYS)
        df260 = df_full.tail(RS_RATING_LOOKBACK_DAYS)

        # 옛 방식: 60일 fetch → extension/return_nd, 260일 fetch → oneil_raw
        old = _old_calculate_screening_signals(
            "MOCK", current_price, "20240101",
            lookback_days=SCREENING_SIGNAL_LOOKBACK_DAYS,
            df60=df60, df260=df260,
        )

        # 새 방식: 260일 1회 fetch, tail(60) 슬라이싱
        # get_multi_day_ohlcv 를 스텁 — RS_RATING_LOOKBACK_DAYS 요청 시 df260 반환
        def stub_fetch(ticker, end_date, days):
            return df_full.tail(days)

        with patch("trigger_batch.get_multi_day_ohlcv", side_effect=stub_fetch):
            new = calculate_screening_signals("MOCK", current_price, "20240101")

        assert math.isclose(old["return_nd"], new["return_nd"], abs_tol=1e-9), (
            f"return_nd mismatch: old={old['return_nd']}, new={new['return_nd']}"
        )
        assert math.isclose(old["extension_in_adr"], new["extension_in_adr"], abs_tol=1e-9), (
            f"extension_in_adr mismatch: old={old['extension_in_adr']}, new={new['extension_in_adr']}"
        )
        assert math.isclose(old["extension_score"], new["extension_score"], abs_tol=1e-9), (
            f"extension_score mismatch: old={old['extension_score']}, new={new['extension_score']}"
        )

    def test_uptrend_300rows(self):
        self._run(300, start_close=100.0, end_close=150.0, current_price=150.0)

    def test_downtrend_300rows(self):
        self._run(300, start_close=150.0, end_close=100.0, current_price=100.0)

    def test_flat_300rows(self):
        self._run(300, start_close=100.0, end_close=100.0, current_price=100.0)

    def test_exactly_260rows(self):
        """딱 260행일 때 tail(60) == 직접 60행 fetch 와 동일."""
        self._run(260, start_close=80.0, end_close=120.0, current_price=120.0)


# ---------------------------------------------------------------------------
# (2) KRX fallback 테스트 (KR only — get_multi_day_ohlcv in trigger_batch)
# ---------------------------------------------------------------------------

class TestKRXFallback:
    """KRX 빈 df 또는 예외 시 FinanceDataReader 로 fallback."""

    def _fdr_df(self, ticker="005930"):
        """FDR이 반환하는 스타일 DataFrame (컬럼명 소문자 또는 한글)."""
        df = _make_ohlcv(30, start_close=60000.0, end_close=65000.0)
        # FDR 실제 컬럼: Open/High/Low/Close/Volume (이미 영문 대문자인 경우도 있음)
        return df

    def test_krx_empty_triggers_fdr(self, monkeypatch):
        """get_market_ohlcv_by_date 빈 df → FDR DataReader 호출, 정규화된 df 반환."""
        fdr_result = self._fdr_df()

        import FinanceDataReader as fdr_module

        with patch("trigger_batch.get_multi_day_ohlcv.__wrapped__", create=True):
            pass  # get_multi_day_ohlcv 는 내부에서 import 함 — 직접 내부 mock

        # get_market_ohlcv_by_date 를 빈 df 반환으로 패치
        with patch("krx_data_client.get_market_ohlcv_by_date", return_value=pd.DataFrame()):
            with patch("FinanceDataReader.DataReader", return_value=fdr_result) as mock_fdr:
                result = get_multi_day_ohlcv("005930", "20240101", 30)

        mock_fdr.assert_called_once()
        assert not result.empty, "FDR fallback 후 결과가 비어있어서는 안 됨"
        assert "Close" in result.columns, "Close 컬럼이 있어야 함"

    def test_krx_exception_triggers_fdr(self, monkeypatch):
        """get_market_ohlcv_by_date 예외 → FDR fallback."""
        fdr_result = self._fdr_df()

        with patch("krx_data_client.get_market_ohlcv_by_date", side_effect=RuntimeError("KRX down")):
            with patch("FinanceDataReader.DataReader", return_value=fdr_result) as mock_fdr:
                result = get_multi_day_ohlcv("005930", "20240101", 30)

        mock_fdr.assert_called_once()
        assert not result.empty

    def test_krx_empty_fdr_also_fails(self):
        """KRX 빈 df + FDR도 예외 → 빈 df 반환 (오류 전파 없음)."""
        with patch("krx_data_client.get_market_ohlcv_by_date", return_value=pd.DataFrame()):
            with patch("FinanceDataReader.DataReader", side_effect=Exception("FDR also down")):
                result = get_multi_day_ohlcv("005930", "20240101", 10)

        assert result.empty

    def test_krx_empty_fdr_empty(self):
        """KRX 빈 df + FDR도 빈 df → 빈 df 반환."""
        with patch("krx_data_client.get_market_ohlcv_by_date", return_value=pd.DataFrame()):
            with patch("FinanceDataReader.DataReader", return_value=pd.DataFrame()):
                result = get_multi_day_ohlcv("005930", "20240101", 10)

        assert result.empty

    def test_fdr_column_normalization(self):
        """FDR 소문자 컬럼 → 영문 대문자로 정규화 확인."""
        # FDR이 소문자 컬럼을 반환하는 경우 시뮬레이션
        raw_fdr = pd.DataFrame({
            "open": [100.0], "high": [102.0], "low": [99.0],
            "close": [101.0], "volume": [500000],
        }, index=pd.date_range("2024-01-01", periods=1))

        with patch("krx_data_client.get_market_ohlcv_by_date", return_value=pd.DataFrame()):
            with patch("FinanceDataReader.DataReader", return_value=raw_fdr):
                result = get_multi_day_ohlcv("005930", "20240101", 5)

        assert "Close" in result.columns, f"Close 컬럼 없음, 컬럼: {list(result.columns)}"
        assert "High" in result.columns
        assert "Low" in result.columns


# ---------------------------------------------------------------------------
# (3) 데이터 부족 종목 (<260행)
# ---------------------------------------------------------------------------

class TestThinData:
    """260행 미만 종목: oneil_raw=None 가능, return_nd/extension 은 정상."""

    def test_thin_data_50rows(self):
        """50행만 있을 때 return_nd 는 계산되고 oneil_raw 는 None 이 될 수 있다."""
        df_thin = _make_ohlcv(50, start_close=100.0, end_close=110.0)

        def stub_fetch(ticker, end_date, days):
            return df_thin.tail(days)

        with patch("trigger_batch.get_multi_day_ohlcv", side_effect=stub_fetch):
            result = calculate_screening_signals("THIN", 110.0, "20240101")

        # 50행 < 260이므로 tail(260) = 50행, tail(60) = 50행 → 계산 가능
        # return_nd: (110 - first_close) / first_close * 100 — 양수 기대
        assert result["return_nd"] > 0.0, f"return_nd expected >0, got {result['return_nd']}"
        # extension_score 는 0~1
        assert 0.0 <= result["extension_score"] <= 1.0

    def test_thin_data_3rows_safe_defaults(self):
        """3행 — len(df) < 5 → 안전 기본값 반환."""
        df_tiny = _make_ohlcv(3)

        def stub_fetch(ticker, end_date, days):
            return df_tiny.tail(days)

        with patch("trigger_batch.get_multi_day_ohlcv", side_effect=stub_fetch):
            result = calculate_screening_signals("TINY", 100.0, "20240101")

        assert result["extension_score"] == 1.0
        assert result["return_nd"] == 0.0

    def test_empty_fetch_safe_defaults(self):
        """fetch 완전 실패 → 안전 기본값."""
        with patch("trigger_batch.get_multi_day_ohlcv", return_value=pd.DataFrame()):
            result = calculate_screening_signals("EMPTY", 100.0, "20240101")

        assert result == {"extension_in_adr": 0.0, "extension_score": 1.0, "return_nd": 0.0, "oneil_raw": None}

    def test_zero_price_safe_defaults(self):
        """current_price <= 0 → 즉시 안전 기본값."""
        result = calculate_screening_signals("ZERO", 0.0, "20240101")
        assert result == {"extension_in_adr": 0.0, "extension_score": 1.0, "return_nd": 0.0, "oneil_raw": None}
