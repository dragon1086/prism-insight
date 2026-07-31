from __future__ import annotations

from datetime import date, timedelta
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext

from prism_core.data.contracts import DataQualityStatus
from prism_core.market import (
    KRComputedMetrics,
    KRMarketRegime,
    compute_kr_market_metrics,
)


def _sessions(*, count: int, end: date) -> list[date]:
    result: list[date] = []
    cursor = end
    while len(result) < count:
        if cursor.weekday() < 5:
            result.append(cursor)
        cursor -= timedelta(days=1)
    return list(reversed(result))


def test_computes_session_based_index_breadth_flows_and_strong_bull_regime() -> None:
    session = date(2026, 7, 29)
    history = tuple(
        (trade_date, Decimal(100 + index))
        for index, trade_date in enumerate(_sessions(count=20, end=session))
    )

    result = compute_kr_market_metrics(
        session_date=session,
        index_history=history,
        breadth_counts={
            "advance_count": 1200,
            "decline_count": 900,
            "unchanged_count": 100,
            "eligible_equity_count": 2200,
            "excluded_non_equity_count": 175,
            "unclassified_equity_count": 0,
        },
        investor_flows={
            "foreign_net_purchase_krw": Decimal("125000000000"),
            "institution_net_purchase_krw": Decimal("-32000000000"),
        },
        source_quality=DataQualityStatus.FRESH,
        evidence_id="KRX:fixture",
    )

    assert {metric.name: metric.value for metric in result.index_state} == {
        "kospi_close": Decimal("119"),
        "kospi_ma20": Decimal("109.5"),
        "kospi_return_10d_pct": Decimal("9.174311926605504587155963300"),
    }
    assert {metric.name: metric.value for metric in result.breadth} == {
        "advance_count": Decimal("1200"),
        "decline_count": Decimal("900"),
        "eligible_equity_count": Decimal("2200"),
        "excluded_non_equity_count": Decimal("175"),
        "unclassified_equity_count": Decimal("0"),
        "unchanged_count": Decimal("100"),
    }
    assert {metric.name: metric.value for metric in result.investor_flows} == {
        "foreign_net_purchase_krw": Decimal("125000000000"),
        "institution_net_purchase_krw": Decimal("-32000000000"),
    }
    assert result.regime.regime is KRMarketRegime.STRONG_BULL
    assert result.missing_fields == ()


def _compute_with_closes(
    closes: list[str],
    *,
    quality: DataQualityStatus = DataQualityStatus.FRESH,
    unclassified: int = 0,
) -> KRComputedMetrics:
    session = date(2026, 7, 29)
    sessions = _sessions(count=len(closes), end=session)
    return compute_kr_market_metrics(
        session_date=session,
        index_history=tuple(
            (trade_date, Decimal(close))
            for trade_date, close in zip(sessions, closes, strict=True)
        ),
        breadth_counts={
            "advance_count": 2,
            "decline_count": 1,
            "unchanged_count": 1,
            "eligible_equity_count": 4,
            "excluded_non_equity_count": 8,
            "unclassified_equity_count": unclassified,
        },
        investor_flows=None,
        source_quality=quality,
        evidence_id="KRX:fixture",
    )


def test_classifies_exact_positive_and_negative_boundaries_deterministically() -> None:
    with localcontext(Context(prec=6, rounding=ROUND_HALF_EVEN)):
        positive = _compute_with_closes(["100"] * 19 + ["105"])
    negative = _compute_with_closes(["100"] * 19 + ["95"])

    assert positive.regime.regime is KRMarketRegime.STRONG_BULL
    assert positive.index_state[-1].value == Decimal("5.00")
    assert negative.regime.regime is KRMarketRegime.STRONG_BEAR
    assert negative.index_state[-1].value == Decimal("-5.00")


def test_lookbacks_count_observed_sessions_not_calendar_days_or_input_order() -> None:
    session = date(2026, 7, 29)
    sessions = _sessions(count=20, end=session)
    history = list(
        reversed(
            [
                (trade_date, Decimal(100 + index))
                for index, trade_date in enumerate(sessions)
            ]
        )
    )

    result = compute_kr_market_metrics(
        session_date=session,
        index_history=history,
        breadth_counts=None,
        investor_flows=None,
        source_quality=DataQualityStatus.FRESH,
        evidence_id="KRX:fixture",
    )

    assert result.index_state[-1].value == Decimal("9.174311926605504587155963300")
    assert result.regime.regime is KRMarketRegime.STRONG_BULL
    assert result.missing_fields == ("breadth",)


def test_unchanged_and_excluded_products_are_counted_without_entering_directions() -> None:
    result = _compute_with_closes(["100"] * 19 + ["101"])

    breadth = {metric.name: metric.value for metric in result.breadth}
    assert breadth["advance_count"] == Decimal("2")
    assert breadth["decline_count"] == Decimal("1")
    assert breadth["unchanged_count"] == Decimal("1")
    assert breadth["eligible_equity_count"] == Decimal("4")
    assert breadth["excluded_non_equity_count"] == Decimal("8")
    assert "breadth" not in result.missing_fields


def test_partial_breadth_does_not_fabricate_index_feature_insufficiency() -> None:
    result = _compute_with_closes(
        ["100"] * 19 + ["101"],
        quality=DataQualityStatus.PARTIAL,
        unclassified=1,
    )

    assert result.regime.regime is KRMarketRegime.MODERATE_BULL
    assert result.regime.missing_features == ()
    assert result.missing_fields == ("breadth", "source_quality")


def test_insufficient_stale_and_unclassified_inputs_expose_unknown_without_guessing() -> None:
    insufficient = _compute_with_closes(
        ["100"] * 9 + ["101"],
        quality=DataQualityStatus.STALE,
        unclassified=1,
    )

    assert {metric.name for metric in insufficient.index_state} == {"kospi_close"}
    assert insufficient.regime.regime is KRMarketRegime.UNKNOWN
    assert insufficient.regime.missing_features == (
        "kospi_close",
        "kospi_ma20",
        "kospi_return_10d_pct",
    )
    assert insufficient.missing_fields == (
        "breadth",
        "kospi_ma20",
        "kospi_return_10d_pct",
        "source_quality",
    )
    assert insufficient.optional_missing_fields == ("investor_flows",)


def test_non_session_as_of_does_not_relabel_a_prior_close_as_current() -> None:
    completed_friday = date(2026, 7, 31)
    history = tuple(
        (trade_date, Decimal(100 + index))
        for index, trade_date in enumerate(
            _sessions(count=20, end=completed_friday)
        )
    )

    result = compute_kr_market_metrics(
        session_date=date(2026, 8, 1),
        index_history=history,
        breadth_counts=None,
        investor_flows=None,
        source_quality=DataQualityStatus.FRESH,
        evidence_id="KRX:fixture",
    )

    assert result.index_state == ()
    assert result.regime.regime is KRMarketRegime.UNKNOWN
    assert result.regime.missing_features == (
        "kospi_close",
        "kospi_ma20",
        "kospi_return_10d_pct",
    )
