#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KRX 현재가 조회 재시도 로직 테스트 (신규 매수후보 누락 방지).

KRX API 일시 타임아웃 시 N회 재시도 후 성공하면 가격을 반환하고,
모두 실패하면 기존대로 DB last-price fallback으로 떨어지는지 검증.
asyncio.sleep을 몽키패치해 실제 대기 없이 빠르게 실행.
"""
import os
import sys
import asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

passed = 0
failed = 0


def check(label, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS: {label}")
    else:
        failed += 1
        print(f"  FAIL: {label}")


import tracking.helpers as H


class FakeDF:
    """Minimal df with .index and .loc[ticker, 'Close'] behavior."""
    def __init__(self, prices):  # prices: {ticker: close}
        self._p = prices

    @property
    def index(self):
        return list(self._p.keys())

    @property
    def loc(self):
        p = self._p
        class _L:
            def __getitem__(self, key):
                tk, col = key
                return p[tk]
        return _L()


class FakeCursor:
    """Returns a stored last price for _get_last_price_from_db."""
    def __init__(self, last=None):
        self._last = last
    def execute(self, *a, **k):
        pass
    def fetchone(self):
        return (self._last,) if self._last is not None else None


async def _run():
    # No real sleeping
    _orig_sleep = asyncio.sleep
    async def _fast_sleep(_):
        return None
    asyncio.sleep = _fast_sleep

    # Patch the krx_data_client symbols at import site (function imports them locally)
    import krx_data_client as K
    _o_nbd = K.get_nearest_business_day_in_a_week
    _o_ohlcv = K.get_market_ohlcv_by_ticker
    try:
        K.get_nearest_business_day_in_a_week = lambda *a, **k: "20260529"

        print("[Test 1] 처음 2회 타임아웃 후 3회차 성공 → 가격 반환 (재시도 작동)")
        calls = {"n": 0}
        def flaky(_date):
            calls["n"] += 1
            if calls["n"] < 3:
                raise TimeoutError("data.krx.co.kr Read timed out")
            return FakeDF({"353200": 189300.0})
        K.get_market_ohlcv_by_ticker = flaky
        price = await H.get_current_stock_price(FakeCursor(last=None), "353200")
        check("3회차에 정확한 가격 반환", price == 189300.0)
        check("정확히 3회 호출됨(2회 재시도)", calls["n"] == 3)

        print("\n[Test 2] 신규후보(DB에 없음) 전부 타임아웃 → fallback 0 (스킵), 단 재시도는 다 함")
        calls2 = {"n": 0}
        def always_fail(_date):
            calls2["n"] += 1
            raise TimeoutError("timeout")
        K.get_market_ohlcv_by_ticker = always_fail
        price2 = await H.get_current_stock_price(FakeCursor(last=None), "353200")
        check("DB에 가격 없으면 0.0 반환(기존 동작 보존)", price2 == 0.0)
        check("MAX_RETRIES(3)회 모두 시도", calls2["n"] == 3)

        print("\n[Test 3] 보유종목(DB last price 있음) 전부 타임아웃 → last price fallback")
        def always_fail2(_date):
            raise TimeoutError("timeout")
        K.get_market_ohlcv_by_ticker = always_fail2
        price3 = await H.get_current_stock_price(FakeCursor(last=1952000.0), "009150")
        check("타임아웃 시 last price로 fallback", price3 == 1952000.0)

        print("\n[Test 4] 1회차 즉시 성공 → 재시도 안 함(정상시 비용 0)")
        calls4 = {"n": 0}
        def ok(_date):
            calls4["n"] += 1
            return FakeDF({"005935": 206500.0})
        K.get_market_ohlcv_by_ticker = ok
        price4 = await H.get_current_stock_price(FakeCursor(), "005935")
        check("1회차 성공 시 가격 반환", price4 == 206500.0)
        check("재시도 없이 1회만 호출", calls4["n"] == 1)

        print("\n[Test 5] 데이터는 받았으나 종목 없음 → 재시도 무의미, 즉시 fallback")
        calls5 = {"n": 0}
        def no_ticker(_date):
            calls5["n"] += 1
            return FakeDF({"000000": 100.0})  # 353200 없음
        K.get_market_ohlcv_by_ticker = no_ticker
        price5 = await H.get_current_stock_price(FakeCursor(last=None), "353200")
        check("종목 부재 시 1회만 호출(재시도 안 함)", calls5["n"] == 1)
        check("종목 부재 시 fallback 0.0", price5 == 0.0)

    finally:
        asyncio.sleep = _orig_sleep
        K.get_nearest_business_day_in_a_week = _o_nbd
        K.get_market_ohlcv_by_ticker = _o_ohlcv


def test_price_query_retry_contract():
    """Expose the legacy script checks to pytest without exiting collection."""
    global passed, failed
    passed = failed = 0
    asyncio.run(_run())
    assert failed == 0, f"{failed} of {passed + failed} retry checks failed"


if __name__ == "__main__":
    asyncio.run(_run())
    print(f"\n===== RESULT: {passed} passed, {failed} failed =====")
    sys.exit(1 if failed else 0)
