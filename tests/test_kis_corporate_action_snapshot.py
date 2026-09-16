"""Offline regression for the observed 2026-09-16 bonus-rights KIS views."""
import zipfile
from dataclasses import replace
from io import BytesIO
from types import SimpleNamespace

import pandas as pd
import pytest

from cores import kis_market_snapshot as m

RAW = {"Open": 8700, "High": 8780, "Low": 8090, "Close": 8290, "Volume": 95410, "Amount": 801599045}
ADJUSTED = {"Open": 2901, "High": 2928, "Low": 2698, "Close": 2764, "Volume": 286057, "Amount": 801599045}


def dated(row, date="20260915"):
    return pd.DataFrame([row], index=[pd.Timestamp(date)], dtype=float)


@pytest.fixture
def case(monkeypatch):
    monkeypatch.setattr(m, "_today_kst", lambda: "20260916")
    monkeypatch.setattr(m, "previous_session", lambda date: "20260915")
    master = m.KisMasterData(
        {"475460": "미트박스"}, pd.DataFrame({"시가총액": [46800000000]}, index=["475460"]),
        {"475460": "20250123"}, "20260916", {"475460": "KOSDAQ"}, {}, {"475460": 31822},
        {"475460": "2765"}, {"475460": ("01", "00", "02")},
    )
    cached = pd.DataFrame([RAW], index=["475460"])
    current = pd.DataFrame([dict(ADJUSTED, Close=2800)], index=["475460"])
    options = {"master_fetcher": lambda: master, "history_fetcher": lambda codes, date: cached,
               "snapshot_fetcher": lambda codes: current,
               "corporate_action_fetcher": lambda code, date: (dated(RAW), dated(ADJUSTED), "2765")}
    return master, cached, options


def test_observed_bonus_normalizes_comparison_not_raw_cache_or_cap(case):
    master, cached, options = case
    result = m.build_kis_snapshot_bundle("20260916", **options)
    assert cached.loc["475460"].to_dict() == RAW
    assert result.prev_snapshot.loc["475460"].to_dict() == ADJUSTED
    assert result.cap_df.equals(master.cap_df)
    change = result.snapshot.loc["475460", "Close"] / result.prev_snapshot.loc["475460", "Close"] - 1
    assert 0 < change < .02  # No fabricated -67% crash.
    assert result.prev_snapshot.attrs["corporate_action_adjusted_codes"] == ["475460"]


@pytest.mark.parametrize("flags", [("00", "00", "00"), ("01", "01", "02"), ("01", "00", "01")])
def test_mismatch_without_explicit_bonus_allowlist_fails(case, flags):
    master, _, options = case
    options["master_fetcher"] = lambda: replace(master, action_flags={"475460": flags})
    with pytest.raises(m.KisSnapshotError, match="volume/session mismatch"):
        m.build_kis_snapshot_bundle("20260916", **options)


@pytest.mark.parametrize("fault", ["base", "date", "raw", "volume", "amount", "ohlc", "missing", "nan"])
def test_action_evidence_faults_fail_closed(case, fault):
    _, _, options = case
    raw, adj, base = dated(RAW), dated(ADJUSTED), "2765"
    if fault == "base": base = "2764"
    if fault == "date": adj.index = [pd.Timestamp("20260914")]
    if fault == "raw": raw.loc[:, "Open"] = 8699
    if fault == "volume": adj.loc[:, "Volume"] = 286056
    if fault == "amount": adj.loc[:, "Amount"] = 801599044
    if fault == "ohlc": adj.loc[:, "Low"] = 3000
    if fault == "missing": adj = adj.drop(columns=["Amount"])
    if fault == "nan": adj.loc[:, "Close"] = float("nan")
    options["corporate_action_fetcher"] = lambda code, date: (raw, adj, base)
    with pytest.raises(m.KisSnapshotError, match="corporate-action validation failed"):
        m.build_kis_snapshot_bundle("20260916", **options)


def test_flagged_equal_zero_volume_still_fetches_adjusted_view(case):
    master, _, options = case
    raw = dict(RAW, Volume=0, Amount=0)
    adj = dict(ADJUSTED, Volume=0, Amount=0)
    options["master_fetcher"] = lambda: replace(master, previous_volumes={"475460": 0})
    options["history_fetcher"] = lambda codes, date: pd.DataFrame([raw], index=codes)
    calls = []
    def fetch(code, date):
        calls.append(code)
        return dated(raw), dated(adj), "2765"
    options["corporate_action_fetcher"] = fetch
    result = m.build_kis_snapshot_bundle("20260916", **options)
    assert calls == ["475460"]
    assert result.prev_snapshot.loc["475460", "Close"] == 2764


