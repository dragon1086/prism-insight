"""Round 10: causal failure clustering and short-horizon reversal research.

The clustering model is fit only on losing trades entered in 2022-2023.  Its
scaler, four centroids, and harmful-cluster selection are then frozen before
2024-2025 evaluation.  Production strategy code is never modified here.

Design: ``research/round10_failure_cluster_design.md``.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections.abc import Iterable
from dataclasses import asdict, dataclass, replace
from typing import Literal

import numpy as np
import pandas as pd

from analysis.round9_target_return import (
    PortfolioLimits,
    TargetTrade,
    _main_target_trades,
    _native,
    _swing_target_trades,
    simulate_portfolio,
)
from backtest.engine import SLIPPAGE_SL, TAKER_FEE, _load_tf_data
from collector.store import get_connection
from engine.indicators import add_indicators

FEATURE_COLUMNS = (
    "ts_4h",
    "ts_1d",
    "extension_4h",
    "ret_24h",
    "atr_ratio_4h",
    "volume_ratio_4h",
    "range_ratio_4h",
    "funding_z",
    "oi_change_6h",
    "lane_swing",
)
K_CLUSTERS = 4
TRAIN_START = pd.Timestamp("2022-01-01", tz="UTC")
TRAIN_END = pd.Timestamp("2024-01-01", tz="UTC")
TEST_START = TRAIN_END
TEST_END = pd.Timestamp("2026-01-01", tz="UTC")

CORE_RISK_G275 = 0.055
SWING_RISK_G275 = 0.0275
REVERSAL_RISK = 0.005
REVERSAL_STOP_ATR = 1.5
REVERSAL_TARGET_R = 1.5
REVERSAL_MAX_HOLD_BARS = 24
REVERSAL_COOLDOWN = pd.Timedelta(hours=12)
REVERSAL_MAX_GROSS = 3.0


@dataclass(frozen=True)
class ClusterModel:
    center: dict[str, float]
    scale: dict[str, float]
    centroids: list[list[float]]


def fit_robust_scaler(
    frame: pd.DataFrame,
    columns: Iterable[str] = FEATURE_COLUMNS,
) -> tuple[pd.Series, pd.Series]:
    """Fit median/IQR on the training-loss sample only."""
    cols = list(columns)
    numeric = frame[cols].apply(pd.to_numeric, errors="coerce")
    center = numeric.median()
    filled = numeric.fillna(center)
    scale = filled.quantile(0.75) - filled.quantile(0.25)
    scale = scale.mask(scale.abs() < 1e-12, 1.0).fillna(1.0)
    return center.fillna(0.0), scale


def transform_robust(
    frame: pd.DataFrame,
    center: pd.Series,
    scale: pd.Series,
    columns: Iterable[str] = FEATURE_COLUMNS,
) -> np.ndarray:
    """Transform using a previously fitted scaler; never refit on validation."""
    cols = list(columns)
    numeric = frame[cols].apply(pd.to_numeric, errors="coerce")
    normalized = (numeric.fillna(center) - center) / scale
    return normalized.to_numpy(dtype=float)


def _nearest(points: np.ndarray, centroids: np.ndarray) -> np.ndarray:
    distances = ((points[:, None, :] - centroids[None, :, :]) ** 2).sum(axis=2)
    return distances.argmin(axis=1)


def deterministic_kmeans(
    points: np.ndarray,
    *,
    k: int = K_CLUSTERS,
    max_iter: int = 100,
    tolerance: float = 1e-8,
) -> tuple[np.ndarray, np.ndarray]:
    """Small deterministic k-means with farthest-point initialization."""
    data = np.asarray(points, dtype=float)
    if data.ndim != 2 or len(data) < k or k <= 0:
        raise ValueError("k-means requires a 2D array with n >= k > 0")
    median = np.median(data, axis=0)
    first = int(np.argmax(((data - median) ** 2).sum(axis=1)))
    selected = [first]
    while len(selected) < k:
        chosen = data[selected]
        min_distance = ((data[:, None, :] - chosen[None, :, :]) ** 2).sum(axis=2).min(axis=1)
        min_distance[selected] = -1.0
        selected.append(int(np.argmax(min_distance)))
    centroids = data[selected].copy()

    for _ in range(max_iter):
        labels = _nearest(data, centroids)
        updated = centroids.copy()
        for cluster in range(k):
            members = data[labels == cluster]
            if len(members):
                updated[cluster] = members.mean(axis=0)
            else:
                distances = ((data - centroids[labels]) ** 2).sum(axis=1)
                updated[cluster] = data[int(np.argmax(distances))]
        if float(np.abs(updated - centroids).max()) < tolerance:
            centroids = updated
            break
        centroids = updated
    return centroids, _nearest(data, centroids)


def select_harmful_clusters(stats: Iterable[dict]) -> set[int]:
    """Apply the four pre-registered harmful-cluster gates."""
    harmful: set[int] = set()
    for row in stats:
        years = {int(year) for year in row.get("years", [])}
        if (
            int(row.get("n", 0)) >= 12
            and float(row.get("profit_factor", float("inf"))) < 0.90
            and float(row.get("avg_r", 0.0)) < -0.15
            and {2022, 2023}.issubset(years)
        ):
            harmful.add(int(row["cluster"]))
    return harmful


def apply_trade_guard(
    trades: Iterable[TargetTrade],
    cluster_by_source: dict[str, int],
    harmful_clusters: set[int],
    *,
    mode: Literal["block", "half"],
) -> list[TargetTrade]:
    """Return research-only blocked or half-sized copies of target trades."""
    guarded: list[TargetTrade] = []
    for trade in trades:
        harmful = cluster_by_source.get(trade.source_id) in harmful_clusters
        if harmful and mode == "block":
            continue
        if harmful and mode == "half":
            trade = replace(
                trade,
                edge_per_risk=trade.edge_per_risk * 0.5,
                heat_per_risk=trade.heat_per_risk * 0.5,
                gross_per_risk=trade.gross_per_risk * 0.5,
            )
        guarded.append(trade)
    return guarded


def apply_pyramid_block(
    trades: Iterable[TargetTrade],
    cluster_by_source: dict[str, int],
    warning_clusters: set[int],
) -> list[TargetTrade]:
    """Keep initial/swing entries but suppress warned core add-on tranches."""
    return [
        trade
        for trade in trades
        if not (
            cluster_by_source.get(trade.source_id) in warning_clusters
            and trade.lane == "core"
            and trade.heat_per_risk < 0.4
        )
    ]


def _utc(value) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def simulate_reversal_lane(
    signals: pd.DataFrame,
    bars_30m: pd.DataFrame,
    *,
    harmful_clusters: set[int],
) -> list[TargetTrade]:
    """Run the single pre-registered opposite-direction 12h event lane."""
    if not harmful_clusters or signals.empty or bars_30m.empty:
        return []
    bars = bars_30m.sort_index()
    index = pd.DatetimeIndex([_utc(value) for value in bars.index])
    next_available = pd.Timestamp.min.tz_localize("UTC")
    trades: list[TargetTrade] = []

    for row in signals.sort_values("entry_time").itertuples(index=False):
        if int(row.cluster) not in harmful_clusters:
            continue
        signal_time = _utc(row.entry_time)
        if signal_time < next_available:
            continue
        entry_idx = int(index.searchsorted(signal_time, side="right"))
        if entry_idx >= len(bars):
            continue
        atr_value = float(bars.iloc[max(0, entry_idx - 1)].get("atr14", 0.0))
        entry = float(bars.iloc[entry_idx]["open"])
        if entry <= 0 or atr_value <= 0:
            continue

        original_side = str(row.side)
        side: Literal["long", "short"] = "short" if original_side == "long" else "long"
        direction = 1.0 if side == "long" else -1.0
        stop_distance = REVERSAL_STOP_ATR * atr_value
        stop = entry - direction * stop_distance
        target = entry + direction * REVERSAL_TARGET_R * stop_distance
        exit_idx = min(entry_idx + REVERSAL_MAX_HOLD_BARS - 1, len(bars) - 1)
        exit_price = float(bars.iloc[exit_idx]["close"])

        for idx in range(entry_idx, exit_idx + 1):
            bar = bars.iloc[idx]
            stop_hit = (
                float(bar["low"]) <= stop
                if side == "long"
                else float(bar["high"]) >= stop
            )
            target_hit = (
                float(bar["high"]) >= target
                if side == "long"
                else float(bar["low"]) <= target
            )
            if stop_hit:
                exit_price = stop * (1 - SLIPPAGE_SL if side == "long" else 1 + SLIPPAGE_SL)
                exit_idx = idx
                break
            if target_hit:
                exit_price = target
                exit_idx = idx
                break

        stop_pct = stop_distance / entry
        net_asset_return = direction * (exit_price - entry) / entry - 2 * TAKER_FEE
        trades.append(TargetTrade(
            lane="reversal",
            entry_time=index[entry_idx],
            exit_time=index[exit_idx],
            side=side,
            edge_per_risk=net_asset_return / stop_pct,
            heat_per_risk=1.0,
            gross_per_risk=1.0 / stop_pct,
            lane_gross_cap=REVERSAL_MAX_GROSS,
            source_id=f"reversal:{row.source_id}",
        ))
        next_available = index[exit_idx] + REVERSAL_COOLDOWN
    return trades


def _prepare_timeframe(
    conn: sqlite3.Connection,
    timeframe: str,
    duration: pd.Timedelta,
) -> pd.DataFrame:
    frame = add_indicators(_load_tf_data(conn, timeframe)).copy()
    frame["effective"] = frame.index + duration
    return frame.reset_index(drop=False)


def _feature_context(
    main_db_path: str | None,
    event_db_path: str,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    main_conn = get_connection(main_db_path)
    try:
        four_h = _prepare_timeframe(main_conn, "4h", pd.Timedelta(hours=4))
        one_d = _prepare_timeframe(main_conn, "1d", pd.Timedelta(days=1))
        funding = pd.read_sql_query(
            "SELECT funding_time, rate FROM funding ORDER BY funding_time",
            main_conn,
        )
        bars_30m = add_indicators(_load_tf_data(main_conn, "30m"))
    finally:
        main_conn.close()

    four_h["ts_4h"] = (four_h["ma10"] - four_h["ma35"]).abs() / four_h["atr14"]
    four_h["extension_raw"] = (four_h["close"] - four_h["ma35"]) / four_h["atr14"]
    four_h["ret_24h_raw"] = four_h["close"] / four_h["close"].shift(6) - 1.0
    volume_base = four_h["volume"].shift(1).rolling(20, min_periods=10).median()
    four_h["volume_ratio_4h"] = four_h["volume"] / volume_base
    four_h["range_ratio_4h"] = (four_h["high"] - four_h["low"]) / four_h["atr14"]
    four_h["atr_ratio_4h"] = four_h["atr14"] / four_h["close"]
    one_d["ts_1d"] = (one_d["ma10"] - one_d["ma35"]).abs() / one_d["atr14"]

    funding["effective_funding"] = pd.to_datetime(
        funding["funding_time"], unit="ms", utc=True
    )
    prior = funding["rate"].shift(1)
    median = prior.rolling(180, min_periods=60).median()
    iqr = (
        prior.rolling(180, min_periods=60).quantile(0.75)
        - prior.rolling(180, min_periods=60).quantile(0.25)
    ).replace(0.0, np.nan)
    funding["funding_z_raw"] = (funding["rate"] - median) / iqr

    event_conn = get_connection(event_db_path)
    try:
        oi = pd.read_sql_query(
            "SELECT timestamp, open_interest FROM open_interest ORDER BY timestamp",
            event_conn,
        )
    finally:
        event_conn.close()
    oi["effective_oi"] = pd.to_datetime(oi["timestamp"], unit="ms", utc=True)
    oi["oi_change_6h"] = oi["open_interest"] / oi["open_interest"].shift(6) - 1.0
    return four_h, one_d, funding, oi, bars_30m


def enrich_trades(
    trades: Iterable[TargetTrade],
    *,
    main_db_path: str | None,
    event_db_path: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Attach only information available before each trade entry."""
    rows = []
    for trade in trades:
        heat = float(trade.heat_per_risk)
        rows.append({
            "source_id": trade.source_id,
            "lane": trade.lane,
            "entry_time": _utc(trade.entry_time),
            "exit_time": _utc(trade.exit_time),
            "side": trade.side,
            "r_multiple": float(trade.edge_per_risk / heat) if heat else 0.0,
        })
    frame = pd.DataFrame(rows).sort_values("entry_time")
    four_h, one_d, funding, oi, bars_30m = _feature_context(
        main_db_path, event_db_path
    )

    frame = pd.merge_asof(
        frame,
        four_h[[
            "effective", "ts_4h", "extension_raw", "ret_24h_raw",
            "atr_ratio_4h", "volume_ratio_4h", "range_ratio_4h",
        ]].sort_values("effective"),
        left_on="entry_time",
        right_on="effective",
        direction="backward",
    )
    frame = pd.merge_asof(
        frame.sort_values("entry_time"),
        one_d[["effective", "ts_1d"]].sort_values("effective"),
        left_on="entry_time",
        right_on="effective",
        direction="backward",
        suffixes=("", "_1d"),
    )
    frame = pd.merge_asof(
        frame.sort_values("entry_time"),
        funding[["effective_funding", "funding_z_raw"]].sort_values("effective_funding"),
        left_on="entry_time",
        right_on="effective_funding",
        direction="backward",
    )
    frame = pd.merge_asof(
        frame.sort_values("entry_time"),
        oi[["effective_oi", "oi_change_6h"]].sort_values("effective_oi"),
        left_on="entry_time",
        right_on="effective_oi",
        direction="backward",
    )
    direction = np.where(frame["side"].eq("long"), 1.0, -1.0)
    frame["extension_4h"] = direction * frame["extension_raw"]
    frame["ret_24h"] = direction * frame["ret_24h_raw"]
    frame["funding_z"] = direction * frame["funding_z_raw"]
    frame["lane_swing"] = frame["lane"].eq("swing").astype(float)
    return frame, bars_30m


