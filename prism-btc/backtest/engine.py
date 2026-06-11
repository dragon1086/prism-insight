# backtest/engine.py — Event-driven backtester for prism-btc (§3 of D3 spec)
from __future__ import annotations

import sqlite3
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal, Optional
import pandas as pd

from engine.indicators import add_indicators
from engine.regime import build_snapshot, RegimeSnapshot
from engine.signal import generate_signal, check_exit_signal, Signal
from engine.sizing import (
    compute_sizing,
    can_add_tranche,
    approx_liq_price,
    SizingResult,
    LIQ_BUFFER_MIN_FRAC,
    TRANCHE_FRACS,
)
import engine.sizing as _sizing  # RISK_PER_TRADE 단일 소스 참조 (런타임 조회)

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
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Fee / cost constants
# ---------------------------------------------------------------------------
MAKER_FEE: float = 0.0002          # 0.02% post-only
TAKER_FEE: float = 0.00055         # 0.055% market
SLIPPAGE_SL: float = 0.0005        # 0.05% extra slippage for SL market orders
FUNDING_INTERVAL_BARS: int = 16    # 8 hours / 30m = 16 bars
FUNDING_RATE: float = -0.0001      # -0.01% per 8h (pessimistic, against position)

# Candle cutoff: post-only entry valid for 2 bars
ENTRY_ORDER_EXPIRY_BARS: int = 2

# Trailing (라운드4, tasks/v3_edge_diagnosis.md §2 처방 2): the edge lives at the
# 14d horizon while a 1h MA10 trail kept exiting on short-term noise (capture
# efficiency median 0.41 — 59% of favorable excursion given back). Align the exit
# timescale with the edge timescale: trail on the 12h MA10.
TRAILING_TF = "12h"

# BE stop + trailing activate only after price reaches this R multiple
# (라운드4: 1R → 1.5R). At 1R, 34.4% of MFE≥1R trades were truncated to <0.5R;
# at 2R the truncation rate was only 13.8% — the 1R BE stop was the cutter.
# TP1 partial close (1/3 at 1R) is unchanged; only BE/trailing are delayed.
BE_TRAIL_ACTIVATE_R: float = 1.5

# Liquidation buffer monitoring threshold (50% of entry→liq gap)
LIQ_MONITOR_FRAC: float = 0.50

# TFs needed for indicators
ALL_TFS = ("30m", "1h", "4h", "12h", "1d", "1w")

# TF bar duration (used to enforce "only confirmed/closed candles" cutoff — no look-ahead)
TF_DURATION: dict[str, pd.Timedelta] = {
    "30m": pd.Timedelta(minutes=30),
    "1h": pd.Timedelta(hours=1),
    "4h": pd.Timedelta(hours=4),
    "12h": pd.Timedelta(hours=12),
    "1d": pd.Timedelta(days=1),
    "1w": pd.Timedelta(weeks=1),
}

# --- P1-1 signal throttle (오닐: 거래를 엄선, 추세를 길게) ---
# Re-entry cooldown: same-direction re-entry needs N bars after last close
REENTRY_COOLDOWN_BARS: int = 8       # 8 × 30m = 4h
# After a stop-loss, same-direction re-entry needs a longer cooldown (churn 방지)
SL_REENTRY_COOLDOWN_BARS: int = 16   # 16 × 30m = 8h

# Minimum rows per TF to compute indicators (35 for MA35 + 14 for ATR warm-up)
MIN_ROWS = 50


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class TradeLog:
    """
    One row == one POSITION lifecycle (all legs aggregated). A position that
    partial-closes via TP1/TP2 and then ends on SL/BE is a SINGLE trade here.

    win == net_pnl > 0 (fees + funding + slippage already deducted).
    `r_multiple` is the NET R (net_pnl / initial_risk) and is authoritative.
    `gross_r_multiple` / `gross_pnl` are reference-only (pre-cost) columns.
    """
    trade_id: int
    side: Literal["long", "short"]
    entry_time: str
    entry_price: float
    exit_time: str
    exit_price: float          # final leg exit price
    qty: float                 # original (full) position qty
    leverage: float
    sl_price: float
    exit_reason: str           # final close reason: "sl", "tp3", "signal_exit", "end_of_period", ...
    r_multiple: float          # NET realized R (net_pnl / initial_risk) — authoritative
    fee_paid: float            # total fees across all legs (entry + every exit)
    funding_paid: float        # total funding across position lifetime
    tranche_index: int
    liq_price: float
    net_pnl: float = 0.0       # net $ across all legs (gross - fees - funding)
    gross_pnl: float = 0.0     # reference: pre-cost price PnL across all legs
    gross_r_multiple: float = 0.0  # reference: gross_pnl / initial_risk
    num_legs: int = 1          # number of partial closes that made up this position


