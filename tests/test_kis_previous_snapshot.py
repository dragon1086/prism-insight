"""Exact dated KIS history must never be invented from the last close."""
import json

import pandas as pd
import pytest

from cores.kis_market_snapshot import KisSnapshotError, fetch_kis_previous_history


class Source:
    def __init__(self, *, missing=False):
        self.calls = []
        self.missing = missing

    def price_history(self, ticker, start, end, *, adjusted):
        self.calls.append((ticker, start, end, adjusted))
        return pd.DataFrame(
            {"Open": [90], "High": [110], "Low": [80], "Close": [100],
             "Volume": [1000], "Amount": [100000]},
            index=pd.to_datetime(["2026-09-09" if self.missing else end]),
        )


def test_exact_ohlcv_and_dated_cache_reuse(tmp_path):
    source = Source()
    first = fetch_kis_previous_history(
        ["005930"], "20260910", source=source, cache_dir=tmp_path,
        request_interval_sec=0,
    )
    second = fetch_kis_previous_history(
        ["005930"], "20260910", source=source, cache_dir=tmp_path,
        request_interval_sec=0,
    )
    assert len(source.calls) == 1
    assert source.calls[0][-1] is False
    pd.testing.assert_frame_equal(first, second)
    assert first.loc["005930", ["Open", "High", "Low", "Close"]].tolist() == [90, 110, 80, 100]


def test_missing_exact_date_fails_not_nearest_older_row(tmp_path):
    with pytest.raises(KisSnapshotError, match="coverage"):
        fetch_kis_previous_history(
            ["005930"], "20260910", source=Source(missing=True),
            cache_dir=tmp_path, request_interval_sec=0,
        )


def test_unverified_cache_is_not_relabelled_kis(tmp_path):
    path = tmp_path / "20260910"
    path.mkdir()
    (path / "005930.json").write_text(json.dumps({
        "source": "krx", "date": "20260910", "ticker": "005930",
        "row": {"Open": 1, "High": 1, "Low": 1, "Close": 1, "Volume": 1, "Amount": 1},
    }))
    source = Source()
    result = fetch_kis_previous_history(
        ["005930"], "20260910", source=source, cache_dir=tmp_path,
        request_interval_sec=0,
    )
    assert len(source.calls) == 1
    assert result.loc["005930", "Close"] == 100


def test_late_history_response_is_rejected_before_cache_write(tmp_path, monkeypatch):
    import cores.kis_market_snapshot as module
    now = [0.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    class SlowSource(Source):
        def price_history(self, *args, **kwargs):
            result = super().price_history(*args, **kwargs)
            now[0] = 11.0
            return result
    with pytest.raises(KisSnapshotError, match="deadline after response"):
        fetch_kis_previous_history(["005930"], "20260910", source=SlowSource(),
                                   cache_dir=tmp_path, request_interval_sec=0, max_duration_sec=10)
    assert not (tmp_path / "20260910" / "005930.json").exists()


def test_final_acceptance_deadline_includes_last_request_gap(tmp_path, monkeypatch):
    import cores.kis_market_snapshot as module
    now = [0.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(module.time, "sleep", lambda _: now.__setitem__(0, 11.0))
    with pytest.raises(KisSnapshotError, match="deadline before acceptance"):
        fetch_kis_previous_history(["005930"], "20260910", source=Source(),
                                   cache_dir=tmp_path, request_interval_sec=1, max_duration_sec=10)
