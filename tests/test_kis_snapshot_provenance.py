from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from cores.market_data.kis_source import KisSource
from cores.market_data.mcp_server import _answer
from cores.data_prefetch import _dict_to_markdown


def test_metadata_is_not_a_fake_bar_in_market_regime_computation():
    import pandas as pd
    from cores.data_prefetch import _compute_kr_regime
    bars = {day.strftime("%Y-%m-%d"): {"Close": 100 + i, "Volume": 1000} for i, day in enumerate(pd.bdate_range("2026-08-01", periods=25))}
    metadata = {"source": "kis", "observed_at": "2026-09-11T10:00:00+09:00"}
    assert _compute_kr_regime(bars, bars) == _compute_kr_regime({**bars, "__meta__": metadata}, {**bars, "__meta__": metadata})


def test_current_derived_fact_metadata_survives_mcp_and_prefetch():
    frame = KisSource._snapshot_frame({"MarketCap": 27_000_000_000}, datetime(2026, 9, 11, 10, tzinfo=ZoneInfo("Asia/Seoul")))
    frame.attrs.update(data_status="derived_current_snapshot", unit="KRW", derivation_fields=["stck_prpr", "lstn_stcn"], private_key="never copy")
    payload = _answer(frame, "cap")
    meta = payload["__meta__"]
    assert meta["source"] == "kis" and meta["latest_only"] is True
    assert meta["data_status"] == "derived_current_snapshot"
    assert meta["derivation_fields"] == ["stck_prpr", "lstn_stcn"]
    assert "private_key" not in meta
    text = _dict_to_markdown(payload)
    for marker in ("derived_current_snapshot", "latest_only=True", "source=kis", "unit=KRW", "stck_prpr", "lstn_stcn", "과거 시계열"):
        assert marker in text


@pytest.mark.parametrize("kind", ["cap", "fundamentals"])
def test_latest_snapshot_cannot_be_rendered_as_a_historical_trend(monkeypatch, kind):
    import cores.stock_chart as charts
    values = {"MarketCap": 10_000_000} if kind == "cap" else {"PER": 5.0, "PBR": .8}
    frame = KisSource._snapshot_frame(values, datetime(2026, 9, 11, tzinfo=ZoneInfo("Asia/Seoul")))
    getter = "get_market_cap_by_date" if kind == "cap" else "get_market_fundamental_by_date"
    factory = charts.create_market_cap_chart if kind == "cap" else charts.create_fundamentals_chart
    monkeypatch.setattr(charts, getter, lambda *a, **k: frame)
    monkeypatch.setattr(charts.plt, "subplots", lambda *a, **k: pytest.fail("Cannot draw a history chart from one snapshot"))
    assert factory("005930", company_name="Fixture") is None
