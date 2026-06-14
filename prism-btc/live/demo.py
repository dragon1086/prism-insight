# live/demo.py — Bybit 데모 실주문 집행 어댑터 (api-demo.bybit.com)
#
# ShadowAdapter 와 **동일 시그니처/동일 결정로직**을 가진다. 다른 건 "체결의 출처"뿐:
#   - shadow: 다음 봉 [low,high] 안에 limit 들면 가상 체결 / 로컬 누산기로 equity 추적
#   - demo:   거래소 post-only 지정가 주문 → 거래소가 진실 (get_wallet_balance/
#             get_positions/get_executions 로 복원). 매 process_bar 시작에 reconcile.
#
# 불변 원칙 (spec §0):
#   - core/engine 결정 로직(generate_signal/check_exit_signal/evaluate_entry/
#     evaluate_exits)과 진입 게이트(4h 확정 + 4h당 1회 하드캡 + 재진입 쿨다운)는
#     shadow.py 와 **완전 동일**하게 재사용한다.
#   - 모든 거래소 호출은 try/except 로 감싸 실패를 흡수 → tracking.log_event 기록.
#     어떤 예외도 process_bar 밖으로 던지지 않는다 (데몬 비중단).
#   - 출금/이체/convert API 절대 호출 금지.
#   - 데이터는 mode='demo' 로 기존 btc_* 테이블에 (섀도우와 완전 독립 추적).
#
# pybit 접속: HTTP(demo=True, ...)  ★ testnet=True 아님 — api-demo.bybit.com.
from __future__ import annotations

import os
import time
import sqlite3
from typing import Optional, Any

import pandas as pd

# 결정 로직/상수는 shadow 와 동일 소스에서 재사용 (집행 의미론 미러).
from backtest.engine import (
    ENTRY_ORDER_EXPIRY_BARS,
    TRAILING_TF,
    BE_TRAIL_ACTIVATE_R,
    LIQ_MONITOR_FRAC,
    FUNDING_INTERVAL_BARS,
    _build_snapshot_at,
    _get_tf_slice,
)
from engine.indicators import atr as calc_atr
from engine.signal import generate_signal, check_exit_signal, Signal

from core.exits import PositionView, BarView, ExitContext, evaluate_exits
from core.entries import EntryInputs, CooldownState, evaluate_entry
from core.actions import (
    ForceReduce,
    UpdateStop,
    ClosePosition,
    BookPartial,
    ActivateBETrail,
    OpenIntent,
)

from live import tracking

# shadow 와 동일 위험/쿨다운/게이트 상수를 그대로 재사용 (바이트 불변 — import 만).
from live.shadow import (
    INITIAL_EQUITY,
    SHADOW_BASE_RISK,
    SHADOW_REDUCED_RISK,
    SHADOW_DD_THRESHOLD,
    bar_index_for,
    _resolve_funding_rate,
)
from backtest.engine import REENTRY_COOLDOWN_BARS, SL_REENTRY_COOLDOWN_BARS
from engine.sizing import TRANCHE_FRACS
import engine.sizing as _sizing
from core.risk import compute_operating_risk

# 거래소 상수.
_CATEGORY = "linear"
_SYMBOL = "BTCUSDT"
_POSITION_IDX = 0  # 단방향 모드.
_RETRY_SLEEP_SEC = 0.5  # 주문 헬퍼 재시도 1회 사이 대기.


# ---------------------------------------------------------------------------
# Bybit 데모 세션 — 키 없으면 None (호출측이 graceful 스킵).
# ---------------------------------------------------------------------------

def _make_session():
    """HTTP(demo=True, ...) 세션을 만든다. 키/패키지 없으면 None 반환 (예외 흡수)."""
    key = os.environ.get("BYBIT_DEMO_API_KEY")
    secret = os.environ.get("BYBIT_DEMO_API_SECRET")
    if not key or not secret:
        # LaunchAgent(zsh -lc) 환경엔 env 가 없다 → 저장소 루트 .env 를 직접 로드.
        try:
            from dotenv import load_dotenv
            from pathlib import Path
            load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")
            key = os.environ.get("BYBIT_DEMO_API_KEY")
            secret = os.environ.get("BYBIT_DEMO_API_SECRET")
        except Exception:  # noqa: BLE001 — dotenv 없거나 .env 없으면 아래서 스킵
            pass
    if not key or not secret:
        return None, "BYBIT_DEMO_API_KEY/SECRET 미설정"
    try:
        from pybit.unified_trading import HTTP
        sess = HTTP(demo=True, api_key=key, api_secret=secret)  # ★ demo=True
        return sess, None
    except Exception as exc:  # noqa: BLE001 — 패키지/네트워크 실패 흡수
        return None, f"pybit HTTP(demo) init 실패: {exc}"


def _ok(resp: Any) -> bool:
    """Bybit 응답이 성공(retCode==0)인지 방어적으로 판정."""
    try:
        return isinstance(resp, dict) and int(resp.get("retCode", -1)) == 0
    except Exception:  # noqa: BLE001
        return False


