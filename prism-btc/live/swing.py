# live/swing.py — 라운드6 Lane B 스윙 레인 가상 집행기 (mode='swing' 자체 원장)
#
# 왜 v1 은 가상 집행인가: 거래소 원웨이 모드에서 메인 레인과 같은 심볼의
# 포지션이 넷팅되어 DemoAdapter 의 reconcile 불변식(거래소 포지션 == 메인
# 포지션)이 깨진다. v1 은 스윙 레인을 tracking 의 mode='swing' 원장으로
# 가상 체결하고 진입/청산을 텔레그램으로 즉시 알린다. 거래소 실집행
# (헤지모드 분리)은 별도 라운드에서.
#
# 집행 의미론 (analysis/round6_swing_lane.py 백테스트 미러):
#   - 신호: 4h 확정봉 (30m 틱마다 _get_tf_slice 로 감지 — 메인과 동일 케이던스)
#   - 진입/룰청산 체결: 감지 시점 30m 봉 종가 (≈ 백테스트의 다음 4h 시가),
#     비용 TAKER_FEE. 하드스탑: 30m 봉 low/high 봉내 감시, 체결 = 스탑가,
#     비용 TAKER_FEE + SLIPPAGE_SL. (백테스트는 4h 봉내 스탑 — 30m 감시가
#     더 정밀하며 보수적 방향의 차이만 있다.)
#   - 포지션 최대 1개, 피라미딩/부분청산/펀딩 없음 (백테스트와 동일 스코프).
#   - 메인 레인과 방향 충돌 시 진입 금지 (core.swing.conflicts_with_main).
#
# 안전 원칙: process() 는 어떤 예외도 밖으로 던지지 않도록 호출측(runner)이
# 감싼다. 여기서도 알림/신호로그 실패는 개별 흡수한다 (매매 결정 비영향).
from __future__ import annotations

import logging
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
from engine.config import SWING_ENABLED, SWING_INITIAL_EQUITY
from live import tracking
from live.shadow import bar_index_for

log = logging.getLogger("live.swing")

MODE = "swing"  # tracking 원장 키 — 메인 mode(shadow/demo/live)와 완전 분리


# ---------------------------------------------------------------------------
# 텔레그램 알림 — notifier 와 동일한 재사용 패턴, [스윙레인] 태그로 구분.
# ---------------------------------------------------------------------------

def _notify(main_mode: str, msg: str) -> None:
    """메인이 demo/live 로 돌 때만 발송 (shadow 로컬 테스트 스팸 방지). 실패 흡수."""
    if main_mode not in ("demo", "live"):
        return
    try:
        import asyncio
        import os

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
# 체결 — 가상 원장 (btc_positions / btc_trading_history / btc_equity_curve)
# ---------------------------------------------------------------------------

def _close_position(conn, pos: tracking.PositionRow, exit_price: float,
                    fee_rate: float, reason: str, bar_time_str: str,
                    equity: float, trade_counter: int,
                    main_mode: str) -> tuple[float, int]:
    """가상 청산: PnL 정산 → 트레이드 기록 → 포지션 제거 → equity 갱신 → 알림."""
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
    equity += net
    tracking.record_equity(conn, equity, MODE, bar_time_str)
    tracking.log_event(conn, "trade",
                       f"swing close {pos.side} @ {exit_price:.1f} ({reason}) "
                       f"net={net:+.2f} eq={equity:.2f}", mode=MODE)
    r = net / risk
    outcome = f"✅ 이익 {r:+.1f}배" if r > 0 else f"❌ 손실 {r:+.1f}배"
    why = "손절" if reason == "swing_sl" else "추세이탈 청산"
    _notify(main_mode,
            f"🌀 [스윙레인] 포지션 정리 — {outcome} ({why})\n"
            f"_가상자금 모의투자입니다_")
    return equity, trade_counter


def _try_entry(conn, bar, bar_time_str: str, s4: pd.DataFrame,
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

    entry_price = float(bar["close"])
    sl = stop_price(side, entry_price, float(cur["atr14"]))
    sz = compute_swing_sizing(equity, entry_price, sl)
    if sz.rejected:
        _log_signal_safe(conn, bar_time_str, side,
                         f"swing 기각: sizing ({sz.reject_reason})", 0)
        return None

    entry_fee = sz.qty * entry_price * TAKER_FEE
    pos = tracking.PositionRow(
        side=side,
        entry_price=entry_price,
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
                       f"swing open {side} @ {entry_price:.1f} qty={sz.qty:.4f} "
                       f"sl={sl:.1f} lev={sz.leverage:.2f}", mode=MODE)
    _log_signal_safe(conn, bar_time_str, side, "swing_entry", 1)
    _notify(main_mode,
            f"🌀 [스윙레인] 새 진입 — {_side_kr(side)}\n"
            f"진입가 {entry_price:,.0f}달러 · 손절 {sl:,.0f}달러 · "
            f"{sz.leverage:.1f}배율\n_가상자금 모의투자입니다_")
    return pos


# ---------------------------------------------------------------------------
# 핵심 진입점 — runner.tick 이 30m 틱마다 호출.
# ---------------------------------------------------------------------------

def process(root_conn, tf_data: dict, main_mode: str = "shadow") -> dict:
    """새 확정 30m 봉들을 스윙 레인 관점에서 처리. {"events": n} 반환.

    자체 메타 커서(mode='swing' 의 last_processed_30m_ns / last_confirmed_4h_ns)
    를 쓰므로 메인 레인 상태와 완전 독립이며 재실행에 멱등이다.
    콜드 스타트(커서 없음)는 마지막 확정봉 1개만 처리한다 (과거 재시뮬 방지).
    """
    result = {"events": 0}
    if not SWING_ENABLED:
        return result
    bars_30m = tf_data.get("30m")
    if bars_30m is None or bars_30m.empty:
        return result
    conn = root_conn

    equity = tracking.latest_equity(conn, MODE)
    if equity is None:
        equity = SWING_INITIAL_EQUITY
        tracking.record_equity(conn, equity, MODE)
        tracking.log_event(conn, "info",
                           f"swing lane ledger seeded: {equity:.0f}", mode=MODE)

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

        # --- 1. 하드스탑 봉내 감시 (매 30m — 백테스트보다 정밀) ---
        if pos is not None:
            hit = (pos.side == "long" and float(bar["low"]) <= pos.sl_price) or \
                  (pos.side == "short" and float(bar["high"]) >= pos.sl_price)
            if hit:
                equity, trade_counter = _close_position(
                    conn, pos, pos.sl_price, TAKER_FEE + SLIPPAGE_SL,
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
                equity, trade_counter = _close_position(
                    conn, pos, float(bar["close"]), TAKER_FEE,
                    "swing_ma35_exit", bar_time_str, equity, trade_counter,
                    main_mode)
                pos = None
                result["events"] += 1
        else:
            s1 = _get_tf_slice(tf_data, bar_time, "1d")
            pos = _try_entry(conn, bar, bar_time_str, s4, s1, equity, main_mode)
            if pos is not None:
                result["events"] += 1

    tracking.set_meta(conn, "last_processed_30m_ns",
                      int(new_bars.index[-1].value), MODE)
    if last_4h_ns is not None:
        tracking.set_meta(conn, "last_confirmed_4h_ns", int(last_4h_ns), MODE)
    tracking.set_meta(conn, "trade_id_counter", trade_counter, MODE)
    return result
