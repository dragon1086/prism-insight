"""Market-data-only KIS HTTP authentication and quotation transport.

This module intentionally knows only the OAuth token and domestic daily-quotation
paths.  It has no account identifier, holdings, balance, or order API surface.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import tempfile
import time as monotonic_time
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Awaitable, Callable, Mapping, Protocol
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from prism_core.data.contracts import DataQualityStatus
from prism_core.data.exchange_calendar import ExchangeMarket, latest_completed_session
from prism_core.data.providers.kis import (
    ProviderPayload,
    ProviderRateLimitError,
    ProviderTimeoutError,
)


KST = ZoneInfo("Asia/Seoul")


def _bounded_date_windows(
    *, start_date: date, end_date: date, max_calendar_days: int = 120
) -> tuple[tuple[date, date], ...]:
    """Return newest-first inclusive windows without overlap or date gaps."""
    if start_date > end_date or max_calendar_days < 1:
        raise ValueError("date window bounds are invalid")
    windows: list[tuple[date, date]] = []
    cursor = end_date
    while cursor >= start_date:
        window_start = max(start_date, cursor - timedelta(days=max_calendar_days - 1))
        windows.append((window_start, cursor))
        cursor = window_start - timedelta(days=1)
    return tuple(windows)
KIS_PRODUCTION_BASE_URL = "https://openapi.koreainvestment.com:9443"
TOKEN_PATH = "/oauth2/tokenP"
DAILY_PRICE_PATH = (
    "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
)
DAILY_PRICE_TR_ID = "FHKST03010100"
_ALLOWED_HOSTS = frozenset({"openapi.koreainvestment.com"})
_SYMBOL_PATTERN = re.compile(r"^[0-9]{6}$")


class KISMarketDataTransportError(RuntimeError):
    """Sanitized market-data transport failure with no provider body attached."""

    def __init__(
        self,
        message: str,
        *,
        operation: str | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.operation = operation
        self.status_code = status_code


@dataclass(frozen=True)
class KISHTTPResponse:
    status_code: int
    body: bytes = field(repr=False)
    received_at: datetime

    def __post_init__(self) -> None:
        if self.received_at.tzinfo is None or self.received_at.utcoffset() is None:
            raise ValueError("received_at must be timezone-aware")


@dataclass(frozen=True)
class KISRequestEvidence:
    """Non-secret metadata for one bounded token or quotation response."""

    endpoint: str
    status_code: int
    received_at: datetime
    raw_payload_hash: str | None

    def as_payload(self) -> dict[str, object]:
        return {
            "endpoint": self.endpoint,
            "status_code": self.status_code,
            "received_at": self.received_at.isoformat(),
            "raw_payload_hash": self.raw_payload_hash,
        }


class KISRequester(Protocol):
    async def request(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        json_body: Mapping[str, str] | None,
        params: Mapping[str, str] | None,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> KISHTTPResponse: ...


class AioHttpKISRequester:
    """Small bounded aiohttp adapter; aiohttp is imported only for live use."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(tz=KST))

    async def request(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        json_body: Mapping[str, str] | None,
        params: Mapping[str, str] | None,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> KISHTTPResponse:
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in _ALLOWED_HOSTS
            or parsed.port != 9443
            or parsed.path not in {TOKEN_PATH, DAILY_PRICE_PATH}
            or parsed.query
            or parsed.fragment
        ):
            raise KISMarketDataTransportError("KIS request URL is not allowlisted")
        try:
            import aiohttp

            timeout = aiohttp.ClientTimeout(total=timeout_seconds)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.request(
                    method,
                    url,
                    headers=dict(headers),
                    json=dict(json_body) if json_body is not None else None,
                    params=dict(params) if params is not None else None,
                ) as response:
                    declared_size = response.content_length
                    if declared_size is not None and declared_size > max_response_bytes:
                        raise KISMarketDataTransportError(
                            "KIS response exceeded the configured size limit"
                        )
                    chunks: list[bytes] = []
                    received_bytes = 0
                    while True:
                        chunk = await response.content.read(
                            max_response_bytes + 1 - received_bytes
                        )
                        if not chunk:
                            break
                        chunks.append(chunk)
                        received_bytes += len(chunk)
                        if received_bytes > max_response_bytes:
                            raise KISMarketDataTransportError(
                                "KIS response exceeded the configured size limit"
                            )
                    body = b"".join(chunks)
                    return KISHTTPResponse(
                        status_code=response.status,
                        body=body,
                        received_at=self._clock(),
                    )
        except asyncio.TimeoutError:
            raise ProviderTimeoutError("KIS market-data request timed out") from None
        except KISMarketDataTransportError:
            raise
        except Exception as exc:
            del exc
            raise KISMarketDataTransportError("KIS market-data request failed") from None