def _result_list(resp: Any) -> list:
    """resp["result"]["list"] 를 안전하게 꺼낸다 (없으면 빈 리스트)."""
    try:
        lst = resp.get("result", {}).get("list", [])
        return lst if isinstance(lst, list) else []
    except Exception:  # noqa: BLE001
        return []


def _f(v: Any, default: float = 0.0) -> float:
    """문자열/None 을 방어적으로 float 로."""
    try:
        if v is None or v == "":
            return default
        return float(v)
    except Exception:  # noqa: BLE001
        return default


class DemoAdapter:
    """Bybit 데모 거래소에 실주문을 집행한다 (거래소가 진실).

    ShadowAdapter 와 동일 시그니처/동일 결정로직. 매 process_bar 시작에 거래소
    상태를 동기화(reconcile)해 btc_positions(demo)/btc_equity_curve(demo) 를 갱신하고,
    종결 트레이드는 거래소 체결내역(get_executions) 기준으로 기록한다.
    """

    def __init__(self, root_conn: sqlite3.Connection,
                 tf_data: dict[str, pd.DataFrame],
                 funding_times: list[int], funding_rates: list[float],
                 mode: str = "demo"):
        self.conn = root_conn
        self.tf_data = tf_data
        self.funding_times = funding_times
        self.funding_rates = funding_rates
        self.mode = mode
        self.sess, self._sess_err = _make_session()

    # --- meta 헬퍼 (shadow 미러) ---
    def _get_meta(self, key, default=None):
        v = tracking.get_meta(self.conn, key, self.mode)
        return default if v is None else v

    def _set_meta(self, key, value):
        tracking.set_meta(self.conn, key, value, self.mode)

    # ------------------------------------------------------------------
    # 거래소 호출 헬퍼 — 모두 실패 흡수 + 재시도 1회. 예외 절대 전파 안 함.
    # ------------------------------------------------------------------

    def _call(self, fn_name: str, **kwargs) -> Optional[dict]:
        """sess.<fn_name>(**kwargs) 를 재시도 1회로 호출. 성공 시 응답, 실패 시 None."""
        if self.sess is None:
            return None
        fn = getattr(self.sess, fn_name, None)
        if fn is None:
            tracking.log_event(self.conn, "error", f"pybit 메서드 없음: {fn_name}",
                               level="error", mode=self.mode)
            return None
        last_exc = None
        for attempt in range(2):  # 최초 + 재시도 1회.
            try:
                resp = fn(**kwargs)
                if _ok(resp):
                    return resp
                last_exc = f"retCode={resp.get('retCode') if isinstance(resp, dict) else '?'} " \
                           f"retMsg={resp.get('retMsg') if isinstance(resp, dict) else resp}"
            except Exception as exc:  # noqa: BLE001 — 모든 거래소 실패 흡수
                last_exc = str(exc)
            if attempt == 0:
                time.sleep(_RETRY_SLEEP_SEC)
        tracking.log_event(self.conn, "error",
                           f"{fn_name} 실패: {last_exc}", level="error", mode=self.mode)
        return None

    def _set_leverage(self, leverage: float) -> None:
        # set_leverage 는 이미 같은 값이면 retCode 110043 등으로 실패할 수 있다 — 흡수.
        self._call("set_leverage", category=_CATEGORY, symbol=_SYMBOL,
                   buyLeverage=str(leverage), sellLeverage=str(leverage))

    def _place_limit_postonly(self, side: str, qty: float, price: float) -> Optional[str]:
        """진입 = post-only 지정가 (maker). orderId 반환 (실패 None)."""
        resp = self._call(
            "place_order", category=_CATEGORY, symbol=_SYMBOL,
            side="Buy" if side == "long" else "Sell",
            orderType="Limit", qty=_qstr(qty), price=_pstr(price),
            timeInForce="PostOnly", positionIdx=_POSITION_IDX,
        )
        oid = _order_id(resp)
        if oid:
            tracking.log_event(self.conn, "order",
                f"진입 post-only {side} qty={qty} @ {price} id={oid}", mode=self.mode)
        return oid

    def _place_stop_market(self, side: str, qty: float, trigger: float) -> Optional[str]:
        """SL = 네이티브 stop-market reduce-only. side 는 포지션 방향(반대로 청산)."""
        close_side = "Sell" if side == "long" else "Buy"
        # long SL 은 가격 하락 시 트리거(triggerDirection=2), short SL 은 상승(1).
        trigger_dir = 2 if side == "long" else 1
        resp = self._call(
            "place_order", category=_CATEGORY, symbol=_SYMBOL,
            side=close_side, orderType="Market", qty=_qstr(qty),
            triggerPrice=_pstr(trigger), triggerDirection=trigger_dir,
            triggerBy="LastPrice", reduceOnly=True,
            timeInForce="GTC", positionIdx=_POSITION_IDX,
        )
        oid = _order_id(resp)
        if oid:
            tracking.log_event(self.conn, "order",
                f"SL stop-market {side} qty={qty} trig={trigger} id={oid}", mode=self.mode)
        return oid

    def _place_reduce_limit(self, side: str, qty: float, price: float) -> Optional[str]:
        """TP1 = reduce-only 지정가 (maker)."""
        close_side = "Sell" if side == "long" else "Buy"
        resp = self._call(
            "place_order", category=_CATEGORY, symbol=_SYMBOL,
            side=close_side, orderType="Limit", qty=_qstr(qty), price=_pstr(price),
            timeInForce="PostOnly", reduceOnly=True, positionIdx=_POSITION_IDX,
        )
        oid = _order_id(resp)
        if oid:
            tracking.log_event(self.conn, "order",
                f"TP1 reduce-limit {side} qty={qty} @ {price} id={oid}", mode=self.mode)
        return oid

    def _market_reduce(self, side: str, qty: float, reason: str) -> Optional[str]:
        """신호청산/ForceReduce = 시장가 reduce-only."""
        if qty <= 0:
            return None
        close_side = "Sell" if side == "long" else "Buy"
        resp = self._call(
            "place_order", category=_CATEGORY, symbol=_SYMBOL,
            side=close_side, orderType="Market", qty=_qstr(qty),
            reduceOnly=True, timeInForce="IOC", positionIdx=_POSITION_IDX,
        )
        oid = _order_id(resp)
        if oid:
            tracking.log_event(self.conn, "order",
                f"market reduce {side} qty={qty} reason={reason} id={oid}", mode=self.mode)
        return oid

    def _amend_stop(self, order_id: str, trigger: float) -> None:
        """트레일 = 기존 stop 주문 triggerPrice amend."""
        if not order_id:
            return
        resp = self._call(
            "amend_order", category=_CATEGORY, symbol=_SYMBOL,
            orderId=order_id, triggerPrice=_pstr(trigger),
        )
        if resp is not None:
            tracking.log_event(self.conn, "order",
                f"stop amend id={order_id} trig={trigger}", mode=self.mode)

    def _cancel(self, order_id: str) -> None:
        if not order_id:
            return
        self._call("cancel_order", category=_CATEGORY, symbol=_SYMBOL, orderId=order_id)

    # ------------------------------------------------------------------
    # reconcile — 거래소가 진실. equity/포지션을 btc_* 테이블에 반영.
    # ------------------------------------------------------------------

    def _sync_state(self, bar_time_str: str) -> dict:
        """거래소에서 잔고/포지션/미체결을 가져와 로컬에 반영. 동기화 스냅샷 반환.

        반환: {"equity", "position"(dict|None), "open_orders"(list)}.
        실패 시 부분 정보로 graceful degrade (None/빈 리스트).
        """
        snap: dict = {"equity": None, "position": None, "open_orders": []}

        # --- 잔고 ---
        wb = self._call("get_wallet_balance", accountType="UNIFIED")
        rows = _result_list(wb)
        if rows:
            equity = _f(rows[0].get("totalEquity"))
            if equity > 0:
                snap["equity"] = equity
                tracking.record_equity(self.conn, equity, self.mode, bar_time_str)

        # --- 포지션 (단일 BTCUSDT) ---
        pr = self._call("get_positions", category=_CATEGORY, symbol=_SYMBOL)
        for p in _result_list(pr):
            sz = _f(p.get("size"))
            if sz > 0:
                snap["position"] = {
                    "side": "long" if p.get("side") == "Buy" else "short",
                    "qty": sz,
                    "entry_price": _f(p.get("avgPrice")),
                    "leverage": _f(p.get("leverage"), 1.0),
                    "liq_price": _f(p.get("liqPrice")),
                    "unrealised_pnl": _f(p.get("unrealisedPnl")),
                }
            break  # 단방향·단일 심볼 → 첫 행만.

        # --- 미체결 주문 ---
        oo = self._call("get_open_orders", category=_CATEGORY, symbol=_SYMBOL)
        for o in _result_list(oo):
            snap["open_orders"].append({
                "order_id": o.get("orderId", ""),
                "side": o.get("side", ""),
                "reduce_only": bool(o.get("reduceOnly", False)),
                "order_type": o.get("orderType", ""),
                "qty": _f(o.get("qty")),
                "price": _f(o.get("price")),
                "trigger_price": _f(o.get("triggerPrice")),
                "stop_order_type": o.get("stopOrderType", ""),
            })
        return snap

    def _record_closed_trades(self, bar_time_str: str) -> None:
        """거래소 체결내역(get_executions) 기준으로 종결 트레이드를 btc_trading_history 에 기록.

        last_exec_ns(meta) 이후의 reduce-only(=청산) 체결만 종결로 본다. r_multiple 은
        net_pnl/initial_risk 로 역산 (initial_risk 는 진입 시 btc_meta 에 저장됨).
        체결 1건당 1행으로 보수적으로 기록 — 부분 청산이 여러 체결로 쪼개질 수 있으나
        거래소가 진실이므로 closedPnl 합산은 reconcile 후속 작업으로 둔다.
        """
        ex = self._call("get_executions", category=_CATEGORY, symbol=_SYMBOL, limit=50)
        rows = _result_list(ex)
        if not rows:
            return
        last_seen = int(self._get_meta("last_exec_ns", 0))
        max_ns = last_seen
        trade_id_counter = int(self._get_meta("trade_id_counter", 0))
        initial_risk = _f(self._get_meta("entry_initial_risk", 0.0))
        new_trades = []
        for r in rows:
            ts_ns = int(_f(r.get("execTime"), 0))
            if ts_ns <= last_seen:
                continue
            max_ns = max(max_ns, ts_ns)
            # 청산(reduce-only) 체결만 종결 트레이드로 기록.
            closed_pnl = _f(r.get("closedSize"))
            is_close = bool(r.get("closedSize")) and closed_pnl > 0
            if not is_close:
                continue
            exec_qty = _f(r.get("execQty"))
            exec_price = _f(r.get("execPrice"))
            fee = _f(r.get("execFee"))
            # execSide 가 Sell 이면 long 청산, Buy 면 short 청산.
            pos_side = "long" if r.get("side") == "Sell" else "short"
            net_pnl = _f(r.get("execPnl")) or _f(r.get("closedPnl"))
            r_mult = round(net_pnl / initial_risk, 3) if initial_risk > 0 else 0.0
            trade = tracking.TradeRow(
                trade_id=trade_id_counter,
                side=pos_side,
                entry_time=str(self._get_meta("entry_time", "")),
                entry_price=_f(self._get_meta("entry_price", 0.0)),
                exit_time=bar_time_str,
                exit_price=exec_price,
                qty=exec_qty,
                leverage=_f(self._get_meta("entry_leverage", 1.0)),
                sl_price=_f(self._get_meta("entry_sl_price", 0.0)),
                exit_reason="exchange_fill",
                r_multiple=r_mult,
                fee_paid=round(fee, 6),
                funding_paid=0.0,
                tranche_index=int(self._get_meta("entry_tranche_index", 0)),
                liq_price=_f(self._get_meta("entry_liq_price", 0.0)),
                net_pnl=round(net_pnl, 4),
                gross_pnl=round(net_pnl + fee, 4),
                gross_r_multiple=r_mult,
                num_legs=1,
                mode=self.mode,
            )
            new_trades.append(trade)
            trade_id_counter += 1
        for t in new_trades:
            tracking.record_trade(self.conn, t)
        if new_trades:
            self._set_meta("trade_id_counter", trade_id_counter)
        if max_ns > last_seen:
            self._set_meta("last_exec_ns", max_ns)

    # ------------------------------------------------------------------
    # process_bar — shadow.process_bar 의 결정 흐름 미러, 집행만 거래소로.
    # ------------------------------------------------------------------

    def process_bar(self, bar_time: pd.Timestamp, bar: pd.Series,
                    new_4h_confirmed: bool, cur_4h_ns: Optional[int]) -> None:
        """단일 확정 30m 봉 처리. 어떤 예외도 밖으로 던지지 않는다 (데몬 비중단)."""
        try:
            self._process_bar_inner(bar_time, bar, new_4h_confirmed, cur_4h_ns)
        except Exception as exc:  # noqa: BLE001 — 데몬 비중단 보장.
            try:
                tracking.log_event(self.conn, "error",
                    f"demo process_bar 예외 흡수: {exc}", level="error", mode=self.mode)
            except Exception:  # noqa: BLE001
                pass

    def _process_bar_inner(self, bar_time: pd.Timestamp, bar: pd.Series,
                           new_4h_confirmed: bool, cur_4h_ns: Optional[int]) -> None:
        mode = self.mode
        conn = self.conn
        bar_close = float(bar["close"])
        bar_high = float(bar["high"])
        bar_low = float(bar["low"])
        bar_time_str = str(bar_time)
        bar_idx = bar_index_for(int(bar_time.value // 1_000_000))

        # 키 없으면 즉시 에러 이벤트 + 스킵 (섀도우 영향 0).
        if self.sess is None:
            tracking.log_event(conn, "error",
                f"demo 세션 없음 — 스킵: {self._sess_err}", level="error", mode=mode)
            return

        # --- 1. reconcile: 거래소가 진실 → equity/포지션/미체결 동기화 ---
        snap = self._sync_state(bar_time_str)
        equity = snap["equity"]
        if equity is None:
            equity = tracking.latest_equity(conn, mode)
        if equity is None:
            equity = INITIAL_EQUITY
        ex_pos = snap["position"]          # 거래소 포지션 (dict|None)
        open_orders = snap["open_orders"]  # 미체결 주문

        # 거래소 체결내역 기준 종결 트레이드 기록 (reduce-only 청산).
        self._record_closed_trades(bar_time_str)

        # 크로스-바 트래커 복원 (shadow 미러).
        last_close_bar = self._get_meta("last_close_bar", {"long": -10_000, "short": -10_000})
        last_close_was_sl = self._get_meta("last_close_was_sl", {"long": False, "short": False})
        last_new_entry_eval_4h_ns = self._get_meta("last_new_entry_eval_4h_ns", None)
        pending = self._get_meta("pending_order", None)
        # 진입 동반 주문(SL/TP) orderId 영속.
        sl_order_id = self._get_meta("sl_order_id", None)
        tp_order_id = self._get_meta("tp_order_id", None)

        # btc_positions(demo) 를 거래소 포지션으로 미러 (열린 포지션 단일 가정).
        local_positions = tracking.load_open_positions(conn, mode)

        # --- 2. pending 진입 주문 체결 여부 확인 (포지션 출현/증가 또는 만료) ---
        # 핵심: Bybit 데모는 같은 심볼을 단일 통합 포지션(평균단가·누적 size)으로 합산한다.
        # 따라서 피라미딩 트랜치 체결은 "거래소 position size 증가분"으로 감지하고, 트랜치
        # 자체는 로컬 장부(btc_positions[demo])에 별도 PositionRow 로 누적 보존한다.
        # 체결 후 SL/TP 는 전체 포지션(평균단가·총수량) 기준으로 재계산해 amend/재발행한다.
        if pending is not None:
            pend_oid = pending.get("order_id")
            still_open = any(o["order_id"] == pend_oid for o in open_orders)
            bars_elapsed = bar_idx - int(pending["bar_idx"])
            tranche_idx = int(pending["tranche_index"])
            # 체결 판정: tranche 0 은 포지션 출현, 피라미딩(>=1)은 size 가 직전 로컬 합계보다
            # 증가. 어느 쪽이든 거래소에 미체결 진입주문이 사라졌어야 한다.
            prev_local_qty = sum(p.qty for p in local_positions
                                 if p.side == pending["side"])
            filled = False
            if ex_pos is not None and not still_open:
                if tranche_idx == 0:
                    filled = ex_pos["side"] == pending["side"]
                else:
                    # size 증가분으로 체결 확인 (부동소수 여유 1e-9).
                    filled = (ex_pos["side"] == pending["side"]
                              and ex_pos["qty"] > prev_local_qty + 1e-9)
            if filled:
                side = ex_pos["side"]
                total_qty = ex_pos["qty"]                 # 거래소 누적 총수량.
                avg_entry = ex_pos["entry_price"]          # 거래소 평균단가.
                # 이번 트랜치의 논리적 수량 = 총수량 - 직전 로컬 합계 (피라미딩),
                # tranche 0 이면 전체.
                tranche_qty = (total_qty if tranche_idx == 0
                               else max(total_qty - prev_local_qty, 0.0))
                # 이번 트랜치의 SL/TP (sizing 결과 — 트랜치별 보존).
                sl_p = float(pending["sizing_sl_price"])
                tp1_p = float(pending["sizing_tp1_price"])
                self._set_leverage(float(pending["sizing_leverage"]))

                # 이번 트랜치를 로컬 장부에 추가 (덮어쓰지 않고 누적 — 트랜치별 보존).
                new_pos = tracking.PositionRow(
                    side=side, entry_price=avg_entry, qty=tranche_qty,
                    leverage=ex_pos["leverage"], sl_price=sl_p,
                    tp1_price=tp1_p, tp2_price=float(pending["sizing_tp2_price"]),
                    tp3_price=float(pending["sizing_tp3_price"]),
                    liq_price=ex_pos["liq_price"] or float(pending["sizing_liq_price"]),
                    entry_time=bar_time_str, tranche_index=tranche_idx,
                    entry_bar_idx=bar_idx, initial_risk=float(pending["initial_risk"]),
                    initial_qty=tranche_qty, mode=mode,
                )
                tracking.save_position(conn, new_pos)
                local_positions = local_positions + [new_pos]

                # 전체 포지션 기준 SL/TP 재계산·재발행 (통합 포지션이라 총수량·트리거 갱신).
                #  - SL: 최신 트랜치의 sl_price 를 전체 총수량에 대해 stop-market 으로 건다.
                #    (피라미딩은 직전 트랜치가 수익 중일 때만 추가되므로 최신 SL 이 더 타이트.)
                #  - TP1: 전체 총수량의 1/3 을 최신 트랜치 tp1 가격에 reduce-limit 으로 건다.
                same_side_local = [p for p in local_positions if p.side == side]
                if sl_order_id:
                    self._amend_stop(sl_order_id, sl_p)
                    # amend 는 수량을 못 바꾼다 → 취소 후 총수량으로 재발행.
                    self._cancel(sl_order_id)
                    sl_order_id = None
                sl_order_id = self._place_stop_market(side, total_qty, sl_p)
                if tp_order_id:
                    self._cancel(tp_order_id)
                tp_qty = total_qty / 3.0
                tp_order_id = self._place_reduce_limit(side, tp_qty, tp1_p)

                # 진입 메타(r_multiple 역산용)는 최신 트랜치 기준으로 갱신.
                self._set_meta("entry_initial_risk", float(pending["initial_risk"]))
                self._set_meta("entry_time", bar_time_str)
                self._set_meta("entry_price", avg_entry)
                self._set_meta("entry_leverage", ex_pos["leverage"])
                self._set_meta("entry_sl_price", sl_p)
                self._set_meta("entry_liq_price", new_pos.liq_price)
                self._set_meta("entry_tranche_index", tranche_idx)
                pending = None
                tracking.log_event(conn, "fill",
                    f"{side} tranche {tranche_idx} filled @ {avg_entry:.2f} "
                    f"tranche_qty={tranche_qty:.6f} total={total_qty:.6f} "
                    f"(n_tranche={len(same_side_local)})",
                    mode=mode, ts=bar_time_str)
            elif bars_elapsed >= ENTRY_ORDER_EXPIRY_BARS:
                self._cancel(pend_oid)
                pending = None
                tracking.log_event(conn, "expire", "pending entry expired (cancel)",
                                   mode=mode, ts=bar_time_str)

        # --- 3. 보유 포지션 exits 평가/집행 (core evaluate_exits, Action 순서 동일) ---
        # 섀도우는 트랜치별 PositionRow 마다 evaluate_exits 를 돌린다 → 데모도 동일.
        # 다만 거래소는 단일 통합 포지션이라 집행은 "트랜치 수량을 통합 포지션에서
        # reduce-only 로 차감"하고, SL/TP 는 남은 총수량 기준으로 재계산해 amend 한다.
        if ex_pos is not None and pending is None and local_positions:
            funding_due = bar_idx % FUNDING_INTERVAL_BARS == 0
            sign_aware = bool(self.funding_times)
            funding_rate = _resolve_funding_rate(
                funding_due, sign_aware, self.funding_times, self.funding_rates, bar_time
            )
            bar_view = BarView(idx=bar_idx, high=bar_high, low=bar_low, close=bar_close)
            # 트레일 MA 는 트랜치 공통(같은 봉) — 한 번만 계산.
            trailing_ma_common: Optional[float] = None
            tf_trail = _get_tf_slice(self.tf_data, bar_time, TRAILING_TF)
            if len(tf_trail) >= 10:
                _ma = tf_trail["close"].rolling(10).mean().iloc[-1]
                if not pd.isna(_ma):
                    trailing_ma_common = float(_ma)

            stop_triggers: list[float] = []     # 살아남은 트랜치들의 갱신 SL → 통합 stop 재계산.
            remaining: list[tracking.PositionRow] = []
            # 통합 stop 은 실제 변동(트레일/감축/청산)이 있을 때만 재발행한다 — 조용한 봉
            # (체결 직후 포함)엔 기존 보호주문을 건드리지 않아 불필요한 취소/재발행을 막는다.
            stop_dirty = False
            for pos in list(local_positions):
                ctx_pos = ExitContext(
                    funding_due=funding_due, funding_rate=funding_rate,
                    funding_sign_aware=sign_aware,
                    trailing_ma=(trailing_ma_common if pos.trailing_active else None),
                    be_trail_activate_r=BE_TRAIL_ACTIVATE_R, liq_monitor_frac=LIQ_MONITOR_FRAC,
                )
                pos_view = PositionView(
                    side=pos.side, entry_price=pos.entry_price, qty=pos.qty,
                    sl_price=pos.sl_price, tp1_price=pos.tp1_price, liq_price=pos.liq_price,
                    trailing_active=pos.trailing_active, be_stop_set=pos.be_stop_set,
                    tp1_hit=pos.tp1_hit, liq_breach_flagged=pos.liq_breach_flagged,
                )
                actions = evaluate_exits(pos_view, bar_view, ctx_pos)

                closed = False
                for act in actions:
                    if isinstance(act, ForceReduce):
                        # 청산 임박 방어 — 해당 트랜치 수량을 시장가 reduce-only 로 차감.
                        self._market_reduce(pos.side, pos.qty * act.fraction, "force_reduce")
                        pos.qty = max(pos.qty * (1.0 - act.fraction), 0.0)
                        pos.liq_breach_flagged = True
                        pos.had_forced_reduce = True
                        stop_dirty = True
                    elif isinstance(act, UpdateStop):
                        # 트레일/BE — 트랜치 SL 갱신. 통합 stop 은 아래서 재계산.
                        pos.sl_price = act.new_stop
                        stop_dirty = True
                    elif isinstance(act, ClosePosition):
                        # SL/신호 청산 — 이 트랜치 수량만 통합 포지션에서 차감.
                        self._market_reduce(pos.side, pos.qty, act.reason)
                        last_close_bar[pos.side] = bar_idx
                        last_close_was_sl[pos.side] = act.reason in ("sl",)
                        if pos.id is not None:
                            tracking.remove_position(conn, pos.id)
                        closed = True
                        stop_dirty = True
                        break
                    elif isinstance(act, BookPartial):
                        # TP1 — 통합 reduce-limit 이 거래소에서 체결한다. 플래그만.
                        pos.tp1_hit = True
                    elif isinstance(act, ActivateBETrail):
                        pos.be_stop_set = True
                        pos.trailing_active = True
                    # ChargeFunding/ClearBreachFlag 는 거래소가 회계하므로 로컬 무시.

                if not closed:
                    tracking.save_position(conn, pos)
                    remaining.append(pos)
                    stop_triggers.append(pos.sl_price)

            # 통합 stop 재계산: 남은 총수량 / 가장 타이트한 SL 트리거로 stop-market amend·재발행.
            remaining_qty = sum(p.qty for p in remaining)
            if remaining_qty <= 0 or not remaining:
                # 모든 트랜치 청산됨 → 잔여 보호주문 정리.
                if sl_order_id:
                    self._cancel(sl_order_id)
                    sl_order_id = None
                if tp_order_id:
                    self._cancel(tp_order_id)
                    tp_order_id = None
            elif stop_dirty:
                # 트레일/감축/부분청산으로 총수량·트리거가 바뀐 경우만 통합 stop 재발행.
                side0 = remaining[0].side
                # long: 가장 높은 SL 이 타이트, short: 가장 낮은 SL 이 타이트.
                tightest = (max(stop_triggers) if side0 == "long"
                            else min(stop_triggers))
                if sl_order_id:
                    self._cancel(sl_order_id)
                    sl_order_id = None
                sl_order_id = self._place_stop_market(side0, remaining_qty, tightest)
            local_positions = remaining
        elif ex_pos is None and local_positions:
            # 거래소엔 포지션 없는데 로컬에 남아있음 → 거래소 체결(SL/TP)로 닫힌 것.
            for pos in local_positions:
                last_close_bar[pos.side] = bar_idx
                if pos.id is not None:
                    tracking.remove_position(conn, pos.id)
            if sl_order_id:
                self._cancel(sl_order_id)
            if tp_order_id:
                self._cancel(tp_order_id)
            sl_order_id = None
            tp_order_id = None
            tracking.log_event(conn, "fill",
                "거래소 포지션 종료 감지 (SL/TP 체결) — 로컬 정리", mode=mode, ts=bar_time_str)

        # --- 4. 신규 진입 평가 (4h 하드캡 + 쿨다운 + 피라미딩, shadow 동일 게이트) ---
        # 데모는 거래소가 단일 통합 포지션(평균단가)으로 합산하지만, 트랜치는 로컬
        # 장부(btc_positions[demo])로 관리한다. current_tranche = 같은 방향 로컬
        # 트랜치 수 → 섀도우와 동일 (len(same_side)). 거래소엔 누적 size 만 맞춘다.
        has_position = ex_pos is not None
        if pending is None:
            snapshot = _build_snapshot_at(self.tf_data, bar_time)
            if snapshot is not None:
                sig = generate_signal(snapshot) if new_4h_confirmed else Signal(
                    side="none", strength=0.0, reason="4h 미확정 — 진입평가 보류"
                )
                if new_4h_confirmed:
                    try:
                        from engine.signal import trend_strength as _ts
                        tracking.log_signal(
                            conn, str(bar_time),
                            score=round(snapshot.alignment_score, 2),
                            ts_4h=(round(_ts(snapshot.tf_states["4h"]), 3)
                                   if "4h" in snapshot.tf_states else None),
                            ts_1d=(round(_ts(snapshot.tf_states["1d"]), 3)
                                   if "1d" in snapshot.tf_states else None),
                            side=sig.side, reason=sig.reason,
                            n_open=len(local_positions), mode=mode)
                    except Exception:  # noqa: BLE001 — 로깅이 매매를 못 막는다
                        pass

                if sig.side != "none":
                    # 같은 방향 로컬 트랜치 수 → current_tranche (섀도우 동일).
                    same_side = [p for p in local_positions if p.side == sig.side]
                    current_tranche = len(same_side)
                    intent: Optional[OpenIntent] = None

                    if current_tranche == 0:
                        # 4h 하드캡 + 재진입 쿨다운 (신규 진입, tranche 0).
                        if cur_4h_ns is not None and cur_4h_ns == last_new_entry_eval_4h_ns:
                            intent = None
                        else:
                            if cur_4h_ns is not None:
                                last_new_entry_eval_4h_ns = cur_4h_ns
                            bars_since_close = bar_idx - int(last_close_bar.get(sig.side, -10_000))
                            cooldown_bars = (
                                SL_REENTRY_COOLDOWN_BARS
                                if last_close_was_sl.get(sig.side, False)
                                else REENTRY_COOLDOWN_BARS
                            )
                            entry_price = bar_close
                            ei = self._entry_inputs(bar_time, sig.side, entry_price)
                            intent = self._evaluate_entry_with_risk(
                                sig, equity, 0, ei,
                                cooldown=CooldownState(
                                    bars_since_close=bars_since_close,
                                    cooldown_bars=cooldown_bars,
                                ),
                            )
                    elif current_tranche < 3:
                        # 피라미딩 추가 트랜치 — evaluate_entry 내부 can_add_tranche 게이트가
                        # 직전 평균단가 대비 수익 중일 때만 통과시킨다 (섀도우 동일).
                        avg_entry = sum(p.entry_price for p in same_side) / len(same_side)
                        entry_price = bar_close
                        ei = self._entry_inputs(bar_time, sig.side, entry_price)
                        intent = self._evaluate_entry_with_risk(
                            sig, equity, current_tranche, ei,
                            avg_entry=avg_entry, current_price=bar_close,
                        )

                    if intent is not None:
                        sz = intent.sizing
                        self._set_leverage(sz.leverage)
                        order_id = self._place_limit_postonly(
                            intent.side, sz.qty, intent.limit_price)
                        if order_id:
                            pending = {
                                "order_id": order_id,
                                "side": intent.side,
                                "limit_price": intent.limit_price,
                                "bar_idx": bar_idx,
                                "sizing_qty": sz.qty,
                                "sizing_leverage": sz.leverage,
                                "sizing_sl_price": sz.sl_price,
                                "sizing_tp1_price": sz.tp1_price,
                                "sizing_tp2_price": sz.tp2_price,
                                "sizing_tp3_price": sz.tp3_price,
                                "sizing_liq_price": sz.liq_price,
                                "initial_risk": intent.initial_risk,
                                "tranche_index": intent.tranche_index,
                            }
                            tracking.log_event(conn, "signal",
                                f"{intent.side} entry intent @ {intent.limit_price:.2f} "
                                f"tranche={intent.tranche_index} "
                                f"risk={intent.initial_risk:.2f} id={order_id}",
                                mode=mode, ts=bar_time_str)

        # --- 5. 메타 영속 + 하트비트 ---
        self._set_meta("last_close_bar", last_close_bar)
        self._set_meta("last_close_was_sl", last_close_was_sl)
        self._set_meta("last_new_entry_eval_4h_ns", last_new_entry_eval_4h_ns)
        self._set_meta("pending_order", pending)
        self._set_meta("sl_order_id", sl_order_id)
        self._set_meta("tp_order_id", tp_order_id)
        tracking.log_event(conn, "heartbeat",
            f"demo tick ok @ {bar_time_str} equity={equity:.2f} "
            f"pos={'yes' if has_position else 'no'} "
            f"tranches={len(local_positions)}", mode=mode, ts=bar_time_str)

    # --- helpers (shadow 미러) ---

    def _entry_inputs(self, bar_time, side, entry_price) -> EntryInputs:
        """1h 슬라이스에서 ATR/swing/MA35 파생 — shadow._entry_inputs 동일."""
        tf_1h_slice = _get_tf_slice(self.tf_data, bar_time, "1h")
        if len(tf_1h_slice) >= 14:
            atr_series = calc_atr(tf_1h_slice, 14)
            atr_1h_val = (float(atr_series.iloc[-1])
                          if not pd.isna(atr_series.iloc[-1]) else entry_price * 0.02)
        else:
            atr_1h_val = entry_price * 0.02
        if len(tf_1h_slice) >= 10:
            if side == "long":
                swing_ref = float(tf_1h_slice["low"].iloc[-10:].min())
            else:
                swing_ref = float(tf_1h_slice["high"].iloc[-10:].max())
        else:
            swing_ref = entry_price * (0.98 if side == "long" else 1.02)
        if len(tf_1h_slice) >= 35:
            ma35_1h = float(tf_1h_slice["close"].rolling(35).mean().iloc[-1])
        else:
            ma35_1h = entry_price
        return EntryInputs(entry_price=entry_price, atr_1h=atr_1h_val,
                           swing_ref=swing_ref, ma35_1h=ma35_1h)

    def _evaluate_entry_with_risk(self, sig, equity, current_tranche, inputs,
                                  *, cooldown=None, avg_entry=None,
                                  current_price=None) -> Optional[OpenIntent]:
        """E4 오버레이 — shadow._evaluate_entry_with_risk 동일 (RISK_PER_TRADE 임시패치)."""
        peak = tracking.peak_equity(self.conn, self.mode) or equity
        op_risk = compute_operating_risk(
            equity, peak,
            base_risk=SHADOW_BASE_RISK,
            dd_threshold=SHADOW_DD_THRESHOLD,
            reduced_risk=SHADOW_REDUCED_RISK,
        )
        orig = _sizing.RISK_PER_TRADE
        _sizing.RISK_PER_TRADE = op_risk
        try:
            return evaluate_entry(
                sig, equity, current_tranche,
                inputs=inputs, cooldown=cooldown,
                avg_entry=avg_entry, current_price=current_price,
            )
        finally:
            _sizing.RISK_PER_TRADE = orig


# ---------------------------------------------------------------------------
# 주문 페이로드 포맷 헬퍼 — Bybit 은 qty/price 를 문자열로 받는다.
# ---------------------------------------------------------------------------

def _qstr(qty: float) -> str:
    """수량 문자열 (BTCUSDT linear 최소단위 0.001 — 3자리 반올림)."""
    return f"{float(qty):.3f}"


def _pstr(price: float) -> str:
    """가격 문자열 (BTCUSDT tick 0.1 — 1자리 반올림)."""
    return f"{float(price):.1f}"


def _order_id(resp: Optional[dict]) -> Optional[str]:
    """place_order 응답에서 orderId 를 방어적으로 추출."""
    if resp is None:
        return None
    try:
        oid = resp.get("result", {}).get("orderId", "")
        return oid or None
    except Exception:  # noqa: BLE001
        return None