def _cluster_stats(frame: pd.DataFrame) -> list[dict]:
    rows: list[dict] = []
    for cluster, group in frame.groupby("cluster", sort=True):
        wins = group.loc[group["r_multiple"] > 0, "r_multiple"].sum()
        losses = abs(group.loc[group["r_multiple"] <= 0, "r_multiple"].sum())
        pf = float(wins / losses) if losses else float("inf")
        medians = {
            feature: round(float(group[feature].median()), 4)
            for feature in FEATURE_COLUMNS
        }
        rows.append({
            "cluster": int(cluster),
            "n": len(group),
            "losses": int((group["r_multiple"] <= 0).sum()),
            "profit_factor": round(pf, 3) if np.isfinite(pf) else float("inf"),
            "avg_r": round(float(group["r_multiple"].mean()), 3),
            "sum_r": round(float(group["r_multiple"].sum()), 3),
            "years": sorted(int(year) for year in group["entry_time"].dt.year.unique()),
            "long_n": int(group["side"].eq("long").sum()),
            "swing_n": int(group["lane"].eq("swing").sum()),
            "feature_medians": medians,
            "label": _cluster_label(medians),
        })
    return rows


def _cluster_label(features: dict[str, float]) -> str:
    if features["extension_4h"] >= 1.5 and features["ret_24h"] > 0:
        return "late_extension"
    if features["funding_z"] >= 1.0:
        return "crowded_funding"
    if features["oi_change_6h"] <= -0.025:
        return "oi_unwind"
    if features["ts_4h"] < 1.5:
        return "weak_trend_chop"
    if features["volume_ratio_4h"] >= 1.5:
        return "volume_shock"
    return "mixed_transition"


