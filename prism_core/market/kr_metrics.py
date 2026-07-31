"""Pure deterministic computations for normalized KR market-context inputs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Context, Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
from typing import Mapping, Sequence

from prism_core.data.contracts import DataQualityStatus
from prism_core.market.context import (
    DeterministicMetric,
    RegimeAssessment,
    RegimeFeature,
    classify_kr_regime,
)

_INDEX_FEATURE_NAMES = (
    "kospi_close",
    "kospi_ma20",
    "kospi_return_10d_pct",
)
_BREADTH_NAMES = (
    "advance_count",
    "decline_count",
    "eligible_equity_count",
    "excluded_non_equity_count",
    "unclassified_equity_count",
    "unchanged_count",
)
_DECIMAL_CONTEXT = Context(prec=28, rounding=ROUND_HALF_EVEN)


@dataclass(frozen=True)
class KRComputedMetrics:
    """Deterministic metrics plus explicit core and optional insufficiency."""

    index_state: tuple[DeterministicMetric, ...]
    breadth: tuple[DeterministicMetric, ...]
    investor_flows: tuple[DeterministicMetric, ...]
    regime: RegimeAssessment
    missing_fields: tuple[str, ...]
    optional_missing_fields: tuple[str, ...]


def _decimal(value: object, *, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{field} must be numeric") from None
    if not result.is_finite():
        raise ValueError(f"{field} must be finite")
    return result


def _metrics(
    values: Mapping[str, Decimal],
    *,
    units: Mapping[str, str],
    evidence_id: str,
) -> tuple[DeterministicMetric, ...]:
    return tuple(
        DeterministicMetric(
            name=name,
            value=value,
            unit=units[name],
            source="KRX",
            evidence_ids=(evidence_id,),
        )
        for name, value in sorted(values.items())
    )


def compute_kr_market_metrics(
    *,
    session_date: date,
    index_history: Sequence[tuple[date, Decimal | int | str]],
    breadth_counts: Mapping[str, Decimal | int | str] | None,
    investor_flows: Mapping[str, Decimal | int | str] | None,
    source_quality: DataQualityStatus,
    evidence_id: str,
) -> KRComputedMetrics:
    """Compute governed KR metrics from completed-session normalized observations.

    Calendar gaps do not consume lookback slots: each input row is one completed
    trading-session observation. Inputs are sorted by session date so collection
    ordering cannot change the result.
    """
    if not evidence_id:
        raise ValueError("evidence_id must not be empty")
    normalized = sorted(
        ((trade_date, _decimal(close, field="index close")) for trade_date, close in index_history),
        key=lambda item: item[0],
    )
    if len({trade_date for trade_date, _ in normalized}) != len(normalized):
        raise ValueError("index history contains duplicate trading sessions")
    if normalized and normalized[-1][0] > session_date:
        raise ValueError("index history cannot contain a future session")

    index_values: dict[str, Decimal] = {}
    if normalized and normalized[-1][0] == session_date:
        closes = [close for _, close in normalized]
        with localcontext(_DECIMAL_CONTEXT):
            index_values["kospi_close"] = +closes[-1]
            if len(closes) >= 20:
                index_values["kospi_ma20"] = sum(
                    closes[-20:], start=Decimal(0)
                ) / Decimal(20)
            if len(closes) >= 11 and closes[-11] != 0:
                index_values["kospi_return_10d_pct"] = (
                    closes[-1] / closes[-11] - Decimal(1)
                ) * Decimal(100)

    index_state = _metrics(
        index_values,
        units={
            "kospi_close": "index_points",
            "kospi_ma20": "index_points",
            "kospi_return_10d_pct": "percent",
        },
        evidence_id=evidence_id,
    )
    missing_index = tuple(sorted(set(_INDEX_FEATURE_NAMES) - set(index_values)))
    index_unusable = source_quality in {
        DataQualityStatus.STALE,
        DataQualityStatus.UNAVAILABLE,
        DataQualityStatus.CONFLICT,
    }
    if index_unusable:
        regime = RegimeAssessment.unknown(missing_features=_INDEX_FEATURE_NAMES)
    else:
        features = tuple(
            RegimeFeature(
                name=metric.name,
                value=metric.value,
                unit=metric.unit,
                evidence_ids=metric.evidence_ids,
            )
            for metric in index_state
        )
        regime = classify_kr_regime(features)

    breadth: tuple[DeterministicMetric, ...] = ()
    breadth_missing = breadth_counts is None
    if breadth_counts is not None:
        if set(breadth_counts) != set(_BREADTH_NAMES):
            raise ValueError("breadth counts must contain the governed fields")
        values = {
            name: _decimal(breadth_counts[name], field=f"breadth {name}")
            for name in _BREADTH_NAMES
        }
        if any(value < 0 or value != value.to_integral_value() for value in values.values()):
            raise ValueError("breadth counts must be non-negative integers")
        if (
            values["advance_count"]
            + values["decline_count"]
            + values["unchanged_count"]
            != values["eligible_equity_count"]
        ):
            raise ValueError("breadth direction counts must equal eligible equity count")
        breadth = _metrics(
            values,
            units={name: "count" for name in _BREADTH_NAMES},
            evidence_id=evidence_id,
        )
        breadth_missing = values["unclassified_equity_count"] > 0

    flow_metrics: tuple[DeterministicMetric, ...] = ()
    optional_missing: tuple[str, ...] = ()
    if investor_flows:
        flow_values = {
            str(name): _decimal(value, field=f"investor flow {name}")
            for name, value in investor_flows.items()
        }
        flow_metrics = _metrics(
            flow_values,
            units={name: "KRW" for name in flow_values},
            evidence_id=evidence_id,
        )
    else:
        optional_missing = ("investor_flows",)

    missing_fields: set[str] = set(missing_index)
    if breadth_missing:
        missing_fields.add("breadth")
    if source_quality is not DataQualityStatus.FRESH:
        missing_fields.add("source_quality")
    return KRComputedMetrics(
        index_state=index_state,
        breadth=breadth,
        investor_flows=flow_metrics,
        regime=regime,
        missing_fields=tuple(sorted(missing_fields)),
        optional_missing_fields=optional_missing,
    )