def test_normal_volume_match_does_not_fetch_extra_data(case):
    master, cached, options = case
    options["master_fetcher"] = lambda: replace(master, previous_volumes={"475460": 95410}, action_flags={})
    options["corporate_action_fetcher"] = lambda *args: pytest.fail("unexpected GET")
    assert m.build_kis_snapshot_bundle("20260916", **options).prev_snapshot.equals(cached)


def test_zero_volume_all_zero_adjusted_close_is_not_usable(case):
    master, _, options = case
    raw = dict(RAW, Volume=0, Amount=0)
    options["master_fetcher"] = lambda: replace(master, previous_volumes={"475460": 0})
    options["history_fetcher"] = lambda codes, date: pd.DataFrame([raw], index=codes)
    options["corporate_action_fetcher"] = lambda code, date: (dated(raw), dated(dict.fromkeys(RAW, 0)), "2765")
    with pytest.raises(m.KisSnapshotError, match="corporate-action validation failed"):
        m.build_kis_snapshot_bundle("20260916", **options)


def test_more_than_ten_actions_fail_before_extra_fetches(case):
    master, _, options = case
    codes = [f"{i:06d}" for i in range(11)]
    options["master_fetcher"] = lambda: replace(
        master, names=dict.fromkeys(codes, "bonus"), listed_dates={},
        cap_df=pd.DataFrame({"시가총액": [46800000000] * 11}, index=codes),
        action_flags=dict.fromkeys(codes, ("01", "00", "02")),
    )
    options["history_fetcher"] = lambda codes, date: pd.DataFrame([RAW] * 11, index=codes)
    options["corporate_action_fetcher"] = lambda *args: pytest.fail("unexpected GET")
    with pytest.raises(m.KisSnapshotError, match="count limit"):
        m.build_kis_snapshot_bundle("20260916", **options)


def test_official_master_action_field_offsets_for_both_markets(monkeypatch):
    monkeypatch.setattr(m, "_today_kst", lambda: "20260916")
    payloads = []
    for code, widths, base_index, action_index, vol_index, cap_index in (
        ("005930", m._KOSPI_WIDTHS, 31, 41, 47, 65),
        ("475460", m._KOSDAQ_WIDTHS, 26, 36, 42, 59),
    ):
        fields = [" " * width for width in widths]
        for index, value in {base_index: "2765", action_index: "01", action_index + 1: "00",
                             action_index + 2: "02", vol_index: "31822", cap_index: "468"}.items():
            fields[index] = value.rjust(widths[index])
        line = f"{code:<9}{'KR7' + code:<12}{'NAME':<40}" + "".join(fields)
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("codes.mst", line.encode("euc-kr"))
        payloads.append(buffer.getvalue())
    class Response:
        def __init__(self, content): self.content = content
        def raise_for_status(self): pass
    master = m.fetch_kis_master_data(request_get=lambda *args, **kw: Response(payloads.pop(0)), min_stock_count=1)
    assert master.base_prices == {"005930": "2765", "475460": "2765"}
    assert master.action_flags == dict.fromkeys(["005930", "475460"], ("01", "00", "02"))


def test_late_action_response_rejected(case, monkeypatch):
    _, _, options = case
    clock = iter([0, 0, 61])
    monkeypatch.setattr(m.time, "monotonic", lambda: next(clock))
    with pytest.raises(m.KisSnapshotError, match="deadline after response"):
        m.build_kis_snapshot_bundle("20260916", **options)


def test_fetcher_uses_raw_adjusted_and_independent_readonly_quote():
    calls = []
    class Source:
        def price_history(self, code, start, end, *, adjusted):
            calls.append((code, start, end, adjusted))
            return dated(ADJUSTED if adjusted else RAW)
        def _fetch(self, url, tr, params):
            calls.append((url, tr, params))
            return SimpleNamespace(output={"stck_sdpr": "2765"})
    raw, adj, base = m.fetch_kis_corporate_action_views("475460", "20260915", source=Source())
    assert calls[:2] == [("475460", "20260915", "20260915", False), ("475460", "20260915", "20260915", True)]
    assert calls[2][1] == "FHKST01010100"
    assert base == "2765" and raw.iloc[0].to_dict() == RAW and adj.iloc[0].to_dict() == ADJUSTED
