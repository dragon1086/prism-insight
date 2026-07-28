"""Read-only SHADOW rendering and persisted application-run readback."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import quote

from prism_app.daily_pipeline import PersistedDailyAnalysis, SQLiteAppRunRepository


_START = "<!-- PRISM_PHASE1_SHADOW_START -->"
_END = "<!-- PRISM_PHASE1_SHADOW_END -->"


@dataclass(frozen=True)
class ShadowReadback:
    analysis: PersistedDailyAnalysis
    markdown: str


def render_shadow_report(analysis: PersistedDailyAnalysis) -> str:
    """Render stored analysis only; no provider, model, or publication call occurs."""

    lines = [
        _START,
        "## Phase 1 SHADOW",
        "",
        "> 이 섹션은 연구용 SHADOW 결과이며 주문 또는 계좌 작업을 수행하지 않습니다.",
        "",
        f"- 실행 ID: `{analysis.run_id}`",
        f"- 시장/기준일: `{analysis.market.value}` / `{analysis.as_of_date.isoformat()}`",
        f"- 데이터 스냅샷: `{analysis.data_snapshot_id}`",
        f"- 품질 판정: `{analysis.quality_decision.disposition.value}`",
    ]
    provider = analysis.source_payload.get("provider", "UNAVAILABLE")
    evidence_level = analysis.source_payload.get("evidence_level", "UNAVAILABLE")
    collected_at = analysis.source_payload.get("collected_at", "UNAVAILABLE")
    observed_at = analysis.source_payload.get("observed_at", "UNAVAILABLE")
    available_at = analysis.source_payload.get("available_at", "UNAVAILABLE")
    latest_session = analysis.source_payload.get(
        "latest_completed_session", "UNAVAILABLE"
    )
    price_basis = analysis.source_payload.get("price_basis", "UNAVAILABLE")
    adjustment_semantics = analysis.source_payload.get(
        "price_adjustment_semantics", "UNAVAILABLE"
    )
    action_count = analysis.source_payload.get("corporate_action_count", "UNAVAILABLE")
    action_coverage = analysis.source_payload.get(
        "corporate_action_coverage_status", "UNAVAILABLE"
    )
    action_coverage_scope = analysis.source_payload.get(
        "corporate_action_coverage_scope", "UNAVAILABLE"
    )
    incomplete_filter = analysis.source_payload.get(
        "incomplete_session_filter", "UNAVAILABLE"
    )
    lines.extend(
        (
            f"- 데이터 출처: `{provider}`",
            f"- 증거 수준: `{evidence_level}`",
            f"- 실제 수집 시각: `{collected_at}`",
            f"- 원천 관측 시각: `{observed_at}`",
            f"- 원천 이용 가능 시각: `{available_at}`",
            f"- 최신 완료 거래일: `{latest_session}`",
            f"- 가격 기준/조정 의미: `{price_basis}` / `{adjustment_semantics}`",
            f"- 기업행위 검증: `{action_coverage}` (건수 `{action_count}`)",
            f"- 기업행위 검증 범위: `{action_coverage_scope}`",
            f"- 미완료 세션 필터: `{incomplete_filter}`",
            "",
        )
    )
    source_as_of = analysis.source_payload.get("source_as_of", {})
    if isinstance(source_as_of, Mapping) and source_as_of:
        lines.extend(("### 원천별 기준 시각", ""))
        for source_name, disclosure in sorted(source_as_of.items()):
            if not isinstance(disclosure, Mapping):
                continue
            source = disclosure.get("source", "UNAVAILABLE")
            as_of = disclosure.get("as_of", "UNAVAILABLE")
            quality = disclosure.get("quality", "UNAVAILABLE")
            lines.append(
                f"- `{source_name}`: {source} / as-of `{as_of}` / 품질 `{quality}`"
            )
        lines.append("")
    if not analysis.strategies:
        lines.append("- 시나리오 상태: `ANALYSIS_INCOMPLETE` (품질 게이트 또는 입력 증거 미충족)")
    for strategy in analysis.strategies:
        payload = strategy.output_payload
        scenario_state = payload.get("scenario_state")
        if not isinstance(scenario_state, str) or not scenario_state:
            decision_value = payload.get("decision")
            scenario_state = (
                decision_value
                if isinstance(decision_value, str) and decision_value
                else "ANALYSIS_INCOMPLETE"
            )
        scenario_complete = payload.get("scenario_complete", False)
        reasons = payload.get("scenario_reasons", ())
        public_reasons = tuple(
            item for item in reasons if isinstance(item, str) and item.strip()
        ) if isinstance(reasons, (tuple, list)) else ()
        lines.extend(
            (
                f"### {strategy.strategy_id.value} ({strategy.strategy_version.value})",
                "",
                f"- 검증 상태: `{payload.get('status', 'UNAVAILABLE')}`",
                f"- 시나리오 상태: `{scenario_state}`",
                f"- 시나리오 완결: `{scenario_complete is True}`",
                f"- 프롬프트 버전: `{payload.get('prompt_version', 'UNAVAILABLE')}`",
                f"- 검증기 버전: `{payload.get('validator_version', 'UNAVAILABLE')}`",
                f"- 근거 참조 수: `{len(strategy.evidence_refs)}`",
            )
        )
        if public_reasons:
            lines.append(f"- 검증 사유: `{' | '.join(public_reasons)}`")
        lines.append("")
        scenario = payload.get("scenario")
        if scenario_complete is True and isinstance(scenario, Mapping):
            lines.extend(_scenario_detail_lines(scenario))
    lines.append(_END)
    return "\n".join(lines) + "\n"


def _scenario_detail_lines(scenario: Mapping[str, object]) -> list[str]:
    """Render the allowlisted normalized scenario without dropping nested values."""

    sections = (
        ("시장 판정", ("market_judgment",)),
        ("섹터 판정", ("sector_judgment",)),
        ("종목 판정", ("security_judgment",)),
        ("상승/기본/하락 경로", ("bull_path", "base_path", "bear_path")),
        ("진입/회피 조건", ("entry_triggers", "avoid_triggers")),
        ("손절/목표 후보", ("stop_candidates", "target_candidates")),
        (
            "리스크 배수/재진입/피라미딩 후보",
            (
                "risk_multiplier_candidate",
                "reentry_candidates",
                "pyramiding_candidates",
            ),
        ),
        (
            "지지/반대 근거/무효화",
            ("bull_evidence_ids", "bear_evidence_ids", "falsifiers"),
        ),
        ("불확실성/다음 검토", ("uncertainty", "next_review_at")),
        ("필드 판정", ("field_dispositions",)),
    )
    lines: list[str] = []
    for title, keys in sections:
        lines.extend((f"#### {title}", ""))
        for key in keys:
            value = scenario.get(key)
            lines.append(
                f"- `{key}`: "
                + json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            )
        lines.append("")
    return lines


def append_shadow_section(existing_markdown: str, shadow_markdown: str) -> str:
    """Append or replace the one bounded SHADOW seam without changing prior content."""

    if _START not in shadow_markdown or _END not in shadow_markdown:
        raise ValueError("shadow markdown is missing bounded section markers")
    start = existing_markdown.find(_START)
    if start >= 0:
        end = existing_markdown.find(_END, start)
        if end < 0:
            raise ValueError("existing report contains an unterminated SHADOW section")
        end += len(_END)
        suffix = existing_markdown[end:]
        if suffix.startswith("\n"):
            suffix = suffix[1:]
        return existing_markdown[:start] + shadow_markdown + suffix
    separator = "" if not existing_markdown or existing_markdown.endswith("\n\n") else "\n"
    return existing_markdown + separator + shadow_markdown


def read_persisted_shadow(path: str | Path, *, job_key: str) -> ShadowReadback:
    """Read one exact persisted run from an existing ops database in SQLite RO mode."""

    resolved = Path(path).expanduser().resolve(strict=True)
    uri = f"file:{quote(str(resolved), safe='/')}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, isolation_level=None)
    try:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA foreign_keys = ON")
        analysis = SQLiteAppRunRepository(connection).get(job_key)
    finally:
        connection.close()
    if analysis is None:
        raise LookupError("persisted SHADOW run was not found")
    return ShadowReadback(analysis=analysis, markdown=render_shadow_report(analysis))