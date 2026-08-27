"""Round 9: target-return portfolio research with execution constraints.

Design and verdict: ``research/round9_target_return_design.md``.

This module does not change production sizing.  It converts the current main
lane and the fixed Round-6 swing Lane B into a shared-equity event stream, then
evaluates one pre-registered risk axis while enforcing portfolio heat and gross
notional limits.  The simulator is deliberately closed-trade based; reported
MDD is therefore accompanied by the 1.5x intra-trade stress ratio observed in
the earlier joint simulation.

Run from ``prism-btc`` with the Python 3.12 backtest environment::

    ../.venv-bt/bin/python -m analysis.round9_target_return
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from typing import Literal

import numpy as np
import pandas as pd

from backtest.engine import compute_metrics, run_backtest
from collector.store import get_connection
from engine import sizing
from engine.config import SWING_MAX_LEVERAGE, SWING_STOP_ATR_MULT
from engine.indicators import add_indicators
from engine.sizing import TRANCHE_FRACS

TARGET_TOTAL_RETURN_PCT = 1590.0
TARGET_FINAL_MULTIPLE = 1.0 + TARGET_TOTAL_RETURN_PCT / 100.0
CORE_RISK_UNIT = 0.02
SWING_RISK_UNIT = 0.01
SWING_ROUND_TRIP_COST = 0.0015
DEFAULT_G_VALUES = tuple(round(1.0 + 0.25 * i, 2) for i in range(11))

Side = Literal["long", "short"]


@dataclass(frozen=True)
class PortfolioLimits:
    max_heat: float = 0.10
    max_gross_leverage: float = 8.0
    dd_level_1: float = 0.10
    dd_level_2: float = 0.15
    dd_level_3: float = 0.20
    dd_hard_stop: float = 0.25
    dd_scale_1: float = 0.75
    dd_scale_2: float = 0.50
    dd_scale_3: float = 0.25


@dataclass(frozen=True)
class TargetTrade:
    lane: str
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    side: Side
    # Account-return fraction per 1.0 of the lane risk budget.
    edge_per_risk: float
    # Stop-loss heat and gross notional per 1.0 of the lane risk budget.
    heat_per_risk: float
    gross_per_risk: float
    lane_gross_cap: float | None = None
    source_id: str = ""


@dataclass
class _OpenTrade:
    trade: TargetTrade
    entry_equity: float
    return_fraction: float
    admitted_heat: float
    admitted_gross: float


@dataclass
class PortfolioResult:
    initial_equity: float
    final_equity: float
    total_return_pct: float
    cagr_pct: float
    mdd_closed_pct: float
    estimated_intra_mdd_pct: float
    profit_factor: float
    win_rate_pct: float
    admitted_trades: int
    cap_scaled_entries: int
    governor_scaled_entries: int
    hard_stop_skips: int
    conflict_skips: int
    max_open_heat: float
    max_open_gross: float
    year_returns_pct: dict[str, float]
    stretch_pass: bool
    equity_curve: list[tuple[str, float]] = field(repr=False)

    def compact(self) -> dict:
        data = _native(asdict(self))
        data.pop("equity_curve", None)
        return data


def _native(value):
    """Recursively convert NumPy scalar results to JSON-safe Python values."""
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {key: _native(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_native(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_native(item) for item in value)
    return value


def target_cagr(total_return_pct: float, years: float) -> float:
    """Return the annualized decimal growth rate for a cumulative target."""
    if years <= 0:
        raise ValueError("years must be positive")
    final_multiple = 1.0 + total_return_pct / 100.0
    if final_multiple <= 0:
        raise ValueError("final multiple must be positive")
    return final_multiple ** (1.0 / years) - 1.0


def drawdown_risk_multiplier(
    equity: float,
    high_watermark: float,
    limits: PortfolioLimits | None = None,
) -> float:
    """Causal risk scale derived from realized equity seen before an entry."""
    limits = limits or PortfolioLimits()
    if high_watermark <= 0 or equity <= 0:
        return 0.0
    # Stabilize exact policy boundaries such as 90/100 == 10% drawdown.
    # Binary floating point can otherwise produce 0.09999999999999998 and
    # delay the governor by one tier at precisely the configured threshold.
    drawdown = round(max(0.0, 1.0 - equity / high_watermark), 12)
    if drawdown >= limits.dd_hard_stop:
        return 0.0
    if drawdown >= limits.dd_level_3:
        return limits.dd_scale_3
    if drawdown >= limits.dd_level_2:
        return limits.dd_scale_2
    if drawdown >= limits.dd_level_1:
        return limits.dd_scale_1
    return 1.0


def admission_scale(
    *,
    open_heat: float,
    open_gross: float,
    requested_heat: float,
    requested_gross: float,
    limits: PortfolioLimits,
) -> float:
    """Scale a new order so both portfolio limits remain satisfied."""
    if requested_heat <= 0 or requested_gross <= 0:
        return 0.0
    heat_room = max(0.0, limits.max_heat - open_heat)
    gross_room = max(0.0, limits.max_gross_leverage - open_gross)
    return max(
        0.0,
        min(1.0, heat_room / requested_heat, gross_room / requested_gross),
    )


def _as_utc(value: pd.Timestamp | str) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def _year_returns(
    initial_equity: float,
    equity_curve: list[tuple[str, float]],
) -> dict[str, float]:
    if not equity_curve:
        return {}
    points = [(_as_utc(ts), float(eq)) for ts, eq in equity_curve]
    points.sort(key=lambda item: item[0])
    first_year = points[0][0].year
    last_year = points[-1][0].year
    returns: dict[str, float] = {}
    previous = initial_equity
    for year in range(first_year, last_year + 1):
        in_year = [eq for ts, eq in points if ts.year == year]
        final = in_year[-1] if in_year else previous
        returns[str(year)] = float(round((final / previous - 1.0) * 100.0, 2))
        previous = final
    return returns


def simulate_portfolio(
    trades: Iterable[TargetTrade],
    *,
    lane_risk: Mapping[str, float],
    limits: PortfolioLimits | None = None,
    initial_equity: float = 10_000.0,
    use_drawdown_governor: bool = True,
) -> PortfolioResult:
    """Simulate shared realized equity with entry-time heat/notional admission.

    Exits are processed before unrelated entries at the same timestamp,
    matching the conservative operational rule that released exposure may fund
    a later signal only after the close is known.  A trade stopped inside its
    entry candle is the exception: its open event necessarily precedes its own
    stop event.  Opposite-direction overlap is rejected, while same-direction
    core/swing overlap is admitted up to caps.
    """
    limits = limits or PortfolioLimits()
    ordered = list(trades)
    events: list[tuple[pd.Timestamp, int, str, int]] = []
    for idx, trade in enumerate(ordered):
        entry_time = _as_utc(trade.entry_time)
        exit_time = _as_utc(trade.exit_time)
        if exit_time < entry_time:
            raise ValueError(f"trade exits before entry: {trade.source_id or idx}")
        same_bar = exit_time == entry_time
        events.append((exit_time, 2 if same_bar else 0, "exit", idx))
        events.append((entry_time, 1, "entry", idx))
    events.sort(key=lambda event: (event[0], event[1], event[3]))

    equity = float(initial_equity)
    peak = equity
    open_heat = 0.0
    open_gross = 0.0
    max_heat = 0.0
    max_gross = 0.0
    open_trades: dict[int, _OpenTrade] = {}
    curve: list[tuple[str, float]] = []
    realized_pnls: list[float] = []
    cap_scaled = 0
    governor_scaled = 0
    hard_stop_skips = 0
    conflict_skips = 0

    for event_time, _, kind, idx in events:
        trade = ordered[idx]
        if kind == "exit":
            position = open_trades.pop(idx, None)
            if position is None:
                continue
            pnl = position.entry_equity * position.return_fraction
            equity += pnl
            realized_pnls.append(pnl)
            open_heat = max(0.0, open_heat - position.admitted_heat)
            open_gross = max(0.0, open_gross - position.admitted_gross)
            peak = max(peak, equity)
            curve.append((str(event_time), equity))
            continue

        configured_risk = float(lane_risk.get(trade.lane, 0.0))
        if configured_risk <= 0 or equity <= 0:
            continue
        open_sides = {position.trade.side for position in open_trades.values()}
        if open_sides and trade.side not in open_sides:
            conflict_skips += 1
            continue

        dd_scale = (
            drawdown_risk_multiplier(equity, peak, limits)
            if use_drawdown_governor
            else 1.0
        )
        if dd_scale <= 0:
            hard_stop_skips += 1
            continue
        if dd_scale < 1.0:
            governor_scaled += 1

        effective_risk = configured_risk * dd_scale
        requested_heat = effective_risk * trade.heat_per_risk
        requested_gross = effective_risk * trade.gross_per_risk

        lane_scale = 1.0
        if trade.lane_gross_cap is not None and requested_gross > trade.lane_gross_cap:
            lane_scale = trade.lane_gross_cap / requested_gross
            requested_heat *= lane_scale
            requested_gross *= lane_scale

        cap_scale = admission_scale(
            open_heat=open_heat,
            open_gross=open_gross,
            requested_heat=requested_heat,
            requested_gross=requested_gross,
            limits=limits,
        )
        total_scale = lane_scale * cap_scale
        if total_scale <= 0:
            cap_scaled += 1
            continue
        if total_scale < 1.0 - 1e-12:
            cap_scaled += 1

        admitted_heat = requested_heat * cap_scale
        admitted_gross = requested_gross * cap_scale
        return_fraction = (
            effective_risk * trade.edge_per_risk * total_scale
        )
        open_trades[idx] = _OpenTrade(
            trade=trade,
            entry_equity=equity,
            return_fraction=return_fraction,
            admitted_heat=admitted_heat,
            admitted_gross=admitted_gross,
        )
        open_heat += admitted_heat
        open_gross += admitted_gross
        max_heat = max(max_heat, open_heat)
        max_gross = max(max_gross, open_gross)

    if curve:
        peak_value = initial_equity
        mdd = 0.0
        for _, value in curve:
            peak_value = max(peak_value, value)
            mdd = min(mdd, value / peak_value - 1.0)
        start_time = min(_as_utc(t.entry_time) for t in ordered)
        end_time = max(_as_utc(t.exit_time) for t in ordered)
        years = max((end_time - start_time).total_seconds() / (365.25 * 86_400), 0.1)
    else:
        mdd = 0.0
        years = 1.0

    gross_profit = sum(pnl for pnl in realized_pnls if pnl > 0)
    gross_loss = abs(sum(pnl for pnl in realized_pnls if pnl <= 0))
    profit_factor = gross_profit / gross_loss if gross_loss else float("inf")
    wins = sum(1 for pnl in realized_pnls if pnl > 0)
    total_return_pct = (equity / initial_equity - 1.0) * 100.0
    cagr = (equity / initial_equity) ** (1.0 / years) - 1.0 if equity > 0 else -1.0
    year_returns = _year_returns(initial_equity, curve)
    positive_years = sum(value > 0 for value in year_returns.values())
    no_year_below_limit = all(value >= -25.0 for value in year_returns.values())
    estimated_intra = abs(mdd) * 1.5
    stretch_pass = (
        equity / initial_equity >= TARGET_FINAL_MULTIPLE
        and abs(mdd) <= 0.30
        and estimated_intra <= 0.45
        and max_heat <= limits.max_heat + 1e-12
        and max_gross <= limits.max_gross_leverage + 1e-12
        and positive_years >= min(3, len(year_returns))
        and no_year_below_limit
    )

    return PortfolioResult(
        initial_equity=round(initial_equity, 4),
        final_equity=round(equity, 4),
        total_return_pct=round(total_return_pct, 2),
        cagr_pct=round(cagr * 100.0, 2),
        mdd_closed_pct=round(mdd * 100.0, 2),
        estimated_intra_mdd_pct=round(estimated_intra * 100.0, 2),
        profit_factor=round(profit_factor, 3) if np.isfinite(profit_factor) else float("inf"),
        win_rate_pct=round(100.0 * wins / len(realized_pnls), 1) if realized_pnls else 0.0,
        admitted_trades=len(realized_pnls),
        cap_scaled_entries=cap_scaled,
        governor_scaled_entries=governor_scaled,
        hard_stop_skips=hard_stop_skips,
        conflict_skips=conflict_skips,
        max_open_heat=round(max_heat, 6),
        max_open_gross=round(max_gross, 6),
        year_returns_pct=year_returns,
        stretch_pass=stretch_pass,
        equity_curve=[(ts, round(value, 4)) for ts, value in curve],
    )


def _main_target_trades(
    db_path: str | None,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> tuple[list[TargetTrade], dict]:
    """Run the authoritative main engine and normalize its actual TradeLogs."""
    reference_risk = sizing.RISK_PER_TRADE
    conn = get_connection(db_path)
    try:
        state = run_backtest(conn, start, end, initial_equity=10_000.0)
    finally:
        conn.close()
    metrics = compute_metrics(state, 10_000.0)

    trades: list[TargetTrade] = []
    for trade in state.trade_logs:
        tranche_frac = TRANCHE_FRACS[min(trade.tranche_index, len(TRANCHE_FRACS) - 1)]
        initial_risk = 0.0
        if abs(trade.r_multiple) > 1e-9:
            initial_risk = abs(trade.net_pnl / trade.r_multiple)
        elif abs(trade.gross_r_multiple) > 1e-9:
            initial_risk = abs(trade.gross_pnl / trade.gross_r_multiple)
        if initial_risk <= 0:
            # Defensive fallback.  It is intentionally conservative and should
            # not be used by normal non-zero-R lifecycle logs.
            initial_risk = trade.qty * trade.entry_price * reference_risk
        gross_per_risk = trade.qty * trade.entry_price * tranche_frac / initial_risk
        trades.append(TargetTrade(
            lane="core",
            entry_time=_as_utc(trade.entry_time),
            exit_time=_as_utc(trade.exit_time),
            side=trade.side,
            edge_per_risk=trade.r_multiple * tranche_frac,
            heat_per_risk=tranche_frac,
            gross_per_risk=max(gross_per_risk, 1e-9),
            source_id=f"core:{trade.trade_id}",
        ))
    return trades, metrics


def _load_indicators(conn: sqlite3.Connection, timeframe: str) -> pd.DataFrame:
    frame = pd.read_sql_query(
        "SELECT open_time, open, high, low, close, volume, turnover "
        "FROM klines WHERE timeframe=? AND confirmed=1 ORDER BY open_time",
        conn,
        params=(timeframe,),
    )
    frame = add_indicators(frame)
    if len(frame) >= 35 and frame["ma10"].isna().all():
        raise RuntimeError(
            f"indicator computation broken for {timeframe}; use .venv-bt"
        )
    frame["dt"] = pd.to_datetime(frame["open_time"], unit="ms", utc=True)
    return frame.dropna(subset=["ma10", "ma35", "atr14"]).reset_index(drop=True)


def _swing_target_trades(
    db_path: str | None,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> list[TargetTrade]:
    """Mirror fixed Round-6 Lane B without parameter search."""
    conn = get_connection(db_path)
    try:
        d4 = _load_indicators(conn, "4h")
        d1 = _load_indicators(conn, "1d")
    finally:
        conn.close()

    d4["cross_up"] = (
        (d4["ma10"] > d4["ma35"])
        & (d4["ma10"].shift(1) <= d4["ma35"].shift(1))
    )
    d4["cross_dn"] = (
        (d4["ma10"] < d4["ma35"])
        & (d4["ma10"].shift(1) >= d4["ma35"].shift(1))
    )
    d1_close_time = d1["open_time"].to_numpy() + 86_400_000
    d4_close_time = d4["open_time"].to_numpy() + 4 * 3_600_000
    i1 = np.searchsorted(d1_close_time, d4_close_time, side="right") - 1

    open_ = d4["open"].to_numpy(dtype=float)
    high = d4["high"].to_numpy(dtype=float)
    low = d4["low"].to_numpy(dtype=float)
    close = d4["close"].to_numpy(dtype=float)
    atr = d4["atr14"].to_numpy(dtype=float)
    ma35 = d4["ma35"].to_numpy(dtype=float)
    ma10_1d = d1["ma10"].to_numpy(dtype=float)
    ma35_1d = d1["ma35"].to_numpy(dtype=float)
    cross_up = d4["cross_up"].to_numpy(dtype=bool)
    cross_dn = d4["cross_dn"].to_numpy(dtype=bool)
    dts = d4["dt"].tolist()

    raw: list[tuple[int, int, int, float, float, float]] = []
    side = 0
    entry = stop = stop_pct = 0.0
    entry_idx = -1
    for i in range(40, len(d4) - 1):
        if side == 0:
            k = int(i1[i])
            if k < 35:
                continue
            signal_side = 0
            if cross_up[i] and ma10_1d[k] > ma35_1d[k] and close[i] > ma35[i]:
                signal_side = 1
            elif cross_dn[i] and ma10_1d[k] <= ma35_1d[k] and close[i] < ma35[i]:
                signal_side = -1
            if signal_side:
                side = signal_side
                entry_idx = i + 1
                entry = open_[entry_idx]
                stop = entry - side * SWING_STOP_ATR_MULT * atr[i]
                stop_pct = abs(entry - stop) / entry
            continue

        j = i
        if j < entry_idx:
            continue
        stop_hit = (side > 0 and low[j] <= stop) or (side < 0 and high[j] >= stop)
        if stop_hit:
            raw.append((entry_idx, j, side, entry, stop, stop_pct))
            side = 0
            continue
        rule_exit = (side > 0 and close[j] < ma35[j]) or (side < 0 and close[j] > ma35[j])
        if rule_exit and j + 1 < len(d4):
            raw.append((entry_idx, j + 1, side, entry, open_[j + 1], stop_pct))
            side = 0

    trades: list[TargetTrade] = []
    for seq, (entry_i, exit_i, direction, entry_price, exit_price, sl_pct) in enumerate(raw):
        entry_time = _as_utc(dts[entry_i])
        exit_time = _as_utc(dts[exit_i])
        if entry_time < start or entry_time >= end:
            continue
        asset_net_return = direction * (exit_price - entry_price) / entry_price - SWING_ROUND_TRIP_COST
        trades.append(TargetTrade(
            lane="swing",
            entry_time=entry_time,
            exit_time=exit_time,
            side="long" if direction > 0 else "short",
            edge_per_risk=asset_net_return / sl_pct,
            heat_per_risk=1.0,
            gross_per_risk=1.0 / sl_pct,
            lane_gross_cap=SWING_MAX_LEVERAGE,
            source_id=f"swing:{seq}",
        ))
    return trades


def run_frontier(
    *,
    db_path: str | None,
    start: str,
    end: str,
    g_values: Iterable[float] = DEFAULT_G_VALUES,
    limits: PortfolioLimits | None = None,
) -> dict:
    limits = limits or PortfolioLimits()
    start_ts = _as_utc(start)
    end_ts = _as_utc(end)
    main_trades, main_metrics = _main_target_trades(db_path, start_ts, end_ts)
    swing_trades = _swing_target_trades(db_path, start_ts, end_ts)
    trades = main_trades + swing_trades

    rows: list[dict] = []
    for g in g_values:
        risks = {"core": CORE_RISK_UNIT * g, "swing": SWING_RISK_UNIT * g}
        raw = simulate_portfolio(
            trades,
            lane_risk=risks,
            limits=limits,
            use_drawdown_governor=False,
        )
        governed = simulate_portfolio(
            trades,
            lane_risk=risks,
            limits=limits,
            use_drawdown_governor=True,
        )
        rows.append({
            "g": g,
            "core_risk_pct": round(risks["core"] * 100.0, 2),
            "swing_risk_pct": round(risks["swing"] * 100.0, 2),
            "raw": raw.compact(),
            "governed": governed.compact(),
        })

    passing = [row for row in rows if row["governed"]["stretch_pass"]]
    selected = min(passing, key=lambda row: row["g"])["g"] if passing else None
    return {
        "window": {"start": str(start_ts), "end": str(end_ts)},
        "target": {
            "total_return_pct": TARGET_TOTAL_RETURN_PCT,
            "final_multiple": TARGET_FINAL_MULTIPLE,
            "four_year_cagr_pct": round(target_cagr(TARGET_TOTAL_RETURN_PCT, 4.0) * 100.0, 2),
        },
        "limits": asdict(limits),
        "main_reference_metrics": main_metrics,
        "source_trade_count": {"core": len(main_trades), "swing": len(swing_trades)},
        "frontier": rows,
        "minimum_passing_g": selected,
    }


def _print_frontier(result: dict) -> None:
    print(
        "g   core  swing | raw return | governed return  CAGR  MDD(closed/intra*) "
        "PF  heat gross caps gov PASS"
    )
    print("-" * 118)
    for row in result["frontier"]:
        raw = row["raw"]
        metrics = row["governed"]
        print(
            f"{row['g']:>3.2f} {row['core_risk_pct']:>5.2f}% {row['swing_risk_pct']:>5.2f}% | "
            f"{raw['total_return_pct']:>8.1f}% | "
            f"{metrics['total_return_pct']:>9.1f}% {metrics['cagr_pct']:>6.1f}% "
            f"{metrics['mdd_closed_pct']:>7.1f}%/{metrics['estimated_intra_mdd_pct']:>5.1f}% "
            f"{metrics['profit_factor']:>4} {metrics['max_open_heat']*100:>4.1f}% "
            f"{metrics['max_open_gross']:>4.1f}x {metrics['cap_scaled_entries']:>4} "
            f"{metrics['governor_scaled_entries']:>3} "
            f"{'YES' if metrics['stretch_pass'] else 'NO'}"
        )
    print(f"minimum passing g: {result['minimum_passing_g']}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=None)
    parser.add_argument("--from", dest="start", default="2022-01-01")
    parser.add_argument("--to", dest="end", default="2025-12-31")
    parser.add_argument("--json", action="store_true", help="print full JSON to stdout")
    args = parser.parse_args()
    result = run_frontier(db_path=args.db, start=args.start, end=args.end)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        _print_frontier(result)


if __name__ == "__main__":
    main()
