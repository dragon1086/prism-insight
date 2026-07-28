"""Deterministic, point-in-time input pack for complete strategy scenarios.

The pack is built only from normalized provider snapshots and exact strategy
feature snapshots.  It never fills absent market facts with narrative defaults.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
from enum import Enum
from typing import Iterable, Mapping, Sequence
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from prism_core.data.contracts import (
    CorporateAction,
    DataQualityStatus,
    EvidenceItem,
    FundamentalObservation,
    MarketSnapshot,
    PriceBar,
    SecurityId,
    SymbolMapping,
)
from prism_core.data.exchange_calendar import (
    ExchangeMarket,
    latest_completed_session,
)
from prism_core.data.quality import QualityDisposition
from prism_core.strategies.contracts import (
    FeatureSnapshot,
    FeatureValue,
    Market,
    StrategyId,
)


CONTRACT_VERSION = "scenario-input-pack.v1"
_QUANTUM = Decimal("0.000000000001")
_HUNDRED = Decimal("100")
_TRADING_SESSIONS_PER_YEAR = Decimal("252")


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class ScenarioInputStatus(str, Enum):
    COMPLETE = "COMPLETE"
    ANALYSIS_INCOMPLETE = "ANALYSIS_INCOMPLETE"


class ScenarioPriceBasis(str, Enum):
    RAW = "RAW"
    SPLIT_ADJUSTED = "SPLIT_ADJUSTED"


class ScenarioIssueClass(str, Enum):
    CORE = "CORE"
    SUPPLEMENTAL = "SUPPLEMENTAL"


class ScenarioPeakState(str, Enum):
    AT_HIGH = "AT_HIGH"
    NEAR_HIGH = "NEAR_HIGH"
    BELOW_HIGH = "BELOW_HIGH"


class ScenarioInputIssue(_Model):
    field: str = Field(min_length=1)
    classification: ScenarioIssueClass
    quality: DataQualityStatus
    detail: str = Field(min_length=1)


class ScenarioIdentity(_Model):
    market: Market
    security_id: SecurityId
    provider: str | None
    provider_symbol: str | None
    benchmark_security_id: SecurityId
    benchmark_provider: str | None
    benchmark_provider_symbol: str | None


class ScenarioProvenance(_Model):
    data_snapshot_id: UUID
    snapshot_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    as_of: AwareDatetime
    created_at: AwareDatetime
    observed_at: AwareDatetime | None
    available_at: AwareDatetime | None
    ingested_at: AwareDatetime | None
    latest_completed_session: date
    excluded_incomplete_sessions: tuple[date, ...]


class ScenarioOHLC(_Model):
    session: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    currency: str
    provider: str
    provider_symbol: str
    source_record_id: str
    source_hash: str
    revision: int
    observed_at: AwareDatetime
    available_at: AwareDatetime
    ingested_at: AwareDatetime


class FormulaValue(_Model):
    name: str = Field(min_length=1)
    formula_version: str = Field(min_length=1)
    value: Decimal


class ScenarioFundamental(_Model):
    metric: str
    value: Decimal
    unit: str
    period_start: date
    period_end: date
    observed_at: AwareDatetime
    accepted_at: AwareDatetime
    ingested_at: AwareDatetime
    provider: str
    provider_symbol: str
    source_record_id: str
    source_hash: str
    revision: int
    quality: DataQualityStatus


class ScenarioEvidence(_Model):
    evidence_id: UUID
    scope: str
    kind: str
    title: str
    provider: str
    provider_symbol: str
    source_record_id: str
    source_hash: str
    content_hash: str
    source_url: str
    observed_at: AwareDatetime
    available_at: AwareDatetime
    ingested_at: AwareDatetime
    quality: DataQualityStatus


class ScenarioEvent(_Model):
    event_type: str
    title: str
    event_at: AwareDatetime
    evidence_id: UUID
    provider: str
    source_record_id: str
    quality: DataQualityStatus


class ScenarioCorporateAction(_Model):
    action_type: str
    effective_date: date
    ratio: Decimal | None
    cash_amount: Decimal | None
    currency: str | None
    provider: str
    provider_symbol: str
    source_record_id: str
    source_hash: str
    revision: int
    observed_at: AwareDatetime
    available_at: AwareDatetime
    ingested_at: AwareDatetime
    quality: DataQualityStatus


class StrategyScenarioInputs(_Model):
    strategy_id: StrategyId
    strategy_version: str
    feature_snapshot_id: UUID
    feature_version: str
    source_features: tuple[FeatureValue, ...]
    indicators: tuple[FormulaValue, ...]
    peak_state: ScenarioPeakState
    peak_state_formula_version: str


class ScenarioInputPack(_Model):
    contract_version: str
    status: ScenarioInputStatus
    policy_disposition: QualityDisposition
    price_basis: ScenarioPriceBasis
    identity: ScenarioIdentity
    provenance: ScenarioProvenance
    latest_bar: ScenarioOHLC | None
    corporate_actions: tuple[ScenarioCorporateAction, ...]
    adjustment_as_of: AwareDatetime | None
    fundamentals: tuple[ScenarioFundamental, ...]
    company_evidence: tuple[ScenarioEvidence, ...]
    market_context_evidence: tuple[ScenarioEvidence, ...]
    earnings_events: tuple[ScenarioEvent, ...]
    next_review_events: tuple[ScenarioEvent, ...]
    strategies: tuple[StrategyScenarioInputs, ...]
    issues: tuple[ScenarioInputIssue, ...]
    entry_vetoes: tuple[str, ...]


_COMPANY_EVIDENCE_KINDS = frozenset(
    {"company_filing", "company_news", "company_event"}
)
_MARKET_EVIDENCE_KINDS = frozenset(
    {"market_context", "macro", "sector_context", "industry_context"}
)
_RAW_DISCONTINUITY_ACTIONS = frozenset(
    {"SPLIT", "REVERSE_SPLIT", "STOCK_DIVIDEND", "RIGHTS", "SPINOFF"}
)


def build_scenario_input_pack(
    *,
    snapshot: MarketSnapshot,
    market: Market,
    security_id: SecurityId,
    benchmark_security_id: SecurityId,
    price_basis: ScenarioPriceBasis,
    feature_snapshots: Mapping[StrategyId, FeatureSnapshot],
) -> ScenarioInputPack:
    """Build a deterministic scenario pack and classify every absent input.

    Missing or conflicting core identity, price basis, completed-session history,
    or strategy feature identity returns ``ANALYSIS_INCOMPLETE`` plus
    ``REPORT_ONLY``.  Supplemental gaps preserve computed analysis but add an
    entry veto and ``REPORT_ONLY`` disposition.
    """

    if not isinstance(snapshot, MarketSnapshot):
        raise TypeError("snapshot must be MarketSnapshot")
    if not isinstance(market, Market):
        raise TypeError("market must be Market")
    if not isinstance(security_id, SecurityId) or not isinstance(
        benchmark_security_id, SecurityId
    ):
        raise TypeError("security identities must be SecurityId values")
    if security_id == benchmark_security_id:
        raise ValueError("security and benchmark identities must differ")
    if not isinstance(price_basis, ScenarioPriceBasis):
        raise TypeError("price_basis must be ScenarioPriceBasis")
    if not isinstance(feature_snapshots, Mapping):
        raise TypeError("feature_snapshots must be a mapping")
    if any(
        not isinstance(key, StrategyId) or not isinstance(value, FeatureSnapshot)
        for key, value in feature_snapshots.items()
    ):
        raise TypeError(
            "feature_snapshots must map StrategyId keys to FeatureSnapshot values"
        )

    issues: list[ScenarioInputIssue] = []
    if snapshot.market != market.value:
        _issue(issues, "identity.market", ScenarioIssueClass.CORE, DataQualityStatus.CONFLICT, "snapshot market does not match requested market")
    if snapshot.quality is not DataQualityStatus.FRESH:
        _issue(issues, "price.snapshot_quality", ScenarioIssueClass.CORE, snapshot.quality, "core provider snapshot is not fresh")

    completed = latest_completed_session(
        ExchangeMarket.KRX if market is Market.KR else ExchangeMarket.NYSE,
        snapshot.as_of_date,
    )
    stock_mapping = _mapping_for(snapshot.symbol_mappings, security_id, market, snapshot.as_of_date, issues, "identity.security")
    benchmark_mapping = _mapping_for(snapshot.symbol_mappings, benchmark_security_id, market, snapshot.as_of_date, issues, "identity.benchmark")

    stock_bars, excluded_stock = _bars_for(
        snapshot.price_bars,
        security_id,
        completed,
        snapshot.as_of_date,
        stock_mapping,
        issues,
        "price.security",
    )
    benchmark_bars, excluded_benchmark = _bars_for(
        snapshot.price_bars,
        benchmark_security_id,
        completed,
        snapshot.as_of_date,
        benchmark_mapping,
        issues,
        "price.benchmark",
    )
    stock_by_session = {bar.bar_start.date(): bar for bar in stock_bars}
    benchmark_by_session = {bar.bar_start.date(): bar for bar in benchmark_bars}
    sessions = tuple(sorted(set(stock_by_session) & set(benchmark_by_session)))
    if set(stock_by_session) != set(benchmark_by_session):
        _issue(issues, "price.session_alignment", ScenarioIssueClass.CORE, DataQualityStatus.CONFLICT, "security and benchmark completed sessions do not align")
    if not sessions or sessions[-1] != completed:
        _issue(issues, "price.latest_completed_session", ScenarioIssueClass.CORE, DataQualityStatus.UNAVAILABLE, "latest completed exchange session is unavailable")
    if len(sessions) < 252:
        _issue(issues, "price.history_252", ScenarioIssueClass.CORE, DataQualityStatus.PARTIAL, "at least 252 aligned completed sessions are required")

    selected_stock: tuple[_SelectedBar, ...] = ()
    selected_benchmark: tuple[_SelectedBar, ...] = ()
    if sessions:
        try:
            selected_stock = tuple(_select_bar(stock_by_session[item], price_basis) for item in sessions)
            selected_benchmark = tuple(_select_bar(benchmark_by_session[item], price_basis) for item in sessions)
        except ValueError as exc:
            _issue(issues, "price.basis", ScenarioIssueClass.CORE, DataQualityStatus.UNAVAILABLE, str(exc))

    actions = _corporate_actions(
        snapshot.corporate_actions,
        security_id,
        snapshot.as_of_date,
        price_basis,
        issues,
    )
    benchmark_actions = _corporate_actions(
        snapshot.corporate_actions,
        benchmark_security_id,
        snapshot.as_of_date,
        price_basis,
        issues,
        issue_classification=ScenarioIssueClass.CORE,
    )
    if price_basis is ScenarioPriceBasis.RAW and selected_stock:
        structure_start = selected_stock[-min(len(selected_stock), 252)].session
        structure_end = selected_stock[-1].session
        if any(
            item.action_type in _RAW_DISCONTINUITY_ACTIONS
            and structure_start <= item.effective_date <= structure_end
            for item in actions
        ):
            _issue(
                issues,
                "price.raw_corporate_action_window",
                ScenarioIssueClass.CORE,
                DataQualityStatus.CONFLICT,
                "raw price history crosses a split corporate action in the 252-session structure window",
            )
    if price_basis is ScenarioPriceBasis.RAW and selected_benchmark:
        benchmark_start = selected_benchmark[
            -min(len(selected_benchmark), 252)
        ].session
        benchmark_end = selected_benchmark[-1].session
        if any(
            item.action_type in _RAW_DISCONTINUITY_ACTIONS
            and benchmark_start <= item.effective_date <= benchmark_end
            for item in benchmark_actions
        ):
            _issue(
                issues,
                "price.raw_benchmark_corporate_action_window",
                ScenarioIssueClass.CORE,
                DataQualityStatus.CONFLICT,
                "raw benchmark history crosses a discontinuous corporate action in the relative-strength window",
            )
    adjustment_as_of = None
    if price_basis is ScenarioPriceBasis.SPLIT_ADJUSTED and selected_stock:
        adjustment_values = {
            stock_by_session[item.session].adjustment_as_of
            for item in selected_stock
            if stock_by_session[item.session].adjustment_as_of is not None
        }
        adjustment_as_of = max(adjustment_values) if adjustment_values else None
        if adjustment_as_of is None or not actions:
            _issue(issues, "price.adjustment_provenance", ScenarioIssueClass.CORE, DataQualityStatus.UNAVAILABLE, "split-adjusted basis requires adjustment time and corporate-action provenance")
        else:
            if len(adjustment_values) != 1:
                _issue(
                    issues,
                    "price.adjustment_provenance",
                    ScenarioIssueClass.CORE,
                    DataQualityStatus.CONFLICT,
                    "split-adjusted price history contains mixed adjustment vintages",
                )
            discontinuity_dates = tuple(
                item.effective_date
                for item in actions
                if item.action_type in _RAW_DISCONTINUITY_ACTIONS
            )
            if discontinuity_dates and min(adjustment_values).date() < max(
                discontinuity_dates
            ):
                _issue(
                    issues,
                    "price.adjustment_provenance",
                    ScenarioIssueClass.CORE,
                    DataQualityStatus.CONFLICT,
                    "adjustment vintage predates the latest discontinuous corporate action",
                )

    fundamentals = _fundamentals(
        snapshot.fundamentals, security_id, snapshot.as_of_date, issues
    )
    company_evidence, market_evidence, earnings_events, next_review_events = _evidence(
        snapshot.evidence,
        security_id,
        benchmark_security_id,
        snapshot.as_of_date,
        issues,
    )

    valid_features: dict[StrategyId, FeatureSnapshot] = {}
    for strategy_id in (StrategyId.SWING_V1, StrategyId.TREND_V1):
        feature = feature_snapshots.get(strategy_id)
        if feature is None:
            _issue(issues, f"features.{strategy_id.value}", ScenarioIssueClass.CORE, DataQualityStatus.UNAVAILABLE, "strategy feature snapshot is unavailable")
            continue
        if (
            feature.strategy_id is not strategy_id
            or feature.market is not market
            or feature.security_id != security_id
            or feature.data_snapshot_id != snapshot.snapshot_id
            or feature.as_of != snapshot.as_of_date
        ):
            _issue(issues, f"features.{strategy_id.value}.identity", ScenarioIssueClass.CORE, DataQualityStatus.CONFLICT, "feature snapshot identity does not match provider snapshot")
            continue
        if (
            feature.data_quality_status is not DataQualityStatus.FRESH
            or feature.quality_disposition is not QualityDisposition.ACCEPT
        ):
            _issue(issues, f"features.{strategy_id.value}.quality", ScenarioIssueClass.CORE, feature.data_quality_status, "strategy features are not proposal-eligible")
            continue
        valid_features[strategy_id] = feature

    if len(selected_stock) >= 21 and _mean(
        tuple(item.volume for item in selected_stock[-21:-1])
    ) <= 0:
        _issue(
            issues,
            "price.liquidity",
            ScenarioIssueClass.CORE,
            DataQualityStatus.CONFLICT,
            "prior 20-session average volume must be positive",
        )

    strategies: list[StrategyScenarioInputs] = []
    if not any(item.classification is ScenarioIssueClass.CORE for item in issues):
        for strategy_id in (StrategyId.SWING_V1, StrategyId.TREND_V1):
            feature = valid_features[strategy_id]
            indicators = _strategy_indicators(
                strategy_id,
                selected_stock,
                selected_benchmark,
            )
            strategies.append(
                StrategyScenarioInputs(
                    strategy_id=strategy_id,
                    strategy_version=feature.strategy_version.value,
                    feature_snapshot_id=feature.feature_snapshot_id,
                    feature_version=feature.feature_version,
                    source_features=feature.values,
                    indicators=indicators,
                    peak_state=_peak_state(selected_stock),
                    peak_state_formula_version="peak_state.distance_from_high_252.v1",
                )
            )

    core_incomplete = any(item.classification is ScenarioIssueClass.CORE for item in issues)
    policy = QualityDisposition.REPORT_ONLY if issues else QualityDisposition.ACCEPT
    timings = tuple(bar.timing for bar in stock_bars)
    latest_bar = _scenario_bar(selected_stock[-1]) if selected_stock else None
    excluded = tuple(
        sorted(
            {bar.bar_start.date() for bar in (*excluded_stock, *excluded_benchmark)}
        )
    )
    entry_vetoes = tuple(
        dict.fromkeys(
            f"{item.classification.value}:{item.field}:{item.quality.value}"
            for item in issues
        )
    )
    return ScenarioInputPack(
        contract_version=CONTRACT_VERSION,
        status=(ScenarioInputStatus.ANALYSIS_INCOMPLETE if core_incomplete else ScenarioInputStatus.COMPLETE),
        policy_disposition=policy,
        price_basis=price_basis,
        identity=ScenarioIdentity(
            market=market,
            security_id=security_id,
            provider=None if stock_mapping is None else stock_mapping.provider,
            provider_symbol=None if stock_mapping is None else stock_mapping.provider_symbol,
            benchmark_security_id=benchmark_security_id,
            benchmark_provider=None if benchmark_mapping is None else benchmark_mapping.provider,
            benchmark_provider_symbol=None if benchmark_mapping is None else benchmark_mapping.provider_symbol,
        ),
        provenance=ScenarioProvenance(
            data_snapshot_id=snapshot.snapshot_id,
            snapshot_content_hash=snapshot.content_hash,
            as_of=snapshot.as_of_date,
            created_at=snapshot.created_at,
            observed_at=max((item.observed_at for item in timings), default=None),
            available_at=max((item.available_at for item in timings), default=None),
            ingested_at=max((item.ingested_at for item in timings), default=None),
            latest_completed_session=completed,
            excluded_incomplete_sessions=excluded,
        ),
        latest_bar=latest_bar,
        corporate_actions=actions,
        adjustment_as_of=adjustment_as_of,
        fundamentals=fundamentals,
        company_evidence=company_evidence,
        market_context_evidence=market_evidence,
        earnings_events=earnings_events,
        next_review_events=next_review_events,
        strategies=tuple(strategies),
        issues=tuple(issues),
        entry_vetoes=entry_vetoes,
    )


class _SelectedBar:
    __slots__ = (
        "session", "open", "high", "low", "close", "volume", "bar"
    )

    def __init__(
        self,
        *,
        session: date,
        open: Decimal,
        high: Decimal,
        low: Decimal,
        close: Decimal,
        volume: Decimal,
        bar: PriceBar,
    ) -> None:
        self.session = session
        self.open = open
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume
        self.bar = bar


def _mapping_for(
    mappings: tuple[SymbolMapping, ...],
    security_id: SecurityId,
    market: Market,
    as_of: datetime,
    issues: list[ScenarioInputIssue],
    field: str,
) -> SymbolMapping | None:
    candidates = tuple(item for item in mappings if item.security_id == security_id)
    selected = tuple(
        item
        for item in candidates
        if item.valid_from <= as_of
        and (item.valid_to is None or item.valid_to > as_of)
        and item.timing.observed_at <= item.timing.available_at
        and item.timing.available_at <= item.timing.ingested_at
        and item.timing.available_at <= as_of
        and item.timing.as_of_date == as_of
    )
    if not selected:
        _issue(issues, field, ScenarioIssueClass.CORE, DataQualityStatus.UNAVAILABLE, "active provider symbol mapping is unavailable")
        return None
    identities = {(item.provider, item.provider_symbol, item.market) for item in selected}
    if len(identities) != 1 or selected[0].market != market.value:
        _issue(issues, field, ScenarioIssueClass.CORE, DataQualityStatus.CONFLICT, "provider symbol mappings conflict")
        return None
    return selected[0]


def _bars_for(
    bars: tuple[PriceBar, ...],
    security_id: SecurityId,
    completed: date,
    as_of: datetime,
    mapping: SymbolMapping | None,
    issues: list[ScenarioInputIssue],
    field: str,
) -> tuple[tuple[PriceBar, ...], tuple[PriceBar, ...]]:
    all_selected = tuple(item for item in bars if item.security_id == security_id)
    completed_candidates = tuple(
        item for item in all_selected if item.bar_start.date() <= completed
    )
    completed_values: list[PriceBar] = []
    for item in completed_candidates:
        if (
            item.timing.observed_at > item.timing.available_at
            or item.timing.available_at > item.timing.ingested_at
            or item.timing.available_at > as_of
            or item.timing.as_of_date != as_of
        ):
            _issue(
                issues,
                f"{field}.timing",
                ScenarioIssueClass.CORE,
                DataQualityStatus.CONFLICT,
                "completed-session price timing violates the point-in-time boundary",
            )
            continue
        completed_values.append(item)
    completed_bars = tuple(completed_values)
    excluded = tuple(item for item in all_selected if item.bar_start.date() > completed)
    sessions = [item.bar_start.date() for item in completed_bars]
    if len(sessions) != len(set(sessions)):
        _issue(issues, field, ScenarioIssueClass.CORE, DataQualityStatus.CONFLICT, "multiple bars exist for one completed session")
        return (), excluded
    if mapping is not None and any(
        item.provider != mapping.provider or item.provider_symbol != mapping.provider_symbol
        for item in completed_bars
    ):
        _issue(issues, f"{field}.symbol", ScenarioIssueClass.CORE, DataQualityStatus.CONFLICT, "price bars do not match the active provider symbol mapping")
    if any(item.quality is not DataQualityStatus.FRESH for item in completed_bars):
        _issue(issues, f"{field}.quality", ScenarioIssueClass.CORE, DataQualityStatus.CONFLICT, "completed-session price bars contain non-fresh quality")
    return tuple(sorted(completed_bars, key=lambda item: item.bar_start)), excluded


def _select_bar(bar: PriceBar, basis: ScenarioPriceBasis) -> _SelectedBar:
    if basis is ScenarioPriceBasis.RAW:
        values = (bar.raw_open, bar.raw_high, bar.raw_low, bar.raw_close, bar.raw_volume)
    else:
        values = (
            bar.adjusted_open,
            bar.adjusted_high,
            bar.adjusted_low,
            bar.adjusted_close,
            bar.adjusted_volume,
        )
        if any(item is None for item in values):
            raise ValueError("split-adjusted basis requires complete adjusted OHLCV")
    return _SelectedBar(
        session=bar.bar_start.date(),
        open=values[0],  # type: ignore[arg-type]
        high=values[1],  # type: ignore[arg-type]
        low=values[2],  # type: ignore[arg-type]
        close=values[3],  # type: ignore[arg-type]
        volume=values[4],  # type: ignore[arg-type]
        bar=bar,
    )


def _scenario_bar(item: _SelectedBar) -> ScenarioOHLC:
    bar = item.bar
    return ScenarioOHLC(
        session=item.session,
        open=item.open,
        high=item.high,
        low=item.low,
        close=item.close,
        volume=item.volume,
        currency=bar.currency,
        provider=bar.provider,
        provider_symbol=bar.provider_symbol,
        source_record_id=bar.source_record_id,
        source_hash=bar.source_hash,
        revision=bar.revision,
        observed_at=bar.timing.observed_at,
        available_at=bar.timing.available_at,
        ingested_at=bar.timing.ingested_at,
    )


def _fundamentals(
    values: tuple[FundamentalObservation, ...],
    security_id: SecurityId,
    as_of: datetime,
    issues: list[ScenarioInputIssue],
) -> tuple[ScenarioFundamental, ...]:
    selected: list[FundamentalObservation] = []
    for item in values:
        if item.security_id != security_id:
            continue
        if (
            item.timing.observed_at > item.timing.available_at
            or item.timing.available_at > item.timing.ingested_at
            or item.timing.available_at > as_of
            or item.timing.as_of_date != as_of
        ):
            _issue(
                issues,
                f"fundamentals.{item.metric}.timing",
                ScenarioIssueClass.SUPPLEMENTAL,
                DataQualityStatus.CONFLICT,
                "fundamental timing violates the point-in-time boundary",
            )
            continue
        selected.append(item)
    if not selected:
        if not any(item.field.startswith("fundamentals.") for item in issues):
            _issue(issues, "fundamentals", ScenarioIssueClass.SUPPLEMENTAL, DataQualityStatus.UNAVAILABLE, "point-in-time fundamental statements are unavailable")
        return ()
    by_identity: dict[tuple[str, date, date], list[FundamentalObservation]] = {}
    for item in selected:
        by_identity.setdefault((item.metric, item.period_start, item.period_end), []).append(item)
    result: list[ScenarioFundamental] = []
    for identity, revisions in sorted(by_identity.items()):
        highest_revision = max(item.revision for item in revisions)
        current = tuple(item for item in revisions if item.revision == highest_revision)
        values_and_units = {(item.value, item.unit) for item in current}
        if len(values_and_units) != 1:
            _issue(issues, f"fundamentals.{identity[0]}", ScenarioIssueClass.SUPPLEMENTAL, DataQualityStatus.CONFLICT, "same-vintage fundamental values conflict")
            continue
        item = sorted(current, key=lambda value: (value.provider, value.source_record_id))[0]
        if item.quality is not DataQualityStatus.FRESH:
            _issue(issues, f"fundamentals.{item.metric}", ScenarioIssueClass.SUPPLEMENTAL, item.quality, "fundamental statement is not fresh")
        result.append(
            ScenarioFundamental(
                metric=item.metric,
                value=item.value,
                unit=item.unit,
                period_start=item.period_start,
                period_end=item.period_end,
                observed_at=item.timing.observed_at,
                accepted_at=item.timing.available_at,
                ingested_at=item.timing.ingested_at,
                provider=item.provider,
                provider_symbol=item.provider_symbol,
                source_record_id=item.source_record_id,
                source_hash=item.source_hash,
                revision=item.revision,
                quality=item.quality,
            )
        )
    return tuple(result)


def _evidence(
    values: tuple[EvidenceItem, ...],
    security_id: SecurityId,
    benchmark_security_id: SecurityId,
    as_of: datetime,
    issues: list[ScenarioInputIssue],
) -> tuple[
    tuple[ScenarioEvidence, ...],
    tuple[ScenarioEvidence, ...],
    tuple[ScenarioEvent, ...],
    tuple[ScenarioEvent, ...],
]:
    company: list[ScenarioEvidence] = []
    market: list[ScenarioEvidence] = []
    earnings: list[ScenarioEvent] = []
    next_review: list[ScenarioEvent] = []
    for item in sorted(values, key=lambda value: str(value.evidence_id)):
        relevant = (
            item.security_id == security_id
            and item.kind
            in {*_COMPANY_EVIDENCE_KINDS, "earnings_event", "next_review"}
        ) or (
            item.security_id in (security_id, benchmark_security_id)
            and item.kind in _MARKET_EVIDENCE_KINDS
        )
        if not relevant:
            continue
        if (
            item.timing.observed_at > item.timing.available_at
            or item.timing.available_at > item.timing.ingested_at
            or item.timing.available_at > as_of
            or item.timing.as_of_date != as_of
        ):
            _issue(
                issues,
                f"evidence.{item.kind}.timing",
                ScenarioIssueClass.SUPPLEMENTAL,
                DataQualityStatus.CONFLICT,
                "evidence timing violates the point-in-time boundary",
            )
            continue
        if item.quality is not DataQualityStatus.FRESH:
            _issue(
                issues,
                f"evidence.{item.kind}.quality",
                ScenarioIssueClass.SUPPLEMENTAL,
                item.quality,
                "supplemental evidence is not fresh",
            )
        if item.kind in _COMPANY_EVIDENCE_KINDS and item.security_id == security_id:
            company.append(_scenario_evidence(item, "COMPANY"))
        elif item.kind in _MARKET_EVIDENCE_KINDS and item.security_id in {
            security_id,
            benchmark_security_id,
        }:
            market.append(_scenario_evidence(item, "MARKET_CONTEXT"))
        if item.kind == "earnings_event" and item.security_id == security_id:
            earnings.append(_scenario_event(item, "EARNINGS"))
        if item.kind == "next_review" and item.security_id == security_id:
            next_review.append(_scenario_event(item, "NEXT_REVIEW"))
    if not company:
        _issue(issues, "evidence.company", ScenarioIssueClass.SUPPLEMENTAL, DataQualityStatus.UNAVAILABLE, "company-specific evidence is unavailable")
    if not market:
        _issue(issues, "evidence.market_context", ScenarioIssueClass.SUPPLEMENTAL, DataQualityStatus.UNAVAILABLE, "market-context evidence is unavailable")
    if not earnings:
        _issue(issues, "events.earnings", ScenarioIssueClass.SUPPLEMENTAL, DataQualityStatus.UNAVAILABLE, "earnings event evidence is unavailable")
    if not next_review:
        _issue(issues, "events.next_review", ScenarioIssueClass.SUPPLEMENTAL, DataQualityStatus.UNAVAILABLE, "next-review event evidence is unavailable")
    return tuple(company), tuple(market), tuple(earnings), tuple(next_review)


def _scenario_evidence(item: EvidenceItem, scope: str) -> ScenarioEvidence:
    return ScenarioEvidence(
        evidence_id=item.evidence_id,
        scope=scope,
        kind=item.kind,
        title=item.title,
        provider=item.provider,
        provider_symbol=item.provider_symbol,
        source_record_id=item.source_record_id,
        source_hash=item.source_hash,
        content_hash=item.content_hash,
        source_url=str(item.source_url),
        observed_at=item.timing.observed_at,
        available_at=item.timing.available_at,
        ingested_at=item.timing.ingested_at,
        quality=item.quality,
    )


def _scenario_event(item: EvidenceItem, event_type: str) -> ScenarioEvent:
    return ScenarioEvent(
        event_type=event_type,
        title=item.title,
        event_at=item.timing.observed_at,
        evidence_id=item.evidence_id,
        provider=item.provider,
        source_record_id=item.source_record_id,
        quality=item.quality,
    )


def _corporate_action(item: CorporateAction) -> ScenarioCorporateAction:
    return ScenarioCorporateAction(
        action_type=item.action_type.value,
        effective_date=item.effective_date,
        ratio=item.ratio,
        cash_amount=item.cash_amount,
        currency=item.currency,
        provider=item.provider,
        provider_symbol=item.provider_symbol,
        source_record_id=item.source_record_id,
        source_hash=item.source_hash,
        revision=item.revision,
        observed_at=item.timing.observed_at,
        available_at=item.timing.available_at,
        ingested_at=item.timing.ingested_at,
        quality=item.quality,
    )


def _corporate_actions(
    values: tuple[CorporateAction, ...],
    security_id: SecurityId,
    as_of: datetime,
    price_basis: ScenarioPriceBasis,
    issues: list[ScenarioInputIssue],
    *,
    issue_classification: ScenarioIssueClass | None = None,
) -> tuple[ScenarioCorporateAction, ...]:
    selected: list[CorporateAction] = []
    classification = issue_classification or (
        ScenarioIssueClass.CORE
        if price_basis is ScenarioPriceBasis.SPLIT_ADJUSTED
        else ScenarioIssueClass.SUPPLEMENTAL
    )
    for item in values:
        if item.security_id != security_id:
            continue
        if (
            item.timing.observed_at > item.timing.available_at
            or item.timing.available_at > item.timing.ingested_at
            or item.timing.available_at > as_of
            or item.timing.as_of_date != as_of
        ):
            _issue(
                issues,
                "corporate_actions.timing",
                classification,
                DataQualityStatus.CONFLICT,
                "corporate-action timing violates the point-in-time boundary",
            )
            continue
        if item.quality is not DataQualityStatus.FRESH:
            _issue(
                issues,
                "corporate_actions.quality",
                classification,
                item.quality,
                "corporate-action provenance is not fresh",
            )
            continue
        selected.append(item)
    return tuple(
        _corporate_action(item)
        for item in sorted(
            selected,
            key=lambda item: (
                item.effective_date,
                item.provider,
                item.source_record_id,
                item.revision,
            ),
        )
    )


def _strategy_indicators(
    strategy_id: StrategyId,
    stock: tuple[_SelectedBar, ...],
    benchmark: tuple[_SelectedBar, ...],
) -> tuple[FormulaValue, ...]:
    prefix = strategy_id.value.lower()
    closes = tuple(item.close for item in stock)
    benchmark_closes = tuple(item.close for item in benchmark)
    highs = tuple(item.high for item in stock)
    lows = tuple(item.low for item in stock)
    opens = tuple(item.open for item in stock)
    volumes = tuple(item.volume for item in stock)
    if strategy_id is StrategyId.SWING_V1:
        ma_windows = (20, 50)
        slope_window = 5
        structure_window = 20
        rs_window = 20
        momentum_windows = (5, 20)
    else:
        ma_windows = (50, 200)
        slope_window = 20
        structure_window = 60
        rs_window = 60
        momentum_windows = (60, 120)

    values: list[FormulaValue] = []
    for window in ma_windows:
        values.append(_formula(f"{prefix}.ma_{window}", f"sma.close.{window}.v1", _mean(closes[-window:])))
    primary_ma = ma_windows[0]
    current_ma = _mean(closes[-primary_ma:])
    prior_ma = _mean(closes[-(primary_ma + slope_window):-slope_window])
    values.append(_formula(f"{prefix}.ma_{primary_ma}_slope_{slope_window}", f"sma.percent_slope.{primary_ma}.{slope_window}.v1", _percent(current_ma, prior_ma)))

    true_ranges = tuple(
        max(
            highs[index] - lows[index],
            abs(highs[index] - closes[index - 1]),
            abs(lows[index] - closes[index - 1]),
        )
        for index in range(len(closes) - 14, len(closes))
    )
    atr = _mean(true_ranges)
    values.extend(
        (
            _formula(f"{prefix}.atr_14", "atr.simple_true_range.14.v1", atr),
            _formula(f"{prefix}.atr_percent_14", "atr.simple_true_range_percent_close.14.v1", atr / closes[-1] * _HUNDRED),
            _formula(f"{prefix}.realized_volatility_20", "realized_vol.arithmetic_return.sample_stddev.20.ann252.v1", _realized_volatility(closes, 20)),
            _formula(f"{prefix}.max_abs_gap_percent_20", "gap.max_abs_open_vs_previous_close.20.v1", _max_gap(opens, closes, 20)),
        )
    )
    prior_highs = highs[-(structure_window + 1):-1]
    prior_lows = lows[-(structure_window + 1):-1]
    resistance = max(prior_highs)
    support = min(prior_lows)
    values.extend(
        (
            _formula(f"{prefix}.recent_swing_high_{structure_window}", f"structure.max_high.{structure_window}.including_latest.v1", max(highs[-structure_window:])),
            _formula(f"{prefix}.recent_swing_low_{structure_window}", f"structure.min_low.{structure_window}.including_latest.v1", min(lows[-structure_window:])),
            _formula(f"{prefix}.support_{structure_window}", f"structure.min_low.{structure_window}.excluding_latest.v1", support),
            _formula(f"{prefix}.resistance_{structure_window}", f"structure.max_high.{structure_window}.excluding_latest.v1", resistance),
            _formula(f"{prefix}.breakout_level_{structure_window}", f"structure.prior_resistance.{structure_window}.v1", resistance),
            _formula(f"{prefix}.relative_strength_{rs_window}", f"relative_strength.return_difference.{rs_window}.v1", _return(closes, rs_window) - _return(benchmark_closes, rs_window)),
        )
    )
    high_52 = max(highs[-252:])
    values.extend(
        (
            _formula(f"{prefix}.high_52_week", "high.max_high.252_sessions.v1", high_52),
            _formula(f"{prefix}.distance_from_52_week_high_percent", "high.latest_close_distance_from_max_high.252_sessions.v1", (closes[-1] / high_52 - Decimal("1")) * _HUNDRED),
        )
    )
    for window in momentum_windows:
        values.append(_formula(f"{prefix}.momentum_{window}", f"momentum.close_return.{window}.v1", _return(closes, window)))
    average_volume = _mean(volumes[-21:-1])
    values.extend(
        (
            _formula(f"{prefix}.volume_expansion_20", "volume.latest_vs_prior_mean.20.v1", volumes[-1] / average_volume * _HUNDRED),
            _formula(f"{prefix}.average_volume_20", "liquidity.mean_volume.20.v1", _mean(volumes[-20:])),
            _formula(f"{prefix}.average_dollar_volume_20", "liquidity.mean_close_times_volume.20.v1", _mean(tuple(closes[index] * volumes[index] for index in range(len(closes) - 20, len(closes))))),
            _formula(f"{prefix}.latest_dollar_volume", "liquidity.latest_close_times_volume.v1", closes[-1] * volumes[-1]),
        )
    )
    return tuple(sorted(values, key=lambda item: item.name))


def _peak_state(stock: tuple[_SelectedBar, ...]) -> ScenarioPeakState:
    high_52 = max(item.high for item in stock[-252:])
    distance = (stock[-1].close / high_52 - Decimal("1")) * _HUNDRED
    if distance >= Decimal("-0.5"):
        return ScenarioPeakState.AT_HIGH
    if distance >= Decimal("-5"):
        return ScenarioPeakState.NEAR_HIGH
    return ScenarioPeakState.BELOW_HIGH


def _formula(name: str, version: str, value: Decimal) -> FormulaValue:
    return FormulaValue(name=name, formula_version=version, value=_normalize(value))


def _mean(values: Sequence[Decimal]) -> Decimal:
    if not values:
        raise ValueError("formula window must not be empty")
    with localcontext(Context(prec=50, rounding=ROUND_HALF_EVEN)):
        return sum(values, Decimal("0")) / Decimal(len(values))


def _return(values: Sequence[Decimal], window: int) -> Decimal:
    if len(values) <= window:
        raise ValueError(f"at least {window + 1} values are required")
    return (values[-1] / values[-(window + 1)] - Decimal("1")) * _HUNDRED


def _percent(current: Decimal, prior: Decimal) -> Decimal:
    if prior <= 0:
        raise ValueError("formula denominator must be positive")
    return (current / prior - Decimal("1")) * _HUNDRED


def _realized_volatility(closes: Sequence[Decimal], window: int) -> Decimal:
    returns = tuple(
        closes[index] / closes[index - 1] - Decimal("1")
        for index in range(len(closes) - window, len(closes))
    )
    mean = _mean(returns)
    variance = sum((item - mean) ** 2 for item in returns) / Decimal(window - 1)
    return variance.sqrt() * _TRADING_SESSIONS_PER_YEAR.sqrt() * _HUNDRED


def _max_gap(opens: Sequence[Decimal], closes: Sequence[Decimal], window: int) -> Decimal:
    return max(
        abs(opens[index] / closes[index - 1] - Decimal("1")) * _HUNDRED
        for index in range(len(closes) - window, len(closes))
    )


def _normalize(value: Decimal) -> Decimal:
    if not value.is_finite():
        raise ValueError("computed scenario input must be finite")
    with localcontext(Context(prec=50, rounding=ROUND_HALF_EVEN)):
        return value.quantize(_QUANTUM)


def _issue(
    issues: list[ScenarioInputIssue],
    field: str,
    classification: ScenarioIssueClass,
    quality: DataQualityStatus,
    detail: str,
) -> None:
    candidate = ScenarioInputIssue(
        field=field,
        classification=classification,
        quality=quality,
        detail=detail,
    )
    if candidate not in issues:
        issues.append(candidate)
