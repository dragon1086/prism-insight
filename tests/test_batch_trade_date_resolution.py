"""배치 기준 거래일 해석은 KRX 가 죽어도 살아남아야 한다.

2026-08-05 오후 KR 배치가 **리포트 0건**으로 끝났다. 실패 지점은 ``run_batch`` 의
첫 데이터 호출이었다:

    trigger_batch.py  trade_date = stock_api.get_nearest_business_day_in_a_week(...)
    → krx_data_client → KRXBlockedError: KRX 접근이 차단된 상태입니다
    → "No stocks selected. Terminating process."   (시작 64초 만에)

차단은 전날 8/4 18:04 에 걸린 것이라 그 배치가 유발한 것도 아니었다.

이 모듈의 다른 KRX 호출 6곳은 이미 보호돼 있었다 — 종목명 조회는 try/except 로
코드 표시로 강등되고, 스냅샷·시총 조회는 ``load_market_snapshot_bundle`` 의 Naver
폴백에 덮인다. **무방비였던 것은 이 한 줄뿐이고, 하필 가장 먼저 실행됐다.**

그래서 검증의 핵심은 "KRX 가 예외를 던져도 거래일이 나오는가" 하나다.
"""

from __future__ import annotations

import datetime

import pytest

pd = pytest.importorskip("pandas")

import trigger_batch  # noqa: E402


class _KrxDown:
    """KRX 를 부르면 무조건 터지게 만든다 (차단 상태 재현)."""

    def __init__(self):
        self.called = 0

    def __call__(self, *args, **kwargs):
        self.called += 1
        raise RuntimeError("KRX 접근이 차단된 상태입니다. 198분 뒤(18:04)에 다시 시도하세요.")


def _patch_krx(monkeypatch) -> _KrxDown:
    down = _KrxDown()
    monkeypatch.setattr(
        trigger_batch.stock_api, "get_nearest_business_day_in_a_week", down
    )
    return down


# --- 회귀의 핵심 -------------------------------------------------------------


def test_resolves_trading_day_while_krx_is_blocked(monkeypatch):
    """8/5 장애 재현 — KRX 가 터져도 거래일이 나와야 한다."""
    _patch_krx(monkeypatch)

    # 2026-08-05 는 수요일(평일).
    assert trigger_batch._resolve_trade_date("20260805") == "20260805"


def test_does_not_call_krx_on_a_normal_trading_day(monkeypatch):
    """평상시엔 KRX 를 아예 안 부른다 — 스크래핑을 줄이는 것이 Plan A 의 목적이다."""
    down = _patch_krx(monkeypatch)

    trigger_batch._resolve_trade_date("20260805")

    assert down.called == 0


def test_walks_back_to_friday_from_a_weekend(monkeypatch):
    """휴장일이면 직전 개장일로 물러난다."""
    _patch_krx(monkeypatch)

    # 2026-08-08 토요일 → 08-07 금요일
    assert trigger_batch._resolve_trade_date("20260808") == "20260807"
    # 2026-08-09 일요일 → 08-07 금요일
    assert trigger_batch._resolve_trade_date("20260809") == "20260807"


def test_walks_back_over_a_public_holiday(monkeypatch):
    """법정공휴일도 건너뛴다 (2026-08-15 광복절, 토요일이라 08-14 금요일이 답)."""
    _patch_krx(monkeypatch)

    assert trigger_batch._resolve_trade_date("20260815") == "20260814"


# --- 폴백 사슬 ---------------------------------------------------------------


def test_falls_back_to_krx_when_the_local_calendar_is_unavailable(monkeypatch):
    """달력을 못 읽으면 예전 동작(KRX)으로 물러난다 — 기능 후퇴가 아니다."""
    import builtins

    real_import = builtins.__import__

    def _no_calendar(name, *args, **kwargs):
        if name == "check_market_day":
            raise ImportError("no calendar here")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_calendar)
    monkeypatch.setattr(
        trigger_batch.stock_api,
        "get_nearest_business_day_in_a_week",
        lambda *a, **k: "20260804",
    )

    assert trigger_batch._resolve_trade_date("20260805") == "20260804"


def test_returns_the_input_date_when_every_source_fails(monkeypatch):
    """둘 다 죽어도 배치를 끝내지 않는다 — 예외를 올리면 8/5 와 같은 결과가 된다."""
    import builtins

    real_import = builtins.__import__

    def _no_calendar(name, *args, **kwargs):
        if name == "check_market_day":
            raise ImportError("no calendar here")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_calendar)
    _patch_krx(monkeypatch)

    assert trigger_batch._resolve_trade_date("20260805") == "20260805"


def test_never_raises_so_the_batch_cannot_die_here(monkeypatch):
    """이 함수는 어떤 경우에도 예외를 밖으로 내보내지 않는다."""
    import builtins

    real_import = builtins.__import__

    def _explode(name, *args, **kwargs):
        if name == "check_market_day":
            raise RuntimeError("boom")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _explode)
    _patch_krx(monkeypatch)

    assert trigger_batch._resolve_trade_date("20260805") == "20260805"


# --- 달력 자체 ---------------------------------------------------------------


def test_is_market_day_accepts_an_explicit_date():
    """``is_market_day()`` 는 인자 없이도(스케줄러) 인자와 함께도(배치) 쓰인다."""
    from check_market_day import is_market_day

    assert is_market_day(datetime.date(2026, 8, 5)) is True  # 수요일
    assert is_market_day(datetime.date(2026, 8, 8)) is False  # 토요일
    assert is_market_day(datetime.date(2026, 8, 15)) is False  # 광복절
    # 인자 없는 기존 호출 형태가 그대로 동작한다.
    assert isinstance(is_market_day(), bool)
