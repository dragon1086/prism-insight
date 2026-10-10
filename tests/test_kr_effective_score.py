"""KR compares effective_score = buy_score + macro_adjustment with min_score, as the BUY instruction says."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "stock_tracking_enhanced_agent.py").read_text()


def _helper():
    tree = ast.parse(SOURCE)
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_kr_effective_score")
    namespace = {}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "kr_effective_score", "exec"), namespace)
    return namespace["_kr_effective_score"]


def test_effective_score_adds_the_macro_adjustment():
    effective = _helper()
    assert effective({"buy_score": 7, "macro_adjustment": 1}) == 8
    assert effective({"buy_score": 6, "macro_adjustment": -1}) == 5
    assert effective({"buy_score": 6}) == 6 and isinstance(effective({"buy_score": 6}), int)
    assert effective({"buy_score": None, "macro_adjustment": "x"}) == 0
    assert effective({"buy_score": float("nan"), "macro_adjustment": 1}) == 1
    assert effective(None) == 0


def test_batch_score_checks_use_the_effective_score():
    batch = SOURCE[SOURCE.index("effective_score = _kr_effective_score(scenario)"):]
    batch = batch[:batch.index("async def _enter_eligible_candidate")]
    assert "score_override=effective_score" in batch
    assert "(effective_score < min_score and not rebound_pilot)" in batch
    assert "(effective_score >= min_score or rebound_pilot)" in batch
    assert "buy_score < min_score" not in batch
