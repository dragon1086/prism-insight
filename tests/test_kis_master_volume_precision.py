"""Observed KIS master binary32 rounding is not an arbitrary discrepancy tolerance."""
import pytest
import pandas as pd

from cores.kis_market_snapshot import _master_volume_match_kind


@pytest.mark.parametrize("master,daily", [
    (99746736, 99746740), (22517076, 22517075), (22285604, 22285603),
    (17867160, 17867161), (38771528, 38771527), (46035712, 46035714),
])
def test_six_observed_raw_master_roundings(master, daily):
    assert _master_volume_match_kind(master, daily) == "float32_master_observed"


@pytest.mark.parametrize("master,daily", [
    (99746736, 99746741), (22517076, 22517078), (22285604, 22285606),
    (17867160, 17867162), (38771528, 38771531), (46035712, 46035715),
    (22517075, 22517076), (1000, 1001), (1000, 2000),
    (1.5, 1.5), (float("nan"), 1), (float("inf"), 1), (-1, -1),
    (1000000000000, 1000000000000), (True, 1),
])
def test_outside_exact_binary32_cell_or_invalid_counts_rejected(master, daily):
    assert _master_volume_match_kind(master, daily) is None


@pytest.mark.parametrize("volume", [0, 1, 16777215, 22517075, 999999999999])
def test_exact_integer_equality_is_preserved(volume):
    assert _master_volume_match_kind(volume, volume) == "exact"


def test_bundle_records_compatibility_without_rounding_daily_volume(monkeypatch):
    import cores.kis_market_snapshot as module
    monkeypatch.setattr(module, "_today_kst", lambda: "20260911")
    previous = pd.DataFrame({"Open": [10], "High": [12], "Low": [9], "Close": [11],
                             "Volume": [22517075], "Amount": [247687825]}, index=["005930"])
    original = previous.copy(deep=True)
    current = previous.copy(deep=True)
    cap = pd.DataFrame({"시가총액": [1000000]}, index=previous.index)
    master = module.KisMasterData({"005930": "삼성전자"}, cap, {}, "20260911", {}, {},
                                  {"005930": 22517076})
    result = module.build_kis_snapshot_bundle(
        "20260911", master_fetcher=lambda: master,
        snapshot_fetcher=lambda _: current, history_fetcher=lambda *_: previous,
    )
    pd.testing.assert_frame_equal(result.prev_snapshot, original)
    assert result.prev_snapshot.at["005930", "Volume"] == 22517075
    assert master.previous_volumes["005930"] == 22517076
    assert result.cap_df.attrs["master_volume_binary32_compatibility"] == ["005930"]
