"""Host-owned demo recovery permits and mandatory local evidence journal.

Legacy one-shot permits remain unchanged. Opt-in policy v2 re-observes only
after exact settlement and grades bounded stage evidence without clearing the
breaker. Unknown submission is never a new opportunity or a retry permit.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import uuid
from live.scenario_analysis_frames import allowed_evidence_path
from live.scenario_review_storage import review_storage, review_text

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
    conn.execute("CREATE INDEX IF NOT EXISTS llm_scenario_recovery_source_lookup ON llm_scenario_recovery_events(scenario_id,kind)")
    conn.execute("CREATE INDEX IF NOT EXISTS llm_scenario_recovery_slot_kind ON llm_scenario_recovery_events(slot,kind)")


def _reference_storage(conn, kind, slot, sequence, original, fallback):
    """Use a reference only after its durable source is rechecked under the write lock."""
    if type(slot) is not int or slot < 0 or type(sequence) is not int or sequence < 0:
        return fallback
    try:
        from utils.btc_recovery_references import compact, pack, read_recovery
        from utils.btc_review_event_references import read_event
        rows = conn.execute('SELECT body FROM llm_scenario_review_events WHERE slot=? AND sequence=? LIMIT 2',
                            (slot, sequence)).fetchall()
        if len(rows) != 1:
            return fallback
        candidate = compact(read_event(rows[0][0], conn, slot, sequence, decode_legacy=review_text), original, slot, sequence)
        if candidate == original:
            return fallback
        candidate = pack(candidate)
        fallback_bytes = len(fallback.encode('utf-8')) if isinstance(fallback, str) else len(fallback)
        if len(candidate) >= fallback_bytes:
            return fallback
        if not conn.in_transaction:
            conn.execute('BEGIN IMMEDIATE')
        if read_recovery(candidate, conn, slot, decode_legacy=review_text, expected_kind=kind) == original:
            return candidate
    except Exception:
        return fallback
    return fallback


def journal(conn, kind, body, now, *, slot=None, scenario_id=None, review_sequence=None):
    if slot is not None and "input_id" not in body:
        row = conn.execute("SELECT context FROM llm_scenario_decisions WHERE slot=?", (slot,)).fetchone()
        if row and row[0]:
            body = dict(body, input_id=json.loads(row[0]).get("input_id"))
    event_id = str(uuid.uuid4())
    text = encoded(body)
    stored = review_storage(text) if kind in {"MODEL_REQUEST", "MODEL_RAW"} else text
    if kind in {"MODEL_REQUEST", "MODEL_RAW"} and body.get('calculated_review_version') == 1:
        stored = _reference_storage(conn, kind, slot, review_sequence, text, stored)
    conn.execute("INSERT INTO llm_scenario_recovery_events VALUES(?,?,?,?,?,?)",
                 (event_id, slot, scenario_id, kind, now, stored))
    return event_id


def compact_input_event(conn, event_id, slot):
    """Compact only within the transaction that saved the matching decision input."""
    if not conn.in_transaction:
        raise ValueError('recovery_input_transaction_required')
    row = conn.execute("SELECT r.body,d.context FROM llm_scenario_recovery_events r "
                       "JOIN llm_scenario_decisions d ON d.slot=r.slot "
                       "WHERE r.event_id=? AND r.slot=? AND r.kind='INPUT'", (event_id, slot)).fetchone()
    if row is None:
        return False
    original, context = row
    try:
        from utils.btc_recovery_references import compact_input, pack, read_recovery
        candidate = compact_input(context, original, slot)
        if candidate == original:
            return False
        candidate = pack(candidate)
        if len(candidate) >= len(original.encode('utf-8')):
            return False
        if read_recovery(candidate, conn, slot, decode_legacy=review_text, expected_kind='INPUT') != original:
            return False
    except (TypeError, ValueError, OverflowError):
        return False
    return conn.execute("UPDATE llm_scenario_recovery_events SET body=? WHERE event_id=? AND body=?",
                        (candidate, event_id, original)).rowcount == 1


@contextmanager
def capture_wire(conn, slot, clock):
    token = _wire.set((conn, slot, clock))
    try:
        yield
    finally:
        _wire.reset(token)


def record_model_request(*, system_prompt, user_prompt, response_schema,
                         model=None, effort=None, fast=None, input_projection=None):
    capture = _wire.get()
    if capture is None:
        return
    conn, slot, clock = capture
    try:
        body = {"system_prompt": system_prompt, "user_prompt": user_prompt,
                "response_schema": response_schema, "model": model,
                "effort": effort, "fast": fast}
        if input_projection is not None:
            body["input_projection"] = input_projection
        body["request_hash"] = digest(body)
        journal(conn, "MODEL_REQUEST", body, clock(), slot=slot)
        conn.commit()
    except Exception:
        conn.rollback()


def record_model_transport_attempt(*, attempt, status, reason, elapsed_seconds, retry):
    """Best-effort sanitized HTTP telemetry; never an order or recovery permit."""
    capture = _wire.get()
    if capture is None:
        return False
    conn, slot, clock = capture
    try:
        if (type(attempt) is not int or attempt not in (1, 2)
                or (status is not None and (type(status) is not int or not 100 <= status <= 599))
                or not _number(elapsed_seconds) or elapsed_seconds < 0
                or type(retry) is not bool):
            return False
        safe = reason if reason in {"transport_failed", "http_response", "http_error",
            "upstream_transfer_truncated", "deadline_exceeded"} else "transport_failed"
        body = {"attempt": attempt, "status": status, "reason": safe,
                "elapsed_seconds": round(elapsed_seconds, 6), "retry": bool(retry)}
        journal(conn, "MODEL_TRANSPORT_ATTEMPT", body, clock(), slot=slot)
        conn.commit()
        return True
    except Exception:
        try:
            conn.rollback()
        except Exception:
            return False  # Even a closed journal cannot invalidate a safe response.
        return False


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
    permitted_breaker = (breaker.get("blocked") is True
                         and set(breaker.get("reasons", [])) == {"three_losses"})
    if state.get('incident_epoch') and not breaker.get('blocked') and not breaker.get('reasons'):
        permitted_breaker = True
    return (permitted_breaker
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


def _stage_evidence(equity, *, rule_version=2, window_origin=None):
    result = {"filled_count": 0, "net_pnl": 0., "peak_net_pnl": 0.,
            "max_realized_drawdown": 0., "stage_start_equity": equity,
            "promotion_rule_version": rule_version, "positive_count": 0}
    if rule_version == 3:
        result.update(window=[], window_origin=window_origin, net_wins=0., net_losses=0.)
    return result


def _window_metrics(rows):
    if not isinstance(rows, list) or len(rows) > 6:
        raise ValueError("normalization_state_invalid")
    seen = set()
    total = peak = drawdown = wins = losses = Decimal(0)
    positives = 0
    previous_time = 0
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {"scenario_id", "settlement_hash", "net_pnl", "settled_at"}
                or not isinstance(row["scenario_id"], str) or not row["scenario_id"]
                or row["scenario_id"] in seen or not isinstance(row["settlement_hash"], str)
                or len(row["settlement_hash"]) != 64
                or any(c not in '0123456789abcdef' for c in row["settlement_hash"])
                or not _number(row["net_pnl"]) or not _number(row["settled_at"])
                or row["settled_at"] <= 0 or row["settled_at"] < previous_time):
            raise ValueError("normalization_state_invalid")
        seen.add(row["scenario_id"])
        previous_time = row["settled_at"]
        pnl = Decimal(str(row["net_pnl"]))
        total += pnl
        wins += max(pnl, Decimal(0))
        losses += max(-pnl, Decimal(0))
        positives += int(pnl > 0)
        peak = max(peak, total)
        drawdown = max(drawdown, peak-total)
    result = dict(filled_count=len(rows), positive_count=positives, net_pnl=float(total),
        peak_net_pnl=float(peak), max_realized_drawdown=float(drawdown),
        net_wins=float(wins), net_losses=float(losses))
    if any(not _number(value) for value in result.values()):
        raise ValueError("normalization_state_invalid")
    return result


def _validate_canonical_window(conn, recovery):
    """Bind the complete window, not merely a cherry-picked subset of true rows."""
    evidence = recovery["stage_evidence"]
    try:
        if recovery.get("phase") == "CONSUMED":
            rows = conn.execute("SELECT body FROM llm_scenario_recovery_events WHERE kind='PERMIT_CONSUMED' AND scenario_id=?",
                                (recovery.get("scenario_id"),)).fetchall()
            proof = json.loads(rows[0][0]) if len(rows) == 1 else {}
            if (all(proof.get(key) == recovery.get(key) for key in
                    ("policy_version", "epoch_id", "stage", "scenario_id", "permit_id"))
                    and proof.get("stage_evidence") == evidence):
                return
        elif recovery.get("phase") == "OBSERVING":
            previous_time = 0
            previous = recovery.get("previous_scenario_id")
            if previous is not None:
                rows = conn.execute("SELECT observed_at,body FROM llm_scenario_recovery_events WHERE kind='SETTLEMENT' AND scenario_id=?",
                                    (previous,)).fetchall()
                if len(rows) != 1:
                    raise ValueError("normalization_state_invalid")
                previous_time, body = rows[0][0], json.loads(rows[0][1])
                prior = body.get("stage_evidence") or {}
                if prior.get("window_origin") == evidence.get("window_origin"):
                    rearms = conn.execute("SELECT body FROM llm_scenario_recovery_events WHERE kind='REARM' AND scenario_id=?",
                                          (previous,)).fetchall()
                    after = json.loads(rearms[0][0]).get("after", {}) if len(rearms) == 1 else {}
                    if (prior != evidence or after.get("stage_evidence") != evidence
                            or any(after.get(key) != recovery.get(key) for key in ("epoch_id", "stage", "policy_version"))):
                        raise ValueError("normalization_state_invalid")
                    return
            # Only a real recorded migration may establish a new empty origin.
            record = conn.execute("SELECT kind,observed_at,body FROM llm_scenario_recovery_events WHERE event_id=?",
                                  (recovery.get("bootstrap_evidence_event_id"),)).fetchone()
            if record and record[1] >= previous_time:
                body = json.loads(record[2])
                expected = body.get("after", {})
                if record[0] == "MIGRATION":
                    valid = (expected.get("epoch_id") == recovery.get("epoch_id")
                        and expected.get("stage") == recovery.get("stage")
                        and expected.get("stage_evidence") == evidence)
                elif record[0] == "PROMOTION_RULE_MIGRATION":
                    valid = (body.get("epoch_id") == recovery.get("epoch_id")
                        and body.get("stage") == recovery.get("stage") and expected == evidence)
                else:
                    valid = False
                if valid and evidence.get("window") == []:
                    return
    except (AttributeError, TypeError, KeyError, ValueError):
        pass
    raise ValueError("normalization_state_invalid")


def _validate_window_sources(conn, recovery):
    evidence = recovery["stage_evidence"]
    if evidence.get("promotion_rule_version") != 3:
        return
    _window_metrics(evidence.get("window"))
    _validate_canonical_window(conn, recovery)
    for row in evidence["window"]:
        stored = conn.execute("SELECT evidence FROM llm_scenario_settlements WHERE scenario_id=?",
                              (row["scenario_id"],)).fetchone()
        try:
            proof = json.loads(stored[0]) if stored else None
            valid = (isinstance(proof, dict) and _confirmed(proof, row["scenario_id"])
                     and bool(proof["execution_ids"]) and digest(proof) == row["settlement_hash"]
                     and proof["net_pnl"] == row["net_pnl"])
            events = conn.execute("SELECT observed_at,body FROM llm_scenario_recovery_events WHERE kind='SETTLEMENT' AND scenario_id=?",
                                  (row["scenario_id"],)).fetchall()
            valid = valid and len(events) == 1 and events[0][0] == row["settled_at"] and json.loads(events[0][1]).get("settlement") == proof
            permits = conn.execute("SELECT body FROM llm_scenario_recovery_events WHERE kind='PERMIT_CONSUMED' AND scenario_id=?",
                                   (row["scenario_id"],)).fetchall()
            permit = json.loads(permits[0][0]) if len(permits) == 1 else {}
            valid = (valid and permit.get("permit_id") == json.loads(events[0][1]).get("permit_id")
                and permit.get("policy_version") == 2 and permit.get("epoch_id") == recovery.get("epoch_id")
                and permit.get("stage") == recovery.get("stage")
                and permit.get("scenario_id") == row["scenario_id"]
                and (permit.get("stage_evidence") or {}).get("promotion_rule_version") == 3
                and (permit.get("stage_evidence") or {}).get("window_origin") == evidence.get("window_origin"))
        except (AttributeError, TypeError, ValueError, KeyError):
            valid = False
        if not valid:
            raise ValueError("normalization_state_invalid")


def _migrate_promotion_rule(conn, state, ctx, now):
    old = state["recovery"]
    offered_stage(old)  # Never repair corrupted legacy evidence by erasing it.
    if old["stage_evidence"].get("promotion_rule_version") == 3:
        _validate_window_sources(conn, old)
        return False
    if (old.get("phase") != "OBSERVING" or state.get("active") is not None
            or not hard_guards(state, ctx) or ctx.get("positions") != [] or ctx.get("pending_entries") != []
            or conn.execute("SELECT 1 FROM llm_scenario_intents WHERE status IS NULL OR status!='TERMINAL'").fetchone()):
        return False
    equity = ctx.get("initial_equity")
    if not _number(equity) or equity <= 0:
        return False
    new = _stage_evidence(equity, rule_version=3, window_origin=digest({"epoch":old["epoch_id"],
        "before":old["stage_evidence"], "version":state["version"], "at":now, "kind":"promotion_rule_migration"}))
    event_id = journal(conn, "PROMOTION_RULE_MIGRATION", {"before": old["stage_evidence"], "after": new,
        "epoch_id":old["epoch_id"], "stage": old["stage"], "reason": "prospective_cost_net_window_no_legacy_backfill"}, now)
    old["stage_evidence"] = new
    old["bootstrap_evidence_event_id"] = event_id
    state["version"] += 1
    return True


def migrate(conn, state, ctx, now):
    """Called under runtime mutation lock; never resets the breaker or financials."""
    old = state.get("recovery", {})
    if old.get("policy_version") == 2:
        return _migrate_promotion_rule(conn, state, ctx, now)
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
    epoch_id = str(uuid.uuid4())
    new = {"policy_version": 2, "epoch_id": epoch_id, "stage": 0,
           "phase": "OBSERVING", "started_at": now, "epoch_start_equity": equity,
           "stage_evidence": _stage_evidence(equity, rule_version=3,
               window_origin=digest({"epoch":epoch_id,"at":now,"kind":"initial_normalization"})),
           "previous_permit_id": old.get("permit_id"),
           "previous_scenario_id": old.get("scenario_id")}
    event_id = journal(conn, "MIGRATION", {"before": old, "after": new,
            "prior_settlement_hash": digest(previous) if previous else None}, now)
    new["bootstrap_evidence_event_id"] = event_id
    state["recovery"] = new
    state["version"] += 1
    return True


def offered_stage(recovery):
    stage = recovery.get("stage", 0)
    evidence = recovery.get("stage_evidence", {})
    if recovery.get('auto_promotion') is False:
        if stage != 0:
            raise ValueError('incident_epoch_stage_invalid')
    if not isinstance(evidence, dict):
        raise ValueError("normalization_state_invalid")
    if recovery.get("policy_version") == 2 and evidence.get("promotion_rule_version") == 3:
        if type(stage) is not int or stage not in range(3) or type(evidence["promotion_rule_version"]) is not int:
            raise ValueError("normalization_state_invalid")
        metrics = _window_metrics(evidence.get("window"))
        origin = evidence.get("window_origin")
        if not isinstance(origin, str) or len(origin) != 64 or any(c not in '0123456789abcdef' for c in origin):
            raise ValueError("normalization_state_invalid")
        if any(type(evidence.get(k)) is not type(v) or evidence[k] != v for k, v in metrics.items()):
            raise ValueError("normalization_state_invalid")
        equity = evidence.get("stage_start_equity")
        if (equity is None and evidence["window"]) or (equity is not None and (not _number(equity) or equity <= 0)):
            raise ValueError("normalization_state_invalid")
        if (recovery.get('auto_promotion') is not False and stage < 2
                and metrics["positive_count"] >= 2 and metrics["net_pnl"] > 0
                and metrics["max_realized_drawdown"] <= STAGE_FRACTIONS[stage]*equity):
            return stage+1
        return stage
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
        rule = evidence.get("promotion_rule_version")
        if "promotion_rule_version" in evidence:
            count = evidence.get("positive_count")
            if (type(rule) is not int or rule != 2 or type(count) is not int
                    or not 0 <= count <= evidence["filled_count"]
                    or (count > 0 and evidence["net_pnl"] <= 0)):
                raise ValueError("normalization_state_invalid")
        elif "positive_count" in evidence:
            raise ValueError("normalization_state_invalid")
    if (recovery.get("policy_version") == 2 and stage < 2
            and evidence.get("promotion_rule_version") == 2
            and evidence.get("positive_count", 0) >= 2 and evidence.get("net_pnl", 0) > 0
            and evidence.get("max_realized_drawdown", float("inf")) <=
                STAGE_FRACTIONS[stage] * evidence.get("stage_start_equity", 0)):
        return stage + 1
    return stage


def offered_fraction(state):
    recovery = state.get("recovery", {})
    if state.get('incident_epoch'):
        return min(.005, recovery['risk_fraction'])
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
    values = {k:v for k,v in numeric_market(snapshot).items() if allowed_evidence_path(k)}
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
                        promotion_rule_version=recovery["stage_evidence"].get("promotion_rule_version"),
                        required_positive_settlements=2,
                        promotion_basis=("prospective_last6_filled_confirmed_cost_net_realized_drawdown"
                            if recovery["stage_evidence"].get("promotion_rule_version") == 3 else
                            "prospective_filled_closed_confirmed_net_pnl_positive"),
                        risk_fraction=offered_fraction(state), one_attempt_only=False,
                        automatic_normal_resume=True, policy_version=2)
        if recovery.get('auto_promotion') is False:
            metadata.update(automatic_stage_promotion=False, required_positive_settlements=None,
                            promotion_basis='DISABLED_INCIDENT_EPOCH')
    context["recovery"] = metadata
    context["recovery_contract_version"] = recovery.get("policy_version", 1)
    context["scenario_risk_fraction"] = metadata["risk_fraction"]
    event_id = journal(conn, "INPUT", {"input_id": context["input_id"],
            "snapshot_hash": digest(snapshot), "context_hash": digest(context),
            "decision_slot": slot, "versions": version_evidence, "recovery": metadata}, now, slot=slot)
    return event_id


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
    if not paths or any(not allowed_evidence_path(path) or path not in data["changed_evidence"] for path in paths):
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
        evidence = recovery["stage_evidence"]
        _validate_window_sources(conn, recovery)
        if "promotion_rule_version" not in evidence:
            # Old aggregates include zero-net trades. Preserve them for audit,
            # but never reinterpret them as two new-rule profitable trades.
            upgraded = dict(evidence, promotion_rule_version=2, positive_count=0)
            journal(conn, "PROMOTION_RULE_MIGRATION", {
                "before": evidence, "after": upgraded,
                "input_id": context["input_id"], "effective_new_scenario": payload["scenario_id"]},
                now, slot=slot, scenario_id=payload["scenario_id"])
            recovery["stage_evidence"] = upgraded
        if recovery["stage_evidence"].get("stage_start_equity") is None:
            recovery["stage_evidence"]["stage_start_equity"] = context["initial_equity"]
        offered = offered_stage(recovery)
        if offered != recovery["stage"]:
            journal(conn, "STAGE_TRANSITION", {"before": recovery["stage"], "after": offered,
                    "reason": "confirmed_stage_performance_and_fresh_probe",
                    "evidence": recovery["stage_evidence"], "input_id": context["input_id"]},
                    now, slot=slot, scenario_id=payload["scenario_id"])
            recovery.update(stage=offered, stage_evidence=_stage_evidence(context["initial_equity"],
                rule_version=evidence.get("promotion_rule_version", 2),
                window_origin=digest({"previous":evidence.get("window_origin"),"scenario":payload["scenario_id"],
                    "input":context["input_id"],"stage":offered,"kind":"promotion"})))
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
    if state.get('incident_epoch'):
        return min(.005, recovery['risk_fraction'])
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
    incident_fraction = bool(state.get('incident_epoch') == permit.get('epoch_id')
        and permit.get('auto_promotion') is False and permit.get('stage') == 0
        and _number(fraction) and 0 < fraction <= .005)
    equity = permit.get("initial_equity")
    if (not _number(equity) or equity <= 0 or (fraction not in STAGE_FRACTIONS and not incident_fraction)
            or not _number(permit.get("budget"))
            or not math.isclose(permit["budget"], equity * fraction, abs_tol=1e-8)):
        return None
    if permit.get("policy_version", 1) not in (1, 2):
        return None
    if permit.get("policy_version", 1) == 1 and fraction != RISK_FRACTION:
        return None
    if permit.get("policy_version") == 2 and (
            type(permit.get("stage")) is not int or permit["stage"] not in range(3)
            or (fraction != STAGE_FRACTIONS[permit["stage"]] and not incident_fraction)):
        return None
    return permit


def normal_permission(state, active, ctx):
    permit = managed_permit(state, active)
    return bool(permit and permit.get("policy_version") == 2
                and permit.get("stage") == 2 and hard_guards(state, ctx))


def authorize_pending(state, active, now, *, waiting_deadline=None):
    if state.get('incident_halted'):
        return False
    permit = managed_permit(state, active)
    breaker = state.get("breaker", {})
    deadline = (active.get("expires_at") if permit and permit.get("policy_version") == 2
                and permit.get("stage") == 2 else (permit or {}).get("entry_deadline"))
    # Only the broker's verified existing-child lease may extend retention.
    # authorize_entry never supplies this argument, preserving submission limits.
    if waiting_deadline is not None:
        deadline = waiting_deadline
    permitted_breaker = (breaker.get("blocked") is True and set(breaker.get("reasons", [])) == {"three_losses"})
    if state.get('incident_epoch') and not breaker.get('blocked') and not breaker.get('reasons'):
        permitted_breaker = True
    return bool(permit and permitted_breaker
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
        if evidence.get("promotion_rule_version") == 3:
            offered_stage(recovery)
            _validate_window_sources(conn, recovery)
            if settlement["execution_ids"]:
                if settlement["scenario_id"] in {row["scenario_id"] for row in evidence["window"]}:
                    return
                rows = (evidence["window"] + [dict(scenario_id=settlement["scenario_id"],
                    settlement_hash=digest(settlement), net_pnl=settlement["net_pnl"], settled_at=now)])[-6:]
                evidence.update(window=rows, **_window_metrics(rows))
                cap = STAGE_FRACTIONS[old_stage]*evidence["stage_start_equity"]
                if old_stage > 0 and (evidence["net_pnl"] < 0 or evidence["max_realized_drawdown"] > cap):
                    recovery["stage"] = old_stage-1
                    recovery["stage_evidence"] = _stage_evidence(None, rule_version=3,
                        window_origin=digest({"previous":evidence["window_origin"],"settlement":digest(settlement),
                            "stage":old_stage-1,"kind":"demotion"}))
                    journal(conn, "STAGE_TRANSITION", {"before": old_stage, "after": recovery["stage"],
                        "reason": "cost_net_window_or_realized_drawdown", "settlement": settlement,
                        "previous_evidence": evidence}, now, scenario_id=settlement["scenario_id"])
        elif settlement["execution_ids"]:
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
                if evidence.get("promotion_rule_version") == 2 and pnl > 0:
                    evidence["positive_count"] += 1
                evidence["net_pnl"] += pnl
                evidence["peak_net_pnl"] = max(evidence["peak_net_pnl"], evidence["net_pnl"])
                evidence["max_realized_drawdown"] = max(evidence["max_realized_drawdown"],
                    evidence["peak_net_pnl"] - evidence["net_pnl"])
        journal(conn, "SETTLEMENT", {"permit_id": recovery["permit_id"], "settlement": settlement,
                "automatic_normal_resume": True, "stage_evidence": recovery["stage_evidence"]},
                now, scenario_id=settlement["scenario_id"])
        previous = {"permit_id": recovery["permit_id"], "scenario_id": recovery["scenario_id"]}
        keep = {key: recovery[key] for key in ("policy_version", "epoch_id", "stage", "stage_evidence", "started_at", "epoch_start_equity")}
        if recovery.get('auto_promotion') is False:
            keep.update(auto_promotion=False, risk_fraction=recovery['risk_fraction'])
        if "bootstrap_evidence_event_id" in recovery:
            keep["bootstrap_evidence_event_id"] = recovery["bootstrap_evidence_event_id"]
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
