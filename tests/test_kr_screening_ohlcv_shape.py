"""Exercise the imported KR consumer, not an extracted function body."""
import pandas as pd
import pytest

import trigger_batch


@pytest.mark.parametrize("shape", ["flat", "multi", "duplicate"])
def test_kr_evidence_column_shape_boundary(monkeypatch, shape):
    history = pd.DataFrame({"High": [101., 102., 103.]})
    if shape == "multi":
        history.columns = pd.MultiIndex.from_tuples([("High", "005930")])
    elif shape == "duplicate":
        history = pd.concat([history, history], axis=1)
    monkeypatch.setattr(trigger_batch, "get_multi_day_ohlcv", lambda *_: history)
    result = trigger_batch.score_candidates_by_agent_criteria(
        pd.DataFrame({"Close": [100.]}, index=["005930"]), "20260914")
    evidence = result.at["005930", "screening_price_evidence"]
    assert result.at["005930", "risk_reward_ratio"] is None
    assert evidence["status"] == (
        "MISSING" if shape == "duplicate" else "OBSERVED_HIGH_ABOVE_REFERENCE")
