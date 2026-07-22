# live/swing.py — 라운드6 Lane B 스윙 레인 집행기 (mode='swing' 자체 원장)
#
# 결정 로직은 core/swing.py 순수함수 (백테스트 패리티 고정). 이 모듈은 집행만.
#
# 집행 백엔드 2종 (v2):
#   - ExchangeBackend: 스윙 전용 Bybit 데모 키(BYBIT_SWING_API_KEY/SECRET)로
#     실주문. ★ 메인 레인 키와 반드시 다른 계정(별도 지갑)이어야 한다 —
#     같은 계좌를 쓰면 원웨이 넷팅으로 DemoAdapter 의 reconcile 3중 오염:
#     ① _sync_state 가 임의 포지션을 메인 것으로 채택 ② _record_closed_trades
#     가 모든 reduce-only 체결을 메인 트레이드로 기록 ③ 지갑 equity 공유.
#   - VirtualBackend: 키 미설정 시 폴백 — 가상 체결 (v1 과 동일 의미론).
#
# 집행 의미론 (analysis/round6_swing_lane.py 백테스트 미러):
#   - 신호: 4h 확정봉 (30m 틱마다 _get_tf_slice 로 감지 — 메인과 동일 케이던스)
#   - 진입: 시장가 (백테스트 taker 비용 모델과 일치), 진입 직후 네이티브
#     stop-market reduce-only SL 부착. 룰 청산: SL 취소 → 시장가 reduce.
#   - 하드스탑: 가상=30m 봉내 감시 / 실집행=거래소 네이티브 스탑이 진실,
#     틱마다 포지션 소멸 감지로 사후 기록.
#   - 포지션 최대 1개, 피라미딩/부분청산/펀딩 없음. 메인 방향충돌 시 진입금지.
#
# 실행 모드: runner 는 SWING_RUN_MODES(기본 demo 전용) 틱에서만 호출한다 —
# 서버는 shadow(:01)/demo(:02) 크론이 병행이라 양쪽에서 돌리면 단일 커서를
# 선착 틱이 소비해 알림/충돌검사가 어긋난다.
#
# 안전 원칙: process() 밖으로 예외 비전파는 호출측(runner)이 보장, 여기서도
# 알림/신호로그/거래소 호출 실패는 개별 흡수한다.
from __future__ import annotations

import logging
import os
import time
from typing import Optional

import pandas as pd

from backtest.engine import SLIPPAGE_SL, TAKER_FEE, _get_tf_slice
from core.swing import (
    compute_swing_sizing,
    conflicts_with_main,
    detect_cross,
    entry_side,
    rule_exit_due,
    stop_price,
)
from engine.config import SWING_ENABLED, SWING_INITIAL_EQUITY, SWING_MAX_LEVERAGE
from live import tracking
from live.demo import _f, _order_id, _pstr, _qstr, _result_list
from live.shadow import bar_index_for

log = logging.getLogger("live.swing")

MODE = "swing"  # tracking 원장 키 — 메인 mode(shadow/demo/live)와 완전 분리

_CATEGORY = "linear"
_SYMBOL = "BTCUSDT"
_POSITION_IDX = 0
_RETRY_SLEEP_SEC = 0.5


# ---------------------------------------------------------------------------
# 스윙 전용 세션 — 메인(BYBIT_DEMO_*)과 다른 키. 없으면 None (가상 폴백).
# ---------------------------------------------------------------------------

def _make_swing_session():
    key = os.environ.get("BYBIT_SWING_API_KEY")
    secret = os.environ.get("BYBIT_SWING_API_SECRET")
    if not key or not secret:
        try:
            from pathlib import Path

            from dotenv import load_dotenv
            load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")
            key = os.environ.get("BYBIT_SWING_API_KEY")
            secret = os.environ.get("BYBIT_SWING_API_SECRET")
        except Exception:  # noqa: BLE001
            pass
    if not key or not secret:
        return None, "BYBIT_SWING_API_KEY/SECRET 미설정"
    # 안전 가드: 메인 키와 동일하면 실집행 금지 (넷팅 오염 방지).
    if key == os.environ.get("BYBIT_DEMO_API_KEY"):
        return None, "SWING 키가 메인 DEMO 키와 동일 — 별도 계정 필요, 가상 폴백"
    try:
        from pybit.unified_trading import HTTP
        return HTTP(demo=True, api_key=key, api_secret=secret), None
    except Exception as exc:  # noqa: BLE001
        return None, f"pybit HTTP(demo) init 실패: {exc}"


