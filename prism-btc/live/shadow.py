# live/shadow.py — 섀도우 집행 어댑터 (가상 페이퍼 트레이딩)
#
# backtest/engine.py 와 **동일한 집행 의미론**을 가진다:
#   - 동일한 core 결정 함수: core.exits.evaluate_exits / core.entries.evaluate_entry
#   - 동일한 비용 상수: MAKER_FEE / TAKER_FEE / SLIPPAGE_SL / FUNDING_INTERVAL_BARS
#   - 동일한 Action 적용 순서 (ChargeFunding → ForceReduce → ClearBreachFlag →
#     UpdateStop → ClosePosition → BookPartial → ActivateBETrail)
#   - 동일한 post-only 진입 체결 판정 (PendingOrder: 다음 봉 [low,high] 안에 limit)
#   - 동일한 4h 진입평가 하드캡 + 재진입 쿨다운
#   - 동일한 회계 (_book_leg / _close_position 미러)
#
# 백테스트와 다른 점은 "회계 결과의 영속 위치"뿐: BacktestState 대신 루트 DB의
# btc_* 테이블에 모든 상태를 저장해 프로세스 재시작에 안전하다.
#
# bar_idx 정의: 30m 봉 open_time(ms) // 30분 = 절대 글로벌 30m 인덱스.
# 펀딩 경계(bar_idx % 16 == 0)는 Bybit 실제 8h 펀딩(00/08/16 UTC)과 정렬된다.
from __future__ import annotations

import bisect
import sqlite3
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

# 백테스트 어댑터의 레퍼런스 구현에서 상수/헬퍼를 그대로 재사용 (집행 의미론 동일).
from backtest.engine import (
    MAKER_FEE,
    TAKER_FEE,
    SLIPPAGE_SL,
    FUNDING_INTERVAL_BARS,
    FUNDING_RATE,
    ENTRY_ORDER_EXPIRY_BARS,
    TRAILING_TF,
    BE_TRAIL_ACTIVATE_R,
    LIQ_MONITOR_FRAC,
    REENTRY_COOLDOWN_BARS,
    SL_REENTRY_COOLDOWN_BARS,
    _build_snapshot_at,
    _get_tf_slice,
)
from engine.indicators import atr as calc_atr
from engine.signal import generate_signal, check_exit_signal, Signal
import engine.sizing as _sizing

from core.exits import PositionView, BarView, ExitContext, evaluate_exits
from core.entries import EntryInputs, CooldownState, evaluate_entry
from core.actions import (
    ChargeFunding,
    ForceReduce,
    ClearBreachFlag,
    UpdateStop,
    ClosePosition,
    BookPartial,
    ActivateBETrail,
    OpenIntent,
)
from core.risk import compute_operating_risk

from live import tracking
from live.tracking import PositionRow, TradeRow

# 섀도우 위험: 고정 2% — E4 오버레이 비활성 (reduced == base 로 중립화).
# 근거 (2026-06-13 전체 재시뮬, tasks/handoff_btc.md §3-1.5): E4 2%/1% 는
# 고정 2% 대비 CAGR 8.3→6.0 / MDD 7.15→6.1 / PF 2.14→1.85 — 수익 2.3%p 를
# 내고 MDD 1%p 를 사는 손해 보는 보험. 추세전략의 큰 승리가 손실 직후에
# 오는 구조라, DD 트리거가 정확히 회복 트레이드의 사이즈를 반토막낸다.
# compute_operating_risk 배관은 유지 (가변 리스크 재검토 시 값만 변경).
SHADOW_BASE_RISK: float = 0.02
SHADOW_REDUCED_RISK: float = 0.02  # == base → E4 비활성
SHADOW_DD_THRESHOLD: float = 0.05

INITIAL_EQUITY: float = 10_000.0
_30M_MS: int = 30 * 60 * 1000


def bar_index_for(open_time_ms: int) -> int:
    """30m 봉 open_time(ms) → 절대 글로벌 30m 인덱스. 펀딩 경계 정렬용."""
    return int(open_time_ms) // _30M_MS


@dataclass
class PendingOrder:
    """미체결 진입 주문 (backtest.engine.PendingOrder 미러)."""
    side: str
    limit_price: float
    bar_idx: int
    sizing_qty: float
    sizing_leverage: float
    sizing_sl_price: float
    sizing_tp1_price: float
    sizing_tp2_price: float
    sizing_tp3_price: float
    sizing_liq_price: float
    initial_risk: float
    tranche_index: int


