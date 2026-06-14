# tests/test_healthcheck.py — 운영 이상감지 watchdog (healthcheck) 단위 테스트 (오프라인).
#
# 원칙: 네트워크 0. 텔레그램 전송은 monkeypatch 로 캡처한다. 시간 기준은 now 인자
# 주입으로 결정적. 점검 항목별로 정상/이상을 각각 검증한다.
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from live import healthcheck, tracking
from live.tracking import PositionRow, ensure_schema


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)


def _root_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    return conn


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _heartbeat(conn, *, minutes_ago: float, mode="demo") -> None:
    ts = _iso(_NOW - timedelta(minutes=minutes_ago))
    tracking.log_event(conn, "heartbeat", "tick", mode=mode, ts=ts)


def _set_price_ns(conn, *, minutes_ago: float, mode="demo") -> None:
    dt = _NOW - timedelta(minutes=minutes_ago)
    ns = int(dt.timestamp() * 1e9)
    tracking.set_meta(conn, "last_processed_30m_ns", ns, mode)


def _healthy_conn() -> sqlite3.Connection:
    """모든 점검을 통과하는(이슈 0) 정상 상태 conn."""
    conn = _root_conn()
    _heartbeat(conn, minutes_ago=5)
    _set_price_ns(conn, minutes_ago=10)
    tracking.record_equity(conn, 10000.0, mode="demo")
    return conn


def _mk_position(entry_time: str, mode="demo") -> PositionRow:
    return PositionRow(
        side="long", entry_price=50000.0, qty=0.01, leverage=3.0,
        sl_price=49000.0, tp1_price=51000.0, tp2_price=52000.0,
        tp3_price=53000.0, liq_price=45000.0, entry_time=entry_time,
        tranche_index=0, entry_bar_idx=1, initial_risk=10.0, mode=mode,
    )


@pytest.fixture
def captured(monkeypatch):
    """healthcheck._send 호출을 캡처 (네트워크 0). 보낸 메시지 리스트."""
    sent: list[str] = []

    async def _fake_send(token, channel, message):
        sent.append(message)
        return True

    monkeypatch.setattr(healthcheck, "_send", _fake_send)
    monkeypatch.setattr(healthcheck, "_load_env", lambda: None)
    # 채널/토큰 주입되어 있다고 가정 (전송 경로 타게).
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("BTC_OPS_CHANNEL_ID", "12345")
    return sent


# ---------------------------------------------------------------------------
# 정상 — 이슈 0, 전송 0
# ---------------------------------------------------------------------------

def test_healthy_no_issues_no_send(captured):
    conn = _healthy_conn()
    issues = healthcheck.run_healthcheck(conn, "demo", now=_NOW)
    assert issues == []

    res = healthcheck.notify_health(conn, "demo", send=True, now=_NOW)
    assert res["issues"] == 0
    assert res["sent"] is False
    assert captured == []  # 정상이면 조용.


# ---------------------------------------------------------------------------
# 1) 데몬 정지
# ---------------------------------------------------------------------------

def test_daemon_down_old_heartbeat(captured):
    conn = _root_conn()
    _heartbeat(conn, minutes_ago=80)  # 70분 초과.
    _set_price_ns(conn, minutes_ago=10)
    tracking.record_equity(conn, 10000.0, mode="demo")

    issues = healthcheck.run_healthcheck(conn, "demo", now=_NOW)
    codes = {i["code"] for i in issues}
    assert "daemon_down" in codes
    assert any(i["level"] == "alert" and i["code"] == "daemon_down" for i in issues)

    res = healthcheck.notify_health(conn, "demo", send=True, now=_NOW)
    assert res["sent"] is True
    assert len(captured) == 1
    assert "데몬정지" in captured[0]


def test_daemon_down_no_heartbeat(captured):
    conn = _root_conn()
    _set_price_ns(conn, minutes_ago=10)
    tracking.record_equity(conn, 10000.0, mode="demo")

    issues = healthcheck.run_healthcheck(conn, "demo", now=_NOW)
    assert any(i["code"] == "daemon_down" and i["level"] == "alert" for i in issues)


# ---------------------------------------------------------------------------
# 2) 에러 폭주
# ---------------------------------------------------------------------------

