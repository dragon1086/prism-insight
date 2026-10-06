"""One-shot demo recovery authority and mandatory, local evidence journal.

This never clears the circuit breaker. A consumed attempt cannot be retried,
including a zero-fill cancellation, unknown submit, restart, or profitable exit.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import math
from pathlib import Path
import uuid

RISK_FRACTION = .005
_wire = ContextVar("recovery_wire", default=None)
TIMEFRAMES = ("15m", "30m", "1h", "4h", "12h", "1d", "1w")


def _number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def encoded(value):
    result = json.dumps(value, sort_keys=True, allow_nan=False, ensure_ascii=False)
    if len(result.encode()) > 256 * 1024:
        raise ValueError("recovery_journal_bound")
    return result


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def ensure_schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS llm_scenario_recovery_events (
        event_id TEXT PRIMARY KEY, slot INTEGER, scenario_id TEXT,
        kind TEXT NOT NULL, observed_at REAL NOT NULL, body TEXT NOT NULL)""")


def journal(conn, kind, body, now, *, slot=None, scenario_id=None):
    if slot is not None and "input_id" not in body:
        row = conn.execute("SELECT context FROM llm_scenario_decisions WHERE slot=?", (slot,)).fetchone()
        if row and row[0]:
            body = dict(body, input_id=json.loads(row[0]).get("input_id"))
    conn.execute("INSERT INTO llm_scenario_recovery_events VALUES(?,?,?,?,?,?)",
                 (str(uuid.uuid4()), slot, scenario_id, kind, now, encoded(body)))


@contextmanager
def capture_wire(conn, slot, clock):
    token = _wire.set((conn, slot, clock))
    try:
        yield
    finally:
        _wire.reset(token)


def record_model_request(*, system_prompt, user_prompt, response_schema,
                         model=None, effort=None, fast=None):
    capture = _wire.get()
    if capture is None:
        return
    conn, slot, clock = capture
    try:
        body = {"system_prompt": system_prompt, "user_prompt": user_prompt,
                "response_schema": response_schema, "model": model,
                "effort": effort, "fast": fast}
        body["request_hash"] = digest(body)
        journal(conn, "MODEL_REQUEST", body, clock(), slot=slot)
        conn.commit()
    except Exception:
        conn.rollback()


def record_model_error(code):
    capture = _wire.get()
    if capture is None:
        return
    conn, slot, clock = capture
    try:
        safe = code if code in {"oauth_model_failed", "late_response", "invalid_json",
                              "invalid_contract_context", "invalid_input"} else "model_contract_failed"
        journal(conn, "MODEL_ERROR", {"code": safe}, clock(), slot=slot)
        conn.commit()
    except Exception:
        conn.rollback()


def record_model_wire(raw_text, *, model=None, effort=None, fast=None):
    capture = _wire.get()
    if capture is None:
        return
    conn, slot, clock = capture
    try:
        if not isinstance(raw_text, str) or len(raw_text.encode()) > 128 * 1024:
            raise ValueError("recovery_wire_bound")
        journal(conn, "MODEL_RAW", {"raw": raw_text,
                "sha256": hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
                "model": model, "effort": effort, "fast": fast},
                clock(), slot=slot)
        conn.commit()  # Must survive invalid JSON and post-call protection races.
    except Exception:
        conn.rollback()  # Missing mandatory RAW denies PROBE, not protective EXIT.


def hard_guards(state, ctx):
    breaker = state.get("breaker", {})
    return (breaker.get("blocked") is True
            and set(breaker.get("reasons", [])) == {"three_losses"}
            and ctx.get("legacy_fenced") is False
            and ctx.get("protection_ok") is True
            and ctx.get("accounting_status") == "confirmed"
            and ctx.get("recovery_hard_blocked", ctx.get("new_risk_blocked")) is False)


def observing_allowed(state, ctx):
    recovery = state.get("recovery", {})
    return (hard_guards(state, ctx) and state.get("active") is None
            and ctx.get("positions") == [] and ctx.get("pending_entries") == []
            and recovery.get("phase", "OBSERVING") == "OBSERVING")


def numeric_market(snapshot):
    """Compact stable market fields only; time/progress counters cannot qualify."""
    accepted = {"open", "high", "low", "close", "ma10", "ma35", "ma10_slope",
                "ma35_slope", "slope10", "slope35", "spread", "spread_pct",
                "price", "volume", "volume_ratio"}
    result = {}
    def visit(value, path, depth=0):
        if depth > 5 or not isinstance(value, dict):
            return
        for key, child in sorted(value.items()):
            if key in accepted and _number(child):
                result[path + "." + key] = child
            elif isinstance(child, dict):
                visit(child, path + "." + key, depth + 1)
    for tf in TIMEFRAMES:
        visit(snapshot.get("timeframes", {}).get(tf, {}), "timeframes." + tf)
    if not result or len(result) > 512:
        raise ValueError("recovery_market_evidence_missing")
    return result


