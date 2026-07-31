"""Bounded official KRX market-context provider.

The provider reads only KOSPI index, KOSPI/KOSDAQ equity OHLCV, and aggregate
investor-flow data. It has no broker, account, holdings, or order capability.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import os
import stat
import tempfile
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable, Protocol
from zoneinfo import ZoneInfo

import pandas as pd

from prism_core.data.contracts import DataQualityStatus
from prism_core.data.exchange_calendar import ExchangeMarket, latest_completed_session
from prism_core.data.providers.kis import ProviderPayload


class KRXEquityMarketClient(Protocol):
    """Synchronous official-data client restricted to equity market context."""

    def get_index_history(self, start: date, end: date, ticker: str) -> pd.DataFrame: ...

    def get_equity_ohlcv(self, session: date, market: str) -> pd.DataFrame: ...

    def get_investor_flows(self, session: date, market: str) -> pd.DataFrame: ...


class OfficialKRXEquityMarketClient:
    """Lazy authenticated KRX wrapper whose market endpoint contains equities."""

    DEFAULT_SESSION_SOURCE_PATH = Path.home() / ".krx_session.json"
    _MAX_SESSION_BYTES = 1_000_000

    def __init__(
        self,
        *,
        session_source_path: Path | None = DEFAULT_SESSION_SOURCE_PATH,
    ) -> None:
        self._session_directory: tempfile.TemporaryDirectory[str] | None = None
        self._client: object | None = None
        self._session_source_path = session_source_path

    @staticmethod
    def _stock_module() -> object:
        return importlib.import_module("krx_data_client")

    def _data_client(self) -> object:
        if self._client is not None:
            return self._client
        session_directory = tempfile.TemporaryDirectory(
            prefix="prism-insight-krx-market-context-"
        )
        session_root = Path(session_directory.name)
        os.chmod(session_root, 0o700)
        try:
            stock = self._stock_module()
            session_source = self._session_source_path
            use_session_copy = session_source is not None and session_source.exists()
            if use_session_copy:
                client = stock.KRXDataClient(  # type: ignore[attr-defined]
                    auto_login=False,
                    krx_id="SESSION_COPY_ONLY",
                    krx_pw="SESSION_COPY_ONLY",
                    login_method="krx",
                )
            else:
                client = stock.KRXDataClient(  # type: ignore[attr-defined]
                    auto_login=False
                )
            auth_manager = client._auth_manager  # type: ignore[attr-defined]
            auth_manager.COOKIE_PATH = session_root / "session.json"
            auth_manager.LEGACY_COOKIE_PATH = session_root / "legacy-cookies.json"
            auth_manager.LOCK_PATH = session_root / "session.lock"
            client.ISIN_CACHE_PATH = session_root / "isin-cache.json"
            if use_session_copy:
                assert session_source is not None
                self._copy_session_read_only(
                    source=session_source,
                    destination=auth_manager.COOKIE_PATH,
                )
                if not auth_manager._load_session():
                    raise RuntimeError("isolated KRX session copy could not be loaded")

                def session_copy_login(force: bool = False) -> bool:
                    return not force and bool(auth_manager.is_logged_in)

                auth_manager.login = session_copy_login
            else:
                auth_manager.login()
        except Exception:
            session_directory.cleanup()
            raise
        self._session_directory = session_directory
        self._client = client
        return client

    @classmethod
    def default_session_copy_available(cls) -> bool:
        path = cls.DEFAULT_SESSION_SOURCE_PATH
        return path.exists() and path.is_file() and not path.is_symlink()

    @classmethod
    def _copy_session_read_only(cls, *, source: Path, destination: Path) -> None:
        source_fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            source_stat = os.fstat(source_fd)
            if not stat.S_ISREG(source_stat.st_mode):
                raise ValueError("KRX session source must be a regular file")
            if source_stat.st_uid != os.getuid():
                raise PermissionError("KRX session source must be owned by the current user")
            if source_stat.st_mode & 0o022:
                raise PermissionError("KRX session source must not be group/world writable")
            if source_stat.st_size <= 0 or source_stat.st_size > cls._MAX_SESSION_BYTES:
                raise ValueError("KRX session source size is outside the allowed bound")
            content = bytearray()
            while len(content) <= cls._MAX_SESSION_BYTES:
                chunk = os.read(
                    source_fd,
                    min(65_536, cls._MAX_SESSION_BYTES + 1 - len(content)),
                )
                if not chunk:
                    break
                content.extend(chunk)
            if len(content) > cls._MAX_SESSION_BYTES:
                raise ValueError("KRX session source exceeded the allowed bound")
        finally:
            os.close(source_fd)
        try:
            parsed = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("KRX session source is not valid JSON") from None
        if not isinstance(parsed, dict) or not parsed.get("cookies") or not parsed.get(
            "last_login"
        ):
            raise ValueError("KRX session source omitted required session fields")
        destination_fd = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o400,
        )
        try:
            view = memoryview(content)
            while view:
                written = os.write(destination_fd, view)
                view = view[written:]
            os.fsync(destination_fd)
        finally:
            os.close(destination_fd)

    def close(self) -> None:
        self._client = None
        session_directory = getattr(self, "_session_directory", None)
        if session_directory is not None:
            session_directory.cleanup()
            self._session_directory = None

    def __del__(self) -> None:
        self.close()

    def get_index_history(self, start: date, end: date, ticker: str) -> pd.DataFrame:
        client = self._data_client()
        return client.get_index_ohlcv_by_date(  # type: ignore[attr-defined,no-any-return]
            start.strftime("%Y%m%d"),
            end.strftime("%Y%m%d"),
            ticker,
        )

    def get_equity_ohlcv(self, session: date, market: str) -> pd.DataFrame:
        client = self._data_client()
        frame = client.get_market_ohlcv_by_ticker(  # type: ignore[attr-defined]
            session.strftime("%Y%m%d"), market=market
        )
        # MDCSTAT01501 with STK/KSQ is KRX's listed-stock endpoint; ETF/ETN
        # products are served by separate product endpoints. Bind that official
        # classification to every row so downstream breadth fails closed if a
        # different client cannot prove the same universe.
        frame = frame.copy()
        frame["SecurityType"] = "EQUITY"
        return frame

    def get_investor_flows(self, session: date, market: str) -> pd.DataFrame:
        del session, market
        raise NotImplementedError(
            "the authenticated KRX wrapper exposes security-level, not market-level, "
            "investor flows"
        )


def _column(frame: pd.DataFrame, *names: str) -> str:
    for name in names:
        if name in frame.columns:
            return name
    raise ValueError("KRX response omitted a required normalized column")


def _decimal(value: object) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("KRX response contained a non-numeric value") from None
    if not result.is_finite():
        raise ValueError("KRX response contained a non-finite value")
    return result


def _frame_hash(frame: pd.DataFrame) -> str:
    """Hash complete constituent-bound frame identity without retaining raw rows."""
    ordered = frame.copy()
    ordered.columns = [str(column) for column in ordered.columns]
    ordered.index = [str(index) for index in ordered.index]
    ordered = ordered.sort_index().sort_index(axis=1)
    canonical = {
        "columns": list(ordered.columns),
        "index": list(ordered.index),
        "data": [
            [str(value) for value in row]
            for row in ordered.itertuples(index=False, name=None)
        ],
    }
    encoded = json.dumps(
        canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class KRXMarketContextProvider:
    """Normalize one completed-session official KRX context payload."""

    def __init__(
        self,
        *,
        client: KRXEquityMarketClient | None = None,
        clock: Callable[[], datetime],
        history_calendar_days: int = 60,
        timeout_seconds: float = 20.0,
    ) -> None:
        if history_calendar_days < 35 or history_calendar_days > 120:
            raise ValueError("KRX history window must be between 35 and 120 calendar days")
        if timeout_seconds <= 0 or timeout_seconds > 60:
            raise ValueError("KRX request timeout must be greater than 0 and at most 60 seconds")
        self._owns_client = client is None
        self._client = client or OfficialKRXEquityMarketClient()
        self._clock = clock
        self._history_calendar_days = history_calendar_days
        self._timeout_seconds = timeout_seconds

    @staticmethod
    async def _drain_request(request: asyncio.Task[pd.DataFrame]) -> bool:
        """Wait for a synchronous worker despite repeated outer cancellation."""
        cancellation_requested = False
        while not request.done():
            try:
                await asyncio.shield(request)
            except asyncio.CancelledError:
                cancellation_requested = True
            except Exception:
                break
        try:
            request.result()
        except BaseException:
            # Retrieving the terminal outcome prevents an orphaned task warning;
            # the caller preserves its timeout or cancellation disposition.
            pass
        return cancellation_requested

    async def _call(
        self,
        operation: str,
        function: Callable[..., pd.DataFrame],
        *args: object,
    ) -> pd.DataFrame:
        request = asyncio.create_task(asyncio.to_thread(function, *args))
        try:
            return await asyncio.wait_for(
                asyncio.shield(request), timeout=self._timeout_seconds
            )
        except TimeoutError:
            if request.done():
                # Preserve a TimeoutError raised by the client itself instead
                # of misclassifying it as the outer request deadline.
                return request.result()
            # Python cannot terminate a running worker thread. Drain it before
            # returning the fail-soft timeout so session/network work never
            # survives provider cleanup or continues behind the next batch.
            cancelled_during_drain = await self._drain_request(request)
            if cancelled_during_drain:
                raise asyncio.CancelledError
            raise TimeoutError(f"KRX {operation} request timed out") from None
        except asyncio.CancelledError:
            await self._drain_request(request)
            raise

    async def fetch_market_context(self, *, as_of: datetime) -> ProviderPayload:
        try:
            return await self._fetch_market_context(as_of=as_of)
        finally:
            if self._owns_client:
                close = getattr(self._client, "close", None)
                if callable(close):
                    close()

    async def _fetch_market_context(self, *, as_of: datetime) -> ProviderPayload:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("KRX context as_of must be timezone-aware")
        session = latest_completed_session(ExchangeMarket.KRX, as_of)
        previous_session = latest_completed_session(
            ExchangeMarket.KRX,
            datetime.combine(session, time.min, tzinfo=as_of.tzinfo),
        )
        start = session - timedelta(days=self._history_calendar_days)
        index_frame = await self._call(
            "index_history", self._client.get_index_history, start, session, "1001"
        )
        markets = ("KOSPI", "KOSDAQ")
        current_equity_frames = {
            market: await self._call(
                f"equity_current_{market}",
                self._client.get_equity_ohlcv,
                session,
                market,
            )
            for market in markets
        }
        previous_equity_frames = {
            market: await self._call(
                f"equity_previous_{market}",
                self._client.get_equity_ohlcv,
                previous_session,
                market,
            )
            for market in markets
        }
        flow_frames: list[pd.DataFrame] = []
        flow_markets: list[str] = []
        missing_flow_markets: list[str] = []
        component_hashes = {
            "index_history": _frame_hash(index_frame),
            **{
                f"equity_current_{market}": _frame_hash(current_equity_frames[market])
                for market in markets
            },
            **{
                f"equity_previous_{market}": _frame_hash(previous_equity_frames[market])
                for market in markets
            },
        }
        for market in markets:
            try:
                frame = await self._call(
                    f"investor_flows_{market}",
                    self._client.get_investor_flows,
                    session,
                    market,
                )
            except Exception:
                missing_flow_markets.append(market)
                continue
            if frame.empty:
                missing_flow_markets.append(market)
                continue
            flow_frames.append(frame)
            flow_markets.append(market)
            component_hashes[f"investor_flows_{market}"] = _frame_hash(frame)
        received_at = self._clock()
        if received_at.tzinfo is None or received_at.utcoffset() is None:
            raise ValueError("KRX provider clock must be timezone-aware")
        equity_breadth = self._equity_breadth(
            [
                (current_equity_frames[market], previous_equity_frames[market])
                for market in markets
            ]
        )
        payload = {
            "session_date": session.isoformat(),
            "index_history": self._index_history(index_frame, session=session),
            "equity_breadth": equity_breadth,
            "component_hashes": dict(sorted(component_hashes.items())),
            "investor_flow_markets": sorted(flow_markets),
            "investor_flow_missing_markets": sorted(missing_flow_markets),
        }
        flows = self._investor_flows(flow_frames)
        if flows:
            payload["investor_flows"] = flows
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        source_hash = hashlib.sha256(encoded).hexdigest()
        return ProviderPayload(
            provider="KRX",
            source_record_id=f"KRX:market-context:{session.isoformat()}:{source_hash}",
            revision=0,
            observed_at=datetime.combine(
                session, time(15, 30), tzinfo=ZoneInfo("Asia/Seoul")
            ),
            available_at=received_at,
            payload=payload,
            quality=(
                DataQualityStatus.PARTIAL
                if equity_breadth["unclassified_equity_count"]
                else DataQualityStatus.FRESH
            ),
            raw_payload_hash=source_hash,
        )

    @staticmethod
    def _index_history(frame: pd.DataFrame, *, session: date) -> list[dict[str, str]]:
        if frame.empty:
            raise ValueError("KRX KOSPI index history was empty")
        close_column = _column(frame, "Close", "종가")
        rows: list[dict[str, str]] = []
        for raw_date, value in frame[close_column].items():
            trade_date = pd.Timestamp(raw_date).date()
            if trade_date <= session:
                rows.append(
                    {"trade_date": trade_date.isoformat(), "close": str(_decimal(value))}
                )
        rows.sort(key=lambda item: item["trade_date"])
        if len(rows) < 20 or rows[-1]["trade_date"] != session.isoformat():
            raise ValueError("KRX index history omitted required completed sessions")
        return rows

    @staticmethod
    def _equity_breadth(
        frames: list[tuple[pd.DataFrame, pd.DataFrame]],
    ) -> dict[str, object]:
        advances = declines = unchanged = excluded = unclassified = 0
        seen_current_identities: set[object] = set()
        for current, previous in frames:
            if current.empty or previous.empty:
                raise ValueError("KRX equity universe response was empty")
            if current.index.has_duplicates or previous.index.has_duplicates:
                raise ValueError("KRX equity universe contained duplicate equity identities")
            current_identities = set(current.index)
            if seen_current_identities.intersection(current_identities):
                raise ValueError("KRX equity universe contained duplicate equity identities")
            seen_current_identities.update(current_identities)
            current_close_column = _column(current, "Close", "종가")
            previous_close_column = _column(previous, "Close", "종가")
            if "SecurityType" not in current.columns:
                raise ValueError("KRX equity universe omitted authoritative security types")
            for ticker, row in current.iterrows():
                if row["SecurityType"] != "EQUITY":
                    excluded += 1
                    continue
                if ticker not in previous.index:
                    unclassified += 1
                    continue
                current_close = _decimal(row[current_close_column])
                previous_close = _decimal(previous.loc[ticker, previous_close_column])
                change = current_close - previous_close
                advances += int(change > 0)
                declines += int(change < 0)
                unchanged += int(change == 0)
        eligible = advances + declines + unchanged
        if eligible == 0:
            raise ValueError("KRX eligible equity universe was empty")
        return {
            "advance_count": advances,
            "decline_count": declines,
            "unchanged_count": unchanged,
            "eligible_equity_count": eligible,
            "excluded_non_equity_count": excluded,
            "unclassified_equity_count": unclassified,
            "universe": "KOSPI_KOSDAQ_EQUITIES",
        }

    @staticmethod
    def _investor_flows(frames: list[pd.DataFrame]) -> dict[str, str]:
        totals = {"Foreign": Decimal(0), "Institution": Decimal(0)}
        for frame in frames:
            if frame.empty:
                continue
            if {"외국인합계", "기관합계"}.issubset(frame.columns):
                latest = frame.iloc[-1]
                totals["Foreign"] += _decimal(latest["외국인합계"])
                totals["Institution"] += _decimal(latest["기관합계"])
                continue
            value_column = _column(frame, "NetPurchaseKRW", "순매수")
            for investor in totals:
                if investor in frame.index:
                    totals[investor] += _decimal(frame.loc[investor, value_column])
                else:
                    korean = "외국인합계" if investor == "Foreign" else "기관합계"
                    if korean in frame.index:
                        totals[investor] += _decimal(frame.loc[korean, value_column])
        if not frames:
            return {}
        return {
            "foreign_net_purchase_krw": str(totals["Foreign"]),
            "institution_net_purchase_krw": str(totals["Institution"]),
        }
