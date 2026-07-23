from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from prism_core.data.contracts import (
    CorporateAction,
    CorporateActionType,
    DataQualityStatus,
    EvidenceItem,
    FundamentalObservation,
    MarketSnapshot,
    ObservationTime,
    PriceBar,
    SecurityId,
    SymbolMapping,
)
from prism_core.data.provider import MarketDataProvider


UTC = timezone.utc


def utc_datetime(day: int, hour: int = 0) -> datetime:
    return datetime(2026, 1, day, hour, tzinfo=UTC)


def test_security_id_is_strict_immutable_internal_identity() -> None:
    security_id = SecurityId(value=UUID("6b41a9de-55f2-4cec-98c5-6ff1763368b1"))

    with pytest.raises(ValidationError):
        SecurityId(value=str(security_id.value))
    with pytest.raises(ValidationError):
        security_id.value = UUID("fa4a25da-4313-45d7-b88c-d2ea789c027e")
    with pytest.raises(ValidationError):
        SecurityId(
            value=UUID("6b41a9de-55f2-4cec-98c5-6ff1763368b1"),
            provider="kis",
        )


def test_observation_time_preserves_pit_ordering_and_evaluation_boundary() -> None:
    timing = ObservationTime(
        observed_at=utc_datetime(1),
        available_at=utc_datetime(2),
        ingested_at=utc_datetime(4),
        as_of_date=utc_datetime(3),
    )

    assert timing.available_at <= timing.as_of_date < timing.ingested_at


@pytest.mark.parametrize("field", ["observed_at", "available_at", "ingested_at", "as_of_date"])
def test_observation_time_rejects_timezone_naive_values(field: str) -> None:
    values = {
        "observed_at": utc_datetime(1),
        "available_at": utc_datetime(2),
        "ingested_at": utc_datetime(3),
        "as_of_date": utc_datetime(3),
    }
    values[field] = datetime(2026, 1, 1)

    with pytest.raises(ValidationError, match="timezone"):
        ObservationTime(**values)


@pytest.mark.parametrize(
    "values",
    [
        {
            "observed_at": utc_datetime(2),
            "available_at": utc_datetime(1),
            "ingested_at": utc_datetime(3),
            "as_of_date": utc_datetime(3),
        },
        {
            "observed_at": utc_datetime(1),
            "available_at": utc_datetime(3),
            "ingested_at": utc_datetime(2),
            "as_of_date": utc_datetime(3),
        },
        {
            "observed_at": utc_datetime(1),
            "available_at": utc_datetime(3),
            "ingested_at": utc_datetime(4),
            "as_of_date": utc_datetime(2),
        },
    ],
)
def test_observation_time_rejects_inconsistent_ordering(values: dict[str, datetime]) -> None:
    with pytest.raises(ValidationError, match="observed_at|available_at"):
        ObservationTime(**values)


SECURITY_ID = SecurityId(value=UUID("6b41a9de-55f2-4cec-98c5-6ff1763368b1"))
TIMING = ObservationTime(
    observed_at=utc_datetime(1, 23),
    available_at=utc_datetime(2),
    ingested_at=utc_datetime(3),
    as_of_date=utc_datetime(2),
)
SOURCE_HASH = "a" * 64


def record_provenance() -> dict[str, object]:
    return {
        "security_id": SECURITY_ID,
        "provider": "fmp",
        "provider_symbol": "AAPL",
        "source_record_id": "prices:AAPL:1d:2026-01-01",
        "source_hash": SOURCE_HASH,
        "revision": 0,
        "timing": TIMING,
        "quality": DataQualityStatus.FRESH,
    }


def make_price_bar(**overrides: object) -> PriceBar:
    values = {
        **record_provenance(),
        "bar_start": utc_datetime(1),
        "bar_end": utc_datetime(1, 23),
        "interval": "1d",
        "currency": "USD",
        "raw_open": Decimal("100"),
        "raw_high": Decimal("110"),
        "raw_low": Decimal("95"),
        "raw_close": Decimal("105"),
        "raw_volume": Decimal("1000000"),
    }
    values.update(overrides)
    return PriceBar(**values)


def test_symbol_mapping_is_time_bounded_and_keeps_provider_provenance() -> None:
    mapping = SymbolMapping(
        security_id=SECURITY_ID,
        provider="fmp",
        provider_symbol="AAPL",
        market="US",
        valid_from=utc_datetime(1),
        valid_to=utc_datetime(2),
        timing=TIMING,
        source_hash=SOURCE_HASH,
    )

    assert mapping.security_id == SECURITY_ID
    with pytest.raises(ValidationError, match="valid_to"):
        SymbolMapping(
            security_id=SECURITY_ID,
            provider="fmp",
            provider_symbol="AAPL",
            market="US",
            valid_from=utc_datetime(2),
            valid_to=utc_datetime(1),
            timing=TIMING,
            source_hash=SOURCE_HASH,
        )


