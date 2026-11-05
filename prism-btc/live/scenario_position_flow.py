"""Read-only, bounded scenario history. Historical opinions are not market facts."""
import copy
import json
import math
import sqlite3

from live.scenario_entry_contract import valid_record, registered_contract, strip_metadata
from live.scenario_model_input import project_model_input, restore_model_input

MAX_BYTES = 6000
SCAN_LIMIT = 288
_ROW_BYTES = 32768
_MODEL_INPUT_LIMIT = 100_000
_CONTEXT_FIELDS = ("scenario_id", "now", "input_captured_at", "account_captured_at",
                   "mark_price", "positions", "previous_hard_stop", "target_status",
                   "accounting_status", "recorded_scenario_net")


def _size(value):
    return len(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode())


def _number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _pick(value, keys):
    return {k: copy.deepcopy(value[k]) for k in keys if k in value}


def _object(raw):
    if not isinstance(raw, str) or len(raw.encode()) > _ROW_BYTES:
        raise ValueError("history_row_size")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("history_row_not_object")
    _size(value)
    return value


def _plan(value):
    return _pick(value, ("action_id", "action", "side", "revision", "entries", "hard_stop",
                         "take_profits", "partial_stops", "rationale", "expires_at"))


def _observation(context, now):
    stamp = context.get("account_captured_at")
    if not _number(stamp) or not 0 < stamp <= now:
        return dict(status="UNAVAILABLE", reason="missing_or_future_capture_time")
    return dict(source="SAVED_ACCOUNT_OBSERVATION_NOT_FILL_EVENT", captured_at=stamp,
                **_pick(context, ("positions", "previous_hard_stop", "target_status",
                                  "recorded_scenario_net", "accounting_status", "mark_price")))


def project_holding_price_order_input(projected, audit=None):
    """Append bounded numeric order only after all existing optional views fit."""
    logical = restore_model_input(projected)
    context = logical.get('contract_context', {})
    if (context.get('immediate_reduce_version') != 1 or context.get('scenario_id') is None
            or not context.get('positions') or _size(projected) > _MODEL_INPUT_LIMIT):
        return projected, audit
    side = context.get('side')
    targets = (context.get('current_plan') or {}).get('take_profits')
    primary = (context.get('ma_structure') or {}).get('primary', {})
    view = dict(status='UNAVAILABLE')
    if side in {'LONG','SHORT'} and isinstance(targets,list) and len(targets)<=20:
        values = [(context.get('mark_price'),'M')]
        values += [(row.get('price'),f'T{i}') for i,row in enumerate(targets) if isinstance(row,dict)]
        complete = len(values)==len(targets)+1 and bool(targets)
        for frame in ('30m','1h'):
            points = primary.get(frame,{}).get('points',[])
            confirmed = [(i,p) for i,p in enumerate(points) if p.get('phase')=='CONFIRMED' and p.get('is_confirmed') is True][-3:]
            forming = [(i,p) for i,p in enumerate(points) if p.get('phase')=='FORMING' and p.get('is_confirmed') is False][-1:]
            complete = complete and bool(confirmed) and bool(forming)
            for i,point in confirmed+forming:
                values += [(point.get(field),f'{frame}.{i}.{field}') for field in ('high','low')]
        complete = complete and all(_number(value) and value>0 for value,_ in values)
        grouped = {}
        for value,ref in values:
            if _number(value) and value>0:
                grouped.setdefault(value,[]).append(ref)
        view = dict(status='AVAILABLE' if complete else 'PARTIAL',
            ordering='ASCENDING_PRICE' if side=='LONG' else 'DESCENDING_PRICE',
            meaning='NUMERIC_ORDER_NOT_SUPPORT_OR_LIVE_ORDER_PROOF; not travel/time/fill order. M=mark_price; T#=current_plan.take_profits[index].price; frame.index.high/low=ma_structure.primary[frame].points[index][field]. Original point phase/time applies; target activity/remaining quota require separate evidence.',
            prices=[[price,grouped[price]] for price in sorted(grouped,reverse=side=='SHORT')])
    if _size(view)>2000:
        view=dict(status='UNAVAILABLE_SIZE_BOUND')
    for value in (view,dict(status='OMITTED_INPUT_BUDGET')):
        logical['contract_context']['holding_price_order']=value
        candidate,candidate_audit=project_model_input(logical,allow_gap_rescue=True)
        if _size(candidate)<=_MODEL_INPUT_LIMIT:
            return candidate,candidate_audit
    return projected,audit


