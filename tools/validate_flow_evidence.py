"""Independent arithmetic checks on explicitly supplied, frozen public inputs.

This is input-contract validation, not a return backtest or live trading command.
"""

import argparse
import hashlib
import io
import json
import math
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from prism_core.flow_evidence import compute_us_flow_evidence, describe_us_holdings
from prism_core.kr_flow_evidence import compute_kr_flow_evidence


def verify(directory):
    result = {"version": "flow-evidence-comparison-v1", "input_sha256": {},
              "us": [], "kr": [], "performance_validation": "NOT_ESTABLISHED",
              "scope": "INPUT_CONTRACT_ONLY_NO_NEW_GATE"}
    for ticker in ("HPE", "NVDA", "SPY", "000660", "005930"):
        path = directory / f"flow-evidence-live-{ticker}.json"
        raw_bytes = path.read_bytes()
        raw = json.loads(raw_bytes)
        result["input_sha256"][ticker] = hashlib.sha256(raw_bytes).hexdigest()
        if ticker.isdigit():
            evidence = compute_kr_flow_evidence(raw["flow"], raw["prices"], raw["sessions"], asof_utc=raw["asof_utc"])
            for window in evidence["windows"].values():
                if window["status"] != "OK":
                    raise ValueError("KR canary window unavailable")
                dates = sorted(day for day in raw["sessions"] if window["start"] <= day <= window["end"])
                if len(dates) != window["required_sessions"]:
                    raise ValueError("KR window count mismatch")
                foreign = sum(int(raw["flow"][day]["외국인합계"]) for day in dates)
                institution = sum(int(raw["flow"][day]["기관합계"]) for day in dates)
                if window["net_shares"] != {"foreign": foreign, "institution": institution, "combined": foreign + institution}:
                    raise ValueError("KR independent arithmetic mismatch")
            result["kr"].append({"ticker": ticker, "evidence": evidence})
            continue
        frame = pd.read_json(io.StringIO(json.dumps(raw["ohlcv"])), orient="table")
        evidence = compute_us_flow_evidence(frame, asof_utc=raw["asof_utc"])
        data = frame.rename(columns=str.lower).copy()
        data.index = [str(day.date()) for day in data.index]
        for name, metric in evidence["metrics"].items():
            if metric["status"] != "computed":
                raise ValueError("US canary metric unavailable")
            rows = data.loc[metric["window_start"]:metric["window_end"]]
            if len(rows) != metric["window_sessions"]:
                raise ValueError("US window count mismatch")
            total, weighted = float(rows.volume.sum()), 0.0
            for day, bar in rows.iterrows():
                if name.startswith("signed_volume"):
                    position = data.index.get_loc(day)
                    change = bar.close - data.iloc[position - 1].close
                    weight = 1 if change > 0 else -1 if change < 0 else 0
                else:
                    width = bar.high - bar.low
                    weight = (2 * bar.close - bar.high - bar.low) / width if width else 0
                weighted += weight * bar.volume
            if not math.isclose(weighted / total, metric["value"], abs_tol=5.1e-9):
                raise ValueError("US independent arithmetic mismatch")
        scaled = frame.copy()
        scaled["volume"] = scaled["volume"] * 7
        if not (scaled["volume"] == frame["volume"] * 7).all():
            raise ValueError("Volume scaling fixture failed")
        changed = compute_us_flow_evidence(scaled, asof_utc=raw["asof_utc"])
        if changed["metrics"] != evidence["metrics"]:
            raise ValueError("Volume scaling changed dimensionless metrics")
        holders = {key: pd.read_json(io.StringIO(json.dumps(value)), orient="table")
                   for key, value in raw["holders"].items()}
        result["us"].append({"ticker": ticker, "evidence": evidence,
                              "holdings": describe_us_holdings(holders, asof_utc=raw["asof_utc"])})
    result["comparison_id"] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()[:24]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve() in {p.resolve() for p in args.input_dir.glob("flow-evidence-live-*.json")}:
        parser.error("output must not overwrite inputs")
    result = verify(args.input_dir)
    if result != verify(args.input_dir):
        raise ValueError("Non-deterministic comparison")
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"comparison_id": result["comparison_id"], "US_cases": len(result["us"]),
                      "KR_cases": len(result["kr"]), "status": "PASSED_INPUT_CONTRACT_NOT_PNL"}))


if __name__ == "__main__":
    main()
