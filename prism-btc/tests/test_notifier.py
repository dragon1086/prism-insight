# tests/test_notifier.py — 실시간 매매 이벤트 알림 (notifier) 단위 테스트 (오프라인).
#
# 원칙: 네트워크 0. 텔레그램 전송은 monkeypatch 로 캡처한다.
#   - 콜드스타트: 마커만 세팅, 전송 0
#   - 신규 진입 1건 감지·전송
#   - 추가 트랜치(2/3) 문구
#   - 청산 이익·손실 문구
#   - 멱등: 재호출 시 중복 전송 0
#   - 토큰 없을 때 stdout 폴백·크래시 없음
from __future__ import annotations

import sqlite3

import pytest

from live import notifier, tracking
from live.tracking import PositionRow, TradeRow, ensure_schema


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _root_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    return conn


@pytest.fixture
def captured(monkeypatch):
    """notifier._dispatch 내부의 _send 호출을 캡처 (네트워크 0). 보낸 메시지 리스트."""
    sent: list[str] = []

    async def _fake_send(token, channel, message):
        sent.append(message)
        return True

    monkeypatch.setattr(notifier, "_send", _fake_send)
    return sent


def _mk_position(side="long", entry=50000.0, sl=49000.0, lev=3.0, tranche=0,
                 mode="demo") -> PositionRow:
    return PositionRow(
        side=side, entry_price=entry, qty=0.01, leverage=lev, sl_price=sl,
        tp1_price=entry * 1.02, tp2_price=entry * 1.04, tp3_price=entry * 1.06,
        liq_price=entry * 0.9, entry_time="2026-06-15T00:00:00+00:00",
        tranche_index=tranche, entry_bar_idx=1, initial_risk=10.0, mode=mode,
    )


def _mk_trade(side="long", r=2.3, reason="trail", mode="demo") -> TradeRow:
    return TradeRow(
        trade_id=1, side=side, entry_time="2026-06-15T00:00:00+00:00",
        entry_price=50000.0, exit_time="2026-06-15T04:00:00+00:00",
        exit_price=51000.0, qty=0.01, leverage=3.0, sl_price=49000.0,
        exit_reason=reason, r_multiple=r, fee_paid=0.1, funding_paid=0.0,
        tranche_index=0, liq_price=45000.0, net_pnl=23.0, mode=mode,
    )


# ---------------------------------------------------------------------------
# 콜드스타트 가드
# ---------------------------------------------------------------------------

def test_cold_start_sets_markers_no_send(captured):
    """마커 없음 + 기존 행 존재 → 전송 0, 마커만 현재 max(id) 로 세팅."""
    conn = _root_conn()
    # 과거 행이 이미 쌓여있는 상태.
    tracking.save_position(conn, _mk_position(tranche=0))
    tracking.record_trade(conn, _mk_trade())

    res = notifier.notify_new_events(conn, mode="demo")

    assert captured == []  # 과거 폭주 전송 안 함.
    assert res == {"entries": 0, "exits": 0}
    # 마커가 현재 max(id) 로 세팅됨.
    assert tracking.get_meta(conn, "last_notified_entry_id", "demo") == 1
    assert tracking.get_meta(conn, "last_notified_exit_id", "demo") == 1


def test_cold_start_empty_tables(captured):
    """행이 전혀 없을 때 콜드스타트 → 마커 0, 전송 0, 크래시 없음."""
    conn = _root_conn()
    res = notifier.notify_new_events(conn, mode="demo")
    assert captured == []
    assert res == {"entries": 0, "exits": 0}
    assert tracking.get_meta(conn, "last_notified_entry_id", "demo") == 0
    assert tracking.get_meta(conn, "last_notified_exit_id", "demo") == 0


# ---------------------------------------------------------------------------
# 신규 진입 감지·전송
# ---------------------------------------------------------------------------

def test_new_entry_detected_and_sent(captured):
    """콜드스타트 후 신규 진입 1건 → 전송 1건, '새 진입' 문구."""
    conn = _root_conn()
    notifier.notify_new_events(conn, mode="demo")  # 콜드스타트 (마커 세팅).
    assert captured == []

    tracking.save_position(conn, _mk_position(tranche=0, entry=50000.0, sl=49000.0))
    res = notifier.notify_new_events(conn, mode="demo")

    assert res["entries"] == 1
    assert len(captured) == 1
    msg = captured[0]
    assert "새 진입" in msg
    assert "📈 상승 베팅" in msg
    assert "50,000달러" in msg
    assert "49,000달러" in msg
    assert "가상자금 모의투자입니다" in msg


