"""Compact LLM-scenario facts for the shared ClickStack ledger (fail-open).

The scenario runtime keeps its authoritative audit trail in SQLite
(``llm_scenario_audit_events``, ``llm_scenario_outbox``); none of it reached
the ClickStack spool, and the runner's stdout status lines carry no time.  This
bridge forwards a bounded summary of each committed audit event, each operator
notice and each runner invocation.

Same boundary as ``exit_capture``: no credentials, no exchange response bodies
and no raw order IDs — exchange orders appear only as the audit trail's own
``order_id_hash``.  Every function swallows every failure; observability is
never an execution dependency.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sys
from pathlib import Path

log = logging.getLogger(__name__)

SERVICE = "prism-btc-scenario"
# accounting_observation is a large per-run snapshot; positions already ship as
# btc.exit.snapshot, so it stays in SQLite only.
FORWARDED_KINDS = frozenset({"decision_input", "intent_committed", "exchange_call", "settlement_recorded"})
_NUMERIC = (int, float, bool)


def _emit():
    root = str(Path(__file__).resolve().parents[2])
    if root not in sys.path:
        sys.path.append(root)
    from observability.events import emit_event
    return emit_event


def _hex(value, length=32):
    return hashlib.sha256(str(value).encode()).hexdigest()[:length]


def _scalars(mapping, *, skip=()):
    return {k: v for k, v in (mapping or {}).items()
            if k not in skip and (isinstance(v, _NUMERIC) or v is None)}


def summarize(kind, body):
    """Bounded, ID-free attributes for one audit kind."""
    if kind == "decision_input":
        context = body.get("context") or {}
        snapshot = body.get("snapshot") or {}
        return {"day": context.get("day"), "accounting_status": context.get("accounting_status"),
                "daily_net_pnl": context.get("daily_net_pnl"),
                "day_start_equity": context.get("day_start_equity"),
                "has_current_plan": context.get("current_plan") is not None,
                "snapshot_valid": snapshot.get("valid"),
                "context_hash": body.get("context_hash"), "snapshot_hash": body.get("snapshot_hash"),
                "input_id_hash": body.get("input_id_hash")}
    if kind == "intent_committed":
        payload = body.get("payload") or {}
        return {"action": payload.get("action"), "confidence": payload.get("confidence"),
                "leverage": payload.get("leverage"), "hard_stop": payload.get("hard_stop"),
                "expires_at": payload.get("expires_at"), "revision": payload.get("revision"),
                "entry_count": len(payload.get("entries") or []),
                "rationale": str(payload.get("rationale") or "")[:300] or None,
                "risk": _scalars(body.get("risk")), "payload_hash": body.get("payload_hash")}
    if kind == "exchange_call":
        started, ended = body.get("started_at"), body.get("ended_at")
        request = body.get("request") or {}
        return {"method": body.get("method"), "status": body.get("status"),
                "ret_code": body.get("ret_code"),
                "latency_ms": round((ended - started) * 1000, 1)
                if isinstance(started, (int, float)) and isinstance(ended, (int, float)) else None,
                "symbol": request.get("symbol") if isinstance(request, dict) else None,
                "order_id_hash": body.get("order_id_hash"), "request_hash": body.get("request_hash")}
    if kind == "settlement_recorded":
        settlement = body.get("settlement") or {}
        attrs = _scalars(settlement)
        attrs["execution_count"] = len(settlement.get("execution_ids") or [])
        return attrs
    return {}


def forward_audits(events):
    """Forward one run's committed rows.  A decision input carries no scenario id (the
    scenario does not exist yet); when the run touched exactly one scenario, the input
    joins that scenario's trace, flagged ``scenario_inferred``."""
    try:
        scenarios = {event[4] for event in events if event[4]}
        run_scenario = next(iter(scenarios)) if len(scenarios) == 1 else None
    except Exception:
        run_scenario = None
    for event in events:
        forward_audit(event, run_scenario=run_scenario)


def forward_audit(event, *, run_scenario=None):
    """``event`` is the committed llm_scenario_audit_events row tuple."""
    try:
        event_id, run_id, kind, observed_at, scenario_id, intent_id, slot, manifest_id, text = event
        if kind not in FORWARDED_KINDS:
            return
        inferred = not scenario_id and bool(run_scenario) and kind == "decision_input"
        scenario_id = scenario_id or (run_scenario if inferred else None)
        from datetime import datetime, timezone
        _emit()(f"btc.scenario.{kind}", service=SERVICE, market="CRYPTO", ticker="BTCUSDT",
                event_id=_hex(f"audit:{event_id}"),
                trace_id=_hex(f"scenario:{scenario_id}") if scenario_id else None,
                event_time=datetime.fromtimestamp(float(observed_at), timezone.utc),
                attributes={"run_ref": _hex(run_id, 16), "decision_slot": slot,
                            "scenario_ref": _hex(scenario_id, 16) if scenario_id else None,
                            "intent_ref": _hex(intent_id, 16) if intent_id else None,
                            "manifest_ref": _hex(manifest_id, 16) if manifest_id else None,
                            "scenario_inferred": inferred,
                            **summarize(kind, json.loads(text))})
    except Exception:
        log.debug("scenario audit forward skipped")


def forward_notice(event_id, event, body):
    """Operator/Telegram notices (HALTED, MODEL_ERROR, FILLED, …) as queued."""
    try:
        scenario_id = event.get("scenario_id") if isinstance(event, dict) else None
        _emit()("btc.scenario.notice", service=SERVICE, market="CRYPTO", ticker="BTCUSDT",
                event_id=_hex(f"notice:{event_id}"),
                trace_id=_hex(f"scenario:{scenario_id}") if scenario_id else None,
                severity="WARNING" if event.get("kind") in {"HALTED", "MODEL_ERROR"} else "INFO",
                attributes={"kind": event.get("kind"), "stage": "queued", "text": str(body)[:600],
                            "scenario_ref": _hex(scenario_id, 16) if scenario_id else None})
    except Exception:
        log.debug("scenario notice forward skipped")


def forward_run(result, *, loop, started):
    """One heartbeat per runner invocation; stdout status lines carry no time."""
    try:
        root = str(Path(__file__).resolve().parents[2])
        if root not in sys.path:
            sys.path.append(root)
        from observability.job_runs import emit_job_run
        result = result or {}
        status = str(result.get("status") or "unknown")
        # A deliberate halt reports blocked/new_risk_halted every run; only a runtime
        # failure (it carries error_type) is an error. Lock contention is expected.
        failed = result.get("error_type") and status != "lock_busy"
        emit_job_run("btc-scenario", market="CRYPTO", mode=loop, started=started,
                     status="ERROR" if failed else status.upper(),
                     reason=result.get("reason"), error=result.get("error_type") if failed else None,
                     summary=result)
    except Exception:
        log.debug("scenario run forward skipped")