def project_position_flow_input(payload):
    history = (payload.get('contract_context') or {}).get('last_closed_trade')
    if history is None:
        return _project_position_flow_input(payload)
    baseline = copy.deepcopy(payload)
    baseline['contract_context'].pop('last_closed_trade')
    projected, audit = _project_position_flow_input(baseline)
    if _size(projected) > _MODEL_INPUT_LIMIT:
        return projected, audit
    restored = restore_model_input(projected)
    # Preserve existing history selection; optional continuity cannot evict facts.
    for value in (history, dict(status='OMITTED_INPUT_BUDGET')):
        restored['contract_context']['last_closed_trade'] = value
        candidate, candidate_audit = project_model_input(restored, allow_gap_rescue=True)
        if _size(candidate) <= _MODEL_INPUT_LIMIT:
            return candidate, candidate_audit
    return projected, audit


def _project_position_flow_input(payload):
    """Fit optional history only; preserve the mandatory input size rejection.

    Projection audit hashes describe the actual trimmed input. The frozen source
    is untouched, and every omission is disclosed inside the model-facing flow.
    """
    projected, audit = project_model_input(payload)
    flow = (payload.get("contract_context") or {}).get("position_flow")
    if _size(projected) <= _MODEL_INPUT_LIMIT:
        return projected, audit
    if not isinstance(flow, dict):
        return project_model_input(payload, allow_gap_rescue=True)
    candidate = copy.deepcopy(payload)
    flow = candidate["contract_context"]["position_flow"]
    fields = ("historical_observation_changes", "requested_changes", "thesis_transitions")
    coverage = flow.get("coverage")
    if not isinstance(coverage, dict):
        coverage = flow["coverage"] = {}
    omitted = 0
    # The builder retains at most 3 + 3 + 4 records. Even malformed optional
    # input cannot turn this budget pass into an unbounded projection loop.
    for _ in range(10):
        field = next((key for key in fields if isinstance(flow.get(key), list) and flow[key]), None)
        if field is None:
            break
        del flow[field][0]
        omitted += 1
        coverage["model_budget_omitted_items"] = omitted
        flow["status"] = "PARTIAL"
        projected, audit = project_model_input(candidate)
        if _size(projected) <= _MODEL_INPUT_LIMIT:
            return projected, audit
    candidate["contract_context"]["position_flow"] = dict(version=1,
        status="OMITTED_INPUT_BUDGET",
        scope="Refer to registered_entry_contract/entry_thesis/previous_thesis_assessment; history unavailable.")
    # If mandatory market/account/candidate evidence still exceeds the cap, the
    # existing caller rejects it. Never trim safety or candidate evidence here.
    # Try lossless gap reuse only after the legacy history-budget pass fails.
    return project_model_input(candidate, allow_gap_rescue=True)


