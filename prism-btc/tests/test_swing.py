# tests/test_swing.py — 라운드6 Lane B 스윙 레인 (core/swing + live/swing)
#
# 오프라인 전용 (네트워크/실DB 불필요): 순수함수 단위 테스트 + 인메모리
# sqlite/합성 tf_data 로 진입→손절 전체 사이클 검증.
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.swing import (  # noqa: E402
    SwingSizing,
    compute_swing_sizing,
    conflicts_with_main,
    detect_cross,
    entry_side,
    rule_exit_due,
    stop_price,
)
from engine.config import (  # noqa: E402
    SWING_INITIAL_EQUITY,
    SWING_MAX_LEVERAGE,
    SWING_RISK_PER_TRADE,
    SWING_STOP_ATR_MULT,
)


# ---------------------------------------------------------------------------
# core/swing — 순수함수 (백테스트 entryB/exitB 정의 고정)
# ---------------------------------------------------------------------------

class TestDetectCross:
    def test_golden_cross(self):
        assert detect_cross(99.0, 100.0, 101.0, 100.0) == 1

    def test_dead_cross(self):
        assert detect_cross(101.0, 100.0, 99.0, 100.0) == -1

    def test_no_cross_above(self):
        assert detect_cross(101.0, 100.0, 102.0, 100.0) == 0

    def test_no_cross_below(self):
        assert detect_cross(99.0, 100.0, 98.0, 100.0) == 0

    def test_touch_then_cross_up(self):
        # 이전 봉 ma10 == ma35 (경계) 후 상향 — 백테스트 정의상 크로스다.
        assert detect_cross(100.0, 100.0, 101.0, 100.0) == 1


class TestEntrySide:
    def test_long(self):
        assert entry_side(1, 105.0, 100.0, 110.0, 100.0) == "long"

    def test_long_blocked_by_1d(self):
        # 1d 하락 정렬이면 골든크로스여도 롱 금지.
        assert entry_side(1, 95.0, 100.0, 110.0, 100.0) is None

    def test_long_blocked_below_ma35(self):
        assert entry_side(1, 105.0, 100.0, 99.0, 100.0) is None

    def test_short(self):
        assert entry_side(-1, 95.0, 100.0, 90.0, 100.0) == "short"

    def test_short_allows_1d_equal(self):
        # 백테스트 정의: 숏의 1d 조건은 not(ma10>ma35) → 같아도 허용.
        assert entry_side(-1, 100.0, 100.0, 90.0, 100.0) == "short"

    def test_no_cross_no_entry(self):
        assert entry_side(0, 105.0, 100.0, 110.0, 100.0) is None


class TestStopAndExit:
    def test_stop_long(self):
        assert stop_price("long", 100.0, 2.0) == 100.0 - SWING_STOP_ATR_MULT * 2.0

    def test_stop_short(self):
        assert stop_price("short", 100.0, 2.0) == 100.0 + SWING_STOP_ATR_MULT * 2.0

    def test_exit_long_on_break(self):
        assert rule_exit_due("long", 99.0, 100.0) is True
        assert rule_exit_due("long", 101.0, 100.0) is False

    def test_exit_short_on_break(self):
        assert rule_exit_due("short", 101.0, 100.0) is True
        assert rule_exit_due("short", 99.0, 100.0) is False


class TestConflict:
    def test_opposite_blocks(self):
        assert conflicts_with_main("long", ["short"]) is True

    def test_same_side_allowed(self):
        assert conflicts_with_main("long", ["long"]) is False

    def test_no_main_positions(self):
        assert conflicts_with_main("short", []) is False


