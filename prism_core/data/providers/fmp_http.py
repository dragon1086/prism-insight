"""Bounded market-data-only HTTP transport for the FMP provider contract."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import time as monotonic_time
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Callable, Mapping, Protocol
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from prism_core.data.contracts import DataQualityStatus
from prism_core.data.providers.fmp import (
    FMP_RAW_EOD_PATH,
    FMPRateLimitError,
    FMPTimeoutError,
)
from prism_core.data.providers.fmp_models import FMPApiKey, FMPRequest, FMPResponseEnvelope


FMP_PRODUCTION_BASE_URL = "https://financialmodelingprep.com"
_ALLOWED_HOSTS = frozenset({"financialmodelingprep.com"})
_MARKET_TIMEZONE = ZoneInfo("America/New_York")


def _canonical_raw_payload_hash(body: bytes) -> str:
    try:
        decoded = json.loads(body)
        canonical = json.dumps(
            decoded,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        canonical = body
    return hashlib.sha256(canonical).hexdigest()


def load_fmp_api_key_from_env() -> FMPApiKey:
    """Load only the two approved runtime env names without searching secret files."""

    primary = os.environ.get("FMP_API_KEY", "")
    alias = os.environ.get("FINANCIAL_MODELING_PREP_API_KEY", "")
    configured = {value for value in (primary, alias) if value}
    if not configured:
        raise RuntimeError(
            "FMP market-data credential is unavailable; set FMP_API_KEY or "
            "FINANCIAL_MODELING_PREP_API_KEY"
        )
    if len(configured) != 1:
        raise RuntimeError("FMP market-data credential environment is conflicting")
    return FMPApiKey(configured.pop())


class FMPHTTPTransportError(RuntimeError):
    """Sanitized FMP market-data transport or wire-schema failure."""


@dataclass(frozen=True)
class FMPHTTPResponse:
    status_code: int
    body: bytes = field(repr=False)
    received_at: datetime
    latency_ms: int
    retry_after_seconds: float | None = None

    def __post_init__(self) -> None:
        if not 100 <= self.status_code <= 599:
            raise ValueError("status_code must be a valid HTTP status")
        if self.received_at.tzinfo is None or self.received_at.utcoffset() is None:
            raise ValueError("received_at must be timezone-aware")
        if self.latency_ms < 0:
            raise ValueError("latency_ms must be non-negative")
        if self.retry_after_seconds is not None and (
            not math.isfinite(self.retry_after_seconds) or self.retry_after_seconds < 0
        ):
            raise ValueError("retry_after_seconds must be finite and non-negative")


@dataclass(frozen=True)
class FMPRequestEvidence:
    """Sanitized non-payload evidence for one actual FMP HTTP request."""

    endpoint: str
    status_code: int
    received_at: datetime
    latency_ms: int
    request_correlation: str
    raw_payload_hash: str
    provider_version: str = "stable"


class FMPRequester(Protocol):
    async def request(self, **kwargs: object) -> FMPHTTPResponse: ...


class AioHttpFMPRequester:
    """Small bounded aiohttp requester with an exact market-data URL allowlist."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self._clock = clock or (lambda: datetime.now(tz=ZoneInfo("UTC")))
        self._monotonic = monotonic or monotonic_time.monotonic

    async def request(
        self,
        *,
        method: str,
        url: str,
        params: Mapping[str, str],
        connect_timeout_seconds: float,
        read_timeout_seconds: float,
        total_timeout_seconds: float,
        max_response_bytes: int,
    ) -> FMPHTTPResponse:
        parsed = urlparse(url)
        if (
            method != "GET"
            or parsed.scheme != "https"
            or parsed.hostname not in _ALLOWED_HOSTS
            or parsed.port not in {None, 443}
            or parsed.path != FMP_RAW_EOD_PATH
            or parsed.query
            or parsed.fragment
        ):
            raise FMPHTTPTransportError("FMP request URL is not allowlisted")
        started = self._monotonic()
        try:
            import aiohttp

            timeout = aiohttp.ClientTimeout(
                connect=connect_timeout_seconds,
                sock_read=read_timeout_seconds,
                total=total_timeout_seconds,
            )
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, params=dict(params)) as response:
                    declared_size = response.content_length
                    if declared_size is not None and declared_size > max_response_bytes:
                        raise FMPHTTPTransportError(
                            "FMP response exceeded the configured size limit"
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
                            raise FMPHTTPTransportError(
                                "FMP response exceeded the configured size limit"
                            )
                    retry_after: float | None = None
                    retry_after_header = response.headers.get("Retry-After")
                    if retry_after_header is not None:
                        try:
                            parsed_retry_after = float(retry_after_header)
                            if math.isfinite(parsed_retry_after) and parsed_retry_after >= 0:
                                retry_after = parsed_retry_after
                        except ValueError:
                            retry_after = None
                    elapsed_ms = max(0, round((self._monotonic() - started) * 1000))
                    return FMPHTTPResponse(
                        status_code=response.status,
                        body=b"".join(chunks),
                        received_at=self._clock(),
                        latency_ms=elapsed_ms,
                        retry_after_seconds=retry_after,
                    )
        except asyncio.TimeoutError:
            raise FMPTimeoutError("FMP market-data request timed out") from None
        except (FMPHTTPTransportError, FMPTimeoutError):
            raise
        except Exception as exc:
            del exc
            raise FMPHTTPTransportError("FMP market-data request failed") from None


class FMPHTTPTransport:
    """One-symbol, one-wire-call adapter for unadjusted FMP daily prices."""

    def __init__(
        self,
        *,
        requester: FMPRequester | None = None,
        base_url: str = FMP_PRODUCTION_BASE_URL,
        lookback_days: int = 10,
        availability_delay_minutes: int = 15,
        connect_timeout_seconds: float = 3.0,
        read_timeout_seconds: float = 7.0,
        total_timeout_seconds: float = 10.0,
        max_response_bytes: int = 1_000_000,
    ) -> None:
        parsed = urlparse(base_url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in _ALLOWED_HOSTS
            or parsed.port not in {None, 443}
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("base_url must be the allowlisted FMP HTTPS origin")
        if lookback_days < 1 or availability_delay_minutes < 0:
            raise ValueError("lookback and availability delay must be bounded")
        if (
            connect_timeout_seconds <= 0
            or read_timeout_seconds <= 0
            or total_timeout_seconds <= 0
            or total_timeout_seconds < max(connect_timeout_seconds, read_timeout_seconds)
            or max_response_bytes < 1
        ):
            raise ValueError("timeout and response-size limits must be positive and bounded")
        self._requester = requester or AioHttpFMPRequester()
        self._base_url = base_url.rstrip("/")
        self._lookback_days = lookback_days
        self._availability_delay_minutes = availability_delay_minutes
        self._connect_timeout_seconds = connect_timeout_seconds
        self._read_timeout_seconds = read_timeout_seconds
        self._total_timeout_seconds = total_timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._evidence: tuple[FMPRequestEvidence, ...] = ()

    @property
    def evidence(self) -> tuple[FMPRequestEvidence, ...]:
        return self._evidence

    async def execute(
        self,
        request: FMPRequest,
        *,
        api_key: FMPApiKey,
    ) -> FMPResponseEnvelope:
        if request.operation not in {"fetch_price_page", "probe_capability"} or (
            request.path != FMP_RAW_EOD_PATH
        ):
            raise FMPHTTPTransportError("FMP operation or endpoint is not allowlisted")
        if request.operation == "probe_capability":
            requested_symbol = request.params.get("symbol")
            if not isinstance(requested_symbol, str) or not requested_symbol:
                raise FMPHTTPTransportError("FMP capability probe requires one symbol")
        else:
            symbols = request.params.get("symbols")
            if (
                not isinstance(symbols, list)
                or len(symbols) != 1
                or not isinstance(symbols[0], str)
            ):
                raise FMPHTTPTransportError("FMP HTTP transport requires exactly one symbol")
            if request.params.get("page") != 1:
                raise FMPHTTPTransportError("FMP HTTP transport supports one synthetic page")
            requested_symbol = symbols[0]
        try:
            as_of = datetime.fromisoformat(str(request.params["as_of_date"]))
        except (KeyError, TypeError, ValueError):
            raise FMPHTTPTransportError("FMP request has invalid point-in-time metadata") from None
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise FMPHTTPTransportError("FMP request has invalid point-in-time metadata")
        market_date = as_of.astimezone(_MARKET_TIMEZONE).date()
        start_date = market_date - timedelta(days=self._lookback_days - 1)
        response = await self._requester.request(
            method="GET",
            url=f"{self._base_url}{FMP_RAW_EOD_PATH}",
            params={
                "apikey": api_key.get_secret_value(),
                "from": start_date.isoformat(),
                "symbol": requested_symbol,
                "to": market_date.isoformat(),
            },
            connect_timeout_seconds=self._connect_timeout_seconds,
            read_timeout_seconds=self._read_timeout_seconds,
            total_timeout_seconds=self._total_timeout_seconds,
            max_response_bytes=self._max_response_bytes,
        )
        self._evidence = (
            FMPRequestEvidence(
                endpoint=FMP_RAW_EOD_PATH,
                status_code=response.status_code,
                received_at=response.received_at,
                latency_ms=response.latency_ms,
                request_correlation=request.request_hash,
                raw_payload_hash=_canonical_raw_payload_hash(response.body),
            ),
        )
        if response.status_code == 429:
            raise FMPRateLimitError(
                "FMP market-data request was rate limited",
                retry_after_seconds=response.retry_after_seconds,
            )
        try:
            decoded = json.loads(response.body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise FMPHTTPTransportError("FMP returned malformed JSON") from None
        if request.operation == "probe_capability":
            valid_schema = False
            if isinstance(decoded, list) and decoded:
                first = decoded[0]
                if isinstance(first, Mapping):
                    try:
                        datetime.fromisoformat(str(first["date"]))
                        for field_name in (
                            "adjOpen",
                            "adjHigh",
                            "adjLow",
                            "adjClose",
                            "volume",
                        ):
                            Decimal(str(first[field_name]))
                        valid_schema = first.get("symbol") == requested_symbol
                    except (KeyError, InvalidOperation, TypeError, ValueError):
                        valid_schema = False
            payload = (
                {"entitled": True}
                if 200 <= response.status_code < 300 and valid_schema
                else {}
            )
            return FMPResponseEnvelope(
                status_code=response.status_code,
                source_record_id=f"fmp:capability:raw-eod:{requested_symbol}",
                revision=0,
                observed_at=response.received_at,
                available_at=response.received_at,
                payload=payload,
                quality=(
                    DataQualityStatus.FRESH
                    if payload
                    else DataQualityStatus.UNAVAILABLE
                ),
            )
        if not isinstance(decoded, list):
            raise FMPHTTPTransportError("FMP returned an unexpected wire schema")
        normalized: list[dict[str, str]] = []
        for item in decoded:
            if not isinstance(item, Mapping):
                raise FMPHTTPTransportError("FMP returned an invalid price row")
            try:
                symbol = str(item["symbol"])
                date_value = str(item["date"])
                datetime.fromisoformat(date_value)
                values = {
                    "rawOpen": str(Decimal(str(item["adjOpen"]))),
                    "rawHigh": str(Decimal(str(item["adjHigh"]))),
                    "rawLow": str(Decimal(str(item["adjLow"]))),
                    "rawClose": str(Decimal(str(item["adjClose"]))),
                    "rawVolume": str(Decimal(str(item["volume"]))),
                }
            except (KeyError, InvalidOperation, TypeError, ValueError):
                raise FMPHTTPTransportError("FMP returned an invalid price row") from None
            if symbol != requested_symbol:
                raise FMPHTTPTransportError("FMP returned an unexpected provider symbol")
            normalized.append({"symbol": symbol, "date": date_value, **values})
        if normalized:
            latest_date = max(datetime.fromisoformat(row["date"]).date() for row in normalized)
            observed_at = datetime.combine(latest_date, time(16, 0), tzinfo=_MARKET_TIMEZONE)
            available_at = observed_at + timedelta(minutes=self._availability_delay_minutes)
            quality = (
                DataQualityStatus.FRESH
                if latest_date == market_date and available_at <= as_of
                else DataQualityStatus.STALE
            )
        else:
            observed_at = min(response.received_at, as_of)
            available_at = observed_at
            quality = DataQualityStatus.UNAVAILABLE
        payload = {
            "data": normalized,
            "pagination": {
                "page": 1,
                "totalPages": 1,
                "hasMore": False,
                "nextPage": None,
            },
        }
        return FMPResponseEnvelope(
            status_code=response.status_code,
            source_record_id=(
                f"fmp:raw-eod:{requested_symbol}:{start_date.isoformat()}:{market_date.isoformat()}"
            ),
            revision=0,
            observed_at=observed_at,
            available_at=available_at,
            payload=payload,
            quality=quality,
        )
