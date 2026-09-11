"""KIS bulk master failure must preserve safe per-symbol label resolution."""

from __future__ import annotations

import pytest

pd = pytest.importorskip("pandas")

import trigger_batch  # noqa: E402


def _blocked(*args, **kwargs):
    raise RuntimeError("KIS master unavailable")


@pytest.fixture
def kis_master_down(monkeypatch):
    """KIS bulk master failure must not erase fallback labels."""
    monkeypatch.setattr(trigger_batch, "fetch_kis_master_universe", _blocked)
    monkeypatch.setattr(trigger_batch, "_TICKER_NAME_CACHE", None)


def test_bulk_lookup_degrades_to_empty_map_when_krx_is_blocked(kis_master_down):
    """전제 확인 — 대량 조회는 여전히 빈 맵으로 강등된다."""
    assert trigger_batch._get_ticker_name_map() == {}


def test_falls_back_to_the_source_chain_for_the_name(kis_master_down, monkeypatch):
    """빈 맵이어도 체인이 이름을 준다."""
    import cores.market_data as market_data

    monkeypatch.setattr(market_data, "get_market_ticker_name", lambda t: "삼성전기")

    assert trigger_batch._get_display_ticker_name("009150", {}) == "삼성전기"


def test_caches_the_resolved_name_into_the_map(monkeypatch):
    """한 번 찾은 이름은 맵에 적재해 같은 실행에서 다시 조회하지 않는다."""
    import cores.market_data as market_data

    calls = []

    def _counted(ticker):
        calls.append(ticker)
        return "삼성전기"

    monkeypatch.setattr(market_data, "get_market_ticker_name", _counted)

    name_map: dict[str, str] = {}
    assert trigger_batch._get_display_ticker_name("009150", name_map) == "삼성전기"
    assert trigger_batch._get_display_ticker_name("009150", name_map) == "삼성전기"

    assert calls == ["009150"]
    assert name_map["009150"] == "삼성전기"


def test_prefers_the_bulk_map_when_krx_is_healthy(monkeypatch):
    """KRX 가 살아 있으면 체인을 부르지 않는다 — 기존 경로가 우선이다."""
    import cores.market_data as market_data

    def _must_not_be_called(ticker):
        raise AssertionError("bulk map 이 있는데 체인을 불렀다")

    monkeypatch.setattr(market_data, "get_market_ticker_name", _must_not_be_called)

    assert trigger_batch._get_display_ticker_name("009150", {"009150": "삼성전기"}) == "삼성전기"


def test_falls_back_to_the_code_when_no_source_can_answer(monkeypatch):
    """체인도 못 찾으면 종목코드다 — 라벨 때문에 배치를 죽이지 않는다.

    ``cores.market_data.get_market_ticker_name`` 은 전 소스 소진 시 티커를 그대로
    돌려준다. 그걸 '해결됐다'고 착각하면 안 된다.
    """
    import cores.market_data as market_data

    monkeypatch.setattr(market_data, "get_market_ticker_name", lambda t: t)

    assert trigger_batch._get_display_ticker_name("009150", {}) == "009150"


def test_chain_failure_never_propagates(monkeypatch):
    """체인이 터져도 예외가 밖으로 나가지 않는다."""
    import cores.market_data as market_data

    monkeypatch.setattr(market_data, "get_market_ticker_name", _blocked)

    assert trigger_batch._get_display_ticker_name("009150", {}) == "009150"


def test_normalizes_short_codes_before_lookup(monkeypatch):
    """6자리 zero-fill 이 조회 전에 적용된다."""
    import cores.market_data as market_data

    seen = []

    def _capture(ticker):
        seen.append(ticker)
        return "삼성전기"

    monkeypatch.setattr(market_data, "get_market_ticker_name", _capture)

    assert trigger_batch._get_display_ticker_name(9150, {}) == "삼성전기"
    assert seen == ["009150"]