def build_closed_continuity(conn, state, now):
    """One last filled completion; opinions never establish current reversal edge."""
    missing = dict(version=1, status='UNAVAILABLE')
    try:
        from backtest.scenario_tape import digest
        from live.scenario_recovery import _confirmed
        ids = state.get('breaker', {}).get('completed_ids', [])
        if not isinstance(ids, list) or not _number(now) or now <= 0:
            return missing
        if not ids:
            return dict(version=1, status='MISSING')
        # Circuit breaker completions contain filled settlements, not cancellations.
        sid = ids[-1]
        if not isinstance(sid, str) or not 0 < len(sid) <= 128:
            return missing
        row = conn.execute('SELECT substr(evidence,1,?) FROM llm_scenario_settlements WHERE scenario_id=?',
                           (_ROW_BYTES + 1, sid)).fetchone()
        settlement = _object(row[0]) if row else {}
        if not _confirmed(settlement, sid) or not settlement['execution_ids']:
            return missing
        events = conn.execute("SELECT event_id,observed_at,substr(body,1,?) FROM llm_scenario_audit_events "
                              "WHERE kind='settlement_recorded' AND scenario_id=? LIMIT 2",
                              (_ROW_BYTES + 1, sid)).fetchall()
        if (len(events) != 1 or not _number(events[0][1]) or not 0 < events[0][1] <= now
                or digest(_object(events[0][2]).get('settlement')) != digest(settlement)):
            return missing
        event_id, observed, _ = events[0]
        row = conn.execute('SELECT substr(body,1,?) FROM llm_scenario_entry_contracts WHERE scenario_id=?',
                           (_ROW_BYTES + 1, sid)).fetchone()
        contract = _object(row[0]) if row else {}
        if not valid_record(contract, sid) or not 0 < contract['origin']['registered_at'] <= observed:
            return missing
        row = conn.execute('SELECT substr(payload,1,?),status FROM llm_scenario_intents WHERE id=? AND scenario_id=?',
                           (_ROW_BYTES + 1, contract['origin']['action_id'], sid)).fetchone()
        plan = _object(row[0]) if row else {}
        if (not row or row[1] != 'TERMINAL' or plan.get('side') not in {'LONG','SHORT'}
                or not registered_contract(conn, dict(scenario_id=sid, side=plan['side'], entry_contract=contract))):
            return missing
        result = dict(version=1, status='AVAILABLE', scenario_id=sid, side=plan['side'],
            scope='CLOSED_HISTORY_NOT_CURRENT_FACT_OR_ENTRY_PERMISSION',
            times=dict(registered_at=contract['origin']['registered_at'], settlement_observed_at=observed,
                       first_entry_fill_at='UNKNOWN', last_exit_fill_at='UNKNOWN'),
            original_contract=contract,
            settlement=dict(kind='CONFIRMED_ACCOUNTING_NOT_CAUSE', evidence_sha256=digest(settlement),
                source=dict(table='llm_scenario_settlements', scenario_id=sid, audit_event_id=event_id),
                values=_pick(settlement, ('gross_pnl','fees','funding_net','net_pnl'))),
            exit_opinion=dict(status='UNAVAILABLE'))
        rows = conn.execute("SELECT slot,substr(proposal,1,?),substr(outcome,1,?) FROM "
            "(SELECT slot,proposal,outcome FROM llm_scenario_decisions ORDER BY slot DESC LIMIT ?) "
            "WHERE json_valid(proposal) AND json_extract(proposal,'$.scenario_id')=? "
            "AND json_extract(proposal,'$.action')='EXIT' ORDER BY slot DESC LIMIT 1",
            (_ROW_BYTES + 1, _ROW_BYTES + 1, SCAN_LIMIT, sid)).fetchall()
        if rows:
            slot, raw_proposal, raw_outcome = rows[0]
            proposal, outcome = _object(raw_proposal), _object(raw_outcome)
            intent = conn.execute('SELECT substr(payload,1,?),status FROM llm_scenario_intents WHERE id=? AND scenario_id=?',
                                  (_ROW_BYTES + 1, proposal.get('action_id'), sid)).fetchone()
            intent_plan = _object(intent[0]) if intent else {}
            # Match the runtime metadata boundary and core EXIT normalization.
            execution_plan = {k:v for k,v in strip_metadata(proposal).items()
                              if k not in {'review','recovery','stop_revision'}}
            assessment = proposal.get('thesis_assessment')
            if (type(slot) is int and 0 < slot * 300 <= observed
                    and outcome.get('status') == 'intent_pending' and intent and intent[1] == 'TERMINAL'
                    and all(isinstance(proposal.get(k), str) and proposal[k] for k in
                            ('action_id','input_id','rationale'))
                    and digest(intent_plan) == digest(execution_plan)):
                if (isinstance(assessment, dict) and assessment.get('contract_id') == contract['contract_id']
                        and set(assessment) == {'contract_id','status','reason'}
                        and isinstance(assessment.get('reason'), str) and 1 <= len(assessment['reason']) <= 240
                        and assessment.get('status') in {'INTACT','WEAKENING','FAILED','UNKNOWN'}):
                    result['exit_opinion'] = dict(status='AVAILABLE', kind='HISTORICAL_MODEL_OPINION_NOT_FILL_CAUSE',
                        source=dict(decision_slot=slot, action_id=proposal['action_id'], proposal_sha256=digest(proposal)),
                        assessment=assessment)
        return result if _size(result) <= 3000 else dict(version=1,status='UNAVAILABLE',reason='history_size')
    except (sqlite3.Error, ValueError, TypeError, KeyError, OverflowError, AttributeError):
        return missing


