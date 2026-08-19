import sys
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PRISM_US_DIR = PROJECT_ROOT / "prism-us"
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PRISM_US_DIR))

from cores import data_prefetch as dp  # noqa: E402


class _FakeClient:
    def __init__(self):
        self.calls = []

    def get_ohlcv(self, ticker, **kwargs):
        self.calls.append((ticker, kwargs))
        idx = pd.date_range("2026-01-01", periods=5, freq="D")
        return pd.DataFrame(
            {"close": [100, 101, 100, 102, 103], "volume": [1000] * 5},
            index=idx,
        )


def test_prefetch_caps_all_index_requests_at_reference_date(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(dp, "_get_us_data_client", lambda: client)
    monkeypatch.setattr(dp, "_compute_us_regime", lambda *args: {"market_regime": "sideways"})
    monkeypatch.setattr(dp, "_log_regime_snapshot", lambda *args: None)

    result = dp.prefetch_us_macro_intelligence_data("20260818")

    assert result["computed_regime"]["market_regime"] == "sideways"
    assert len(client.calls) == 3
    for _, kwargs in client.calls:
        assert kwargs["end"] == "2026-08-19"
        assert kwargs["start"] < kwargs["end"]
        assert kwargs["interval"] == "1d"
