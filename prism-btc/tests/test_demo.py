# tests/test_demo.py — Bybit 데모 어댑터 (DemoAdapter) 테스트
#
# 원칙: 네트워크 호출 0. pybit HTTP 를 인메모리 FakeExchange 로 대체(monkeypatch).
#       모든 거래소 호출은 call log 로 검증한다. 결정 흐름은 demo.py 의 실제 구조를
#       따르되, exits 분기는 실제 core.evaluate_exits 를 통과시켜 결정적으로 구동한다.
#
# 검증 케이스 (요청 명세 매핑):
#   1. 키 없을 때 graceful 스킵 (_make_session None → 예외 없이 스킵 + error 이벤트)
#   2. _sync_state(reconcile): equity/포지션 → btc_equity_curve/btc_positions 갱신
#   3. 진입 = post-only Limit (place_order timeInForce=PostOnly) — call log 확인
#   4. 진입 체결 감지 시 SL stop-market(reduceOnly) + TP1 reduce-only limit 동반 발행
#   5. 신호/SL 청산 + ForceReduce → reduce-only 시장가(Market, IOC) 주문
#   6. ★ 출금/이체/convert 류 메서드가 단 한 번도 호출되지 않음 (call log assert)
#   7. 거래소 호출 실패(retCode!=0 / 예외) 시 process_bar 가 예외를 밖으로 안 던짐
from __future__ import annotations

import json

import pandas as pd
import pytest

from live import demo, tracking
from live.demo import DemoAdapter
from backtest.engine import ENTRY_ORDER_EXPIRY_BARS
from engine.signal import Signal
from engine.sizing import TRANCHE_FRACS


# ---------------------------------------------------------------------------
# FakeExchange — pybit unified_trading.HTTP 인터페이스를 흉내내는 인메모리 가짜.
# 모든 호출을 calls 에 기록하고 retCode=0 형식 응답을 반환한다.
# ---------------------------------------------------------------------------

# 절대 호출되어선 안 되는 자금이동 류 메서드 (case 6).
_FORBIDDEN_METHODS = (
    "withdraw", "create_withdrawal", "withdraw_records",
    "create_internal_transfer", "create_universal_transfer",
    "create_transfer", "transfer", "convert", "create_convert",
    "exchange_coin", "request_a_quote", "confirm_a_quote",
)


class FakeExchange:
    """pybit HTTP 의 부분 모킹. 주문을 내부 상태에 저장하고 reconcile 를 시뮬레이트."""

    def __init__(self, equity=10_000.0, position=None,
                 open_orders=None, executions=None, fail_all=False,
                 raise_all=False):
        self.calls = []                      # [(method, kwargs), ...]
        self._equity = equity
        self._position = position            # dict|None (Bybit get_positions row)
        self._open_orders = list(open_orders or [])
        self._executions = list(executions or [])
        self._fail_all = fail_all            # retCode != 0 로 응답
        self._raise_all = raise_all          # 예외를 던짐
        self._order_seq = 0
        self.placed_orders = []              # place_order 페이로드 누적

    # --- 자금이동 류: 정의해두되 호출되면 즉시 실패시켜 누수를 잡는다 ---
    def _forbidden(self, name):
        def _stub(**kwargs):  # pragma: no cover - 호출되면 안 됨
            self.calls.append((name, kwargs))
            raise AssertionError(f"FORBIDDEN exchange method called: {name}")
        return _stub

    def __getattr__(self, name):
        # FORBIDDEN 메서드는 존재하게 만들어, 혹시 호출되면 AssertionError 로 터뜨린다.
        if name in _FORBIDDEN_METHODS:
            return self._forbidden(name)
        raise AttributeError(name)

    def _record(self, method, kwargs):
        self.calls.append((method, kwargs))
        if self._raise_all:
            raise RuntimeError(f"boom in {method}")
        if self._fail_all:
            return {"retCode": 10001, "retMsg": "simulated failure", "result": {}}
        return None

    def _ok(self, result):
        return {"retCode": 0, "retMsg": "OK", "result": result}

    # --- read 계열 ---
    def get_wallet_balance(self, **kwargs):
        bad = self._record("get_wallet_balance", kwargs)
        if bad is not None:
            return bad
        return self._ok({"list": [{"totalEquity": str(self._equity)}]})

    def get_positions(self, **kwargs):
        bad = self._record("get_positions", kwargs)
        if bad is not None:
            return bad
        lst = [self._position] if self._position else []
        return self._ok({"list": lst})

    def get_open_orders(self, **kwargs):
        bad = self._record("get_open_orders", kwargs)
        if bad is not None:
            return bad
        return self._ok({"list": list(self._open_orders)})

    def get_executions(self, **kwargs):
        bad = self._record("get_executions", kwargs)
        if bad is not None:
            return bad
        return self._ok({"list": list(self._executions)})

    # --- write 계열 ---
    def place_order(self, **kwargs):
        bad = self._record("place_order", kwargs)
        if bad is not None:
            return bad
        self._order_seq += 1
        oid = f"oid-{self._order_seq}"
        self.placed_orders.append(kwargs)
        return self._ok({"orderId": oid})

    def cancel_order(self, **kwargs):
        bad = self._record("cancel_order", kwargs)
        if bad is not None:
            return bad
        return self._ok({"orderId": kwargs.get("orderId", "")})

    def amend_order(self, **kwargs):
        bad = self._record("amend_order", kwargs)
        if bad is not None:
            return bad
        return self._ok({"orderId": kwargs.get("orderId", "")})

    def set_leverage(self, **kwargs):
        bad = self._record("set_leverage", kwargs)
        if bad is not None:
            return bad
        return self._ok({})

    # --- 통합 포지션 시뮬레이션 헬퍼 (피라미딩 테스트용) ---
    def set_position(self, side="Buy", size="0.030", avg="100.0",
                     lev="10", liq="80.0"):
        """거래소 단일 통합 포지션을 갱신 (트랜치 체결로 size/avg 증가 시뮬레이트)."""
        self._position = {"side": side, "size": str(size), "avgPrice": str(avg),
                          "leverage": str(lev), "liqPrice": str(liq),
                          "unrealisedPnl": "0"}

    def clear_position(self):
        self._position = None

    # --- call log 헬퍼 ---
    def methods_called(self):
        return [m for m, _ in self.calls]

    def calls_to(self, method):
        return [kw for m, kw in self.calls if m == method]