def build_position_flow(conn, active, context, now, *, snapshot=None):
    try:
        return _build_position_flow(conn, active, context, now, snapshot=snapshot)
    except (sqlite3.Error, ValueError, TypeError, KeyError, OverflowError, AttributeError):
        # Optional continuity must never prevent independent protection checks.
        return dict(version=1, status="UNAVAILABLE", reason="malformed_optional_history")


def _build_position_flow(conn, active, context, now, *, snapshot=None):
    """Project saved same-scenario events without SQL writes or external dependencies.

    Context is the existing broker-verified context, not an independent observation.
    Latest global decision slots are scanned with a bound, so older coverage is
    explicitly unknown. No order intent or cumulative action fill becomes a fill.
    """
    if not isinstance(active, dict) or not active.get("scenario_id"):
        return dict(version=1, status="NOT_ACTIVE")
    sid = active["scenario_id"]
    if not isinstance(sid, str) or len(sid) > 128 or not _number(now) or now <= 0:
        return dict(version=1, status="UNAVAILABLE", reason="invalid_identity_or_time")
    if context.get("scenario_id") != sid:
        return dict(version=1, status="UNAVAILABLE", reason="context_scenario_mismatch")
    created = active.get("created_at")
    if created is not None and (not _number(created) or created > now):
        return dict(version=1, status="UNAVAILABLE", reason="invalid_scenario_time")
    coverage = dict(scan_limit=SCAN_LIMIT, scanned_rows=0, same_scenario_rows=0,
                    rejected_rows=0, invalid_rows=0, omitted_items=0,
                    older_history="UNKNOWN", source="LOCAL_DURABLE_LEDGER")
    result = dict(version=1, scenario_id=sid, as_of=now, status="AVAILABLE",
                  scope="Historical opinions and requested plans are not current facts or fills; missing history is unknown.",
                  coverage=coverage)

    def add(key, value, limit=2400):
        try:
            if _size(value) > limit:
                raise ValueError("item_size")
            result[key] = copy.deepcopy(value)
            if _size(result) > MAX_BYTES - 512:
                del result[key]
                raise ValueError("total_size")
        except (ValueError, TypeError, OverflowError):
            coverage["omitted_items"] += 1
            result["status"] = "PARTIAL"

    def recent(key, values, count):
        # Drop whole oldest records, never cut a reason or numeric literal.
        retained = values[-count:]
        coverage["omitted_items"] += len(values) - len(retained)
        while retained:
            result[key] = retained
            if _size(result) <= MAX_BYTES - 128:
                return
            retained = retained[1:]
            coverage["omitted_items"] += 1
        result[key] = []

    try:
        contract = context.get("registered_entry_contract")
        if valid_record(contract, sid) and contract["origin"]["registered_at"] > now:
            return dict(version=1, status="UNAVAILABLE", reason="future_contract")
        if valid_record(contract, sid) and contract["origin"]["registered_at"] <= now:
            add("original_contract", contract)
        else:
            result["original_contract_status"] = "UNAVAILABLE"
        row = conn.execute(
            "SELECT id,substr(payload,1,?),status FROM llm_scenario_intents "
            "WHERE scenario_id=? ORDER BY rowid LIMIT 1", (_ROW_BYTES + 1, sid)).fetchone()
        if row:
            original = _object(row[1])
            if (original.get("action") != "OPEN" or original.get("scenario_id") != sid
                    or original.get("action_id") != row[0]
                    or (contract and valid_record(contract, sid)
                        and contract["origin"]["action_id"] != row[0])):
                raise ValueError("original_plan_binding")
            add("original_requested_plan", dict(source="REGISTERED_INTENT_NOT_FILL",
                intent_status_at_read=row[2], plan=_plan(original)))
        else:
            result["original_plan_status"] = "MISSING"
    except (sqlite3.Error, ValueError, TypeError, KeyError, OverflowError):
        coverage["invalid_rows"] += 1
        result["status"] = "PARTIAL"

    # Reuse evidence already checked by the broker; do not recompute quantities.
    thesis = context.get("entry_thesis")
    if (isinstance(thesis, dict) and thesis.get("status") == "VERIFIED"
            and _number(thesis.get("observed_at")) and thesis["observed_at"] <= now):
        add("initial_fill_evidence", _pick(thesis, ("status", "side", "initial_hard_stop",
            "initial_filled_quantity", "initial_fill_average", "first_fill_at",
            "latest_entry_fill_at", "observed_at", "scope")), 1500)
    else:
        result["initial_fill_status"] = "UNAVAILABLE"
    result["current_context_references"] = dict(
        observations=["positions", "target_status", "previous_hard_stop", "account_captured_at"],
        economics="runner_economics", advisory_checkpoints_not_exit_rules="review_memory")

    transitions, changes, observations, marks = [], [], [], []
    previous_status = previous_observation = None
    try:
        # Project only needed context fields in SQLite before crossing the DB
        # boundary; snapshots and full prompts are intentionally never fetched.
        # SQLite 3.26 JSON serialization rounds scalar doubles. Return numeric
        # columns directly, and parse preserved JSON array text only in Python.
        rows = conn.execute("""
            SELECT slot,json_valid(context),
                CASE WHEN json_valid(context) THEN CASE
                    WHEN length(json_extract(context,'$.scenario_id'))<=128
                    THEN json_extract(context,'$.scenario_id') END END,
                CASE WHEN json_valid(context) THEN CASE
                    WHEN json_type(context,'$.now') IN ('integer','real') THEN json_extract(context,'$.now')
                    WHEN json_type(context,'$.now') IS NULL OR json_type(context,'$.now')='null'
                    THEN NULL ELSE 'INVALID_NUMBER' END END,
                CASE WHEN json_valid(context) THEN CASE
                    WHEN json_type(context,'$.input_captured_at') IN ('integer','real')
                    THEN json_extract(context,'$.input_captured_at')
                    WHEN json_type(context,'$.input_captured_at') IS NULL OR json_type(context,'$.input_captured_at')='null'
                    THEN NULL ELSE 'INVALID_NUMBER' END END,
                CASE WHEN json_valid(context) THEN CASE
                    WHEN json_type(context,'$.account_captured_at') IN ('integer','real')
                    THEN json_extract(context,'$.account_captured_at')
                    WHEN json_type(context,'$.account_captured_at') IS NULL OR json_type(context,'$.account_captured_at')='null'
                    THEN NULL ELSE 'INVALID_NUMBER' END END,
                CASE WHEN json_valid(context) THEN CASE
                    WHEN json_type(context,'$.mark_price') IN ('integer','real') THEN json_extract(context,'$.mark_price')
                    WHEN json_type(context,'$.mark_price') IS NULL OR json_type(context,'$.mark_price')='null'
                    THEN NULL ELSE 'INVALID_NUMBER' END END,
                CASE WHEN json_valid(context) THEN substr(json_extract(context,'$.positions'),1,?) END,
                CASE WHEN json_valid(context) THEN CASE
                    WHEN json_type(context,'$.previous_hard_stop') IN ('integer','real')
                    THEN json_extract(context,'$.previous_hard_stop')
                    WHEN json_type(context,'$.previous_hard_stop') IS NULL OR json_type(context,'$.previous_hard_stop')='null'
                    THEN NULL ELSE 'INVALID_NUMBER' END END,
                CASE WHEN json_valid(context) THEN substr(json_extract(context,'$.target_status'),1,?) END,
                CASE WHEN json_valid(context) THEN CASE
                    WHEN length(json_extract(context,'$.accounting_status'))<=128
                    THEN json_extract(context,'$.accounting_status') END END,
                CASE WHEN json_valid(context) THEN CASE
                    WHEN json_type(context,'$.recorded_scenario_net') IN ('integer','real')
                    THEN json_extract(context,'$.recorded_scenario_net')
                    WHEN json_type(context,'$.recorded_scenario_net') IS NULL OR json_type(context,'$.recorded_scenario_net')='null'
                    THEN NULL ELSE 'INVALID_NUMBER' END END,
                substr(proposal,1,?),substr(outcome,1,?)
            FROM llm_scenario_decisions WHERE slot<? AND outcome IS NOT NULL
            ORDER BY slot DESC LIMIT ?
            """, (_ROW_BYTES + 1, _ROW_BYTES + 1, _ROW_BYTES + 1, _ROW_BYTES + 1,
                  int(now // 300), SCAN_LIMIT + 1)).fetchall()
        coverage["older_history"] = "OMITTED_SCAN_LIMIT" if len(rows) > SCAN_LIMIT else "NO_OLDER_STORED_ROWS"
        rows = rows[:SCAN_LIMIT]
        coverage["scanned_rows"] = len(rows)
        coverage["source_first_slot"] = rows[-1][0] if rows else None
        coverage["source_last_slot"] = rows[0][0] if rows else None
        for row in reversed(rows):
            try:
                slot, context_valid, *values = row
                if context_valid != 1:
                    raise ValueError("invalid_history_context")
                ctx = dict(zip(_CONTEXT_FIELDS, values[:-2]))
                for key in ("now", "input_captured_at", "account_captured_at", "mark_price",
                            "previous_hard_stop", "recorded_scenario_net"):
                    if ctx[key] is not None and not _number(ctx[key]):
                        raise ValueError("invalid_history_number")
                for key in ("positions", "target_status"):
                    raw = ctx[key]
                    if raw is not None:
                        if not isinstance(raw, str) or len(raw.encode()) > _ROW_BYTES:
                            raise ValueError("history_array_size")
                        ctx[key] = json.loads(raw)
                        if not isinstance(ctx[key], list):
                            raise ValueError("invalid_history_array")
                if _size(ctx) > _ROW_BYTES:
                    raise ValueError("history_context_size")
                raw_proposal, raw_outcome = values[-2:]
                outcome = _object(raw_outcome)
                if ctx.get("scenario_id") != sid:
                    continue
                coverage["same_scenario_rows"] += 1
                if outcome.get("status") not in {"wait", "intent_pending"}:
                    coverage["rejected_rows"] += 1
                    continue
                for key in ("now", "input_captured_at", "account_captured_at"):
                    stamp = ctx.get(key)
                    if stamp is not None and (not _number(stamp) or stamp > now):
                        raise ValueError("future_or_invalid_history_time")
                proposal = _object(raw_proposal)
                if proposal.get("scenario_id") != sid:
                    raise ValueError("proposal_binding")
                action = proposal.get("action")
                if outcome["status"] == "wait" and action != "WAIT":
                    raise ValueError("wait_binding")
                if outcome["status"] == "intent_pending":
                    intent = conn.execute(
                        "SELECT substr(payload,1,?),status FROM llm_scenario_intents WHERE id=? AND scenario_id=?",
                        (_ROW_BYTES + 1, proposal.get("action_id"), sid)).fetchone()
                    if not intent:
                        raise ValueError("missing_intent")
                    plan = _object(intent[0])
                    if (plan.get("scenario_id") != sid or plan.get("action_id") != proposal.get("action_id")
                            or plan.get("action") != action):
                        raise ValueError("intent_binding")
                    changes.append(dict(slot=slot, source="REQUESTED_PLAN_NOT_FILL",
                        outcome_at_decision=outcome["status"], intent_status_at_read=intent[1],
                        plan=_plan(plan)))
                assessment = proposal.get("thesis_assessment")
                if assessment is not None:
                    if (not isinstance(assessment, dict) or not valid_record(contract, sid)
                            or assessment.get("contract_id") != contract["contract_id"]
                            or assessment.get("status") not in {"INTACT", "WEAKENING", "FAILED", "UNKNOWN"}
                            or not isinstance(assessment.get("reason"), str)
                            or not assessment["reason"].strip()
                            or not 1 <= len(assessment["reason"]) <= 240):
                        raise ValueError("assessment_binding")
                    if assessment["status"] != previous_status:
                        transitions.append(dict(slot=slot, source="HISTORICAL_MODEL_OPINION",
                            **_pick(assessment, ("status", "reason"))))
                    previous_status = assessment["status"]
                observation = _observation(ctx, now)
                comparison = {k: v for k, v in observation.items() if k not in {"captured_at", "mark_price"}}
                if observation.get("status") != "UNAVAILABLE" and comparison != previous_observation:
                    observations.append(dict(slot=slot, **observation))
                    previous_observation = comparison
                if (observation.get("status") != "UNAVAILABLE" and ctx.get("accounting_status") == "confirmed"
                        and ctx.get("positions")
                        and all(isinstance(p, dict) and _number(p.get("quantity")) and p["quantity"] > 0
                                for p in ctx["positions"])
                        and _number(ctx.get("mark_price")) and ctx["mark_price"] > 0):
                    marks.append((observation["captured_at"], ctx["mark_price"]))
            except (ValueError, TypeError, KeyError, OverflowError, sqlite3.Error):
                coverage["invalid_rows"] += 1
    except (sqlite3.Error, ValueError, TypeError, OverflowError):
        coverage["invalid_rows"] += 1
        coverage["older_history"] = "UNAVAILABLE"
    coverage.update(status_transitions_found=len(transitions), requested_changes_found=len(changes),
                    observed_changes_found=len(observations))
    if snapshot is not None:
        add("mark_pace", _mark_pace(snapshot, active, context, now, marks), 800)
    recent("thesis_transitions", transitions, 4)
    recent("requested_changes", changes, 3)
    recent("historical_observation_changes", observations, 3)
    if coverage["omitted_items"] or coverage["invalid_rows"] or coverage["older_history"] != "NO_OLDER_STORED_ROWS":
        result["status"] = "PARTIAL"
    return result


def _mark_pace(snapshot, active, context, now, marks):
    """Two verified Mark endpoints are not an intrabar path or order flow."""
    try:
        thesis = context['entry_thesis']
        current_at, current = context['account_captured_at'], context['mark_price']
        side = active['side']
        primary = snapshot['timeframes']['30m']
        frame = primary['confirmed']
        at = snapshot['as_of_ms']
        atr = frame['atr14_risk_only']
        filled_at, entry = thesis['first_fill_at'], thesis['initial_fill_average']
        values = (current_at, current, at, atr, filled_at, entry, now,
                  frame['open_time_ms'], thesis['observed_at'])
        if (not all(_number(v) for v in values) or not snapshot.get('valid') is True
                or thesis.get('status') != 'VERIFIED' or side not in ('LONG', 'SHORT')
                or thesis.get('side') != side or frame.get('is_confirmed') is not True
                or primary.get('status') != 'ok'
                or context.get('accounting_status') != 'confirmed'
                or not filled_at <= thesis['observed_at'] <= now
                or not isinstance(context.get('positions'), list) or not context['positions']
                or any(not isinstance(p, dict) or not _number(p.get('quantity'))
                       or p['quantity'] <= 0 for p in context['positions'])
                or min(current, atr, entry) <= 0 or not 0 <= now - current_at <= 120
                or frame['open_time_ms'] + 1800000 != int(at // 1800000) * 1800000
                or frame['open_time_ms'] + 1800000 > current_at * 1000
                or at > now * 1000):
            return dict(status='MISSING')
        prior = [(t, p) for t, p in marks if _number(t) and _number(p)
                 and p > 0 and filled_at <= t < current_at and 60 <= current_at - t <= 600]
        if not prior:
            latest_fill = thesis.get('latest_entry_fill_at')
            additional = thesis.get('additional_entry_fills')
            elapsed = current_at - filled_at
            # Fill price and Mark are different price streams, not a Mark-to-Mark pace.
            if (not _number(latest_fill) or latest_fill != filled_at
                    or type(additional) is not int or additional != 0 or not 60 <= elapsed <= 600
                    or thesis['observed_at'] < current_at
                    or not 0 <= now - at / 1000 <= 120
                    or abs(current_at - at / 1000) > 120):
                return dict(status='MISSING')
            change = (1 if side == 'LONG' else -1) * (current - entry)
            normalized = (change / atr) * 300 / elapsed
            if not all(_number(v) for v in (change, normalized)):
                return dict(status='MISSING')
            hourly_frame = snapshot['timeframes'].get('1h')
            hourly = hourly_frame.get('confirmed') if isinstance(hourly_frame, dict) else None
            hourly = hourly if isinstance(hourly, dict) else {}
            hourly_atr, hourly_open = hourly.get('atr14_risk_only'), hourly.get('open_time_ms')
            hourly_normalized = None
            if (isinstance(hourly_frame, dict) and hourly_frame.get('status') == 'ok'
                    and hourly.get('is_confirmed') is True and _number(hourly_atr) and hourly_atr > 0
                    and _number(hourly_open) and hourly_open + 3600000 == int(at // 3600000) * 3600000
                    and hourly_open + 3600000 <= current_at * 1000):
                value = (change / hourly_atr) * 300 / elapsed
                if _number(value):
                    hourly_normalized = value
            def partial_compact(value, digits):
                rounded = round(value, digits)
                return value if rounded == 0 and value != 0 else rounded
            return dict(status='PARTIAL', source='FILL_AVG_TO_MARK_DIFFERENT_PRICE_STREAMS',
                        interval_seconds=partial_compact(elapsed, 1),
                        fill_to_mark_favorable_usdt_per_btc=partial_compact(change, 4),
                        fill_to_mark_atr30_per_5m=partial_compact(normalized, 4),
                        fill_to_mark_atr1h_per_5m=partial_compact(hourly_normalized, 4) if hourly_normalized is not None else None,
                        favorable_atr30_per_5m=None, favorable_atr1h_per_5m=None)
        previous_at, previous = max(prior, key=lambda x: x[0])
        elapsed = current_at - previous_at
        sign = 1 if side == 'LONG' else -1
        favorable = sign * (current - previous)
        rate30 = (favorable / atr) * 300 / elapsed
        if not all(_number(v) for v in (favorable, rate30)):
            return dict(status='MISSING')
        rate1h = None
        hourly_frame = snapshot['timeframes'].get('1h')
        hourly = hourly_frame.get('confirmed') if isinstance(hourly_frame, dict) else None
        hourly = hourly if isinstance(hourly, dict) else {}
        hourly_atr, hourly_open = hourly.get('atr14_risk_only'), hourly.get('open_time_ms')
        if (isinstance(hourly_frame, dict) and hourly_frame.get('status') == 'ok'
                and hourly.get('is_confirmed') is True and _number(hourly_atr) and hourly_atr > 0
                and _number(hourly_open) and hourly_open + 3600000 == int(at // 3600000) * 3600000
                and hourly_open + 3600000 <= current_at * 1000):
            value = (favorable / hourly_atr) * 300 / elapsed
            if _number(value):
                rate1h = value
        forming_move = forming_range = None
        forming = snapshot['timeframes']['30m'].get('forming')
        if isinstance(forming, dict):
            ohlcv = forming.get('ohlcv')
            observed, opened = forming.get('observed_at_ms'), forming.get('open_time_ms')
            historical = snapshot.get('historical_replay')
            historical_ok = (forming.get('observation_kind') == 'historical_completed_subbars'
                             and isinstance(historical, dict)
                             and historical.get('source_interval_ms') == 60000
                             and historical.get('closed_source_through_ms') == at
                             and isinstance(historical.get('zero_progress_synthetic_timeframes'), list)
                             and '30m' not in historical['zero_progress_synthetic_timeframes'])
            if (isinstance(ohlcv, dict) and (forming.get('observation_kind') == 'observed' or historical_ok)
                    and _number(observed) and _number(opened) and opened <= observed <= at
                    and at < opened + 1800000
                    and all(_number(ohlcv.get(k)) and ohlcv[k] > 0 for k in ('open', 'high', 'low', 'close'))
                    and ohlcv['low'] <= min(ohlcv['open'], ohlcv['close'])
                    and ohlcv['high'] >= max(ohlcv['open'], ohlcv['close'])):
                move = sign * (ohlcv['close'] - ohlcv['open']) / atr
                span = (ohlcv['high'] - ohlcv['low']) / atr
                if _number(move) and _number(span):
                    forming_move, forming_range = move, span
        def compact(value, digits=4):
            rounded = round(value, digits)
            return value if rounded == 0 and value != 0 else rounded
        return dict(status='AVAILABLE', source='MARK_PAIR_LAST_OHLC_NO_PATH',
                    interval_seconds=compact(elapsed, 1), favorable_usdt_per_btc=compact(favorable),
                    favorable_atr30_per_5m=compact(rate30),
                    favorable_atr1h_per_5m=compact(rate1h) if rate1h is not None else None,
                    last30_favorable_atr=compact(forming_move) if forming_move is not None else None,
                    last30_range_atr=compact(forming_range) if forming_range is not None else None)
    except (KeyError, TypeError, ValueError, OverflowError, ZeroDivisionError):
        return dict(status='MISSING')