@dataclass
class Position:
    side: Literal["long", "short"]
    entry_price: float
    qty: float
    leverage: float
    sl_price: float
    tp1_price: float
    tp2_price: float
    tp3_price: float
    liq_price: float
    entry_time: str
    tranche_index: int
    entry_bar_idx: int
    initial_risk: float        # equity * 2% * tranche_frac — used for R calc
    trailing_active: bool = False
    be_stop_set: bool = False  # breakeven stop set (after 1R)
    tp1_hit: bool = False
    tp2_hit: bool = False
    entry_fee: float = 0.0
    liq_breach_flagged: bool = False  # True when currently in 50% buffer breach
    had_forced_reduce: bool = False   # 라운드3 C: this position experienced a forced de-risk reduction
    # --- position-lifecycle accumulators (P0-2 net accounting) ---
    initial_qty: float = 0.0        # full qty at entry (legs reduce `qty`)
    acc_gross_pnl: float = 0.0      # sum of price PnL across closed legs
    acc_exit_fee: float = 0.0       # sum of exit fees across closed legs
    acc_funding: float = 0.0        # sum of funding charged over lifetime
    legs_closed: int = 0            # number of partial legs closed so far
    last_leg_exit_price: float = 0.0
    last_leg_reason: str = ""


@dataclass
class PendingOrder:
    side: Literal["long", "short"]
    limit_price: float
    bar_idx: int               # bar when signal was generated
    sizing: SizingResult
    initial_risk: float
    tranche_index: int


@dataclass
class BacktestState:
    equity: float
    positions: list[Position] = field(default_factory=list)
    pending_order: Optional[PendingOrder] = None
    trade_logs: list[TradeLog] = field(default_factory=list)
    equity_curve: list[tuple[str, float]] = field(default_factory=list)
    trade_id_counter: int = 0
    total_funding: float = 0.0
    total_fees: float = 0.0
    liq_approach_count: int = 0   # mark price breached 50% of entry→liq gap (forced-reduce events)
    # --- 라운드3 C: liq_approach 분리 계측 (강제감축이 실제 수익을 깎는지 vs 측정 아티팩트) ---
    # 강제감축 레그에서 실현된 gross PnL 합계($). 음수면 감축이 손실을 조기 확정한 것.
    liq_forced_reduce_pnl: float = 0.0
    # 강제감축이 발생한 포지션 중, 최종적으로 SL/liq_forced_reduce 로 끝난 건수
    # (= 감축이 없었어도 SL로 끝났을 것으로 추정되는 건수).
    liq_reduce_would_be_sl: int = 0
    # 강제감축이 발생한 포지션 중, 최종적으로 수익(net>0)으로 끝난 건수
    # (= 감축이 업사이드를 깎았을 가능성이 있는 건수).
    liq_reduce_ended_win: int = 0
    # --- P1-1 re-entry cooldown: last close bar index per side, and whether it was a SL ---
    last_close_bar: dict[str, int] = field(
        default_factory=lambda: {"long": -10_000, "short": -10_000}
    )
    last_close_was_sl: dict[str, bool] = field(
        default_factory=lambda: {"long": False, "short": False}
    )


# ---------------------------------------------------------------------------
# DB data loading
# ---------------------------------------------------------------------------

def _load_tf_data(conn: sqlite3.Connection, tf: str) -> pd.DataFrame:
    """Load all confirmed klines for a TF, sorted oldest first."""
    df = pd.read_sql_query(
        "SELECT open_time, open, high, low, close, volume, turnover "
        "FROM klines WHERE timeframe=? AND confirmed=1 ORDER BY open_time ASC",
        conn,
        params=(tf,),
    )
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df.set_index("open_time")
    return df


# Cache of candle end-times (int64 ns) per loaded TF frame — hot-path helper
# for _get_tf_slice. Keyed by (id(df), tf); frames live for the whole run.
_END_NS_CACHE: dict = {}