# ---------------------------------------------------------------------------
# 헬퍼 — DB / 봉 / 어댑터 조립
# ---------------------------------------------------------------------------

def _conn():
    conn = tracking.get_connection(":memory:")
    tracking.ensure_schema(conn)
    return conn


# 2026-01-01 00:00:00 UTC 기준 30m 봉 시각 (절대 인덱스가 안정적인 값).
_BASE_TS = pd.Timestamp("2026-01-01 00:00:00", tz="UTC")


def _bar(close=100.0, high=None, low=None):
    return pd.Series({
        "open": close,
        "high": high if high is not None else close,
        "low": low if low is not None else close,
        "close": close,
        "volume": 1.0,
    })


def _bar_idx_for(ts: pd.Timestamp) -> int:
    return demo.bar_index_for(int(ts.value // 1_000_000))


def _make_adapter(conn, fake, mode="demo"):
    """tf_data 는 빈 dict (exits 의 trailing/entry 슬라이스는 가드로 우회).
    _make_session 을 monkeypatch 한 뒤 호출해야 fake 가 주입된다."""
    return DemoAdapter(conn, tf_data={}, funding_times=[], funding_rates=[], mode=mode)


def _patch_session(monkeypatch, fake):
    monkeypatch.setattr(demo, "_make_session", lambda: (fake, None))


def _ex_position(side="Buy", size="0.030", avg="100.0", lev="10", liq="80.0"):
    """Bybit get_positions 형식 포지션 행."""
    return {"side": side, "size": size, "avgPrice": avg,
            "leverage": lev, "liqPrice": liq, "unrealisedPnl": "0"}


def _seed_pending(adapter, bar_idx, side="long", sl=90.0, tp1=110.0,
                  lev=10.0, order_id="entry-oid"):
    """진입 post-only 주문이 직전 봉에 걸려있는 상태를 meta 로 시드."""
    adapter._set_meta("pending_order", {
        "order_id": order_id, "side": side, "limit_price": 100.0,
        "bar_idx": bar_idx, "sizing_qty": 0.03, "sizing_leverage": lev,
        "sizing_sl_price": sl, "sizing_tp1_price": tp1,
        "sizing_tp2_price": tp1 + 10, "sizing_tp3_price": tp1 + 20,
        "sizing_liq_price": 80.0, "initial_risk": 50.0, "tranche_index": 0,
    })


def _seed_open_position(adapter, conn, side="long", entry=100.0, qty=0.03,
                        sl=90.0, tp1=110.0, liq=80.0, mode="demo"):
    """로컬 btc_positions(demo) 에 열린 포지션을 시드 (exits 평가 대상)."""
    pos = tracking.PositionRow(
        side=side, entry_price=entry, qty=qty, leverage=10.0, sl_price=sl,
        tp1_price=tp1, tp2_price=tp1 + 10, tp3_price=tp1 + 20, liq_price=liq,
        entry_time=str(_BASE_TS), tranche_index=0,
        entry_bar_idx=_bar_idx_for(_BASE_TS), initial_risk=50.0,
        initial_qty=qty, mode=mode,
    )
    tracking.save_position(conn, pos)
    return pos


# ===========================================================================
# Case 1 — 키 없을 때 graceful 스킵
# ===========================================================================

class TestKeylessSkip:
    def test_no_session_skips_without_exception_and_logs_error(self, monkeypatch):
        monkeypatch.setattr(demo, "_make_session",
                            lambda: (None, "BYBIT_DEMO_API_KEY/SECRET 미설정"))
        conn = _conn()
        adapter = _make_adapter(conn, fake=None)
        assert adapter.sess is None

        # 예외 없이 조용히 스킵해야 한다.
        adapter.process_bar(_BASE_TS, _bar(close=100.0),
                            new_4h_confirmed=True, cur_4h_ns=None)

        # error 이벤트가 기록됐다.
        kinds = [r["kind"] for r in conn.execute(
            "SELECT kind FROM btc_events WHERE mode='demo'").fetchall()]
        assert "error" in kinds
        # 거래소엔 접근 자체를 안 했으니 equity/position 테이블은 비어있다.
        assert conn.execute(
            "SELECT COUNT(*) FROM btc_equity_curve").fetchone()[0] == 0


# ===========================================================================
# Case 2 — reconcile: equity/포지션이 로컬 테이블에 반영
# ===========================================================================

class TestSyncState:
    def test_sync_state_records_equity_from_exchange(self, monkeypatch):
        fake = FakeExchange(equity=12_345.0, position=None)
        _patch_session(monkeypatch, fake)
        conn = _conn()
        adapter = _make_adapter(conn, fake)

        snap = adapter._sync_state(str(_BASE_TS))

        assert snap["equity"] == pytest.approx(12_345.0)
        assert snap["position"] is None
        # btc_equity_curve(demo) 에 기록됐다.
        eq = conn.execute(
            "SELECT equity FROM btc_equity_curve WHERE mode='demo'").fetchone()
        assert eq[0] == pytest.approx(12_345.0)
        # 읽기 3종(잔고/포지션/미체결)이 호출됐다.
        called = fake.methods_called()
        assert "get_wallet_balance" in called
        assert "get_positions" in called
        assert "get_open_orders" in called

    def test_sync_state_exposes_exchange_position_snapshot(self, monkeypatch):
        fake = FakeExchange(equity=10_000.0,
                            position=_ex_position(side="Buy", size="0.050",
                                                  avg="101.5", lev="8"))
        _patch_session(monkeypatch, fake)
        conn = _conn()
        adapter = _make_adapter(conn, fake)

        snap = adapter._sync_state(str(_BASE_TS))

        assert snap["position"]["side"] == "long"
        assert snap["position"]["qty"] == pytest.approx(0.05)
        assert snap["position"]["entry_price"] == pytest.approx(101.5)
        assert snap["position"]["leverage"] == pytest.approx(8.0)

    def test_process_bar_mirrors_exchange_position_into_btc_positions(self, monkeypatch):
        # 진입 주문이 직전 봉에 걸려있고, 이번 봉에서 거래소 포지션이 출현 →
        # btc_positions(demo) 에 미러되어야 한다 (= equity/포지션 reconcile 결과 반영).
        bar_idx = _bar_idx_for(_BASE_TS)
        fake = FakeExchange(
            equity=10_000.0,
            position=_ex_position(side="Buy", size="0.030", avg="100.0"),
            open_orders=[],  # pending 주문은 더 이상 미체결 = 체결됨.
        )
        _patch_session(monkeypatch, fake)
        conn = _conn()
        adapter = _make_adapter(conn, fake)
        _seed_pending(adapter, bar_idx, side="long")

        adapter.process_bar(_BASE_TS, _bar(close=100.0),
                            new_4h_confirmed=False, cur_4h_ns=None)

        positions = tracking.load_open_positions(conn, "demo")
        assert len(positions) == 1
        assert positions[0].side == "long"
        assert positions[0].qty == pytest.approx(0.03)


# ===========================================================================
# Case 3 — 진입 = post-only Limit
# ===========================================================================

class TestEntryPostOnly:
    def test_place_limit_postonly_uses_postonly_limit(self, monkeypatch):
        fake = FakeExchange()
        _patch_session(monkeypatch, fake)
        conn = _conn()
        adapter = _make_adapter(conn, fake)

        oid = adapter._place_limit_postonly("long", qty=0.03, price=99.5)

        assert oid == "oid-1"
        orders = fake.calls_to("place_order")
        assert len(orders) == 1
        o = orders[0]
        assert o["orderType"] == "Limit"
        assert o["timeInForce"] == "PostOnly"
        assert o["side"] == "Buy"            # long → Buy
        assert "reduceOnly" not in o          # 진입은 reduce-only 아님.

    def test_short_entry_places_sell_postonly_limit(self, monkeypatch):
        fake = FakeExchange()
        _patch_session(monkeypatch, fake)
        conn = _conn()
        adapter = _make_adapter(conn, fake)

        adapter._place_limit_postonly("short", qty=0.03, price=100.5)
        o = fake.calls_to("place_order")[0]
        assert o["side"] == "Sell"
        assert o["orderType"] == "Limit"
        assert o["timeInForce"] == "PostOnly"


# ===========================================================================
# Case 4 — 진입 체결 감지 시 SL stop-market + TP1 reduce-only 동반 주문
# ===========================================================================

class TestEntryFillAttachesSlTp:
    def test_fill_emits_stop_market_sl_and_reduce_only_tp1(self, monkeypatch):
        bar_idx = _bar_idx_for(_BASE_TS)
        fake = FakeExchange(
            equity=10_000.0,
            position=_ex_position(side="Buy", size="0.030", avg="100.0"),
            open_orders=[],  # pending 진입주문이 사라짐 = 체결.
        )
        _patch_session(monkeypatch, fake)
        conn = _conn()
        adapter = _make_adapter(conn, fake)
        _seed_pending(adapter, bar_idx, side="long", sl=90.0, tp1=110.0)

        adapter.process_bar(_BASE_TS, _bar(close=100.0),
                            new_4h_confirmed=False, cur_4h_ns=None)

        placed = fake.placed_orders
        # 진입 체결 후 SL + TP1 = 2건의 place_order (진입 자체는 직전 봉).
        assert len(placed) == 2

        # SL = stop-market reduce-only. long → close_side=Sell, triggerDirection=2.
        sl = [o for o in placed if o.get("triggerPrice")]
        assert len(sl) == 1
        assert sl[0]["orderType"] == "Market"
        assert sl[0]["reduceOnly"] is True
        assert sl[0]["side"] == "Sell"
        assert int(sl[0]["triggerDirection"]) == 2

        # TP1 = reduce-only limit. close_side=Sell, qty = 진입수량/3.
        tp = [o for o in placed
              if o.get("orderType") == "Limit" and o.get("reduceOnly")]
        assert len(tp) == 1
        assert tp[0]["side"] == "Sell"
        assert tp[0]["timeInForce"] == "PostOnly"
        assert tp[0]["qty"] == f"{0.03 / 3.0:.3f}"

        # SL/TP orderId 가 meta 에 영속됐다.
        assert adapter._get_meta("sl_order_id") is not None
        assert adapter._get_meta("tp_order_id") is not None


# ===========================================================================
# Case 5 — 신호/SL 청산 + ForceReduce → reduce-only 시장가 주문
# ===========================================================================

class TestExitReduceOnly:
    def test_sl_cross_emits_market_reduce_only_ioc(self, monkeypatch):
        # 거래소 포지션이 존재하고, 봉 저가가 SL 을 관통 → ClosePosition → 시장가 reduce.
        fake = FakeExchange(
            equity=10_000.0,
            position=_ex_position(side="Buy", size="0.030", avg="100.0",
                                  liq="80.0"),
            open_orders=[],
        )
        _patch_session(monkeypatch, fake)
        conn = _conn()
        adapter = _make_adapter(conn, fake)
        # pending 없음 + 로컬 포지션 존재 → exits 평가 대상.
        _seed_open_position(conn=conn, adapter=adapter, side="long",
                            entry=100.0, qty=0.03, sl=95.0, tp1=110.0, liq=80.0)

        # 저가 94 < sl 95 → ClosePosition(reason='sl').
        adapter.process_bar(_BASE_TS, _bar(close=94.5, high=96.0, low=94.0),
                            new_4h_confirmed=False, cur_4h_ns=None)

        market_reduces = [o for o in fake.placed_orders
                          if o.get("orderType") == "Market"
                          and o.get("reduceOnly") and o.get("timeInForce") == "IOC"]
        assert len(market_reduces) >= 1
        mr = market_reduces[0]
        assert mr["side"] == "Sell"          # long 청산 → Sell.
        # 로컬 포지션이 정리됐다.
        assert tracking.load_open_positions(conn, "demo") == []

    def test_force_reduce_emits_market_reduce_only(self, monkeypatch):
        # 봉이 liq 버퍼 밴드를 침범 → ForceReduce → 시장가 부분 reduce.
        fake = FakeExchange(
            equity=10_000.0,
            position=_ex_position(side="Buy", size="0.030", avg="100.0",
                                  liq="80.0"),
            open_orders=[],
        )
        _patch_session(monkeypatch, fake)
        conn = _conn()
        adapter = _make_adapter(conn, fake)
        _seed_open_position(conn=conn, adapter=adapter, side="long",
                            entry=100.0, qty=0.03, sl=70.0, tp1=140.0, liq=80.0)

        # 저가 82 가 liq(80) 의 50% 버퍼 밴드를 침범 → ForceReduce (+ ClosePosition).
        adapter.process_bar(_BASE_TS, _bar(close=83.0, high=100.0, low=82.0),
                            new_4h_confirmed=False, cur_4h_ns=None)

        # 모든 reduce 주문은 reduce-only 시장가여야 한다.
        market_reduces = [o for o in fake.placed_orders
                          if o.get("orderType") == "Market" and o.get("reduceOnly")]
        assert len(market_reduces) >= 1
        for o in market_reduces:
            assert o["reduceOnly"] is True
            assert o["side"] == "Sell"        # long 방향 → reduce 는 Sell.


# ===========================================================================
# Case 6 — ★ 출금/이체/convert 류 메서드는 단 한 번도 호출되지 않음
# ===========================================================================

class TestNoFundsMovement:
    def _run_full_lifecycle(self, monkeypatch):
        """진입 시드 → 체결(SL/TP attach) → 다음 봉 SL 청산까지 한 사이클 구동."""
        bar_idx = _bar_idx_for(_BASE_TS)
        fake = FakeExchange(
            equity=10_000.0,
            position=_ex_position(side="Buy", size="0.030", avg="100.0",
                                  liq="80.0"),
            open_orders=[],
        )
        _patch_session(monkeypatch, fake)
        conn = _conn()
        adapter = _make_adapter(conn, fake)
        _seed_pending(adapter, bar_idx, side="long", sl=95.0, tp1=110.0)

        # 봉 1: 진입 체결 → SL/TP attach.
        adapter.process_bar(_BASE_TS, _bar(close=100.0),
                            new_4h_confirmed=False, cur_4h_ns=None)
        # 봉 2: SL 관통 → 시장가 청산.
        ts2 = _BASE_TS + pd.Timedelta(minutes=30)
        adapter.process_bar(ts2, _bar(close=94.0, high=99.0, low=93.0),
                            new_4h_confirmed=False, cur_4h_ns=None)
        return fake

    def test_no_withdraw_transfer_or_convert_methods_called(self, monkeypatch):
        fake = self._run_full_lifecycle(monkeypatch)
        called = set(fake.methods_called())
        for forbidden in _FORBIDDEN_METHODS:
            assert forbidden not in called, f"{forbidden} 가 호출됨!"
        # 호출된 메서드는 화이트리스트(읽기 + 주문/취소/수정/레버리지)에만 속한다.
        allowed = {
            "get_wallet_balance", "get_positions", "get_open_orders",
            "get_executions", "place_order", "cancel_order", "amend_order",
            "set_leverage",
        }
        assert called.issubset(allowed), f"예상 외 메서드 호출: {called - allowed}"


# ===========================================================================
# Case 7 — 거래소 실패/예외 시 process_bar 가 예외를 밖으로 던지지 않음
# ===========================================================================

class TestDaemonNeverCrashes:
    def test_retcode_failure_does_not_propagate(self, monkeypatch):
        fake = FakeExchange(fail_all=True)   # 모든 호출 retCode!=0.
        _patch_session(monkeypatch, fake)
        conn = _conn()
        adapter = _make_adapter(conn, fake)

        # 예외 없이 반환되어야 한다 (데몬 비중단).
        adapter.process_bar(_BASE_TS, _bar(close=100.0),
                            new_4h_confirmed=True, cur_4h_ns=None)

        # _call 이 실패를 error 이벤트로 흡수했다.
        kinds = [r["kind"] for r in conn.execute(
            "SELECT kind FROM btc_events WHERE mode='demo'").fetchall()]
        assert "error" in kinds

    def test_exchange_raises_does_not_propagate(self, monkeypatch):
        fake = FakeExchange(raise_all=True)  # 모든 호출이 예외.
        _patch_session(monkeypatch, fake)
        conn = _conn()
        adapter = _make_adapter(conn, fake)

        # 예외가 _call 의 try/except + process_bar 래퍼에서 흡수되어야 한다.
        adapter.process_bar(_BASE_TS, _bar(close=100.0),
                            new_4h_confirmed=True, cur_4h_ns=None)

        rows = conn.execute(
            "SELECT COUNT(*) FROM btc_events WHERE mode='demo' AND level='error'"
        ).fetchone()
        assert rows[0] >= 1

    def test_inner_exception_absorbed_by_process_bar_wrapper(self, monkeypatch):
        # _process_bar_inner 가 던지는 임의 예외도 래퍼가 흡수한다.
        fake = FakeExchange()
        _patch_session(monkeypatch, fake)
        conn = _conn()
        adapter = _make_adapter(conn, fake)

        def boom(*a, **k):
            raise ValueError("synthetic inner failure")

        monkeypatch.setattr(adapter, "_sync_state", boom)
        # 예외가 밖으로 나오면 이 호출이 실패한다 — 나오면 안 된다.
        adapter.process_bar(_BASE_TS, _bar(close=100.0),
                            new_4h_confirmed=True, cur_4h_ns=None)

        kinds = [r["kind"] for r in conn.execute(
            "SELECT kind FROM btc_events WHERE mode='demo'").fetchall()]
        assert "error" in kinds


# ===========================================================================
# 피라미딩 (3트랜치 40/30/30) — 다중 트랜치 검증
# ===========================================================================
#
# Section 4 진입 게이트는 _build_snapshot_at(tf_data) 가 None 이 아니어야 평가된다.
# 기존 케이스는 tf_data={} 라 진입신호가 절대 안 뜬다(=의도된 격리). 피라미딩
# 진입을 결정적으로 구동하려면 demo._build_snapshot_at / demo.generate_signal 을
# 모킹해 신호를 주입한다 (core.evaluate_entry 의 피라미딩 게이트는 실제로 통과시킴).
# _entry_inputs 는 빈 tf_data 에서 fallback(ATR=2%, swing ±2%, MA35=entry)을 쓰므로
# compute_sizing 이 결정적으로 qty>0 을 반환한다.


def _force_signal(monkeypatch, side="long", strength=80.0):
    """Section 4 가 평가되도록 snapshot/signal 을 주입.
    snapshot 은 비-None 센티넬이면 충분 (generate_signal 을 직접 모킹하므로 내용 무관)."""
    monkeypatch.setattr(demo, "_build_snapshot_at",
                        lambda tf_data, bar_time: object())
    monkeypatch.setattr(
        demo, "generate_signal",
        lambda snapshot: Signal(side=side, strength=strength, reason="forced"))


def _seed_local_tranche(adapter, conn, *, tranche_index, side="long",
                        entry=100.0, qty=0.03, sl=90.0, tp1=110.0, liq=80.0,
                        initial_risk=50.0, mode="demo"):
    """로컬 장부(btc_positions[demo])에 트랜치 하나를 직접 적재."""
    pos = tracking.PositionRow(
        side=side, entry_price=entry, qty=qty, leverage=10.0, sl_price=sl,
        tp1_price=tp1, tp2_price=tp1 + 10, tp3_price=tp1 + 20, liq_price=liq,
        entry_time=str(_BASE_TS), tranche_index=tranche_index,
        entry_bar_idx=_bar_idx_for(_BASE_TS), initial_risk=initial_risk,
        initial_qty=qty, mode=mode,
    )
    tracking.save_position(conn, pos)
    return pos


def _seed_pending_tranche(adapter, bar_idx, tranche_index, *, side="long",
                          sl=92.0, tp1=112.0, lev=10.0, order_id="pyr-oid"):
    """피라미딩 추가 트랜치의 pending 진입주문을 meta 로 시드."""
    adapter._set_meta("pending_order", {
        "order_id": order_id, "side": side, "limit_price": 102.0,
        "bar_idx": bar_idx, "sizing_qty": 0.02, "sizing_leverage": lev,
        "sizing_sl_price": sl, "sizing_tp1_price": tp1,
        "sizing_tp2_price": tp1 + 10, "sizing_tp3_price": tp1 + 20,
        "sizing_liq_price": 80.0, "initial_risk": 40.0,
        "tranche_index": tranche_index,
    })


# ---------------------------------------------------------------------------
# 1. 트랜치 1 추가 진입: 같은 방향 1개 보유 → current_tranche=1 평가 + post-only 발행
# ---------------------------------------------------------------------------

class TestPyramidAddTranche:
    def test_second_tranche_signal_places_postonly_with_tranche_index_1(
            self, monkeypatch):
        # 거래소엔 통합 long 포지션(=트랜치0 체결분)이 존재, 로컬엔 트랜치0 보유.
        fake = FakeExchange(
            equity=10_000.0,
            position=_ex_position(side="Buy", size="0.030", avg="100.0"),
            open_orders=[],
        )
        _patch_session(monkeypatch, fake)
        conn = _conn()
        adapter = _make_adapter(conn, fake)
        _seed_local_tranche(conn=conn, adapter=adapter, tranche_index=0,
                            side="long", entry=100.0, qty=0.03)

        # current_price(105) > avg_entry(100) → can_add_tranche(long) 통과.
        _force_signal(monkeypatch, side="long", strength=80.0)
        adapter.process_bar(_BASE_TS, _bar(close=105.0, high=106.0, low=104.0),
                            new_4h_confirmed=True, cur_4h_ns=12345)

        # 추가 트랜치 = post-only Limit 진입주문 1건 (reduce-only 아님).
        entries = [o for o in fake.placed_orders
                   if o.get("orderType") == "Limit"
                   and o.get("timeInForce") == "PostOnly"
                   and "reduceOnly" not in o]
        assert len(entries) == 1
        assert entries[0]["side"] == "Buy"

        # pending_order 가 tranche_index=1 로 기록됐다 (= evaluate_entry(current_tranche=1)).
        pending = adapter._get_meta("pending_order", None)
        assert pending is not None
        assert int(pending["tranche_index"]) == 1

    def test_pyramid_blocked_when_not_in_profit(self, monkeypatch):
        # current_price(98) < avg_entry(100) → can_add_tranche(long) 차단 → 진입주문 0.
        fake = FakeExchange(
            equity=10_000.0,
            position=_ex_position(side="Buy", size="0.030", avg="100.0"),
            open_orders=[],
        )
        _patch_session(monkeypatch, fake)
        conn = _conn()
        adapter = _make_adapter(conn, fake)
        _seed_local_tranche(conn=conn, adapter=adapter, tranche_index=0,
                            side="long", entry=100.0, qty=0.03)

        _force_signal(monkeypatch, side="long", strength=80.0)
        adapter.process_bar(_BASE_TS, _bar(close=98.0, high=99.0, low=97.0),
                            new_4h_confirmed=True, cur_4h_ns=12345)

        entries = [o for o in fake.placed_orders
                   if o.get("orderType") == "Limit" and "reduceOnly" not in o]
        assert entries == []
        assert adapter._get_meta("pending_order", None) is None


# ---------------------------------------------------------------------------
# 2. 통합 size 증가로 트랜치 체결 감지 → 로컬 장부에 append(덮어쓰기 아님),
#    tranche_index/initial_risk 가 트랜치별 보존
# ---------------------------------------------------------------------------

class TestPyramidFillAppends:
    def test_tranche_fill_appends_preserving_tranche_index_and_risk(
            self, monkeypatch):
        bar_idx = _bar_idx_for(_BASE_TS)
        # 거래소 통합 포지션: 트랜치0(0.03)에 트랜치1(0.02)이 더해져 0.05 로 증가.
        # 평균단가도 갱신(거래소 평균) — 100→101.
        fake = FakeExchange(
            equity=10_000.0,
            position=_ex_position(side="Buy", size="0.050", avg="101.0"),
            open_orders=[],  # pending 진입주문 사라짐 = 체결.
        )
        _patch_session(monkeypatch, fake)
        conn = _conn()
        adapter = _make_adapter(conn, fake)
        # 직전 로컬 합계 = 트랜치0(0.03, risk 50).
        _seed_local_tranche(conn=conn, adapter=adapter, tranche_index=0,
                            side="long", entry=100.0, qty=0.03,
                            initial_risk=50.0)
        # 트랜치1 pending (risk 40, sl 92, tp1 112).
        _seed_pending_tranche(adapter, bar_idx, tranche_index=1, side="long",
                              sl=92.0, tp1=112.0)

        adapter.process_bar(_BASE_TS, _bar(close=101.0, high=102.0, low=100.0),
                            new_4h_confirmed=False, cur_4h_ns=None)

        positions = tracking.load_open_positions(conn, "demo")
        # 덮어쓰기가 아니라 append → 트랜치 2개 보존.
        assert len(positions) == 2
        by_idx = {p.tranche_index: p for p in positions}
        assert set(by_idx) == {0, 1}
        # 트랜치별 initial_risk 보존 (0→50, 1→40).
        assert by_idx[0].initial_risk == pytest.approx(50.0)
        assert by_idx[1].initial_risk == pytest.approx(40.0)
        # 트랜치1 논리수량 = 통합총량 - 직전로컬합계 = 0.05 - 0.03 = 0.02.
        assert by_idx[1].qty == pytest.approx(0.02)
        # 트랜치1 SL/TP 는 그 트랜치의 sizing 값으로 보존.
        assert by_idx[1].sl_price == pytest.approx(92.0)
        assert by_idx[1].tp1_price == pytest.approx(112.0)


# ---------------------------------------------------------------------------
# 6. 피라미딩 트랜치 entry_price = 거래소 평균단가(avgPrice) 기준
#    (executor 메모: avg_entry 는 트랜치별 저장 entry_price = 거래소 평균단가)
# ---------------------------------------------------------------------------

class TestPyramidEntryPriceIsExchangeAvg:
    def test_filled_tranche_entry_price_uses_exchange_avg_price(
            self, monkeypatch):
        bar_idx = _bar_idx_for(_BASE_TS)
        # 거래소 평균단가 101.0 — 트랜치 체결 시 이 값이 entry_price 로 저장돼야 한다.
        fake = FakeExchange(
            equity=10_000.0,
            position=_ex_position(side="Buy", size="0.050", avg="101.0"),
            open_orders=[],
        )
        _patch_session(monkeypatch, fake)
        conn = _conn()
        adapter = _make_adapter(conn, fake)
        _seed_local_tranche(conn=conn, adapter=adapter, tranche_index=0,
                            side="long", entry=100.0, qty=0.03)
        # pending 의 limit_price(102.0) 와 거래소 평균단가(101.0)를 다르게 둔다 —
        # 저장값이 limit_price 가 아니라 거래소 avgPrice 임을 구분해 검증한다.
        _seed_pending_tranche(adapter, bar_idx, tranche_index=1, side="long")

        adapter.process_bar(_BASE_TS, _bar(close=101.0, high=102.0, low=100.0),
                            new_4h_confirmed=False, cur_4h_ns=None)

        positions = tracking.load_open_positions(conn, "demo")
        by_idx = {p.tranche_index: p for p in positions}
        # 새 트랜치의 entry_price = 거래소 평균단가(101.0), pending limit_price(102.0) 아님.
        assert by_idx[1].entry_price == pytest.approx(101.0)
        # entry 메타도 거래소 평균단가로 갱신된다 (r_multiple 역산 기준).
        assert adapter._get_meta("entry_price", None) == pytest.approx(101.0)


# ---------------------------------------------------------------------------
# 3. 트랜치 추가 시 SL/TP 가 전체(통합) 수량 기준으로 재계산(취소→재발행)
# ---------------------------------------------------------------------------

class TestPyramidSlTpRecalcOnFullSize:
    def test_tranche_fill_reissues_sl_tp_on_total_size(self, monkeypatch):
        bar_idx = _bar_idx_for(_BASE_TS)
        fake = FakeExchange(
            equity=10_000.0,
            position=_ex_position(side="Buy", size="0.050", avg="101.0"),
            open_orders=[],
        )
        _patch_session(monkeypatch, fake)
        conn = _conn()
        adapter = _make_adapter(conn, fake)
        _seed_local_tranche(conn=conn, adapter=adapter, tranche_index=0,
                            side="long", entry=100.0, qty=0.03)
        # 기존 SL/TP 주문이 거래소에 걸려있는 상태 (트랜치0 체결 시 발행됐던 것).
        adapter._set_meta("sl_order_id", "old-sl")
        adapter._set_meta("tp_order_id", "old-tp")
        _seed_pending_tranche(adapter, bar_idx, tranche_index=1, side="long",
                              sl=92.0, tp1=112.0)

        adapter.process_bar(_BASE_TS, _bar(close=101.0, high=102.0, low=100.0),
                            new_4h_confirmed=False, cur_4h_ns=None)

        # 기존 SL/TP 가 취소됐다 (수량을 못 바꾸므로 취소 후 재발행).
        cancelled = [kw.get("orderId") for kw in fake.calls_to("cancel_order")]
        assert "old-sl" in cancelled
        assert "old-tp" in cancelled

        # 재발행된 SL(stop-market) = 통합 총수량(0.05) 기준.
        sl = [o for o in fake.placed_orders
              if o.get("orderType") == "Market" and o.get("triggerPrice")
              and o.get("reduceOnly")]
        assert len(sl) >= 1
        assert sl[-1]["qty"] == f"{0.05:.3f}"          # 전체 수량.
        assert sl[-1]["side"] == "Sell"                # long 보호 → Sell.

        # 재발행된 TP1(reduce-limit) = 통합 총수량의 1/3.
        tp = [o for o in fake.placed_orders
              if o.get("orderType") == "Limit" and o.get("reduceOnly")]
        assert len(tp) >= 1
        assert tp[-1]["qty"] == f"{0.05 / 3.0:.3f}"


# ---------------------------------------------------------------------------
# 4. 같은 방향 3트랜치 보유 시 더 이상 진입하지 않음 (current_tranche >= 3 차단)
# ---------------------------------------------------------------------------

class TestPyramidMaxThreeTranches:
    def test_fourth_tranche_entry_blocked_at_three(self, monkeypatch):
        fake = FakeExchange(
            equity=10_000.0,
            position=_ex_position(side="Buy", size="0.090", avg="100.0"),
            open_orders=[],
        )
        _patch_session(monkeypatch, fake)
        conn = _conn()
        adapter = _make_adapter(conn, fake)
        # 같은 방향 3트랜치 적재 (current_tranche == 3).
        for ti in range(3):
            _seed_local_tranche(conn=conn, adapter=adapter, tranche_index=ti,
                                side="long", entry=100.0, qty=0.03)

        # 깊은 수익(can_add_tranche 자체는 통과할 가격)이어도 3 이상이면 차단돼야.
        _force_signal(monkeypatch, side="long", strength=80.0)
        adapter.process_bar(_BASE_TS, _bar(close=130.0, high=131.0, low=129.0),
                            new_4h_confirmed=True, cur_4h_ns=12345)

        # 신규 진입(post-only Limit, non-reduce) 주문이 나가지 않았다.
        entries = [o for o in fake.placed_orders
                   if o.get("orderType") == "Limit" and "reduceOnly" not in o]
        assert entries == []
        assert adapter._get_meta("pending_order", None) is None


# ---------------------------------------------------------------------------
# 5. 다중 트랜치 보유 중 청산 신호 → 전체 트랜치가 reduce-only 로 정리
# ---------------------------------------------------------------------------

class TestPyramidExitAllTranches:
    def test_sl_cross_closes_all_tranches_reduce_only(self, monkeypatch):
        # 통합 long 포지션 + 로컬 트랜치 3개. 봉 저가가 모든 트랜치 SL 을 관통 →
        # 각 트랜치가 시장가 reduce-only 로 청산되고 로컬 장부가 비워진다.
        fake = FakeExchange(
            equity=10_000.0,
            position=_ex_position(side="Buy", size="0.090", avg="100.0",
                                  liq="70.0"),
            open_orders=[],
        )
        _patch_session(monkeypatch, fake)
        conn = _conn()
        adapter = _make_adapter(conn, fake)
        for ti in range(3):
            _seed_local_tranche(conn=conn, adapter=adapter, tranche_index=ti,
                                side="long", entry=100.0, qty=0.03,
                                sl=95.0, tp1=110.0, liq=70.0)

        # 저가 94 < 모든 트랜치 SL(95) → 트랜치마다 ClosePosition(reason='sl').
        adapter.process_bar(_BASE_TS, _bar(close=94.5, high=96.0, low=94.0),
                            new_4h_confirmed=False, cur_4h_ns=None)

        # 모든 reduce 는 시장가 reduce-only Sell(IOC).
        market_reduces = [o for o in fake.placed_orders
                          if o.get("orderType") == "Market"
                          and o.get("reduceOnly")
                          and o.get("timeInForce") == "IOC"]
        # 트랜치 3개 → 최소 3건의 reduce-only 시장가 청산.
        assert len(market_reduces) >= 3
        for o in market_reduces:
            assert o["side"] == "Sell"
            assert o["reduceOnly"] is True
        # 로컬 장부의 모든 트랜치가 정리됐다.
        assert tracking.load_open_positions(conn, "demo") == []