def test_snapshot_rejects_symbol_mapping_outside_evaluation_interval() -> None:
    future_mapping = SymbolMapping(
        security_id=SECURITY_ID,
        provider="fmp",
        provider_symbol="AAPL2",
        market="US",
        valid_from=utc_datetime(3),
        valid_to=None,
        timing=TIMING,
        source_hash=SOURCE_HASH,
    )

    with pytest.raises(ValidationError, match="symbol mapping validity"):
        MarketSnapshot(
            snapshot_id=UUID("a2df7117-a5cf-4812-a919-d761ee06c5e9"),
            market="US",
            as_of_date=TIMING.as_of_date,
            created_at=utc_datetime(3),
            content_hash="c" * 64,
            quality=DataQualityStatus.FRESH,
            symbol_mappings=(future_mapping,),
            price_bars=(),
            fundamentals=(),
            corporate_actions=(),
            evidence=(),
        )


def test_price_bar_keeps_raw_and_adjusted_ohlcv_explicit() -> None:
    bar = make_price_bar(
        adjusted_open=Decimal("50"),
        adjusted_high=Decimal("55"),
        adjusted_low=Decimal("47.5"),
        adjusted_close=Decimal("52.5"),
        adjusted_volume=Decimal("2000000"),
        adjustment_as_of=utc_datetime(2),
    )

    assert bar.raw_close == Decimal("105")
    assert bar.adjusted_close == Decimal("52.5")


def test_price_bar_rejects_content_period_not_yet_observed() -> None:
    with pytest.raises(ValidationError, match="bar_end"):
        make_price_bar(bar_end=utc_datetime(2, 1))


