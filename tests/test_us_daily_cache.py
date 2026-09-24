"""Offline completed-session/adjustment/atomicity cache contract."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json

import pandas as pd
import pytest

from prism_core.us_daily_cache import DailyBarCache, PRICE_BASIS, histories_share_basis


def frame(days=("2026-09-21", "2026-09-22", "2026-09-23")):
    return pd.DataFrame({"Open": [100.] * len(days), "High": [105.] * len(days),
                         "Low": [95.] * len(days), "Close": [102.] * len(days),
                         "Volume": [1000.] * len(days)}, index=pd.to_datetime(list(days)))


def cache(tmp_path, now="2026-09-24T21:00:00+00:00"):
    return DailyBarCache(tmp_path, now=datetime.fromisoformat(now))


def stored(tmp_path, day="20260923"):
    return next(tmp_path.glob(f"*/{day}.json"))


def test_completed_exact_day_round_trip_and_capture_provenance(tmp_path):
    subject = cache(tmp_path)
    assert subject.save_history("AAPL", frame()) == 3
    row = subject.load_row("AAPL", "20260923", frame().iloc[:2])
    assert row["Close"] == 102 and row["Volume"] == 1000
    assert row["session_date"] == "20260923" and row["source"] == "daily_cache"
    assert row["captured_at"] == "2026-09-24T21:00:00+00:00"
    assert row["price_basis"] == PRICE_BASIS
    assert subject.load_row("MSFT", "20260923", frame()) is None
    assert subject.load_row("AAPL", "20260924", frame()) is None
    assert subject.load_row("AAPL", "20260921", frame()) is None  # no earlier anchors


@pytest.mark.parametrize("day,before,after", [
    ("2026-09-23", "2026-09-23T19:59:59+00:00", "2026-09-23T20:00:01+00:00"),
    ("2026-11-27", "2026-11-27T17:59:59+00:00", "2026-11-27T18:00:01+00:00"),
    ("2026-03-06", "2026-03-06T20:59:59+00:00", "2026-03-06T21:00:01+00:00"),
    ("2026-03-09", "2026-03-09T19:59:59+00:00", "2026-03-09T20:00:01+00:00"),
])
def test_actual_regular_early_close_and_dst(day, before, after, tmp_path):
    assert cache(tmp_path, before).save_history("AAPL", frame([day])) == 0
    assert cache(tmp_path, after).save_history("AAPL", frame([day])) == 1


def test_frozen_response_capture_not_later_save_time_controls_finality(tmp_path):
    subject = cache(tmp_path, "2026-09-23T19:59:59+00:00")
    assert subject.save_history("AAPL", frame()) == 2
    assert not list(tmp_path.glob("*/20260923.json"))
    assert subject.load_row("AAPL", "20260923", frame()) is None


def test_holiday_weekend_and_future_never_saved(tmp_path):
    assert cache(tmp_path).save_history("AAPL", frame(["2026-09-07", "2026-09-19", "2026-09-25"])) == 0


@pytest.mark.parametrize("field,value", [
    ("Open", 0), ("High", 99), ("Low", 103), ("Close", float("nan")),
    ("Volume", -1), ("High", float("inf")), ("Open", "100"), ("Close", True),
])
def test_invalid_bars_not_saved_or_usable_as_anchors(tmp_path, field, value):
    subject = cache(tmp_path)
    assert subject.save_history("AAPL", frame()) == 3
    malformed = frame().astype(object)
    malformed.loc[malformed.index[0], field] = value
    assert subject.load_row("AAPL", "20260923", malformed.iloc[:2]) is None
    assert subject.save_history("MSFT", malformed) == 2


def test_zero_volume_and_unrepaired_flags_allowed_but_repaired_rejected(tmp_path):
    subject = cache(tmp_path)
    data = frame()
    data["Volume"] = 0
    data["Repaired?"] = [False, True, False]
    assert subject.save_history("AAPL", data) == 2
    assert subject.load_row("AAPL", "20260922", frame()) is None
    assert subject.load_row("AAPL", "20260923", data.iloc[:1])["Volume"] == 0
    data.iloc[0, data.columns.get_loc("Repaired?")] = True
    assert subject.load_row("AAPL", "20260923", data) is None


@pytest.mark.parametrize("kind", ["duplicate", "intraday", "reversed", "multiindex", "duplicate_columns", "nat"])
def test_ambiguous_frame_rejected(tmp_path, kind):
    data = frame()
    if kind == "duplicate":
        data.index = pd.to_datetime(["2026-09-21", "2026-09-21", "2026-09-23"])
    elif kind == "intraday":
        data.index += pd.Timedelta(hours=1)
    elif kind == "reversed":
        data = data.iloc[::-1]
    elif kind == "multiindex":
        data.columns = pd.MultiIndex.from_product([data.columns, ["AAPL"]])
    elif kind == "duplicate_columns":
        data.columns = ["Open", "Open", "Low", "Close", "Volume"]
    else:
        data.index = pd.DatetimeIndex(["2026-09-21", "2026-09-22", pd.NaT])
    assert cache(tmp_path).save_history("AAPL", data) == 0


def test_timezone_aware_midnight_session_labels(tmp_path):
    data = frame()
    data.index = data.index.tz_localize("America/New_York")
    assert cache(tmp_path).save_history("AAPL", data) == 3


@pytest.mark.parametrize("field", ["Open", "High", "Low", "Close", "Volume"])
def test_any_earlier_overlap_changed_rejects_even_if_another_matches(tmp_path, field):
    subject = cache(tmp_path)
    subject.save_history("AAPL", frame())
    data = frame().iloc[:2].copy()
    data.loc[data.index[0], field] += .1
    assert subject.load_row("AAPL", "20260923", data) is None
    assert not histories_share_basis(frame(), data, "20260923")


def test_absent_anchor_or_only_later_overlap_is_not_confirmation(tmp_path):
    subject = cache(tmp_path)
    subject.save_history("AAPL", frame())
    assert subject.load_row("AAPL", "20260923", frame(["2026-09-24"])) is None
    assert not histories_share_basis(frame(), frame(["2026-09-24"]), "20260923")
    assert histories_share_basis(frame(), frame().iloc[:1], "20260923")


@pytest.mark.parametrize("mutation", ["symbol", "date", "version", "basis_type", "schema", "future_capture",
                                     "early_capture", "anchor_date", "anchor_nan", "anchor_repaired", "bar_nan"])
def test_corrupt_or_mismatched_metadata_fails_closed(tmp_path, mutation):
    subject = cache(tmp_path)
    subject.save_history("AAPL", frame())
    path = stored(tmp_path)
    data = json.loads(path.read_text())
    if mutation == "symbol": data["symbol"] = "MSFT"
    elif mutation == "date": data["session_date"] = "20260922"
    elif mutation == "version": data["price_basis"]["version"] = "different"
    elif mutation == "basis_type": data["price_basis"]["auto_adjust"] = 1
    elif mutation == "schema": data["schema"] = True
    elif mutation == "future_capture": data["captured_at"] = "2027-01-01T00:00:00+00:00"
    elif mutation == "early_capture": data["captured_at"] = "2026-09-23T19:59:00+00:00"
    elif mutation == "anchor_date": data["anchors"]["20260924"] = data["anchors"].pop("20260921")
    elif mutation == "anchor_nan": data["anchors"]["20260921"]["Close"] = float("nan")
    elif mutation == "anchor_repaired": data["anchors"]["20260921"]["Repaired?"] = True
    elif mutation == "bar_nan": data["bar"]["Close"] = float("nan")
    path.write_text(json.dumps(data))
    assert subject.load_row("AAPL", "20260923", frame()) is None


def test_invalid_json_and_io_failure_are_misses(tmp_path):
    subject = cache(tmp_path)
    subject.save_history("AAPL", frame())
    stored(tmp_path).write_text("not-json")
    assert subject.load_row("AAPL", "20260923", frame()) is None
    bad_root = tmp_path / "file"
    bad_root.write_text("not a directory")
    assert cache(bad_root).save_history("AAPL", frame()) == 0
    assert cache(bad_root).load_row("AAPL", "20260923", frame()) is None


def test_duplicate_json_key_rejected(tmp_path):
    subject = cache(tmp_path)
    subject.save_history("AAPL", frame())
    path = stored(tmp_path)
    path.write_text(path.read_text().replace('"schema":1', '"schema":1,"schema":1'))
    assert subject.load_row("AAPL", "20260923", frame()) is None


def test_atomic_write_failure_cleans_temporary_file(tmp_path, monkeypatch):
    def fail(*args):
        raise OSError("fixture filesystem failure")
    monkeypatch.setattr("prism_core.us_daily_cache.os.replace", fail)
    assert cache(tmp_path).save_history("AAPL", frame()) == 0
    assert not list(tmp_path.rglob("*.tmp"))
    assert not list(tmp_path.rglob("*.json"))


def test_concurrent_older_capture_never_overwrites_newer_immutable_generation(tmp_path):
    older = cache(tmp_path, "2026-09-24T20:00:00+00:00")
    newer = cache(tmp_path, "2026-09-24T21:00:00+00:00")
    old_frame, new_frame = frame(), frame()
    new_frame["Volume"] = 2000
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(newer.save_history, "AAPL", new_frame), pool.submit(older.save_history, "AAPL", old_frame)]
        for future in futures: future.result()
    assert newer.load_row("AAPL", "20260923", new_frame)["Volume"] == 2000
    assert newer.load_row("AAPL", "20260923", old_frame) is None
    assert not list(tmp_path.rglob("*.tmp"))


def test_exact_identity_and_date_validation(tmp_path):
    subject = cache(tmp_path)
    assert subject.save_history("../AAPL", frame()) == 0
    assert subject.load_row("AAPL", "../20260923", frame()) is None
    assert subject.load_row("AAPL", "20260230", frame()) is None
    assert cache(tmp_path, "2026-09-24T21:00:00").save_history("AAPL", frame()) == 0
