import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cores.regime_policy import enforce_computed_regime  # noqa: E402


def test_computed_regime_overrides_llm_label_but_preserves_audit_value():
    result = enforce_computed_regime(
        {
            "market_regime": "strong_bull",
            "leading_sectors": [{"sector": "Semiconductors"}],
        },
        {
            "market_regime": "moderate_bear",
            "regime_confidence": 0.78,
            "simple_ma_regime": "bear",
            "index_summary": {"distribution_days": 6},
        },
    )

    assert result["market_regime"] == "moderate_bear"
    assert result["llm_market_regime"] == "strong_bull"
    assert result["index_summary"]["distribution_days"] == 6
    assert result["leading_sectors"] == [{"sector": "Semiconductors"}]


def test_missing_computed_regime_is_non_destructive():
    original = {"market_regime": "strong_bull", "leading_sectors": []}
    assert enforce_computed_regime(original, None) == original
