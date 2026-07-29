"""Read-only SHADOW rendering and persisted application-run readback."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
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

    suppress_levels, suppression_reasons = _level_suppression(analysis)
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
    lines = [
        _START,
        "## Phase 1 SHADOW",
        "",
        "> 이 섹션은 연구용 SHADOW 결과이며 주문 또는 계좌 작업을 수행하지 않습니다.",
        "",
        "### 1. 원천·기준시점·호출 증거와 데이터 품질",
        "",
        f"- 시장/기준일: `{analysis.market.value}` / `{analysis.as_of_date.isoformat()}`",
        f"- 품질 판정: `{analysis.quality_decision.disposition.value}`",
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
    ]
    source_as_of = analysis.source_payload.get("source_as_of", {})
    if isinstance(source_as_of, Mapping) and source_as_of:
        lines.extend(("#### 원천별 기준 시각", ""))
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

    scenarios: list[tuple[Any, Mapping[str, object]]] = []
    for strategy in analysis.strategies:
        scenario = strategy.output_payload.get("scenario")
        if isinstance(scenario, Mapping):
            scenarios.append((strategy, scenario))
    market_judgments = [
        scenario.get("market_judgment")
        for _, scenario in scenarios
        if isinstance(scenario, Mapping) and scenario.get("market_judgment") is not None
    ]
    lines.extend(
        (
            "### 2. KR 시장 국면·수급·주도/약세 그룹",
            "",
            "- 시장 국면: " + _display_json(market_judgments or "UNAVAILABLE"),
            "- 시장 폭: `UNAVAILABLE` (이 실행의 영속 계약에 별도 시장 폭 필드가 없습니다.)",
            "- 투자자 수급: `UNAVAILABLE` (이 실행의 영속 계약에 별도 수급 필드가 없습니다.)",
            "- 주도/약세 그룹: `UNAVAILABLE` (저장된 종목 판단 외 그룹 집계가 없습니다.)",
            "",
            "### 3. 후보 요약",
            "",
            "| 종목 | 채널 | 트리거 | SWING | TREND | 현재 상태 | 상위 지지 근거 | 상위 반대 근거 | 변화 |",
            "|---|---|---|---|---|---|---|---|---|",
        )
    )
    strategy_states = {
        strategy.strategy_id.value: _scenario_state(strategy.output_payload)
        for strategy in analysis.strategies
    }
    support = _top_evidence(scenarios, "bull_evidence_ids")
    counter = _top_evidence(scenarios, "bear_evidence_ids") or _top_evidence(
        scenarios, "counter_evidence"
    )
    symbol = str(analysis.source_payload.get("stock_symbol", "UNAVAILABLE"))
    lines.extend(
        (
            f"| {symbol} | 저장된 PRISM 후보 | 저장된 분석 실행 | "
            f"{strategy_states.get('SWING_V1', 'ANALYSIS_INCOMPLETE')} | "
            f"{strategy_states.get('TREND_V1', 'ANALYSIS_INCOMPLETE')} | "
            f"{_combined_state(tuple(strategy_states.values()))} | "
            f"{support or '없음'} | {counter or '없음'} | DATA_MISSING |",
            "",
            f"- 상위 지지 근거: `{support or '없음'}`",
            f"- 상위 반대 근거: `{counter or '없음'}`",
            "",
            "### 4. 종목별 SWING/TREND 카드",
            "",
        )
    )
    if not analysis.strategies:
        lines.append("- 시나리오 상태: `ANALYSIS_INCOMPLETE` (품질 게이트 또는 입력 증거 미충족)")
    for strategy in analysis.strategies:
        payload = strategy.output_payload
        scenario_state = _scenario_state(payload)
        scenario_complete = payload.get("scenario_complete", False)
        reasons = payload.get("scenario_reasons", ())
        public_reasons = tuple(
            item for item in reasons if isinstance(item, str) and item.strip()
        ) if isinstance(reasons, (tuple, list)) else ()
        quant_score = payload.get("quant_score")
        quant_score = quant_score if isinstance(quant_score, Mapping) else {}
        lines.extend(
            (
                f"#### {strategy.strategy_id.value} ({strategy.strategy_version.value})",
                "",
                f"- 검증 상태: `{payload.get('status', 'UNAVAILABLE')}`",
                f"- 시나리오 상태: `{scenario_state}`",
                f"- 시나리오 완결: `{scenario_complete is True}`",
                f"- 프롬프트 버전: `{payload.get('prompt_version', 'UNAVAILABLE')}`",
                f"- 검증기 버전: `{payload.get('validator_version', 'UNAVAILABLE')}`",
                f"- 결정론 점수 버전: `{quant_score.get('score_version', 'UNAVAILABLE')}`",
                f"- 결정론 점수: `{quant_score.get('total_score', 'UNAVAILABLE')}`",
                "- 점수 구성: "
                + _display_json(quant_score.get("components", {})),
                f"- 근거 참조 수: `{len(strategy.evidence_refs)}`",
            )
        )
        if public_reasons:
            lines.append(f"- 검증 사유: `{' | '.join(public_reasons)}`")
        lines.append("")
    lines.extend(
        (
            "### 5. 조건부 진입·회피와 무효화·축소/청산",
            "",
        )
    )
    for strategy, scenario in scenarios:
        safe_scenario = _scenario_without_actionable_levels(scenario) if suppress_levels else scenario
        lines.extend((f"#### {strategy.strategy_id.value}", ""))
        for key in (
            "entry_triggers", "triggers", "avoid_triggers", "failure_transition",
            "stop_candidates", "target_candidates", "reentry_candidates",
            "pyramiding_candidates",
        ):
            if key in safe_scenario:
                lines.append(f"- `{key}`: {_display_json(safe_scenario.get(key))}")
        lines.append("")
    lines.extend(("### 6. 데이터 공백·충돌과 가격 수준 공개 여부", ""))
    gaps = [
        *analysis.quality_decision.reasons,
        *analysis.quality_decision.missing_fields,
        *analysis.quality_decision.stale_fields,
    ]
    lines.append(f"- 데이터 공백/충돌: `{'; '.join(dict.fromkeys(gaps)) or '없음'}`")
    if suppress_levels:
        lines.append(
            "- 데이터 품질이 완전하지 않아 정확한 가격 수준을 공개하지 않습니다: `"
            + " | ".join(suppression_reasons)
            + "`"
        )
    else:
        lines.append("- 가격 수준 공개: `품질 게이트 ACCEPT인 저장 시나리오 후보만 표시`")
    lines.extend(
        (
            "",
            "### 7. 이전 실행 대비 변화",
            "",
            "- 변화 상태: `DATA_MISSING` (이 단일 실행 readback에는 이전 실행 연결 정보가 없습니다.)",
            "",
            "### 8. 다음 이벤트와 검토 시각",
            "",
        )
    )
    next_reviews = sorted(
        {
            str(scenario["next_review_at"])
            for _, scenario in scenarios
            if scenario.get("next_review_at")
        }
    )
    lines.append(f"- 다음 검토: `{', '.join(next_reviews) or 'UNAVAILABLE'}`")
    lines.extend(("", "### 9. 감사 상세", "", "<details>", "<summary>감사 상세 펼치기</summary>", ""))
    lines.extend(
        (
            f"- 실행 ID: `{analysis.run_id}`",
            f"- 데이터 스냅샷: `{analysis.data_snapshot_id}`",
            f"- 증거 해시: `{analysis.source_payload.get('evidence_hash', 'UNAVAILABLE')}`",
            "",
        )
    )
    for strategy, scenario in scenarios:
        safe_scenario = _scenario_without_actionable_levels(scenario) if suppress_levels else scenario
        lines.extend((f"#### {strategy.strategy_id.value} 감사 필드", ""))
        lines.extend(_scenario_detail_lines(safe_scenario))
    lines.extend(("</details>", ""))
    lines.append(_END)
    return "\n".join(lines) + "\n"


def _scenario_state(payload: Mapping[str, object]) -> str:
    value = payload.get("scenario_state") or payload.get("decision")
    return value if isinstance(value, str) and value else "ANALYSIS_INCOMPLETE"


def _combined_state(states: tuple[str, ...]) -> str:
    if not states:
        return "ANALYSIS_INCOMPLETE"
    return states[0] if len(set(states)) == 1 else "전략별 상이"


def _top_evidence(
    scenarios: list[tuple[Any, Mapping[str, object]]], key: str
) -> str | None:
    for _, scenario in scenarios:
        value = scenario.get(key)
        if isinstance(value, (tuple, list)):
            for item in value:
                if isinstance(item, str) and item:
                    return item
    return None


def _display_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _level_suppression(analysis: PersistedDailyAnalysis) -> tuple[bool, tuple[str, ...]]:
    reasons: list[str] = []
    if analysis.quality_decision.disposition.value != "ACCEPT":
        reasons.append(analysis.quality_decision.disposition.value)
    source_as_of = analysis.source_payload.get("source_as_of")
    if isinstance(source_as_of, Mapping):
        for source, disclosure in sorted(source_as_of.items()):
            if not isinstance(disclosure, Mapping):
                continue
            quality = disclosure.get("quality")
            if quality in {"STALE", "PARTIAL", "CONFLICT", "UNAVAILABLE"}:
                reasons.append(f"{source}:{quality}")
    return bool(reasons), tuple(dict.fromkeys(reasons))


def _scenario_without_actionable_levels(
    scenario: Mapping[str, object],
) -> dict[str, object]:
    suppressed = _scrub_level_values(scenario)
    for key in ("stop_candidates", "target_candidates"):
        if key in suppressed:
            suppressed[key] = [
                {"status": "SUPPRESSED_DUE_TO_DATA_QUALITY"}
            ]
    dispositions = suppressed.get("field_dispositions")
    if isinstance(dispositions, (tuple, list)):
        suppressed["field_dispositions"] = [
            {
                key: value
                for key, value in item.items()
                if key not in {"proposed_value", "resolved_value"}
            }
            for item in dispositions
            if isinstance(item, Mapping)
        ]
    return suppressed


def _scrub_level_values(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: _scrub_level_values(item)
            for key, item in value.items()
            if key not in {
                "price", "proposed_value", "resolved_value", "comparison_value",
                "observed_value", "lower_value", "upper_value",
            }
        }
    if isinstance(value, (tuple, list)):
        return [_scrub_level_values(item) for item in value]
    return value


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