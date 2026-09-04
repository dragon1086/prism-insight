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
    def test_stale_cursor_without_position_fast_forwards_without_replay(self, conn):
        from live import swing, tracking
        tf_data = _make_tf_data()
        stale = int((tf_data["30m"].index[0] - pd.Timedelta(days=1)).value)
        tracking.set_meta(conn, "last_processed_30m_ns", stale, "swing")
        tracking.set_meta(conn, "last_confirmed_4h_ns", stale, "swing")

        res = swing.process(conn, tf_data, main_mode="demo")

        assert res["events"] == 0
        assert tracking.load_open_positions(conn, "swing") == []
        assert tracking.get_meta(
            conn, "last_processed_30m_ns", "swing"
        ) == int(tf_data["30m"].index[-1].value)
        event = conn.execute(
            "SELECT kind FROM btc_events WHERE mode='swing' "
            "AND kind='cursor_resync'"
        ).fetchone()
        assert event is not None

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

    def test_exit_notification_includes_quantitative_trade_and_account_metrics(
            self, conn, monkeypatch):
        from live import swing, tracking

        pos = tracking.PositionRow(
            side="long", entry_price=50_000.0, qty=0.01, leverage=3.0,
            sl_price=49_000.0, tp1_price=0.0, tp2_price=0.0, tp3_price=0.0,
            liq_price=0.0, entry_time="2026-01-07T00:00:00+00:00",
            tranche_index=0, entry_bar_idx=0, initial_risk=10.0,
            entry_fee=0.10, mode="swing",
        )
        tracking.save_position(conn, pos)
        sent = []
        monkeypatch.setattr(
            swing, "_notify", lambda _main_mode, message: sent.append(message)
        )

        equity_after, _ = swing._close_position(
            conn, swing.VirtualBackend(conn), pos, 51_000.0,
            fee_rate=0.00055, reason="swing_ma35_exit",
            bar_time_str="2026-01-07T04:00:00+00:00", equity=10_000.0,
            trade_counter=0, main_mode="demo",
        )

        assert len(sent) == 1
        message = sent[0]
        assert "가격 기준 +2.00%" in message
        assert "전략 노출 3.0배" in message
        assert "가상 원장: 10,000.00 →" in message
        assert "+0.10%" in message
        assert "실현손익:" in message
        assert "보유 4시간" in message
        assert "데모 계정 모의투자입니다" in message
        assert equity_after > 10_000.0

    def test_entry_notification_separates_exchange_leverage_from_exposure(
            self, conn, monkeypatch):
        from live import swing

        sent = []
        monkeypatch.setattr(
            swing, "_notify",
            lambda _main_mode, message: sent.append(message) or True,
        )
        backend = swing.VirtualBackend(conn)

        res = swing.process(
            conn,
            _make_tf_data(),
            main_mode="demo",
            backend=backend,
        )

        assert res["events"] == 1
        assert len(sent) == 1
        message = sent[0]
        assert "체결수량:" in message
        assert "명목 포지션:" in message
        assert "전략 노출배수:" in message
        assert "거래소 레버리지: 가상체결" in message
        assert "보호 손절:" in message
        assert "손절 위험:" in message
        assert "고정 익절가는 없음" in message
        assert "4시간 MA10이 MA35를 상향 돌파" in message
        assert "완결 일봉 MA10 > MA35" in message
        assert "4시간 종가 > MA35" in message
        assert "가상자금 모의투자입니다" in message
        event = conn.execute(
            "SELECT level, kind, message FROM btc_events "
            "WHERE mode='swing' AND kind='notification' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert event["level"] == "info"
        assert "channel delivery ok" in event["message"]

    def test_entry_notification_records_channel_delivery_failure(
            self, conn, monkeypatch):
        from live import swing

        monkeypatch.setattr(swing, "_notify", lambda *_args: False)
        swing.process(
            conn,
            _make_tf_data(),
            main_mode="demo",
            backend=swing.VirtualBackend(conn),
        )

        event = conn.execute(
            "SELECT level, kind, message FROM btc_events "
            "WHERE mode='swing' AND kind='notification' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert event["level"] == "error"
        assert "channel delivery failed" in event["message"]

    def test_short_entry_notification_uses_bearish_rules(self):
        from live import swing, tracking

        pos = tracking.PositionRow(
            side="short", entry_price=50_000.0, qty=0.1, leverage=0.5,
            sl_price=51_000.0, tp1_price=0.0, tp2_price=0.0, tp3_price=0.0,
            liq_price=0.0, entry_time="2026-01-07 16:00:00+00:00",
            tranche_index=0, entry_bar_idx=0, initial_risk=100.0,
            mode="swing",
        )
        message = swing._build_entry_message(
            pos=pos,
            backend_name="virtual",
            equity=10_000.0,
            signal_context={
                "prev_ma10_4h": 50_100.0,
                "prev_ma35_4h": 50_000.0,
                "ma10_4h": 49_900.0,
                "ma35_4h": 50_000.0,
                "close_4h": 49_500.0,
                "ma10_1d": 49_000.0,
                "ma35_1d": 50_000.0,
            },
        )

        assert "MA35를 하향 돌파" in message
        assert "완결 일봉 MA10 ≤ MA35" in message
        assert "4시간 종가 < MA35" in message
        assert "MA35 위로 이탈하면 추세청산" in message

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


# ---------------------------------------------------------------------------
# ExchangeBackend — FakeSession 으로 실주문 경로 검증 (네트워크 없음)
# ---------------------------------------------------------------------------

class FakeSession:
    """pybit HTTP 흉내: 호출 기록 + 프로그래머블 응답."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self._oid = 0
        self.position_size = 0.0
        self.avg_price = 0.0
        self.total_equity = 10_000.0
        self.close_exec_price = 0.0
        self.execution_rows: list[dict] = []
        self.closed_pnl_rows: list[dict] = []

    def _record(self, name, kw):
        self.calls.append((name, kw))

    def set_leverage(self, **kw):
        self._record("set_leverage", kw)
        return {"retCode": 0, "result": {}}

    def place_order(self, **kw):
        self._record("place_order", kw)
        self._oid += 1
        # 시장가 진입이면 포지션이 생긴 것으로 시뮬.
        if kw.get("orderType") == "Market" and not kw.get("reduceOnly"):
            self.position_size = float(kw["qty"])
        if kw.get("reduceOnly") and kw.get("orderType") == "Market" \
                and "triggerPrice" not in kw:
            self.position_size = 0.0
        return {"retCode": 0, "result": {"orderId": f"oid-{self._oid}"}}

    def get_positions(self, **kw):
        self._record("get_positions", kw)
        lst = []
        if self.position_size > 0:
            lst = [{"size": str(self.position_size), "side": "Buy",
                    "avgPrice": str(self.avg_price), "leverage": "5"}]
        return {"retCode": 0, "result": {"list": lst}}

    def get_wallet_balance(self, **kw):
        self._record("get_wallet_balance", kw)
        return {"retCode": 0,
                "result": {"list": [{"totalEquity": str(self.total_equity)}]}}

    def get_executions(self, **kw):
        self._record("get_executions", kw)
        lst = self.execution_rows
        if not lst and self.close_exec_price > 0:
            lst = [{"closedSize": "0.1", "execQty": "0.1",
                    "execPrice": str(self.close_exec_price),
                    "orderId": kw.get("orderId", "")}]
        return {"retCode": 0, "result": {"list": lst}}

    def get_closed_pnl(self, **kw):
        self._record("get_closed_pnl", kw)
        return {"retCode": 0, "result": {"list": self.closed_pnl_rows}}

    def cancel_order(self, **kw):
        self._record("cancel_order", kw)
        return {"retCode": 0, "result": {}}

    def _calls_named(self, name):
        return [kw for n, kw in self.calls if n == name]


def _pos_row(**over):
    from live import tracking
    base = dict(
        side="long", entry_price=50_000.0, qty=0.1, leverage=1.0,
        sl_price=49_000.0, tp1_price=0.0, tp2_price=0.0, tp3_price=0.0,
        liq_price=0.0, entry_time="t", tranche_index=0, entry_bar_idx=0,
        initial_risk=100.0, mode="swing")
    base.update(over)
    return tracking.PositionRow(**base)


class TestExchangeBackend:
    def test_open_places_market_then_native_sl(self, conn):
        from live import swing, tracking
        sess = FakeSession()
        sess.avg_price = 50_050.0
        be = swing.ExchangeBackend(conn, sess)
        fill = be.open("long", 0.1, 49_000.0, 50_000.0)
        assert fill == pytest.approx(50_050.0)  # 거래소 avgPrice 가 체결가
        orders = sess._calls_named("place_order")
        assert len(orders) == 2
        entry, sl = orders
        assert entry["side"] == "Buy" and entry["orderType"] == "Market"
        assert not entry.get("reduceOnly")
        assert sl["reduceOnly"] is True and sl["triggerPrice"] == "49000.0"
        assert sl["triggerDirection"] == 2 and sl["side"] == "Sell"
        assert tracking.get_meta(conn, "swing_sl_order_id", "swing")

    def test_open_captures_exchange_position_context_for_notification(self, conn):
        from live import swing

        sess = FakeSession()
        sess.avg_price = 50_050.0
        be = swing.ExchangeBackend(conn, sess)

        assert be.open("long", 0.1, 49_000.0, 50_000.0) == pytest.approx(50_050.0)
        assert be.last_open_snapshot["leverage"] == pytest.approx(5.0)
        assert be.last_open_snapshot["qty"] == pytest.approx(0.1)

    def test_open_prefers_actual_execution_qty_price_and_fee(self, conn):
        from live import swing

        sess = FakeSession()
        sess.avg_price = 50_050.0
        sess.execution_rows = [{
            "orderId": "oid-1", "closedSize": "0", "execQty": "0.099",
            "execPrice": "50040", "execFee": "2.724678",
        }]
        be = swing.ExchangeBackend(conn, sess)

        fill = be.open("long", 0.1, 49_000.0, 50_000.0)

        assert fill == pytest.approx(50_040.0)
        assert be.last_open_snapshot["qty"] == pytest.approx(0.099)
        assert be.last_open_snapshot["entry_fee"] == pytest.approx(2.724678)
        assert sess._calls_named("place_order")[1]["qty"] == "0.099"

    def test_idempotent_exchange_responses_do_not_emit_false_errors(self, conn):
        from live import swing

        class IdempotentSession(FakeSession):
            def set_leverage(self, **kw):
                self._record("set_leverage", kw)
                return {"retCode": 110043, "retMsg": "leverage not modified"}

            def cancel_order(self, **kw):
                self._record("cancel_order", kw)
                return {"retCode": 110001, "retMsg": "order not exists"}

        be = swing.ExchangeBackend(conn, IdempotentSession())
        assert be._call("set_leverage") is not None
        assert be._call("cancel_order") is not None
        errors = conn.execute(
            "SELECT COUNT(*) AS n FROM btc_events "
            "WHERE mode='swing' AND level='error'"
        ).fetchone()
        assert errors["n"] == 0

    def test_check_stop_detects_position_gone(self, conn):
        from live import swing, tracking
        sess = FakeSession()
        sess.position_size = 0.0  # 스탑 체결로 포지션 소멸 상태
        sess.close_exec_price = 48_990.0
        tracking.set_meta(conn, "swing_sl_order_id", "oid-7", "swing")
        be = swing.ExchangeBackend(conn, sess)
        price = be.check_stop(_pos_row(), None)
        assert price == pytest.approx(48_990.0)  # 실체결가 사용
        assert sess._calls_named("cancel_order")  # 잔여 SL 정리
        assert tracking.get_meta(conn, "swing_sl_order_id", "swing") == ""

    def test_check_stop_captures_bybit_closed_pnl_settlement(self, conn):
        from live import swing, tracking

        sess = FakeSession()
        sess.execution_rows = [{
            "orderId": "oid-7", "closedSize": "1.299", "execQty": "1.299",
            "execPrice": "79356.3", "execFee": "56.69610854",
        }]
        sess.closed_pnl_rows = [{
            "orderId": "oid-7", "qty": "1.299",
            "avgEntryPrice": "81453.3", "avgExitPrice": "79356.3",
            "closedPnl": "-2853.89941874", "openFee": "58.19431019",
            "closeFee": "56.69610854",
        }]
        tracking.set_meta(conn, "swing_sl_order_id", "oid-7", "swing")
        tracking.set_meta(conn, "swing_exchange_leverage", 5.0, "swing")
        be = swing.ExchangeBackend(conn, sess)

        price = be.check_stop(_pos_row(
            entry_price=81_453.3, qty=1.299, leverage=0.57,
            entry_fee=58.19431019,
        ), None)

        assert price == pytest.approx(79_356.3)
        assert be.last_close_snapshot["closed_pnl"] == pytest.approx(
            -2_853.89941874
        )
        assert be.last_close_snapshot["funding_paid"] == pytest.approx(
            15.00600001
        )
        assert be.last_close_snapshot["exchange_leverage"] == pytest.approx(5.0)

    def test_check_stop_holds_when_query_fails(self, conn, monkeypatch):
        from live import swing

        class DeadSession:
            def get_positions(self, **kw):
                raise ConnectionError("down")
        monkeypatch.setattr(swing, "_RETRY_SLEEP_SEC", 0.0)
        be = swing.ExchangeBackend(conn, DeadSession())
        # 조회 실패 → None (판단 유보, 청산 기록 금지).
        assert be.check_stop(_pos_row(), None) is None

    def test_close_cancels_sl_and_market_reduces(self, conn):
        from live import swing, tracking
        sess = FakeSession()
        sess.position_size = 0.1
        sess.close_exec_price = 50_500.0
        tracking.set_meta(conn, "swing_sl_order_id", "oid-3", "swing")
        be = swing.ExchangeBackend(conn, sess)
        fill = be.close(_pos_row(), 50_400.0)
        assert fill == pytest.approx(50_500.0)
        assert sess._calls_named("cancel_order")[0]["orderId"] == "oid-3"
        reduces = [kw for kw in sess._calls_named("place_order")
                   if kw.get("reduceOnly")]
        assert reduces and reduces[0]["side"] == "Sell"

    def test_exchange_exit_uses_confirmed_pnl_without_stale_equity_gain(
            self, conn, monkeypatch):
        from live import swing, tracking

        sent = []
        monkeypatch.setattr(
            swing, "_notify",
            lambda _main_mode, message: sent.append(message) or True,
        )
        sess = FakeSession()
        sess.total_equity = 180_272.51154821
        be = swing.ExchangeBackend(conn, sess)
        be.last_close_snapshot = {
            "qty": 1.299,
            "entry_price": 81_453.3,
            "exit_price": 79_356.3,
            "closed_pnl": -2_853.89941874,
            "open_fee": 58.19431019,
            "close_fee": 56.69610854,
            "funding_paid": 15.00600001,
            "exchange_leverage": 5.0,
        }
        tracking.set_meta(
            conn, "swing_entry_account_equity", 180_500.0, "swing"
        )
        pos = _pos_row(
            entry_price=81_453.3, qty=1.2989409015, leverage=0.57129,
            sl_price=79_356.3, entry_time="2026-09-03T04:54:00+00:00",
            initial_risk=2_724.0, entry_fee=58.19431019,
        )
        tracking.save_position(conn, pos)
        pos = tracking.load_open_positions(conn, "swing")[0]

        equity_after, _ = swing._close_position(
            conn, be, pos, 79_356.3,
            fee_rate=0.00055 + 0.0002, reason="swing_sl",
            bar_time_str="2026-09-03T21:24:00+00:00", equity=166_566.0082,
            trade_counter=0, main_mode="demo",
        )

        trade = conn.execute(
            "SELECT * FROM btc_trading_history WHERE mode='swing'"
        ).fetchone()
        assert trade["net_pnl"] == pytest.approx(-2_853.89941874)
        assert trade["fee_paid"] == pytest.approx(114.89041873)
        assert trade["funding_paid"] == pytest.approx(15.00600001)
        assert trade["qty"] == pytest.approx(1.299)
        assert equity_after == pytest.approx(180_272.51154821)
        assert len(sent) == 1
        message = sent[0]
        assert "Bybit 확정 실현손익: -2,853.90달러" in message
        assert "가격손익 -2,724.00달러" in message
        assert "매매수수료 114.89달러" in message
        assert "펀딩비 15.01달러" in message
        assert "현재 교차계좌 평가액: 180,272.51달러" in message
        assert "현재 스냅샷이며 이번 거래 수익 아님" in message
        assert "거래소 레버리지 5배 · 전략 노출 0.6배" in message
        assert "166,566.01 →" not in message
        assert "+13,706" not in message
        assert "증거금 기준" not in message
        assert tracking.get_meta(
            conn, "swing_entry_account_equity", "swing") is None

    def test_process_e2e_with_exchange_backend(self, conn, monkeypatch):
        from live import swing, tracking
        sent = []
        monkeypatch.setattr(
            swing, "_notify",
            lambda _main_mode, message: sent.append(message) or True,
        )
        sess = FakeSession()
        sess.avg_price = 50_020.0
        be = swing.ExchangeBackend(conn, sess)
        res = swing.process(conn, _make_tf_data(), main_mode="demo", backend=be)
        assert res["events"] == 1
        pos = tracking.load_open_positions(conn, "swing")
        assert len(pos) == 1
        assert pos[0].entry_price == pytest.approx(50_020.0)
        # 지갑 equity 가 원장 시드의 진실.
        assert tracking.latest_equity(conn, "swing") == pytest.approx(10_000.0)
        assert tracking.get_meta(
            conn, "swing_entry_account_equity", "swing") == pytest.approx(
                10_000.0
            )
        assert tracking.get_meta(
            conn, "swing_exchange_leverage", "swing") == pytest.approx(5.0)
        assert len(sent) == 1
        assert "거래소 레버리지: 5배" in sent[0]
        assert "전략 노출배수: 계좌 평가액 대비" in sent[0]
        assert "거래소 조건부 주문 부착 완료" in sent[0]

    def test_same_key_guard_forces_virtual(self, conn, monkeypatch):
        from live import swing
        monkeypatch.setenv("BYBIT_SWING_DEMO_API_KEY", "SAMEKEY")
        monkeypatch.setenv("BYBIT_SWING_DEMO_API_SECRET", "s")
        monkeypatch.setenv("BYBIT_DEMO_API_KEY", "SAMEKEY")
        sess, err = swing._make_swing_session()
        assert sess is None
        assert "동일" in err

    def test_backend_autoselect_virtual_without_keys(self, conn, monkeypatch):
        from live import swing
        monkeypatch.setattr(swing, "_make_swing_session",
                            lambda: (None, "미설정"))
        be = swing._make_backend(conn, "demo")
        assert be.name == "virtual"

    def test_backend_rearms_fallback_notice_after_exchange_recovers(
            self, conn, monkeypatch):
        from live import swing, tracking
        tracking.set_meta(conn, "swing_exec_fallback_notified", 1, "swing")
        monkeypatch.setattr(swing, "_make_swing_session",
                            lambda: (FakeSession(), None))
        be = swing._make_backend(conn, "demo")
        assert be.name == "exchange"
        assert tracking.get_meta(
            conn, "swing_exec_fallback_notified", "swing") == 0
