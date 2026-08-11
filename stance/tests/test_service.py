"""서비스 계층 — HTTP 없이 검증한다.

로직이 프레임워크와 분리되어 있으므로 FastAPI 없이도 전부 확인할 수 있다.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from stance.server import Ledger, Quote
from stance.server.service import RateLimit, StanceError, StanceService

UTC = timezone.utc


def make(quote_price=10000, tradable=True, provider=None):
    def default_provider(market, symbol):
        return Quote(symbol, D(quote_price), tradable=tradable)

    svc = StanceService(ledger=Ledger(), quote_provider=provider or default_provider)
    reg = svc.register("s1", "테스트 전략", "@me", market="KRX")
    return svc, reg


# ── 등록과 인증 ───────────────────────────────────────────────────────────

def test_register_returns_key_once_and_stores_only_hash():
    svc, reg = make()
    assert reg.api_key.startswith("stk_")

    row = svc.ledger.conn.execute(
        "SELECT api_key_hash FROM strategies WHERE strategy_id='s1'"
    ).fetchone()
    assert row["api_key_hash"] != reg.api_key      # 평문을 저장하지 않는다
    assert len(row["api_key_hash"]) == 64


def test_authenticate_maps_key_to_its_own_strategy():
    svc, reg = make()
    assert svc.authenticate(reg.api_key) == "s1"


def test_rotate_key_revokes_old_key_immediately():
    svc, reg = make()
    rotated = svc.rotate_key("s1")

    assert rotated.startswith("stk_")
    assert rotated != reg.api_key
    with pytest.raises(StanceError) as exc:
        svc.authenticate(reg.api_key)
    assert exc.value.status == 401
    assert svc.authenticate(rotated) == "s1"


@pytest.mark.parametrize("key", [None, "", "stk_wrong"])
def test_bad_key_is_401(key):
    svc, _ = make()
    with pytest.raises(StanceError) as e:
        svc.authenticate(key)
    assert e.value.status == 401


def test_duplicate_strategy_id_is_409():
    svc, _ = make()
    with pytest.raises(StanceError) as e:
        svc.register("s1", "중복", "@me")
    assert e.value.status == 409


def test_unknown_market_is_rejected():
    svc = StanceService(ledger=Ledger())
    with pytest.raises(ValueError, match="지원 시장"):
        svc.register("x", "x", "@me", market="FOREX")


def test_unknown_cadence_is_rejected():
    svc = StanceService(ledger=Ledger())
    with pytest.raises(StanceError):
        svc.register("x", "x", "@me", cadence="hourly")


# ── 선언 접수 ─────────────────────────────────────────────────────────────

def test_submit_returns_verdict_synchronously():
    """축소·거부를 몇 초 뒤에 알려주면 참여자는 이미 주문을 낸 뒤다."""
    svc, _ = make()
    out = svc.submit("s1", 1, symbol="005930", target_weight="0.3")

    assert out["admit"] == "accepted"
    assert out["fill_price"] == 10000
    assert out["received_at"]                       # 접수시각이 응답에 실린다
    assert out["total_assets_after"] == pytest.approx(1.0)


def test_clamp_is_reported_with_effective_weight():
    svc, _ = make()
    svc.submit("s1", 1, symbol="AAA", target_weight="0.7")
    out = svc.submit("s1", 2, symbol="BBB", target_weight="0.7")

    assert out["admit"] == "clamped"
    assert out["requested_weight"] == pytest.approx(0.7)
    assert out["effective_weight"] < 0.7            # 남은 현금만큼만


def test_quote_failure_is_pending_not_rejected():
    """시세 소스 장애는 서버 책임이다. 참여자의 판단 기록을 지우지 않는다."""
    def dead(market, symbol):
        raise RuntimeError("시세 서버 다운")

    svc, _ = make(provider=dead)
    out = svc.submit("s1", 1, symbol="005930", target_weight="0.3")

    assert out["admit"] == "pending"
    assert out["pending"] is True
    # 선언 자체는 원장에 남는다
    assert svc.ledger.next_seq("s1") == 2


def test_untradable_is_rejected():
    svc, _ = make(tradable=False)
    out = svc.submit("s1", 1, symbol="005930", target_weight="0.3")
    assert out["admit"] == "rejected"


def test_stale_seq_is_409():
    svc, _ = make()
    svc.submit("s1", 1, symbol="AAA", target_weight="0.2")
    with pytest.raises(StanceError) as e:
        svc.submit("s1", 1, symbol="AAA", target_weight="0.9")
    assert e.value.status == 409


def test_identical_retry_is_idempotent_and_does_not_append():
    svc, _ = make()
    first = svc.submit("s1", 1, symbol="AAA", target_weight="0.2", reason="signal")
    retried = svc.submit("s1", 1, symbol="AAA", target_weight="0.2", reason="signal")

    assert retried == {**first, "replayed": True}
    assert svc.ledger.next_seq("s1") == 2


def test_same_seq_with_different_body_is_still_409():
    svc, _ = make()
    svc.submit("s1", 1, symbol="AAA", target_weight="0.2")
    with pytest.raises(StanceError) as exc:
        svc.submit("s1", 1, symbol="AAA", target_weight="0.3")
    assert exc.value.status == 409


def test_concurrent_identical_retry_appends_once():
    svc, _ = make()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(
            lambda _: svc.submit("s1", 1, symbol="AAA", target_weight="0.2"),
            range(2),
        ))

    assert sorted(result["replayed"] for result in results) == [False, True]
    assert svc.ledger.next_seq("s1") == 2


def test_seq_gap_is_allowed():
    """누락은 감지 대상이지 차단 대상이 아니다. 앞선 번호는 그대로 받는다."""
    svc, _ = make()
    svc.submit("s1", 1, symbol="AAA", target_weight="0.2")
    out = svc.submit("s1", 9, symbol="AAA", target_weight="0.3")
    assert out["admit"] == "accepted"


def test_seq_must_be_positive():
    svc, _ = make()
    with pytest.raises(StanceError):
        svc.submit("s1", 0, kind="hold")


@pytest.mark.parametrize("bad", ["1.5", "-0.1", "abc", 2])
def test_invalid_weight_is_400(bad):
    svc, _ = make()
    with pytest.raises(StanceError):
        svc.submit("s1", 1, symbol="AAA", target_weight=bad)


def test_set_requires_symbol_and_weight():
    svc, _ = make()
    with pytest.raises(StanceError):
        svc.submit("s1", 1, target_weight="0.2")
    with pytest.raises(StanceError):
        svc.submit("s1", 1, symbol="AAA")


def test_reason_length_is_capped():
    svc, _ = make()
    with pytest.raises(StanceError):
        svc.submit("s1", 1, symbol="AAA", target_weight="0.1", reason="가" * 501)


def test_hold_and_pause_need_no_symbol():
    svc, _ = make()
    assert svc.submit("s1", 1, kind="hold")["admit"] == "accepted"
    assert svc.submit("s1", 2, kind="pause")["admit"] == "accepted"
    assert svc.submit("s1", 3, kind="resume")["admit"] == "accepted"


def test_symbol_is_normalized_before_storage():
    svc, _ = make()
    svc.submit("s1", 1, symbol="A005930", target_weight="0.2")
    out = svc.portfolio("s1")
    assert [p["symbol"] for p in out["positions"]] == ["005930"]


# ── 요청 한도 ─────────────────────────────────────────────────────────────

def test_rate_limit_blocks_flooding():
    """지울 수 없는 테이블에 무제한 쓰기를 열어두면 안 된다."""
    svc, _ = make()
    svc.rate_limit = RateLimit(per_minute=3, per_day=100)

    for i in range(1, 4):
        svc.submit("s1", i, kind="hold")
    with pytest.raises(StanceError) as e:
        svc.submit("s1", 4, kind="hold")
    assert e.value.status == 429


def test_rate_limit_allows_minute_bursts_within_daily_cap():
    """분 단위로 도는 시스템을 막지 않도록 분당 한도는 넉넉해야 한다."""
    rl = RateLimit(per_minute=120, per_day=5000)
    now = 1_000_000.0
    for i in range(120):
        rl.check("s1", now=now + i * 0.1)
    with pytest.raises(StanceError):
        rl.check("s1", now=now + 12.1)
    # 1분이 지나면 다시 열린다
    rl.check("s1", now=now + 61)


# ── 조회 ──────────────────────────────────────────────────────────────────

def test_portfolio_shape():
    svc, _ = make()
    svc.submit("s1", 1, symbol="005930", target_weight="0.25")
    out = svc.portfolio("s1")

    assert out["last_seq"] == 1
    assert out["total_assets"] == pytest.approx(1.0)
    assert out["cash"] == pytest.approx(0.75)
    assert out["invested_ratio"] == pytest.approx(0.25)
    assert out["positions"][0]["symbol"] == "005930"


def test_engine_is_rebuilt_from_ledger_after_restart():
    """프로세스가 죽어도 원장만 있으면 장부가 복원된다."""
    svc, reg = make()
    svc.submit("s1", 1, symbol="005930", target_weight="0.4")
    before = svc.portfolio("s1")

    restarted = StanceService(ledger=svc.ledger,
                              quote_provider=lambda m, s: Quote(s, D(10000)))
    after = restarted.portfolio("s1")

    assert after["total_assets"] == pytest.approx(before["total_assets"])
    assert after["cash"] == pytest.approx(before["cash"])
    assert after["last_seq"] == before["last_seq"]


def test_metrics_uses_market_profile():
    svc, _ = make()
    svc.submit("s1", 1, symbol="005930", target_weight="0.3")
    m = svc.metrics("s1")
    assert m.market == "KRX"
    assert not m.experimental


def test_unknown_strategy_is_404():
    svc, _ = make()
    with pytest.raises(StanceError) as e:
        svc.portfolio("nope")
    assert e.value.status == 404