def test_error_burst_detected(captured):
    conn = _healthy_conn()
    for i in range(6):  # 6건 > 5 임계.
        ts = _iso(_NOW - timedelta(minutes=10 + i))
        tracking.log_event(conn, "error", f"boom {i}", level="error",
                           mode="demo", ts=ts)

    issues = healthcheck.run_healthcheck(conn, "demo", now=_NOW)
    burst = [i for i in issues if i["code"] == "error_burst"]
    assert len(burst) == 1
    assert burst[0]["level"] == "alert"
    # 최근 1건 메시지 첨부 (가장 최근 = 마지막 기록된 boom 5).
    assert "boom 5" in burst[0]["msg"]


def test_error_burst_outside_window_ignored(captured):
    conn = _healthy_conn()
    # 6건이지만 전부 3시간 전 → 2시간 창 밖.
    for i in range(6):
        ts = _iso(_NOW - timedelta(hours=3, minutes=i))
        tracking.log_event(conn, "error", f"old {i}", level="error",
                           mode="demo", ts=ts)
    issues = healthcheck.run_healthcheck(conn, "demo", now=_NOW)
    assert not any(i["code"] == "error_burst" for i in issues)


# ---------------------------------------------------------------------------
# 3) 시세 갱신 정지
# ---------------------------------------------------------------------------

def test_price_stale_detected(captured):
    conn = _root_conn()
    _heartbeat(conn, minutes_ago=5)
    _set_price_ns(conn, minutes_ago=120)  # 90분 초과.
    tracking.record_equity(conn, 10000.0, mode="demo")

    issues = healthcheck.run_healthcheck(conn, "demo", now=_NOW)
    stale = [i for i in issues if i["code"] == "price_stale"]
    assert len(stale) == 1
    assert stale[0]["level"] == "alert"


def test_price_stale_missing_is_warn(captured):
    conn = _root_conn()
    _heartbeat(conn, minutes_ago=5)
    tracking.record_equity(conn, 10000.0, mode="demo")
    # last_processed_30m_ns 미설정.
    issues = healthcheck.run_healthcheck(conn, "demo", now=_NOW)
    stale = [i for i in issues if i["code"] == "price_stale"]
    assert len(stale) == 1
    assert stale[0]["level"] == "warn"


# ---------------------------------------------------------------------------
# 4) 자산 이상
# ---------------------------------------------------------------------------

def test_equity_missing_is_warn(captured):
    conn = _root_conn()
    _heartbeat(conn, minutes_ago=5)
    _set_price_ns(conn, minutes_ago=10)
    # equity 기록 없음.
    issues = healthcheck.run_healthcheck(conn, "demo", now=_NOW)
    eq = [i for i in issues if i["code"] == "equity_missing"]
    assert len(eq) == 1
    assert eq[0]["level"] == "warn"


def test_equity_zero_demo_is_alert(captured):
    conn = _root_conn()
    _heartbeat(conn, minutes_ago=5)
    _set_price_ns(conn, minutes_ago=10)
    tracking.record_equity(conn, 0.0, mode="demo")
    issues = healthcheck.run_healthcheck(conn, "demo", now=_NOW)
    eq = [i for i in issues if i["code"] == "equity_zero"]
    assert len(eq) == 1
    assert eq[0]["level"] == "alert"


# ---------------------------------------------------------------------------
# 5) 포지션 고착
# ---------------------------------------------------------------------------

def test_stale_position_detected(captured):
    conn = _healthy_conn()
    old_entry = _iso(_NOW - timedelta(days=25))  # 20일 초과.
    tracking.save_position(conn, _mk_position(old_entry, mode="demo"))

    issues = healthcheck.run_healthcheck(conn, "demo", now=_NOW)
    stale = [i for i in issues if i["code"] == "stale_position"]
    assert len(stale) == 1
    assert stale[0]["level"] == "warn"


def test_fresh_position_not_stale(captured):
    conn = _healthy_conn()
    recent = _iso(_NOW - timedelta(days=2))
    tracking.save_position(conn, _mk_position(recent, mode="demo"))
    issues = healthcheck.run_healthcheck(conn, "demo", now=_NOW)
    assert not any(i["code"] == "stale_position" for i in issues)


# ---------------------------------------------------------------------------
# 6) 섀도우-데모 괴리
# ---------------------------------------------------------------------------

