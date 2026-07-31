from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
from datetime import date, datetime
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
from pathlib import Path
from stat import S_IMODE
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from prism_core.data.contracts import DataQualityStatus
from prism_core.data.providers.kis import ProviderPayload
from prism_core.market.krx import KRXMarketContextProvider, OfficialKRXEquityMarketClient


KST = ZoneInfo("Asia/Seoul")


def test_official_krx_index_fetch_uses_authenticated_wrapper_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instances: list[object] = []

    class AuthManager:
        COOKIE_PATH = Path.home() / ".krx_session.json"
        LEGACY_COOKIE_PATH = Path.home() / ".krx_cookies.json"
        LOCK_PATH = Path.home() / ".krx_session.lock"

        def __init__(self) -> None:
            self.login_calls = 0

        def login(self) -> None:
            self.login_calls += 1

    class StockModule:
        class KRXDataClient:
            ISIN_CACHE_PATH = Path.home() / ".krx_isin_cache.json"

            def __init__(self, *, auto_login: bool) -> None:
                assert auto_login is False
                self._auth_manager = AuthManager()
                instances.append(self)

            def get_index_ohlcv_by_date(
                self, *args: object, **kwargs: object
            ) -> pd.DataFrame:
                assert self._auth_manager.login_calls == 1
                assert args == ("20260601", "20260729", "1001")
                assert kwargs == {}
                return pd.DataFrame()

    monkeypatch.setattr(
        OfficialKRXEquityMarketClient,
        "_stock_module",
        staticmethod(lambda: StockModule()),
    )

    client = OfficialKRXEquityMarketClient(session_source_path=None)
    client.get_index_history(date(2026, 6, 1), date(2026, 7, 29), "1001")

    instance = instances[0]
    auth = instance._auth_manager  # type: ignore[attr-defined]
    isolated_root = auth.COOKIE_PATH.parent
    assert isolated_root != Path.home()
    assert S_IMODE(isolated_root.stat().st_mode) == 0o700
    assert {
        auth.COOKIE_PATH.parent,
        auth.LEGACY_COOKIE_PATH.parent,
        auth.LOCK_PATH.parent,
        instance.ISIN_CACHE_PATH.parent,  # type: ignore[attr-defined]
    } == {isolated_root}
    client.close()
    assert not isolated_root.exists()


def test_official_krx_reuses_only_an_isolated_read_only_session_copy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "user-home-session.json"
    source.write_text(
        json.dumps(
            {
                "cookies": {"session": "private-fixture"},
                "krx_cookies": [],
                "last_login": datetime.now().isoformat(),
                "last_validated": datetime.now().isoformat(),
            }
        ),
        encoding="utf-8",
    )
    source.chmod(0o600)
    source_before = (
        source.stat().st_mtime_ns,
        source.stat().st_size,
        source.stat().st_mode,
        hashlib.sha256(source.read_bytes()).hexdigest(),
    )
    instances: list[object] = []

    class AuthManager:
        COOKIE_PATH = Path("unset")

        def __init__(self) -> None:
            self.login_calls: list[bool] = []
            self.loaded = False

        @property
        def is_logged_in(self) -> bool:
            return self.loaded

        def _load_session(self) -> bool:
            assert self.COOKIE_PATH.read_text(encoding="utf-8") == source.read_text(
                encoding="utf-8"
            )
            self.loaded = True
            return True

        def login(self, force: bool = False) -> bool:
            self.login_calls.append(force)
            raise AssertionError("session-copy mode must not invoke provider login")

    class StockModule:
        class KRXDataClient:
            def __init__(self, **kwargs: object) -> None:
                assert kwargs == {
                    "auto_login": False,
                    "krx_id": "SESSION_COPY_ONLY",
                    "krx_pw": "SESSION_COPY_ONLY",
                    "login_method": "krx",
                }
                self._auth_manager = AuthManager()
                instances.append(self)

            def get_index_ohlcv_by_date(self, *args: object) -> pd.DataFrame:
                assert self._auth_manager.login() is True
                return pd.DataFrame()

    monkeypatch.setattr(
        OfficialKRXEquityMarketClient,
        "_stock_module",
        staticmethod(lambda: StockModule()),
    )

    client = OfficialKRXEquityMarketClient(session_source_path=source)
    client.get_index_history(date(2026, 6, 1), date(2026, 7, 29), "1001")

    instance = instances[0]
    auth = instance._auth_manager  # type: ignore[attr-defined]
    isolated_copy = auth.COOKIE_PATH
    assert isolated_copy != source
    assert S_IMODE(isolated_copy.stat().st_mode) == 0o400
    assert source_before == (
        source.stat().st_mtime_ns,
        source.stat().st_size,
        source.stat().st_mode,
        hashlib.sha256(source.read_bytes()).hexdigest(),
    )
    client.close()
    assert not isolated_copy.parent.exists()


