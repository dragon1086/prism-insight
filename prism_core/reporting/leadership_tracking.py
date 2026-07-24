"""Strict schemas and append-only storage for leadership report evidence."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Annotated, Literal
from uuid import NAMESPACE_URL, uuid5

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    field_validator,
    model_validator,
)

from prism_core.data.contracts import DataQualityStatus
from prism_core.storage.database import transaction

SCHEMA_VERSION = "market_tracking_v1"
PROVIDER = "hermes_agent_report"
POLICY_DISPOSITION = "REPORT_ONLY"
MARKET_OBSERVATION_KIND = "leadership_market_state"
SECURITY_OBSERVATION_KIND = "leadership_security_state"
REPORT_KIND = "market_leadership_v1"

NonEmptyStr = Annotated[str, Field(min_length=1)]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
FiniteDecimal = Annotated[Decimal, Field(allow_inf_nan=False)]
NonNegativeDecimal = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]
Score = Annotated[Decimal, Field(ge=-100, le=100, allow_inf_nan=False)]
RelativeStrengthScore = Annotated[Decimal, Field(ge=0, le=100, allow_inf_nan=False)]


class TrackingModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class Market(str, Enum):
    KR = "KR"
    US = "US"


class MarketStage(str, Enum):
    PRIOR_CLOSE_CONTEXT = "PRIOR_CLOSE_CONTEXT"
    PREOPEN_OBSERVATIONS = "PREOPEN_OBSERVATIONS"
    INTRADAY = "INTRADAY"
    CLOSE = "CLOSE"
    POST_CLOSE_EVENTS = "POST_CLOSE_EVENTS"
    PREMARKET_OBSERVATIONS = "PREMARKET_OBSERVATIONS"


class ConfirmationState(str, Enum):
    PROVISIONAL = "PROVISIONAL"
    CONFIRMED = "CONFIRMED"


class MarketRegime(str, Enum):
    STRONG_BULL = "STRONG_BULL"
    MODERATE_BULL = "MODERATE_BULL"
    SIDEWAYS = "SIDEWAYS"
    MODERATE_BEAR = "MODERATE_BEAR"
    STRONG_BEAR = "STRONG_BEAR"
    UNKNOWN = "UNKNOWN"


class EventType(str, Enum):
    MACRO = "MACRO"
    EARNINGS = "EARNINGS"
    POLICY = "POLICY"
    MARKET = "MARKET"
    RISK = "RISK"
    OTHER = "OTHER"


class High52WeekState(str, Enum):
    NEW_HIGH = "NEW_HIGH"
    NEAR_HIGH = "NEAR_HIGH"
    BELOW_HIGH = "BELOW_HIGH"
    UNKNOWN = "UNKNOWN"


class MomentumState(str, Enum):
    ACCELERATING = "ACCELERATING"
    ADVANCING = "ADVANCING"
    FLAT = "FLAT"
    WEAKENING = "WEAKENING"
    DECLINING = "DECLINING"
    UNKNOWN = "UNKNOWN"


class PeakState(str, Enum):
    NOT_PEAKED = "NOT_PEAKED"
    APPROACHING = "APPROACHING"
    PEAKED = "PEAKED"
    DECLINING = "DECLINING"
    UNKNOWN = "UNKNOWN"


class StrategyLabel(str, Enum):
    SWING_V1 = "SWING_V1"
    TREND_V1 = "TREND_V1"


class DecisionStatus(str, Enum):
    LEADER = "LEADER"
    WATCH = "WATCH"
    NO_ENTRY = "NO_ENTRY"
    EXIT_CANDIDATE = "EXIT_CANDIDATE"


class SourceProvenance(TrackingModel):
    report_path: NonEmptyStr
    report_sha256: Sha256
    source_urls: tuple[HttpUrl, ...]
    evidence_refs: tuple[NonEmptyStr, ...]


class MarketState(TrackingModel):
    regime: MarketRegime
    summary: NonEmptyStr
    evidence_refs: tuple[NonEmptyStr, ...]


class MarketEvent(TrackingModel):
    event_id: NonEmptyStr
    event_type: EventType
    title: NonEmptyStr
    summary: NonEmptyStr
    evidence_refs: tuple[NonEmptyStr, ...]


class RelativeStrength(TrackingModel):
    rs_5d: RelativeStrengthScore | None
    rs_20d: RelativeStrengthScore | None
    rs_60d: RelativeStrengthScore | None


class High52Week(TrackingModel):
    state: High52WeekState
    distance_pct: NonNegativeDecimal | None

    @model_validator(mode="after")
    def validate_state(self) -> High52Week:
        if self.state is High52WeekState.UNKNOWN and self.distance_pct is not None:
            raise ValueError("UNKNOWN 52-week-high state cannot carry a distance")
        if self.state is High52WeekState.NEW_HIGH and self.distance_pct != Decimal("0"):
            raise ValueError("NEW_HIGH requires 52-week-high distance_pct=0")
        if self.state not in {High52WeekState.UNKNOWN, High52WeekState.NEW_HIGH} and self.distance_pct is None:
            raise ValueError("known 52-week-high state requires distance_pct")
        return self


class Liquidity(TrackingModel):
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
    latest_trading_value: NonNegativeDecimal
    average_20d_trading_value: NonNegativeDecimal
    latest_volume: NonNegativeDecimal


class MarketFlow(TrackingModel):
    unit: NonEmptyStr
    foreign_net: FiniteDecimal | None
    institutional_net: FiniteDecimal | None
    retail_net: FiniteDecimal | None


class Momentum(TrackingModel):
    state: MomentumState
    score: Score | None


class Peak(TrackingModel):
    state: PeakState
    score: Score | None


class LeadershipSecurity(TrackingModel):
    symbol: NonEmptyStr
    name: NonEmptyStr
    relative_strength: RelativeStrength
    high_52_week: High52Week
    liquidity: Liquidity
    flow: MarketFlow | None
    momentum: Momentum
    peak: Peak
    strategies: tuple[StrategyLabel, ...]
    decision_status: DecisionStatus
    evidence_refs: tuple[NonEmptyStr, ...]

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("symbol must not be blank")
        return normalized

    @model_validator(mode="after")
    def validate_strategy_decision(self) -> LeadershipSecurity:
        if len(set(self.strategies)) != len(self.strategies):
            raise ValueError("strategy labels must be unique")
        if self.decision_status is not DecisionStatus.NO_ENTRY and not self.strategies:
            raise ValueError("this decision status requires at least one strategy label")
        return self


_STAGE_CONTRACT = {
    (Market.KR, 1): (MarketStage.PRIOR_CLOSE_CONTEXT, ConfirmationState.CONFIRMED),
    (Market.KR, 7): (MarketStage.PREOPEN_OBSERVATIONS, ConfirmationState.PROVISIONAL),
    (Market.KR, 13): (MarketStage.INTRADAY, ConfirmationState.PROVISIONAL),
    (Market.KR, 19): (MarketStage.CLOSE, ConfirmationState.CONFIRMED),
    (Market.US, 1): (MarketStage.INTRADAY, ConfirmationState.PROVISIONAL),
    (Market.US, 7): (MarketStage.CLOSE, ConfirmationState.CONFIRMED),
    (Market.US, 13): (MarketStage.POST_CLOSE_EVENTS, ConfirmationState.CONFIRMED),
    (Market.US, 19): (MarketStage.PREMARKET_OBSERVATIONS, ConfirmationState.PROVISIONAL),
}


class MarketTrackingSnapshot(TrackingModel):
    schema_version: Literal["market_tracking_v1"]
    run_id: NonEmptyStr
    revision: Annotated[int, Field(ge=0)]
    market: Market
    kst_slot: Literal[1, 7, 13, 19]
    stage: MarketStage
    confirmation_state: ConfirmationState
    as_of: AwareDatetime
    observed_at: AwareDatetime
    available_at: AwareDatetime
    ingested_at: AwareDatetime
    source: SourceProvenance
    quality: DataQualityStatus
    quality_reasons: tuple[NonEmptyStr, ...]
    core_evidence_usable: bool
    leader_universe_complete: bool
    market_state: MarketState
    events: tuple[MarketEvent, ...]
    leaders: tuple[LeadershipSecurity, ...]

    @model_validator(mode="after")
    def validate_snapshot(self) -> MarketTrackingSnapshot:
        expected = _STAGE_CONTRACT[(self.market, self.kst_slot)]
        if (self.stage, self.confirmation_state) != expected:
            raise ValueError("market, KST slot, stage, and confirmation state are inconsistent")
        if not self.observed_at <= self.available_at <= self.as_of <= self.ingested_at:
            raise ValueError(
                "timestamps must satisfy observed_at <= available_at <= as_of <= ingested_at"
            )
        if self.available_at > datetime.now(timezone.utc):
            raise ValueError("available_at cannot be in the future")
        if self.ingested_at > datetime.now(timezone.utc):
            raise ValueError("as_of and ingested_at cannot be in the future")
        if self.quality is DataQualityStatus.FRESH and not self.core_evidence_usable:
            raise ValueError("FRESH quality requires usable core evidence")
        if self.quality in {
            DataQualityStatus.STALE,
            DataQualityStatus.UNAVAILABLE,
            DataQualityStatus.CONFLICT,
        } and self.core_evidence_usable:
            raise ValueError("this quality state cannot mark core evidence usable")
        if self.quality is not DataQualityStatus.FRESH and not self.quality_reasons:
            raise ValueError("non-FRESH quality requires at least one reason")
        if self.leader_universe_complete and not self.core_evidence_usable:
            raise ValueError("a complete leader universe requires usable core evidence")
        symbols = [leader.symbol for leader in self.leaders]
        if len(set(symbols)) != len(symbols):
            raise ValueError("leader symbols must be unique within a run")
        for leader in self.leaders:
            if self.market is Market.KR and not (
                len(leader.symbol) == 6 and leader.symbol.isdigit()
            ):
                raise ValueError("KR symbols must contain exactly six digits")
            if self.market is Market.US and not all(
                character.isalnum() or character in {".", "-"}
                for character in leader.symbol
            ):
                raise ValueError("US symbols contain unsupported characters")
        return self


def canonical_snapshot_json(snapshot: MarketTrackingSnapshot) -> str:
    """Return a byte-stable JSON representation of the complete validated input."""
    return _canonical_json(snapshot.model_dump(mode="python"))


def _canonical_json(payload: object) -> str:
    normalized = _normalize_canonical_value(payload)
    return json.dumps(
        normalized, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )


def _normalize_canonical_value(value: object) -> object:
    if isinstance(value, datetime):
        return _utc_iso(value)
    if isinstance(value, Decimal):
        normalized = value.normalize()
        return "0" if normalized == 0 else format(normalized, "f")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _normalize_canonical_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_normalize_canonical_value(item) for item in value]
    if value is None or isinstance(value, (str, int, bool)):
        return value
    return str(value)


def _identity_json(snapshot: MarketTrackingSnapshot) -> str:
    payload = snapshot.model_dump(mode="python")
    payload.pop("ingested_at")
    return _canonical_json(payload)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _source_record_id(
    snapshot: MarketTrackingSnapshot, kind: str, symbol: str | None = None
) -> str:
    natural_key = _canonical_json(
        [snapshot.market.value, snapshot.run_id, kind, symbol or "MARKET"]
    )
    return f"leadership:{_sha256(natural_key)}"


class LeadershipChange(str, Enum):
    NEW = "NEW"
    MAINTAINED = "MAINTAINED"
    EXITED = "EXITED"
    DATA_MISSING = "DATA_MISSING"


@dataclass(frozen=True)
class LeadershipChangeItem:
    symbol: str
    state: LeadershipChange


@dataclass(frozen=True)
class LeadershipIngestResult:
    snapshot_id: str
    report_id: str
    idempotent: bool
    changes: tuple[LeadershipChangeItem, ...]
    rendered_markdown: str


@dataclass(frozen=True)
class StoredLeadershipRun:
    snapshot: MarketTrackingSnapshot
    snapshot_id: str
    report_id: str
    changes: tuple[LeadershipChangeItem, ...]
    rendered_markdown: str


class LeadershipConflictError(RuntimeError):
    """Raised when immutable source identity is reused with conflicting content."""


def render_leadership_report(
    snapshot: MarketTrackingSnapshot,
    changes: tuple[LeadershipChangeItem, ...],
) -> str:
    """Render deterministic, generic Markdown from one validated market run."""
    lines = [
        f"# {snapshot.market.value} Leadership Evidence",
        "",
        "## Run Context",
        f"- KST slot: {snapshot.kst_slot:02d}",
        f"- Market stage: {snapshot.stage.value}",
        f"- Confirmation: {snapshot.confirmation_state.value}",
        f"- As of: {_utc_iso(snapshot.as_of)}",
        f"- Policy disposition: {POLICY_DISPOSITION}",
        "",
        "## Data Quality",
        f"- Quality: {snapshot.quality.value}",
        f"- Core evidence usable: {'YES' if snapshot.core_evidence_usable else 'NO'}",
    ]
    for reason in snapshot.quality_reasons:
        lines.append(f"- Warning: {reason}")
    if not snapshot.core_evidence_usable:
        lines.append(
            "- FAIL-CLOSED: core evidence is unusable; actionable price levels are suppressed."
        )
    lines.extend(
        (
            "",
            "## Market State",
            f"- Regime: {snapshot.market_state.regime.value}",
            f"- Summary: {snapshot.market_state.summary}",
            "",
            "## Tracked Events",
        )
    )
    if snapshot.events:
        lines.extend(
            f"- {event.event_type.value}: {event.title} — {event.summary}"
            for event in sorted(snapshot.events, key=lambda item: item.event_id)
        )
    else:
        lines.append("- None")
    lines.extend(("", "## Leadership Changes"))
    if changes:
        lines.extend(f"- {item.symbol}: {item.state.value}" for item in changes)
    else:
        lines.append("- None")
    lines.extend(("", "## Security Evidence"))
    if snapshot.leaders and snapshot.core_evidence_usable:
        for leader in sorted(snapshot.leaders, key=lambda item: item.symbol):
            strategies = ", ".join(item.value for item in leader.strategies) or "NONE"
            rs = leader.relative_strength
            high = leader.high_52_week
            high_distance = (
                "unknown" if high.distance_pct is None else f"{high.distance_pct}%"
            )
            lines.extend(
                (
                    f"### {leader.symbol} — {leader.name}",
                    f"- Decision: {leader.decision_status.value}",
                    f"- Strategies: {strategies}",
                    f"- RS 5D: {rs.rs_5d if rs.rs_5d is not None else 'N/A'}",
                    f"- RS 20D: {rs.rs_20d if rs.rs_20d is not None else 'N/A'}",
                    f"- RS 60D: {rs.rs_60d if rs.rs_60d is not None else 'N/A'}",
                    f"- 52-week high: {high.state.value} ({high_distance})",
                    f"- Liquidity latest: {leader.liquidity.latest_trading_value} "
                    f"{leader.liquidity.currency}",
                    f"- Liquidity 20D average: "
                    f"{leader.liquidity.average_20d_trading_value} "
                    f"{leader.liquidity.currency}",
                    f"- Latest volume: {leader.liquidity.latest_volume}",
                    f"- Momentum: {leader.momentum.state.value} "
                    f"({leader.momentum.score if leader.momentum.score is not None else 'N/A'})",
                    f"- Peak: {leader.peak.state.value} "
                    f"({leader.peak.score if leader.peak.score is not None else 'N/A'})",
                )
            )
            if leader.flow is not None:
                lines.append(
                    "- Flow: "
                    f"foreign={leader.flow.foreign_net if leader.flow.foreign_net is not None else 'N/A'}, "
                    f"institutional={leader.flow.institutional_net if leader.flow.institutional_net is not None else 'N/A'}, "
                    f"retail={leader.flow.retail_net if leader.flow.retail_net is not None else 'N/A'} "
                    f"{leader.flow.unit}"
                )
    else:
        lines.append("- No usable current leadership rows")
    return "\n".join(lines) + "\n"


class LeadershipRepository:
    """Append and read REPORT_ONLY leadership evidence in research.sqlite."""

    def __init__(self, connection: sqlite3.Connection):
        self._connection = connection

    def ingest(self, snapshot: MarketTrackingSnapshot) -> LeadershipIngestResult:
        identity_hash = _sha256(_identity_json(snapshot))
        snapshot_id = str(uuid5(NAMESPACE_URL, f"{SCHEMA_VERSION}:{identity_hash}"))
        market_source_id = _source_record_id(snapshot, MARKET_OBSERVATION_KIND)

        with transaction(self._connection):
            existing_snapshot = self._connection.execute(
                "SELECT snapshot_id FROM market_snapshots WHERE snapshot_id = ?",
                (snapshot_id,),
            ).fetchone()
            if existing_snapshot is not None:
                stored = self._read(snapshot_id)
                return LeadershipIngestResult(
                    stored.snapshot_id,
                    stored.report_id,
                    True,
                    stored.changes,
                    stored.rendered_markdown,
                )

            existing_revision = self._connection.execute(
                "SELECT snapshot_id FROM observations "
                "WHERE provider = ? AND source_record_id = ? AND revision = ?",
                (PROVIDER, market_source_id, snapshot.revision),
            ).fetchone()
            if existing_revision is not None:
                raise LeadershipConflictError(
                    "same run and revision already contain different canonical content"
                )

            max_revision_row = self._connection.execute(
                "SELECT MAX(revision) FROM observations "
                "WHERE provider = ? AND source_record_id = ?",
                (PROVIDER, market_source_id),
            ).fetchone()
            max_revision = max_revision_row[0]
            if max_revision is None and snapshot.revision != 0:
                raise LeadershipConflictError("the first revision for a run must be zero")
            if max_revision is not None and snapshot.revision <= max_revision:
                raise LeadershipConflictError("a correction must use a higher revision")

            prior = self._prior_run(snapshot, snapshot_id)
            changes = self._compare(snapshot, prior.snapshot if prior is not None else None)
            rendered = render_leadership_report(snapshot, changes)
            created_at = _utc_iso(snapshot.ingested_at)

            self._connection.execute(
                "INSERT INTO market_snapshots "
                "(snapshot_id, market, as_of_date, content_hash, quality, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    snapshot_id,
                    snapshot.market.value,
                    _utc_iso(snapshot.as_of),
                    identity_hash,
                    snapshot.quality.value,
                    created_at,
                ),
            )
            market_payload = {
                "schema_version": SCHEMA_VERSION,
                "policy_disposition": POLICY_DISPOSITION,
                "record_type": MARKET_OBSERVATION_KIND,
                "snapshot": snapshot.model_dump(mode="json"),
                "prior_snapshot_id": prior.snapshot_id if prior is not None else None,
                "changes": [
                    {"symbol": item.symbol, "state": item.state.value} for item in changes
                ],
            }
            self._insert_observation(
                snapshot,
                snapshot_id,
                MARKET_OBSERVATION_KIND,
                market_source_id,
                None,
                market_payload,
            )
            for leader in snapshot.leaders:
                self._insert_observation(
                    snapshot,
                    snapshot_id,
                    SECURITY_OBSERVATION_KIND,
                    _source_record_id(snapshot, SECURITY_OBSERVATION_KIND, leader.symbol),
                    leader.symbol,
                    {
                        "schema_version": SCHEMA_VERSION,
                        "policy_disposition": POLICY_DISPOSITION,
                        "record_type": SECURITY_OBSERVATION_KIND,
                        "run_id": snapshot.run_id,
                        "quality": snapshot.quality.value,
                        "quality_reasons": list(snapshot.quality_reasons),
                        "core_evidence_usable": snapshot.core_evidence_usable,
                        "leader_universe_complete": snapshot.leader_universe_complete,
                        "leader": leader.model_dump(mode="json"),
                    },
                )

            report_id = str(uuid5(NAMESPACE_URL, f"{snapshot_id}:{REPORT_KIND}"))
            self._connection.execute(
                "INSERT INTO reports "
                "(report_id, snapshot_id, report_kind, content, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (report_id, snapshot_id, REPORT_KIND, rendered, created_at),
            )
            return LeadershipIngestResult(
                snapshot_id, report_id, False, changes, rendered
            )

    def read(self, snapshot_id: str) -> StoredLeadershipRun:
        return self._read(snapshot_id)

    def _read(self, snapshot_id: str) -> StoredLeadershipRun:
        row = self._connection.execute(
            "SELECT o.payload_json, r.report_id, r.content "
            "FROM observations AS o JOIN reports AS r USING (snapshot_id) "
            "WHERE o.snapshot_id = ? AND o.observation_kind = ? "
            "AND o.provider = ? AND r.report_kind = ?",
            (snapshot_id, MARKET_OBSERVATION_KIND, PROVIDER, REPORT_KIND),
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown leadership snapshot: {snapshot_id}")
        payload = json.loads(row[0])
        snapshot = MarketTrackingSnapshot.model_validate_json(
            _canonical_json(payload["snapshot"])
        )
        changes = tuple(
            LeadershipChangeItem(item["symbol"], LeadershipChange(item["state"]))
            for item in payload["changes"]
        )
        return StoredLeadershipRun(snapshot, snapshot_id, row[1], changes, row[2])

    def _insert_observation(
        self,
        snapshot: MarketTrackingSnapshot,
        snapshot_id: str,
        kind: str,
        source_record_id: str,
        provider_symbol: str | None,
        payload: object,
    ) -> None:
        payload_json = _canonical_json(payload)
        observation_id = str(
            uuid5(
                NAMESPACE_URL,
                _canonical_json(
                    [
                        PROVIDER,
                        source_record_id,
                        snapshot.revision,
                        _sha256(payload_json),
                    ]
                ),
            )
        )
        self._connection.execute(
            "INSERT INTO observations "
            "(observation_id, snapshot_id, security_id, observation_kind, provider, "
            "provider_symbol, source_record_id, revision, observed_at, available_at, "
            "ingested_at, payload_json, created_at) "
            "VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                observation_id,
                snapshot_id,
                kind,
                PROVIDER,
                provider_symbol,
                source_record_id,
                snapshot.revision,
                _utc_iso(snapshot.observed_at),
                _utc_iso(snapshot.available_at),
                _utc_iso(snapshot.ingested_at),
                payload_json,
                _utc_iso(snapshot.ingested_at),
            ),
        )

    def _prior_run(
        self, snapshot: MarketTrackingSnapshot, snapshot_id: str
    ) -> StoredLeadershipRun | None:
        current_key = (
            snapshot.available_at.astimezone(timezone.utc),
            snapshot.ingested_at.astimezone(timezone.utc),
            snapshot_id,
        )
        candidates_by_run: dict[
            str, tuple[int, tuple[datetime, datetime, str], str]
        ] = {}
        for candidate_id, payload_json in self._connection.execute(
            "SELECT o.snapshot_id, o.payload_json FROM observations AS o "
            "JOIN market_snapshots AS s USING (snapshot_id) "
            "WHERE s.market = ? AND o.observation_kind = ? AND o.provider = ?",
            (snapshot.market.value, MARKET_OBSERVATION_KIND, PROVIDER),
        ):
            payload = json.loads(payload_json)
            candidate = MarketTrackingSnapshot.model_validate_json(
                _canonical_json(payload["snapshot"])
            )
            if candidate.run_id == snapshot.run_id:
                continue
            key = (
                candidate.available_at.astimezone(timezone.utc),
                candidate.ingested_at.astimezone(timezone.utc),
                candidate_id,
            )
            if key < current_key:
                existing = candidates_by_run.get(candidate.run_id)
                if existing is None or candidate.revision > existing[0]:
                    candidates_by_run[candidate.run_id] = (
                        candidate.revision,
                        key,
                        candidate_id,
                    )
        if not candidates_by_run:
            return None
        selected = max(candidates_by_run.values(), key=lambda item: item[1])
        return self._read(selected[2])

    @staticmethod
    def _compare(
        current: MarketTrackingSnapshot,
        prior: MarketTrackingSnapshot | None,
    ) -> tuple[LeadershipChangeItem, ...]:
        current_symbols = {leader.symbol for leader in current.leaders}
        prior_symbols = (
            set()
            if prior is None or not prior.core_evidence_usable
            else {leader.symbol for leader in prior.leaders}
        )
        current_state_unusable = not current.core_evidence_usable
        changes = [
            LeadershipChangeItem(
                symbol,
                LeadershipChange.DATA_MISSING
                if current_state_unusable
                else (
                    LeadershipChange.MAINTAINED
                    if symbol in prior_symbols
                    else LeadershipChange.NEW
                ),
            )
            for symbol in current_symbols
        ]
        absence_state = (
            LeadershipChange.EXITED
            if current.core_evidence_usable and current.leader_universe_complete
            else LeadershipChange.DATA_MISSING
        )
        changes.extend(
            LeadershipChangeItem(symbol, absence_state)
            for symbol in prior_symbols - current_symbols
        )
        return tuple(sorted(changes, key=lambda item: item.symbol))
