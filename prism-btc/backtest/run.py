# backtest/run.py — CLI runner for prism-btc backtester
# Usage: python -m backtest.run --from 2022-01-01 --to 2022-12-31
#        python -m backtest.run --three-periods   (runs 3 standard periods)
from __future__ import annotations

import argparse
import json
import csv
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from collector.store import get_connection
from backtest.engine import run_backtest, compute_metrics

log = logging.getLogger(__name__)

RESULTS_DIR = Path(__file__).parent / "results"

THREE_PERIODS = [
    ("2022-01-01", "2022-12-31", "2022_bear"),
    ("2023-01-01", "2023-12-31", "2023_sideways"),
    ("2024-01-01", "2025-12-31", "2024_2025_bull"),
]

PASS_CRITERIA = {
    "mdd_pct": ("<", 25.0),
    "profit_factor": (">", 1.3),
    "liq_approach_count": ("==", 0),
}

INITIAL_EQUITY = 10_000.0


def _parse_ts(s: str) -> pd.Timestamp:
    return pd.Timestamp(s, tz="UTC")


def _save_results(label: str, metrics: dict, trade_logs: list) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    # JSON metrics
    metrics_path = RESULTS_DIR / f"{label}_metrics.json"
    with open(metrics_path, "w") as f:
        json.dump(metrics, f, indent=2)

    # CSV trade log
    if trade_logs:
        csv_path = RESULTS_DIR / f"{label}_trades.csv"
        fields = [
            "trade_id", "side", "entry_time", "entry_price", "exit_time",
            "exit_price", "qty", "leverage", "sl_price", "exit_reason",
            "r_multiple", "fee_paid", "funding_paid", "tranche_index", "liq_price",
        ]
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for t in trade_logs:
                writer.writerow({k: getattr(t, k) for k in fields})


def _evaluate_pass(metrics: dict) -> dict[str, bool]:
    results = {}
    for key, (op, threshold) in PASS_CRITERIA.items():
        val = metrics.get(key, None)
        if val is None:
            results[key] = False
            continue
        if op == "<":
            results[key] = val < threshold
        elif op == ">":
            results[key] = val > threshold
        elif op == "==":
            results[key] = val == threshold
    return results


def _print_metrics_table(label: str, metrics: dict, pass_eval: dict[str, bool]) -> None:
    sep = "-" * 62
    print(f"\n{'='*62}")
    print(f"  구간: {label}")
    print(sep)
    print(f"  {'지표':<30} {'값':>12}  {'합격':>6}")
    print(sep)

    rows = [
        ("총수익률(%)", "total_return_pct", None),
        ("MDD(%)", "mdd_pct", "mdd_pct"),
        ("Profit Factor", "profit_factor", "profit_factor"),
        ("승률(%)", "win_rate_pct", None),
        ("평균 R", "avg_r", None),
        ("트레이드 수", "trade_count", None),
        ("수수료 합계($)", "total_fees", None),
        ("펀딩 합계($)", "total_funding", None),
        ("청산가접근횟수", "liq_approach_count", "liq_approach_count"),
        ("강제감축PnL영향($)", "liq_forced_reduce_pnl", None),
        ("감축→SL추정 건수", "liq_reduce_would_be_sl", None),
        ("감축→수익마감 건수", "liq_reduce_ended_win", None),
        ("롱 트레이드 수", "long_trades", None),
        ("숏 트레이드 수", "short_trades", None),
        ("롱 승률(%)", "long_win_pct", None),
        ("숏 승률(%)", "short_win_pct", None),
    ]

    for name, key, pass_key in rows:
        val = metrics.get(key, "N/A")
        if pass_key and pass_key in pass_eval:
            passed = pass_eval[pass_key]
            mark = "PASS" if passed else "FAIL"
        else:
            mark = ""
        print(f"  {name:<30} {str(val):>12}  {mark:>6}")

    print(sep)
    overall = all(pass_eval.values())
    print(f"  종합 합격: {'PASS' if overall else 'FAIL'}")
    print(f"{'='*62}")


def run_period(
    db_path: str | None,
    start: str,
    end: str,
    label: str,
) -> dict:
    conn = get_connection(db_path)
    start_ts = _parse_ts(start)
    end_ts = _parse_ts(end)

    print(f"\n[backtest] {label}: {start} ~ {end} ...")
    state = run_backtest(conn, start_ts, end_ts, initial_equity=INITIAL_EQUITY)
    conn.close()

    metrics = compute_metrics(state, INITIAL_EQUITY)
    pass_eval = _evaluate_pass(metrics)
    _print_metrics_table(label, metrics, pass_eval)
    _save_results(label, metrics, state.trade_logs)

    return metrics


def main() -> None:
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="prism-btc backtester",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--from", dest="from_date", help="Start date YYYY-MM-DD")
    parser.add_argument("--to", dest="to_date", help="End date YYYY-MM-DD")
    parser.add_argument(
        "--three-periods",
        action="store_true",
        help="Run 3 standard periods: 2022 bear / 2023 sideways / 2024-2025 bull",
    )
    parser.add_argument("--db", dest="db_path", default=None, help="Path to market.db")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.INFO)

    if args.three_periods:
        all_metrics = []
        for start, end, label in THREE_PERIODS:
            m = run_period(args.db_path, start, end, label)
            all_metrics.append((label, m))

        # Summary table
        print("\n" + "=" * 62)
        print("  3구간 종합 요약")
        print("=" * 62)
        header = f"  {'구간':<22} {'수익%':>7} {'MDD%':>6} {'PF':>6} {'승률%':>6} {'트레이드':>8} {'청산접근':>8}"
        print(header)
        print("-" * 62)
        for label, m in all_metrics:
            pass_eval = _evaluate_pass(m)
            ok = "OK" if all(pass_eval.values()) else "NG"
            print(
                f"  {label:<22} {m['total_return_pct']:>7.1f} "
                f"{m['mdd_pct']:>6.1f} {m['profit_factor']:>6.3f} "
                f"{m['win_rate_pct']:>6.1f} {m['trade_count']:>8} "
                f"{m['liq_approach_count']:>8}  [{ok}]"
            )
        print("=" * 62)

    elif args.from_date and args.to_date:
        label = f"{args.from_date}_to_{args.to_date}"
        run_period(args.db_path, args.from_date, args.to_date, label)

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