# ---------------------------------------------------------------------------
# 집행 백엔드
# ---------------------------------------------------------------------------

class VirtualBackend:
    """가상 체결 (v1 의미론): 진입/룰청산 = 30m 종가, 스탑 = 봉내 스탑가."""

    name = "virtual"

    def __init__(self, conn):
        self.conn = conn

    def equity(self, fallback: float) -> float:
        return fallback

    def open(self, side: str, qty: float, sl: float,
             hint_price: float) -> Optional[float]:
        return hint_price

    def check_stop(self, pos: tracking.PositionRow,
                   bar: pd.Series) -> Optional[float]:
        if pos.side == "long" and float(bar["low"]) <= pos.sl_price:
            return pos.sl_price
        if pos.side == "short" and float(bar["high"]) >= pos.sl_price:
            return pos.sl_price
        return None

    def close(self, pos: tracking.PositionRow, hint_price: float) -> float:
        return hint_price


class ExchangeBackend:
    """스윙 전용 데모 계정에 실주문. 거래소 지갑이 equity 의 진실."""

    name = "exchange"

    def __init__(self, conn, sess):
        self.conn = conn
        self.sess = sess

    # --- 호출 헬퍼 (demo.DemoAdapter._call 미러 — 재시도 1회, 실패 흡수) ---
    def _call(self, fn_name: str, **kwargs) -> Optional[dict]:
        fn = getattr(self.sess, fn_name, None)
        if fn is None:
            return None
        last_exc = None
        for attempt in range(2):
            try:
                resp = fn(**kwargs)
                if isinstance(resp, dict) and int(resp.get("retCode", -1)) == 0:
                    return resp
                last_exc = (resp.get("retMsg") if isinstance(resp, dict) else resp)
            except Exception as exc:  # noqa: BLE001
                last_exc = str(exc)
            if attempt == 0:
                time.sleep(_RETRY_SLEEP_SEC)
        tracking.log_event(self.conn, "error", f"swing {fn_name} 실패: {last_exc}",
                           level="error", mode=MODE)
        return None

    def equity(self, fallback: float) -> float:
        wb = self._call("get_wallet_balance", accountType="UNIFIED")
        rows = _result_list(wb)
        if rows:
            eq = _f(rows[0].get("totalEquity"))
            if eq > 0:
                return eq
        return fallback

    def _position_size(self) -> Optional[float]:
        """현재 스윙 계정 BTCUSDT 포지션 크기. 조회 실패 시 None (판단 유보)."""
        pr = self._call("get_positions", category=_CATEGORY, symbol=_SYMBOL)
        if pr is None:
            return None
        for p in _result_list(pr):
            return _f(p.get("size"))
        return 0.0

    def open(self, side: str, qty: float, sl: float,
             hint_price: float) -> Optional[float]:
        """시장가 진입 → 체결가 확인 → 네이티브 SL 부착. 실패 시 None (무진입)."""
        self._call("set_leverage", category=_CATEGORY, symbol=_SYMBOL,
                   buyLeverage=str(SWING_MAX_LEVERAGE),
                   sellLeverage=str(SWING_MAX_LEVERAGE))
        resp = self._call(
            "place_order", category=_CATEGORY, symbol=_SYMBOL,
            side="Buy" if side == "long" else "Sell",
            orderType="Market", qty=_qstr(qty),
            timeInForce="IOC", positionIdx=_POSITION_IDX,
        )
        if _order_id(resp) is None:
            return None
        # 체결가 확인 (최대 3회 폴링, 실패 시 힌트가로 기록).
        fill = hint_price
        for _ in range(3):
            pr = self._call("get_positions", category=_CATEGORY, symbol=_SYMBOL)
            for p in _result_list(pr or {}):
                if _f(p.get("size")) > 0:
                    fill = _f(p.get("avgPrice"), hint_price)
                    break
            else:
                time.sleep(_RETRY_SLEEP_SEC)
                continue
            break
        # 네이티브 SL (stop-market reduce-only).
        close_side = "Sell" if side == "long" else "Buy"
        trigger_dir = 2 if side == "long" else 1
        sl_resp = self._call(
            "place_order", category=_CATEGORY, symbol=_SYMBOL,
            side=close_side, orderType="Market", qty=_qstr(qty),
            triggerPrice=_pstr(sl), triggerDirection=trigger_dir,
            triggerBy="LastPrice", reduceOnly=True,
            timeInForce="GTC", positionIdx=_POSITION_IDX,
        )
        sl_oid = _order_id(sl_resp)
        tracking.set_meta(self.conn, "swing_sl_order_id", sl_oid or "", MODE)
        if sl_oid is None:
            tracking.log_event(self.conn, "error",
                               "swing SL 주문 실패 — 소프트 감시로만 보호됨",
                               level="error", mode=MODE)
        tracking.log_event(self.conn, "order",
                           f"swing open {side} market qty={qty:.4f} fill≈{fill:.1f} "
                           f"sl={sl:.1f} sl_oid={sl_oid}", mode=MODE)
        return fill

    def _last_close_exec_price(self) -> Optional[float]:
        ex = self._call("get_executions", category=_CATEGORY, symbol=_SYMBOL,
                        limit=10)
        for r in _result_list(ex or {}):
            if _f(r.get("closedSize")) > 0:
                return _f(r.get("execPrice")) or None
        return None

    def check_stop(self, pos: tracking.PositionRow,
                   bar: pd.Series) -> Optional[float]:
        """거래소 포지션 소멸 = 스탑(또는 외부 청산) 체결. 체결가를 반환."""
        size = self._position_size()
        if size is None or size > 0:
            return None
        price = self._last_close_exec_price() or pos.sl_price
        sl_oid = tracking.get_meta(self.conn, "swing_sl_order_id", MODE)
        if sl_oid:
            self._call("cancel_order", category=_CATEGORY, symbol=_SYMBOL,
                       orderId=sl_oid)
        tracking.set_meta(self.conn, "swing_sl_order_id", "", MODE)
        return price

    def close(self, pos: tracking.PositionRow, hint_price: float) -> float:
        sl_oid = tracking.get_meta(self.conn, "swing_sl_order_id", MODE)
        if sl_oid:
            self._call("cancel_order", category=_CATEGORY, symbol=_SYMBOL,
                       orderId=sl_oid)
        tracking.set_meta(self.conn, "swing_sl_order_id", "", MODE)
        close_side = "Sell" if pos.side == "long" else "Buy"
        self._call(
            "place_order", category=_CATEGORY, symbol=_SYMBOL,
            side=close_side, orderType="Market", qty=_qstr(pos.qty),
            reduceOnly=True, timeInForce="IOC", positionIdx=_POSITION_IDX,
        )
        return self._last_close_exec_price() or hint_price


