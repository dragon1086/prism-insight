"""User-operated KR Phase 1 SHADOW product command with explicit PIT evidence."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from prism_app.market_snapshot_composer import KRPITEvidence, KRProductSnapshotComposer
from prism_app.live_kr_evidence import (
    FMPIncomeStatementClient,
    LiveKREvidenceError,
    LiveKREvidenceProvider,
)
from prism_app.kr_evidence_composer import DARTPort, KINDPort, KREvidenceComposer
from prism_app.oauth_llm import CHATGPT_OAUTH_DEFAULT_MODEL, ChatGPTOAuthRuntime
from prism_app.product_composition import ProductRunConfig, run_kr_shadow_product
from prism_app.daily_pipeline import PersistedDailyAnalysis
from prism_core.data.contracts import SecurityId
from prism_core.data.quality import QualityDisposition
from prism_core.data.providers.kis import KISInstrument, KISMarketDataProvider
from prism_core.data.providers.kis_fundamentals import KISFundamentalProvider
from prism_core.data.providers.kis_http import (
    KISHTTPTransport,
    KISMarketDataCredentials,
    KISMarketDataTransportError,
    SecureFileKISTokenCache,
)
from prism_core.data.providers.agentnews import AgentNewsProvider
from prism_core.data.providers.agentnews_models import AgentNewsBoard
from prism_core.data.providers.dart import UnavailableDARTAdapter
from prism_core.data.providers.kind import UnavailableKINDAdapter
from prism_core.runtime.settings import ProductMode, RuntimeSettings


KST = ZoneInfo("Asia/Seoul")
_EVIDENCE_FIELDS = {
    "observed_at",
    "available_at",
    "ingested_at",
    "observations",
    "evidence_payload",
}


class ProductRuntimeProofIncomplete(RuntimeError):
    """The diagnostic ran, but did not prove a complete actionable runtime path."""


def load_pit_evidence(path: str | Path, *, as_of: datetime) -> KRPITEvidence:
    """Load a strict local evidence file; secret-shaped extra fields are rejected."""

    try:
        payload = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("PIT evidence file is unavailable or invalid JSON") from exc
    if not isinstance(payload, dict) or set(payload) != _EVIDENCE_FIELDS:
        raise ValueError("PIT evidence must contain exactly the documented fields")
    observations = payload["observations"]
    evidence_payload = payload["evidence_payload"]
    if not isinstance(observations, dict) or not isinstance(evidence_payload, dict):
        raise ValueError("PIT observations and evidence_payload must be objects")
    try:
        decimal_observations = {
            str(name): Decimal(str(value)) for name, value in observations.items()
        }
        evidence = KRPITEvidence(
            observed_at=datetime.fromisoformat(payload["observed_at"]),
            available_at=datetime.fromisoformat(payload["available_at"]),
            ingested_at=datetime.fromisoformat(payload["ingested_at"]),
            observations=decimal_observations,
            evidence_payload=evidence_payload,
        )
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("PIT evidence contains an invalid typed value") from exc
    if evidence.available_at > as_of:
        raise ValueError("PIT evidence was available after evaluation")
    return evidence


def _security_id(symbol: str) -> SecurityId:
    return SecurityId(value=uuid5(NAMESPACE_URL, f"prism:kr:{symbol}"))


def require_product_runtime_proof(
    analysis: PersistedDailyAnalysis,
    *,
    idempotent_replay: bool = False,
    require_fresh_invocation: bool = True,
) -> None:
    """Reject a persisted cycle that did not traverse every proof-critical leg."""

    if idempotent_replay and require_fresh_invocation:
        raise ProductRuntimeProofIncomplete("product runtime proof requires a fresh non-replay invocation")
    if analysis.quality_decision.disposition is not QualityDisposition.ACCEPT:
        raise ProductRuntimeProofIncomplete("product runtime proof stopped at the quality gate")
    if len(analysis.strategies) != 2:
        raise ProductRuntimeProofIncomplete("product runtime proof requires both strategy families")
    if any(
        item.output_payload.get("backend_error_type") == "LLM_BACKEND_FAILURE"
        for item in analysis.strategies
    ):
        raise ProductRuntimeProofIncomplete("product runtime proof requires the OAuth LLM backend")
    if any(
        item.output_payload.get("model_output_error_type") == "LLM_OUTPUT_INVALID"
        for item in analysis.strategies
    ):
        raise ProductRuntimeProofIncomplete("product runtime proof requires valid structured model output")
    if any(
        item.output_payload.get("scenario_complete") is not True
        or item.output_payload.get("decision")
        not in {"WATCH", "NO_ENTRY", "ENTRY_CANDIDATE"}
        for item in analysis.strategies
    ):
        raise ProductRuntimeProofIncomplete("product runtime proof requires complete normalized scenarios")
    score_versions: set[str] = set()
    for item in analysis.strategies:
        quant_score = item.output_payload.get("quant_score")
        if not isinstance(quant_score, dict):
            raise ProductRuntimeProofIncomplete(
                "product runtime proof requires the current SHADOW score audit"
            )
        score_version = quant_score.get("score_version")
        if not isinstance(score_version, str) or not score_version.startswith(
            "SHADOW_SCORE_V1."
        ):
            raise ProductRuntimeProofIncomplete(
                "product runtime proof requires the current SHADOW score audit"
            )
        score_versions.add(score_version)
        if (
            not isinstance(quant_score.get("feature_snapshot_id"), str)
            or quant_score.get("recomposition_matches") is not True
            or quant_score.get("recomposed_total") != quant_score.get("total_score")
            or not isinstance(quant_score.get("component_details"), list)
            or not quant_score["component_details"]
            or not isinstance(quant_score.get("threshold_version"), str)
            or not quant_score["threshold_version"].startswith(
                "SHADOW_ENTRY_THRESHOLDS_V1."
            )
            or not isinstance(quant_score.get("thresholds"), list)
            or not quant_score["thresholds"]
        ):
            raise ProductRuntimeProofIncomplete(
                "product runtime proof requires score recomposition and threshold audit"
            )
    if score_versions != {
        "SHADOW_SCORE_V1.SWING_V1",
        "SHADOW_SCORE_V1.TREND_V1",
    }:
        raise ProductRuntimeProofIncomplete(
            "product runtime proof requires separate current SWING/TREND score audits"
        )


def enforce_product_runtime_proof(
    analysis: PersistedDailyAnalysis,
    *,
    enabled: bool,
    idempotent_replay: bool,
    require_fresh_invocation: bool,
) -> None:
    """Keep strict UAT gating optional for callers that must render invalid readbacks."""

    if enabled:
        require_product_runtime_proof(
            analysis,
            idempotent_replay=idempotent_replay,
            require_fresh_invocation=require_fresh_invocation,
        )


def runtime_invocation_evidence(
    *, idempotent_replay: bool, strategy_payloads: Sequence[Mapping[str, object]]
) -> dict[str, bool | int]:
    """Separate calls made now from structured responses read from persistence."""

    strategy_count = len(strategy_payloads)
    backend_failure_count = sum(
        payload.get("backend_error_type") is not None
        for payload in strategy_payloads
    )
    invalid_model_output_count = sum(
        payload.get("backend_error_type") is None
        and payload.get("model_output_error_type") is not None
        for payload in strategy_payloads
    )
    invalid_proposal_count = sum(
        payload.get("backend_error_type") is None
        and payload.get("model_output_error_type") is None
        and payload.get("scenario_state") == "INVALID_PROPOSAL"
        for payload in strategy_payloads
    )
    structured_count = (
        strategy_count
        - backend_failure_count
        - invalid_model_output_count
        - invalid_proposal_count
    )
    return {
        "fresh_invocation_verified": not idempotent_replay and strategy_count > 0,
        "idempotent_replay": idempotent_replay,
        "structured_response_count": 0 if idempotent_replay else structured_count,
        "replayed_response_count": structured_count if idempotent_replay else 0,
        "backend_failure_count": backend_failure_count,
        "invalid_model_output_count": invalid_model_output_count,
        "invalid_proposal_count": invalid_proposal_count,
    }


def runtime_strategy_evidence(analysis: PersistedDailyAnalysis) -> tuple[str, ...]:
    """Return the exact persisted strategy identities in evaluation order."""

    return tuple(item.strategy_id.value for item in analysis.strategies)


def runtime_strategy_results(
    analysis: PersistedDailyAnalysis,
) -> dict[str, dict[str, object]]:
    """Project exact persisted decisions and score audits for product composition."""

    results: dict[str, dict[str, object]] = {}
    for item in analysis.strategies:
        payload = item.output_payload
        results[item.strategy_id.value] = {
            "data_snapshot_id": str(analysis.data_snapshot_id),
            "scenario_state": payload.get("scenario_state"),
            "scenario_complete": payload.get("scenario_complete"),
            "decision": payload.get("decision"),
            "backend_error_type": payload.get("backend_error_type"),
            "model_output_error_type": payload.get("model_output_error_type"),
            "scenario_reasons": list(payload.get("scenario_reasons", ())),
            "hard_vetoes": list(payload.get("hard_vetoes", ())),
            "quant_score": dict(payload.get("quant_score", {})),
        }
    return results


def sanitized_failure_payload(exc: Exception) -> dict[str, Any]:
    """Return bounded non-secret capability evidence for a failed product run."""

    payload: dict[str, Any] = {
        "stage": "PHASE1_SHADOW_PRODUCT",
        "status": (
            "PRODUCT_RUNTIME_PROOF_INCOMPLETE"
            if isinstance(exc, ProductRuntimeProofIncomplete)
            else "PRODUCT_CAPABILITY_UNAVAILABLE"
        ),
        "failure_type": type(exc).__name__,
        "broker_called": False,
        "schedule_activated": False,
        "uat_accepted": False,
        "operational_readiness": False,
    }
    if isinstance(exc, ProductRuntimeProofIncomplete):
        payload["failure_stage"] = "runtime_proof"
    if isinstance(exc, KISMarketDataTransportError):
        if exc.operation is not None:
            payload["failure_stage"] = exc.operation
        if exc.status_code is not None:
            payload["provider_status_code"] = exc.status_code
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the read-only KR provider→features→quant→OAuth LLM→SQLite→"
            "SHADOW report product path. No account or broker capability is loaded."
        )
    )
    parser.add_argument("--symbol", default="005930")
    parser.add_argument("--benchmark", default="069500")
    parser.add_argument("--as-of", type=datetime.fromisoformat)
    parser.add_argument(
        "--evidence-json",
        type=Path,
        help="legacy diagnostic override; normal product runs collect evidence automatically",
    )
    parser.add_argument("--fmp-symbol")
    parser.add_argument("--research-db", required=True, type=Path)
    parser.add_argument("--ops-db", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--base-report", type=Path)
    parser.add_argument("--model", default=CHATGPT_OAUTH_DEFAULT_MODEL)
    parser.add_argument("--model-version", default=CHATGPT_OAUTH_DEFAULT_MODEL)
    parser.add_argument("--code-version", default="uncommitted-phase1-product")
    parser.add_argument("--lookback-days", type=int, default=400)
    parser.add_argument(
        "--kis-token-cache",
        type=Path,
        default=Path.home() / ".cache" / "prism-insight" / "kis-market-data-token.json",
        help="mode-0600 local read-only market-data token cache",
    )
    return parser


def _resolved_fmp_symbol(args: argparse.Namespace) -> str:
    return args.fmp_symbol or f"{args.symbol}.KS"


def _optional_fmp_client() -> FMPIncomeStatementClient | None:
    try:
        return FMPIncomeStatementClient.from_env()
    except LiveKREvidenceError:
        return None


def _kis_primary_evidence_composer(
    *,
    stock_code: str,
    security_id: SecurityId,
    provider: KISFundamentalProvider,
    dart: DARTPort,
    kind: KINDPort,
) -> KREvidenceComposer:
    """Compose the normal KR path without loading an FMP fallback."""

    return KREvidenceComposer(
        stock_code=stock_code,
        security_id=security_id,
        kis=provider,
        dart=dart,
        kind=kind,
        fmp=None,
    )


async def _prefetch_before_live_decision(
    *,
    provider: KISFundamentalProvider,
    stock_code: str,
    requested_as_of: datetime | None,
    clock: Callable[[], datetime],
    force_live_prefetch: bool = False,
) -> datetime:
    """Freeze live receipts, then establish the PIT decision instant."""

    if requested_as_of is not None and not force_live_prefetch:
        return requested_as_of
    await provider.prefetch(stock_code=stock_code)
    decision_as_of = clock()
    if decision_as_of.tzinfo is None or decision_as_of.utcoffset() is None:
        raise ValueError("live decision clock must be timezone-aware")
    return decision_as_of


def _product_status(disposition: QualityDisposition) -> str:
    return {
        QualityDisposition.ACCEPT: "PERSISTED_READBACK_VERIFIED",
        QualityDisposition.REPORT_ONLY: "REPORT_ONLY_READBACK_VERIFIED",
        QualityDisposition.REJECT: "ANALYSIS_INCOMPLETE_READBACK_VERIFIED",
    }[disposition]


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    as_of = args.as_of or datetime.now(tz=KST)
    if as_of.tzinfo is None:
        raise ValueError("--as-of must include a timezone offset")
    evidence = (
        load_pit_evidence(args.evidence_json, as_of=as_of)
        if args.evidence_json is not None
        else None
    )
    stock_id = _security_id(args.symbol)
    benchmark_id = _security_id(args.benchmark)
    credentials = KISMarketDataCredentials.from_env()
    transport = KISHTTPTransport(
        credentials=credentials,
        symbols=(args.symbol, args.benchmark),
        lookback_calendar_days=args.lookback_days,
        timeout_seconds=15.0,
        max_response_bytes=4_000_000,
        min_request_interval_seconds=0.1,
        token_cache=SecureFileKISTokenCache(args.kis_token_cache),
    )
    provider = KISMarketDataProvider(
        transport=transport,
        instruments=(
            KISInstrument(security_id=stock_id, kis_symbol=args.symbol),
            KISInstrument(security_id=benchmark_id, kis_symbol=args.benchmark),
        ),
        clock=lambda: datetime.now(tz=KST),
        max_attempts=2,
    )
    composer_kwargs: dict[str, Any] = {
        "provider": provider,
        "stock_id": stock_id,
        "benchmark_id": benchmark_id,
        "stock_symbol": args.symbol,
        "benchmark_symbol": args.benchmark,
    }
    if evidence is not None:
        composer_kwargs["evidence"] = evidence
        agentnews_results: list[Any] = []
        fundamentals = None
        fundamental_transport = None
        cascade = None
    else:
        agentnews = AgentNewsProvider()
        agentnews_results = []

        async def fetch_agentnews_kr():
            fetch_result = await agentnews.fetch_result(AgentNewsBoard.KR)
            agentnews_results.append(fetch_result)
            return fetch_result.snapshot

        fundamentals = None
        fundamental_transport = KISHTTPTransport(
            credentials=credentials,
            symbols=(),
            timeout_seconds=15.0,
            max_response_bytes=1_000_000,
            min_request_interval_seconds=0.1,
            token_cache=SecureFileKISTokenCache(args.kis_token_cache),
        )
        fundamental_provider = KISFundamentalProvider(
            transport=fundamental_transport
        )
        as_of = await _prefetch_before_live_decision(
            provider=fundamental_provider,
            stock_code=args.symbol,
            requested_as_of=args.as_of,
            clock=lambda: datetime.now(tz=KST),
            force_live_prefetch=getattr(args, "force_live_prefetch", False),
        )
        cascade = _kis_primary_evidence_composer(
            stock_code=args.symbol,
            security_id=stock_id,
            provider=fundamental_provider,
            dart=UnavailableDARTAdapter(),
            kind=UnavailableKINDAdapter(),
        )
        composer_kwargs["evidence_provider"] = LiveKREvidenceProvider(
            agentnews_fetcher=fetch_agentnews_kr,
            fundamentals_client=cascade,
            fmp_symbol=_resolved_fmp_symbol(args),
        )
    composer = KRProductSnapshotComposer(**composer_kwargs)
    settings = RuntimeSettings(
        product_mode=ProductMode.SHADOW,
        broker_enabled=False,
        research_db_path=args.research_db,
        ops_db_path=args.ops_db,
    )
    prior_auth_mode = os.environ.get("PRISM_OPENAI_AUTH_MODE")
    if prior_auth_mode is None:
        os.environ["PRISM_OPENAI_AUTH_MODE"] = "chatgpt_oauth"
    try:
        async with ChatGPTOAuthRuntime() as backend:
            result = await run_kr_shadow_product(
                composer=composer,
                backend=backend,
                settings=settings,
                config=ProductRunConfig(
                    evaluated_at=as_of,
                    run_type="daily-close",
                    model_id=args.model,
                    model_version=args.model_version,
                    code_version=args.code_version,
                    owner_id=f"phase1-uat-{os.getpid()}",
                ),
                output_path=args.output,
                base_report_path=args.base_report,
            )
    finally:
        if prior_auth_mode is None:
            os.environ.pop("PRISM_OPENAI_AUTH_MODE", None)
    if result.analysis.quality_decision.disposition is QualityDisposition.ACCEPT:
        enforce_product_runtime_proof(
            result.analysis,
            enabled=getattr(args, "require_complete_runtime_proof", True),
            idempotent_replay=result.idempotent_replay,
            require_fresh_invocation=getattr(
                args, "require_fresh_runtime_proof", True
            ),
        )
    board_evidence = [
        {
            "provider": "AgentNews",
            "host": "agentnews.md",
            "endpoint": "/finance-ko.md",
            "status_code": attempt.status_code,
            "received_at": attempt.fetched_at.isoformat(),
            "latency_ms": attempt.latency_ms,
            "outcome": attempt.outcome,
        }
        for fetch_result in agentnews_results
        for attempt in fetch_result.attempts
    ]
    invocation_evidence = runtime_invocation_evidence(
        idempotent_replay=result.idempotent_replay,
        strategy_payloads=tuple(
            item.output_payload for item in result.analysis.strategies
        ),
    )
    strategy_ids = runtime_strategy_evidence(result.analysis)
    return {
        "stage": "PHASE1_SHADOW_PRODUCT",
        "status": _product_status(result.analysis.quality_decision.disposition),
        "job_key": result.analysis.job_key,
        "invocation_id": result.invocation_id,
        "data_snapshot_id": str(result.analysis.data_snapshot_id),
        "quality_disposition": result.analysis.quality_decision.disposition.value,
        "quality_reasons": result.analysis.quality_decision.reasons,
        "missing_fields": result.analysis.quality_decision.missing_fields,
        "stale_fields": result.analysis.quality_decision.stale_fields,
        "strategy_count": len(result.analysis.strategies),
        "strategy_ids": strategy_ids,
        "strategy_results": runtime_strategy_results(result.analysis),
        "llm_backend_verified": (
            invocation_evidence["structured_response_count"]
            + invocation_evidence["replayed_response_count"]
            == len(result.analysis.strategies)
            and bool(result.analysis.strategies)
        ),
        "fresh_invocation_verified": invocation_evidence["fresh_invocation_verified"],
        "idempotent_replay": invocation_evidence["idempotent_replay"],
        "network_evidence": {
            "kis_market_data": [
                {
                    "provider": "KIS",
                    "host": "openapi.koreainvestment.com:9443",
                    **item,
                }
                for item in transport.evidence
            ],
            "fmp_fundamentals": () if fundamentals is None else fundamentals.evidence,
            "kis_fundamentals": (
                () if fundamental_transport is None else fundamental_transport.evidence
            ),
            "kr_official_cascade": (
                ()
                if cascade is None or cascade.last_result is None
                else {
                    "selected_provider": cascade.last_result.selected_provider,
                    "quality": cascade.last_result.quality.value,
                    "issues": cascade.last_result.issues,
                    "calls": cascade.last_result.call_evidence,
                }
            ),
            "agentnews_kr": board_evidence,
            "oauth": {
                "auth_mode": "chatgpt_oauth",
                "requested_and_forwarded_model": args.model,
                "structured_response_count": invocation_evidence[
                    "structured_response_count"
                ],
                "replayed_response_count": invocation_evidence[
                    "replayed_response_count"
                ],
                "backend_failure_count": invocation_evidence[
                    "backend_failure_count"
                ],
                "invalid_model_output_count": invocation_evidence[
                    "invalid_model_output_count"
                ],
                "invalid_proposal_count": invocation_evidence[
                    "invalid_proposal_count"
                ],
                "tool_count": 0,
            },
        },
        "output": str(result.output_path),
        "broker_called": False,
        "schedule_activated": False,
        "uat_accepted": False,
        "operational_readiness": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        payload = asyncio.run(_run(args))
        exit_code = 0
    except Exception as exc:  # noqa: BLE001 - never expose provider message/details
        payload = sanitized_failure_payload(exc)
        exit_code = 2
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
