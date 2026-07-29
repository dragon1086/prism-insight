"""Strict immutable contracts for point-in-time market data."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Annotated, cast
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    computed_field,
    model_validator,
)


NonEmptyStr = Annotated[str, Field(min_length=1)]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Currency = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
FiniteDecimal = Annotated[Decimal, Field(allow_inf_nan=False)]
PositiveDecimal = Annotated[Decimal, Field(gt=0, allow_inf_nan=False)]
NonNegativeDecimal = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]


class ContractModel(BaseModel):
    """Base configuration shared by all data-boundary models."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class SecurityId(ContractModel):
    """Stable internal identity, intentionally independent of provider symbols."""

    value: UUID


class ObservationTime(ContractModel):
    """Lifecycle and evaluation times for one point-in-time observation.

    ``as_of_date`` is an evaluation instant despite its historical name. A record
    may have been ingested after that evaluation instant when replaying a backfill,
    but it must have been available by the evaluation instant.
    """

    observed_at: AwareDatetime
    available_at: AwareDatetime
    ingested_at: AwareDatetime
    as_of_date: AwareDatetime

    @model_validator(mode="after")
    def validate_ordering(self) -> ObservationTime:
        if self.observed_at > self.available_at:
            raise ValueError("observed_at must be at or before available_at")
        if self.available_at > self.ingested_at:
            raise ValueError("available_at must be at or before ingested_at")
        if self.available_at > self.as_of_date:
            raise ValueError("available_at must be at or before as_of_date")
        return self


class DataQualityStatus(str, Enum):
    """Observed data state; policy dispositions belong to the quality gate."""

    FRESH = "FRESH"
    STALE = "STALE"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"
    CONFLICT = "CONFLICT"


class CorporateActionType(str, Enum):
    SPLIT = "SPLIT"
    REVERSE_SPLIT = "REVERSE_SPLIT"
    CASH_DIVIDEND = "CASH_DIVIDEND"
    STOCK_DIVIDEND = "STOCK_DIVIDEND"
    SPINOFF = "SPINOFF"
    MERGER = "MERGER"
    TICKER_CHANGE = "TICKER_CHANGE"
    DELISTING = "DELISTING"
    RIGHTS = "RIGHTS"


class SymbolMapping(ContractModel):
    """A provider symbol's validity interval for one internal security."""

    security_id: SecurityId
    provider: NonEmptyStr
    provider_symbol: NonEmptyStr
    market: NonEmptyStr
    valid_from: AwareDatetime
    valid_to: AwareDatetime | None = None
    timing: ObservationTime
    source_hash: Sha256

    @model_validator(mode="after")
    def validate_validity_interval(self) -> SymbolMapping:
        if self.valid_to is not None and self.valid_to <= self.valid_from:
            raise ValueError("valid_to must be after valid_from")
        return self


class ObservationRecord(ContractModel):
    """Shared provenance and revision metadata for append-only observations."""

    security_id: SecurityId
    provider: NonEmptyStr
    provider_symbol: NonEmptyStr
    source_record_id: NonEmptyStr
    source_hash: Sha256
    revision: Annotated[int, Field(ge=0)]
    timing: ObservationTime
    quality: DataQualityStatus


class PriceBar(ObservationRecord):
    """A bar with canonical raw OHLCV and optional explicitly vintaged adjustments."""

    bar_start: AwareDatetime
    bar_end: AwareDatetime
    interval: NonEmptyStr
    currency: Currency
    raw_open: PositiveDecimal
    raw_high: PositiveDecimal
    raw_low: PositiveDecimal
    raw_close: PositiveDecimal
    raw_volume: NonNegativeDecimal
    adjusted_open: PositiveDecimal | None = None
    adjusted_high: PositiveDecimal | None = None
    adjusted_low: PositiveDecimal | None = None
    adjusted_close: PositiveDecimal | None = None
    adjusted_volume: NonNegativeDecimal | None = None
    adjustment_as_of: AwareDatetime | None = None

    @computed_field
    @property
    def natural_identity(
        self,
    ) -> tuple[SecurityId, str, str, str, AwareDatetime, AwareDatetime]:
        return (
            self.security_id,
            self.provider,
            self.provider_symbol,
            self.interval,
            self.bar_start,
            self.bar_end,
        )

    @model_validator(mode="after")
    def validate_bar(self) -> PriceBar:
        if self.bar_end <= self.bar_start:
            raise ValueError("bar_end must be after bar_start")
        if self.bar_end > self.timing.observed_at:
            raise ValueError("bar_end must be at or before observed_at")
        self._validate_ohlc(
            self.raw_open,
            self.raw_high,
            self.raw_low,
            self.raw_close,
            "raw",
        )
        adjusted = (
            self.adjusted_open,
            self.adjusted_high,
            self.adjusted_low,
            self.adjusted_close,
            self.adjusted_volume,
        )
        if any(value is not None for value in adjusted) and not all(
            value is not None for value in adjusted
        ):
            raise ValueError("adjusted OHLCV must be supplied together")
        if all(value is not None for value in adjusted):
            if self.adjustment_as_of is None:
                raise ValueError("adjustment_as_of is required for adjusted OHLCV")
            if self.adjustment_as_of > self.timing.as_of_date:
                raise ValueError("adjustment_as_of cannot exceed evaluation as_of_date")
            self._validate_ohlc(
                cast(Decimal, self.adjusted_open),
                cast(Decimal, self.adjusted_high),
                cast(Decimal, self.adjusted_low),
                cast(Decimal, self.adjusted_close),
                "adjusted",
            )
        elif self.adjustment_as_of is not None:
            raise ValueError("adjustment_as_of requires adjusted OHLCV")
        return self

    @staticmethod
    def _validate_ohlc(
        open_price: Decimal,
        high: Decimal,
        low: Decimal,
        close: Decimal,
        label: str,
    ) -> None:
        if high < max(open_price, close) or low > min(open_price, close) or high < low:
            raise ValueError(f"{label} OHLC values are inconsistent")


