"""KRX 가 죽어도 종목명이 종목코드로 강등되지 않아야 한다.

2026-08-05 오후 시그널 얼럿이 **종목코드만** 달고 사용자에게 나갔다:

    🚀 일중 상승률 상위주
    · 009150 (009150)      ← "삼성전기 (009150)" 이어야 한다
    · 103140 (103140)      ← 풍산
    · 003490 (003490)      ← 대한항공

원인은 ``_get_ticker_name_map()`` 의 KRX 대량 조회 **한 번**이다. 실패하면 빈
dict 를 캐시하고, 그 뒤 모든 종목이 조용히 코드로 표시된다. 예외도 안 나고
로그 한 줄(`ticker name lookup failed`)만 남는다 — 그날 로그에 정확히 2건 있었다.

수정은 ``_get_display_ticker_name`` 이 ``cores.market_data`` 체인으로 물러나게
한 것이다. FDR 소스가 KRX 상장목록 스냅샷에서 답하는데, 차단된 Data Marketplace
세션과 무관하다.
"""

from __future__ import annotations

import pytest

pd = pytest.importorskip("pandas")

import trigger_batch  # noqa: E402


def _blocked(*args, **kwargs):
    raise RuntimeError("KRX 접근이 차단된 상태입니다. 198분 뒤(18:04)에 다시 시도하세요.")


@pytest.fixture
def krx_down(monkeypatch):
    """KRX 대량 조회를 막는다.

    ``trigger_batch`` 는 ``_get_client`` 를 모듈 최상단에서 직접 import 하므로
    ``krx_data_client._get_client`` 를 막아도 소용없다. 이 모듈이 들고 있는
    참조를 막아야 한다 — 처음 이 테스트를 짤 때 실제로 여기서 헛짚었다.
    """
    monkeypatch.setattr(trigger_batch, "_get_client", _blocked)
    monkeypatch.setattr(trigger_batch.stock_api, "get_market_ticker_name", _blocked)
    monkeypatch.setattr(trigger_batch, "_TICKER_NAME_CACHE", None)


def test_bulk_lookup_degrades_to_empty_map_when_krx_is_blocked(krx_down):
    """전제 확인 — 대량 조회는 여전히 빈 맵으로 강등된다."""
    assert trigger_batch._get_ticker_name_map() == {}


def test_falls_back_to_the_source_chain_for_the_name(krx_down, monkeypatch):
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
