"""Uncapped KR daily-close candidate composition with isolated read-only analysis."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from prism_app.oauth_llm import CHATGPT_OAUTH_DEFAULT_MODEL
from prism_app.stockeasy_snapshot_import import (
    StockEasyImportOutcome,
    StockEasyImportResult,
    load_stockeasy_import,
    stockeasy_capability_projection,
)
from prism_core.candidates import (
    AnalysisCohort,
    CandidateReconciliation,
    CandidateSourceState,
    CohortDisposition,
    ObservationUniverse,
    ReconciledCandidate,
    select_analysis_cohort,
)
from prism_core.data.contracts import SecurityId
from prism_core.strategies.contracts import StrategyId


_KR_START = "<!-- PRISM_KR_DAILY_COMPOSITION_START -->"
_KR_END = "<!-- PRISM_KR_DAILY_COMPOSITION_END -->"


def _render_strategy_result(
    strategy_id: str, payload: Mapping[str, object]
) -> str:
    quant_value = payload.get("quant_score")
    quant_score = quant_value if isinstance(quant_value, Mapping) else {}
    reasons = payload.get("scenario_reasons", [])
    hard_vetoes = payload.get("hard_vetoes", [])
    return (
        "- 전략 결과 "
        f"`{strategy_id}`: 상태 `{payload.get('scenario_state')}`, "
        f"결정 `{payload.get('decision')}`, "
        f"점수 `{quant_score.get('total_score')}`, "
        "점수/임계값 버전 "
        f"`{quant_score.get('score_version')}` / "
        f"`{quant_score.get('threshold_version')}`, "
        "결정론적 진입 거부 "
        + json.dumps(
            hard_vetoes,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + ", "
        "사유 "
        + json.dumps(reasons, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )


class MarketContextComposer(Protocol):
    async def compose(self) -> Any: ...


class CandidateSource(Protocol):
    def discover(self, *, trigger_time: str, market_context: Any) -> Any: ...


class _RecordingAgentNewsProvider:
    """Retain the typed fetch result for sanitized call-evidence projection."""

    def __init__(self, provider: Any) -> None:
        self._provider = provider
        self.last_result: Any | None = None

    async def fetch_result(self, board: Any) -> Any:
        result = await self._provider.fetch_result(board)
        self.last_result = result
        return result


@dataclass(frozen=True)
class CandidateAnalysisResult:
    security_id: SecurityId
    provider_symbol: str
    job_key: str
    status: str
    strategy_ids: tuple[StrategyId, ...]
    report_markdown: str
    data_snapshot_id: str | None = None
    strategy_results: Mapping[str, Mapping[str, object]] | None = None
    issues: tuple[str, ...] = ()
    call_evidence: Mapping[str, object] | None = None
    fresh_invocation: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.security_id, SecurityId):
            raise TypeError("security_id must be SecurityId")
        if not self.provider_symbol or not self.job_key or not self.status:
            raise ValueError("candidate analysis identity and status are required")
        if len(set(self.strategy_ids)) != len(self.strategy_ids):
            raise ValueError("strategy identities must be unique")
        if self.strategy_results is not None:
            expected = {item.value for item in self.strategy_ids}
            if set(self.strategy_results) != expected:
                raise ValueError("strategy result identities must match strategy_ids")
            if not self.data_snapshot_id:
                raise ValueError("strategy results require one data snapshot identity")
            if any(
                payload.get("data_snapshot_id") != self.data_snapshot_id
                for payload in self.strategy_results.values()
            ):
                raise ValueError("strategy results must use the same data snapshot")


@dataclass(frozen=True)
class CandidateAnalysisFailure:
    security_id: SecurityId
    provider_symbol: str
    failure_type: str


@dataclass(frozen=True)
class KRDailyCounts:
    raw_assertions: int
    raw_assertions_by_source: Mapping[str, int]
    raw_assertions_by_channel: Mapping[str, int]
    raw_assertions_by_trigger: Mapping[str, int]
    unique_identities: int
    excluded_identities: int
    invalid_records: int
    data_unavailable: int
    analyzed_swing: int
    analyzed_trend: int
    analysis_failures: int
    truncated: int = 0


@dataclass(frozen=True)
class KRDailyProductResult:
    market_context: Any
    reconciliation: CandidateReconciliation
    observation_universe: ObservationUniverse
    analysis_cohort: AnalysisCohort
    supplement_outcome: StockEasyImportOutcome
    supplement_error_code: str | None
    analyses: tuple[CandidateAnalysisResult, ...]
    failures: tuple[CandidateAnalysisFailure, ...]
    counts: KRDailyCounts
    supplement_capability: Mapping[str, object] | None = None


class AnalysisCollection(Protocol):
    @property
    def analyses(self) -> tuple[CandidateAnalysisResult, ...]: ...


def candidate_readback_status(
    persisted_status: str,
    strategy_results: Mapping[str, Mapping[str, object]],
) -> str:
    """Preserve persisted invalid/policy states instead of hiding them as exceptions."""

    states = {str(item.get("scenario_state")) for item in strategy_results.values()}
    complete = all(
        item.get("scenario_complete") is True for item in strategy_results.values()
    )
    if complete and states <= {"WATCH", "NO_ENTRY", "ENTRY_CANDIDATE"}:
        return persisted_status
    if "INVALID_PROPOSAL" in states or "ANALYSIS_INCOMPLETE" in states:
        return "ANALYSIS_INCOMPLETE_READBACK_VERIFIED"
    if "POLICY_REJECTED" in states:
        return "POLICY_REJECTED_READBACK_VERIFIED"
    return "ANALYSIS_INCOMPLETE_READBACK_VERIFIED"


def daily_product_status(result: Any) -> str:
    """Keep genuine completion, report-only, replay, and failure states distinct."""

    if getattr(result, "failures", ()):
        return "ANALYSIS_INCOMPLETE"
    analyses = tuple(result.analyses)
    if analyses and all(not item.fresh_invocation for item in analyses):
        return "IDEMPOTENT_REPLAY"
    if genuine_completed_count(result):
        return "COMPLETED"
    if analyses and all(
        item.status == "REPORT_ONLY_READBACK_VERIFIED" for item in analyses
    ):
        return "REPORT_ONLY"
    if analyses and all(
        item.status in {
            "PERSISTED_READBACK_VERIFIED",
            "POLICY_REJECTED_READBACK_VERIFIED",
            "REPORT_ONLY_READBACK_VERIFIED",
        }
        for item in analyses
    ) and any(
        item.status == "POLICY_REJECTED_READBACK_VERIFIED" for item in analyses
    ):
        return "COMPLETED_WITH_POLICY_REJECTIONS"
    return "ANALYSIS_INCOMPLETE"


def daily_product_exit_code(status: str) -> int:
    """Return success for definitive read-only outputs, not only actionable days."""

    return 0 if status in {
        "COMPLETED",
        "COMPLETED_WITH_POLICY_REJECTIONS",
        "REPORT_ONLY",
        "IDEMPOTENT_REPLAY",
    } else 2


class KRDailyProduct:
    """Share one market context, retain every identity, then analyze the cohort.

    Candidate work is deliberately sequential.  This is an observable bounded
    queue of size one, preserves the existing OAuth rate boundary, and cannot
    truncate the reconciled set.
    """

    def __init__(
        self,
        *,
        context_composer: MarketContextComposer,
        candidate_source: CandidateSource,
        supplement_composer: Any,
        candidate_analyzer: Callable[[ReconciledCandidate], Awaitable[CandidateAnalysisResult]],
    ) -> None:
        self._context_composer = context_composer
        self._candidate_source = candidate_source
        self._supplement_composer = supplement_composer
        self._candidate_analyzer = candidate_analyzer

    async def run(
        self,
        *,
        trigger_time: str,
        supplement_import: StockEasyImportResult,
        supplement_snapshot_argument_supplied: bool = False,
        authoritative_values: Mapping[str, object] | None = None,
    ) -> KRDailyProductResult:
        context = await self._context_composer.compose()
        selection = self._candidate_source.discover(
            trigger_time=trigger_time,
            market_context=context,
        )
        reconciliation = selection.reconciliation
        snapshots = tuple(selection.snapshots)
        effective_supplement_import = supplement_import
        if supplement_import.outcome is StockEasyImportOutcome.IMPORTED:
            try:
                composition = self._supplement_composer.compose(
                    core_candidates=snapshots,
                    supplement=supplement_import.snapshot,
                    authoritative_values=authoritative_values or {},
                )
            except Exception:  # noqa: BLE001 - optional supplement must remain fail-soft
                effective_supplement_import = StockEasyImportResult(
                    outcome=StockEasyImportOutcome.REJECTED,
                    error_code="SUPPLEMENT_COMPOSITION_FAILED",
                    temporary_image_deleted=supplement_import.temporary_image_deleted,
                )
            else:
                reconciliation = composition.reconciliation
                snapshots = (*snapshots, *tuple(composition.supplemental_candidates))

        observation_universe = ObservationUniverse.from_reconciliation(reconciliation)
        analysis_cohort = select_analysis_cohort(
            observation_universe,
            core_state=_core_source_state(context),
            supplement_state=_supplement_source_state(
                effective_supplement_import,
                snapshot_argument_supplied=supplement_snapshot_argument_supplied,
            ),
        )

        analyses: list[CandidateAnalysisResult] = []
        failures: list[CandidateAnalysisFailure] = []
        for cohort_member in analysis_cohort.analysis_members:
            candidate = cohort_member.candidate
            symbol = candidate.provider_symbols[0]
            try:
                analysis = await self._candidate_analyzer(candidate)
                if analysis.security_id != candidate.security_id:
                    raise ValueError("candidate analyzer returned a mismatched stable identity")
            except Exception as exc:  # noqa: BLE001 - isolate one candidate and sanitize
                failures.append(
                    CandidateAnalysisFailure(
                        security_id=candidate.security_id,
                        provider_symbol=symbol,
                        failure_type=type(exc).__name__,
                    )
                )
                continue
            analyses.append(analysis)

        channel_counts = Counter(item.channel.value for item in snapshots)
        source_counts = Counter(item.provider for item in snapshots)
        trigger_counts = Counter(
            trigger for item in snapshots for trigger in item.trigger_ids
        )
        data_unavailable = sum(
            item.exclusion_code.value == "DATA_UNAVAILABLE"
            for item in reconciliation.excluded
        )
        base_invalid = selection.reconciliation.invalid_record_count
        supplement_invalid = (
            reconciliation.invalid_record_count
            if effective_supplement_import.outcome is StockEasyImportOutcome.IMPORTED
            else 0
        )
        total_invalid = base_invalid + supplement_invalid
        if total_invalid:
            source_counts["INVALID_RECORD"] += total_invalid
            trigger_counts["INVALID_RECORD"] += total_invalid
        return KRDailyProductResult(
            market_context=context,
            reconciliation=reconciliation,
            observation_universe=observation_universe,
            analysis_cohort=analysis_cohort,
            supplement_outcome=effective_supplement_import.outcome,
            supplement_error_code=effective_supplement_import.error_code,
            analyses=tuple(analyses),
            failures=tuple(failures),
            counts=KRDailyCounts(
                raw_assertions=(
                    reconciliation.input_count + base_invalid
                    if effective_supplement_import.outcome is StockEasyImportOutcome.IMPORTED
                    else selection.reconciliation.input_count
                ),
                raw_assertions_by_source=dict(sorted(source_counts.items())),
                raw_assertions_by_channel=dict(sorted(channel_counts.items())),
                raw_assertions_by_trigger=dict(sorted(trigger_counts.items())),
                unique_identities=reconciliation.unique_identity_count,
                excluded_identities=reconciliation.excluded_identity_count,
                invalid_records=total_invalid,
                data_unavailable=data_unavailable,
                analyzed_swing=sum(
                    StrategyId.SWING_V1 in item.strategy_ids for item in analyses
                ),
                analyzed_trend=sum(
                    StrategyId.TREND_V1 in item.strategy_ids for item in analyses
                ),
                analysis_failures=len(failures),
                truncated=reconciliation.truncated_candidate_count,
            ),
            supplement_capability=stockeasy_capability_projection(
                effective_supplement_import,
                checked_at=context.timing.as_of,
                snapshot_argument_supplied=supplement_snapshot_argument_supplied,
            ),
        )


def _supplement_source_state(
    result: StockEasyImportResult,
    *,
    snapshot_argument_supplied: bool,
) -> CandidateSourceState:
    if result.outcome is StockEasyImportOutcome.IMPORTED:
        quality = getattr(getattr(result, "snapshot", None), "quality", None)
        try:
            return CandidateSourceState(quality.value)
        except (AttributeError, ValueError):
            return CandidateSourceState.CONFLICT
    if result.outcome is StockEasyImportOutcome.UNAVAILABLE:
        return (
            CandidateSourceState.UNAVAILABLE
            if snapshot_argument_supplied
            else CandidateSourceState.NOT_INGESTED
        )
    if result.error_code == "SUPPLEMENT_COMPOSITION_FAILED":
        return CandidateSourceState.UNAVAILABLE
    return CandidateSourceState.CONFLICT


def _core_source_state(context: Any) -> CandidateSourceState:
    quality = getattr(context, "quality", None)
    if quality is None:
        return CandidateSourceState.FRESH
    try:
        return CandidateSourceState(quality.value)
    except (AttributeError, ValueError):
        return CandidateSourceState.CONFLICT


def genuine_completed_count(result: AnalysisCollection) -> int:
    """Count only accepted readbacks with both strategy families completed."""

    required = {StrategyId.SWING_V1, StrategyId.TREND_V1}
    return sum(
        item.status == "PERSISTED_READBACK_VERIFIED"
        and item.fresh_invocation
        and set(item.strategy_ids) == required
        for item in result.analyses
    )


def candidate_analysis_state(analysis: CandidateAnalysisResult) -> str:
    """Summarize persisted per-strategy states without inferring from row presence."""

    if not analysis.strategy_results:
        return "COMPLETE" if analysis.strategy_ids else "ANALYSIS_INCOMPLETE"
    if all(
        payload.get("scenario_complete") is True
        for payload in analysis.strategy_results.values()
    ):
        return "COMPLETE"
    states = sorted(
        {
            str(payload.get("scenario_state"))
            for payload in analysis.strategy_results.values()
            if payload.get("scenario_state")
        }
    )
    return " / ".join(states) if states else "ANALYSIS_INCOMPLETE"


def render_daily_composition(
    existing_markdown: str,
    result: KRDailyProductResult,
) -> str:
    """Append the uncapped candidate funnel to the existing KR report surface."""

    counts = result.counts
    supplement_capability = getattr(result, "supplement_capability", None)
    lines = [
        "",
        _KR_START,
        "## KR 일일 종가 후보 분석",
        "",
        "> 읽기 전용 SHADOW 분석이며 계좌, 주문, 메시지 또는 일정 기능을 사용하지 않습니다.",
        "",
        "### 후보 퍼널",
        "",
        f"- 원천 주장: `{counts.raw_assertions}`",
        f"- 고유 종목: `{counts.unique_identities}`",
        f"- 제외/무효/데이터 불가: `{counts.excluded_identities}` / `{counts.invalid_records}` / `{counts.data_unavailable}`",
        f"- 분석 완료 SWING/TREND: `{counts.analyzed_swing}` / `{counts.analyzed_trend}`",
        f"- 후보 절단: `{counts.truncated}`",
        f"- 보조 리더십: `{result.supplement_outcome.value}`"
        + (
            f" (`{result.supplement_error_code}`)"
            if result.supplement_error_code is not None
            else ""
        ),
        "",
        *(
            _render_stockeasy_capability_lines(supplement_capability)
            if supplement_capability is not None
            else ()
        ),
        "### 원천/트리거별 주장",
        "",
    ]
    lines.extend(
        f"- 원천 `{name}`: `{count}`"
        for name, count in sorted(counts.raw_assertions_by_source.items())
    )
    lines.extend(
        f"- 채널 `{name}`: `{count}`"
        for name, count in sorted(counts.raw_assertions_by_channel.items())
    )
    lines.extend(
        f"- 트리거 `{name}`: `{count}`"
        for name, count in sorted(counts.raw_assertions_by_trigger.items())
    )
    by_identity = {item.security_id: item for item in result.reconciliation.included}
    lines.extend(("", "### 종목별 SWING/TREND", ""))
    for analysis in result.analyses:
        candidate = by_identity[analysis.security_id]
        channels = ", ".join(channel.value for channel in candidate.channels)
        sources = ", ".join(
            f"{item['provider']} (available_at={item['available_at']})"
            for item in _candidate_sources(candidate)
        )
        strategies = ", ".join(item.value for item in analysis.strategy_ids)
        scenario_state = candidate_analysis_state(analysis)
        lines.extend(
            (
                f"#### {analysis.provider_symbol} — {channels}",
                "",
                f"- 상태: `{analysis.status}`",
                f"- 후보 원천: `{sources}`",
                f"- 분석 상태: `{scenario_state}`",
                f"- 전략: `{strategies or 'ANALYSIS_INCOMPLETE'}`",
                f"- 데이터 스냅샷: `{analysis.data_snapshot_id or 'UNAVAILABLE'}`",
                f"- 작업 키: `{analysis.job_key}`",
                *tuple(
                    _render_strategy_result(strategy_id, payload)
                    for strategy_id, payload in sorted(
                        (analysis.strategy_results or {}).items()
                    )
                ),
                *(
                    (f"- 이슈: `{', '.join(analysis.issues)}`",)
                    if analysis.issues
                    else ()
                ),
                "",
                _without_shadow_markers(analysis.report_markdown).strip(),
                "",
            )
        )
    for failure in result.failures:
        candidate = by_identity[failure.security_id]
        channels = ", ".join(channel.value for channel in candidate.channels)
        lines.extend(
            (
                f"#### {failure.provider_symbol} — {channels}",
                "",
                "- 상태: `ANALYSIS_INCOMPLETE`",
                f"- 실패 유형: `{failure.failure_type}`",
                "",
            )
        )
    analysis_cohort = getattr(result, "analysis_cohort", None)
    for member in (
        () if analysis_cohort is None else analysis_cohort.observation_only_members
    ):
        candidate = member.candidate
        channels = ", ".join(channel.value for channel in candidate.channels)
        reasons = ", ".join(reason.value for reason in member.selection_reasons)
        lines.extend(
            (
                f"#### {candidate.provider_symbols[0]} — {channels}",
                "",
                "- 상태: `NOT_ANALYZED`",
                f"- 분석 제외 사유: `{reasons}`",
                "",
            )
        )
    lines.append(_KR_END)
    prefix = _without_existing_daily_composition(existing_markdown).rstrip()
    return (prefix + "\n" if prefix else "") + "\n".join(lines).lstrip() + "\n"


def _counts_payload(counts: KRDailyCounts | Any) -> dict[str, object]:
    return {
        "raw_assertions": counts.raw_assertions,
        "raw_assertions_by_source": dict(counts.raw_assertions_by_source),
        "raw_assertions_by_channel": dict(counts.raw_assertions_by_channel),
        "raw_assertions_by_trigger": dict(counts.raw_assertions_by_trigger),
        "unique_identities": counts.unique_identities,
        "excluded_identities": counts.excluded_identities,
        "invalid_records": counts.invalid_records,
        "data_unavailable": counts.data_unavailable,
        "analyzed_swing": counts.analyzed_swing,
        "analyzed_trend": counts.analyzed_trend,
        "analysis_failures": counts.analysis_failures,
        "truncated": counts.truncated,
    }


def _candidate_sources(candidate: ReconciledCandidate) -> list[dict[str, str]]:
    sources: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for snapshot in candidate.snapshots:
        identity = (snapshot.provider, snapshot.source_snapshot_id)
        if identity in seen:
            continue
        seen.add(identity)
        sources.append(
            {
                "provider": snapshot.provider,
                "source_snapshot_id": snapshot.source_snapshot_id,
                "observed_at": snapshot.observed_at.isoformat(),
                "available_at": snapshot.available_at.isoformat(),
                "ingested_at": snapshot.ingested_at.isoformat(),
            }
        )
    sources.sort(key=lambda item: (item["provider"], item["source_snapshot_id"]))
    return sources


def daily_dashboard_projection(result: KRDailyProductResult | Any) -> dict[str, object]:
    """Project the reconciled identity set and funnel onto the existing dashboard."""

    analyses = {item.security_id: item for item in result.analyses}
    failures = {item.security_id: item for item in result.failures}
    cohort = getattr(result, "analysis_cohort", None)
    cohort_members = {
        item.candidate.security_id: item
        for item in (() if cohort is None else cohort.members)
    }
    observation_universe = getattr(result, "observation_universe", None)
    observed_candidates = (
        result.reconciliation.included
        if observation_universe is None
        else observation_universe.members
    )
    candidates = []
    for candidate in observed_candidates:
        analysis = analyses.get(candidate.security_id)
        failure = failures.get(candidate.security_id)
        cohort_member = cohort_members.get(candidate.security_id)
        candidates.append(
            {
                "security_id": str(candidate.security_id.value),
                "provider_symbols": list(candidate.provider_symbols),
                "channels": [item.value for item in candidate.channels],
                "sources": _candidate_sources(candidate),
                "triggers": list(candidate.trigger_ids),
                "candidate_status": candidate.status.value,
                "analysis_status": (
                    analysis.status
                    if analysis is not None
                    else (
                        "NOT_ANALYZED"
                        if cohort_member is not None
                        and cohort_member.disposition is CohortDisposition.OBSERVE_ONLY
                        else "ANALYSIS_INCOMPLETE"
                    )
                ),
                "cohort_disposition": (
                    None if cohort_member is None else cohort_member.disposition.value
                ),
                "selection_reasons": (
                    []
                    if cohort_member is None
                    else [item.value for item in cohort_member.selection_reasons]
                ),
                "strategy_ids": (
                    [item.value for item in analysis.strategy_ids]
                    if analysis is not None
                    else []
                ),
                "data_snapshot_id": (
                    analysis.data_snapshot_id if analysis is not None else None
                ),
                "strategy_results": (
                    {
                        strategy_id: dict(payload)
                        for strategy_id, payload in analysis.strategy_results.items()
                    }
                    if analysis is not None and analysis.strategy_results is not None
                    else {}
                ),
                "failure_type": None if failure is None else failure.failure_type,
            }
        )
    return {
        "status": daily_product_status(result),
        "counts": _counts_payload(result.counts),
        "stockeasy": _stockeasy_projection(result),
        "candidates": candidates,
    }


def _stockeasy_projection(result: Any) -> dict[str, object]:
    capability = getattr(result, "supplement_capability", None)
    if capability is not None:
        return dict(capability)
    return {
        "status": result.supplement_outcome.value,
        "reason": result.supplement_error_code,
    }


def _render_stockeasy_requirements(capability: Mapping[str, object]) -> str:
    requirements = capability.get("requirements")
    if not isinstance(requirements, list):
        return "EVIDENCE_STATE_UNAVAILABLE"
    rows = []
    for item in requirements:
        if isinstance(item, Mapping):
            rows.append(f"{item.get('requirement')}={item.get('status')}")
    return ", ".join(rows) or "EVIDENCE_STATE_UNAVAILABLE"


def _render_stockeasy_capability_lines(
    capability: Mapping[str, object],
) -> tuple[str, ...]:
    lines = [
        f"- 보조 근거 기능 상태: `{capability['status']}`",
        "- 사이트/수집 상태: `"
        + str(capability.get("site_status", "SITE_STATUS_UNKNOWN"))
        + "` / `"
        + str(capability.get("ingestion_status", "NOT_INGESTED"))
        + "`",
        "- 필수 섹션 상태: `"
        + _render_stockeasy_requirements(capability)
        + "`",
    ]
    if capability.get("site_status_as_of") is not None:
        lines.extend(
            (
                f"- 사이트 상태 기준 시각: `{capability['site_status_as_of']}`",
                f"- 사이트 상태 근거: `{capability.get('site_status_basis')}`",
                f"- 현재 시점 재검증: `{capability.get('site_currently_verified')}`",
            )
        )
    if capability.get("authority_crosscheck_status") is not None:
        lines.extend(
            (
                "- KIS/KRX 값 대조: `"
                + str(capability["authority_crosscheck_status"])
                + "`",
                "- StockEasy 전략 수치 입력 사용: `"
                + str(capability.get("supplemental_numeric_values_used_for_strategy"))
                + "`",
            )
        )
    prerequisite = capability.get("prerequisite_code")
    if prerequisite is not None:
        lines.append(f"- 비밀정보 없는 사용자 선행조건: `{prerequisite}`")
    observations = capability.get("observations")
    if isinstance(observations, list) and observations:
        lines.append("- 보조 근거 관측:")
        lines.extend(
            f"  - `{_render_stockeasy_observation(item)}`"
            for item in observations
            if isinstance(item, Mapping)
        )
    lines.append("")
    return tuple(lines)


def _render_stockeasy_observation(item: Mapping[str, object]) -> str:
    scope = str(item.get("scope", "UNKNOWN"))
    identity = item.get("provider_symbol") or item.get("group_id") or scope
    return (
        f"{item.get('kind', 'UNKNOWN')} {scope} {identity}="
        f"{item.get('value', 'UNAVAILABLE')} {item.get('unit', 'UNAVAILABLE')}"
    )


def write_dashboard_projection(
    path: Path, projection: Mapping[str, object]
) -> None:
    """Extend, rather than replace, the existing dashboard read model atomically."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("existing dashboard output is unavailable or invalid") from exc
    if not isinstance(payload, dict):
        raise ValueError("existing dashboard output must be a JSON object")
    payload["kr_daily_composition"] = dict(projection)
    _atomic_write(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def context_call_evidence(
    *,
    context: Any,
    kis_evidence: Sequence[Mapping[str, object]],
    agentnews_result: Any | None,
) -> dict[str, object]:
    """Whitelist timestamped provider evidence without raw bodies or credentials."""

    allowed_kis_fields = {
        "endpoint",
        "status_code",
        "received_at",
        "raw_payload_hash",
    }
    agentnews_attempts = () if agentnews_result is None else agentnews_result.attempts
    return {
        "session_authority": "KRX_EXCHANGE_CALENDAR",
        "session_date": context.timing.session_date.isoformat(),
        "session_state": context.timing.session_state.value,
        "kis": [
            {key: value for key, value in item.items() if key in allowed_kis_fields}
            for item in kis_evidence
        ],
        "agentnews": [
            {
                "endpoint": attempt.url,
                "status_code": attempt.status_code,
                "received_at": attempt.fetched_at.isoformat(),
                "latency_ms": attempt.latency_ms,
                "content_hash": attempt.content_hash,
                "outcome": attempt.outcome,
            }
            for attempt in agentnews_attempts
        ],
        "agentnews_used_last_known_good": (
            False
            if agentnews_result is None
            else bool(agentnews_result.used_last_known_good)
        ),
        "source_clocks": [
            {
                "source": clock.source,
                "observed_at": clock.observed_at.isoformat(),
                "available_at": clock.available_at.isoformat(),
                "ingested_at": clock.ingested_at.isoformat(),
                "quality": clock.quality.value,
            }
            for clock in context.source_clocks
        ],
    }


def _without_shadow_markers(value: str) -> str:
    return value.replace("<!-- PRISM_PHASE1_SHADOW_START -->", "").replace(
        "<!-- PRISM_PHASE1_SHADOW_END -->", ""
    )


def _without_existing_daily_composition(value: str) -> str:
    start = value.find(_KR_START)
    if start < 0:
        return value
    end = value.find(_KR_END, start)
    if end < 0:
        raise ValueError("existing report contains an unterminated KR daily section")
    return value[:start] + value[end + len(_KR_END) :].lstrip("\n")


KST = ZoneInfo("Asia/Seoul")


def resolve_stockeasy_import(
    *,
    snapshot_path: Path | None,
    permission_path: Path | None,
    imported_at: datetime,
) -> StockEasyImportResult:
    """Resolve the paired local contracts without making StockEasy a hard dependency."""

    if snapshot_path is None and permission_path is None:
        return StockEasyImportResult(
            outcome=StockEasyImportOutcome.UNAVAILABLE,
            error_code="APPROVED_UI_EXPORT_NOT_CONFIRMED",
            temporary_image_deleted=False,
        )
    if snapshot_path is None or permission_path is None:
        return StockEasyImportResult(
            outcome=StockEasyImportOutcome.REJECTED,
            error_code="SNAPSHOT_AND_PERMISSION_RECORD_REQUIRED",
            temporary_image_deleted=False,
        )
    return load_stockeasy_import(
        snapshot_path=snapshot_path,
        permission_path=permission_path,
        imported_at=imported_at,
        max_age=timedelta(hours=24),
    )


def resolve_runtime_as_of(*, requested: datetime, now: datetime) -> datetime:
    """Use a post-fetch evaluation clock while rejecting future user boundaries."""

    for label, value in (("requested", requested), ("now", now)):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{label} as-of must be timezone-aware")
    if requested > now:
        raise ValueError("requested --as-of cannot be in the future")
    return now


def live_candidate_analysis_as_of(
    *, requested: datetime, now: datetime
) -> None:
    """Validate the operator boundary, then let live fundamentals fix the PIT clock."""

    resolve_runtime_as_of(requested=requested, now=now)
    return None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the complete uncapped KR daily-close candidate product through "
            "KIS/official evidence, SWING/TREND OAuth analysis, SQLite readback, "
            "and the existing report/dashboard. No account or broker capability "
            "is loaded."
        )
    )
    parser.add_argument("--as-of", required=True, type=datetime.fromisoformat)
    parser.add_argument("--research-db", required=True, type=Path)
    parser.add_argument("--paper-db", required=True, type=Path)
    parser.add_argument("--ops-db", required=True, type=Path)
    parser.add_argument("--report-output", required=True, type=Path)
    parser.add_argument("--dashboard-output", required=True, type=Path)
    parser.add_argument(
        "--stockeasy-snapshot",
        type=Path,
        help=(
            "optional bounded stockeasy_sanitized_snapshot_v1 JSON; requires the "
            "separate --stockeasy-permission-record contract"
        ),
    )
    parser.add_argument(
        "--stockeasy-permission-record",
        type=Path,
        help=(
            "separate scoped StockEasyPermissionRecord JSON; supplying either local "
            "contract without the other is rejected fail-soft"
        ),
    )
    parser.add_argument("--base-report", type=Path)
    parser.add_argument("--benchmark", default="069500")
    parser.add_argument("--model", default=CHATGPT_OAUTH_DEFAULT_MODEL)
    parser.add_argument("--model-version", default=CHATGPT_OAUTH_DEFAULT_MODEL)
    parser.add_argument("--code-version", default="uncommitted-kr-daily")
    parser.add_argument("--lookback-days", type=int, default=400)
    parser.add_argument(
        "--kis-token-cache",
        type=Path,
        default=Path.home() / ".cache" / "prism-insight" / "kis-market-data-token.json",
    )
    return parser


