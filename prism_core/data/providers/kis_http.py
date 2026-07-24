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
import time as monotonic_time
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Awaitable, Callable, Mapping, Protocol
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from prism_core.data.contracts import DataQualityStatus
from prism_core.data.providers.kis import (
    ProviderPayload,
    ProviderRateLimitError,
    ProviderTimeoutError,
)


KST = ZoneInfo("Asia/Seoul")
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
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
        sleeper: Callable[[float], Awaitable[None]] | None = None,
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
        self._credentials = credentials
        self._symbols = symbols
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._min_request_interval_seconds = min_request_interval_seconds
        self._token_expiry_margin_seconds = token_expiry_margin_seconds
        self._clock = clock or (lambda: datetime.now(tz=KST))
        self._requester = requester or AioHttpKISRequester(clock=self._clock)
        self._monotonic = monotonic or monotonic_time.monotonic
        self._sleeper = sleeper or asyncio.sleep
        self._request_lock = asyncio.Lock()
        self._token_lock = asyncio.Lock()
        self._last_request_started: float | None = None
        self._token: _CachedToken | None = None

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
            raise KISMarketDataTransportError(f"KIS {operation} returned an error status")
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
        if market_as_of.time() < time(15, 31):
            raise KISMarketDataTransportError(
                "KIS daily transport requires a completed daily bar"
            )
        token, token_evidence = await self._access_token()
        request_evidence = [token_evidence] if token_evidence is not None else []
        request_date = market_as_of.date()
        request_date_compact = request_date.strftime("%Y%m%d")
        rows: list[dict[str, str]] = []
        raw_hashes: list[str] = []
        received_at_values: list[datetime] = []
        for symbol in self._symbols:
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
                    "FID_INPUT_DATE_1": request_date_compact,
                    "FID_INPUT_DATE_2": request_date_compact,
                    "FID_PERIOD_DIV_CODE": "D",
                    "FID_ORG_ADJ_PRC": "0",
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
                if trade_date <= request_date:
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
            if latest_trade_date == request_date:
                quality = DataQualityStatus.FRESH
            else:
                quality = DataQualityStatus.STALE
        else:
            observed_at = min(retrieved_at, as_of_date)
            available_at = observed_at
            quality = DataQualityStatus.UNAVAILABLE
        raw_payload_hash = (
            raw_hashes[0]
            if len(raw_hashes) == 1
            else hashlib.sha256(
                json.dumps(raw_hashes, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
        )
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
                "transport_evidence": [
                    evidence.as_payload() for evidence in request_evidence
                ],
            },
            quality=quality,
            raw_payload_hash=raw_payload_hash,
        )