class TestSizing:
    def test_risk_based_qty(self):
        sz = compute_swing_sizing(10_000.0, 50_000.0, 49_000.0)
        assert not sz.rejected
        # risk = 10000 * 1% = 100; sl_dist = 1000 → qty = 0.1
        assert sz.qty == pytest.approx(10_000.0 * SWING_RISK_PER_TRADE / 1_000.0)
        assert sz.leverage == pytest.approx(sz.qty * 50_000.0 / 10_000.0)
        assert sz.leverage < SWING_MAX_LEVERAGE

    def test_leverage_cap(self):
        # 스탑이 극단적으로 좁으면 명목이 커진다 → 5x 캡, 리스크 축소.
        sz = compute_swing_sizing(10_000.0, 100.0, 99.9)
        assert not sz.rejected
        assert sz.leverage == pytest.approx(SWING_MAX_LEVERAGE)
        assert sz.qty == pytest.approx(10_000.0 * SWING_MAX_LEVERAGE / 100.0)
        assert sz.risk_amount < 10_000.0 * SWING_RISK_PER_TRADE

    def test_rejects_zero_stop_distance(self):
        assert compute_swing_sizing(10_000.0, 100.0, 100.0).rejected

    def test_rejects_bad_equity(self):
        assert compute_swing_sizing(0.0, 100.0, 99.0).rejected

    def test_frozen_dataclass(self):
        sz = SwingSizing(1.0, 1.0, 1.0, False)
        with pytest.raises(Exception):
            sz.qty = 2.0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# live/swing.process — 합성 시나리오 E2E (인메모리 sqlite, 가상 체결)
# ---------------------------------------------------------------------------

def _dt(s: str) -> pd.Timestamp:
    return pd.Timestamp(s, tz="UTC")


def _make_tf_data(with_stop_bar: bool = False) -> dict:
    """골든크로스 직후 상태의 합성 tf_data.

    4h: 40봉, 마지막 봉(12:00 open, 16:00 close)에서 ma10 이 ma35 상향 돌파.
    1d: 완결 봉 ma10>ma35 (상승 정렬).
    30m: 16:00 봉(진입 트리거) + 선행 더미, with_stop_bar 면 16:30 봉(low 가
         스탑 관통) 추가.
    """
    idx4 = pd.date_range(_dt("2026-01-01 00:00"), periods=40, freq="4h")
    d4 = pd.DataFrame({
        "open": 49_500.0, "high": 50_200.0, "low": 49_300.0,
        "close": 50_000.0, "volume": 1.0, "turnover": 1.0,
        "ma10": 48_900.0, "ma35": 49_000.0, "atr14": 500.0,
    }, index=idx4)
    # 마지막 봉에서 골든크로스 (prev: 48,900 <= 49,000 / cur: 49,100 > 49,000).
    d4.iloc[-1, d4.columns.get_loc("ma10")] = 49_100.0

    idx1 = pd.date_range(_dt("2025-12-20 00:00"), periods=18, freq="1D")
    d1 = pd.DataFrame({
        "open": 48_000.0, "high": 49_000.0, "low": 47_000.0,
        "close": 48_500.0, "volume": 1.0, "turnover": 1.0,
        "ma10": 48_000.0, "ma35": 47_000.0, "atr14": 800.0,
    }, index=idx1)

    rows_30m = [
        (_dt("2026-01-07 15:30"), 49_950.0, 50_050.0, 49_900.0, 49_990.0),
        (_dt("2026-01-07 16:00"), 49_990.0, 50_100.0, 49_900.0, 50_000.0),
    ]
    if with_stop_bar:
        rows_30m.append(
            (_dt("2026-01-07 16:30"), 50_000.0, 50_050.0, 48_900.0, 48_950.0))
    d30 = pd.DataFrame(
        [{"open": o, "high": h, "low": lo, "close": c,
          "volume": 1.0, "turnover": 1.0}
         for _, o, h, lo, c in rows_30m],
        index=pd.DatetimeIndex([t for t, *_ in rows_30m]))
    return {"30m": d30, "4h": d4, "1d": d1}


@pytest.fixture()
def conn():
    from live import tracking
    c = tracking.get_connection(":memory:")
    tracking.ensure_schema(c)
    yield c
    c.close()