@dataclass(frozen=True, repr=False)
class KISMarketDataCredentials:
    """KIS app credentials with a deliberately non-secret representation."""

    app_key: str
    app_secret: str

    def __post_init__(self) -> None:
        if not self.app_key or not self.app_secret:
            raise ValueError("KIS market-data credentials must not be empty")

    def __repr__(self) -> str:
        return "KISMarketDataCredentials(app_key=<REDACTED>, app_secret=<REDACTED>)"

    @classmethod
    def from_env(cls) -> "KISMarketDataCredentials":
        app_key = os.environ.get("KIS_APP_KEY", "")
        app_secret = os.environ.get("KIS_APP_SECRET", "")
        if not app_key or not app_secret:
            raise RuntimeError(
                "KIS market-data credentials are unavailable; set KIS_APP_KEY and "
                "KIS_APP_SECRET"
            )
        return cls(app_key=app_key, app_secret=app_secret)


@dataclass(frozen=True, repr=False)
class _CachedToken:
    value: str
    expires_at: datetime


class KISTokenCache(Protocol):
    """Local bearer-token reuse contract; implementations must protect the secret."""

    def load(self, *, app_key: str, now: datetime) -> tuple[str, datetime] | None: ...

    def save(self, *, app_key: str, token: str, expires_at: datetime) -> None: ...


