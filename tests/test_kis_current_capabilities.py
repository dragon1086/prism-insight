"""Current KIS facts must never masquerade as historical observations."""
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

import cores.market_data.kis_source as module
from cores.market_data.kis_source import KisSource
from cores.market_data.source import Unavailable, Unsupported


@pytest.fixture
def source(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 9, 11, 10, tzinfo=ZoneInfo("Asia/Seoul"))

    monkeypatch.setattr(module, "datetime", Clock)
    obj = KisSource()
    obj.calls = []
    obj.output = {"stck_prpr": "27000", "lstn_stcn": "1000000",
                  "per": "8.5", "pbr": "0.5", "eps": "3176", "bps": "54000",
                  "bstp_kor_isnm": "기타금융"}

    def fetch(url, tr, params):
        obj.calls.append((url, tr, params))
        return SimpleNamespace(output=obj.output)

    monkeypatch.setattr(obj, "_fetch", fetch)
    monkeypatch.setattr(obj, "_master_names", lambda: {}, raising=False)
    return obj


def test_current_facts_share_quote_and_preserve_scope(source):
    cap = source.market_cap_history("012630", "20260101", "20260911")
    facts = source.fundamentals("012630", "20260101", "20260911")
    assert cap["MarketCap"].tolist() == [27_000_000_000]
    assert cap.attrs["data_status"] == "derived_current_snapshot"
    assert cap.attrs["derivation_fields"] == ["stck_prpr", "lstn_stcn"]
    assert facts["PER"].tolist() == [8.5]
    assert len(source.calls) == 1
    for frame in [cap, facts]:
        assert frame.index.tolist() == [pd.Timestamp("20260911")]
        assert frame.attrs["latest_only"] is True
        assert frame.attrs["source"] == "kis"
        assert frame.attrs["as_of"].startswith("2026-09-11T10:00")
        assert frame.attrs["bar_status"] == "UNKNOWN"


@pytest.mark.parametrize("method", ["market_cap_history", "fundamentals"])
def test_historical_request_never_fetches_current_quote(source, method):
    with pytest.raises(Unsupported):
        getattr(source, method)("012630", "20260901", "20260910")
    assert source.calls == []


def test_zero_ratios_are_missing_not_cheap(source):
    source.output.update(per="0", pbr="0", eps="-10", bps="-20")
    result = source.fundamentals("012630", "20260911", "20260911")
    assert pd.isna(result.iloc[0]["PER"])
    assert pd.isna(result.iloc[0]["PBR"])
    assert result.iloc[0]["EPS"] == -10


@pytest.mark.parametrize("bad", [None, [], "bad", {}])
def test_malformed_quote_is_unavailable(source, bad):
    source.output = bad
    with pytest.raises(Unavailable):
        source.fundamentals("012630", "20260911", "20260911")


@pytest.mark.parametrize("price,shares", [("", "100"), ("inf", "100"), ("0", "100"), ("1", "-1")])
def test_invalid_cap_never_becomes_zero(source, price, shares):
    source.output.update(stck_prpr=price, lstn_stcn=shares)
    with pytest.raises(Unavailable):
        source.market_cap_history("012630", "20260911", "20260911")


def test_empty_fundamentals_unavailable(source):
    source.output = {"per": "", "pbr": "NaN", "eps": "inf", "bps": ""}
    with pytest.raises(Unavailable):
        source.fundamentals("012630", "20260911", "20260911")


def test_cache_expiry_fetches_again(source, monkeypatch):
    clock = [1.0]
    monkeypatch.setattr(module, "monotonic", lambda: clock[0])
    source.fundamentals("012630", "20260911", "20260911")
    clock[0] = 32.0
    source.fundamentals("012630", "20260911", "20260911")
    assert len(source.calls) == 2