class TestSwingProcess:
    def test_cold_start_entry_on_golden_cross(self, conn):
        from live import swing, tracking
        tf_data = _make_tf_data()
        res = swing.process(conn, tf_data, main_mode="shadow")
        assert res["events"] == 1
        pos = tracking.load_open_positions(conn, "swing")
        assert len(pos) == 1
        p = pos[0]
        assert p.side == "long"
        assert p.entry_price == pytest.approx(50_000.0)
        # stop = entry - 2*ATR(500) = 49,000
        assert p.sl_price == pytest.approx(49_000.0)
        assert p.leverage < SWING_MAX_LEVERAGE
        assert tracking.latest_equity(conn, "swing") == pytest.approx(
            SWING_INITIAL_EQUITY)

    def test_idempotent_no_new_bars(self, conn):
        from live import swing, tracking
        tf_data = _make_tf_data()
        swing.process(conn, tf_data, main_mode="shadow")
        res2 = swing.process(conn, tf_data, main_mode="shadow")  # 같은 봉 재호출
        assert res2["events"] == 0
        assert len(tracking.load_open_positions(conn, "swing")) == 1

    def test_stop_hit_closes_and_records(self, conn):
        from live import swing, tracking
        swing.process(conn, _make_tf_data(), main_mode="shadow")
        res = swing.process(conn, _make_tf_data(with_stop_bar=True),
                            main_mode="shadow")
        assert res["events"] == 1
        assert tracking.load_open_positions(conn, "swing") == []
        trades = conn.execute(
            "SELECT * FROM btc_trading_history WHERE mode='swing'").fetchall()
        assert len(trades) == 1
        t = trades[0]
        assert t["exit_reason"] == "swing_sl"
        assert t["exit_price"] == pytest.approx(49_000.0)
        # 손실 ≈ -1R (수수료 포함 -1.0 ~ -1.2R 범위).
        assert -1.2 < t["r_multiple"] < -0.95
        eq = tracking.latest_equity(conn, "swing")
        assert eq < SWING_INITIAL_EQUITY

    def test_conflict_with_main_blocks_entry(self, conn):
        from live import swing, tracking
        # 메인(demo) 레인이 숏 보유 중 → 스윙 롱 진입 금지.
        tracking.save_position(conn, tracking.PositionRow(
            side="short", entry_price=50_000.0, qty=0.1, leverage=10.0,
            sl_price=51_000.0, tp1_price=0.0, tp2_price=0.0, tp3_price=0.0,
            liq_price=0.0, entry_time="2026-01-07 00:00:00+00:00",
            tranche_index=0, entry_bar_idx=0, initial_risk=100.0,
            mode="demo"))
        res = swing.process(conn, _make_tf_data(), main_mode="demo")
        assert res["events"] == 0
        assert tracking.load_open_positions(conn, "swing") == []

    def test_no_entry_without_cross(self, conn):
        from live import swing, tracking
        tf_data = _make_tf_data()
        # 크로스 제거: 마지막 4h ma10 도 ma35 아래로.
        tf_data["4h"].iloc[-1, tf_data["4h"].columns.get_loc("ma10")] = 48_950.0
        res = swing.process(conn, tf_data, main_mode="shadow")
        assert res["events"] == 0
        assert tracking.load_open_positions(conn, "swing") == []

    def test_1d_disagreement_blocks_long(self, conn):
        from live import swing, tracking
        tf_data = _make_tf_data()
        tf_data["1d"]["ma10"] = 46_000.0  # 1d 하락 정렬
        res = swing.process(conn, tf_data, main_mode="shadow")
        assert res["events"] == 0
        assert tracking.load_open_positions(conn, "swing") == []

    def test_disabled_flag(self, conn, monkeypatch):
        from live import swing
        monkeypatch.setattr(swing, "SWING_ENABLED", False)
        res = swing.process(conn, _make_tf_data(), main_mode="shadow")
        assert res == {"events": 0}
