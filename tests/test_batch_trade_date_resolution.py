"""A local calendar resolves sessions; no removed provider fallback."""
import datetime
import pytest
import trigger_batch


@pytest.mark.parametrize("day, expected", [
    ("20260805", "20260805"), ("20260808", "20260807"),
    ("20260809", "20260807"), ("20260815", "20260814"),
])
def test_resolves_local_session(day, expected):
    assert trigger_batch._resolve_trade_date(day) == expected


def test_calendar_failure_does_not_guess_a_session(monkeypatch):
    import check_market_day
    def unavailable(_date):
        raise RuntimeError("calendar unavailable")
    monkeypatch.setattr(check_market_day, "is_market_day", unavailable)
    with pytest.raises(RuntimeError, match="calendar unavailable"):
        trigger_batch._resolve_trade_date("20260805")


def test_no_session_in_bound_fails_closed(monkeypatch):
    import check_market_day
    monkeypatch.setattr(check_market_day, "is_market_day", lambda _date: False)
    with pytest.raises(trigger_batch.MarketSnapshotUnavailableError):
        trigger_batch._resolve_trade_date("20260805")


def test_explicit_calendar_date():
    from check_market_day import is_market_day
    assert is_market_day(datetime.date(2026, 8, 5))
    assert not is_market_day(datetime.date(2026, 8, 8))


def test_snapshot_failure_is_typed_not_another_provider(monkeypatch):
    def unavailable(_date):
        raise RuntimeError("KIS down")
    monkeypatch.setattr(trigger_batch, "build_kis_snapshot_bundle", unavailable)
    with pytest.raises(trigger_batch.MarketSnapshotUnavailableError, match="KIS down"):
        trigger_batch.load_market_snapshot_bundle("20260911")
    assert not hasattr(trigger_batch, "fetch_naver_snapshot_bundle")
