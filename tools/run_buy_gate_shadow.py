#!/usr/bin/env python3
"""Run the buy-gate shadow evaluator on as-of scenario samples.

No production DB writes and no LLM calls. The market-data clients are only used
to retrieve OHLCV ending at each sample's buy date.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta
import importlib.util
import json
from pathlib import Path
import sys

from tools.buy_gate_shadow import build_asof_features, normalize_regime, validate_scenario

ROOT = Path(__file__).resolve().parent.parent


def _load_rows(paths: list[str], market: str) -> list[tuple[dict, dict]]:
    rows: list[tuple[dict, dict]] = []
    for raw_path in paths:
        path = Path(raw_path)
        if path.suffix.lower() == ".json":
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                payload = {}
            lines = payload if isinstance(payload, list) else payload.get("rows", [])
        else:
            lines = []
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    lines.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        for outer in lines:
            scenario = outer.get("scenario_json")
            if scenario is None and isinstance(outer.get("scenario"), dict):
                scenario = outer.get("scenario")
            if not isinstance(scenario, dict):
                continue
            row_market = str(outer.get("market", "") or scenario.get("market", "")).lower()
            if not row_market:
                ticker = str(outer.get("ticker", ""))
                row_market = "kr" if ticker.isdigit() and len(ticker) == 6 else "us"
            if market != "both" and row_market != market:
                continue
            rows.append((outer, scenario))
    return rows


def _kr_ohlcv(ticker: str, buy_date: str):
    from cores.stock_chart import get_market_ohlcv_by_date

    end = buy_date.replace("-", "")[:8]
    start = (datetime.strptime(end, "%Y%m%d") - timedelta(days=400)).strftime("%Y%m%d")
    return get_market_ohlcv_by_date(start, end, ticker, adjusted=True)


def _us_client():
    path = ROOT / "prism-us" / "cores" / "us_data_client.py"
    spec = importlib.util.spec_from_file_location("buy_gate_us_data_client", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module.USDataClient()


def _us_ohlcv(client, ticker: str, buy_date: str):
    end_dt = datetime.strptime(buy_date, "%Y-%m-%d")
    start = (end_dt - timedelta(days=400)).strftime("%Y-%m-%d")
    end = (end_dt + timedelta(days=1)).strftime("%Y-%m-%d")
    return client.get_ohlcv(ticker, start=start, end=end, interval="1d")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=("kr", "us", "both"), default="kr")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--input",
        action="append",
        default=[
            str(ROOT / "tasks/eval/trading_outcome_results.jsonl"),
            str(ROOT / "tasks/eval/trading_outcome_results_extra.jsonl"),
        ],
    )
    args = parser.parse_args()
    rows = _load_rows(args.input, args.market)
    if args.limit:
        rows = rows[: args.limit]

    client = _us_client() if args.market in ("us", "both") else None
    stats = Counter()
    findings = Counter()
    examples: list[tuple] = []

    for outer, scenario in rows:
        ticker = str(outer.get("ticker", ""))
        buy_date = str(outer.get("buy_date", ""))
        market = str(outer.get("market", "") or scenario.get("market", "")).lower()
        if not market:
            market = "kr" if ticker.isdigit() and len(ticker) == 6 else "us"
        try:
            frame = _kr_ohlcv(ticker, buy_date) if market == "kr" else _us_ohlcv(client, ticker, buy_date)
            if frame is None or frame.empty:
                stats["data_skip"] += 1
                continue
            features = build_asof_features(
                frame,
                regime=normalize_regime(scenario.get("market_condition")),
            )
            result = validate_scenario(scenario, asof_features=features)
        except Exception as exc:  # shadow tool must keep the sample moving
            stats["data_skip"] += 1
            if len(examples) < 5:
                examples.append((ticker, buy_date, "ERROR", type(exc).__name__))
            continue

        decision = str(scenario.get("decision", ""))
        outcome = "loss" if float(outer.get("outcome_score", 0) or 0) < 0 else "nonloss"
        stats["rows"] += 1
        stats[("decision", decision)] += 1
        stats[("outcome", outcome)] += 1
        stats[("would_block", decision, result["would_block"])] += 1
        for finding in result["hard_findings"]:
            findings[finding["code"]] += 1
        if result["would_block"] and len(examples) < 10:
            examples.append((ticker, buy_date, decision, outcome, [f["code"] for f in result["hard_findings"]]))

    print(f"rows={stats['rows']} data_skip={stats['data_skip']} market={args.market}")
    print("decisions:", {k[1]: v for k, v in stats.items() if isinstance(k, tuple) and k[0] == "decision"})
    print("outcomes:", {k[1]: v for k, v in stats.items() if isinstance(k, tuple) and k[0] == "outcome"})
    print("would_block_by_decision:", {
        f"{k[1]}:{k[2]}": v
        for k, v in stats.items()
        if isinstance(k, tuple) and k[0] == "would_block"
    })
    print("hard_findings:", dict(findings))
    print("examples:", examples)
    return 0


if __name__ == "__main__":
    sys.exit(main())
