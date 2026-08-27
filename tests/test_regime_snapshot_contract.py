import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cores.regime_policy import enforce_computed_regime, stamp_scenario_market_regime  # noqa: E402


def test_computed_regime_overrides_llm_label_but_preserves_audit_value():
    result = enforce_computed_regime(
        {
            "market_regime": "strong_bull",
            "leading_sectors": [{"sector": "Semiconductors"}],
        },
        {
            "market_regime": "moderate_bear",
            "primary_trend_regime": "moderate_bear",
            "effective_entry_regime": "moderate_bear",
            "swing_state": "pullback",
            "regime_confidence": 0.78,
            "simple_ma_regime": "bear",
            "index_summary": {"distribution_days": 6},
        },
    )

    assert result["market_regime"] == "moderate_bear"
    assert result["primary_trend_regime"] == "moderate_bear"
    assert result["effective_entry_regime"] == "moderate_bear"
    assert result["swing_state"] == "pullback"
    assert result["llm_market_regime"] == "strong_bull"
    assert result["index_summary"]["distribution_days"] == 6
    assert result["leading_sectors"] == [{"sector": "Semiconductors"}]


def test_missing_computed_regime_is_non_destructive():
    original = {"market_regime": "strong_bull", "leading_sectors": []}
    assert enforce_computed_regime(original, None) == original


def test_scenario_market_condition_is_canonicalized_for_display():
    result = stamp_scenario_market_regime(
        {"market_condition": "strong_bull: LLM prose", "buy_score": 7},
        "sideways",
    )
    assert result["market_condition"] == "sideways"
    assert result["market_regime"] == "sideways"
    assert result["_deterministic_market_regime"] == "sideways"
    assert result["llm_market_condition"] == "strong_bull: LLM prose"
    assert result["market_regime_source"] == "trigger_batch"