def test_official_krx_prefers_explicit_environment_credentials_over_stale_session_copy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "stale-session.json"
    source.write_text(
        json.dumps({"cookies": {"session": "stale"}, "last_login": "2026-07-01"}),
        encoding="utf-8",
    )
    source.chmod(0o600)
    monkeypatch.setenv("KRX_LOGIN_METHOD", "krx")
    monkeypatch.setenv("KRX_ID", "synthetic-id")
    monkeypatch.setenv("KRX_PW", "synthetic-password")
    login_calls: list[bool] = []

    class AuthManager:
        def _load_session(self) -> bool:
            raise AssertionError("explicit credentials must not load a stale session copy")

        def login(self) -> None:
            login_calls.append(True)

    class StockModule:
        class KRXDataClient:
            def __init__(self, *, auto_login: bool) -> None:
                assert auto_login is False
                self._auth_manager = AuthManager()

            def get_index_ohlcv_by_date(self, *args: object) -> pd.DataFrame:
                return pd.DataFrame()

    monkeypatch.setattr(
        OfficialKRXEquityMarketClient,
        "_stock_module",
        staticmethod(lambda: StockModule()),
    )

    client = OfficialKRXEquityMarketClient(session_source_path=source)
    client.get_index_history(date(2026, 6, 1), date(2026, 7, 29), "1001")

    assert login_calls == [True]
    client.close()


def test_official_krx_stock_endpoint_binds_authoritative_equity_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class AuthManager:
        def login(self) -> None:
            return None

    class StockModule:
        class KRXDataClient:
            def __init__(self, *, auto_login: bool) -> None:
                assert auto_login is False
                self._auth_manager = AuthManager()

            def get_market_ohlcv_by_ticker(
                self, *args: object, **kwargs: object
            ) -> pd.DataFrame:
                assert args == ("20260729",)
                assert kwargs == {"market": "KOSPI"}
                return pd.DataFrame({"Close": [100]}, index=["005930"])

    monkeypatch.setattr(
        OfficialKRXEquityMarketClient,
        "_stock_module",
        staticmethod(lambda: StockModule()),
    )

    frame = OfficialKRXEquityMarketClient(session_source_path=None).get_equity_ohlcv(
        date(2026, 7, 29), "KOSPI"
    )

    assert frame.loc["005930", "SecurityType"] == "EQUITY"


class FixtureKRXClient:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def get_index_history(self, start: date, end: date, ticker: str) -> pd.DataFrame:
        self.calls.append(("index", start, end, ticker))
        dates = pd.bdate_range(end=end, periods=25)
        return pd.DataFrame({"Close": range(100, 125)}, index=dates)

    def get_equity_ohlcv(self, session: date, market: str) -> pd.DataFrame:
        self.calls.append(("equity", session, market))
        closes = [101, 99, 100] if session == date(2026, 7, 29) else [100, 100, 100]
        amounts = [500_000_000, 300_000_000, 200_000_000]
        symbols = ["000001", "000002", "000003"]
        if market == "KOSDAQ":
            closes = [103, 99] if session == date(2026, 7, 29) else [100, 100]
            amounts = [400_000_000, 100_000_000]
            symbols = ["100001", "100002"]
        return pd.DataFrame(
            {
                "Close": closes,
                "Amount": amounts,
                "SecurityType": ["EQUITY"] * len(closes),
            },
            index=symbols,
        )

    def get_sector_classification(self, session: date, market: str) -> dict[str, str]:
        self.calls.append(("sector", session, market))
        if market == "KOSPI":
            return {
                "000001": "전기 전자",
                "000002": "자동차",
                "000003": "전기 전자",
            }
        return {"100001": "반도체", "100002": "바이오"}

    def get_investor_flows(self, session: date, market: str) -> pd.DataFrame:
        self.calls.append(("flows", session, market))
        return pd.DataFrame(
            {"NetPurchaseKRW": [100, -40]},
            index=["Foreign", "Institution"],
        )


