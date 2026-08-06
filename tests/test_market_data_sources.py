"""Falling back between market data providers.

The behaviour under test is the one that failed in production on 2026-08-04:
KRX restricted the server's IP, `cores/stock_chart.py` returned `None` from
every call, and the afternoon report shipped at 17,387 characters instead of
351,311 with no error logged anywhere. Screening survived because it already
had a second source; the report path did not.
"""

from __future__ import annotations

import pandas as pd
import pytest

from cores.market_data import (
    default_chain,
    get_market_ohlcv_by_date,
    set_default_chain,
)
from cores.market_data.schema import has_ohlcv, normalize, to_dashed
from cores.market_data.source import SourceChain, Unavailable, Unsupported

DATES = pd.date_range("2026-08-01", periods=3)


def ohlcv(close: float = 100.0) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Open": [close] * 3,
            "High": [close] * 3,
            "Low": [close] * 3,
            "Close": [close] * 3,
            "Volume": [1_000] * 3,
        },
        index=DATES,
    )


class FakeSource:
    """A source scripted to answer, refuse, or fail."""

    def __init__(self, name: str, *, result=None, raises=None) -> None:
        self.name = name
        self._result = result
        self._raises = raises
        self.calls: list[tuple] = []

    def price_history(self, ticker, start, end, *, adjusted=True):
        self.calls.append(("price_history", ticker))
        if self._raises:
            raise self._raises
        return self._result

    def investor_flows(self, ticker, start, end):
        self.calls.append(("investor_flows", ticker))
        if self._raises:
            raise self._raises
        return self._result

    def ticker_name(self, ticker):
        self.calls.append(("ticker_name", ticker))
        if self._raises:
            raise self._raises
        return self._result


@pytest.fixture(autouse=True)
def _restore_chain():
    yield
    set_default_chain(None)


class TestSchema:
    def test_korean_headers_become_english(self):
        frame = normalize(
            pd.DataFrame(
                {"시가": [1], "고가": [2], "저가": [3], "종가": [4], "거래량": [5]},
                index=["20260801"],
            )
        )

        assert set(frame.columns) == {"Open", "High", "Low", "Close", "Volume"}

    def test_the_index_becomes_dates_and_sorts_oldest_first(self):
        frame = normalize(
            pd.DataFrame({"Close": [2, 1]}, index=["20260802", "20260801"])
        )

        assert isinstance(frame.index, pd.DatetimeIndex)
        assert list(frame["Close"]) == [1, 2]

    def test_rows_without_a_close_are_not_a_price_series(self):
        # A frame that is merely non-empty renders as a blank chart rather than
        # an error, which is how the outage stayed invisible.
        assert not has_ohlcv(pd.DataFrame({"Foo": [1]}, index=DATES[:1]))
        assert has_ohlcv(ohlcv())

    def test_empty_is_not_a_price_series(self):
        assert not has_ohlcv(pd.DataFrame())

    def test_dates_convert_to_the_dashed_form_providers_want(self):
        assert to_dashed("20260804") == "2026-08-04"


class TestChainOrder:
    def test_the_first_source_that_answers_wins(self):
        first = FakeSource("first", result=ohlcv(100))
        second = FakeSource("second", result=ohlcv(200))

        frame = SourceChain([first, second]).fetch("price_history", "005930", "a", "b")

        assert frame["Close"].iloc[0] == 100
        assert second.calls == []

    def test_an_unavailable_source_hands_over(self):
        down = FakeSource("down", raises=Unavailable("IP restricted"))
        up = FakeSource("up", result=ohlcv(200))

        frame = SourceChain([down, up]).fetch("price_history", "005930", "a", "b")

        assert frame["Close"].iloc[0] == 200
        assert up.calls == [("price_history", "005930")]

    def test_a_source_that_lacks_the_capability_is_skipped(self):
        # Not a failure: FinanceDataReader simply has no investor flows.
        without = FakeSource("without", raises=Unsupported("no flows here"))
        with_it = FakeSource("with", result=ohlcv())

        frame = SourceChain([without, with_it]).fetch(
            "investor_flows", "005930", "a", "b"
        )

        assert not frame.empty

    def test_an_unexpected_error_does_not_stop_the_chain(self):
        # A provider bug must not take the report down with it.
        broken = FakeSource("broken", raises=RuntimeError("boom"))
        healthy = FakeSource("healthy", result=ohlcv(300))

        frame = SourceChain([broken, healthy]).fetch("price_history", "005930", "a", "b")

        assert frame["Close"].iloc[0] == 300

    def test_exhausting_every_source_raises_rather_than_returning_empty(self):
        chain = SourceChain(
            [
                FakeSource("a", raises=Unavailable("restricted")),
                FakeSource("b", raises=Unsupported("nope")),
            ]
        )

        with pytest.raises(Unavailable) as excinfo:
            chain.fetch("price_history", "005930", "a", "b")

        # The message has to name every attempt; "not found" is what made the
        # real outage take two hours to diagnose.
        assert "restricted" in str(excinfo.value)
        assert "unsupported" in str(excinfo.value)

    def test_a_chain_needs_at_least_one_source(self):
        with pytest.raises(ValueError):
            SourceChain([])

    def test_a_missing_capability_is_skipped_not_crashed(self):
        class Partial:
            name = "partial"

        chain = SourceChain([Partial(), FakeSource("full", result=ohlcv())])

        assert not chain.fetch("price_history", "005930", "a", "b").empty