def _get_tf_slice(
    tf_data: dict[str, pd.DataFrame],
    current_30m_time: pd.Timestamp,
    tf: str,
) -> pd.DataFrame:
    """
    Return only CLOSED/confirmed candles for `tf` as of current_30m_time.
    A candle is closed iff open_time + tf_duration <= current_30m_time, so a
    higher-TF candle still in progress at current_30m_time is excluded (미래참조 금지).
    """
    df = tf_data.get(tf)
    if df is None or df.empty:
        return pd.DataFrame()
    duration = TF_DURATION.get(tf, pd.Timedelta(0))
    # closed iff candle end (open_time + duration) <= current time.
    # Index is sorted, so the closed candles form a prefix — find its length
    # with binary search on cached end-times instead of a full boolean mask
    # (hot path: called 6x per simulated 30m bar).
    key = (id(df), tf)
    ends = _END_NS_CACHE.get(key)
    if ends is None or len(ends) != len(df):
        # as_unit("ns") matters: pandas may load the index at us/ms resolution,
        # and asi8 returns ints in the index's own unit while Timestamp.value
        # is always ns — without normalization the comparison is off by 1000x.
        ends = (df.index + duration).as_unit("ns").asi8
        _END_NS_CACHE[key] = ends
    k = int(ends.searchsorted(current_30m_time.value, side="right"))
    return df.iloc[:k]


# ---------------------------------------------------------------------------
# Snapshot builder (no look-ahead)
# ---------------------------------------------------------------------------

def _build_snapshot_at(
    tf_data: dict[str, pd.DataFrame],
    current_30m_time: pd.Timestamp,
) -> Optional[RegimeSnapshot]:
    """
    Build RegimeSnapshot using only confirmed candles strictly before current_30m_time.
    Returns None if any TF has insufficient data.
    """
    tf_dfs: dict[str, pd.DataFrame] = {}
    for tf in ALL_TFS:
        sliced = _get_tf_slice(tf_data, current_30m_time, tf)
        if len(sliced) < MIN_ROWS:
            return None
        tf_dfs[tf] = sliced

    try:
        dt = current_30m_time.to_pydatetime().replace(tzinfo=timezone.utc)
        return build_snapshot(tf_dfs, evaluated_at=dt)
    except Exception as exc:
        log.debug("build_snapshot failed at %s: %s", current_30m_time, exc)
        return None


# ---------------------------------------------------------------------------
# Trade execution helpers
# ---------------------------------------------------------------------------

def _apply_fee(equity: float, nominal: float, fee_rate: float) -> tuple[float, float]:
    """Deduct fee from equity. Returns (new_equity, fee_paid)."""
    fee = nominal * fee_rate
    return equity - fee, fee


def _book_leg(
    pos: Position,
    close_qty: float,
    exit_price: float,
    fee_rate: float,
    state: BacktestState,
) -> None:
    """
    Realize a partial (or remaining) leg: book gross price PnL minus exit fee
    to equity, and accumulate gross/fee on the position for lifecycle aggregation.
    Does NOT emit a TradeLog — that happens once, at final close, in _close_position.
    """
    nominal = close_qty * exit_price
    if pos.side == "long":
        gross = (exit_price - pos.entry_price) * close_qty
    else:
        gross = (pos.entry_price - exit_price) * close_qty
    exit_fee = nominal * fee_rate

    state.equity += gross - exit_fee
    state.total_fees += exit_fee

    pos.acc_gross_pnl += gross
    pos.acc_exit_fee += exit_fee
    pos.legs_closed += 1
    pos.last_leg_exit_price = exit_price
    pos.qty -= close_qty


def _close_position(
    pos: Position,
    exit_price: float,
    exit_time: str,
    exit_reason: str,
    state: BacktestState,
    fee_rate: float = TAKER_FEE + SLIPPAGE_SL,
    bar_idx: int = -1,
) -> None:
    """
    Close the REMAINING qty of a position and emit ONE position-lifecycle TradeLog
    aggregating all legs. Win == net_pnl > 0 (gross - all fees - all funding).
    """
    # Realize the final remaining leg.
    _book_leg(pos, pos.qty, exit_price, fee_rate, state)

    gross_pnl = pos.acc_gross_pnl
    total_fee = pos.entry_fee + pos.acc_exit_fee
    funding = pos.acc_funding
    net_pnl = gross_pnl - total_fee - funding

    net_r = net_pnl / pos.initial_risk if pos.initial_risk > 0 else 0.0
    gross_r = gross_pnl / pos.initial_risk if pos.initial_risk > 0 else 0.0

    log_entry = TradeLog(
        trade_id=state.trade_id_counter,
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
    )
    state.trade_logs.append(log_entry)
    state.trade_id_counter += 1

    # 라운드3 C: 강제감축을 겪은 포지션의 최종 결말 분류.
    # SL/liq_forced_reduce 로 끝났으면 "감축 없었어도 SL이었을 것"으로 추정,
    # net 수익으로 끝났으면 "감축이 업사이드를 깎았을 가능성"으로 카운트.
    if pos.had_forced_reduce:
        if exit_reason in ("sl", "liq_forced_reduce"):
            state.liq_reduce_would_be_sl += 1
        elif net_pnl > 0:
            state.liq_reduce_ended_win += 1

    # Record cooldown info (P1-1): when and whether this side closed on a stop.
    if bar_idx >= 0:
        state.last_close_bar[pos.side] = bar_idx
        state.last_close_was_sl[pos.side] = exit_reason in ("sl",)