@pytest.mark.asyncio
async def test_provider_fetches_official_index_complete_equities_and_optional_flows() -> None:
    client = FixtureKRXClient()
    provider = KRXMarketContextProvider(
        client=client,
        clock=lambda: datetime(2026, 7, 29, 15, 34, tzinfo=KST),
    )

    payload = await provider.fetch_market_context(
        as_of=datetime(2026, 7, 29, 15, 33, tzinfo=KST)
    )

    assert payload.provider == "KRX"
    assert payload.quality is DataQualityStatus.FRESH
    assert payload.observed_at == datetime(2026, 7, 29, 15, 30, tzinfo=KST)
    assert payload.available_at == datetime(2026, 7, 29, 15, 34, tzinfo=KST)
    assert payload.payload["session_date"] == "2026-07-29"
    assert len(payload.payload["index_history"]) == 25
    assert payload.payload["equity_breadth"] == {
        "advance_count": 2,
        "decline_count": 2,
        "unchanged_count": 1,
        "eligible_equity_count": 5,
        "excluded_non_equity_count": 0,
        "unclassified_equity_count": 0,
        "universe": "KOSPI_KOSDAQ_EQUITIES",
    }
    assert payload.payload["investor_flows"] == {
        "foreign_net_purchase_krw": "200",
        "institution_net_purchase_krw": "-80",
    }
    assert payload.payload["group_taxonomy"]["version"] == "KRX_SECTOR_2026-07-29"
    assert len(payload.payload["group_taxonomy"]["assignments"]) == 5
    assert payload.payload["group_observations"][0] == {
        "current_close": "101",
        "previous_close": "100",
        "provider_symbol": "000001",
        "turnover_krw": "500000000",
    }
    component_hashes = payload.payload["component_hashes"]
    assert isinstance(component_hashes, dict)
    assert set(component_hashes) == {
        "equity_current_KOSDAQ",
        "equity_current_KOSPI",
        "equity_previous_KOSDAQ",
        "equity_previous_KOSPI",
        "index_history",
        "investor_flows_KOSDAQ",
        "investor_flows_KOSPI",
        "sector_taxonomy_KOSDAQ",
        "sector_taxonomy_KOSPI",
    }
    assert all(
        len(component_hash) == 64
        for component_hash in component_hashes.values()
    )
    assert [(call[0], call[-1]) for call in client.calls] == [
        ("index", "1001"),
        ("equity", "KOSPI"),
        ("equity", "KOSDAQ"),
        ("equity", "KOSPI"),
        ("equity", "KOSDAQ"),
        ("sector", "KOSPI"),
        ("sector", "KOSDAQ"),
        ("flows", "KOSPI"),
        ("flows", "KOSDAQ"),
    ]


@pytest.mark.asyncio
async def test_provider_decimal_arithmetic_is_independent_of_ambient_context() -> None:
    class HighMagnitudeFlowClient(FixtureKRXClient):
        def get_investor_flows(self, session: date, market: str) -> pd.DataFrame:
            self.calls.append(("flows", session, market))
            return pd.DataFrame(
                {
                    "NetPurchaseKRW": [
                        Decimal("999999999999999999"),
                        Decimal("-888888888888888888"),
                    ]
                },
                index=["Foreign", "Institution"],
            )

    async def fetch() -> ProviderPayload:
        return await KRXMarketContextProvider(
            client=HighMagnitudeFlowClient(),
            clock=lambda: datetime(2026, 7, 29, 15, 34, tzinfo=KST),
        ).fetch_market_context(
            as_of=datetime(2026, 7, 29, 15, 33, tzinfo=KST)
        )

    expected = await fetch()
    with localcontext(Context(prec=6, rounding=ROUND_HALF_EVEN)):
        actual = await fetch()

    assert actual.payload["equity_breadth"] == expected.payload["equity_breadth"]
    assert actual.payload["investor_flows"] == expected.payload["investor_flows"]


