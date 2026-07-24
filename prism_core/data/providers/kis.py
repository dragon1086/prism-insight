"""KIS-primary Korean point-in-time market-data adapter.

The adapter accepts an injected market-data-only transport.  It deliberately has
no dependency on the repository's legacy trading/account packages.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from typing import Awaitable, Callable, Mapping, Protocol, cast
from uuid import NAMESPACE_URL, UUID, uuid5
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime

from prism_core.data.contracts import (
    ContractModel,
    CorporateAction,
    CorporateActionType,
    DataQualityStatus,
    MarketSnapshot,
    ObservationTime,
    PriceBar,
    SecurityId,
    SymbolMapping,
)
from prism_core.data.corporate_actions import CorporateActionEvidence
from prism_core.data.security_master import (
    ListingStatus,
    SecurityAliasEvidence,
    SecurityListingEvidence,
)


KST = ZoneInfo("Asia/Seoul")
SUPPORTED_PROVIDERS = ("KIS", "KRX", "DART", "KIND")


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _require_aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value


@dataclass(frozen=True)
class KISInstrument:
    """Stable internal identity and KIS quotation symbol."""

    security_id: SecurityId
    kis_symbol: str
    provider_symbols: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.kis_symbol:
            raise ValueError("kis_symbol must not be empty")
        if any(
            provider not in SUPPORTED_PROVIDERS[1:] or not symbol
            for provider, symbol in self.provider_symbols.items()
        ):
            raise ValueError("provider_symbols must contain supported supplements")

    def symbol_for(self, provider: str) -> str | None:
        return self.kis_symbol if provider == "KIS" else self.provider_symbols.get(provider)


@dataclass(frozen=True)
class ProviderPayload:
    """One immutable raw response envelope supplied by a fixture/network boundary."""

    provider: str
    source_record_id: str
    revision: int
    observed_at: datetime
    available_at: datetime
    payload: Mapping[str, object]
    quality: DataQualityStatus = DataQualityStatus.FRESH
    _source_hash: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.provider not in SUPPORTED_PROVIDERS:
            raise ValueError(f"unsupported Korean data provider: {self.provider}")
        if not self.source_record_id:
            raise ValueError("source_record_id must not be empty")
        if self.revision < 0:
            raise ValueError("revision must be non-negative")
        _require_aware(self.observed_at, "observed_at")
        _require_aware(self.available_at, "available_at")
        if self.observed_at > self.available_at:
            raise ValueError("observed_at must be at or before available_at")
        encoded = _canonical_json(self.payload)
        object.__setattr__(self, "payload", json.loads(encoded))
        object.__setattr__(self, "_source_hash", hashlib.sha256(encoded).hexdigest())

    @property
    def source_hash(self) -> str:
        """Hash the full raw payload body without processing timestamps."""

        return self._source_hash


class KoreanMarketDataTransport(Protocol):
    """Narrow market-data transport; account/order operations are absent by design."""

    async def fetch(self, provider: str, *, as_of_date: datetime) -> ProviderPayload: ...


class ProviderTimeoutError(TimeoutError):
    """A transport timeout known to be safe to retry within the read-only budget."""


class ProviderRateLimitError(RuntimeError):
    """A provider rate-limit response known to be safe to retry."""


class ProviderEventKind(str, Enum):
    TIMEOUT = "TIMEOUT"
    RATE_LIMIT = "RATE_LIMIT"
    UNAVAILABLE = "UNAVAILABLE"
    MISSING = "MISSING"
    STALE = "STALE"
    PARTIAL = "PARTIAL"
    CONFLICT = "CONFLICT"
    MALFORMED = "MALFORMED"
    UNMATCHED = "UNMATCHED"
    RETRY_EXHAUSTED = "RETRY_EXHAUSTED"


class ProviderQualityEvent(ContractModel):
    provider: str
    kind: ProviderEventKind
    quality: DataQualityStatus
    attempt: int
    operation: str
    detail: str
    occurred_at: AwareDatetime


@dataclass(frozen=True)
class KISFetchResult:
    snapshot: MarketSnapshot
    raw_payloads: tuple[ProviderPayload, ...]
    events: tuple[ProviderQualityEvent, ...]
    alias_evidence: tuple[SecurityAliasEvidence, ...] = ()
    listing_evidence: tuple[SecurityListingEvidence, ...] = ()
    corporate_action_evidence: tuple[CorporateActionEvidence, ...] = ()


class KISMarketDataProvider:
    """Normalize KIS primary data and explicit official supplements."""

    provider_name = "KIS"

    def __init__(
        self,
        *,
        transport: KoreanMarketDataTransport,
        instruments: tuple[KISInstrument, ...],
        clock: Callable[[], datetime],
        supplement_providers: tuple[str, ...] = (),
        max_attempts: int = 3,
        sleeper: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if any(provider not in SUPPORTED_PROVIDERS[1:] for provider in supplement_providers):
            raise ValueError("supplements must be one of KRX, DART, or KIND")
        by_id = {instrument.security_id: instrument for instrument in instruments}
        if len(by_id) != len(instruments):
            raise ValueError("security_id instruments must be unique")
        for provider in SUPPORTED_PROVIDERS:
            symbols = tuple(
                symbol
                for instrument in instruments
                if (symbol := instrument.symbol_for(provider)) is not None
            )
            if len(set(symbols)) != len(symbols):
                raise ValueError("provider symbols must be unique per provider")
        self._transport = transport
        self._instruments = by_id
        self._clock = clock
        self._supplement_providers = supplement_providers
        self._max_attempts = max_attempts
        self._sleeper = sleeper

    async def _fetch_provider(
        self,
        provider: str,
        *,
        as_of_date: datetime,
        occurred_at: datetime,
    ) -> tuple[ProviderPayload | None, tuple[ProviderQualityEvent, ...]]:
        events: list[ProviderQualityEvent] = []
        for attempt in range(1, self._max_attempts + 1):
            try:
                return (
                    await self._transport.fetch(provider, as_of_date=as_of_date),
                    tuple(events),
                )
            except (ProviderTimeoutError, ProviderRateLimitError) as exc:
                kind = (
                    ProviderEventKind.TIMEOUT
                    if isinstance(exc, ProviderTimeoutError)
                    else ProviderEventKind.RATE_LIMIT
                )
                events.append(
                    ProviderQualityEvent(
                        provider=provider,
                        kind=kind,
                        quality=DataQualityStatus.UNAVAILABLE,
                        attempt=attempt,
                        operation="fetch_market_data",
                        detail=exc.__class__.__name__,
                        occurred_at=occurred_at,
                    )
                )
                if attempt == self._max_attempts:
                    events.append(
                        ProviderQualityEvent(
                            provider=provider,
                            kind=ProviderEventKind.RETRY_EXHAUSTED,
                            quality=DataQualityStatus.UNAVAILABLE,
                            attempt=attempt,
                            operation="fetch_market_data",
                            detail=f"retry budget exhausted after {attempt} attempts",
                            occurred_at=occurred_at,
                        )
                    )
                    return None, tuple(events)
                if self._sleeper is not None:
                    await self._sleeper(float(2 ** (attempt - 1)))
        raise AssertionError("unreachable retry state")

    @staticmethod
    def _payload_validation_error(
        envelope: ProviderPayload,
        *,
        as_of_date: datetime,
        ingested_at: datetime,
    ) -> str | None:
        """Return a non-sensitive schema error before normalizing an envelope."""

        if envelope.available_at > as_of_date:
            return "payload was not available at the requested as-of time"
        try:
            placeholder_security_id = SecurityId(value=UUID(int=0))
            timing = ObservationTime(
                observed_at=envelope.observed_at,
                available_at=envelope.available_at,
                ingested_at=ingested_at,
                as_of_date=as_of_date,
            )
            for name in (
                "prices",
                "symbol_events",
                "listing_events",
                "corporate_actions",
            ):
                rows = envelope.payload.get(name, ())
                if not isinstance(rows, (list, tuple)):
                    return f"{name} must be a list"
                if not all(isinstance(row, Mapping) for row in rows):
                    return f"{name} rows must be objects"
            for row in cast(
                list[Mapping[str, object]] | tuple[Mapping[str, object], ...],
                envelope.payload.get("prices", ()),
            ):
                required = {
                    "provider_symbol",
                    "trade_date",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                }
                if required - row.keys():
                    continue
                trading_date = date.fromisoformat(str(row["trade_date"]))
                PriceBar(
                    security_id=placeholder_security_id,
                    provider=envelope.provider,
                    provider_symbol=str(row["provider_symbol"]),
                    source_record_id="validation:price",
                    source_hash=envelope.source_hash,
                    revision=envelope.revision,
                    timing=timing,
                    quality=envelope.quality,
                    bar_start=datetime.combine(trading_date, time.min, tzinfo=KST),
                    bar_end=datetime.combine(trading_date, time(15, 30), tzinfo=KST),
                    interval="1d",
                    currency="KRW",
                    raw_open=Decimal(str(row["open"])),
                    raw_high=Decimal(str(row["high"])),
                    raw_low=Decimal(str(row["low"])),
                    raw_close=Decimal(str(row["close"])),
                    raw_volume=Decimal(str(row["volume"])),
                )
            for row in cast(
                list[Mapping[str, object]] | tuple[Mapping[str, object], ...],
                envelope.payload.get("symbol_events", ()),
            ):
                event_kind = str(row.get("event_kind", ""))
                if event_kind not in {"CORRECTION", "RENAME"}:
                    raise ValueError("unsupported symbol event kind")
                identity_key = (
                    "canonical_alias_id" if event_kind == "CORRECTION" else "rename_event_id"
                )
                identity = str(row.get(identity_key, ""))
                if not identity:
                    raise ValueError("symbol event identity is required")
                valid_from = datetime.combine(
                    date.fromisoformat(str(row["valid_from"])),
                    time.min,
                    tzinfo=KST,
                )
                valid_to = (
                    datetime.combine(
                        date.fromisoformat(str(row["valid_to"])),
                        time.min,
                        tzinfo=KST,
                    )
                    if row.get("valid_to") is not None
                    else None
                )
                SecurityAliasEvidence(
                    mapping=SymbolMapping(
                        security_id=placeholder_security_id,
                        provider=envelope.provider,
                        provider_symbol=str(row.get("provider_symbol", "")),
                        market="KR",
                        valid_from=valid_from,
                        valid_to=valid_to,
                        timing=timing,
                        source_hash=envelope.source_hash,
                    ),
                    source_record_id=f"validation:alias:{identity}",
                    revision=(
                        int(str(row.get("revision", envelope.revision)))
                        if event_kind == "CORRECTION"
                        else 0
                    ),
                    quality=envelope.quality,
                )
            for row in cast(
                list[Mapping[str, object]] | tuple[Mapping[str, object], ...],
                envelope.payload.get("listing_events", ()),
            ):
                event_key = str(row.get("event_key", ""))
                if not event_key:
                    raise ValueError("listing event identity is required")
                SecurityListingEvidence(
                    security_id=placeholder_security_id,
                    provider=envelope.provider,
                    provider_symbol=str(row.get("provider_symbol", "")),
                    market="KR",
                    status=ListingStatus(str(row["status"])),
                    effective_at=datetime.combine(
                        date.fromisoformat(str(row["effective_date"])),
                        time.min,
                        tzinfo=KST,
                    ),
                    source_record_id=f"validation:listing:{event_key}",
                    source_hash=envelope.source_hash,
                    revision=int(str(row.get("revision", envelope.revision))),
                    timing=timing,
                    quality=envelope.quality,
                )
            for row in cast(
                list[Mapping[str, object]] | tuple[Mapping[str, object], ...],
                envelope.payload.get("corporate_actions", ()),
            ):
                event_key = str(row.get("event_key", ""))
                if not event_key:
                    raise ValueError("corporate action identity is required")
                CorporateAction(
                    security_id=placeholder_security_id,
                    provider=envelope.provider,
                    provider_symbol=str(row.get("provider_symbol", "")),
                    source_record_id=f"validation:corporate-action:{event_key}",
                    source_hash=envelope.source_hash,
                    revision=envelope.revision,
                    timing=timing,
                    quality=envelope.quality,
                    action_type=CorporateActionType(str(row["action_type"])),
                    effective_date=date.fromisoformat(str(row["effective_date"])),
                    ratio=(
                        Decimal(str(row["ratio"]))
                        if row.get("ratio") is not None
                        else None
                    ),
                    cash_amount=(
                        Decimal(str(row["cash_amount"]))
                        if row.get("cash_amount") is not None
                        else None
                    ),
                    currency=(
                        str(row["currency"])
                        if row.get("currency") is not None
                        else None
                    ),
                )
        except (KeyError, TypeError, ValueError):
            return "payload contains an invalid field value"
        return None

    async def fetch_snapshot(
        self,
        *,
        security_ids: tuple[SecurityId, ...],
        as_of_date: datetime,
    ) -> MarketSnapshot:
        return (
            await self.fetch_result(
                security_ids=security_ids,
                as_of_date=as_of_date,
            )
        ).snapshot

    async def fetch_result(
        self,
        *,
        security_ids: tuple[SecurityId, ...],
        as_of_date: datetime,
    ) -> KISFetchResult:
        _require_aware(as_of_date, "as_of_date")
        unknown = [security_id for security_id in security_ids if security_id not in self._instruments]
        if unknown:
            raise ValueError("every security_id must have a configured KIS instrument")
        ingested_at = _require_aware(self._clock(), "clock result")
        if ingested_at < as_of_date:
            raise ValueError("ingested_at must be at or after as_of_date")

        primary, retry_events = await self._fetch_provider(
            "KIS",
            as_of_date=as_of_date,
            occurred_at=ingested_at,
        )
        payload_list = [primary] if primary is not None else []
        event_list = list(retry_events)
        for provider in self._supplement_providers:
            supplement, supplement_events = await self._fetch_provider(
                provider,
                as_of_date=as_of_date,
                occurred_at=ingested_at,
            )
            event_list.extend(supplement_events)
            if supplement is not None:
                payload_list.append(supplement)
        raw_payloads = tuple(payload_list)
        normalization_payloads: list[ProviderPayload] = []
        for payload in raw_payloads:
            validation_error = self._payload_validation_error(
                payload,
                as_of_date=as_of_date,
                ingested_at=ingested_at,
            )
            if validation_error is None:
                normalization_payloads.append(payload)
                continue
            event_list.append(
                ProviderQualityEvent(
                    provider=payload.provider,
                    kind=ProviderEventKind.MALFORMED,
                    quality=DataQualityStatus.UNAVAILABLE,
                    attempt=1,
                    operation="validate_market_data",
                    detail=validation_error,
                    occurred_at=ingested_at,
                )
            )
        quality_event_kinds = {
            DataQualityStatus.STALE: ProviderEventKind.STALE,
            DataQualityStatus.PARTIAL: ProviderEventKind.PARTIAL,
            DataQualityStatus.UNAVAILABLE: ProviderEventKind.UNAVAILABLE,
            DataQualityStatus.CONFLICT: ProviderEventKind.CONFLICT,
        }
        for payload in raw_payloads:
            if payload.quality is not DataQualityStatus.FRESH:
                event_list.append(
                    ProviderQualityEvent(
                        provider=payload.provider,
                        kind=quality_event_kinds[payload.quality],
                        quality=payload.quality,
                        attempt=1,
                        operation="normalize_market_data",
                        detail=f"provider labelled payload {payload.quality.value}",
                        occurred_at=ingested_at,
                    )
                )
        bars: list[PriceBar] = []
        requested = set(security_ids)

        def record_unmatched(
            envelope: ProviderPayload,
            *,
            operation: str,
            symbol: str,
        ) -> None:
            if envelope.provider == "KIS":
                return
            event_list.append(
                ProviderQualityEvent(
                    provider=envelope.provider,
                    kind=ProviderEventKind.UNMATCHED,
                    quality=DataQualityStatus.PARTIAL,
                    attempt=1,
                    operation=operation,
                    detail=f"unmatched provider symbol: {symbol or '<empty>'}",
                    occurred_at=ingested_at,
                )
            )

        for envelope in normalization_payloads:
            rows = envelope.payload.get("prices", ())
            if not isinstance(rows, (list, tuple)):
                raise ValueError("prices must be a list")
            for row in rows:
                if not isinstance(row, Mapping):
                    raise ValueError("each price row must be an object")
                symbol = str(row.get("provider_symbol", ""))
                instrument = next(
                    (
                        candidate
                        for candidate in self._instruments.values()
                        if candidate.symbol_for(envelope.provider) == symbol
                        and candidate.security_id in requested
                    ),
                    None,
                )
                if instrument is None:
                    record_unmatched(
                        envelope,
                        operation="normalize_price_bar",
                        symbol=symbol,
                    )
                    continue
                required_price_fields = {
                    "trade_date",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                }
                missing_fields = sorted(required_price_fields - row.keys())
                if missing_fields:
                    event_list.append(
                        ProviderQualityEvent(
                            provider=envelope.provider,
                            kind=ProviderEventKind.PARTIAL,
                            quality=DataQualityStatus.PARTIAL,
                            attempt=1,
                            operation="normalize_price_bar",
                            detail=(
                                f"{symbol} missing fields: "
                                + ",".join(missing_fields)
                            ),
                            occurred_at=ingested_at,
                        )
                    )
                    continue
                trading_date = date.fromisoformat(str(row["trade_date"]))
                bar_start = datetime.combine(trading_date, time.min, tzinfo=KST)
                bar_end = datetime.combine(trading_date, time(15, 30), tzinfo=KST)
                timing = ObservationTime(
                    observed_at=envelope.observed_at,
                    available_at=envelope.available_at,
                    ingested_at=ingested_at,
                    as_of_date=as_of_date,
                )
                bars.append(
                    PriceBar(
                        security_id=instrument.security_id,
                        provider=envelope.provider,
                        provider_symbol=symbol,
                        source_record_id=(
                            f"{envelope.provider}:price:{symbol}:1d:"
                            f"{trading_date.isoformat()}"
                        ),
                        source_hash=envelope.source_hash,
                        revision=envelope.revision,
                        timing=timing,
                        quality=envelope.quality,
                        bar_start=bar_start,
                        bar_end=bar_end,
                        interval="1d",
                        currency="KRW",
                        raw_open=Decimal(str(row["open"])),
                        raw_high=Decimal(str(row["high"])),
                        raw_low=Decimal(str(row["low"])),
                        raw_close=Decimal(str(row["close"])),
                        raw_volume=Decimal(str(row["volume"])),
                    )
                )

        primary_security_ids = {
            bar.security_id.value for bar in bars if bar.provider == "KIS"
        }
        missing_primary = {
            security_id.value for security_id in security_ids
        } - primary_security_ids
        if primary is not None and missing_primary:
            missing_quality = (
                DataQualityStatus.PARTIAL
                if primary_security_ids
                else DataQualityStatus.UNAVAILABLE
            )
            event_list.append(
                ProviderQualityEvent(
                    provider="KIS",
                    kind=ProviderEventKind.MISSING,
                    quality=missing_quality,
                    attempt=1,
                    operation="normalize_price_bars",
                    detail=(
                        "missing requested security_ids: "
                        + ",".join(sorted(str(item) for item in missing_primary))
                    ),
                    occurred_at=ingested_at,
                )
            )

        bars = [
            bar
            for bar in bars
            if bar.provider == "KIS" or bar.security_id.value in primary_security_ids
        ]

        bars.sort(
            key=lambda bar: (
                bar.bar_start,
                str(bar.security_id.value),
                SUPPORTED_PROVIDERS.index(bar.provider),
            )
        )
        signatures: dict[tuple[SecurityId, date], set[tuple[Decimal, ...]]] = {}
        for bar in bars:
            signatures.setdefault((bar.security_id, bar.bar_start.date()), set()).add(
                (
                    bar.raw_open,
                    bar.raw_high,
                    bar.raw_low,
                    bar.raw_close,
                    bar.raw_volume,
                )
            )
        if any(len(values) > 1 for values in signatures.values()):
            event_list.append(
                ProviderQualityEvent(
                    provider="KIS+SUPPLEMENT",
                    kind=ProviderEventKind.CONFLICT,
                    quality=DataQualityStatus.CONFLICT,
                    attempt=1,
                    operation="reconcile_price_bars",
                    detail="providers disagree on raw OHLCV",
                    occurred_at=ingested_at,
                )
            )

        alias_evidence: list[SecurityAliasEvidence] = []
        for envelope in normalization_payloads:
            rows = envelope.payload.get("symbol_events", ())
            if not isinstance(rows, (list, tuple)):
                raise ValueError("symbol_events must be a list")
            for row in rows:
                if not isinstance(row, Mapping):
                    raise ValueError("each symbol event must be an object")
                instrument_symbol = str(row.get("instrument_symbol", ""))
                instrument = next(
                    (
                        candidate
                        for candidate in self._instruments.values()
                        if candidate.kis_symbol == instrument_symbol
                        and candidate.security_id in requested
                    ),
                    None,
                )
                if instrument is None:
                    record_unmatched(
                        envelope,
                        operation="normalize_symbol_event",
                        symbol=instrument_symbol,
                    )
                    continue
                event_kind = str(row.get("event_kind", ""))
                provider_symbol = str(row.get("provider_symbol", ""))
                if event_kind == "CORRECTION":
                    identity = str(row.get("canonical_alias_id", ""))
                    if not identity:
                        raise ValueError("correction requires canonical_alias_id")
                    source_record_id = f"{envelope.provider}:alias:{identity}"
                    revision = int(str(row.get("revision", envelope.revision)))
                elif event_kind == "RENAME":
                    identity = str(row.get("rename_event_id", ""))
                    if not identity:
                        raise ValueError("rename requires rename_event_id")
                    source_record_id = (
                        f"{envelope.provider}:rename:{identity}:{provider_symbol}"
                    )
                    revision = 0
                else:
                    raise ValueError("event_kind must be CORRECTION or RENAME")
                valid_from = datetime.combine(
                    date.fromisoformat(str(row["valid_from"])),
                    time.min,
                    tzinfo=KST,
                )
                valid_to = (
                    datetime.combine(
                        date.fromisoformat(str(row["valid_to"])),
                        time.min,
                        tzinfo=KST,
                    )
                    if row.get("valid_to") is not None
                    else None
                )
                timing = ObservationTime(
                    observed_at=envelope.observed_at,
                    available_at=envelope.available_at,
                    ingested_at=ingested_at,
                    as_of_date=as_of_date,
                )
                alias_evidence.append(
                    SecurityAliasEvidence(
                        mapping=SymbolMapping(
                            security_id=instrument.security_id,
                            provider=envelope.provider,
                            provider_symbol=provider_symbol,
                            market="KR",
                            valid_from=valid_from,
                            valid_to=valid_to,
                            timing=timing,
                            source_hash=envelope.source_hash,
                        ),
                        source_record_id=source_record_id,
                        revision=revision,
                        quality=envelope.quality,
                    )
                )

        listing_evidence: list[SecurityListingEvidence] = []
        for envelope in normalization_payloads:
            rows = envelope.payload.get("listing_events", ())
            if not isinstance(rows, (list, tuple)):
                raise ValueError("listing_events must be a list")
            for row in rows:
                if not isinstance(row, Mapping):
                    raise ValueError("each listing event must be an object")
                instrument_symbol = str(row.get("instrument_symbol", ""))
                instrument = next(
                    (
                        candidate
                        for candidate in self._instruments.values()
                        if candidate.kis_symbol == instrument_symbol
                        and candidate.security_id in requested
                    ),
                    None,
                )
                if instrument is None:
                    record_unmatched(
                        envelope,
                        operation="normalize_listing_event",
                        symbol=instrument_symbol,
                    )
                    continue
                event_key = str(row.get("event_key", ""))
                if not event_key:
                    raise ValueError("listing event_key must not be empty")
                effective_date = date.fromisoformat(str(row["effective_date"]))
                timing = ObservationTime(
                    observed_at=envelope.observed_at,
                    available_at=envelope.available_at,
                    ingested_at=ingested_at,
                    as_of_date=as_of_date,
                )
                listing_evidence.append(
                    SecurityListingEvidence(
                        security_id=instrument.security_id,
                        provider=envelope.provider,
                        provider_symbol=str(row["provider_symbol"]),
                        market="KR",
                        status=ListingStatus(str(row["status"])),
                        effective_at=datetime.combine(
                            effective_date,
                            time.min,
                            tzinfo=KST,
                        ),
                        source_record_id=(
                            f"{envelope.provider}:listing:{event_key}"
                        ),
                        source_hash=envelope.source_hash,
                        revision=int(str(row.get("revision", envelope.revision))),
                        timing=timing,
                        quality=envelope.quality,
                    )
                )

        corporate_action_evidence: list[CorporateActionEvidence] = []
        for envelope in normalization_payloads:
            rows = envelope.payload.get("corporate_actions", ())
            if not isinstance(rows, (list, tuple)):
                raise ValueError("corporate_actions must be a list")
            for row in rows:
                if not isinstance(row, Mapping):
                    raise ValueError("each corporate action must be an object")
                symbol = str(row.get("provider_symbol", ""))
                instrument = next(
                    (
                        candidate
                        for candidate in self._instruments.values()
                        if candidate.symbol_for(envelope.provider) == symbol
                        and candidate.security_id in requested
                    ),
                    None,
                )
                if instrument is None:
                    record_unmatched(
                        envelope,
                        operation="normalize_corporate_action",
                        symbol=symbol,
                    )
                    continue
                event_key = str(row.get("event_key", ""))
                if not event_key:
                    raise ValueError("corporate action event_key must not be empty")
                effective_date = date.fromisoformat(str(row["effective_date"]))
                effective_at = datetime.combine(effective_date, time.min, tzinfo=KST)
                timing = ObservationTime(
                    observed_at=envelope.observed_at,
                    available_at=envelope.available_at,
                    ingested_at=ingested_at,
                    as_of_date=as_of_date,
                )
                action = CorporateAction(
                    security_id=instrument.security_id,
                    provider=envelope.provider,
                    provider_symbol=symbol,
                    source_record_id=(
                        f"{envelope.provider}:corporate_action:{event_key}"
                    ),
                    source_hash=envelope.source_hash,
                    revision=envelope.revision,
                    timing=timing,
                    quality=envelope.quality,
                    action_type=CorporateActionType(str(row["action_type"])),
                    effective_date=effective_date,
                    ratio=(
                        Decimal(str(row["ratio"]))
                        if row.get("ratio") is not None
                        else None
                    ),
                    cash_amount=(
                        Decimal(str(row["cash_amount"]))
                        if row.get("cash_amount") is not None
                        else None
                    ),
                    currency=(
                        str(row["currency"])
                        if row.get("currency") is not None
                        else None
                    ),
                )
                corporate_action_evidence.append(
                    CorporateActionEvidence(
                        action_id=uuid5(
                            NAMESPACE_URL,
                            (
                                "prism:kr:corporate-action:"
                                f"{instrument.security_id.value}:"
                                f"{action.action_type.value}:"
                                f"{effective_date.isoformat()}"
                            ),
                        ),
                        effective_at=effective_at,
                        action=action,
                    )
                )

        content = {
            "market": "KR",
            "as_of_date": as_of_date.isoformat(),
            "raw_payload_hashes": [payload.source_hash for payload in raw_payloads],
            "price_bars": [bar.model_dump(mode="json") for bar in bars],
            "corporate_actions": [
                evidence.model_dump(mode="json")
                for evidence in corporate_action_evidence
            ],
            "symbol_events": [
                evidence.model_dump(mode="json") for evidence in alias_evidence
            ],
            "listing_events": [
                evidence.model_dump(mode="json") for evidence in listing_evidence
            ],
        }
        content_hash = _sha256(content)
        snapshot_quality = DataQualityStatus.UNAVAILABLE
        if primary is not None:
            precedence = {
                DataQualityStatus.FRESH: 0,
                DataQualityStatus.PARTIAL: 1,
                DataQualityStatus.STALE: 2,
                DataQualityStatus.UNAVAILABLE: 3,
                DataQualityStatus.CONFLICT: 4,
            }
            snapshot_quality = max(
                (
                    primary.quality,
                    *(
                        (
                            DataQualityStatus.PARTIAL
                            if event.provider != "KIS"
                            and event.quality
                            in {
                                DataQualityStatus.PARTIAL,
                                DataQualityStatus.STALE,
                                DataQualityStatus.UNAVAILABLE,
                            }
                            else event.quality
                        )
                        for event in event_list
                        if event.kind
                        not in {
                            ProviderEventKind.TIMEOUT,
                            ProviderEventKind.RATE_LIMIT,
                        }
                    ),
                ),
                key=precedence.__getitem__,
            )
        snapshot = MarketSnapshot(
            snapshot_id=uuid5(NAMESPACE_URL, f"prism:kr:market-snapshot:{content_hash}"),
            market="KR",
            as_of_date=as_of_date,
            created_at=ingested_at,
            content_hash=content_hash,
            quality=snapshot_quality,
            symbol_mappings=tuple(
                evidence.mapping
                for evidence in alias_evidence
                if evidence.mapping.valid_from <= as_of_date
                and (
                    evidence.mapping.valid_to is None
                    or evidence.mapping.valid_to > as_of_date
                )
            ),
            price_bars=tuple(bars),
            fundamentals=(),
            corporate_actions=tuple(
                evidence.action for evidence in corporate_action_evidence
            ),
            evidence=(),
        )
        return KISFetchResult(
            snapshot=snapshot,
            raw_payloads=raw_payloads,
            events=tuple(event_list),
            alias_evidence=tuple(alias_evidence),
            listing_evidence=tuple(listing_evidence),
            corporate_action_evidence=tuple(corporate_action_evidence),
        )
