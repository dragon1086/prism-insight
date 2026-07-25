"""In-memory experiment provenance and sealed out-of-sample exposure registry."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from enum import Enum
from types import MappingProxyType
from typing import Mapping
from uuid import UUID

from prism_core.strategies.contracts import StrategyId


_CODE_SHA = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")


class WindowKind(str, Enum):
    WALK_FORWARD = "WALK_FORWARD"
    SEALED_OOS = "SEALED_OOS"


class OOSExposure(str, Enum):
    FRESH = "FRESH"
    EXPOSED = "EXPOSED"


@dataclass(frozen=True)
class EvaluationWindow:
    window_id: str
    train_start: datetime
    train_end: datetime
    evaluation_start: datetime
    evaluation_end: datetime
    kind: WindowKind

    def __post_init__(self) -> None:
        if not isinstance(self.window_id, str) or not self.window_id.strip():
            raise ValueError("window_id must be a non-empty string")
        for label, value in (
            ("train_start", self.train_start),
            ("train_end", self.train_end),
            ("evaluation_start", self.evaluation_start),
            ("evaluation_end", self.evaluation_end),
        ):
            _require_aware(value, label)
        if not (
            self.train_start
            < self.train_end
            <= self.evaluation_start
            < self.evaluation_end
        ):
            raise ValueError(
                "window must have chronological train then evaluation boundaries"
            )


@dataclass(frozen=True)
class ExperimentSpec:
    experiment_id: UUID
    strategy_id: StrategyId
    config: Mapping[str, object]
    data_snapshot_ids: tuple[UUID, ...]
    code_sha: str
    windows: tuple[EvaluationWindow, ...]
    caveats: tuple[str, ...]
    _normalized_config: object = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.strategy_id, StrategyId):
            raise TypeError("strategy_id must be StrategyId")
        if not self.data_snapshot_ids or len(set(self.data_snapshot_ids)) != len(
            self.data_snapshot_ids
        ):
            raise ValueError("data_snapshot_ids must be non-empty and unique")
        if not _CODE_SHA.fullmatch(self.code_sha):
            raise ValueError("code_sha must be a lowercase 40- or 64-character hex SHA")
        if not self.windows or len({item.window_id for item in self.windows}) != len(
            self.windows
        ):
            raise ValueError("windows must be non-empty with unique IDs")
        ordered = sorted(self.windows, key=lambda item: item.evaluation_start)
        for previous, current in zip(ordered, ordered[1:]):
            if current.evaluation_start < previous.evaluation_end:
                raise ValueError("evaluation windows must not overlap")
        if not self.caveats or any(
            not isinstance(item, str) or not item.strip() for item in self.caveats
        ):
            raise ValueError("experiment caveats must be explicit and non-empty")
        normalized = _normalize_config(self.config)
        object.__setattr__(self, "config", MappingProxyType(dict(self.config)))
        object.__setattr__(self, "_normalized_config", normalized)

    @property
    def config_hash(self) -> str:
        encoded = json.dumps(
            self._normalized_config,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode()
        return "sha256:" + hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ExperimentRecord:
    experiment_id: UUID
    strategy_id: StrategyId
    config_hash: str
    data_snapshot_ids: tuple[UUID, ...]
    code_sha: str
    windows: tuple[EvaluationWindow, ...]
    window_exposures: tuple[tuple[str, OOSExposure], ...]
    caveats: tuple[str, ...]


_ExposureKey = tuple[StrategyId, datetime, datetime]


class ExperimentRegistry:
    """Tracks when evaluation data stops being sealed within one registry store."""

    def __init__(self) -> None:
        self._records: dict[UUID, ExperimentRecord] = {}
        self._exposed: set[_ExposureKey] = set()

    def register(self, spec: ExperimentSpec) -> ExperimentRecord:
        if not isinstance(spec, ExperimentSpec):
            raise TypeError("spec must be ExperimentSpec")
        if spec.experiment_id in self._records:
            raise ValueError("experiment_id is already registered")
        exposures = tuple(
            (
                window.window_id,
                OOSExposure.EXPOSED
                if self._overlaps_exposed(spec, window)
                else OOSExposure.FRESH,
            )
            for window in spec.windows
        )
        record = ExperimentRecord(
            experiment_id=spec.experiment_id,
            strategy_id=spec.strategy_id,
            config_hash=spec.config_hash,
            data_snapshot_ids=spec.data_snapshot_ids,
            code_sha=spec.code_sha,
            windows=spec.windows,
            window_exposures=exposures,
            caveats=spec.caveats,
        )
        self._records[spec.experiment_id] = record
        return record

    def mark_observed(self, experiment_id: UUID, window_id: str) -> ExperimentRecord:
        record = self._record(experiment_id)
        window = _window(record, window_id)
        self._exposed.add(self._key(record, window))
        for current_id, current in tuple(self._records.items()):
            updated = tuple(
                (
                    candidate.window_id,
                    OOSExposure.EXPOSED
                    if self._record_window_overlaps(current, candidate, record, window)
                    else exposure,
                )
                for candidate, (_, exposure) in zip(
                    current.windows, current.window_exposures
                )
            )
            self._records[current_id] = replace(current, window_exposures=updated)
        return self._records[experiment_id]

    def assert_fresh(self, experiment_id: UUID, window_id: str) -> None:
        record = self._record(experiment_id)
        exposures = dict(record.window_exposures)
        if window_id not in exposures:
            raise ValueError("window_id is not registered for the experiment")
        if exposures[window_id] is OOSExposure.EXPOSED:
            raise ValueError("OOS window is already exposed and cannot be relabelled fresh")

    def _record(self, experiment_id: UUID) -> ExperimentRecord:
        try:
            return self._records[experiment_id]
        except KeyError as exc:
            raise ValueError("experiment_id is not registered") from exc

    def _overlaps_exposed(
        self, spec: ExperimentSpec, window: EvaluationWindow
    ) -> bool:
        return any(
            strategy_id is spec.strategy_id
            and window.evaluation_start < exposed_end
            and exposed_start < window.evaluation_end
            for strategy_id, exposed_start, exposed_end in self._exposed
        )

    @staticmethod
    def _key(
        record: ExperimentRecord, window: EvaluationWindow
    ) -> _ExposureKey:
        return (
            record.strategy_id,
            window.evaluation_start,
            window.evaluation_end,
        )

    @staticmethod
    def _record_window_overlaps(
        candidate_record: ExperimentRecord,
        candidate: EvaluationWindow,
        exposed_record: ExperimentRecord,
        exposed: EvaluationWindow,
    ) -> bool:
        return (
            candidate_record.strategy_id is exposed_record.strategy_id
            and candidate.evaluation_start < exposed.evaluation_end
            and exposed.evaluation_start < candidate.evaluation_end
        )


def _window(record: ExperimentRecord, window_id: str) -> EvaluationWindow:
    matches = tuple(window for window in record.windows if window.window_id == window_id)
    if len(matches) != 1:
        raise ValueError("window_id is not registered for the experiment")
    return matches[0]


def _normalize_config(value: object) -> object:
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) or not key for key in value):
            raise ValueError("config keys must be non-empty strings")
        return {key: _normalize_config(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_normalize_config(item) for item in value]
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("config Decimals must be finite")
        return {"decimal": format(value.normalize(), "f")}
    if isinstance(value, Enum):
        return {"enum": f"{type(value).__name__}.{value.name}"}
    if isinstance(value, UUID):
        return {"uuid": str(value)}
    if isinstance(value, datetime):
        _require_aware(value, "config datetime")
        return {"datetime": value.isoformat()}
    if value is None or isinstance(value, (str, bool, int)):
        return value
    raise TypeError(
        "config values must be deterministic mappings, sequences, Decimal, Enum, "
        "UUID, aware datetime, string, bool, int, or None"
    )


def _require_aware(value: datetime, label: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