async def _run_command(args: argparse.Namespace) -> dict[str, object]:
    """Wire only the already-approved read-only provider and product contracts."""

    if args.as_of.tzinfo is None or args.as_of.utcoffset() is None:
        raise ValueError("--as-of must include a timezone offset")
    resolve_runtime_as_of(requested=args.as_of, now=datetime.now(tz=KST))

    from prism_app import product_uat
    from prism_app.dashboard_export import export_dashboard
    from prism_app.kr_candidate_source import KRCandidateSource
    from prism_app.leadership_supplement import LeadershipSupplementComposer
    from prism_core.data.providers.agentnews import AgentNewsProvider
    from prism_core.data.providers.kis_http import (
        KISHTTPTransport,
        KISMarketDataCredentials,
        SecureFileKISTokenCache,
    )
    from prism_core.market.composer import KRMarketContextComposer
    from prism_core.storage.database import open_database
    from prism_core.storage.migrations import DatabaseKind, migrate_database

    credentials = KISMarketDataCredentials.from_env()
    context_transport = KISHTTPTransport(
        credentials=credentials,
        symbols=(),
        timeout_seconds=15.0,
        max_response_bytes=1_000_000,
        min_request_interval_seconds=0.1,
        token_cache=SecureFileKISTokenCache(args.kis_token_cache),
    )
    agentnews_provider = _RecordingAgentNewsProvider(AgentNewsProvider())
    context_composer = KRMarketContextComposer(
        kis_transport=context_transport,
        agentnews_provider=agentnews_provider,
        clock=lambda: resolve_runtime_as_of(
            requested=args.as_of,
            now=datetime.now(tz=KST),
        ),
    )
    supplement_import = resolve_stockeasy_import(
        snapshot_path=args.stockeasy_snapshot,
        permission_path=args.stockeasy_permission_record,
        imported_at=datetime.now(tz=KST),
    )

    async def analyze(candidate: ReconciledCandidate) -> CandidateAnalysisResult:
        symbol = candidate.provider_symbols[0]
        candidate_as_of = live_candidate_analysis_as_of(
            requested=args.as_of,
            now=datetime.now(tz=KST),
        )
        with tempfile.TemporaryDirectory(prefix="prism-kr-daily-") as directory:
            candidate_report = Path(directory) / f"{symbol}.md"
            payload = await product_uat._run(  # noqa: SLF001 - canonical app composition
                argparse.Namespace(
                    symbol=symbol,
                    benchmark=args.benchmark,
                    as_of=candidate_as_of,
                    evidence_json=None,
                    fmp_symbol=None,
                    research_db=args.research_db,
                    paper_db=args.paper_db,
                    ops_db=args.ops_db,
                    output=candidate_report,
                    base_report=None,
                    model=args.model,
                    model_version=args.model_version,
                    code_version=args.code_version,
                    lookback_days=args.lookback_days,
                    kis_token_cache=args.kis_token_cache,
                    require_fresh_runtime_proof=False,
                    require_complete_runtime_proof=False,
                    force_live_prefetch=True,
                )
            )
            markdown = candidate_report.read_text(encoding="utf-8")
        raw_strategy_ids = payload.get("strategy_ids", ())
        if not isinstance(raw_strategy_ids, (list, tuple)):
            raise ValueError("product readback omitted exact strategy identities")
        strategy_ids = tuple(StrategyId(item) for item in raw_strategy_ids)
        raw_strategy_results = payload.get("strategy_results")
        if not isinstance(raw_strategy_results, Mapping):
            raise ValueError("product readback omitted persisted strategy results")
        strategy_results = {
            str(strategy_id): dict(result)
            for strategy_id, result in raw_strategy_results.items()
            if isinstance(strategy_id, str) and isinstance(result, Mapping)
        }
        issues = tuple(
            str(item)
            for field in ("quality_reasons", "missing_fields", "stale_fields")
            for item in payload.get(field, ())
        )
        return CandidateAnalysisResult(
            security_id=candidate.security_id,
            provider_symbol=symbol,
            job_key=str(payload["job_key"]),
            status=candidate_readback_status(str(payload["status"]), strategy_results),
            strategy_ids=strategy_ids,
            report_markdown=markdown,
            data_snapshot_id=str(payload["data_snapshot_id"]),
            strategy_results=strategy_results,
            issues=tuple(dict.fromkeys(issues)),
            call_evidence=(
                payload.get("network_evidence")
                if isinstance(payload.get("network_evidence"), Mapping)
                else None
            ),
            fresh_invocation=payload.get("fresh_invocation_verified") is True,
        )

    result = await KRDailyProduct(
        context_composer=context_composer,
        candidate_source=KRCandidateSource(),
        supplement_composer=LeadershipSupplementComposer(),
        candidate_analyzer=analyze,
    ).run(
        trigger_time="afternoon",
        supplement_import=supplement_import,
        supplement_snapshot_argument_supplied=args.stockeasy_snapshot is not None,
    )

    base = ""
    if args.base_report is not None:
        base = args.base_report.read_text(encoding="utf-8")
    _atomic_write(args.report_output, render_daily_composition(base, result))

    for path, kind in (
        (args.research_db, DatabaseKind.RESEARCH),
        (args.paper_db, DatabaseKind.PAPER),
        (args.ops_db, DatabaseKind.OPS),
    ):
        with open_database(path) as connection:
            migrate_database(connection, kind)
    run_as_of = resolve_runtime_as_of(
        requested=args.as_of,
        now=datetime.now(tz=KST),
    )
    export_dashboard(
        research_db=args.research_db,
        paper_db=args.paper_db,
        ops_db=args.ops_db,
        output_path=args.dashboard_output,
        as_of=run_as_of,
        generated_at=datetime.now(tz=KST),
    )
    write_dashboard_projection(
        args.dashboard_output,
        daily_dashboard_projection(result),
    )

    status = daily_product_status(result)
    return {
        "stage": "KR_DAILY_CLOSE_PRODUCT",
        "status": status,
        "requested_as_of": args.as_of.isoformat(),
        "as_of": run_as_of.isoformat(),
        "context_as_of": result.market_context.timing.as_of.isoformat(),
        "session_date": result.market_context.timing.session_date.isoformat(),
        "context_disposition": result.market_context.disposition.value,
        "source_statuses": {
            clock.source: clock.quality.value
            for clock in result.market_context.source_clocks
        },
        "context_call_evidence": context_call_evidence(
            context=result.market_context,
            kis_evidence=context_transport.evidence,
            agentnews_result=agentnews_provider.last_result,
        ),
        "counts": _counts_payload(result.counts),
        "stockeasy": _stockeasy_projection(result),
        "jobs": [item.job_key for item in result.analyses],
        "call_evidence": [
            {
                "provider_symbol": item.provider_symbol,
                "network": item.call_evidence,
            }
            for item in result.analyses
        ],
        "outputs": {
            "report": str(args.report_output),
            "dashboard": str(args.dashboard_output),
        },
        "broker_called": False,
        "message_sent": False,
        "schedule_activated": False,
        "uat_accepted": False,
        "operational_readiness": False,
    }


def _atomic_write(path: Path, content: str) -> None:
    destination = path.expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        payload = asyncio.run(_run_command(args))
        exit_code = daily_product_exit_code(str(payload["status"]))
    except Exception as exc:  # noqa: BLE001 - sanitize every provider/runtime failure
        payload = {
            "stage": "KR_DAILY_CLOSE_PRODUCT",
            "status": "PRODUCT_CAPABILITY_UNAVAILABLE",
            "failure_type": type(exc).__name__,
            "broker_called": False,
            "message_sent": False,
            "schedule_activated": False,
            "uat_accepted": False,
            "operational_readiness": False,
        }
        exit_code = 2
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return exit_code