def test_add_tranche_message(captured):
    """추가 트랜치(tranche_index=1) → '비중 추가 (2/3)' 문구."""
    conn = _root_conn()
    notifier.notify_new_events(conn, mode="demo")  # 콜드스타트.

    tracking.save_position(conn, _mk_position(tranche=1, side="long"))
    res = notifier.notify_new_events(conn, mode="demo")

    assert res["entries"] == 1
    assert len(captured) == 1
    assert "비중 추가 (2/3)" in captured[0]
    assert "📈 상승 베팅" in captured[0]


# ---------------------------------------------------------------------------
# 청산 이익/손실 문구
# ---------------------------------------------------------------------------

def test_exit_profit_message(captured):
    """청산 이익(r>0) → '✅ 이익' + 한글화된 사유."""
    conn = _root_conn()
    notifier.notify_new_events(conn, mode="demo")  # 콜드스타트.

    tracking.record_trade(conn, _mk_trade(r=2.3, reason="trail"))
    res = notifier.notify_new_events(conn, mode="demo")

    assert res["exits"] == 1
    assert len(captured) == 1
    msg = captured[0]
    assert "포지션 정리" in msg
    assert "✅ 이익 +2.3배" in msg
    assert "추세 꺾여 익절 마감" in msg  # _reason_kr("trail")
    assert "가상자금 모의투자입니다" in msg


def test_exit_loss_message(captured):
    """청산 손실(r<0) → '❌ 손실' + 손절 사유."""
    conn = _root_conn()
    notifier.notify_new_events(conn, mode="demo")  # 콜드스타트.

    tracking.record_trade(conn, _mk_trade(r=-1.0, reason="sl"))
    res = notifier.notify_new_events(conn, mode="demo")

    assert res["exits"] == 1
    assert len(captured) == 1
    msg = captured[0]
    assert "❌ 손실 -1.0배" in msg
    assert "손절" in msg  # _reason_kr("sl")


# ---------------------------------------------------------------------------
# 멱등성
# ---------------------------------------------------------------------------

def test_idempotent_no_duplicate_send(captured):
    """같은 이벤트로 재호출 → 두 번째는 전송 0 (마커 갱신됨)."""
    conn = _root_conn()
    notifier.notify_new_events(conn, mode="demo")  # 콜드스타트.

    tracking.save_position(conn, _mk_position(tranche=0))
    tracking.record_trade(conn, _mk_trade())

    res1 = notifier.notify_new_events(conn, mode="demo")
    assert res1 == {"entries": 1, "exits": 1}
    assert len(captured) == 2

    captured.clear()
    res2 = notifier.notify_new_events(conn, mode="demo")
    assert res2 == {"entries": 0, "exits": 0}
    assert captured == []  # 중복 전송 0.


# ---------------------------------------------------------------------------
# 토큰 없을 때 stdout 폴백 — 크래시 없음 (실제 _send 사용, monkeypatch 없음)
# ---------------------------------------------------------------------------

def test_no_token_stdout_fallback_no_crash(monkeypatch, capsys):
    """토큰/채널 미설정 → _send 가 stdout 폴백. 예외 없이 완료."""
    # 환경에서 토큰/채널 제거.
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("BTC_TELEGRAM_CHANNEL_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_CHANNEL_ID", raising=False)
    # _load_env 가 .env 를 읽어 토큰을 주입하지 못하도록 무력화.
    monkeypatch.setattr(notifier, "_load_env", lambda: None)

    conn = _root_conn()
    notifier.notify_new_events(conn, mode="demo")  # 콜드스타트.
    tracking.save_position(conn, _mk_position(tranche=0))

    # 크래시 없이 완료해야 한다.
    res = notifier.notify_new_events(conn, mode="demo")
    assert res["entries"] == 1

    out = capsys.readouterr().out
    assert "새 진입" in out  # stdout 폴백으로 메시지가 출력됨.


# ---------------------------------------------------------------------------
# shadow 격리 — notifier 는 mode 인자대로만 동작 (runner 가 shadow 를 안 부름)
# ---------------------------------------------------------------------------

def test_mode_isolation_demo_only_reads_demo(captured):
    """demo 마커 세팅 후 shadow 행을 넣어도 demo 알림에 안 섞인다."""
    conn = _root_conn()
    notifier.notify_new_events(conn, mode="demo")  # demo 콜드스타트.

    # shadow 모드 포지션 — demo 알림과 무관해야 함.
    tracking.save_position(conn, _mk_position(tranche=0, mode="shadow"))
    res = notifier.notify_new_events(conn, mode="demo")

    assert res["entries"] == 0
    assert captured == []