def test_quote_cache_cannot_cross_kst_midnight(source, monkeypatch):
    source.fundamentals("012630", "20260911", "20260911")

    class Tomorrow(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 9, 12, 0, tzinfo=ZoneInfo("Asia/Seoul"))

    monkeypatch.setattr(module, "datetime", Tomorrow)
    result = source.fundamentals("012630", "20260912", "20260912")
    assert len(source.calls) == 2
    assert result.attrs["as_of"].startswith("2026-09-12")
    with pytest.raises(Unsupported):
        source.fundamentals("012630", "20260911", "20260911")


def test_quote_ticker_mismatch_is_not_accepted(source):
    source.output["stck_shrn_iscd"] = "005930"
    with pytest.raises(Unavailable, match="different ticker"):
        source.fundamentals("012630", "20260911", "20260911")


def test_fetch_error_does_not_return_stale_quote(source, monkeypatch):
    source.fundamentals("012630", "20260911", "20260911")
    monkeypatch.setattr(module, "monotonic", lambda: float("inf"))

    def fail(*args):
        raise Unavailable("transport failed")

    monkeypatch.setattr(source, "_fetch", fail)
    with pytest.raises(Unavailable, match="transport failed"):
        source.fundamentals("012630", "20260911", "20260911")


def test_sector_uses_quote_but_never_market_name(source):
    assert source.sector_info("012630")["sector"] == "기타금융"
    source.output = {"rprs_mrkt_kor_name": "KOSPI200"}
    source._quote_cache.clear()
    with pytest.raises(Unavailable, match="sector"):
        source.sector_info("012630")


def test_company_name_uses_official_stock_info(source):
    source.output = {"pdno": "012630", "prdt_abrv_name": " HDC "}
    assert source.ticker_name("012630") == "HDC"
    assert source.calls[0][1:] == ("CTPF1002R", {"PRDT_TYPE_CD": "300", "PDNO": "012630"})


def test_company_name_prefers_official_master_without_quote_rpc(source, monkeypatch):
    monkeypatch.setattr(source, "_master_names", lambda: {"005930": "삼성전자", "012630": "HDC"})
    assert source.ticker_name("005930") == "삼성전자"
    assert source.ticker_name("012630") == "HDC"
    assert source.calls == []


def test_master_download_failure_falls_back_to_kis_stock_info(source, monkeypatch):
    def unavailable():
        raise RuntimeError("master unavailable")

    monkeypatch.setattr(source, "_master_names", unavailable)
    source.output = {"pdno": "005930", "prdt_name": "삼성전자"}
    assert source.ticker_name("005930") == "삼성전자"
    assert len(source.calls) == 1


def test_master_helper_reuses_shared_daily_loader(monkeypatch):
    import cores.kis_market_snapshot as master

    monkeypatch.setattr(master, "fetch_kis_master_universe", lambda: {"005930": "삼성전자"})
    obj = KisSource()

    def forbidden(*args):
        pytest.fail("master hit must not authenticate or request stock-info")

    monkeypatch.setattr(obj, "_fetch", forbidden)
    assert obj.ticker_name("005930") == "삼성전자"


def test_company_name_rejects_market_and_wrong_ticker(source):
    source.output = {"rprs_mrkt_kor_name": "KOSPI200"}
    with pytest.raises(Unavailable, match="company name"):
        source.ticker_name("012630")
    source.output = {"pdno": "005930", "prdt_name": "삼성전자"}
    with pytest.raises(Unavailable, match="different ticker"):
        source.ticker_name("012630")


def test_company_name_accepts_observed_kis_internal_product_identifier(source):
    source.output = {"pdno": "00000A005930", "prdt_abrv_name": "삼성전자"}
    assert source.ticker_name("005930") == "삼성전자"


@pytest.mark.parametrize("identifier", ["00000A000660", "00000B005930", "other005930"])
def test_company_name_rejects_wrong_internal_identifier(source, identifier):
    source.output = {"pdno": identifier, "prdt_name": "삼성전자"}
    with pytest.raises(Unavailable, match="different ticker"):
        source.ticker_name("005930")