@pytest.mark.asyncio
async def test_taxonomy_failure_preserves_authoritative_index_and_breadth_as_partial() -> None:
    class TaxonomyUnavailableClient(FixtureKRXClient):
        def get_sector_classification(
            self, session: date, market: str
        ) -> dict[str, str]:
            del session, market
            raise RuntimeError("sanitized fixture taxonomy outage")

    payload = await KRXMarketContextProvider(
        client=TaxonomyUnavailableClient(),
        clock=lambda: datetime(2026, 7, 29, 15, 34, tzinfo=KST),
    ).fetch_market_context(as_of=datetime(2026, 7, 29, 15, 33, tzinfo=KST))

    assert payload.quality is DataQualityStatus.FRESH
    assert payload.payload["index_history"]
    breadth = payload.payload["equity_breadth"]
    assert isinstance(breadth, dict)
    assert breadth["eligible_equity_count"] == 5
    assert "group_taxonomy" not in payload.payload
    assert payload.payload["group_leadership_availability"] == {
        "quality": "UNAVAILABLE",
        "reason_codes": ["KRX_SECTOR_TAXONOMY_UNAVAILABLE"],
    }


@pytest.mark.asyncio
async def test_provider_closes_its_owned_isolated_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FixtureKRXClient()
    client.closed = False  # type: ignore[attr-defined]

    def close() -> None:
        client.closed = True  # type: ignore[attr-defined]

    client.close = close  # type: ignore[attr-defined]
    monkeypatch.setattr(
        "prism_core.market.krx.OfficialKRXEquityMarketClient", lambda: client
    )
    provider = KRXMarketContextProvider(
        clock=lambda: datetime(2026, 7, 29, 15, 34, tzinfo=KST)
    )

    await provider.fetch_market_context(
        as_of=datetime(2026, 7, 29, 15, 33, tzinfo=KST)
    )

    assert client.closed is True  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_provider_rejects_non_equity_rows_instead_of_polluting_breadth() -> None:
    class ContaminatedClient(FixtureKRXClient):
        def get_equity_ohlcv(self, session: date, market: str) -> pd.DataFrame:
            frame = super().get_equity_ohlcv(session, market)
            if market == "KOSPI":
                frame.loc["leveraged-etf"] = [108, 0, "LEVERAGED_ETF"]
            return frame

    payload = await KRXMarketContextProvider(
        client=ContaminatedClient(),
        clock=lambda: datetime(2026, 7, 29, 15, 34, tzinfo=KST),
    ).fetch_market_context(as_of=datetime(2026, 7, 29, 15, 33, tzinfo=KST))

    assert payload.payload["equity_breadth"]["eligible_equity_count"] == 5
    assert payload.payload["equity_breadth"]["excluded_non_equity_count"] == 1


@pytest.mark.asyncio
async def test_provider_marks_unclassified_equities_partial_instead_of_fresh() -> None:
    class IncompletePriorUniverseClient(FixtureKRXClient):
        def get_equity_ohlcv(self, session: date, market: str) -> pd.DataFrame:
            frame = super().get_equity_ohlcv(session, market)
            if session == date(2026, 7, 28) and market == "KOSPI":
                frame = frame.iloc[:-1]
            return frame

    payload = await KRXMarketContextProvider(
        client=IncompletePriorUniverseClient(),
        clock=lambda: datetime(2026, 7, 29, 15, 34, tzinfo=KST),
    ).fetch_market_context(as_of=datetime(2026, 7, 29, 15, 33, tzinfo=KST))

    assert payload.quality is DataQualityStatus.PARTIAL
    breadth = payload.payload["equity_breadth"]
    assert isinstance(breadth, dict)
    assert breadth["unclassified_equity_count"] == 1
    assert breadth["eligible_equity_count"] == 4


