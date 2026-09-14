#!/usr/bin/env python3
"""Tripwire: the former guaranteed +15% scenario credit stays removed.

Replaces the old defect-preservation alarm after explicit approval. Bottom-up
rank and changed top-down interaction are covered in test_screening_price_evidence.
"""
import ast
import logging
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from prism_core.screening_price_evidence import build_screening_price_evidence
from prism_core.ohlcv_shape import normalize_single_ticker_ohlcv


def test_synthetic_scenario_credit_is_removed():
    for relative in ("trigger_batch.py", "prism-us/us_trigger_batch.py"):
        path = ROOT / relative
        tree = ast.parse(path.read_text())
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                  and n.name == "calculate_agent_fit_metrics")
        node = next(n for n in tree.body if isinstance(n, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == "TRIGGER_CRITERIA" for t in n.targets))
        criteria = ast.literal_eval(node.value)
        namespace = {"pd": pd, "logger": logging.getLogger(__name__), "TRIGGER_CRITERIA": criteria,
                     "build_screening_price_evidence": build_screening_price_evidence,
                     "normalize_single_ticker_ohlcv": normalize_single_ticker_ohlcv}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), str(path), "exec"), namespace)
        for trigger in criteria:
            for high in (70, 90, 100, 102, 105, 110, 114.99, 115, 125, 150, 200, None):
                frame = pd.DataFrame({"High": [high] * 5}) if high else pd.DataFrame()
                namespace["get_multi_day_ohlcv"] = lambda *args: frame
                for price in (100, 0, float("nan")):
                    metrics = namespace["calculate_agent_fit_metrics"](
                        "TEST", price, "20260915", trigger_type=trigger)
                    for field in ("target_price", "stop_loss_price", "stop_loss_pct",
                                  "risk_reward_ratio", "agent_fit_score"):
                        assert metrics[field] is None, (relative, trigger, high, price, field)


if __name__ == "__main__":
    test_synthetic_scenario_credit_is_removed()
    print("PASS: KR/US all criteria, 12 high-window shapes, 3 prices: no fabricated scenario")