def versions():
    from live.scenario_llm import MODEL, EFFORT, SYSTEM_PROMPT
    root = Path(__file__).resolve().parents[1]
    names = ("live/scenario_recovery.py", "live/scenario_runtime.py",
             "live/scenario_llm.py", "live/scenario_contract.py",
             "live/scenario_broker.py", "live/scenario_execution.py",
             "core/llm_scenario.py")
    result = {"model": MODEL, "effort": EFFORT, "prompt_hash": digest(SYSTEM_PROMPT),
            "hash_basis": "OBSERVED_DISK_SOURCE_NOT_LOADED_CODE_ATTESTATION",
            "source_hashes": {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                              for name in names}}
    try:
        from live.scenario_provenance import _manifest
        manifest = _manifest()
        result["loaded_manifest"] = {key: manifest[key] for key in (
            "policy_hash", "execution_hash", "loaded_code_status",
            "judgment_loaded_code_status", "execution_loaded_code_status",
            "loaded_code_checks", "git_revision", "git_revision_status")}
    except Exception:
        result["loaded_manifest"] = {"loaded_code_status": "UNKNOWN"}
    return result


def historical_outcomes(conn, state):
    """Reference confirmed accounting only, never infer why a trade lost."""
    result = []
    ids = state.get("breaker", {}).get("completed_ids", [])[-3:]
    for ident in ids:
        item = {"scenario_id": ident, "kind": "ACCOUNTING_ONLY_NOT_CAUSE", "status": "UNKNOWN"}
        row = conn.execute("SELECT evidence FROM llm_scenario_settlements WHERE scenario_id=?", (ident,)).fetchone()
        if row:
            try:
                evidence = json.loads(row[0])
                fields = ("gross_pnl", "fees", "funding_net", "net_pnl")
                flags = ("flat_confirmed", "orders_terminal", "executions_complete", "fees_complete", "funding_complete")
                if (evidence.get("scenario_id") == ident
                        and all(evidence.get(flag) is True for flag in flags)
                        and all(_number(evidence.get(key)) for key in fields)
                        and isinstance(evidence.get("execution_ids"), list) and evidence["execution_ids"]
                        and math.isclose(evidence["net_pnl"], evidence["gross_pnl"] - evidence["fees"] + evidence["funding_net"], abs_tol=1e-8)):
                    item.update(status="CONFIRMED", evidence_hash=digest(evidence),
                                **{key: evidence[key] for key in fields})
            except (ValueError, TypeError, OverflowError):
                pass
        result.append(item)
    return result


def prepare_input(conn, state, context, snapshot, slot, now):
    values = numeric_market(snapshot)
    version_evidence = versions()
    recovery = state.setdefault("recovery", {"phase": "OBSERVING", "started_at": now})
    first = "baseline" not in recovery
    if first:
        recovery.update(baseline=values, baseline_slot=slot,
                        baseline_input_id=context["input_id"])
    baseline = recovery["baseline"]
    changes = {path: {"before": baseline[path], "now": value}
               for path, value in values.items() if path in baseline and baseline[path] != value}
    metadata = {"phase": "OBSERVING", "risk_fraction": RISK_FRACTION,
                "baseline_ref": recovery["baseline_input_id"],
                "baseline_kind": "FIRST_POST_HALT_OBSERVATION",
                "eligible": bool(changes) and not first, "consumed": False,
                "entry_deadline": (slot + 1) * 300,
                "loaded_code_status": version_evidence["loaded_manifest"]["loaded_code_status"],
                "historical_outcomes": historical_outcomes(conn, state),
                "baseline_slot": recovery["baseline_slot"], "first_observation": first,
                "baseline": baseline, "current": values, "changed_evidence": changes,
                "one_attempt_only": True, "automatic_normal_resume": False}
    context["recovery"] = metadata
    context["recovery_contract_version"] = 1
    context["scenario_risk_fraction"] = RISK_FRACTION
    journal(conn, "INPUT", {"input_id": context["input_id"],
            "snapshot_hash": digest(snapshot), "context_hash": digest(context),
            "decision_slot": slot, "versions": version_evidence, "recovery": metadata}, now, slot=slot)
    return metadata


def validate_metadata(value):
    if not isinstance(value, dict) or set(value) != {
            "decision", "reason", "changed_evidence", "counterevidence", "invalidation"}:
        raise ValueError("recovery_metadata_invalid")
    if value["decision"] not in {"OBSERVE", "PROBE"}:
        raise ValueError("recovery_metadata_invalid")
    for field in ("reason", "counterevidence", "invalidation"):
        text = value[field]
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 600:
            raise ValueError("recovery_metadata_invalid")
    paths = value["changed_evidence"]
    if (not isinstance(paths, list) or len(paths) > 8
            or any(not isinstance(path, str) or len(path) > 160 for path in paths)
            or len(set(paths)) != len(paths)):
        raise ValueError("recovery_metadata_invalid")
    return value


