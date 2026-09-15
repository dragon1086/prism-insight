"""``get_multi_day_ohlcv`` 는 소스 체인을 거쳐야 한다.

2026-08-05 장애 재현 실행에서 KRX 시도 42회 중 **39회가 이 함수**였다. 배치에서
가장 뜨거운 KRX 경로인데 `krx_data_client` 를 직접 불러서, `PRISM_MARKET_DATA_SOURCES`
로 KIS 를 1순위에 올려도 이 경로만은 그대로 KRX 를 때렸다. 체인 순서가 무의미했던
것이고, 그게 호스트가 차단되는 이유이자 Plan A 가 없애려는 대상이다.

스로틀도 같이 옮겼다. `trigger_batch` 에 있을 때는 이 함수 한 군데만 보호했고
체인을 타는 나머지 KRX 호출은 전부 무방비였다. 간격 유지는 호출자가 아니라
**소스**의 책임이다.
"""

from __future__ import annotations

import pytest

pd = pytest.importorskip("pandas")

import trigger_batch  # noqa: E402


def _frame(rows: int = 30) -> pd.DataFrame:
    idx = pd.date_range("2026-07-01", periods=rows, freq="D")
    return pd.DataFrame(
        {
            "Open": range(rows),
            "High": range(rows),
            "Low": range(rows),
            "Close": range(rows),
            "Volume": range(rows),
        },
        index=idx,
    )


def test_reads_through_the_source_chain(monkeypatch):
    """체인 함수가 불려야 한다 — krx_data_client 직결이 아니다."""
    import cores.market_data as market_data

    seen = {}

    def _chain(start, end, ticker, adjusted=True):
        seen["args"] = (start, end, ticker)
        return _frame()

    monkeypatch.setattr(market_data, "get_market_ohlcv_by_date", _chain)

    result = trigger_batch.get_multi_day_ohlcv("005930", "20260731", days=10)

    assert seen["args"][2] == "005930"
    assert seen["args"][1] == "20260731"
    assert len(result) == 10  # tail(days)


def test_krx_data_client_module_is_gone():
    """The KRX provider was removed in the KIS-only migration — a direct call is
    now structurally impossible (no module exists to import)."""
    import importlib.util

    assert importlib.util.find_spec("krx_data_client") is None


def test_returns_empty_frame_when_chain_is_exhausted(monkeypatch):
    """체인 소진 시 예외가 아니라 빈 프레임을 준다.

    MA20 게이트가 빈 결과를 '모름 → 통과'로 읽으므로 예외를 올리면 안 된다.
    (FDR fallback 은 KIS-only 마이그레이션에서 제거됐다.)
    """
    import cores.market_data as market_data

    monkeypatch.setattr(
        market_data, "get_market_ohlcv_by_date", lambda *a, **k: pd.DataFrame()
    )

    assert trigger_batch.get_multi_day_ohlcv("005930", "20260731").empty


def test_chain_exception_does_not_propagate(monkeypatch):
    """체인이 터져도 배치를 죽이지 않는다 — 빈 프레임 반환."""
    import cores.market_data as market_data

    def _boom(*args, **kwargs):
        raise RuntimeError("chain exploded")

    monkeypatch.setattr(market_data, "get_market_ohlcv_by_date", _boom)

    assert trigger_batch.get_multi_day_ohlcv("005930", "20260731", days=3).empty