def _guard_verdict(baseline: dict, candidate: dict) -> dict:
    base_years = baseline["year_returns_pct"]
    cand_years = candidate["year_returns_pct"]
    checks = {
        "pf_improved": candidate["profit_factor"] > baseline["profit_factor"],
        "mdd_not_worse": abs(candidate["mdd_closed_pct"]) <= abs(baseline["mdd_closed_pct"]),
        "return_preserved": candidate["total_return_pct"] >= baseline["total_return_pct"] * 0.90,
        "trade_count_preserved": candidate["admitted_trades"] >= baseline["admitted_trades"] * 0.70,
        "2024_not_worse": cand_years.get("2024", 0.0) >= base_years.get("2024", 0.0),
        "2025_not_worse": cand_years.get("2025", 0.0) >= base_years.get("2025", 0.0),
    }
    return {"pass": all(checks.values()), "checks": checks}


def _monthly_correlation(
    base_trades: Iterable[TargetTrade],
    reversal_trades: Iterable[TargetTrade],
) -> float | None:
    rows = []
    risks = {"core": CORE_RISK_G275, "swing": SWING_RISK_G275}
    for trade in base_trades:
        month = _utc(trade.exit_time).tz_localize(None).to_period("M")
        rows.append(("base", month, risks[trade.lane] * trade.edge_per_risk))
    for trade in reversal_trades:
        month = _utc(trade.exit_time).tz_localize(None).to_period("M")
        rows.append(("reversal", month, REVERSAL_RISK * trade.edge_per_risk))
    if not rows:
        return None
    data = pd.DataFrame(rows, columns=["series", "month", "value"])
    pivot = data.pivot_table(index="month", columns="series", values="value", aggfunc="sum").fillna(0.0)
    if not {"base", "reversal"}.issubset(pivot.columns) or len(pivot) < 3:
        return None
    corr = pivot["base"].corr(pivot["reversal"])
    return round(float(corr), 3) if not pd.isna(corr) else None