@pytest.mark.asyncio
async def test_provider_fails_closed_when_equity_classification_is_unproven() -> None:
    class UnclassifiedClient(FixtureKRXClient):
        def get_equity_ohlcv(self, session: date, market: str) -> pd.DataFrame:
            return super().get_equity_ohlcv(session, market).drop(columns="SecurityType")

    with pytest.raises(ValueError, match="authoritative security types"):
        await KRXMarketContextProvider(
            client=UnclassifiedClient(),
            clock=lambda: datetime(2026, 7, 29, 15, 34, tzinfo=KST),
        ).fetch_market_context(as_of=datetime(2026, 7, 29, 15, 33, tzinfo=KST))


@pytest.mark.asyncio
async def test_provider_fails_closed_on_duplicate_equity_identity() -> None:
    class DuplicateTickerClient(FixtureKRXClient):
        def get_equity_ohlcv(self, session: date, market: str) -> pd.DataFrame:
            frame = super().get_equity_ohlcv(session, market)
            if market == "KOSDAQ":
                frame.index = [
                    "000001" if index == 0 else ticker
                    for index, ticker in enumerate(frame.index)
                ]
            return frame

    with pytest.raises(ValueError, match="duplicate equity identities"):
        await KRXMarketContextProvider(
            client=DuplicateTickerClient(),
            clock=lambda: datetime(2026, 7, 29, 15, 34, tzinfo=KST),
        ).fetch_market_context(as_of=datetime(2026, 7, 29, 15, 33, tzinfo=KST))


@pytest.mark.asyncio
async def test_provider_identity_binds_underlying_constituents_not_only_breadth_counts() -> None:
    first = await KRXMarketContextProvider(
        client=FixtureKRXClient(),
        clock=lambda: datetime(2026, 7, 29, 15, 34, tzinfo=KST),
    ).fetch_market_context(as_of=datetime(2026, 7, 29, 15, 33, tzinfo=KST))

    class SameCountsDifferentConstituents(FixtureKRXClient):
        def get_equity_ohlcv(self, session: date, market: str) -> pd.DataFrame:
            frame = super().get_equity_ohlcv(session, market)
            frame.index = [f"ALT-{ticker}" for ticker in frame.index]
            return frame

    second = await KRXMarketContextProvider(
        client=SameCountsDifferentConstituents(),
        clock=lambda: datetime(2026, 7, 29, 15, 34, tzinfo=KST),
    ).fetch_market_context(as_of=datetime(2026, 7, 29, 15, 33, tzinfo=KST))

    assert first.payload["equity_breadth"] == second.payload["equity_breadth"]
    assert first.raw_payload_hash != second.raw_payload_hash


@pytest.mark.asyncio
async def test_provider_keeps_available_flows_and_marks_missing_market() -> None:
    class PartialFlowClient(FixtureKRXClient):
        def get_investor_flows(self, session: date, market: str) -> pd.DataFrame:
            if market == "KOSDAQ":
                raise TimeoutError("fixture optional flow timeout")
            return super().get_investor_flows(session, market)

    payload = await KRXMarketContextProvider(
        client=PartialFlowClient(),
        clock=lambda: datetime(2026, 7, 29, 15, 34, tzinfo=KST),
    ).fetch_market_context(as_of=datetime(2026, 7, 29, 15, 33, tzinfo=KST))

    assert payload.payload["investor_flows"] == {
        "foreign_net_purchase_krw": "100",
        "institution_net_purchase_krw": "-40",
    }
    assert payload.payload["investor_flow_markets"] == ["KOSPI"]
    assert payload.payload["investor_flow_missing_markets"] == ["KOSDAQ"]


@pytest.mark.asyncio
async def test_provider_treats_empty_flow_frames_as_missing_not_zero() -> None:
    class EmptyFlowClient(FixtureKRXClient):
        def get_investor_flows(self, session: date, market: str) -> pd.DataFrame:
            del session, market
            return pd.DataFrame()

    payload = await KRXMarketContextProvider(
        client=EmptyFlowClient(),
        clock=lambda: datetime(2026, 7, 29, 15, 34, tzinfo=KST),
    ).fetch_market_context(as_of=datetime(2026, 7, 29, 15, 33, tzinfo=KST))

    assert "investor_flows" not in payload.payload
    assert payload.payload["investor_flow_markets"] == []
    assert payload.payload["investor_flow_missing_markets"] == ["KOSDAQ", "KOSPI"]