def _make_backend(conn, main_mode: str):
    """demo/live 틱에서 스윙 키가 있으면 실집행, 아니면 가상 폴백."""
    if main_mode in ("demo", "live"):
        sess, err = _make_swing_session()
        if sess is not None:
            return ExchangeBackend(conn, sess)
        if tracking.get_meta(conn, "swing_exec_fallback_notified", MODE) is None:
            tracking.log_event(conn, "info",
                               f"swing 실집행 불가({err}) — 가상 체결 폴백",
                               mode=MODE)
            tracking.set_meta(conn, "swing_exec_fallback_notified", 1, MODE)
    return VirtualBackend(conn)


# ---------------------------------------------------------------------------
# 텔레그램 알림 — notifier 와 동일한 재사용 패턴, [스윙레인] 태그로 구분.
# ---------------------------------------------------------------------------

def _notify(main_mode: str, msg: str) -> None:
    """메인이 demo/live 로 돌 때만 발송 (shadow 로컬 테스트 스팸 방지). 실패 흡수."""
    if main_mode not in ("demo", "live"):
        return
    try:
        import asyncio

        from live.telegram_reporter import _load_env, _resolve_channel, _send
        _load_env()
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        asyncio.run(_send(token, _resolve_channel(None), msg))
    except Exception as exc:  # noqa: BLE001 — 알림 실패는 매매와 무관
        log.warning("swing notify 실패 (흡수): %s", exc)


