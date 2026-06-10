# backtest/engine.py — Event-driven backtester for prism-btc (§3 of D3 spec)
from __future__ import annotations

import sqlite3
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal, Optional
import pandas as pd

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

# Trailing: after 1R, track 1h MA10
TRAILING_TF = "1h"

# Liquidation buffer monitoring threshold (50% of entry→liq gap)
LIQ_MONITOR_FRAC: float = 0.50

# TFs needed for indicators
ALL_TFS = ("30m", "1h", "4h", "12h", "1d", "1w")

# Minimum rows per TF to compute indicators (35 for MA35 + 14 for ATR warm-up)
MIN_ROWS = 50


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class TradeLog:
    trade_id: int
    side: Literal["long", "short"]
    entry_time: str
    entry_price: float
    exit_time: str
    exit_price: float
    qty: float
    leverage: float
    sl_price: float
    exit_reason: str           # "sl", "tp1", "tp2", "tp3", "trailing", "signal_exit", "signal_reduce"
    r_multiple: float          # realized R (exit_pnl / initial_risk)
    fee_paid: float            # total fees (entry + exit)
    funding_paid: float
    tranche_index: int
    liq_price: float


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
    liq_approach_count: int = 0   # SL closer than 50% of entry→liq gap


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


def _get_tf_slice(
    tf_data: dict[str, pd.DataFrame],
    current_30m_time: pd.Timestamp,
    tf: str,
) -> pd.DataFrame:
    """
    Return confirmed rows for `tf` up to (but NOT including) current_30m_time.
    Upper TF candles: only rows whose open_time < current_30m_time (미래참조 금지).
    """
    df = tf_data.get(tf)
    if df is None or df.empty:
        return pd.DataFrame()
    mask = df.index < current_30m_time
    return df[mask]


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


