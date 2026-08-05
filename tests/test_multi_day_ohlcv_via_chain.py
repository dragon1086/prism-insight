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


def test_does_not_call_krx_data_client_directly(monkeypatch):
    """KIS 가 답하면 KRX 는 아예 안 불린다 — 이게 이 변경의 목적이다."""
    import cores.market_data as market_data
    import krx_data_client

    def _must_not_be_called(*args, **kwargs):
        raise AssertionError("krx_data_client 를 직접 불렀다")

    monkeypatch.setattr(
        krx_data_client, "get_market_ohlcv_by_date", _must_not_be_called
    )
    monkeypatch.setattr(
        market_data, "get_market_ohlcv_by_date", lambda *a, **k: _frame()
    )

    assert not trigger_batch.get_multi_day_ohlcv("005930", "20260731", days=5).empty


def test_falls_back_to_fdr_when_every_source_is_exhausted(monkeypatch):
    """체인은 소진 시 예외가 아니라 빈 프레임을 준다. 그때만 FDR 재시도다."""
    import cores.market_data as market_data

    monkeypatch.setattr(
        market_data, "get_market_ohlcv_by_date", lambda *a, **k: pd.DataFrame()
    )

    fdr_stub = pytest.importorskip("FinanceDataReader")
    monkeypatch.setattr(fdr_stub, "DataReader", lambda *a, **k: _frame())

    result = trigger_batch.get_multi_day_ohlcv("005930", "20260731", days=7)

    assert len(result) == 7


def test_returns_empty_frame_when_chain_and_fdr_both_fail(monkeypatch):
    """MA20 게이트가 0 을 '모름 → 통과'로 읽으므로 예외를 올리면 안 된다."""
    import cores.market_data as market_data

    monkeypatch.setattr(
        market_data, "get_market_ohlcv_by_date", lambda *a, **k: pd.DataFrame()
    )
    fdr_stub = pytest.importorskip("FinanceDataReader")

    def _boom(*args, **kwargs):
        raise RuntimeError("FDR down")

    monkeypatch.setattr(fdr_stub, "DataReader", _boom)

    assert trigger_batch.get_multi_day_ohlcv("005930", "20260731").empty


def test_chain_exception_does_not_propagate(monkeypatch):
    """체인이 터져도 배치를 죽이지 않는다."""
    import cores.market_data as market_data

    def _boom(*args, **kwargs):
        raise RuntimeError("chain exploded")

    monkeypatch.setattr(market_data, "get_market_ohlcv_by_date", _boom)
    fdr_stub = pytest.importorskip("FinanceDataReader")
    monkeypatch.setattr(fdr_stub, "DataReader", lambda *a, **k: _frame())

    assert not trigger_batch.get_multi_day_ohlcv("005930", "20260731", days=3).empty


# --- 스로틀이 소스로 옮겨갔다 ------------------------------------------------


def test_throttle_now_lives_in_the_krx_source():
    """호출자가 아니라 소스가 간격을 지킨다."""
    from cores.market_data import krx_source

    assert hasattr(krx_source, "_throttle")
    # trigger_batch 에는 더 이상 없다.
    assert not hasattr(trigger_batch, "_krx_throttle")


def test_krx_source_throttles_before_handing_out_the_client(monkeypatch):
    """``_client()`` 를 얻을 때마다 간격이 적용된다."""
    from cores.market_data import krx_source

    calls = []
    monkeypatch.setattr(krx_source, "_throttle", lambda: calls.append(1))

    krx_source.KrxSource._client()
    krx_source.KrxSource._client()

    assert len(calls) == 2


def test_throttle_can_be_disabled_by_env(monkeypatch):
    """KRX_MIN_GAP_SEC=0 이면 대기가 없다 (테스트·백필용)."""
    from cores.market_data import krx_source

    monkeypatch.setenv("KRX_MIN_GAP_SEC", "0")

    slept = []
    monkeypatch.setattr(krx_source.time, "sleep", lambda s: slept.append(s))

    krx_source._throttle()
    krx_source._throttle()

    assert slept == []


def test_throttle_survives_a_garbage_env_value(monkeypatch):
    """잘못된 값이 스로틀을 끄거나 터뜨리지 않는다 — 기본값으로 돌아간다."""
    from cores.market_data import krx_source

    monkeypatch.setenv("KRX_MIN_GAP_SEC", "not-a-number")
    monkeypatch.setattr(krx_source, "_LAST_CALL", [0.0])

    slept = []
    monkeypatch.setattr(krx_source.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(krx_source.time, "monotonic", lambda: 0.0)

    krx_source._throttle()

    assert slept and slept[0] >= 0.4