def test_price_bar_exposes_revision_stable_natural_identity() -> None:
    first = make_price_bar(revision=0)
    revision = make_price_bar(revision=1, source_hash="d" * 64)

    assert first.natural_identity == revision.natural_identity
    assert first.revision != revision.revision


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("raw_high", Decimal("99"), "raw OHLC"),
        ("raw_low", Decimal("106"), "raw OHLC"),
        ("raw_close", Decimal("NaN"), "finite"),
        ("raw_close", Decimal("Infinity"), "finite"),
        ("raw_volume", Decimal("-1"), "greater than or equal"),
        ("raw_open", 100.0, "instance of Decimal"),
    ],
)
def test_price_bar_rejects_malformed_or_non_finite_values(
    field: str, value: object, message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        make_price_bar(**{field: value})


def test_price_bar_rejects_partial_or_unvintaged_adjusted_values() -> None:
    with pytest.raises(ValidationError, match="adjusted OHLCV"):
        make_price_bar(adjusted_close=Decimal("52.5"))
    with pytest.raises(ValidationError, match="adjustment_as_of"):
        make_price_bar(
            adjusted_open=Decimal("50"),
            adjusted_high=Decimal("55"),
            adjusted_low=Decimal("47.5"),
            adjusted_close=Decimal("52.5"),
            adjusted_volume=Decimal("2000000"),
        )
    with pytest.raises(ValidationError, match="requires adjusted OHLCV"):
        make_price_bar(adjustment_as_of=utc_datetime(2))
    with pytest.raises(ValidationError, match="adjusted OHLC"):
        make_price_bar(
            adjusted_open=Decimal("50"),
            adjusted_high=Decimal("49"),
            adjusted_low=Decimal("47.5"),
            adjusted_close=Decimal("52.5"),
            adjusted_volume=Decimal("2000000"),
            adjustment_as_of=utc_datetime(2),
        )
    with pytest.raises(ValidationError, match="evaluation as_of_date"):
        make_price_bar(
            adjusted_open=Decimal("50"),
            adjusted_high=Decimal("55"),
            adjusted_low=Decimal("47.5"),
            adjusted_close=Decimal("52.5"),
            adjusted_volume=Decimal("2000000"),
            adjustment_as_of=utc_datetime(3),
        )


def test_revisable_fundamental_has_stable_natural_identity_and_revision() -> None:
    observation = FundamentalObservation(
        **record_provenance(),
        metric="revenue",
        period_start=date(2025, 10, 1),
        period_end=date(2025, 12, 31),
        value=Decimal("123456.78"),
        unit="USD",
    )

    assert observation.natural_identity == (
        SECURITY_ID,
        "fmp",
        "AAPL",
        "revenue",
        date(2025, 10, 1),
        date(2025, 12, 31),
    )
    assert observation.revision == 0


def test_fundamental_rejects_non_finite_value_and_reversed_period() -> None:
    values = {
        **record_provenance(),
        "metric": "eps",
        "period_start": date(2026, 1, 2),
        "period_end": date(2026, 1, 1),
        "value": Decimal("NaN"),
        "unit": "USD_PER_SHARE",
    }
    with pytest.raises(ValidationError):
        FundamentalObservation(**values)


def test_corporate_action_separates_knowledge_time_from_effective_date() -> None:
    action = CorporateAction(
        **record_provenance(),
        action_type=CorporateActionType.SPLIT,
        effective_date=date(2026, 1, 10),
        ratio=Decimal("2"),
    )

    assert action.timing.available_at < datetime.combine(
        action.effective_date, datetime.min.time(), tzinfo=UTC
    )
    with pytest.raises(ValidationError, match="positive ratio"):
        CorporateAction(
            **record_provenance(),
            action_type=CorporateActionType.SPLIT,
            effective_date=date(2026, 1, 10),
        )


def test_cash_dividend_requires_amount_and_currency() -> None:
    with pytest.raises(ValidationError, match="cash_amount and currency"):
        CorporateAction(
            **record_provenance(),
            action_type=CorporateActionType.CASH_DIVIDEND,
            effective_date=date(2026, 1, 10),
            cash_amount=Decimal("0.25"),
        )


def test_evidence_is_content_addressed_untrusted_data() -> None:
    evidence = EvidenceItem(
        **record_provenance(),
        evidence_id=UUID("333d29ab-b697-4af4-bbc3-1ac9b0d70457"),
        kind="filing",
        title="Quarterly filing",
        source_url="https://www.sec.gov/example",
        content_hash="b" * 64,
    )

    assert evidence.content_hash == "b" * 64
    with pytest.raises(ValidationError):
        EvidenceItem(
            **record_provenance(),
            evidence_id=UUID("333d29ab-b697-4af4-bbc3-1ac9b0d70457"),
            kind="filing",
            title="Quarterly filing",
            source_url="not a URL",
            content_hash="b" * 64,
        )


def test_market_snapshot_has_immutable_content_identity_and_consistent_as_of() -> None:
    bar = make_price_bar()
    snapshot = MarketSnapshot(
        snapshot_id=UUID("a2df7117-a5cf-4812-a919-d761ee06c5e9"),
        market="US",
        as_of_date=TIMING.as_of_date,
        created_at=utc_datetime(3),
        content_hash="c" * 64,
        quality=DataQualityStatus.FRESH,
        symbol_mappings=(),
        price_bars=(bar,),
        fundamentals=(),
        corporate_actions=(),
        evidence=(),
    )

    assert snapshot.price_bars == (bar,)
    with pytest.raises(ValidationError):
        snapshot.price_bars = ()

    later_bar = make_price_bar(
        timing=ObservationTime(
            observed_at=utc_datetime(2),
            available_at=utc_datetime(3),
            ingested_at=utc_datetime(4),
            as_of_date=utc_datetime(3),
        )
    )
    with pytest.raises(ValidationError, match="snapshot as_of_date"):
        MarketSnapshot(
            snapshot_id=UUID("a2df7117-a5cf-4812-a919-d761ee06c5e9"),
            market="US",
            as_of_date=TIMING.as_of_date,
            created_at=utc_datetime(3),
            content_hash="c" * 64,
            quality=DataQualityStatus.FRESH,
            symbol_mappings=(),
            price_bars=(later_bar,),
            fundamentals=(),
            corporate_actions=(),
            evidence=(),
        )


def test_quality_status_is_observation_state_not_policy_disposition() -> None:
    assert {status.value for status in DataQualityStatus} == {
        "FRESH",
        "STALE",
        "PARTIAL",
        "UNAVAILABLE",
        "CONFLICT",
    }


def test_provider_protocol_is_structural_and_returns_market_snapshots() -> None:
    class FixtureProvider:
        provider_name = "fixture"

        async def fetch_snapshot(
            self,
            *,
            security_ids: tuple[SecurityId, ...],
            as_of_date: datetime,
        ) -> MarketSnapshot:
            raise NotImplementedError

    assert isinstance(FixtureProvider(), MarketDataProvider)
