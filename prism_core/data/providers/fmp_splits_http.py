"""Bounded read-only FMP split-calendar coverage for RAW US scenario inputs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time as monotonic_time
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Callable, Mapping, Protocol
from urllib.parse import urlparse
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from prism_core.data.contracts import (
    CorporateAction,
    CorporateActionType,
    DataQualityStatus,
    EvidenceItem,
    ObservationTime,
    SecurityId,
)
from prism_core.data.exchange_calendar import ExchangeMarket, latest_completed_session
from prism_core.data.providers.fmp import FMPInstrument
from prism_core.data.providers.fmp_models import FMPApiKey


FMP_SPLITS_CALENDAR_PATH = "/stable/splits-calendar"
_FMP_ORIGIN = "https://financialmodelingprep.com"
_MARKET_TIMEZONE = ZoneInfo("America/New_York")


class FMPSplitCalendarError(RuntimeError):
    """Sanitized split-calendar transport or schema failure."""


@dataclass(frozen=True)
class FMPJSONResponse:
    status_code: int
    body: bytes = field(repr=False)
    received_at: datetime
    latency_ms: int

    def __post_init__(self) -> None:
        if not 100 <= self.status_code <= 599:
            raise ValueError("status_code must be a valid HTTP status")
        if self.received_at.tzinfo is None or self.received_at.utcoffset() is None:
            raise ValueError("received_at must be timezone-aware")
        if self.latency_ms < 0:
            raise ValueError("latency_ms must be non-negative")


class FMPJSONRequester(Protocol):
    async def request(self, **kwargs: object) -> FMPJSONResponse: ...


class AioHttpFMPJSONRequester:
    """One exact GET-only wire boundary for the FMP split calendar."""

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
        total_timeout_seconds: float,
        max_response_bytes: int,
    ) -> FMPJSONResponse:
        parsed = urlparse(url)
        if (
            method != "GET"
            or parsed.scheme != "https"
            or parsed.hostname != "financialmodelingprep.com"
            or parsed.port not in {None, 443}
            or parsed.path != FMP_SPLITS_CALENDAR_PATH
            or parsed.query
            or parsed.fragment
        ):
            raise FMPSplitCalendarError("FMP split-calendar URL is not allowlisted")
        started = self._monotonic()
        try:
            import aiohttp

            timeout = aiohttp.ClientTimeout(total=total_timeout_seconds)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    url, params=dict(params), allow_redirects=False
                ) as response:
                    declared = response.content_length
                    if declared is not None and declared > max_response_bytes:
                        raise FMPSplitCalendarError(
                            "FMP split-calendar response exceeded size limit"
                        )
                    body = await response.content.read(max_response_bytes + 1)
                    if len(body) > max_response_bytes:
                        raise FMPSplitCalendarError(
                            "FMP split-calendar response exceeded size limit"
                        )
                    elapsed_ms = max(
                        0, round((self._monotonic() - started) * 1000)
                    )
                    return FMPJSONResponse(
                        status_code=response.status,
                        body=body,
                        received_at=self._clock(),
                        latency_ms=elapsed_ms,
                    )
        except asyncio.TimeoutError:
            raise FMPSplitCalendarError("FMP split-calendar request timed out") from None
        except FMPSplitCalendarError:
            raise
        except Exception:
            raise FMPSplitCalendarError("FMP split-calendar request failed") from None


@dataclass(frozen=True)
class FMPSplitCoverage:
    corporate_actions: tuple[CorporateAction, ...]
    coverage_evidence: tuple[EvidenceItem, ...]
    covered_security_ids: tuple[SecurityId, ...]
    latest_completed_session: date
    excluded_future_dates: tuple[str, ...]
    request_evidence: Mapping[str, object]


class FMPSplitCalendarHTTPTransport:
    """Fetch one bounded calendar and retain only configured US symbols."""

    def __init__(
        self,
        *,
        requester: FMPJSONRequester | None = None,
        lookback_days: int = 400,
        timeout_seconds: float = 10.0,
        max_response_bytes: int = 1_000_000,
    ) -> None:
        if not 1 <= lookback_days <= 730:
            raise ValueError("lookback_days must be in [1, 730]")
        if timeout_seconds <= 0 or max_response_bytes < 1:
            raise ValueError("request bounds must be positive")
        self._requester = requester or AioHttpFMPJSONRequester()
        self._lookback_days = lookback_days
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._evidence: tuple[dict[str, object], ...] = ()

    @property
    def evidence(self) -> tuple[dict[str, object], ...]:
        return tuple(dict(item) for item in self._evidence)

    async def fetch(
        self,
        *,
        api_key: FMPApiKey,
        as_of: datetime,
        instruments: tuple[FMPInstrument, ...],
    ) -> FMPSplitCoverage:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        active = tuple(item for item in instruments if item.is_active(as_of))
        if not active or len({item.fmp_symbol for item in active}) != len(active):
            raise ValueError("active FMP instruments must be non-empty and unique")
        completed = latest_completed_session(ExchangeMarket.NYSE, as_of)
        start_date = completed - timedelta(days=self._lookback_days - 1)
        response = await self._requester.request(
            method="GET",
            url=f"{_FMP_ORIGIN}{FMP_SPLITS_CALENDAR_PATH}",
            params={
                "apikey": api_key.get_secret_value(),
                "from": start_date.isoformat(),
                "to": as_of.astimezone(_MARKET_TIMEZONE).date().isoformat(),
            },
            total_timeout_seconds=self._timeout_seconds,
            max_response_bytes=self._max_response_bytes,
        )
        body_hash = _canonical_hash(response.body)
        self._evidence = (
            {
                "provider": "FMP",
                "host": "financialmodelingprep.com",
                "endpoint": FMP_SPLITS_CALENDAR_PATH,
                "status_code": response.status_code,
                "received_at": response.received_at.isoformat(),
                "latency_ms": response.latency_ms,
                "raw_payload_hash": body_hash,
            },
        )
        if not 200 <= response.status_code < 300:
            raise FMPSplitCalendarError("FMP split-calendar request was rejected")
        try:
            decoded = json.loads(response.body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise FMPSplitCalendarError("FMP split calendar returned malformed JSON") from None
        if not isinstance(decoded, list):
            raise FMPSplitCalendarError("FMP split calendar returned an unexpected schema")

        by_symbol = {item.fmp_symbol: item for item in active}
        parsed_actions: list[tuple[FMPInstrument, date, Decimal, Decimal]] = []
        excluded_dates: set[str] = set()
        for raw in decoded:
            if not isinstance(raw, Mapping):
                raise FMPSplitCalendarError("FMP split calendar returned an invalid row")
            symbol = str(raw.get("symbol", ""))
            if symbol not in by_symbol:
                continue
            try:
                effective_date = datetime.fromisoformat(str(raw["date"])).date()
                numerator = Decimal(str(raw["numerator"]))
                denominator = Decimal(str(raw["denominator"]))
            except (KeyError, InvalidOperation, TypeError, ValueError):
                raise FMPSplitCalendarError(
                    "FMP split calendar returned an invalid configured-symbol row"
                ) from None
            if numerator <= 0 or denominator <= 0:
                raise FMPSplitCalendarError("FMP split calendar returned invalid split terms")
            if effective_date > completed:
                excluded_dates.add(effective_date.isoformat())
                continue
            if effective_date < start_date:
                continue
            instrument = by_symbol[symbol]
            parsed_actions.append((instrument, effective_date, numerator, denominator))

        coverage_hash = _canonical_json_hash(
            {
                "symbols": sorted(by_symbol),
                "from": start_date.isoformat(),
                "to": completed.isoformat(),
                "rows": sorted(
                    (
                        {
                            "symbol": instrument.fmp_symbol,
                            "date": effective_date.isoformat(),
                            "numerator": _decimal_identity(numerator),
                            "denominator": _decimal_identity(denominator),
                        }
                        for instrument, effective_date, numerator, denominator
                        in parsed_actions
                    ),
                    key=lambda item: (
                        item["symbol"],
                        item["date"],
                        item["numerator"],
                        item["denominator"],
                    ),
                ),
            }
        )
        actions: list[CorporateAction] = []
        for instrument, effective_date, numerator, denominator in parsed_actions:
            symbol = instrument.fmp_symbol
            action_identity = (
                f"{symbol}:{effective_date.isoformat()}:"
                f"{_decimal_identity(numerator)}:{_decimal_identity(denominator)}"
            )
            effective_at = datetime.combine(
                effective_date, time.min, tzinfo=_MARKET_TIMEZONE
            )
            actions.append(
                CorporateAction(
                    security_id=instrument.security_id,
                    provider="fmp",
                    provider_symbol=symbol,
                    source_record_id=f"fmp:split-calendar:{action_identity}",
                    source_hash=coverage_hash,
                    revision=0,
                    timing=ObservationTime(
                        observed_at=effective_at,
                        available_at=effective_at,
                        ingested_at=response.received_at,
                        as_of_date=as_of,
                    ),
                    quality=DataQualityStatus.FRESH,
                    action_type=CorporateActionType.SPLIT,
                    effective_date=effective_date,
                    ratio=numerator / denominator,
                )
            )

        coverage: list[EvidenceItem] = []
        for instrument in active:
            identity = (
                f"{instrument.fmp_symbol}:{start_date}:{completed}:{coverage_hash}"
            )
            coverage.append(
                EvidenceItem.model_validate(
                    {
                        "security_id": instrument.security_id,
                        "provider": "FMP",
                        "provider_symbol": instrument.fmp_symbol,
                        "source_record_id": f"fmp:split-coverage:{identity}",
                        "source_hash": coverage_hash,
                        "revision": 0,
                        "timing": ObservationTime(
                            observed_at=datetime.combine(
                                completed, time(16, 0), tzinfo=_MARKET_TIMEZONE
                            ),
                            available_at=as_of,
                            ingested_at=response.received_at,
                            as_of_date=as_of,
                        ),
                        "quality": DataQualityStatus.FRESH,
                        "evidence_id": uuid5(
                            NAMESPACE_URL, f"prism-evidence:split-coverage:{identity}"
                        ),
                        "kind": "corporate_action_coverage",
                        "title": (
                            f"FMP split coverage {instrument.fmp_symbol} "
                            f"{start_date.isoformat()}..{completed.isoformat()}"
                        ),
                        "source_url": f"{_FMP_ORIGIN}{FMP_SPLITS_CALENDAR_PATH}",
                        "content_hash": coverage_hash,
                    }
                )
            )
        return FMPSplitCoverage(
            corporate_actions=tuple(
                sorted(actions, key=lambda item: (item.effective_date, item.provider_symbol))
            ),
            coverage_evidence=tuple(coverage),
            covered_security_ids=tuple(item.security_id for item in active),
            latest_completed_session=completed,
            excluded_future_dates=tuple(sorted(excluded_dates)),
            request_evidence=self._evidence[0],
        )


def _canonical_hash(body: bytes) -> str:
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


def _canonical_json_hash(value: object) -> str:
    canonical = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


def _decimal_identity(value: Decimal) -> str:
    return format(value.normalize(), "f")