class FundamentalObservation(ObservationRecord):
    """A revisable metric whose natural identity is independent of its vintage."""

    metric: NonEmptyStr
    period_start: date
    period_end: date
    value: FiniteDecimal
    unit: NonEmptyStr

    @model_validator(mode="after")
    def validate_period(self) -> FundamentalObservation:
        if self.period_end < self.period_start:
            raise ValueError("period_end must be on or after period_start")
        return self

    @computed_field
    @property
    def natural_identity(
        self,
    ) -> tuple[SecurityId, str, str, str, date, date]:
        return (
            self.security_id,
            self.provider,
            self.provider_symbol,
            self.metric,
            self.period_start,
            self.period_end,
        )


class CorporateAction(ObservationRecord):
    """A raw corporate event; adjustment factor computation is deliberately absent."""

    action_type: CorporateActionType
    effective_date: date
    ratio: PositiveDecimal | None = None
    cash_amount: PositiveDecimal | None = None
    currency: Currency | None = None

    @model_validator(mode="after")
    def validate_action_values(self) -> CorporateAction:
        ratio_actions = {
            CorporateActionType.SPLIT,
            CorporateActionType.REVERSE_SPLIT,
            CorporateActionType.STOCK_DIVIDEND,
            CorporateActionType.RIGHTS,
        }
        if self.action_type in ratio_actions and self.ratio is None:
            raise ValueError("a positive ratio is required for this corporate action")
        if self.action_type is CorporateActionType.CASH_DIVIDEND and (
            self.cash_amount is None or self.currency is None
        ):
            raise ValueError("cash_amount and currency are required for a cash dividend")
        if self.cash_amount is None and self.currency is not None:
            raise ValueError("currency requires cash_amount")
        return self


class EvidenceItem(ObservationRecord):
    """Content-addressed untrusted evidence; it carries no action authority."""

    evidence_id: UUID
    kind: NonEmptyStr
    title: NonEmptyStr
    source_url: HttpUrl
    content_hash: Sha256


class MarketSnapshot(ContractModel):
    """Immutable provider output with a producer-supplied content identity."""

    snapshot_id: UUID
    market: NonEmptyStr
    as_of_date: AwareDatetime
    created_at: AwareDatetime
    content_hash: Sha256
    quality: DataQualityStatus
    symbol_mappings: tuple[SymbolMapping, ...]
    price_bars: tuple[PriceBar, ...]
    fundamentals: tuple[FundamentalObservation, ...]
    corporate_actions: tuple[CorporateAction, ...]
    evidence: tuple[EvidenceItem, ...]

    @model_validator(mode="after")
    def validate_snapshot_boundary(self) -> MarketSnapshot:
        if self.created_at < self.as_of_date:
            raise ValueError("created_at must be at or after snapshot as_of_date")
        records: tuple[ObservationRecord, ...] = (
            *self.price_bars,
            *self.fundamentals,
            *self.corporate_actions,
            *self.evidence,
        )
        if any(record.timing.as_of_date != self.as_of_date for record in records):
            raise ValueError("every record must match the snapshot as_of_date")
        if any(mapping.timing.as_of_date != self.as_of_date for mapping in self.symbol_mappings):
            raise ValueError("every symbol mapping must match the snapshot as_of_date")
        if any(
            mapping.valid_from > self.as_of_date
            or (mapping.valid_to is not None and mapping.valid_to <= self.as_of_date)
            for mapping in self.symbol_mappings
        ):
            raise ValueError("symbol mapping validity must contain snapshot as_of_date")
        return self