@pytest.mark.asyncio
async def test_provider_bounds_each_official_request() -> None:
    request_finished = threading.Event()

    class SlowIndexClient(FixtureKRXClient):
        def get_index_history(self, start: date, end: date, ticker: str) -> pd.DataFrame:
            try:
                time.sleep(0.05)
                return super().get_index_history(start, end, ticker)
            finally:
                request_finished.set()

    provider = KRXMarketContextProvider(
        client=SlowIndexClient(),
        clock=lambda: datetime(2026, 7, 29, 15, 34, tzinfo=KST),
        timeout_seconds=0.01,
    )

    with pytest.raises(TimeoutError, match="KRX index_history request timed out"):
        await provider.fetch_market_context(
            as_of=datetime(2026, 7, 29, 15, 33, tzinfo=KST)
        )

    assert request_finished.is_set(), "timed-out KRX work must not outlive fail-soft return"


@pytest.mark.asyncio
async def test_call_preserves_timeout_raised_by_completed_client_request() -> None:
    provider = KRXMarketContextProvider(
        client=FixtureKRXClient(),
        clock=lambda: datetime(2026, 7, 29, 15, 34, tzinfo=KST),
        timeout_seconds=1,
    )

    def raise_client_timeout() -> pd.DataFrame:
        raise TimeoutError("client transport timeout")

    with pytest.raises(TimeoutError, match="client transport timeout"):
        await provider._call("fixture", raise_client_timeout)


@pytest.mark.asyncio
async def test_provider_defers_cancellation_and_owned_client_cleanup_until_worker_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request_started = threading.Event()
    release_request = threading.Event()
    request_finished = threading.Event()

    class CancellationTrackingClient(FixtureKRXClient):
        def __init__(self) -> None:
            super().__init__()
            self.close_called = False
            self.worker_finished_when_closed = False

        def get_index_history(self, start: date, end: date, ticker: str) -> pd.DataFrame:
            request_started.set()
            try:
                assert release_request.wait(timeout=1)
                return super().get_index_history(start, end, ticker)
            finally:
                request_finished.set()

        def close(self) -> None:
            self.close_called = True
            self.worker_finished_when_closed = request_finished.is_set()

    client = CancellationTrackingClient()
    monkeypatch.setattr(
        "prism_core.market.krx.OfficialKRXEquityMarketClient", lambda: client
    )
    provider = KRXMarketContextProvider(
        clock=lambda: datetime(2026, 7, 29, 15, 34, tzinfo=KST),
        timeout_seconds=0.01,
    )
    task = asyncio.create_task(
        provider.fetch_market_context(
            as_of=datetime(2026, 7, 29, 15, 33, tzinfo=KST)
        )
    )

    deadline = asyncio.get_running_loop().time() + 1
    while not request_started.is_set() and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.001)
    assert request_started.is_set()
    await asyncio.sleep(0.02)
    task.cancel()
    await asyncio.sleep(0.01)
    try:
        assert not task.done(), "cancellation must wait for synchronous worker cleanup"
        assert client.close_called is False
    finally:
        release_request.set()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert client.close_called is True
    assert client.worker_finished_when_closed is True


@pytest.mark.asyncio
async def test_provider_normalizes_pykrx_date_rows_and_investor_columns() -> None:
    class PyKRXShapedClient(FixtureKRXClient):
        def get_investor_flows(self, session: date, market: str) -> pd.DataFrame:
            del market
            return pd.DataFrame(
                {"외국인합계": [100], "기관합계": [-40], "개인": [-60]},
                index=[pd.Timestamp(session)],
            )

    payload = await KRXMarketContextProvider(
        client=PyKRXShapedClient(),
        clock=lambda: datetime(2026, 7, 29, 15, 34, tzinfo=KST),
    ).fetch_market_context(as_of=datetime(2026, 7, 29, 15, 33, tzinfo=KST))

    assert payload.payload["investor_flows"] == {
        "foreign_net_purchase_krw": "200",
        "institution_net_purchase_krw": "-80",
    }