class SecureFileKISTokenCache:
    """Mode-0600 local KIS bearer cache bound to a hashed application key."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path).expanduser()

    def __repr__(self) -> str:
        return f"{type(self).__name__}(path={self._path!s})"

    @staticmethod
    def _app_key_hash(app_key: str) -> str:
        return hashlib.sha256(app_key.encode("utf-8")).hexdigest()

    def load(self, *, app_key: str, now: datetime) -> tuple[str, datetime] | None:
        try:
            stat_result = self._path.lstat()
            if self._path.is_symlink() or stat_result.st_mode & 0o077:
                return None
            decoded = json.loads(self._path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
            return None
        if not isinstance(decoded, dict) or decoded.get("app_key_hash") != self._app_key_hash(
            app_key
        ):
            return None
        token = decoded.get("access_token")
        expires_raw = decoded.get("expires_at")
        if not isinstance(token, str) or not token or not isinstance(expires_raw, str):
            return None
        try:
            expires_at = datetime.fromisoformat(expires_raw)
        except ValueError:
            return None
        if (
            expires_at.tzinfo is None
            or expires_at.utcoffset() is None
            or now.tzinfo is None
            or now.utcoffset() is None
            or expires_at <= now
        ):
            return None
        return token, expires_at

    def save(self, *, app_key: str, token: str, expires_at: datetime) -> None:
        if not token or expires_at.tzinfo is None or expires_at.utcoffset() is None:
            raise ValueError("token cache values must be non-empty and timezone-aware")
        self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self._path.parent, 0o700)
        payload = json.dumps(
            {
                "app_key_hash": self._app_key_hash(app_key),
                "access_token": token,
                "expires_at": expires_at.isoformat(),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        descriptor, temporary_name = tempfile.mkstemp(
            dir=self._path.parent,
            prefix=f".{self._path.name}.",
        )
        temporary_path = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self._path)
            os.chmod(self._path, 0o600)
        except BaseException:
            try:
                os.close(descriptor)
            except OSError:
                pass
            temporary_path.unlink(missing_ok=True)
            raise


class KISHTTPTransport:
    """Production KIS OAuth + daily-price transport behind the provider protocol."""

    def __init__(
        self,
        *,
        credentials: KISMarketDataCredentials,
        symbols: tuple[str, ...],
        requester: KISRequester | None = None,
        base_url: str = KIS_PRODUCTION_BASE_URL,
        timeout_seconds: float = 10.0,
        max_response_bytes: int = 1_000_000,
        min_request_interval_seconds: float = 0.05,
        token_expiry_margin_seconds: int = 60,
        lookback_calendar_days: int = 0,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
        sleeper: Callable[[float], Awaitable[None]] | None = None,
        token_cache: KISTokenCache | None = None,
    ) -> None:
        parsed = urlparse(base_url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in _ALLOWED_HOSTS
            or parsed.port != 9443
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("base_url must be the allowlisted KIS HTTPS origin")
        if not symbols or len(symbols) > 20:
            raise ValueError("symbols must contain between 1 and 20 KIS symbols")
        if len(set(symbols)) != len(symbols) or any(
            _SYMBOL_PATTERN.fullmatch(symbol) is None for symbol in symbols
        ):
            raise ValueError("KIS symbols must be unique six-digit strings")
        if timeout_seconds <= 0 or max_response_bytes < 1:
            raise ValueError("timeout and response-size limits must be positive")
        if min_request_interval_seconds < 0 or token_expiry_margin_seconds < 0:
            raise ValueError("rate and token-expiry bounds must be non-negative")
        if type(lookback_calendar_days) is not int or not 0 <= lookback_calendar_days <= 400:
            raise ValueError("lookback_calendar_days must be an integer between 0 and 400")
        self._credentials = credentials
        self._symbols = symbols
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._min_request_interval_seconds = min_request_interval_seconds
        self._token_expiry_margin_seconds = token_expiry_margin_seconds
        self._lookback_calendar_days = lookback_calendar_days
        self._clock = clock or (lambda: datetime.now(tz=KST))
        self._requester = requester or AioHttpKISRequester(clock=self._clock)
        self._monotonic = monotonic or monotonic_time.monotonic
        self._sleeper = sleeper or asyncio.sleep
        self._request_lock = asyncio.Lock()
        self._token_lock = asyncio.Lock()
        self._last_request_started: float | None = None
        self._token: _CachedToken | None = None
        self._token_cache = token_cache
        self._evidence: tuple[dict[str, object], ...] = ()

    @property
    def evidence(self) -> tuple[dict[str, object], ...]:
        return tuple(dict(item) for item in self._evidence)

    async def _request(
        self,
        *,
        method: str,
        path: str,
        headers: Mapping[str, str],
        json_body: Mapping[str, str] | None = None,
        params: Mapping[str, str] | None = None,
    ) -> KISHTTPResponse:
        if path not in {TOKEN_PATH, DAILY_PRICE_PATH}:
            raise KISMarketDataTransportError("KIS endpoint is not allowlisted")
        async with self._request_lock:
            now = self._monotonic()
            if self._last_request_started is not None:
                remaining = (
                    self._min_request_interval_seconds
                    - (now - self._last_request_started)
                )
                if remaining > 0:
                    await self._sleeper(remaining)
                    now = self._monotonic()
            self._last_request_started = now
            return await self._requester.request(
                method=method,
                url=f"{self._base_url}{path}",
                headers=headers,
                json_body=json_body,
                params=params,
                timeout_seconds=self._timeout_seconds,
                max_response_bytes=self._max_response_bytes,
            )

    @staticmethod
    def _decode_object(response: KISHTTPResponse, *, operation: str) -> dict[str, object]:
        if response.status_code == 429:
            raise ProviderRateLimitError(f"KIS {operation} was rate limited")
        if response.status_code < 200 or response.status_code >= 300:
            raise KISMarketDataTransportError(
                f"KIS {operation} returned an error status",
                operation=operation.replace(" ", "_"),
                status_code=response.status_code,
            )
        try:
            decoded = json.loads(response.body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise KISMarketDataTransportError(
                f"KIS {operation} returned malformed JSON"
            ) from None
        if not isinstance(decoded, dict):
            raise KISMarketDataTransportError(
                f"KIS {operation} returned a non-object response"
            )
        return decoded

    async def _access_token(self) -> tuple[str, KISRequestEvidence | None]:
        async with self._token_lock:
            now = self._clock()
            if now.tzinfo is None or now.utcoffset() is None:
                raise ValueError("clock must return a timezone-aware datetime")
            if self._token is not None and now < self._token.expires_at:
                return self._token.value, None
            if self._token_cache is not None:
                cached = self._token_cache.load(
                    app_key=self._credentials.app_key,
                    now=now,
                )
                if cached is not None:
                    value, expires_at = cached
                    if value and expires_at.tzinfo is not None and now < expires_at:
                        self._token = _CachedToken(value=value, expires_at=expires_at)
                        return value, None
            response = await self._request(
                method="POST",
                path=TOKEN_PATH,
                headers={"content-type": "application/json; charset=utf-8"},
                json_body={
                    "grant_type": "client_credentials",
                    "appkey": self._credentials.app_key,
                    "appsecret": self._credentials.app_secret,
                },
            )
            decoded = self._decode_object(response, operation="token request")
            token = decoded.get("access_token")
            expires_in = decoded.get("expires_in")
            if not isinstance(token, str) or not token:
                raise KISMarketDataTransportError("KIS token response omitted access_token")
            try:
                lifetime = int(str(expires_in))
            except (TypeError, ValueError):
                raise KISMarketDataTransportError(
                    "KIS token response contained invalid expiry metadata"
                ) from None
            usable_lifetime = lifetime - self._token_expiry_margin_seconds
            if usable_lifetime <= 0:
                raise KISMarketDataTransportError("KIS token lifetime is too short")
            self._token = _CachedToken(
                value=token,
                expires_at=response.received_at + timedelta(seconds=usable_lifetime),
            )
            if self._token_cache is not None:
                self._token_cache.save(
                    app_key=self._credentials.app_key,
                    token=token,
                    expires_at=self._token.expires_at,
                )
            return token, KISRequestEvidence(
                endpoint=TOKEN_PATH,
                status_code=response.status_code,
                received_at=response.received_at,
                raw_payload_hash=None,
            )

    async def fetch(self, provider: str, *, as_of_date: datetime) -> ProviderPayload:
        if provider != "KIS":
            raise ValueError("KISHTTPTransport supports only the KIS provider")
        if as_of_date.tzinfo is None or as_of_date.utcoffset() is None:
            raise ValueError("as_of_date must be timezone-aware")
        market_as_of = as_of_date.astimezone(KST)
        completed_session = latest_completed_session(ExchangeMarket.KRX, market_as_of)
        token, token_evidence = await self._access_token()
        request_evidence = [token_evidence] if token_evidence is not None else []
        request_date = market_as_of.date()
        start_date = completed_session - timedelta(days=self._lookback_calendar_days)
        date_windows = _bounded_date_windows(
            start_date=start_date,
            end_date=completed_session,
        )
        rows: list[dict[str, str]] = []
        raw_hashes: list[str] = []
        received_at_values: list[datetime] = []
        for symbol in self._symbols:
            for window_start, window_end in date_windows:
                response = await self._request(
                    method="GET",
                    path=DAILY_PRICE_PATH,
                    headers={
                        "content-type": "application/json; charset=utf-8",
                        "authorization": f"Bearer {token}",
                        "appkey": self._credentials.app_key,
                        "appsecret": self._credentials.app_secret,
                        "tr_id": DAILY_PRICE_TR_ID,
                        "custtype": "P",
                    },
                    params={
                        "FID_COND_MRKT_DIV_CODE": "J",
                        "FID_INPUT_ISCD": symbol,
                        "FID_INPUT_DATE_1": window_start.strftime("%Y%m%d"),
                        "FID_INPUT_DATE_2": window_end.strftime("%Y%m%d"),
                        "FID_PERIOD_DIV_CODE": "D",
                        # KIS official samples define 1 as unadjusted/original prices.
                        # PriceBar stores these values in raw_* fields.
                        "FID_ORG_ADJ_PRC": "1",
                    },
                )
                decoded = self._decode_object(response, operation="daily quotation")
                if str(decoded.get("rt_cd", "")) != "0":
                    raise KISMarketDataTransportError("KIS daily quotation was rejected")
                output = decoded.get("output2")
                if not isinstance(output, list):
                    raise KISMarketDataTransportError(
                        "KIS daily quotation omitted the expected row list"
                    )
                selected = [item for item in output if isinstance(item, dict)]
                if len(selected) != len(output):
                    raise KISMarketDataTransportError(
                        "KIS daily quotation contained a malformed row"
                    )
                for item in selected:
                    try:
                        compact_date = str(item["stck_bsop_date"])
                        trade_date = datetime.strptime(compact_date, "%Y%m%d").date()
                        row = {
                            "provider_symbol": symbol,
                            "trade_date": trade_date.isoformat(),
                            "open": str(item["stck_oprc"]),
                            "high": str(item["stck_hgpr"]),
                            "low": str(item["stck_lwpr"]),
                            "close": str(item["stck_clpr"]),
                            "volume": str(item["acml_vol"]),
                        }
                    except (KeyError, TypeError, ValueError):
                        raise KISMarketDataTransportError(
                            "KIS daily quotation contained an invalid row"
                        ) from None
                    in_window = window_start <= trade_date <= window_end
                    if in_window:
                        rows.append(row)
                raw_hashes.append(hashlib.sha256(response.body).hexdigest())
                received_at_values.append(response.received_at)
                request_evidence.append(
                    KISRequestEvidence(
                        endpoint=DAILY_PRICE_PATH,
                        status_code=response.status_code,
                        received_at=response.received_at,
                        raw_payload_hash=raw_hashes[-1],
                    )
                )

        retrieved_at = max(received_at_values)
        if rows:
            latest_trade_date = max(
                datetime.fromisoformat(row["trade_date"]).date() for row in rows
            )
            observed_at = datetime.combine(latest_trade_date, time(15, 30), tzinfo=KST)
            available_at = observed_at + timedelta(minutes=1)
            if latest_trade_date == completed_session:
                quality = DataQualityStatus.FRESH
            else:
                quality = DataQualityStatus.STALE
        else:
            observed_at = min(retrieved_at, as_of_date)
            available_at = observed_at
            quality = DataQualityStatus.UNAVAILABLE
        if available_at > as_of_date:
            raise KISMarketDataTransportError(
                "KIS daily quotation was not available at the requested as-of time"
            )
        raw_payload_hash = (
            raw_hashes[0]
            if len(raw_hashes) == 1
            else hashlib.sha256(
                json.dumps(raw_hashes, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
        )
        self._evidence = tuple(evidence.as_payload() for evidence in request_evidence)
        return ProviderPayload(
            provider="KIS",
            source_record_id=(
                f"KIS:daily:{','.join(self._symbols)}:{request_date.isoformat()}"
            ),
            revision=0,
            observed_at=observed_at,
            available_at=available_at,
            payload={
                "prices": rows,
                "price_basis": "RAW_UNADJUSTED",
                "transport_evidence": list(self._evidence),
            },
            quality=quality,
            raw_payload_hash=raw_payload_hash,
        )
