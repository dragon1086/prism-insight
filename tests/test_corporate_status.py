"""cores.corporate_status (이벤트 강제청산 TIER0 — KIS 상태코드 자동탐지) 단위테스트.

순수 모듈이라 root pytest 세션에서 실행 가능(KR/US cores shadowing 무관).
실행: .venv/bin/python -m pytest tests/test_corporate_status.py -q
"""
import asyncio
import sys
import types

from cores import corporate_status as cs


def test_kis_status_codes():
    assert cs.classify_kis_status("51")[0] is True   # 관리종목(부실) → 청산
    # 시장경고 단계(급등 종목)는 청산 X — 승자 수익반납 방지
    assert cs.classify_kis_status("52")[0] is False  # 투자위험
    assert cs.classify_kis_status("53")[0] is False  # 투자경고
    assert cs.classify_kis_status("54")[0] is False  # 투자주의
    assert cs.classify_kis_status("58")[0] is False  # 거래정지(모호 → 자동청산 제외)
    assert cs.classify_kis_status("57")[0] is False  # 증거금100%(정상)
    assert cs.classify_kis_status("55")[0] is False  # 신용가능(정상)
    assert cs.classify_kis_status("00")[0] is False  # 정상
    assert cs.classify_kis_status("")[0] is False
    assert cs.classify_kis_status(None)[0] is False


def test_check_event_exit_kis_code():
    ok, reason = cs.check_event_exit("000660", kis_status_code="51", market="KR")
    assert ok is True and "KIS_STATUS" in reason
    # 급등 시장경고(52) 종목은 자동청산되지 않음(수익반납 방지)
    assert cs.check_event_exit("000660", kis_status_code="52", market="KR")[0] is False
    # 코드 미주입이면 미평가(뉴스 프롬프트가 담당)
    assert cs.check_event_exit("000660", kis_status_code=None, market="KR")[0] is False


def test_empty_ticker():
    assert cs.check_event_exit("", kis_status_code="51", market="KR")[0] is False


# ── fetch_status_codes (KIS 상태코드 일괄 prefetch) ─────────────────
class _FakeTrader:
    def __init__(self, mapping):
        self._m = mapping

    def get_current_price(self, ticker):
        if ticker in self._m:
            return {"current_price": 1, "iscd_stat_cls_code": self._m[ticker]}
        return None


class _FakeCtx:
    def __init__(self, mapping):
        self._t = _FakeTrader(mapping)

    async def __aenter__(self):
        return self._t

    async def __aexit__(self, *a):
        return False


class _BoomCtx:
    async def __aenter__(self):
        raise RuntimeError("no credentials")

    async def __aexit__(self, *a):
        return False


def _install_fake_trading(monkeypatch, mapping=None, boom=False):
    parent = types.ModuleType("trading")
    sub = types.ModuleType("trading.domestic_stock_trading")
    sub.AsyncTradingContext = (lambda *a, **k: _BoomCtx()) if boom else (lambda *a, **k: _FakeCtx(mapping or {}))
    parent.domestic_stock_trading = sub
    monkeypatch.setitem(sys.modules, "trading", parent)
    monkeypatch.setitem(sys.modules, "trading.domestic_stock_trading", sub)


def test_fetch_status_codes_ok(monkeypatch):
    _install_fake_trading(monkeypatch, {"012510": "57", "000660": "51"})
    out = asyncio.run(cs.fetch_status_codes(["012510", "000660", "  ", ""]))
    assert out == {"012510": "57", "000660": "51"}


def test_fetch_status_codes_context_failure_safe(monkeypatch):
    _install_fake_trading(monkeypatch, boom=True)
    assert asyncio.run(cs.fetch_status_codes(["012510"])) == {}


def test_fetch_status_codes_empty_input():
    assert asyncio.run(cs.fetch_status_codes([])) == {}


def test_fetch_then_classify_integration(monkeypatch):
    _install_fake_trading(monkeypatch, {"000660": "51"})  # 관리종목
    out = asyncio.run(cs.fetch_status_codes(["000660"]))
    ok, reason = cs.check_event_exit("000660", kis_status_code=out.get("000660"), market="KR")
    assert ok is True and "KIS_STATUS" in reason