def run_analysis(
    *,
    main_db_path: str | None = None,
    event_db_path: str = "state/btc_research_5m.db",
) -> dict:
    main_trades, main_metrics = _main_target_trades(
        main_db_path, TRAIN_START, TEST_END
    )
    swing_trades = _swing_target_trades(
        main_db_path, TRAIN_START, TEST_END
    )
    all_trades = main_trades + swing_trades
    frame, bars_30m = enrich_trades(
        all_trades,
        main_db_path=main_db_path,
        event_db_path=event_db_path,
    )
    train = frame[(frame["entry_time"] >= TRAIN_START) & (frame["entry_time"] < TRAIN_END)].copy()
    test = frame[(frame["entry_time"] >= TEST_START) & (frame["entry_time"] < TEST_END)].copy()
    train_losses = train[train["r_multiple"] < 0].copy()
    if len(train_losses) < K_CLUSTERS:
        raise RuntimeError("insufficient training losses for four clusters")

    center, scale = fit_robust_scaler(train_losses)
    centroids, _ = deterministic_kmeans(transform_robust(train_losses, center, scale), k=K_CLUSTERS)
    train["cluster"] = _nearest(transform_robust(train, center, scale), centroids)
    test["cluster"] = _nearest(transform_robust(test, center, scale), centroids)
    train_stats = _cluster_stats(train)
    test_stats = _cluster_stats(test)
    harmful = select_harmful_clusters(train_stats)
    # Post-hoc observer only: C1 missed the pre-registered avg-R gate by 0.004R
    # but deteriorated sharply in the already-viewed test set.  Preserve it as
    # an explicit non-promotable shadow hypothesis rather than silently relaxing
    # the original gate.
    warning_clusters = {
        int(row["cluster"])
        for row in train_stats
        if (
            int(row["n"]) >= 12
            and float(row["profit_factor"]) < 0.90
            and float(row["avg_r"]) < 0.0
            and {2022, 2023}.issubset({int(year) for year in row["years"]})
        )
    }

    cluster_by_source = {
        str(row.source_id): int(row.cluster)
        for row in pd.concat([train, test]).itertuples(index=False)
    }
    test_trades = [
        trade for trade in all_trades
        if TEST_START <= _utc(trade.entry_time) < TEST_END
    ]
    risks = {"core": CORE_RISK_G275, "swing": SWING_RISK_G275}
    limits = PortfolioLimits(max_heat=0.10, max_gross_leverage=8.0)
    baseline = simulate_portfolio(
        test_trades, lane_risk=risks, limits=limits,
        use_drawdown_governor=False,
    ).compact()
    block = simulate_portfolio(
        apply_trade_guard(test_trades, cluster_by_source, harmful, mode="block"),
        lane_risk=risks, limits=limits, use_drawdown_governor=False,
    ).compact()
    half = simulate_portfolio(
        apply_trade_guard(test_trades, cluster_by_source, harmful, mode="half"),
        lane_risk=risks, limits=limits, use_drawdown_governor=False,
    ).compact()
    test_pyramid_block = simulate_portfolio(
        apply_pyramid_block(
            test_trades, cluster_by_source, warning_clusters
        ),
        lane_risk=risks, limits=limits, use_drawdown_governor=False,
    ).compact()
    full_baseline = simulate_portfolio(
        all_trades, lane_risk=risks, limits=limits,
        use_drawdown_governor=False,
    ).compact()
    full_pyramid_block = simulate_portfolio(
        apply_pyramid_block(
            all_trades, cluster_by_source, warning_clusters
        ),
        lane_risk=risks, limits=limits, use_drawdown_governor=False,
    ).compact()

    reversal_signals = test[["entry_time", "side", "cluster", "source_id"]]
    reversal_trades = simulate_reversal_lane(
        reversal_signals,
        bars_30m,
        harmful_clusters=harmful,
    )
    reversal_result = simulate_portfolio(
        reversal_trades,
        lane_risk={"reversal": REVERSAL_RISK},
        limits=PortfolioLimits(max_heat=0.01, max_gross_leverage=REVERSAL_MAX_GROSS),
        use_drawdown_governor=False,
    ).compact()
    reversal_avg_r = (
        float(np.mean([trade.edge_per_risk for trade in reversal_trades]))
        if reversal_trades else 0.0
    )
    correlation = _monthly_correlation(test_trades, reversal_trades)
    reversal_checks = {
        "min_trades": len(reversal_trades) >= 20,
        "pf": reversal_result["profit_factor"] > 1.30,
        "avg_r": reversal_avg_r > 0.15,
        "2024_positive": reversal_result["year_returns_pct"].get("2024", 0.0) > 0,
        "2025_positive": reversal_result["year_returns_pct"].get("2025", 0.0) > 0,
        "low_correlation": correlation is not None and correlation < 0.60,
    }

    model = ClusterModel(
        center={key: float(value) for key, value in center.items()},
        scale={key: float(value) for key, value in scale.items()},
        centroids=centroids.tolist(),
    )
    return _native({
        "split": {
            "train": [str(TRAIN_START), str(TRAIN_END)],
            "test": [str(TEST_START), str(TEST_END)],
        },
        "source": {
            "main_reference_metrics": main_metrics,
            "main_trades": len(main_trades),
            "swing_trades": len(swing_trades),
            "train_trades": len(train),
            "train_losses": len(train_losses),
            "test_trades": len(test),
        },
        "model": asdict(model),
        "train_clusters": train_stats,
        "harmful_clusters": sorted(harmful),
        "test_clusters": test_stats,
        "guards": {
            "baseline": baseline,
            "block": {"metrics": block, "verdict": _guard_verdict(baseline, block)},
            "half": {"metrics": half, "verdict": _guard_verdict(baseline, half)},
        },
        "posthoc_shadow_candidate": {
            "warning_clusters": sorted(warning_clusters),
            "rule": "warned cluster: block core add-on tranches only",
            "disclaimer": (
                "selected after viewing 2024-2025; metrics may justify shadow "
                "observation but never production promotion"
            ),
            "test_baseline": baseline,
            "test_candidate": test_pyramid_block,
            "test_metrics_pass": _guard_verdict(
                baseline, test_pyramid_block
            ),
            "full_baseline": full_baseline,
            "full_candidate": full_pyramid_block,
            "promotable": False,
        },
        "reversal_lane": {
            "metrics": reversal_result,
            "avg_r": round(reversal_avg_r, 3),
            "monthly_correlation": correlation,
            "checks": reversal_checks,
            "pass": all(reversal_checks.values()),
        },
    })