def _close_position(
    pos: Position,
    exit_price: float,
    exit_time: str,
    exit_reason: str,
    state: BacktestState,
    fee_rate: float = TAKER_FEE + SLIPPAGE_SL,
) -> None:
    """Close a position, book PnL, record trade log."""
    nominal = pos.qty * exit_price
    if pos.side == "long":
        pnl = (exit_price - pos.entry_price) * pos.qty
    else:
        pnl = (pos.entry_price - exit_price) * pos.qty

    exit_fee = nominal * fee_rate
    net_pnl = pnl - exit_fee
    state.equity += net_pnl
    state.total_fees += exit_fee

    r_mult = pnl / pos.initial_risk if pos.initial_risk > 0 else 0.0

    log_entry = TradeLog(
        trade_id=state.trade_id_counter,
        side=pos.side,
        entry_time=pos.entry_time,
        entry_price=pos.entry_price,
        exit_time=exit_time,
        exit_price=exit_price,
        qty=pos.qty,
        leverage=pos.leverage,
        sl_price=pos.sl_price,
        exit_reason=exit_reason,
        r_multiple=round(r_mult, 3),
        fee_paid=round(pos.entry_fee + exit_fee, 6),
        funding_paid=0.0,  # funding tracked separately at position level
        tranche_index=pos.tranche_index,
        liq_price=pos.liq_price,
    )
    state.trade_logs.append(log_entry)
    state.trade_id_counter += 1


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
    # Load all data once
    tf_data: dict[str, pd.DataFrame] = {}
    for tf in ALL_TFS:
        tf_data[tf] = _load_tf_data(conn, tf)

    # Get 30m bars in range
    bars_30m = tf_data["30m"]
    mask = (bars_30m.index >= start_ts) & (bars_30m.index < end_ts)
    sim_bars = bars_30m[mask]

    state = BacktestState(equity=initial_equity)
    state.equity_curve.append((str(start_ts), initial_equity))

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
                )
                state.positions.append(pos)
                state.pending_order = None
            elif bars_elapsed >= ENTRY_ORDER_EXPIRY_BARS:
                # Signal expired — discard
                state.pending_order = None

        # --- 2. Process open positions ---
        positions_to_remove = []
        for pos in state.positions:
            # Funding fee every 16 bars (8h)
            if bar_idx % FUNDING_INTERVAL_BARS == 0:
                funding = pos.qty * bar_close * abs(FUNDING_RATE)
                state.equity -= funding
                state.total_funding += funding

            # Check liq approach (50% buffer breach) — count state transitions only
            gap = abs(pos.entry_price - pos.liq_price)
            if gap > 0:
                if pos.side == "long":
                    sl_to_liq = pos.sl_price - pos.liq_price
                else:
                    sl_to_liq = pos.liq_price - pos.sl_price
                in_breach = (sl_to_liq / gap) < LIQ_MONITOR_FRAC
                if in_breach and not pos.liq_breach_flagged:
                    state.liq_approach_count += 1
                    pos.liq_breach_flagged = True
                elif not in_breach:
                    pos.liq_breach_flagged = False

            # Trailing stop: after 1R hit, track 1h MA10
            if pos.trailing_active:
                # Get current 1h MA10 as trailing stop
                tf_1h = _get_tf_slice(tf_data, bar_time, TRAILING_TF)
                if len(tf_1h) >= 10:
                    trailing_ma10 = tf_1h["close"].rolling(10).mean().iloc[-1]
                    if not pd.isna(trailing_ma10):
                        if pos.side == "long":
                            new_sl = max(pos.sl_price, trailing_ma10)
                            pos.sl_price = new_sl
                        else:
                            new_sl = min(pos.sl_price, trailing_ma10)
                            pos.sl_price = new_sl

            # SL hit check
            sl_hit = False
            if pos.side == "long" and bar_low <= pos.sl_price:
                sl_hit = True
            elif pos.side == "short" and bar_high >= pos.sl_price:
                sl_hit = True

            if sl_hit:
                _close_position(
                    pos, pos.sl_price, bar_time_str, "sl", state,
                    fee_rate=TAKER_FEE + SLIPPAGE_SL,
                )
                positions_to_remove.append(pos)
                continue

            # TP1 hit
            if not pos.tp1_hit:
                tp1_hit = False
                if pos.side == "long" and bar_high >= pos.tp1_price:
                    tp1_hit = True
                elif pos.side == "short" and bar_low <= pos.tp1_price:
                    tp1_hit = True
                if tp1_hit:
                    # Close 1/3 at TP1
                    close_qty = pos.qty / 3.0
                    nominal = close_qty * pos.tp1_price
                    exit_fee = nominal * MAKER_FEE
                    if pos.side == "long":
                        pnl = (pos.tp1_price - pos.entry_price) * close_qty
                    else:
                        pnl = (pos.entry_price - pos.tp1_price) * close_qty
                    state.equity += pnl - exit_fee
                    state.total_fees += exit_fee
                    pos.qty -= close_qty
                    pos.tp1_hit = True
                    # Set BE stop
                    pos.sl_price = pos.entry_price
                    pos.be_stop_set = True
                    # After 1R → activate trailing
                    pos.trailing_active = True
                    state.trade_logs.append(TradeLog(
                        trade_id=state.trade_id_counter,
                        side=pos.side,
                        entry_time=pos.entry_time,
                        entry_price=pos.entry_price,
                        exit_time=bar_time_str,
                        exit_price=pos.tp1_price,
                        qty=close_qty,
                        leverage=pos.leverage,
                        sl_price=pos.sl_price,
                        exit_reason="tp1",
                        r_multiple=1.0,
                        fee_paid=round(exit_fee, 6),
                        funding_paid=0.0,
                        tranche_index=pos.tranche_index,
                        liq_price=pos.liq_price,
                    ))
                    state.trade_id_counter += 1

            # TP2 hit
            if pos.tp1_hit and not pos.tp2_hit:
                tp2_hit = False
                if pos.side == "long" and bar_high >= pos.tp2_price:
                    tp2_hit = True
                elif pos.side == "short" and bar_low <= pos.tp2_price:
                    tp2_hit = True
                if tp2_hit:
                    close_qty = pos.qty / 2.0  # half of remaining (originally 1/3)
                    nominal = close_qty * pos.tp2_price
                    exit_fee = nominal * MAKER_FEE
                    if pos.side == "long":
                        pnl = (pos.tp2_price - pos.entry_price) * close_qty
                    else:
                        pnl = (pos.entry_price - pos.tp2_price) * close_qty
                    state.equity += pnl - exit_fee
                    state.total_fees += exit_fee
                    pos.qty -= close_qty
                    pos.tp2_hit = True
                    state.trade_logs.append(TradeLog(
                        trade_id=state.trade_id_counter,
                        side=pos.side,
                        entry_time=pos.entry_time,
                        entry_price=pos.entry_price,
                        exit_time=bar_time_str,
                        exit_price=pos.tp2_price,
                        qty=close_qty,
                        leverage=pos.leverage,
                        sl_price=pos.sl_price,
                        exit_reason="tp2",
                        r_multiple=2.0,
                        fee_paid=round(exit_fee, 6),
                        funding_paid=0.0,
                        tranche_index=pos.tranche_index,
                        liq_price=pos.liq_price,
                    ))
                    state.trade_id_counter += 1

            # TP3 hit — full close of remainder
            if pos.tp2_hit:
                tp3_hit = False
                if pos.side == "long" and bar_high >= pos.tp3_price:
                    tp3_hit = True
                elif pos.side == "short" and bar_low <= pos.tp3_price:
                    tp3_hit = True
                if tp3_hit:
                    _close_position(
                        pos, pos.tp3_price, bar_time_str, "tp3", state,
                        fee_rate=MAKER_FEE,
                    )
                    positions_to_remove.append(pos)

        for pos in positions_to_remove:
            if pos in state.positions:
                state.positions.remove(pos)

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
                            fee_rate=TAKER_FEE,
                        )
                        if pos in state.positions:
                            state.positions.remove(pos)
                    elif exit_sig.exit_action == "reduce":
                        # Reduce by half
                        close_qty = pos.qty * 0.5
                        if close_qty > 0:
                            nominal = close_qty * bar_close
                            exit_fee = nominal * TAKER_FEE
                            if pos.side == "long":
                                pnl = (bar_close - pos.entry_price) * close_qty
                            else:
                                pnl = (pos.entry_price - bar_close) * close_qty
                            state.equity += pnl - exit_fee
                            state.total_fees += exit_fee
                            pos.qty -= close_qty
                            if pos.qty <= 0:
                                if pos in state.positions:
                                    state.positions.remove(pos)

                # New entry signal
                sig = generate_signal(snapshot)
                if sig.side != "none":
                    # Check if we already have a position in same direction
                    same_side = [p for p in state.positions if p.side == sig.side]
                    current_tranche = len(same_side)

                    if current_tranche == 0:
                        # Fresh entry
                        entry_price = bar_close  # limit at close price (post-only)
                        tf_1h_slice = _get_tf_slice(tf_data, bar_time, "1h")
                        if len(tf_1h_slice) >= 14:
                            atr_1h = tf_1h_slice["close"].rolling(10).mean()
                            # Use 1h ATR from indicators
                            from engine.indicators import atr as calc_atr
                            atr_series = calc_atr(tf_1h_slice, 14)
                            atr_1h_val = float(atr_series.iloc[-1]) if not pd.isna(atr_series.iloc[-1]) else entry_price * 0.02
                        else:
                            atr_1h_val = entry_price * 0.02

                        # Swing ref: recent 10-bar low/high from 1h
                        tf_1h_slice = _get_tf_slice(tf_data, bar_time, "1h")
                        if len(tf_1h_slice) >= 10:
                            if sig.side == "long":
                                swing_ref = float(tf_1h_slice["low"].iloc[-10:].min())
                            else:
                                swing_ref = float(tf_1h_slice["high"].iloc[-10:].max())
                        else:
                            # Fallback: 2% away
                            swing_ref = entry_price * (0.98 if sig.side == "long" else 1.02)

                        # MA35 from 1h
                        if len(tf_1h_slice) >= 35:
                            ma35_1h = float(tf_1h_slice["close"].rolling(35).mean().iloc[-1])
                        else:
                            ma35_1h = entry_price

                        sz = compute_sizing(
                            side=sig.side,
                            entry=entry_price,
                            abs_score=sig.strength,
                            equity=state.equity,
                            atr_1h=atr_1h_val,
                            swing_ref=swing_ref,
                            ma35_1h=ma35_1h,
                            tranche_index=0,
                        )

                        if not sz.rejected and sz.qty > 0:
                            risk_cap = state.equity * 0.02 * TRANCHE_FRACS[0]
                            po = PendingOrder(
                                side=sig.side,
                                limit_price=entry_price,
                                bar_idx=bar_idx,
                                sizing=sz,
                                initial_risk=risk_cap,
                                tranche_index=0,
                            )
                            state.pending_order = po

                    elif current_tranche < 3:
                        # Pyramid check
                        avg_entry = sum(p.entry_price for p in same_side) / len(same_side)
                        if can_add_tranche(current_tranche, avg_entry, bar_close, sig.side):
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

                            sz = compute_sizing(
                                side=sig.side,
                                entry=entry_price,
                                abs_score=sig.strength,
                                equity=state.equity,
                                atr_1h=atr_1h_val,
                                swing_ref=swing_ref,
                                ma35_1h=ma35_1h,
                                tranche_index=current_tranche,
                            )
                            if not sz.rejected and sz.qty > 0:
                                risk_cap = state.equity * 0.02 * TRANCHE_FRACS[current_tranche]
                                po = PendingOrder(
                                    side=sig.side,
                                    limit_price=entry_price,
                                    bar_idx=bar_idx,
                                    sizing=sz,
                                    initial_risk=risk_cap,
                                    tranche_index=current_tranche,
                                )
                                state.pending_order = po

        # Record equity curve every 48 bars (~24h)
        if bar_idx % 48 == 0:
            state.equity_curve.append((bar_time_str, round(state.equity, 2)))

    # Close any remaining positions at last bar close
    if not sim_bars.empty:
        last_bar = sim_bars.iloc[-1]
        last_time = str(sim_bars.index[-1])
        for pos in list(state.positions):
            _close_position(pos, last_bar["close"], last_time, "end_of_period", state, fee_rate=TAKER_FEE)
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
            "liq_approach_count": state.liq_approach_count,
            "long_trades": 0,
            "short_trades": 0,
            "long_win_pct": 0.0,
            "short_win_pct": 0.0,
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

    # Win/loss
    wins = [t for t in logs if t.r_multiple > 0]
    losses = [t for t in logs if t.r_multiple <= 0]
    win_rate = len(wins) / len(logs) * 100.0 if logs else 0.0

    gross_profit = sum(t.r_multiple for t in wins)
    gross_loss = abs(sum(t.r_multiple for t in losses))
    pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    avg_r = sum(t.r_multiple for t in logs) / len(logs) if logs else 0.0

    long_trades = [t for t in logs if t.side == "long"]
    short_trades = [t for t in logs if t.side == "short"]
    long_wins = [t for t in long_trades if t.r_multiple > 0]
    short_wins = [t for t in short_trades if t.r_multiple > 0]

    return {
        "total_return_pct": round(total_return, 2),
        "mdd_pct": round(mdd, 2),
        "profit_factor": round(pf, 3),
        "win_rate_pct": round(win_rate, 1),
        "avg_r": round(avg_r, 3),
        "trade_count": len(logs),
        "total_fees": round(state.total_fees, 4),
        "total_funding": round(state.total_funding, 4),
        "liq_approach_count": state.liq_approach_count,
        "long_trades": len(long_trades),
        "short_trades": len(short_trades),
        "long_win_pct": round(len(long_wins) / len(long_trades) * 100, 1) if long_trades else 0.0,
        "short_win_pct": round(len(short_wins) / len(short_trades) * 100, 1) if short_trades else 0.0,
    }
