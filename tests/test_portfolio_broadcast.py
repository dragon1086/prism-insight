"""Tests for portfolio-summary broadcast de-duplication (market-keyed debounce)."""
import importlib
import time

import pytest


@pytest.fixture()
def pb(tmp_path, monkeypatch):
    db = tmp_path / "stock_tracking_db.sqlite"
    monkeypatch.setenv("PORTFOLIO_BROADCAST_DB", str(db))
    import portfolio_broadcast as m
    importlib.reload(m)  # re-read env into module-level DEBOUNCE_SEC
    return m


def test_first_send_allowed_then_debounced(pb):
    assert pb.should_send_portfolio("US", debounce_sec=120) is True
    # immediate second call within the window is suppressed
    assert pb.should_send_portfolio("US", debounce_sec=120) is False


def test_markets_are_independent(pb):
    assert pb.should_send_portfolio("US", debounce_sec=120) is True
    assert pb.should_send_portfolio("KR", debounce_sec=120) is True  # different market
    assert pb.should_send_portfolio("US", debounce_sec=120) is False
    assert pb.should_send_portfolio("KR", debounce_sec=120) is False


def test_window_expiry_allows_resend(pb):
    assert pb.should_send_portfolio("US", debounce_sec=1) is True
    assert pb.should_send_portfolio("US", debounce_sec=1) is False
    time.sleep(1.1)
    assert pb.should_send_portfolio("US", debounce_sec=1) is True


def test_market_key_case_insensitive(pb):
    assert pb.should_send_portfolio("us", debounce_sec=120) is True
    assert pb.should_send_portfolio("US", debounce_sec=120) is False


def test_force_bypasses_debounce(pb):
    # 배치 run-end(force=True)는 디바운스를 우회해 항상 발송(완전한 최종 요약 보존).
    assert pb.should_send_portfolio("KR", debounce_sec=120) is True   # 루프 등 최초 발송
    assert pb.should_send_portfolio("KR", debounce_sec=120) is False  # 윈도우 내 억제
    assert pb.should_send_portfolio("KR", debounce_sec=120, force=True) is True  # 강제 통과
    # force도 발송시각을 기록 -> 이후 비강제 호출은 다시 디바운스
    assert pb.should_send_portfolio("KR", debounce_sec=120) is False


def test_fail_open_on_bad_db(monkeypatch):
    import portfolio_broadcast as m
    importlib.reload(m)
    # point at an unwritable/invalid path -> must fail OPEN (return True), never drop
    bad = "/proc/nonexistent_dir/cannot_create.sqlite"
    assert m.should_send_portfolio("US", debounce_sec=120, db_path=bad) is True