# ---------------------------------------------------------------------------
# Main backtester
# ---------------------------------------------------------------------------

def run_backtest(
    conn: sqlite3.Connection,
    start_ts: pd.Timestamp,
    end_ts: pd.Timestamp,
    initial_equity: float = 10_000.0,
) -> BacktestState:
    """
    Event-driven backtest over 30m bars in [start_ts, end_ts).

    Per bar:
    1. Check pending entry order fill (next bar after signal)
    2. For each open position: SL/TP/trailing/funding
    3. Build snapshot, generate signal, create pending order
    """
    # Load all data once and precompute indicators per TF (O(n) once instead of
    # O(n^2) per-bar recomputation). Safe: SMA/ATR are causal, and _get_tf_slice
    # always returns a prefix of these frames, so per-row values are identical.
    tf_data: dict[str, pd.DataFrame] = {}
    for tf in ALL_TFS:
        tf_data[tf] = add_indicators(_load_tf_data(conn, tf))

    # 실펀딩 데이터 로드 (있으면 sign-aware, 없으면 기존 비관 고정값 폴백).
    # 실측: 2020~2026 평균 +0.0121%/8h, 양수 84% — 숏은 대부분 기간 펀딩 수취.
    funding_times: list[int] = []
    funding_rates: list[float] = []
    try:
        for ft, fr in conn.execute(
            "SELECT funding_time, rate FROM funding ORDER BY funding_time"
        ):
            funding_times.append(int(ft))
            funding_rates.append(float(fr))
    except Exception:
        pass  # 테이블 없음 → 폴백

    # Get 30m bars in range
    bars_30m = tf_data["30m"]
    mask = (bars_30m.index >= start_ts) & (bars_30m.index < end_ts)
    sim_bars = bars_30m[mask]

    state = BacktestState(equity=initial_equity)
    state.equity_curve.append((str(start_ts), initial_equity))

    # 라운드2 #3: 신규 진입 신호는 4h 캔들이 새로 "확정"된 30m 바에서만 평가한다.
    # (의사결정 cadence 자체는 30m 유지 — 청산/SL/트레일링/risk_guardian 는 매 바 동작.)
    # 직전까지 본 확정 4h 캔들의 open_time(ns)을 추적해 변화 시에만 진입 평가를 연다.
    last_confirmed_4h_ns: int | None = None

    # 라운드3 A: 진입 빈도 하드캡 — 한 4h 캔들 내 신규 진입(트랜치0) 평가는 최대 1회.
    # 체결 여부와 무관하게 "평가가 한 번 열린 4h 캔들"을 기록하고, 같은 4h 캔들에서는
    # 미체결 만료 후에도 신규 진입 재평가를 금지한다. (피라미딩 트랜치 추가는 별개 — 미적용)
    last_new_entry_eval_4h_ns: int | None = None

    for bar_idx, (bar_time, bar) in enumerate(sim_bars.iterrows()):
        bar_open = bar["open"]
        bar_high = bar["high"]
        bar_low = bar["low"]
        bar_close = bar["close"]
        bar_time_str = str(bar_time)

        # --- 1. Check pending order fill ---
        if state.pending_order is not None:
            po = state.pending_order
            bars_elapsed = bar_idx - po.bar_idx
            lp = po.limit_price
            filled = False

            if po.side == "long" and bar_low <= lp <= bar_high:
                filled = True
            elif po.side == "short" and bar_low <= lp <= bar_high:
                filled = True

            if filled:
                sz = po.sizing
                nominal = sz.qty * lp
                state.equity, entry_fee = _apply_fee(state.equity, nominal, MAKER_FEE)
                state.total_fees += entry_fee

                pos = Position(
                    side=po.side,
                    entry_price=lp,
                    qty=sz.qty,
                    leverage=sz.leverage,
                    sl_price=sz.sl_price,
                    tp1_price=sz.tp1_price,
                    tp2_price=sz.tp2_price,
                    tp3_price=sz.tp3_price,
                    liq_price=sz.liq_price,
                    entry_time=bar_time_str,
                    tranche_index=po.tranche_index,
                    entry_bar_idx=bar_idx,
                    initial_risk=po.initial_risk,
                    entry_fee=entry_fee,
                    initial_qty=sz.qty,
                )
                state.positions.append(pos)
                state.pending_order = None
            elif bars_elapsed >= ENTRY_ORDER_EXPIRY_BARS:
                # Signal expired — discard
                state.pending_order = None

        # --- 2. Process open positions ---
        # 결정-집행 분리: core.exits.evaluate_exits 가 순수 결정(Action 리스트)을
        # 내고, 엔진은 그 Action들을 순서대로 집행한다. 결정 시퀀스(펀딩 → liq
        # 강제감축 → 트레일 갱신 → SL → TP1 → BE/트레일 활성화)는 기존 인라인
        # 루프와 동일하므로 행동 보존된다. 회계(_book_leg/_close_position/equity/
        # TradeLog)는 어댑터인 여기 그대로 유지.
        positions_to_remove = []
        for pos in state.positions:
            # Resolve the funding rate for this bar (adapter owns the funding table;
            # core only applies the precomputed formula). bar_idx % 16 == 0 boundary.
            funding_due = bar_idx % FUNDING_INTERVAL_BARS == 0
            funding_sign_aware = bool(funding_times)
            if funding_due:
                if funding_sign_aware:
                    import bisect
                    fi = bisect.bisect_right(funding_times, int(bar_time.value // 1_000_000)) - 1
                    funding_rate = funding_rates[fi] if fi >= 0 else abs(FUNDING_RATE)
                else:
                    funding_rate = abs(FUNDING_RATE)
            else:
                funding_rate = 0.0

            # Trailing MA injected so core stays pandas-free (TRAILING_TF MA10).
            trailing_ma: float | None = None
            if pos.trailing_active:
                tf_trail = _get_tf_slice(tf_data, bar_time, TRAILING_TF)
                if len(tf_trail) >= 10:
                    _ma = tf_trail["close"].rolling(10).mean().iloc[-1]
                    if not pd.isna(_ma):
                        trailing_ma = float(_ma)

            pos_view = PositionView(
                side=pos.side,
                entry_price=pos.entry_price,
                qty=pos.qty,
                sl_price=pos.sl_price,
                tp1_price=pos.tp1_price,
                liq_price=pos.liq_price,
                trailing_active=pos.trailing_active,
                be_stop_set=pos.be_stop_set,
                tp1_hit=pos.tp1_hit,
                liq_breach_flagged=pos.liq_breach_flagged,
            )
            bar_view = BarView(idx=bar_idx, high=bar_high, low=bar_low, close=bar_close)
            ctx = ExitContext(
                funding_due=funding_due,
                funding_rate=funding_rate,
                funding_sign_aware=funding_sign_aware,
                trailing_ma=trailing_ma,
                be_trail_activate_r=BE_TRAIL_ACTIVATE_R,
                liq_monitor_frac=LIQ_MONITOR_FRAC,
            )

            actions = evaluate_exits(pos_view, bar_view, ctx)

            # --- Execute the ordered actions (회계·집행) ---
            closed = False
            for act in actions:
                if isinstance(act, ChargeFunding):
                    state.equity -= act.amount
                    state.total_funding += act.amount
                    pos.acc_funding += act.amount
                elif isinstance(act, ClearBreachFlag):
                    pos.liq_breach_flagged = False
                elif isinstance(act, ForceReduce):
                    # liq-approach forced de-risk: count once, instrument gross PnL.
                    state.liq_approach_count += 1
                    pos.liq_breach_flagged = True
                    state.liq_forced_reduce_pnl += act.gross
                    pos.had_forced_reduce = True
                    reduce_qty = pos.qty * act.fraction
                    _book_leg(pos, reduce_qty, act.price, TAKER_FEE + SLIPPAGE_SL, state)
                    if pos.qty <= 0:
                        _close_position(
                            pos, act.price, bar_time_str, "liq_forced_reduce",
                            state, fee_rate=TAKER_FEE + SLIPPAGE_SL, bar_idx=bar_idx,
                        )
                        positions_to_remove.append(pos)
                        closed = True
                        break
                elif isinstance(act, UpdateStop):
                    pos.sl_price = act.new_stop
                elif isinstance(act, ClosePosition):
                    _close_position(
                        pos, act.price, bar_time_str, act.reason, state,
                        fee_rate=TAKER_FEE + SLIPPAGE_SL, bar_idx=bar_idx,
                    )
                    positions_to_remove.append(pos)
                    closed = True
                    break
                elif isinstance(act, BookPartial):
                    close_qty = pos.qty * act.fraction
                    _book_leg(pos, close_qty, act.price, MAKER_FEE, state)
                    pos.tp1_hit = True
                elif isinstance(act, ActivateBETrail):
                    pos.be_stop_set = True
                    pos.trailing_active = True

            if closed:
                continue

            # 라운드6: TP2/TP3 사다리 제거 — 우측꼬리 개방.
            # 1R/2R/3R 사다리는 최대 승리를 +2R로 캡해 실현 손익비를 1.35에
            # 가뒀다 (H2 승자절단의 잔재). TP1(1R, 1/3)만 유지해 비용·심리를
            # 확보하고, 남은 2/3은 12h MA10 트레일이 끊길 때까지 보유.
            # 6년 실측: RR 1.35→2.29 (최대 +10.1R), CAGR 11.2→15.7%,
            # PF 1.79→2.50, MDD 8.4→9.7%. 6년 중 5년 우세 (유일 악화 2021 챱).

        for pos in positions_to_remove:
            if pos in state.positions:
                state.positions.remove(pos)

        # --- 3a. Detect 4h candle confirmation (라운드2 #3 cadence gate) ---
        # Update every bar regardless of position state so the tracker never lags.
        slice_4h = _get_tf_slice(tf_data, bar_time, "4h")
        new_4h_confirmed = False
        cur_4h_ns: int | None = None
        if not slice_4h.empty:
            cur_4h_ns = int(slice_4h.index[-1].value)
            if last_confirmed_4h_ns is None:
                # Prime the tracker on the first valid bar without firing an entry.
                last_confirmed_4h_ns = cur_4h_ns
            elif cur_4h_ns != last_confirmed_4h_ns:
                new_4h_confirmed = True
                last_confirmed_4h_ns = cur_4h_ns

        # --- 3. Generate new signal ---
        # Only enter new position if no pending order and <= 1 open position (simple mode)
        if state.pending_order is None and len(state.positions) < 3:
            snapshot = _build_snapshot_at(tf_data, bar_time)
            if snapshot is not None:
                # Check exit signals for existing positions
                for pos in list(state.positions):
                    exit_sig = check_exit_signal(snapshot, pos.side)
                    if exit_sig.exit_action == "exit":
                        _close_position(
                            pos, bar_close, bar_time_str, "signal_exit", state,
                            fee_rate=TAKER_FEE, bar_idx=bar_idx,
                        )
                        if pos in state.positions:
                            state.positions.remove(pos)
                    elif exit_sig.exit_action == "reduce":
                        # Reduce by half (partial leg — booked into the position)
                        close_qty = pos.qty * 0.5
                        if close_qty > 0:
                            _book_leg(pos, close_qty, bar_close, TAKER_FEE, state)
                            if pos.qty <= 0:
                                _close_position(
                                    pos, bar_close, bar_time_str, "signal_reduce",
                                    state, fee_rate=TAKER_FEE, bar_idx=bar_idx,
                                )
                                if pos in state.positions:
                                    state.positions.remove(pos)

                # New entry signal — evaluated ONLY on a freshly confirmed 4h
                # candle (라운드2 #3). 30m/1h cadence does not open new entries.
                sig = generate_signal(snapshot) if new_4h_confirmed else Signal(
                    side="none", strength=0.0, reason="4h 미확정 — 진입평가 보류"
                )
                if sig.side != "none":
                    # Check if we already have a position in same direction
                    same_side = [p for p in state.positions if p.side == sig.side]
                    current_tranche = len(same_side)

                    if current_tranche == 0:
                        # 라운드3 A: 진입 빈도 하드캡 — 한 4h 캔들당 신규 진입 평가 최대 1회.
                        # 이 4h 캔들에서 이미 신규 진입을 평가했다면 (체결 여부 무관) 건너뛴다.
                        # (cadence/하드캡은 어댑터의 집행 관심사 — core 결정 밖에서 게이트.)
                        if cur_4h_ns is not None and cur_4h_ns == last_new_entry_eval_4h_ns:
                            continue
                        # 평가가 열리는 시점에 이 4h 캔들을 "평가됨"으로 기록한다.
                        # (쿨다운/사이징 거절로 미체결이어도 같은 4h 캔들 재평가 금지.)
                        if cur_4h_ns is not None:
                            last_new_entry_eval_4h_ns = cur_4h_ns

                        # P1-1: re-entry cooldown. Same-direction re-entry requires
                        # N bars since last close of that side; 16 bars if the last
                        # close was a stop-loss (연속 손절 churn 방지), else 8 bars.
                        bars_since_close = bar_idx - state.last_close_bar[sig.side]
                        cooldown_bars = (
                            SL_REENTRY_COOLDOWN_BARS
                            if state.last_close_was_sl[sig.side]
                            else REENTRY_COOLDOWN_BARS
                        )

                        # Derive pandas inputs (어댑터가 tf_data 소유 → 여기서 슬라이스).
                        entry_price = bar_close  # limit at close price (post-only)
                        tf_1h_slice = _get_tf_slice(tf_data, bar_time, "1h")
                        if len(tf_1h_slice) >= 14:
                            from engine.indicators import atr as calc_atr
                            atr_series = calc_atr(tf_1h_slice, 14)
                            atr_1h_val = float(atr_series.iloc[-1]) if not pd.isna(atr_series.iloc[-1]) else entry_price * 0.02
                        else:
                            atr_1h_val = entry_price * 0.02

                        if len(tf_1h_slice) >= 10:
                            if sig.side == "long":
                                swing_ref = float(tf_1h_slice["low"].iloc[-10:].min())
                            else:
                                swing_ref = float(tf_1h_slice["high"].iloc[-10:].max())
                        else:
                            swing_ref = entry_price * (0.98 if sig.side == "long" else 1.02)

                        if len(tf_1h_slice) >= 35:
                            ma35_1h = float(tf_1h_slice["close"].rolling(35).mean().iloc[-1])
                        else:
                            ma35_1h = entry_price

                        intent = evaluate_entry(
                            sig,
                            state.equity,
                            current_tranche=0,
                            inputs=EntryInputs(
                                entry_price=entry_price,
                                atr_1h=atr_1h_val,
                                swing_ref=swing_ref,
                                ma35_1h=ma35_1h,
                            ),
                            cooldown=CooldownState(
                                bars_since_close=bars_since_close,
                                cooldown_bars=cooldown_bars,
                            ),
                        )
                        if intent is not None:
                            state.pending_order = PendingOrder(
                                side=intent.side,
                                limit_price=intent.limit_price,
                                bar_idx=bar_idx,
                                sizing=intent.sizing,
                                initial_risk=intent.initial_risk,
                                tranche_index=intent.tranche_index,
                            )

                    elif current_tranche < 3:
                        # Pyramid: derive inputs then delegate the gate+sizing decision.
                        avg_entry = sum(p.entry_price for p in same_side) / len(same_side)
                        entry_price = bar_close
                        tf_1h_slice = _get_tf_slice(tf_data, bar_time, "1h")
                        atr_1h_val = entry_price * 0.02
                        if len(tf_1h_slice) >= 14:
                            from engine.indicators import atr as calc_atr
                            atr_series = calc_atr(tf_1h_slice, 14)
                            if not pd.isna(atr_series.iloc[-1]):
                                atr_1h_val = float(atr_series.iloc[-1])

                        if sig.side == "long":
                            swing_ref = float(tf_1h_slice["low"].iloc[-10:].min()) if len(tf_1h_slice) >= 10 else entry_price * 0.98
                        else:
                            swing_ref = float(tf_1h_slice["high"].iloc[-10:].max()) if len(tf_1h_slice) >= 10 else entry_price * 1.02

                        ma35_1h = float(tf_1h_slice["close"].rolling(35).mean().iloc[-1]) if len(tf_1h_slice) >= 35 else entry_price

                        intent = evaluate_entry(
                            sig,
                            state.equity,
                            current_tranche=current_tranche,
                            inputs=EntryInputs(
                                entry_price=entry_price,
                                atr_1h=atr_1h_val,
                                swing_ref=swing_ref,
                                ma35_1h=ma35_1h,
                            ),
                            avg_entry=avg_entry,
                            current_price=bar_close,
                        )
                        if intent is not None:
                            state.pending_order = PendingOrder(
                                side=intent.side,
                                limit_price=intent.limit_price,
                                bar_idx=bar_idx,
                                sizing=intent.sizing,
                                initial_risk=intent.initial_risk,
                                tranche_index=intent.tranche_index,
                            )

        # Record equity curve every 48 bars (~24h)
        if bar_idx % 48 == 0:
            state.equity_curve.append((bar_time_str, round(state.equity, 2)))

    # Close any remaining positions at last bar close
    if not sim_bars.empty:
        last_bar = sim_bars.iloc[-1]
        last_time = str(sim_bars.index[-1])
        for pos in list(state.positions):
            _close_position(
                pos, last_bar["close"], last_time, "end_of_period", state,
                fee_rate=TAKER_FEE, bar_idx=len(sim_bars) - 1,
            )
        state.positions.clear()

    state.equity_curve.append((str(end_ts), round(state.equity, 2)))
    return state


# ---------------------------------------------------------------------------
# Metrics computation
# ---------------------------------------------------------------------------

def compute_metrics(state: BacktestState, initial_equity: float) -> dict:
    """Compute summary metrics from backtest state."""
    logs = state.trade_logs
    if not logs:
        return {
            "total_return_pct": 0.0,
            "mdd_pct": 0.0,
            "profit_factor": 0.0,
            "win_rate_pct": 0.0,
            "avg_r": 0.0,
            "trade_count": 0,
            "total_fees": round(state.total_fees, 4),
            "total_funding": round(state.total_funding, 4),
            "total_cost_pct": round((state.total_fees + state.total_funding) / initial_equity * 100.0, 3),
            "liq_approach_count": state.liq_approach_count,
            "liq_forced_reduce_pnl": round(state.liq_forced_reduce_pnl, 4),
            "liq_reduce_would_be_sl": state.liq_reduce_would_be_sl,
            "liq_reduce_ended_win": state.liq_reduce_ended_win,
            "long_trades": 0,
            "short_trades": 0,
            "long_win_pct": 0.0,
            "short_win_pct": 0.0,
            "gross_profit_factor": 0.0,
            "gross_avg_r": 0.0,
        }

    final_equity = state.equity
    total_return = (final_equity - initial_equity) / initial_equity * 100.0

    # MDD from equity curve
    curve_vals = [v for _, v in state.equity_curve]
    peak = initial_equity
    mdd = 0.0
    for v in curve_vals:
        if v > peak:
            peak = v
        dd = (peak - v) / peak * 100.0
        if dd > mdd:
            mdd = dd

    # Win/loss — POSITION-LIFECYCLE, NET basis (P0-2).
    # Each TradeLog row is one position; win == net_pnl > 0.
    wins = [t for t in logs if t.net_pnl > 0]
    losses = [t for t in logs if t.net_pnl <= 0]
    win_rate = len(wins) / len(logs) * 100.0 if logs else 0.0

    # PF on NET $ (gross profit / gross loss, both net of all costs).
    net_profit = sum(t.net_pnl for t in wins)
    net_loss = abs(sum(t.net_pnl for t in losses))
    pf = net_profit / net_loss if net_loss > 0 else float("inf")

    # avg_r uses NET R.
    avg_r = sum(t.r_multiple for t in logs) / len(logs) if logs else 0.0

    # Reference-only gross metrics (do NOT use for pass/fail).
    gross_wins = [t for t in logs if t.gross_pnl > 0]
    gross_losses = [t for t in logs if t.gross_pnl <= 0]
    gp = sum(t.gross_pnl for t in gross_wins)
    gl = abs(sum(t.gross_pnl for t in gross_losses))
    gross_pf = gp / gl if gl > 0 else float("inf")
    gross_avg_r = sum(t.gross_r_multiple for t in logs) / len(logs) if logs else 0.0

    long_trades = [t for t in logs if t.side == "long"]
    short_trades = [t for t in logs if t.side == "short"]
    long_wins = [t for t in long_trades if t.net_pnl > 0]
    short_wins = [t for t in short_trades if t.net_pnl > 0]

    total_costs = state.total_fees + state.total_funding

    return {
        "total_return_pct": round(total_return, 2),
        "mdd_pct": round(mdd, 2),
        "profit_factor": round(pf, 3),              # NET PF — authoritative
        "win_rate_pct": round(win_rate, 1),          # NET, position-lifecycle
        "avg_r": round(avg_r, 3),                    # NET R
        "trade_count": len(logs),
        "total_fees": round(state.total_fees, 4),
        "total_funding": round(state.total_funding, 4),
        "total_cost_pct": round(total_costs / initial_equity * 100.0, 3),
        "liq_approach_count": state.liq_approach_count,
        # 라운드3 C: 강제감축 영향 분리 계측
        "liq_forced_reduce_pnl": round(state.liq_forced_reduce_pnl, 4),
        "liq_reduce_would_be_sl": state.liq_reduce_would_be_sl,
        "liq_reduce_ended_win": state.liq_reduce_ended_win,
        "long_trades": len(long_trades),
        "short_trades": len(short_trades),
        "long_win_pct": round(len(long_wins) / len(long_trades) * 100, 1) if long_trades else 0.0,
        "short_win_pct": round(len(short_wins) / len(short_trades) * 100, 1) if short_trades else 0.0,
        # --- reference-only (gross, pre-cost) ---
        "gross_profit_factor": round(gross_pf, 3),
        "gross_avg_r": round(gross_avg_r, 3),
    }