# ---------------------------------------------------------------------------
# 회계 헬퍼 — backtest.engine 의 _apply_fee / _book_leg / _close_position 미러.
# BacktestState 대신 (equity, fees, funding) 누산기를 인자로 받는다.
# ---------------------------------------------------------------------------

@dataclass
class _Acc:
    """한 틱 동안의 회계 누산 (equity / 수수료 / 펀딩 / 종결 트레이드 목록)."""
    equity: float
    total_fees: float = 0.0
    total_funding: float = 0.0
    closed_trades: list[TradeRow] = field(default_factory=list)


def _book_leg(pos: PositionRow, close_qty: float, exit_price: float,
              fee_rate: float, acc: _Acc) -> None:
    """부분(또는 잔여) 레그 실현. backtest._book_leg 와 동일 회계."""
    nominal = close_qty * exit_price
    if pos.side == "long":
        gross = (exit_price - pos.entry_price) * close_qty
    else:
        gross = (pos.entry_price - exit_price) * close_qty
    exit_fee = nominal * fee_rate

    acc.equity += gross - exit_fee
    acc.total_fees += exit_fee

    pos.acc_gross_pnl += gross
    pos.acc_exit_fee += exit_fee
    pos.legs_closed += 1
    pos.last_leg_exit_price = exit_price
    pos.qty -= close_qty


def _close_position(pos: PositionRow, exit_price: float, exit_time: str,
                    exit_reason: str, acc: _Acc, trade_id: int, mode: str,
                    fee_rate: float) -> TradeRow:
    """잔여 qty 종결 + 단일 라이프사이클 TradeRow 생성. backtest._close_position 미러."""
    _book_leg(pos, pos.qty, exit_price, fee_rate, acc)

    gross_pnl = pos.acc_gross_pnl
    total_fee = pos.entry_fee + pos.acc_exit_fee
    funding = pos.acc_funding
    net_pnl = gross_pnl - total_fee - funding

    net_r = net_pnl / pos.initial_risk if pos.initial_risk > 0 else 0.0
    gross_r = gross_pnl / pos.initial_risk if pos.initial_risk > 0 else 0.0

    trade = TradeRow(
        trade_id=trade_id,
        side=pos.side,
        entry_time=pos.entry_time,
        entry_price=pos.entry_price,
        exit_time=exit_time,
        exit_price=exit_price,
        qty=pos.initial_qty if pos.initial_qty > 0 else pos.qty,
        leverage=pos.leverage,
        sl_price=pos.sl_price,
        exit_reason=exit_reason,
        r_multiple=round(net_r, 3),
        fee_paid=round(total_fee, 6),
        funding_paid=round(funding, 6),
        tranche_index=pos.tranche_index,
        liq_price=pos.liq_price,
        net_pnl=round(net_pnl, 4),
        gross_pnl=round(gross_pnl, 4),
        gross_r_multiple=round(gross_r, 3),
        num_legs=pos.legs_closed,
        mode=mode,
    )
    acc.closed_trades.append(trade)
    return trade


# ---------------------------------------------------------------------------
# Funding 로드 (market.db) — backtest 와 동일 sign-aware/폴백 로직.
# ---------------------------------------------------------------------------

def _load_funding(market_conn: sqlite3.Connection) -> tuple[list[int], list[float]]:
    times: list[int] = []
    rates: list[float] = []
    try:
        for ft, fr in market_conn.execute(
            "SELECT funding_time, rate FROM funding ORDER BY funding_time"
        ):
            times.append(int(ft))
            rates.append(float(fr))
    except Exception:
        pass
    return times, rates


