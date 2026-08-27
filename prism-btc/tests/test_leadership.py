# tests/test_leadership.py — 라운드8 L 계층 (core/leadership.py)
#
# 핵심 계약: fail-open — 데이터 없음/부족/정체/예외 어떤 경우에도 (1.0, 1.0).
# 정상 경로: BTC 60일 수익률 > ETH → leading 배수, 반대는 lagging 배수.
from __future__ import annotations

import sqlite3
import time

import pytest

import core.leadership as leadership
from core.leadership import leadership_multipliers
from engine.config import (
    L_MULT_LONG_LAGGING,
    L_MULT_LONG_LEADING,
    L_MULT_SHORT_LAGGING,
    L_MULT_SHORT_LEADING,
)

_DAY_MS = 86_400_000


@pytest.fixture(autouse=True)
def _enable_layer_under_test(monkeypatch):
    """Exercise the feature behavior without changing its safe OFF default."""
    monkeypatch.setattr(leadership, "L_LAYER_ENABLED", True)


def test_layer_disabled_is_neutral(monkeypatch):
    monkeypatch.setattr(leadership, "L_LAYER_ENABLED", False)
    assert leadership_multipliers() == (1.0, 1.0, "disabled")


def _make_db(tmp_path, btc_daily_ret: float, eth_daily_ret: float,
             n_bars: int = 70, end_ms: int | None = None):
    """단조 수익률 합성 시계열로 spot db 생성."""
    path = tmp_path / "btc_spot.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE spot_klines (symbol TEXT, timeframe TEXT, open_time INTEGER,"
        " open REAL, high REAL, low REAL, close REAL, volume REAL,"
        " quote_volume REAL, confirmed INTEGER, fetched_at INTEGER,"
        " PRIMARY KEY (symbol, timeframe, open_time))")
    end_ms = end_ms or int(time.time() * 1000)
    start = end_ms - n_bars * _DAY_MS
    for sym, ret in (("BTCUSDT", btc_daily_ret), ("ETHUSDT", eth_daily_ret)):
        px = 100.0
        for i in range(n_bars):
            ot = start + i * _DAY_MS
            px *= (1 + ret)
            conn.execute("INSERT INTO spot_klines VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                         (sym, "1d", ot, px, px, px, px, 1.0, 1.0, 1, end_ms))
    conn.commit()
    conn.close()
    return path


def test_fail_open_when_db_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("PRISM_BTC_SPOT_DB", str(tmp_path / "nope.db"))
    assert leadership_multipliers() == (1.0, 1.0, "fail-open: spot db missing")


def test_fail_open_when_insufficient_bars(tmp_path, monkeypatch):
    path = _make_db(tmp_path, 0.01, 0.0, n_bars=10)
    monkeypatch.setenv("PRISM_BTC_SPOT_DB", str(path))
    long_m, short_m, reason = leadership_multipliers()
    assert (long_m, short_m) == (1.0, 1.0)
    assert "insufficient" in reason


def test_fail_open_when_stale(tmp_path, monkeypatch):
    now = int(time.time() * 1000)
    path = _make_db(tmp_path, 0.01, 0.0, end_ms=now - 10 * _DAY_MS)
    monkeypatch.setenv("PRISM_BTC_SPOT_DB", str(path))
    long_m, short_m, reason = leadership_multipliers(now_ms=now)
    assert (long_m, short_m) == (1.0, 1.0)
    assert "stale" in reason


def test_leading_when_btc_outperforms(tmp_path, monkeypatch):
    path = _make_db(tmp_path, 0.01, 0.0)
    monkeypatch.setenv("PRISM_BTC_SPOT_DB", str(path))
    long_m, short_m, reason = leadership_multipliers()
    assert long_m == pytest.approx(L_MULT_LONG_LEADING)
    assert short_m == pytest.approx(L_MULT_SHORT_LEADING)
    assert "btc_leading" in reason


def test_lagging_when_eth_outperforms(tmp_path, monkeypatch):
    path = _make_db(tmp_path, 0.0, 0.01)
    monkeypatch.setenv("PRISM_BTC_SPOT_DB", str(path))
    long_m, short_m, reason = leadership_multipliers()
    assert long_m == pytest.approx(L_MULT_LONG_LAGGING)
    assert short_m == pytest.approx(L_MULT_SHORT_LAGGING)
    assert "btc_lagging" in reason


def test_swing_sizing_risk_mult():
    from core.swing import compute_swing_sizing
    base = compute_swing_sizing(10_000.0, 1_000.0, 990.0)
    boosted = compute_swing_sizing(10_000.0, 1_000.0, 990.0, risk_mult=1.25)
    assert boosted.qty == pytest.approx(base.qty * 1.25)
    assert boosted.risk_amount == pytest.approx(base.risk_amount * 1.25)


def test_rs_window_uses_confirmed_bars_only(tmp_path, monkeypatch):
    # 미완결(confirmed=0) 최신 봉은 무시되어야 한다 (PIT)
    now = int(time.time() * 1000)
    path = _make_db(tmp_path, 0.01, 0.0, end_ms=now)
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO spot_klines VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                 ("BTCUSDT", "1d", now + _DAY_MS, 1, 1, 1, 0.0001, 1, 1, 0, now))
    conn.commit()
    conn.close()
    monkeypatch.setenv("PRISM_BTC_SPOT_DB", str(path))
    long_m, _, reason = leadership_multipliers(now_ms=now + 2 * _DAY_MS)
    # 폭락한 미완결 봉이 무시되므로 여전히 leading
    assert long_m == pytest.approx(L_MULT_LONG_LEADING), reason