def _print_summary(result: dict) -> None:
    print("=== Round 10 failure clusters ===")
    print(f"harmful(train-only): {result['harmful_clusters']}")
    for row in result["train_clusters"]:
        mark = " HARMFUL" if row["cluster"] in result["harmful_clusters"] else ""
        print(
            f"train C{row['cluster']} {row['label']}: n={row['n']} "
            f"PF={row['profit_factor']} avgR={row['avg_r']}{mark}"
        )
    for row in result["test_clusters"]:
        print(
            f"test  C{row['cluster']} {row['label']}: n={row['n']} "
            f"PF={row['profit_factor']} avgR={row['avg_r']}"
        )
    for name in ("baseline", "block", "half"):
        data = result["guards"][name]
        metrics = data if name == "baseline" else data["metrics"]
        verdict = "" if name == "baseline" else f" pass={data['verdict']['pass']}"
        print(
            f"{name}: ret={metrics['total_return_pct']}% PF={metrics['profit_factor']} "
            f"MDD={metrics['mdd_closed_pct']}% n={metrics['admitted_trades']}{verdict}"
        )
    reversal = result["reversal_lane"]
    print(
        f"reversal: n={reversal['metrics']['admitted_trades']} "
        f"ret={reversal['metrics']['total_return_pct']}% "
        f"PF={reversal['metrics']['profit_factor']} avgR={reversal['avg_r']} "
        f"corr={reversal['monthly_correlation']} pass={reversal['pass']}"
    )
    posthoc = result["posthoc_shadow_candidate"]
    print(
        "posthoc pyramid-block (shadow only): "
        f"full {posthoc['full_baseline']['total_return_pct']}% -> "
        f"{posthoc['full_candidate']['total_return_pct']}%, "
        f"MDD {posthoc['full_baseline']['mdd_closed_pct']}% -> "
        f"{posthoc['full_candidate']['mdd_closed_pct']}%, "
        f"promotable={posthoc['promotable']}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=None)
    parser.add_argument("--event-db", default="state/btc_research_5m.db")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = run_analysis(main_db_path=args.db, event_db_path=args.event_db)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        _print_summary(result)


if __name__ == "__main__":
    main()
