"""Thin read-only adapter from the legacy KR selector to candidate contracts."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

import pandas as pd
from pydantic import ValidationError

from prism_core.candidates import (
    CandidateChannel,
    CandidateReconciliation,
    CandidateSnapshot,
    CandidateStatus,
    reconcile_candidates,
)
from prism_core.data.contracts import SecurityId
from prism_core.market import KRMarketContext, KRMarketRegime
from prism_core.strategies.contracts import Market


LegacyDiscover = Callable[[str, Mapping[str, object]], "LegacyCandidateBatch"]
ScreeningSignals = Callable[[str, float, str], Mapping[str, object]]
LegacySelect = Callable[..., Mapping[str, pd.DataFrame]]


@dataclass(frozen=True)
class LegacyCandidateBatch:
    """Read-only legacy discovery output plus its source/PIT envelope."""

    trade_date: str
    source: str
    source_snapshot_id: str
    observed_at: datetime
    available_at: datetime
    ingested_at: datetime
    evidence_ids: tuple[str, ...]
    triggers: Mapping[str, pd.DataFrame]

    def __post_init__(self) -> None:
        for clock in (self.observed_at, self.available_at, self.ingested_at):
            if clock.tzinfo is None or clock.utcoffset() is None:
                raise ValueError("legacy candidate source clocks must be timezone-aware")
        if self.observed_at > self.available_at or self.available_at > self.ingested_at:
            raise ValueError("legacy candidate source clocks are not PIT-valid")
        if not self.source or not self.source_snapshot_id or not self.evidence_ids:
            raise ValueError("legacy candidate source provenance is required")


@dataclass(frozen=True)
class CandidateParityDiscrepancy:
    code: str
    provider_symbol: str
    detail: str


@dataclass(frozen=True)
class KRCandidateSelection:
    market_context: KRMarketContext
    legacy_batch: LegacyCandidateBatch
    snapshots: tuple[CandidateSnapshot, ...]
    reconciliation: CandidateReconciliation
    legacy_selected_symbols: tuple[str, ...]
    parity_discrepancies: tuple[CandidateParityDiscrepancy, ...]


class KRCandidateSource:
    """Expose every legacy-discovered KR candidate without execution capabilities."""

    def __init__(
        self,
        *,
        legacy_discover: LegacyDiscover | None = None,
        screening_signals: ScreeningSignals | None = None,
        legacy_select: LegacySelect | None = None,
        sector_map: Mapping[str, str] | None = None,
    ) -> None:
        if legacy_discover is None or screening_signals is None or legacy_select is None:
            import trigger_batch

            legacy_discover = legacy_discover or trigger_batch.discover_read_only_candidates
            screening_signals = screening_signals or trigger_batch.calculate_screening_signals
            legacy_select = legacy_select or trigger_batch.select_final_tickers
        self._legacy_discover = legacy_discover
        self._screening_signals = screening_signals
        self._legacy_select = legacy_select
        self._sector_map = {
            _normalize_symbol(symbol): sector
            for symbol, sector in (sector_map or {}).items()
        }

    def discover(
        self, *, trigger_time: str, market_context: KRMarketContext
    ) -> KRCandidateSelection:
        legacy_context = _legacy_context(market_context, sector_map=self._sector_map)
        batch = self._legacy_discover(trigger_time, legacy_context)
        status = (
            CandidateStatus.ELIGIBLE
            if market_context.action_eligible
            else CandidateStatus.REPORT_ONLY
        )
        issues = (
            ()
            if status is CandidateStatus.ELIGIBLE
            else (f"KR_MARKET_CONTEXT_{market_context.disposition.value}",)
        )
        snapshots: list[CandidateSnapshot] = []
        candidate_records: list[CandidateSnapshot | Mapping[str, object]] = []
        signal_cache: dict[str, Mapping[str, object] | None] = {}
        for trigger_id, frame in batch.triggers.items():
            if frame.empty:
                continue
            for raw_symbol, row in frame.iterrows():
                symbol = _normalize_symbol(raw_symbol)
                if symbol not in signal_cache:
                    try:
                        signal_cache[symbol] = self._screening_signals(
                            symbol,
                            _finite_float(row.get("Close", 0)),
                            batch.trade_date,
                        )
                    except Exception:
                        signal_cache[symbol] = None
                signals = signal_cache[symbol] or {}
                candidate_status = (
                    CandidateStatus.REPORT_ONLY
                    if signal_cache[symbol] is None
                    else status
                )
                candidate_issues = tuple(
                    dict.fromkeys(
                        (
                            *issues,
                            *(
                                ("SCREENING_SIGNALS_UNAVAILABLE",)
                                if signal_cache[symbol] is None
                                else ()
                            ),
                        )
                    )
                )
                raw_scores = _raw_scores(trigger_id, row, signals)
                candidate_evidence_id = (
                    f"{batch.source_snapshot_id}:{trigger_id}:{symbol}"
                )
                candidate_payload: dict[str, object] = {
                    "market": Market.KR,
                    "security_id": SecurityId(
                        value=uuid5(NAMESPACE_URL, f"prism:kr:{symbol}")
                    ),
                    "provider": batch.source.upper(),
                    "provider_symbol": symbol,
                    "display_name": _display_name(row, symbol),
                    "channel": CandidateChannel.CORE_PRISM,
                    "source_id": f"LEGACY_KR_TRIGGER:{trigger_id}",
                    "source_snapshot_id": batch.source_snapshot_id,
                    "observed_at": batch.observed_at,
                    "available_at": batch.available_at,
                    "ingested_at": batch.ingested_at,
                    "as_of": max(batch.ingested_at, market_context.timing.as_of),
                    "trigger_ids": (trigger_id,),
                    "raw_scores": raw_scores,
                    "evidence_ids": tuple(
                        dict.fromkeys(
                            (
                                candidate_evidence_id,
                                *batch.evidence_ids,
                                *market_context.evidence_ids,
                            )
                        )
                    ),
                    "status": candidate_status,
                    "issues": candidate_issues,
                }
                try:
                    candidate = CandidateSnapshot.model_validate(candidate_payload)
                except ValidationError:
                    candidate_records.append(candidate_payload)
                    continue
                snapshots.append(candidate)
                candidate_records.append(candidate)
        reconciliation = reconcile_candidates(candidate_records)
        legacy_selected, discrepancies = self._parity(
            batch=batch,
            legacy_context=legacy_context,
            snapshots=tuple(snapshots),
            market_context=market_context,
        )
        return KRCandidateSelection(
            market_context=market_context,
            legacy_batch=batch,
            snapshots=tuple(snapshots),
            reconciliation=reconciliation,
            legacy_selected_symbols=legacy_selected,
            parity_discrepancies=discrepancies,
        )

    def _parity(
        self,
        *,
        batch: LegacyCandidateBatch,
        legacy_context: Mapping[str, object],
        snapshots: tuple[CandidateSnapshot, ...],
        market_context: KRMarketContext,
    ) -> tuple[tuple[str, ...], tuple[CandidateParityDiscrepancy, ...]]:
        if market_context.regime.regime is KRMarketRegime.UNKNOWN:
            return (), (
                CandidateParityDiscrepancy(
                    code="LEGACY_SELECTION_SKIPPED_UNKNOWN_REGIME",
                    provider_symbol="*",
                    detail="shared KRMarketContext is UNKNOWN; sideways fallback is prohibited",
                ),
            )
        selected = self._legacy_select(
            batch.triggers,
            trade_date=batch.trade_date,
            macro_context=dict(legacy_context),
        )
        selected_symbols = tuple(
            dict.fromkeys(
                _normalize_symbol(symbol)
                for frame in selected.values()
                for symbol in frame.index
            )
        )
        adapter_symbols = {
            snapshot.provider_symbol
            for snapshot in snapshots
            if snapshot.status is not CandidateStatus.DATA_UNAVAILABLE
        }
        discrepancies = [
            CandidateParityDiscrepancy(
                code="LEGACY_SELECTED_NOT_DISCOVERED",
                provider_symbol=symbol,
                detail="legacy final selector returned a symbol absent from discovery",
            )
            for symbol in selected_symbols
            if symbol not in adapter_symbols
        ]
        discrepancies.extend(
            CandidateParityDiscrepancy(
                code="ADAPTER_DISCOVERED_NOT_LEGACY_SELECTED",
                provider_symbol=symbol,
                detail="adapter preserves the uncapped discovery set; legacy final selection is capped",
            )
            for symbol in sorted(adapter_symbols.difference(selected_symbols))
        )
        return selected_symbols, tuple(discrepancies)


def _legacy_context(
    context: KRMarketContext, *, sector_map: Mapping[str, str]
) -> dict[str, object]:
    return {
        "market_regime": context.regime.regime.value.lower(),
        "session_state": context.timing.session_state.value,
        "context_session_date": context.timing.session_date.strftime("%Y%m%d"),
        "market_observed_date": context.timing.as_of.astimezone(
            ZoneInfo("Asia/Seoul")
        ).strftime("%Y%m%d"),
        "leading_sectors": [
            {
                "sector": group.group_id,
                "confidence": float(group.concentration_pct / Decimal("100")),
            }
            for group in context.group_leadership
        ],
        "sector_map": dict(sector_map),
        "context_id": context.content_hash,
    }


def _normalize_symbol(value: object) -> str:
    symbol = str(value).strip()
    return symbol.zfill(6) if symbol.isdigit() else symbol


def _display_name(row: pd.Series, symbol: str) -> str:
    value = row.get("stock_name", symbol)
    if value is None or pd.isna(value) or not str(value).strip():
        return symbol
    return str(value).strip()


def _finite_float(value: object) -> float:
    try:
        converted = float(value)
    except (TypeError, ValueError):
        return 0.0
    return converted if pd.notna(converted) and converted not in (float("inf"), float("-inf")) else 0.0


def _raw_scores(
    trigger_id: str, row: pd.Series, signals: Mapping[str, object]
) -> dict[str, Decimal]:
    values: dict[str, object] = {}
    for column, value in row.items():
        column_name = str(column)
        if "score" in column_name.lower():
            values[column_name] = value
    for key in ("return_nd", "oneil_raw", "extension_score", "extension_in_adr"):
        if key in signals:
            values[key] = signals[key]
    scores: dict[str, Decimal] = {}
    for key, value in values.items():
        try:
            decimal = Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            continue
        if decimal.is_finite():
            scores[f"{trigger_id}.{key}"] = decimal
    return scores