def test_shadow_divergence_detected(captured):
    conn = _healthy_conn()  # demo equity 10000.
    tracking.record_equity(conn, 8000.0, mode="shadow")  # 차이 +25% > 15%.

    issues = healthcheck.run_healthcheck(conn, "demo", now=_NOW)
    div = [i for i in issues if i["code"] == "shadow_divergence"]
    assert len(div) == 1
    assert div[0]["level"] == "warn"


def test_shadow_divergence_within_tolerance(captured):
    conn = _healthy_conn()  # demo 10000.
    tracking.record_equity(conn, 9500.0, mode="shadow")  # 차이 ~+5.3% < 15%.
    issues = healthcheck.run_healthcheck(conn, "demo", now=_NOW)
    assert not any(i["code"] == "shadow_divergence" for i in issues)


# ---------------------------------------------------------------------------
# --daily 정상 요약 전송
# ---------------------------------------------------------------------------

def test_daily_summary_sent_when_healthy(captured):
    conn = _healthy_conn()
    res = healthcheck.notify_health(conn, "demo", send=True, daily=True, now=_NOW)
    assert res["issues"] == 0
    assert res["sent"] is True
    assert len(captured) == 1
    assert "정상 가동 중" in captured[0]


def test_daily_summary_skipped_when_issues(captured):
    """이슈 있으면 --daily 여도 정상요약 대신 경보 1건만 전송."""
    conn = _root_conn()
    _heartbeat(conn, minutes_ago=80)  # daemon_down alert.
    _set_price_ns(conn, minutes_ago=10)
    tracking.record_equity(conn, 10000.0, mode="demo")

    res = healthcheck.notify_health(conn, "demo", send=True, daily=True, now=_NOW)
    assert res["issues"] >= 1
    assert len(captured) == 1
    assert "이상감지" in captured[0]
    assert "정상 가동 중" not in captured[0]


# ---------------------------------------------------------------------------
# 이력 기록 (btc_events kind='health')
# ---------------------------------------------------------------------------

def test_health_event_recorded_alert(captured):
    conn = _root_conn()
    _heartbeat(conn, minutes_ago=80)
    _set_price_ns(conn, minutes_ago=10)
    tracking.record_equity(conn, 10000.0, mode="demo")

    healthcheck.notify_health(conn, "demo", send=True, now=_NOW)
    r = conn.execute(
        "SELECT level, kind FROM btc_events WHERE kind='health' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert r is not None
    assert r["kind"] == "health"
    assert r["level"] == "error"  # alert → error.


def test_health_event_recorded_ok(captured):
    conn = _healthy_conn()
    healthcheck.notify_health(conn, "demo", send=True, now=_NOW)
    r = conn.execute(
        "SELECT level, kind, message FROM btc_events WHERE kind='health' "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert r is not None
    assert r["level"] == "info"
    assert "정상" in r["message"]


# ---------------------------------------------------------------------------
# 토큰 없음 → stdout 폴백 (실제 _send 사용, monkeypatch 없음)
# ---------------------------------------------------------------------------

def test_no_token_stdout_fallback_no_crash(monkeypatch, capsys):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("BTC_OPS_CHANNEL_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_CHANNEL_ID", raising=False)
    monkeypatch.setattr(healthcheck, "_load_env", lambda: None)

    conn = _root_conn()
    _heartbeat(conn, minutes_ago=80)  # 이슈 발생 → 전송 시도.
    _set_price_ns(conn, minutes_ago=10)
    tracking.record_equity(conn, 10000.0, mode="demo")

    res = healthcheck.notify_health(conn, "demo", send=True, now=_NOW)
    assert res["sent"] is True  # _send 가 stdout 폴백 후 True.

    out = capsys.readouterr().out
    assert "이상감지" in out  # stdout 폴백으로 경보 메시지 출력.


# ---------------------------------------------------------------------------
# no-send 모드 — 전송 0, 이력은 기록
# ---------------------------------------------------------------------------

def test_no_send_does_not_dispatch(captured):
    conn = _root_conn()
    _heartbeat(conn, minutes_ago=80)
    _set_price_ns(conn, minutes_ago=10)
    tracking.record_equity(conn, 10000.0, mode="demo")

    res = healthcheck.notify_health(conn, "demo", send=False, now=_NOW)
    assert res["sent"] is False
    assert captured == []  # send=False → 전송 0.
    # 이력은 기록됨.
    r = conn.execute(
        "SELECT COUNT(*) AS c FROM btc_events WHERE kind='health'"
    ).fetchone()
    assert r["c"] == 1