def assess(conn, state, context, payload, slot, now):
    meta = validate_metadata(payload.get("recovery"))
    journal(conn, "DECISION", {"input_id": context["input_id"],
            "proposal": payload, "metadata": meta}, now, slot=slot,
            scenario_id=payload.get("scenario_id"))
    conn.commit()  # Preserve rejected reasoning, not only granted permits.
    if payload.get("action") == "WAIT" and meta["decision"] == "OBSERVE":
        return False
    data = context["recovery"]
    paths = meta["changed_evidence"]
    if payload.get("action") != "OPEN" or meta["decision"] != "PROBE":
        raise ValueError("recovery_action_mismatch")
    if data["first_observation"] or slot <= data["baseline_slot"]:
        raise ValueError("recovery_first_observation")
    if not paths or any(path not in data["changed_evidence"] for path in paths):
        raise ValueError("recovery_no_changed_evidence")
    if not conn.execute("SELECT 1 FROM llm_scenario_recovery_events WHERE slot=? AND kind='MODEL_RAW'", (slot,)).fetchone():
        raise ValueError("recovery_missing_raw")
    if not conn.execute("SELECT 1 FROM llm_scenario_recovery_events WHERE slot=? AND kind='MODEL_REQUEST'", (slot,)).fetchone():
        raise ValueError("recovery_missing_request")
    if data.get("loaded_code_status") == "MIXED":
        raise ValueError("recovery_mixed_loaded_code")
    if payload.get("chase") != {"max_bps": 0, "max_reprices": 0}:
        raise ValueError("recovery_chase_forbidden")
    expiry = payload.get("expires_at")
    if not _number(expiry) or not now < expiry <= data["entry_deadline"]:
        raise ValueError("recovery_entry_deadline_invalid")
    return True


def consume(conn, state, payload, context, slot, now):
    recovery = state["recovery"]
    budget = context["initial_equity"] * RISK_FRACTION
    risk = payload["risk"]["total_risk"]
    if (recovery["phase"] != "OBSERVING" or not _number(risk) or risk > budget
            or not payload.get("entries") or now >= (slot + 1) * 300):
        raise ValueError("recovery_probe_risk_invalid")
    recovery.update(phase="CONSUMED", scenario_id=payload["scenario_id"],
                    action_id=payload["action_id"], side=payload["side"],
                    initial_equity=context["initial_equity"], budget=budget,
                    entry_deadline=min(payload["expires_at"], (slot + 1) * 300),
                    initial_entries=payload["entries"], consumed_at=now,
                    permit_id=str(uuid.uuid4()), input_id=context["input_id"])
    journal(conn, "PERMIT_CONSUMED", recovery, now, slot=slot,
            scenario_id=payload["scenario_id"])


def risk_fraction(state, scenario_id):
    recovery = state.get("recovery", {})
    return RISK_FRACTION if (scenario_id and recovery.get("scenario_id") == scenario_id
                            and recovery.get("phase") in {"CONSUMED", "DONE_REVIEW_REQUIRED"}) else .02


def authorize_pending(state, active, now):
    permit = state.get("recovery", {})
    breaker = state.get("breaker", {})
    return bool(active and permit.get("phase") == "CONSUMED"
                and breaker.get("blocked") is True
                and set(breaker.get("reasons", [])) == {"three_losses"}
                and permit.get("scenario_id") == active.get("scenario_id")
                and permit.get("side") == active.get("side")
                and permit.get("initial_equity") == active.get("initial_equity")
                and _number(permit.get("entry_deadline")) and now < permit["entry_deadline"])


def authorize_entry(state, payload, ctx, now):
    permit = state.get("recovery", {})
    if (not authorize_pending(state, state.get("active"), now)
            or not hard_guards(state, ctx) or payload.get("action") != "OPEN"
            or ctx.get("initial_equity") != permit.get("initial_equity")
            or payload.get("action_id") != permit.get("action_id")
            or payload.get("scenario_id") != permit.get("scenario_id")
            or payload.get("side") != permit.get("side")):
        return False
    originals = {row["id"]: row for row in permit["initial_entries"]}
    entries = payload.get("entries", [])
    if (not isinstance(entries, list) or any(not isinstance(row, dict) for row in entries)
            or any(not isinstance(row.get("id"), str) for row in entries)
            or len({row["id"] for row in entries}) != len(entries)):
        return False
    return bool(entries and all(row.get("id") in originals
                and set(row) == set(originals[row["id"]])
                and all(value == originals[row["id"]].get(key) for key, value in row.items() if key != "quantity")
                and _number(row.get("quantity")) and 0 < row["quantity"] <= originals[row["id"]]["quantity"]
                for row in entries))


def settled(conn, state, settlement, now):
    recovery = state.get("recovery", {})
    if recovery.get("scenario_id") != settlement["scenario_id"]:
        return
    recovery.update(phase="DONE_REVIEW_REQUIRED", settled_at=now)
    journal(conn, "SETTLEMENT", {"permit_id": recovery["permit_id"],
            "settlement": settlement, "automatic_normal_resume": False}, now,
            scenario_id=settlement["scenario_id"])
