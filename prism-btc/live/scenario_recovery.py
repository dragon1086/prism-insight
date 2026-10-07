"""Host-owned demo recovery permits and mandatory local evidence journal.

Legacy one-shot permits remain unchanged. Opt-in policy v2 re-observes only
after exact settlement and grades bounded stage evidence without clearing the
breaker. Unknown submission is never a new opportunity or a retry permit.
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
STAGE_FRACTIONS = (.005, .01, .02)
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


def _confirmed(settlement, scenario_id):
    fields = ("gross_pnl", "fees", "funding_net", "net_pnl")
    ids = settlement.get("execution_ids")
    return (settlement.get("scenario_id") == scenario_id
            and all(settlement.get(k) is True for k in (
                "flat_confirmed", "orders_terminal", "executions_complete", "fees_complete", "funding_complete"))
            and all(_number(settlement.get(k)) for k in fields)
            and isinstance(ids, list) and all(isinstance(i, str) and i for i in ids)
            and len(ids) == len(set(ids))
            and (bool(ids) or (settlement.get("no_fills_confirmed") is True
                              and all(settlement[k] == 0 for k in fields)))
            and math.isclose(settlement["net_pnl"], settlement["gross_pnl"] - settlement["fees"]
                             + settlement["funding_net"], abs_tol=1e-8))


def _stage_evidence(equity):
    return {"filled_count": 0, "net_pnl": 0., "peak_net_pnl": 0.,
            "max_realized_drawdown": 0., "stage_start_equity": equity}


def migrate(conn, state, ctx, now):
    """Called under runtime mutation lock; never resets the breaker or financials."""
    old = state.get("recovery", {})
    if (old.get("policy_version", 1) not in (1,) or state.get("active") is not None
            or not hard_guards(state, ctx) or ctx.get("positions") != []
            or ctx.get("pending_entries") != []
            or old.get("phase", "OBSERVING") not in {"OBSERVING", "DONE_REVIEW_REQUIRED"}
            or conn.execute("SELECT 1 FROM llm_scenario_intents WHERE status IS NULL OR status!='TERMINAL'").fetchone()):
        return False
    equity = ctx.get("initial_equity")
    if not _number(equity) or equity <= 0:
        return False
    previous = None
    if old.get("phase") == "DONE_REVIEW_REQUIRED":
        row = conn.execute("SELECT evidence FROM llm_scenario_settlements WHERE scenario_id=?",
                           (old.get("scenario_id"),)).fetchone()
        if not row:
            return False
        try:
            previous = json.loads(row[0])
            if not _confirmed(previous, old.get("scenario_id")) or not old.get("permit_id"):
                return False
        except (TypeError, ValueError):
            return False
    new = {"policy_version": 2, "epoch_id": str(uuid.uuid4()), "stage": 0,
           "phase": "OBSERVING", "started_at": now, "epoch_start_equity": equity,
           "stage_evidence": _stage_evidence(equity),
           "previous_permit_id": old.get("permit_id"),
           "previous_scenario_id": old.get("scenario_id")}
    journal(conn, "MIGRATION", {"before": old, "after": new,
            "prior_settlement_hash": digest(previous) if previous else None}, now)
    state["recovery"] = new
    state["version"] += 1
    return True


def offered_stage(recovery):
    stage = recovery.get("stage", 0)
    evidence = recovery.get("stage_evidence", {})
    if recovery.get("policy_version") == 2:
        fields = ("net_pnl", "peak_net_pnl", "max_realized_drawdown")
        if (type(stage) is not int or stage not in range(3) or not isinstance(evidence, dict)
                or type(evidence.get("filled_count")) is not int or evidence["filled_count"] < 0
                or any(not _number(evidence.get(key)) or evidence[key] < 0 for key in fields)
                or evidence["peak_net_pnl"] < evidence["net_pnl"]):
            raise ValueError("normalization_state_invalid")
        empty = evidence["filled_count"] == 0 and all(evidence[key] == 0 for key in fields)
        equity = evidence.get("stage_start_equity")
        if ((equity is None and not empty) or (equity is not None and
                (not _number(equity) or equity <= 0))
                or (evidence["filled_count"] == 0 and not empty)):
            raise ValueError("normalization_state_invalid")
    if (recovery.get("policy_version") == 2 and stage < 2
            and evidence.get("filled_count", 0) >= 3 and evidence.get("net_pnl", 0) > 0
            and evidence.get("max_realized_drawdown", float("inf")) <=
                STAGE_FRACTIONS[stage] * evidence.get("stage_start_equity", 0)):
        return stage + 1
    return stage


def offered_fraction(state):
    recovery = state.get("recovery", {})
    return STAGE_FRACTIONS[offered_stage(recovery)] if recovery.get("policy_version") == 2 else RISK_FRACTION


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
    if recovery.get("policy_version") == 2:
        metadata.update(current_stage=recovery["stage"], offered_stage=offered_stage(recovery),
                        promotion_evidence=recovery["stage_evidence"],
                        risk_fraction=offered_fraction(state), one_attempt_only=False,
                        automatic_normal_resume=True, policy_version=2)
    context["recovery"] = metadata
    context["recovery_contract_version"] = recovery.get("policy_version", 1)
    context["scenario_risk_fraction"] = metadata["risk_fraction"]
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
    if data.get("offered_stage") != 2 and payload.get("chase") != {"max_bps": 0, "max_reprices": 0}:
        raise ValueError("recovery_chase_forbidden")
    expiry = payload.get("expires_at")
    if not _number(expiry) or not now < expiry <= data["entry_deadline"]:
        raise ValueError("recovery_entry_deadline_invalid")
    return True


def consume(conn, state, payload, context, slot, now):
    recovery = state["recovery"]
    fraction = offered_fraction(state)
    budget = context["initial_equity"] * fraction
    risk = payload["risk"]["total_risk"]
    if (recovery["phase"] != "OBSERVING" or not _number(risk) or risk > budget
            or not payload.get("entries") or now >= (slot + 1) * 300):
        raise ValueError("recovery_probe_risk_invalid")
    if recovery.get("policy_version") == 2:
        if recovery["stage_evidence"].get("stage_start_equity") is None:
            recovery["stage_evidence"]["stage_start_equity"] = context["initial_equity"]
        offered = offered_stage(recovery)
        if offered != recovery["stage"]:
            journal(conn, "STAGE_TRANSITION", {"before": recovery["stage"], "after": offered,
                    "reason": "confirmed_stage_performance_and_fresh_probe",
                    "evidence": recovery["stage_evidence"], "input_id": context["input_id"]},
                    now, slot=slot, scenario_id=payload["scenario_id"])
            recovery.update(stage=offered, stage_evidence=_stage_evidence(context["initial_equity"]))
        recovery["risk_fraction"] = fraction
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
    if (scenario_id and recovery.get("scenario_id") == scenario_id
            and recovery.get("policy_version", 1) == 1
            and recovery.get("phase") in {"CONSUMED", "DONE_REVIEW_REQUIRED"}):
        return RISK_FRACTION
    if scenario_id and recovery.get("scenario_id") == scenario_id and recovery.get("policy_version") == 2:
        if not managed_permit(state, state.get("active")):
            # Conservative valuation remains available for protective operations;
            # permission helpers deny all new risk for a corrupt managed permit.
            return RISK_FRACTION
    return recovery.get("risk_fraction", RISK_FRACTION) if (scenario_id and recovery.get("scenario_id") == scenario_id
                            and recovery.get("phase") in {"CONSUMED", "DONE_REVIEW_REQUIRED"}) else .02


def managed_permit(state, active):
    permit = state.get("recovery", {})
    if (not active or permit.get("phase") != "CONSUMED" or not permit.get("permit_id")
            or permit.get("scenario_id") != active.get("scenario_id")
            or permit.get("side") != active.get("side")
            or permit.get("initial_equity") != active.get("initial_equity")):
        return None
    fraction = permit.get("risk_fraction", RISK_FRACTION)
    equity = permit.get("initial_equity")
    if (not _number(equity) or equity <= 0 or fraction not in STAGE_FRACTIONS
            or not _number(permit.get("budget"))
            or not math.isclose(permit["budget"], equity * fraction, abs_tol=1e-8)):
        return None
    if permit.get("policy_version", 1) not in (1, 2):
        return None
    if permit.get("policy_version", 1) == 1 and fraction != RISK_FRACTION:
        return None
    if permit.get("policy_version") == 2 and (
            type(permit.get("stage")) is not int or permit["stage"] not in range(3)
            or fraction != STAGE_FRACTIONS[permit["stage"]]):
        return None
    return permit


def normal_permission(state, active, ctx):
    permit = managed_permit(state, active)
    return bool(permit and permit.get("policy_version") == 2
                and permit.get("stage") == 2 and hard_guards(state, ctx))


def authorize_pending(state, active, now):
    permit = managed_permit(state, active)
    breaker = state.get("breaker", {})
    deadline = (active.get("expires_at") if permit and permit.get("policy_version") == 2
                and permit.get("stage") == 2 else (permit or {}).get("entry_deadline"))
    return bool(permit
                and breaker.get("blocked") is True
                and set(breaker.get("reasons", [])) == {"three_losses"}
                and permit.get("scenario_id") == active.get("scenario_id")
                and permit.get("side") == active.get("side")
                and permit.get("initial_equity") == active.get("initial_equity")
                and _number(deadline) and now < deadline)


def authorize_entry(state, payload, ctx, now):
    permit = state.get("recovery", {})
    if normal_permission(state, state.get("active"), ctx):
        return bool(authorize_pending(state, state.get("active"), now)
                    and payload.get("action") in {"OPEN", "ADJUST"}
                    and (payload.get("action") != "OPEN"
                         or payload.get("action_id") == permit.get("action_id"))
                    and payload.get("scenario_id") == permit["scenario_id"]
                    and payload.get("side") == permit["side"]
                    and ctx.get("initial_equity") == permit["initial_equity"]
                    and _number(payload.get("expires_at")) and now < payload["expires_at"])
    if (not authorize_pending(state, state.get("active"), now)
            or not hard_guards(state, ctx) or payload.get("action") != "OPEN"
            or payload.get("chase") != {"max_bps": 0, "max_reprices": 0}
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
    if recovery.get("policy_version") == 2:
        if recovery.get("phase") != "CONSUMED" or not _confirmed(settlement, recovery["scenario_id"]):
            return
        if conn.execute("SELECT 1 FROM llm_scenario_settlements WHERE scenario_id=?", (settlement["scenario_id"],)).fetchone():
            return
        evidence = recovery["stage_evidence"]
        old_stage = recovery["stage"]
        if settlement["execution_ids"]:
            pnl = settlement["net_pnl"]
            if pnl < 0:
                # A loss ends this checkpoint immediately. Consequently the
                # surviving stage aggregates describe non-loss settlements,
                # not an intratrade or account-equity drawdown backtest.
                recovery["stage"] = max(0, old_stage - 1)
                # Do not invent account NAV from scenario PnL. The next accepted
                # OPEN supplies fresh, actual account equity for this stage.
                recovery["stage_evidence"] = _stage_evidence(None)
                journal(conn, "STAGE_TRANSITION", {"before": old_stage, "after": recovery["stage"],
                        "reason": "net_loss", "settlement": settlement, "previous_evidence": evidence},
                        now, scenario_id=settlement["scenario_id"])
            else:
                evidence["filled_count"] += 1
                evidence["net_pnl"] += pnl
                evidence["peak_net_pnl"] = max(evidence["peak_net_pnl"], evidence["net_pnl"])
                evidence["max_realized_drawdown"] = max(evidence["max_realized_drawdown"],
                    evidence["peak_net_pnl"] - evidence["net_pnl"])
        journal(conn, "SETTLEMENT", {"permit_id": recovery["permit_id"], "settlement": settlement,
                "automatic_normal_resume": True, "stage_evidence": recovery["stage_evidence"]},
                now, scenario_id=settlement["scenario_id"])
        previous = {"permit_id": recovery["permit_id"], "scenario_id": recovery["scenario_id"]}
        keep = {key: recovery[key] for key in ("policy_version", "epoch_id", "stage", "stage_evidence", "started_at", "epoch_start_equity")}
        keep.update(phase="OBSERVING", previous_permit_id=previous["permit_id"],
                    previous_scenario_id=previous["scenario_id"], settled_at=now)
        journal(conn, "REARM", {"previous": previous, "after": keep,
                "settlement_hash": digest(settlement)}, now, scenario_id=settlement["scenario_id"])
        state["recovery"] = keep
        return
    recovery.update(phase="DONE_REVIEW_REQUIRED", settled_at=now)
    journal(conn, "SETTLEMENT", {"permit_id": recovery["permit_id"],
            "settlement": settlement, "automatic_normal_resume": False}, now,
            scenario_id=settlement["scenario_id"])