def _resolve_funding_rate(funding_due: bool, sign_aware: bool,
                          funding_times: list[int], funding_rates: list[float],
                          bar_time: pd.Timestamp) -> float:
    if not funding_due:
        return 0.0
    if sign_aware:
        fi = bisect.bisect_right(funding_times, int(bar_time.value // 1_000_000)) - 1
        return funding_rates[fi] if fi >= 0 else abs(FUNDING_RATE)
    return abs(FUNDING_RATE)


# ---------------------------------------------------------------------------
# 섀도우 어댑터 — 한 확정 30m 봉을 가상 체결.
# ---------------------------------------------------------------------------

class ShadowAdapter:
    """루트 DB에 영속된 가상 계좌를 30m 확정봉 기준으로 집행한다.

    process_bar() 를 새 확정 30m 봉마다 1회 호출한다. 모든 상태(포지션/펀딩/
    쿨다운/4h 하드캡/pending order)는 btc_* 테이블에 영속되어 재시작에 안전하다.
    """

    def __init__(self, root_conn: sqlite3.Connection,
                 tf_data: dict[str, pd.DataFrame],
                 funding_times: list[int], funding_rates: list[float],
                 mode: str = "shadow"):
        self.conn = root_conn
        self.tf_data = tf_data
        self.funding_times = funding_times
        self.funding_rates = funding_rates
        self.mode = mode

    # --- meta 헬퍼 ---
    def _get_meta(self, key, default=None):
        v = tracking.get_meta(self.conn, key, self.mode)
        return default if v is None else v

    def _set_meta(self, key, value):
        tracking.set_meta(self.conn, key, value, self.mode)

    def process_bar(self, bar_time: pd.Timestamp, bar: pd.Series,
                    new_4h_confirmed: bool, cur_4h_ns: Optional[int]) -> None:
        """단일 확정 30m 봉 처리 — backtest run_backtest 의 per-bar 루프 미러."""
        mode = self.mode
        conn = self.conn
        bar_open = float(bar["open"])
        bar_high = float(bar["high"])
        bar_low = float(bar["low"])
        bar_close = float(bar["close"])
        bar_time_str = str(bar_time)
        bar_idx = bar_index_for(int(bar_time.value // 1_000_000))

        # 가상 계좌 equity 복원 (없으면 초기자본).
        equity = tracking.latest_equity(conn, mode)
        if equity is None:
            equity = INITIAL_EQUITY
            tracking.record_equity(conn, equity, mode, bar_time_str)
        acc = _Acc(equity=equity)

        # 크로스-바 트래커 복원.
        trade_id_counter = int(self._get_meta("trade_id_counter", 0))
        last_close_bar = self._get_meta("last_close_bar", {"long": -10_000, "short": -10_000})
        last_close_was_sl = self._get_meta("last_close_was_sl", {"long": False, "short": False})
        last_new_entry_eval_4h_ns = self._get_meta("last_new_entry_eval_4h_ns", None)
        pending = self._get_meta("pending_order", None)

        positions = tracking.load_open_positions(conn, mode)

        # --- 1. pending 진입 주문 체결 판정 (backtest 와 동일: 다음 봉 [low,high]) ---
        if pending is not None:
            bars_elapsed = bar_idx - int(pending["bar_idx"])
            lp = float(pending["limit_price"])
            filled = bar_low <= lp <= bar_high
            if filled:
                nominal = float(pending["sizing_qty"]) * lp
                entry_fee = nominal * MAKER_FEE
                acc.equity -= entry_fee
                acc.total_fees += entry_fee
                new_pos = PositionRow(
                    side=pending["side"],
                    entry_price=lp,
                    qty=float(pending["sizing_qty"]),
                    leverage=float(pending["sizing_leverage"]),
                    sl_price=float(pending["sizing_sl_price"]),
                    tp1_price=float(pending["sizing_tp1_price"]),
                    tp2_price=float(pending["sizing_tp2_price"]),
                    tp3_price=float(pending["sizing_tp3_price"]),
                    liq_price=float(pending["sizing_liq_price"]),
                    entry_time=bar_time_str,
                    tranche_index=int(pending["tranche_index"]),
                    entry_bar_idx=bar_idx,
                    initial_risk=float(pending["initial_risk"]),
                    entry_fee=entry_fee,
                    initial_qty=float(pending["sizing_qty"]),
                    mode=mode,
                )
                tracking.save_position(conn, new_pos)
                positions.append(new_pos)
                pending = None
                tracking.log_event(conn, "fill",
                    f"{new_pos.side} entry filled @ {lp:.2f} qty={new_pos.qty:.6f}",
                    mode=mode, ts=bar_time_str)
            elif bars_elapsed >= ENTRY_ORDER_EXPIRY_BARS:
                pending = None
                tracking.log_event(conn, "expire", "pending entry expired",
                                   mode=mode, ts=bar_time_str)

        # --- 2. 열린 포지션 exits 평가/집행 (Action 순서 동일) ---
        positions_to_remove: list[PositionRow] = []
        for pos in positions:
            funding_due = bar_idx % FUNDING_INTERVAL_BARS == 0
            sign_aware = bool(self.funding_times)
            funding_rate = _resolve_funding_rate(
                funding_due, sign_aware, self.funding_times, self.funding_rates, bar_time
            )

            trailing_ma: Optional[float] = None
            if pos.trailing_active:
                tf_trail = _get_tf_slice(self.tf_data, bar_time, TRAILING_TF)
                if len(tf_trail) >= 10:
                    _ma = tf_trail["close"].rolling(10).mean().iloc[-1]
                    if not pd.isna(_ma):
                        trailing_ma = float(_ma)

            pos_view = PositionView(
                side=pos.side, entry_price=pos.entry_price, qty=pos.qty,
                sl_price=pos.sl_price, tp1_price=pos.tp1_price, liq_price=pos.liq_price,
                trailing_active=pos.trailing_active, be_stop_set=pos.be_stop_set,
                tp1_hit=pos.tp1_hit, liq_breach_flagged=pos.liq_breach_flagged,
            )
            bar_view = BarView(idx=bar_idx, high=bar_high, low=bar_low, close=bar_close)
            ctx = ExitContext(
                funding_due=funding_due, funding_rate=funding_rate,
                funding_sign_aware=sign_aware, trailing_ma=trailing_ma,
                be_trail_activate_r=BE_TRAIL_ACTIVATE_R, liq_monitor_frac=LIQ_MONITOR_FRAC,
            )
            actions = evaluate_exits(pos_view, bar_view, ctx)

            closed = False
            for act in actions:
                if isinstance(act, ChargeFunding):
                    acc.equity -= act.amount
                    acc.total_funding += act.amount
                    pos.acc_funding += act.amount
                elif isinstance(act, ClearBreachFlag):
                    pos.liq_breach_flagged = False
                elif isinstance(act, ForceReduce):
                    pos.liq_breach_flagged = True
                    pos.had_forced_reduce = True
                    reduce_qty = pos.qty * act.fraction
                    _book_leg(pos, reduce_qty, act.price, TAKER_FEE + SLIPPAGE_SL, acc)
                    if pos.qty <= 0:
                        trade_id_counter = self._finalize_close(
                            conn, pos, act.price, bar_time_str, "liq_forced_reduce",
                            acc, trade_id_counter, mode, TAKER_FEE + SLIPPAGE_SL,
                            last_close_bar, last_close_was_sl, bar_idx,
                        )
                        positions_to_remove.append(pos)
                        closed = True
                        break
                elif isinstance(act, UpdateStop):
                    pos.sl_price = act.new_stop
                elif isinstance(act, ClosePosition):
                    trade_id_counter = self._finalize_close(
                        conn, pos, act.price, bar_time_str, act.reason,
                        acc, trade_id_counter, mode, TAKER_FEE + SLIPPAGE_SL,
                        last_close_bar, last_close_was_sl, bar_idx,
                    )
                    positions_to_remove.append(pos)
                    closed = True
                    break
                elif isinstance(act, BookPartial):
                    close_qty = pos.qty * act.fraction
                    _book_leg(pos, close_qty, act.price, MAKER_FEE, acc)
                    pos.tp1_hit = True
                elif isinstance(act, ActivateBETrail):
                    pos.be_stop_set = True
                    pos.trailing_active = True

            if not closed:
                tracking.save_position(conn, pos)  # 갱신된 상태 영속

        for pos in positions_to_remove:
            if pos in positions:
                positions.remove(pos)
            if pos.id is not None:
                tracking.remove_position(conn, pos.id)

        # --- 3. 진입/청산-신호 평가 (4h 하드캡 + 쿨다운) ---
        if pending is None and len(positions) < 3:
            snapshot = _build_snapshot_at(self.tf_data, bar_time)
            if snapshot is not None:
                # 청산 신호 (signal_exit / signal_reduce)
                for pos in list(positions):
                    exit_sig = check_exit_signal(snapshot, pos.side)
                    if exit_sig.exit_action == "exit":
                        trade_id_counter = self._finalize_close(
                            conn, pos, bar_close, bar_time_str, "signal_exit",
                            acc, trade_id_counter, mode, TAKER_FEE,
                            last_close_bar, last_close_was_sl, bar_idx,
                        )
                        if pos in positions:
                            positions.remove(pos)
                        if pos.id is not None:
                            tracking.remove_position(conn, pos.id)
                    elif exit_sig.exit_action == "reduce":
                        close_qty = pos.qty * 0.5
                        if close_qty > 0:
                            _book_leg(pos, close_qty, bar_close, TAKER_FEE, acc)
                            if pos.qty <= 0:
                                trade_id_counter = self._finalize_close(
                                    conn, pos, bar_close, bar_time_str, "signal_reduce",
                                    acc, trade_id_counter, mode, TAKER_FEE,
                                    last_close_bar, last_close_was_sl, bar_idx,
                                )
                                if pos in positions:
                                    positions.remove(pos)
                                if pos.id is not None:
                                    tracking.remove_position(conn, pos.id)
                            else:
                                tracking.save_position(conn, pos)

                # 신규 진입 신호 (4h 확정봉에서만)
                sig = generate_signal(snapshot) if new_4h_confirmed else Signal(
                    side="none", strength=0.0, reason="4h 미확정 — 진입평가 보류"
                )
                if new_4h_confirmed:
                    # 신호 평가 전수 기록 (기각 포함) — 관측 전용, 실패 비전파.
                    # "진입 안 한 순간"의 데이터가 없으면 사후 연구마다 재시뮬이 필요하다.
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
                            n_open=len(positions), mode=mode)
                    except Exception:  # noqa: BLE001 — 로깅이 매매를 못 막는다
                        pass
                if sig.side != "none":
                    same_side = [p for p in positions if p.side == sig.side]
                    current_tranche = len(same_side)
                    intent: Optional[OpenIntent] = None

                    if current_tranche == 0:
                        # 4h 하드캡: 같은 4h 캔들 재평가 금지.
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
                            # E4 오버레이: drawdown 기반 operating risk 로 RISK_PER_TRADE 조정.
                            intent = self._evaluate_entry_with_risk(
                                sig, acc.equity, 0, ei,
                                cooldown=CooldownState(
                                    bars_since_close=bars_since_close,
                                    cooldown_bars=cooldown_bars,
                                ),
                            )
                    elif current_tranche < 3:
                        avg_entry = sum(p.entry_price for p in same_side) / len(same_side)
                        entry_price = bar_close
                        ei = self._entry_inputs(bar_time, sig.side, entry_price)
                        intent = self._evaluate_entry_with_risk(
                            sig, acc.equity, current_tranche, ei,
                            avg_entry=avg_entry, current_price=bar_close,
                        )

                    if intent is not None:
                        sz = intent.sizing
                        pending = {
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
                            f"tranche={intent.tranche_index} risk={intent.initial_risk:.2f}",
                            mode=mode, ts=bar_time_str)

        # --- 4. 종결 트레이드 기록 + equity 기록 + 메타 영속 ---
        for trade in acc.closed_trades:
            tracking.record_trade(conn, trade)
        tracking.record_equity(conn, acc.equity, mode, bar_time_str)

        self._set_meta("trade_id_counter", trade_id_counter)
        self._set_meta("last_close_bar", last_close_bar)
        self._set_meta("last_close_was_sl", last_close_was_sl)
        self._set_meta("last_new_entry_eval_4h_ns", last_new_entry_eval_4h_ns)
        self._set_meta("pending_order", pending)

    # --- helpers ---

    def _finalize_close(self, conn, pos, price, time_str, reason, acc,
                        trade_id_counter, mode, fee_rate,
                        last_close_bar, last_close_was_sl, bar_idx) -> int:
        """포지션 종결 + 쿨다운 트래커 갱신. 새 trade_id_counter 반환."""
        _close_position(pos, price, time_str, reason, acc, trade_id_counter, mode, fee_rate)
        trade_id_counter += 1
        last_close_bar[pos.side] = bar_idx
        last_close_was_sl[pos.side] = reason in ("sl",)
        return trade_id_counter

    def _entry_inputs(self, bar_time, side, entry_price) -> EntryInputs:
        """1h 슬라이스에서 ATR/swing/MA35 파생 — backtest 인라인 derivation 동일."""
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
        """E4 오버레이: drawdown 기반 operating risk 로 RISK_PER_TRADE 를 일시 조정해
        evaluate_entry 를 호출한다. (engine.sizing.RISK_PER_TRADE 단일 소스 임시 패치.)

        섀도우 기본 risk=2% (base) / 1% (reduced). peak 는 equity 곡선 high-water-mark.
        """
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