class TestConfiguredOrder:
    def test_the_order_comes_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("PRISM_MARKET_DATA_SOURCES", "fdr,krx")
        set_default_chain(None)

        assert default_chain().names == ["fdr", "krx"]

    def test_a_single_source_is_allowed(self, monkeypatch):
        # A host that cannot reach KRX at all should not need a code change.
        monkeypatch.setenv("PRISM_MARKET_DATA_SOURCES", "fdr")
        set_default_chain(None)

        assert default_chain().names == ["fdr"]

    def test_naver_can_be_configured_as_an_investor_flow_fallback(self, monkeypatch):
        monkeypatch.setenv("PRISM_MARKET_DATA_SOURCES", "fdr,naver,krx")
        set_default_chain(None)

        assert default_chain().names == ["fdr", "naver", "krx"]

    def test_an_unknown_name_is_ignored_rather_than_fatal(self, monkeypatch):
        monkeypatch.setenv("PRISM_MARKET_DATA_SOURCES", "nonsense,fdr")
        set_default_chain(None)

        assert default_chain().names == ["fdr"]

    def test_the_default_is_krx_first(self, monkeypatch):
        monkeypatch.delenv("PRISM_MARKET_DATA_SOURCES", raising=False)
        set_default_chain(None)

        assert default_chain().names == ["krx", "fdr"]


class TestCallerFacingApi:
    def test_callers_still_receive_a_frame(self):
        set_default_chain(SourceChain([FakeSource("only", result=ohlcv(150))]))

        frame = get_market_ohlcv_by_date("20260801", "20260803", "005930")

        assert frame["Close"].iloc[0] == 150

    def test_total_exhaustion_reaches_the_caller_as_empty(self):
        # stock_chart reads an empty frame as "skip this chart"; that is the
        # right end state once every source has been asked, and it is logged.
        set_default_chain(
            SourceChain([FakeSource("down", raises=Unavailable("restricted"))])
        )

        frame = get_market_ohlcv_by_date("20260801", "20260803", "005930")

        assert frame.empty


class TestRealSources:
    """Shape of the real adapters, without calling out to the network."""

    def test_fdr_declares_what_it_cannot_do(self):
        from cores.market_data.fdr_source import FdrSource

        source = FdrSource()
        with pytest.raises(Unsupported):
            source.investor_flows("005930", "20260801", "20260803")
        with pytest.raises(Unsupported):
            source.fundamentals("005930", "20260801", "20260803")

    def test_fdr_refuses_an_index_it_has_no_mapping_for(self):
        from cores.market_data.fdr_source import FdrSource

        with pytest.raises(Unsupported):
            FdrSource().index_history("9999", "20260801", "20260803")

    def test_fdr_maps_the_indices_the_reports_chart(self):
        from cores.market_data.fdr_source import INDEX_SYMBOLS

        assert INDEX_SYMBOLS["1001"] == "KS11"
        assert INDEX_SYMBOLS["2001"] == "KQ11"

    def test_krx_import_is_deferred(self):
        # Importing krx_data_client at module scope would make every host
        # without KRX credentials fail to load the package that exists to
        # survive KRX being unavailable.
        import inspect

        from cores.market_data import krx_source

        assert "import krx_data_client" not in inspect.getsource(krx_source).split(
            "def _client"
        )[0]

    def test_both_sources_satisfy_the_protocol(self):
        from cores.market_data.fdr_source import FdrSource
        from cores.market_data.krx_source import KrxSource

        for source in (KrxSource(), FdrSource()):
            for verb in (
                "price_history",
                "index_history",
                "market_cap_history",
                "investor_flows",
                "fundamentals",
                "ticker_name",
            ):
                assert callable(getattr(source, verb)), f"{source.name}.{verb}"
