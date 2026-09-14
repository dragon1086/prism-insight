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


def test_retry_only_missing_ticker_preserves_success_and_cache(tmp_path):
    class TransientSource(Source):
        def price_history(self, ticker, *args, **kwargs):
            first = not any(call[0] == ticker for call in self.calls)
            result = super().price_history(ticker, *args, **kwargs)
            if ticker == "004170" and first:
                raise TimeoutError("sensitive backend payload")
            return result
    source = TransientSource()
    result = fetch_kis_previous_history(
        ["004170", "005930"], "20260910", source=source, cache_dir=tmp_path,
        request_interval_sec=0,
    )
    assert [call[0] for call in source.calls] == ["004170", "005930", "004170"]
    assert len(result) == 2
    fetch_kis_previous_history(["004170", "005930"], "20260910", source=source,
                               cache_dir=tmp_path, request_interval_sec=0)
    assert len(source.calls) == 3


@pytest.mark.parametrize("invalid_date", [False, True])
def test_persistent_failure_is_bounded_and_sanitized(tmp_path, invalid_date):
    class BrokenSource(Source):
        def price_history(self, *args, **kwargs):
            result = super().price_history(*args, **kwargs)
            if not invalid_date:
                raise TimeoutError("private response payload")
            return result
    source = BrokenSource(missing=invalid_date)
    with pytest.raises(KisSnapshotError) as error:
        fetch_kis_previous_history(["004170"], "20260910", source=source,
                                   cache_dir=tmp_path, request_interval_sec=0)
    assert len(source.calls) == 2
    assert "private response payload" not in str(error.value)
    assert ("ValueError" if invalid_date else "TimeoutError") in str(error.value)
    assert not (tmp_path / "20260910" / "004170.json").exists()


def test_retry_shares_original_deadline(tmp_path, monkeypatch):
    import cores.kis_market_snapshot as module
    now = [0.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    class RetryLateSource(Source):
        def price_history(self, *args, **kwargs):
            result = super().price_history(*args, **kwargs)
            if len(self.calls) == 1:
                now[0] = 9.0
                raise TimeoutError()
            now[0] = 11.0
            return result
    source = RetryLateSource()
    with pytest.raises(KisSnapshotError, match="deadline after response"):
        fetch_kis_previous_history(["004170"], "20260910", source=source,
                                   cache_dir=tmp_path, request_interval_sec=0, max_duration_sec=10)
    assert len(source.calls) == 2
    assert not (tmp_path / "20260910" / "004170.json").exists()


def test_broad_outage_stops_after_five_requests_without_retry_storm(tmp_path):
    class DownSource(Source):
        def price_history(self, *args, **kwargs):
            super().price_history(*args, **kwargs)
            raise ConnectionError("private outage payload")
    source = DownSource()
    with pytest.raises(KisSnapshotError, match="repeated failure"):
        fetch_kis_previous_history([f"{i:06}" for i in range(10)], "20260910",
                                   source=source, cache_dir=tmp_path, request_interval_sec=0)
    assert len(source.calls) == 5


def test_expired_budget_does_not_start_retry(tmp_path, monkeypatch):
    import cores.kis_market_snapshot as module
    now = [0.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    class ExpiredSource(Source):
        def price_history(self, *args, **kwargs):
            super().price_history(*args, **kwargs)
            now[0] = 11.0
            raise TimeoutError("private payload")
    source = ExpiredSource()
    with pytest.raises(KisSnapshotError, match="deadline"):
        fetch_kis_previous_history(["004170"], "20260910", source=source,
                                   cache_dir=tmp_path, request_interval_sec=0, max_duration_sec=10)
    assert len(source.calls) == 1


def test_success_cache_survives_another_tickers_exhausted_retry(tmp_path):
    class PartialSource(Source):
        def price_history(self, ticker, *args, **kwargs):
            result = super().price_history(ticker, *args, **kwargs)
            if ticker == "004170":
                raise TimeoutError()
            return result
    source = PartialSource()
    with pytest.raises(KisSnapshotError, match="coverage=1/2"):
        fetch_kis_previous_history(["004170", "005930"], "20260910", source=source,
                                   cache_dir=tmp_path, request_interval_sec=0)
    assert [call[0] for call in source.calls] == ["004170", "005930", "004170"]
    assert (tmp_path / "20260910" / "005930.json").exists()
    assert not (tmp_path / "20260910" / "004170.json").exists()