def _side_kr(side: str) -> str:
    return "롱 (상승 베팅)" if side == "long" else "숏 (하락 베팅)"


def _log_signal_safe(conn, ts: str, side: str, reason: str, n_open: int) -> None:
    try:
        tracking.log_signal(conn, ts, score=None, ts_4h=None, ts_1d=None,
                            side=side, reason=reason, n_open=n_open, mode=MODE)
    except Exception as exc:  # noqa: BLE001 — 관측 실패는 매매와 무관
        log.warning("swing log_signal 실패 (흡수): %s", exc)


# ---------------------------------------------------------------------------
# 원장 정산 — 백엔드 공통 (체결가만 백엔드가 결정).
# ---------------------------------------------------------------------------

def _close_position(conn, backend, pos: tracking.PositionRow, exit_price: float,
                    fee_rate: float, reason: str, bar_time_str: str,
                    equity: float, trade_counter: int,
                    main_mode: str) -> tuple[float, int]:
    """청산 정산: 트레이드 기록 → 포지션 제거 → equity 갱신 → 알림.

    실집행 백엔드는 지갑 equity 가 진실 — 추정 net 으로 갱신 후 지갑값으로 덮는다.
    """
    sign = 1.0 if pos.side == "long" else -1.0
    gross = sign * pos.qty * (exit_price - pos.entry_price)
    exit_fee = pos.qty * exit_price * fee_rate
    net = gross - pos.entry_fee - exit_fee
    risk = pos.initial_risk if pos.initial_risk > 0 else 1.0
    trade_counter += 1
    tracking.record_trade(conn, tracking.TradeRow(
        trade_id=trade_counter,
        side=pos.side,
        entry_time=pos.entry_time,
        entry_price=pos.entry_price,
        exit_time=bar_time_str,
        exit_price=exit_price,
        qty=pos.qty,
        leverage=pos.leverage,
        sl_price=pos.sl_price,
        exit_reason=reason,
        r_multiple=net / risk,
        fee_paid=pos.entry_fee + exit_fee,
        funding_paid=0.0,
        tranche_index=0,
        liq_price=0.0,
        net_pnl=net,
        gross_pnl=gross,
        gross_r_multiple=gross / risk,
        num_legs=1,
        mode=MODE,
    ))
    if pos.id is not None:
        tracking.remove_position(conn, pos.id)
    equity = backend.equity(fallback=equity + net)
    tracking.record_equity(conn, equity, MODE, bar_time_str)
    tracking.log_event(conn, "trade",
                       f"swing close[{backend.name}] {pos.side} @ {exit_price:.1f} "
                       f"({reason}) net≈{net:+.2f} eq={equity:.2f}", mode=MODE)
    r = net / risk
    outcome = f"✅ 이익 {r:+.1f}배" if r > 0 else f"❌ 손실 {r:+.1f}배"
    why = "손절" if reason == "swing_sl" else "추세이탈 청산"
    exec_tag = "실주문" if backend.name == "exchange" else "가상체결"
    _notify(main_mode,
            f"🌀 [스윙레인·{exec_tag}] 포지션 정리 — {outcome} ({why})\n"
            f"_데모 계정 모의투자입니다_")
    return equity, trade_counter


