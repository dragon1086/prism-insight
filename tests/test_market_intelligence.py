"""Deterministic evidence tests, including gaps, future bars and off compatibility."""
from datetime import date, timedelta
from unittest.mock import Mock

import pandas as pd

from prism_core import market_intelligence as mi


def series():
    start = date(2026, 1, 1)
    rows = [(str(start + timedelta(days=i)), 100 + i) for i in range(80)]
    return {"SPY": rows, "XLK": [(day, price * 2) for day, price in rows]}


def test_no_lookahead_and_aligned_relative_returns():
    packet = mi.build_etf_packet(series(), "20260315")
    assert packet["price_asof"] == "2026-03-14"
    tech = next(row for row in packet["rows"] if row["symbol"] == "XLK")
    assert tech["relative_spy_pp"] == {"5": 0.0, "20": 0.0, "60": 0.0}
    assert not packet["is_cash_flow"]
    assert len(mi.render_context(packet)) <= 2500


def test_missing_middle_bar_does_not_compress_horizon():
    data = series()
    data["XLK"] = data["XLK"][:-10] + data["XLK"][-9:]
    packet = mi.build_etf_packet(data, "20260322")
    tech = next(row for row in packet["rows"] if row["symbol"] == "XLK")
    assert "5" in tech["returns_pct"]
    assert "20" not in tech["returns_pct"]


def test_nonfinite_unknown_not_zero():
    packet = mi.build_etf_packet({"SPY": [("2026-03-01", float("nan"))]}, "20260322")
    assert packet["status"] == "UNKNOWN"
    assert all(row["returns_pct"] == {} for row in packet["rows"])


def test_missing_benchmark_session_is_not_silently_shifted():
    data = series()
    days = [row[0] for row in data["SPY"]]
    data["SPY"] = data["SPY"][:-1]
    packet = mi.build_etf_packet(data, "20260322", days)
    assert packet["status"] == "UNKNOWN"
    assert packet["price_asof"] != packet["expected_price_asof"]


def test_off_never_starts_process(monkeypatch):
    monkeypatch.setenv("REPORT_MARKET_CONTEXT_ENABLED", "false")
    run = Mock(side_effect=AssertionError("must not query"))
    monkeypatch.setattr(mi.subprocess, "run", run)
    assert mi.prefetch_us_context("20260322") is None
    run.assert_not_called()


def test_malformed_config_is_off(monkeypatch):
    monkeypatch.delenv("REPORT_MARKET_CONTEXT_ENABLED", raising=False)
    for raw in ("[]", "null", '"true"', "42", "{broken"):
        monkeypatch.setattr(mi.Path, "read_text", lambda *args, value=raw, **kwargs: value)
        assert not mi.enabled()
        assert mi.optional_participation(None, None, "US", "20260917") is None


def test_timeout_unknown_and_bounded(monkeypatch):
    monkeypatch.setenv("REPORT_MARKET_CONTEXT_ENABLED", "true")
    run = Mock(side_effect=mi.subprocess.TimeoutExpired("collector", 20))
    monkeypatch.setattr(mi.subprocess, "run", run)
    assert mi.prefetch_us_context("20260322")["status"] == "UNKNOWN"
    assert run.call_args.kwargs["timeout"] == 20


def test_future_us_date_rejected_before_any_provider_access():
    from tools.run_market_intelligence_prefetch import collect
    import pytest
    with pytest.raises(ValueError, match="future_US_analysis_date"):
        collect("29990101")


def test_participation_keeps_missing_denominator_and_scope():
    snap = pd.DataFrame({"Close": [110, 90, float("nan")]}, index=["A", "B", "C"])
    prev = pd.DataFrame({"Close": [100, 100, 100]}, index=["A", "B", "C"])
    packet = mi.snapshot_participation(snap, "US", "20260322", 5, prev)
    assert (packet["advance"], packet["decline"], packet["missing_count"]) == (1, 1, 3)
    assert packet["is_full_market_ma_breadth"] is False
    assert packet["timing"] == "snapshot_may_be_intraday"


def test_kr_participation_no_synthetic_style_or_fundflow():
    snap = pd.DataFrame({"등락률": [1, -1, 0, None]})
    packet = mi.snapshot_participation(snap, "KR", "20260322")
    assert packet["valid_count"] == 3
    assert packet["missing_count"] == 1
    assert packet["is_cash_flow"] is False
