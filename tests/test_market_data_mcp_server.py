"""리포트용 MCP 서버는 체인 위에서 돌고, 없는 데이터를 침묵으로 넘기지 않는다.

2026-08-05 리포트에서 기술적 지표가 통째로 "산출 불가"로 나갔다. 원인은 리포트
에이전트가 쓰는 `kospi_kosdaq` MCP 서버가 KRX 를 카카오 로그인으로 스크래핑하는
PyPI 패키지였고, KRX 가 IP 를 막았기 때문이다. 차트는 멀쩡했다 —
`cores/stock_chart.py` 는 소스 체인을 타는데 리포트 텍스트만 이 경로를 탔다.

이 서버는 같은 도구 계약을 유지한 채 `cores.market_data` 체인 위에서 돈다.
서버 이름까지 같아서 `report_generator` 의 프롬프트는 바뀌지 않는다.
"""

from __future__ import annotations

import pytest

pd = pytest.importorskip("pandas")
pytest.importorskip("mcp")

from cores.market_data import mcp_server as srv  # noqa: E402


def _ohlcv(rows: int = 3) -> pd.DataFrame:
    idx = pd.to_datetime(["2026-08-03", "2026-08-04", "2026-08-05"][:rows])
    return pd.DataFrame(
        {"Open": [1, 2, 3][:rows], "Close": [2, 3, 4][:rows]}, index=idx
    )


# --- 입력 정규화 -------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [("20260805", "20260805"), (20260805, "20260805"), ("2026-08-05", "20260805")],
)
def test_validate_date_normalizes(raw, expected):
    assert srv.validate_date(raw) == expected


def test_validate_date_rejects_garbage():
    with pytest.raises(ValueError):
        srv.validate_date("august")


def test_validate_ticker_zero_fills():
    """LLM 이 정수로 보내면 앞의 0 이 날아간다 — 9150 은 009150 이다."""
    assert srv.validate_ticker(9150) == "009150"
    assert srv.validate_ticker("009150") == "009150"


# --- 체인 경유 ---------------------------------------------------------------


def test_ohlcv_reads_through_the_chain(monkeypatch):
    seen = {}

    def _chain(start, end, ticker, adjusted=True):
        seen["args"] = (start, end, ticker, adjusted)
        return _ohlcv()

    monkeypatch.setattr(srv, "get_market_ohlcv_by_date", _chain)

    result = srv.get_stock_ohlcv("2026-07-01", 20260805, 9150)

    assert seen["args"] == ("20260701", "20260805", "009150", True)
    assert "2026-08-05" in result


def test_ohlcv_is_sorted_newest_first():
    """기존 서버와 같은 정렬 — 프롬프트가 '최근' 을 앞에서 읽는다."""
    frame = _ohlcv()
    assert list(srv._frame_to_dated_dict(frame)) == [
        "2026-08-05",
        "2026-08-04",
        "2026-08-03",
    ]


def test_trading_volume_reads_through_the_chain(monkeypatch):
    called = {}

    def _chain(start, end, ticker):
        called["ticker"] = ticker
        return _ohlcv()

    monkeypatch.setattr(srv, "get_market_trading_volume_by_date", _chain)

    assert "error" not in srv.get_stock_trading_volume("20260701", "20260805", "005930")
    assert called["ticker"] == "005930"


def test_index_ohlcv_keeps_the_index_code_unpadded(monkeypatch):
    """지수 코드는 종목코드가 아니다 — 1001 을 001001 로 만들면 안 된다."""
    seen = {}

    def _chain(start, end, index_ticker):
        seen["code"] = index_ticker
        return _ohlcv()

    monkeypatch.setattr(srv, "get_index_ohlcv_by_date", _chain)

    srv.get_index_ohlcv("20260701", "20260805", 1001)

    assert seen["code"] == "1001"


# --- 없는 데이터를 침묵으로 넘기지 않는다 -------------------------------------


def test_empty_result_becomes_an_explicit_error(monkeypatch):
    """빈 dict 를 주면 LLM 이 '없다'와 '0 이다'를 구분 못 한다.

    2026-08-05 MA200 건이 정확히 그 실패 모드였다 — 없는 줄을 '언급할 가치 없음'
    으로 읽고 스스로 지어냈다.
    """
    monkeypatch.setattr(
        srv, "get_market_ohlcv_by_date", lambda *a, **k: pd.DataFrame()
    )

    result = srv.get_stock_ohlcv("20260701", "20260805", "005930")

    assert "error" in result
    assert "추정치로 서술하지 마십시오" in result["error"]


def test_chain_exception_is_reported_not_raised(monkeypatch):
    """MCP 도구는 예외를 밖으로 못 넘긴다 — 오류 문자열로 돌려준다."""

    def _boom(*args, **kwargs):
        raise RuntimeError("chain exploded")

    monkeypatch.setattr(srv, "get_market_ohlcv_by_date", _boom)

    result = srv.get_stock_ohlcv("20260701", "20260805", "005930")

    assert "error" in result and "chain exploded" in result["error"]


def test_ticker_name_does_not_pass_off_the_code_as_a_name(monkeypatch):
    """체인은 답할 소스가 없으면 티커를 그대로 준다. 그걸 이름이라 하지 않는다."""
    monkeypatch.setattr(srv, "get_market_ticker_name", lambda t: t)

    assert "error" in srv.get_ticker_name("009150")


def test_ticker_name_returns_the_resolved_name(monkeypatch):
    monkeypatch.setattr(srv, "get_market_ticker_name", lambda t: "삼성전기")

    assert srv.get_ticker_name("009150") == {"ticker": "009150", "name": "삼성전기"}


# --- 리포트 전용 소스 순서 ---------------------------------------------------


def test_report_source_override_wins(monkeypatch):
    monkeypatch.setenv("PRISM_MARKET_DATA_SOURCES", "kis,fdr,krx")
    monkeypatch.setenv("PRISM_REPORT_DATA_SOURCES", "krx,fdr")

    assert srv.apply_report_source_order() == "krx,fdr"
    assert srv.os.environ["PRISM_MARKET_DATA_SOURCES"] == "krx,fdr"


def test_falls_back_to_the_general_chain_order(monkeypatch):
    monkeypatch.setenv("PRISM_MARKET_DATA_SOURCES", "kis,fdr,krx")
    monkeypatch.setenv("PRISM_REPORT_DATA_SOURCES", "")

    assert srv.apply_report_source_order() == "kis,fdr,krx"


# --- 도구 계약 유지 -----------------------------------------------------------


def test_exposes_the_same_tool_names_the_prompts_reference():
    """report_generator 프롬프트가 이름으로 지목하는 도구들이다."""
    for name in (
        "get_stock_ohlcv",
        "get_stock_market_cap",
        "get_stock_fundamental",
        "get_stock_trading_volume",
        "get_index_ohlcv",
    ):
        assert callable(getattr(srv, name)), name


def test_server_name_matches_the_configured_mcp_server():
    """서버 이름이 바뀌면 report_generator 의 server_names 를 전부 고쳐야 한다."""
    assert srv.mcp.name == "kospi_kosdaq"