def _try_entry(conn, backend, bar, bar_time_str: str, s4: pd.DataFrame,
               s1: pd.DataFrame, equity: float,
               main_mode: str) -> Optional[tracking.PositionRow]:
    """4h 확정봉에서 진입 평가. 성공 시 저장된 PositionRow, 아니면 None."""
    if len(s4) < 36 or s1.empty:
        return None
    cur = s4.iloc[-1]
    prev = s4.iloc[-2]
    d1 = s1.iloc[-1]
    needed = [cur.get("ma10"), cur.get("ma35"), cur.get("atr14"),
              prev.get("ma10"), prev.get("ma35"), d1.get("ma10"), d1.get("ma35")]
    if any(v is None or pd.isna(v) for v in needed):
        return None

    cross = detect_cross(float(prev["ma10"]), float(prev["ma35"]),
                         float(cur["ma10"]), float(cur["ma35"]))
    if cross == 0:
        return None

    side = entry_side(cross, float(d1["ma10"]), float(d1["ma35"]),
                      float(cur["close"]), float(cur["ma35"]))
    if side is None:
        _log_signal_safe(conn, bar_time_str, "none",
                         f"swing cross={cross:+d} 기각: 1d 불일치 또는 MA35 역방향", 0)
        return None

    # 메인 레인과 방향 충돌 금지 (같은 runner 프로세스의 메인 mode 포지션 조회).
    try:
        main_sides = [p.side for p in tracking.load_open_positions(conn, main_mode)]
    except Exception:  # noqa: BLE001 — 조회 실패 시 보수적으로 진입 보류
        main_sides = ["__unknown__"]
    if conflicts_with_main(side, main_sides):
        _log_signal_safe(conn, bar_time_str, side,
                         f"swing 기각: 메인 레인 방향 충돌 (main={main_sides})", 1)
        return None

    hint_price = float(bar["close"])
    sizing_equity = backend.equity(fallback=equity)
    sl = stop_price(side, hint_price, float(cur["atr14"]))
    sz = compute_swing_sizing(sizing_equity, hint_price, sl)
    if sz.rejected:
        _log_signal_safe(conn, bar_time_str, side,
                         f"swing 기각: sizing ({sz.reject_reason})", 0)
        return None

    fill = backend.open(side, sz.qty, sl, hint_price)
    if fill is None:
        _log_signal_safe(conn, bar_time_str, side, "swing 기각: 주문 실패", 0)
        return None

    entry_fee = sz.qty * fill * TAKER_FEE
    pos = tracking.PositionRow(
        side=side,
        entry_price=fill,
        qty=sz.qty,
        leverage=sz.leverage,
        sl_price=sl,
        tp1_price=0.0, tp2_price=0.0, tp3_price=0.0,
        liq_price=0.0,
        entry_time=bar_time_str,
        tranche_index=0,
        entry_bar_idx=bar_index_for(int(pd.Timestamp(bar_time_str).value) // 1_000_000),
        initial_risk=sz.risk_amount,
        entry_fee=entry_fee,
        initial_qty=sz.qty,
        mode=MODE,
    )
    tracking.save_position(conn, pos)
    tracking.log_event(conn, "trade",
                       f"swing open[{backend.name}] {side} @ {fill:.1f} "
                       f"qty={sz.qty:.4f} sl={sl:.1f} lev={sz.leverage:.2f}",
                       mode=MODE)
    _log_signal_safe(conn, bar_time_str, side, "swing_entry", 1)
    exec_tag = "실주문" if backend.name == "exchange" else "가상체결"
    _notify(main_mode,
            f"🌀 [스윙레인·{exec_tag}] 새 진입 — {_side_kr(side)}\n"
            f"진입가 {fill:,.0f}달러 · 손절 {sl:,.0f}달러 · "
            f"{sz.leverage:.1f}배율\n_데모 계정 모의투자입니다_")
    return pos


# ---------------------------------------------------------------------------
# 핵심 진입점 — runner.tick 이 SWING_RUN_MODES 틱마다 호출.
# ---------------------------------------------------------------------------

def process(root_conn, tf_data: dict, main_mode: str = "shadow",
            backend=None) -> dict:
    """새 확정 30m 봉들을 스윙 레인 관점에서 처리. {"events": n} 반환.

    자체 메타 커서(mode='swing' 의 last_processed_30m_ns / last_confirmed_4h_ns)
    를 쓰므로 메인 레인 상태와 완전 독립이며 재실행에 멱등이다.
    콜드 스타트(커서 없음)는 마지막 확정봉 1개만 처리한다 (과거 재시뮬 방지).
    backend 미지정 시 자동 선택 (스윙 키 존재+demo/live → 실집행, 아니면 가상).
    """
    result = {"events": 0}
    if not SWING_ENABLED:
        return result
    bars_30m = tf_data.get("30m")
    if bars_30m is None or bars_30m.empty:
        return result
    conn = root_conn
    if backend is None:
        backend = _make_backend(conn, main_mode)

    equity = tracking.latest_equity(conn, MODE)
    if equity is None:
        equity = backend.equity(fallback=SWING_INITIAL_EQUITY)
        tracking.record_equity(conn, equity, MODE)
        tracking.log_event(conn, "info",
                           f"swing lane ledger seeded[{backend.name}]: {equity:.0f}",
                           mode=MODE)

    last_ns = tracking.get_meta(conn, "last_processed_30m_ns", MODE)
    if last_ns is None:
        new_bars = bars_30m.iloc[[-1]]
    else:
        new_bars = bars_30m[bars_30m.index.map(
            lambda t: int(t.value) > int(last_ns))]
    if new_bars.empty:
        return result

    last_4h_ns = tracking.get_meta(conn, "last_confirmed_4h_ns", MODE)
    trade_counter = int(tracking.get_meta(conn, "trade_id_counter", MODE) or 0)
    positions = tracking.load_open_positions(conn, MODE)
    pos = positions[0] if positions else None

    for bar_time, bar in new_bars.iterrows():
        bar_time_str = str(bar_time)

        # --- 1. 하드스탑 감시 (가상=봉내 / 실집행=거래소 포지션 소멸 감지) ---
        if pos is not None:
            stop_fill = backend.check_stop(pos, bar)
            if stop_fill is not None:
                equity, trade_counter = _close_position(
                    conn, backend, pos, stop_fill, TAKER_FEE + SLIPPAGE_SL,
                    "swing_sl", bar_time_str, equity, trade_counter, main_mode)
                pos = None
                result["events"] += 1

        # --- 2. 새 4h 확정 감지 → 룰 청산(보유 시) / 진입 평가(무포지션 시) ---
        # 백테스트 run_lane 미러: 같은 4h 에서 청산과 진입을 겸하지 않는다.
        s4 = _get_tf_slice(tf_data, bar_time, "4h")
        if s4.empty:
            continue
        cur_4h_ns = int(s4.index[-1].value)
        if last_4h_ns is not None and cur_4h_ns == int(last_4h_ns):
            continue
        last_4h_ns = cur_4h_ns

        if pos is not None:
            row = s4.iloc[-1]
            if not pd.isna(row.get("ma35")) and rule_exit_due(
                    pos.side, float(row["close"]), float(row["ma35"])):
                fill = backend.close(pos, float(bar["close"]))
                equity, trade_counter = _close_position(
                    conn, backend, pos, fill, TAKER_FEE,
                    "swing_ma35_exit", bar_time_str, equity, trade_counter,
                    main_mode)
                pos = None
                result["events"] += 1
        else:
            s1 = _get_tf_slice(tf_data, bar_time, "1d")
            pos = _try_entry(conn, backend, bar, bar_time_str, s4, s1,
                             equity, main_mode)
            if pos is not None:
                result["events"] += 1

    tracking.set_meta(conn, "last_processed_30m_ns",
                      int(new_bars.index[-1].value), MODE)
    if last_4h_ns is not None:
        tracking.set_meta(conn, "last_confirmed_4h_ns", int(last_4h_ns), MODE)
    tracking.set_meta(conn, "trade_id_counter", trade_counter, MODE)
    return result
