"""Durable orchestration with explicit broker proof and authorization boundaries.

Broker contract: reconcile() must maintain protection even when new risk is
disabled, returning exact intent evidence and optional settlement. context()
returns fresh, reconciled validator inputs plus account_version, legacy_fenced,
day, day_start_equity, daily_net_pnl and protection_ok. execute() must atomically
protect every partial fill, implement idempotent intent IDs, and never infer
fills from acceptance. Capabilities are an audited adapter contract, not a
substitute for an adapter's exchange integration tests.

An independent protection loop remains mandatory during the out-of-lock LLM call.
Unknown submissions are NEVER resubmitted, even after process restart.
"""
from __future__ import annotations

import json
import logging
import math
import sqlite3
import time
import uuid
from live import scenario_management_quarantine as quarantine
from live import scenario_incident_epoch as incident_epoch
from contextlib import ExitStack, contextmanager

from core.llm_scenario import EntryConvergenceRejection, update_circuit_breaker
from core.scenario_limit_prices import validate_execution_prices
from live.shared_entry_coordinator import database_path, mutation_lock
from live.entry_reservations import LockBusy
from live.scenario_llm import ScenarioModelError
from live.scenario_review_memory import apply_review, observe_review, review_context
from live import scenario_recovery as recovery
from live.scenario_entry_contract import (bind_contract, contract_context, observe_contract,
                                          strip_metadata, validate_metadata)

REQUIRED_CAPABILITIES = frozenset({"exact_fills", "atomic_protection",
    "exact_settlement", "idempotent_intents", "fresh_risk_context"})


def _fresh_safety_detail(code):
    """Return a fixed category without preserving exception text or context values."""
    if not isinstance(code, str):
        return None
    if code.startswith('review_guard_context_changed:'):
        return 'context_changed'
    return {
        'review_guard_effective_plan_changed': 'effective_plan_changed',
        'review_guard_stale_account': 'stale_account',
        'stop_crossed_market': 'stop_crossed_market',
    }.get(code)


@contextmanager
def _snapshot_mutation_lock(conn, diagnostics):
    """Wait only for OS-lock acquisition, never retry input persistence work."""
    started = None
    with ExitStack() as stack:
        while True:
            if started is not None:
                elapsed = time.monotonic()-started
                diagnostics["lock_wait_seconds"] = round(elapsed, 3)
                if elapsed >= 20:
                    raise LockBusy("lock_busy")
            try:
                stack.enter_context(mutation_lock(conn))
                break
            except LockBusy:
                if conn.in_transaction:
                    raise
                if started is None:
                    started = time.monotonic()
                diagnostics["contended"] = True
                remaining = 20-(time.monotonic()-started)
                if remaining <= 0:
                    diagnostics["lock_wait_seconds"] = round(time.monotonic()-started, 3)
                    raise
                time.sleep(min(.25, remaining))
        if started is not None:
            elapsed = time.monotonic()-started
            diagnostics["lock_wait_seconds"] = round(elapsed, 3)
            if elapsed >= 20:
                raise LockBusy("lock_busy")
        # Guarded-body LockBusy must escape, not re-enter the acquisition loop.
        yield


@contextmanager
def _post_model_mutation_lock(conn):
    """Briefly wait for protection; retry acquisition, never the guarded work."""
    deadline = time.monotonic() + 20
    with ExitStack() as stack:
        while True:
            try:
                stack.enter_context(mutation_lock(conn))
                break
            except LockBusy:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise
                time.sleep(min(.25, remaining))
        yield


def _json(value):
    return json.dumps(value, allow_nan=False, sort_keys=True)


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _valid_open_entry(row):
    if not isinstance(row, dict):
        return False
    regular = {"id", "price", "quantity"}
    conditional = regular | {"trigger_price", "order_status", "expires_at"}
    if set(row) not in (regular, conditional):
        return False
    if (not isinstance(row["id"], str) or not row["id"] or
            not _finite(row["price"]) or row["price"] <= 0 or
            not _finite(row["quantity"]) or row["quantity"] <= 0):
        return False
    if set(row) == conditional:
        from live.scenario_execution import LIVE
        return (isinstance(row["order_status"], str) and row["order_status"] in LIVE and _finite(row["trigger_price"]) and
                row["trigger_price"] > 0 and _finite(row["expires_at"]) and row["expires_at"] > 0)
    return True


def snapshot_input_time(snapshot):
    """Use the oldest primary observation, not the end of a sequential fetch."""
    times = [snapshot.get("as_of_ms")]
    for tf in ("15m", "30m", "1h"):
        forming = snapshot.get("timeframes", {}).get(tf, {}).get("forming")
        if isinstance(forming, dict):
            times.append(forming.get("observed_at_ms"))
    if any(not _finite(t) or t < 0 for t in times):
        raise ValueError("invalid_snapshot_time")
    return min(times) / 1000


def _economic_evidence(item):
    """Ignore request/capture metadata, but never ignore a protection/fill change."""
    keys = ("intent_id", "terminal", "protection_ok", "orders_reconciled",
            "executions_complete", "execution_ids", "filled_quantity",
            "exchange_order_ids", "open_entries", "protection")
    proof = {k: item.get(k) for k in keys}
    for key in ("execution_ids", "exchange_order_ids"):
        if isinstance(proof[key], list):
            proof[key] = sorted(proof[key])
    if isinstance(proof["open_entries"], list):
        proof["open_entries"] = sorted(proof["open_entries"], key=lambda o: o["id"])
    return proof


class ScenarioRuntime:
    def __init__(self, conn, broker, propose, snapshot, *, clock=time.time,
                 recovery_enabled=False, automatic_normalization_enabled=False,
                 calculated_review_enabled=False, calculated_review_propose=None,
                 research_review_mode="legacy", entry_contract_enabled=False, pending_renewal_enabled=False,
                 entry_goal_enabled=False, convergence_entry_enabled=False):
        if type(entry_goal_enabled) is not bool or (entry_goal_enabled and
                (not entry_contract_enabled or not calculated_review_enabled)):
            raise ValueError('entry_goal_requires_calculated_contract')
        self.entry_goal_enabled = entry_goal_enabled
        if type(convergence_entry_enabled) is not bool:
            raise ValueError('convergence_entry_flag_must_be_bool')
        self.convergence_entry_enabled = convergence_entry_enabled
        if convergence_entry_enabled:
            broker.convergence_entry_enabled = True
        if entry_goal_enabled and research_review_mode not in {
                'legacy', 'allocation-calculated', 'action-allocation-calculated-v1', 'economic-reject-calculated-v1'}:
            raise ValueError('entry_goal_requires_two_phase_review')
        if type(pending_renewal_enabled) is not bool or (pending_renewal_enabled and not entry_contract_enabled):
            raise ValueError('pending_renewal_requires_entry_contract')
        self.pending_renewal_enabled = pending_renewal_enabled
        if pending_renewal_enabled and (not calculated_review_enabled or research_review_mode not in {
                'legacy', 'allocation-calculated', 'action-allocation-calculated-v1', 'economic-reject-calculated-v1'}):
            raise ValueError('pending_renewal_requires_two_phase_review')
        if type(entry_contract_enabled) is not bool:
            raise ValueError("entry_contract_flag_must_be_bool")
        self.entry_contract_enabled = entry_contract_enabled
        if research_review_mode not in {"legacy", "allocation-single", "allocation-calculated",
                                     "action-allocation-single-v1", "action-allocation-calculated-v1",
                                     "economic-reject-single-v1", "economic-reject-calculated-v1"}:
            raise ValueError("research_review_mode")
        if research_review_mode != "legacy" and not calculated_review_enabled:
            raise ValueError("allocation_review_requires_explicit_enable")
        self.research_review_mode = research_review_mode
        if type(calculated_review_enabled) is not bool:
            raise ValueError("calculated_review_flag_must_be_bool")
        if calculated_review_enabled and not callable(calculated_review_propose):
            raise ValueError("calculated_review_callback_required")
        database_path(conn)  # In-memory state cannot provide restart safety.
        self.conn, self.broker = conn, broker
        self.propose, self.snapshot, self.clock = propose, snapshot, clock
        self.recovery_enabled = recovery_enabled or automatic_normalization_enabled or bool(incident_epoch.epoch(conn))
        self.automatic_normalization_enabled = automatic_normalization_enabled
        self.calculated_review_enabled = calculated_review_enabled
        self.calculated_review_propose = calculated_review_propose
        with mutation_lock(conn):
            from live.scenario_pending_renewal import initialize
            initialize(conn)
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS llm_scenario_state (
                    id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS llm_scenario_slots (
                    slot INTEGER PRIMARY KEY, status TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS llm_scenario_decisions (
                    slot INTEGER PRIMARY KEY, snapshot TEXT, context TEXT,
                    proposal TEXT, outcome TEXT);
                CREATE INDEX IF NOT EXISTS llm_scenario_decisions_action_id
                    ON llm_scenario_decisions(json_extract(proposal, '$.action_id'));
                CREATE TABLE IF NOT EXISTS llm_scenario_intents (
                    id TEXT PRIMARY KEY, scenario_id TEXT NOT NULL,
                    payload TEXT NOT NULL, status TEXT NOT NULL, evidence TEXT);
                CREATE TABLE IF NOT EXISTS llm_scenario_settlements (
                    scenario_id TEXT PRIMARY KEY, evidence TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS llm_scenario_notice_errors (
                    captured_at REAL NOT NULL, reason TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS llm_scenario_entry_contracts (
                    scenario_id TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS llm_scenario_entry_failures (
                    scenario_id TEXT PRIMARY KEY, body TEXT NOT NULL);
            """)
            conn.execute("INSERT OR IGNORE INTO llm_scenario_state VALUES(1, ?)",
                         (_json({"version": 0, "active": None, "breaker": {}}),))
            if self.recovery_enabled:
                recovery.ensure_schema(conn)
            if self.calculated_review_enabled:
                conn.executescript("""
                    CREATE TABLE IF NOT EXISTS llm_scenario_review_events (
                        slot INTEGER NOT NULL, sequence INTEGER NOT NULL,
                        body TEXT NOT NULL, PRIMARY KEY(slot,sequence));
                    CREATE TABLE IF NOT EXISTS llm_scenario_review_tapes (
                        slot INTEGER PRIMARY KEY, body TEXT NOT NULL);
                """)
            conn.commit()

    def _record_calculated_review(self, slot, event, *, commit=True):
        from analysis.scenario_review_tape import MAX_BYTES
        body = _json(event)
        if len(body.encode()) > MAX_BYTES:
            raise ValueError("review_record_bound")
        sequence = self.conn.execute(
            "SELECT COUNT(*) FROM llm_scenario_review_events WHERE slot=?", (slot,)).fetchone()[0]
        if sequence >= 128:
            raise ValueError("review_record_bound")
        self.conn.execute("INSERT INTO llm_scenario_review_events VALUES(?,?,?)",
                          (slot, sequence, body))
        if commit:
            self.conn.commit()

    def _assert_calculated_review_time(self, envelope, context, fresh, payload, *, observed_at=None):
        # Durable journal I/O is inside the original budget, not a new lease.
        now = self.clock() if observed_at is None else observed_at
        started = next(operation["value"] for operation in envelope["operations"]
                       if operation["kind"] == "clock")
        max_age = min(120, context["max_input_age_seconds"])
        if (not _finite(now) or not 0 <= now - started < 75
                or payload["expires_at"] <= now
                or not 0 <= now - context["input_captured_at"] <= max_age
                or not 0 <= now - fresh["account_captured_at"] <= 120):
            raise ValueError("review_final_deadline")

    def state(self):
        return json.loads(self.conn.execute(
            "SELECT body FROM llm_scenario_state WHERE id=1").fetchone()[0])

    def _save(self, state):
        self.conn.execute("UPDATE llm_scenario_state SET body=? WHERE id=1", (_json(state),))
        self.conn.commit()

    def _apply_review(self, active, payload, validated, presented, input_id, slot, *, observed_at=None,
                      persisted_proposal_json=None):
        """Optional audit failures must never interrupt advisory apply or EXIT."""
        stamp, boundary = self.clock() if observed_at is None else observed_at, None
        try:
            from live.scenario_provenance import begin_review_capture
            boundary = begin_review_capture(active, presented, payload, validated,
                input_id=input_id, slot=slot, applied_at=stamp,
                persisted_proposal_json=persisted_proposal_json)
        except Exception:
            boundary = None
        receipt = apply_review(active, payload.get("review"), validated["action_id"],
                               now=stamp, presented=presented)
        try:
            from live.scenario_provenance import finish_review_capture
            finish_review_capture(boundary, active, receipt)
        except Exception:
            return receipt
        return receipt

    def _notice(self, identity, event, *, observed_at=None):
        """Optional local publication must never undo a durable order intent."""
        from live.scenario_outbox import enqueue
        self.conn.execute("SAVEPOINT runtime_notice")
        try:
            enqueue(self.conn,identity,event)
            self.conn.execute("RELEASE runtime_notice")
            return True
        except Exception:
            self.conn.execute("ROLLBACK TO runtime_notice")
            self.conn.execute("RELEASE runtime_notice")
            self.conn.execute("INSERT INTO llm_scenario_notice_errors VALUES(?,?)",
                              (self.clock() if observed_at is None else observed_at,"notice_validation_or_enqueue_failed"))
            return False

    def _enabled(self):
        return (getattr(self.broker, "environment", None) == "demo" and
                getattr(self.broker, "lane", None) == "MAIN" and
                REQUIRED_CAPABILITIES <= set(getattr(self.broker, "capabilities", ())))

    def _reconcile(self, state, *, observed_at=None):
        result = self.broker.reconcile() or {}
        active = state["active"]
        confirmed_stop = result.get("confirmed_hard_stop")
        if (active and result.get("scenario_id") == active["scenario_id"]
                and result.get("protection_confirmed") is True
                and _finite(confirmed_stop) and confirmed_stop > 0):
            old = active["hard_stop"]
            improves = confirmed_stop >= old if active["side"] == "LONG" else confirmed_stop <= old
            if not improves:
                raise ValueError("broker_stop_regressed")
            if confirmed_stop != old:
                active["hard_stop"] = confirmed_stop
                state["version"] += 1
        # Optional rendering must neither delay protection nor roll back a
        # verified economic transition. Enqueue is local, never network I/O.
        from live.scenario_outbox import enqueue
        notices = result.get("notices", [])
        if not isinstance(notices, list) or len(notices) > 100:
            notices = [None]
        for notice in notices:
            self.conn.execute("SAVEPOINT scenario_notice")
            try:
                enqueue(self.conn, notice["event_id"], notice)
                self.conn.execute("RELEASE scenario_notice")
            except Exception:
                self.conn.execute("ROLLBACK TO scenario_notice")
                self.conn.execute("RELEASE scenario_notice")
                self.conn.execute("INSERT INTO llm_scenario_notice_errors VALUES(?,?)",
                                  (self.clock() if observed_at is None else observed_at, "notice_validation_or_enqueue_failed"))
        # Live orders can be managed only with exact current evidence. Acceptance
        # and UNKNOWN retain the fence. Missing fresh evidence revokes LIVE status.
        seen_live = set()
        for item in result.get("intents", []):
            ident = item.get("intent_id")
            row = self.conn.execute("SELECT status, evidence FROM llm_scenario_intents WHERE id=?", (ident,)).fetchone()
            if not row or row[0] == "TERMINAL":
                continue
            exact = (item.get("protection_ok") is True
                    and item.get("orders_reconciled") is True
                    and item.get("executions_complete") is True
                    and isinstance(item.get("execution_ids"), list)
                    and all(isinstance(x, str) and x for x in item["execution_ids"])
                    and len(set(item["execution_ids"])) == len(item["execution_ids"])
                    and _finite(item.get("filled_quantity")) and item["filled_quantity"] >= 0
                    and (item["filled_quantity"] == 0 or item["execution_ids"]))
            if exact and item.get("terminal") is True:
                self.conn.execute("UPDATE llm_scenario_intents SET status='TERMINAL', evidence=? WHERE id=?",
                                  (_json(item), ident))
                state["version"] += 1
            elif exact and item.get("terminal") is False:
                orders = item.get("open_entries")
                ids = item.get("exchange_order_ids")
                if (not isinstance(ids, list) or not ids or
                        any(not isinstance(x, str) or not x for x in ids) or
                        len(set(ids)) != len(ids) or not isinstance(orders, list) or
                        any(not _valid_open_entry(o) for o in orders)):
                    continue
                seen_live.add(ident)
                encoded = _json(item)
                if (row[0] != "LIVE_RECONCILED" or not row[1] or
                        _economic_evidence(json.loads(row[1])) != _economic_evidence(item)):
                    state["version"] += 1
                self.conn.execute("UPDATE llm_scenario_intents SET status='LIVE_RECONCILED', evidence=? WHERE id=?", (encoded, ident))
        for (ident,) in self.conn.execute("SELECT id FROM llm_scenario_intents WHERE status='LIVE_RECONCILED'").fetchall():
            if ident not in incident_epoch.pending(self.conn, ('LIVE_RECONCILED',)):
                continue
            if ident not in seen_live:
                self.conn.execute("UPDATE llm_scenario_intents SET status='PENDING' WHERE id=?", (ident,))
                state["version"] += 1
        settlement = result.get("settlement")
        active = state["active"]
        pending = incident_epoch.unresolved(self.conn)
        if settlement and active and not pending:
            required = ("gross_pnl", "fees", "funding_net", "net_pnl")
            valid = (settlement.get("scenario_id") == active["scenario_id"]
                     and settlement.get("flat_confirmed") is True
                     and settlement.get("orders_terminal") is True
                     and settlement.get("executions_complete") is True
                     and settlement.get("fees_complete") is True
                     and settlement.get("funding_complete") is True
                     and all(_finite(settlement.get(k)) for k in required)
                     and isinstance(settlement.get("execution_ids"), list)
                     and all(isinstance(x, str) and x for x in settlement["execution_ids"])
                     and len(set(settlement["execution_ids"])) == len(settlement["execution_ids"]))
            if valid and math.isclose(settlement["net_pnl"], settlement["gross_pnl"] - settlement["fees"] + settlement["funding_net"], abs_tol=1e-8):
                # Exact zero-fill cancellation is an abandoned attempt, not a
                # breakeven trade that resets consecutive losing scenarios.
                if not settlement["execution_ids"] and not (
                        settlement.get("no_fills_confirmed") is True and
                        all(settlement[k] == 0 for k in required)):
                    self._save(state)
                    return
                context = self.broker.context()
                if settlement["execution_ids"]:
                    old_count = state["breaker"].get("consecutive_losses", 0)
                    state["breaker"] = update_circuit_breaker(state["breaker"],
                        **{k: context[k] for k in ("day", "day_start_equity", "daily_net_pnl")},
                        completed_scenario_id=active["scenario_id"], completed_net_pnl=settlement["net_pnl"])
                    if (state.get("recovery", {}).get("scenario_id") == active["scenario_id"]
                            and state["recovery"].get("policy_version") != 2):
                        state["breaker"]["consecutive_losses"] = max(
                            old_count, state["breaker"]["consecutive_losses"])
                if state.get("recovery", {}).get("scenario_id") == active["scenario_id"]:
                    recovery.settled(self.conn, state, settlement,
                                     self.clock() if observed_at is None else observed_at)
                inserted = self.conn.execute("INSERT OR IGNORE INTO llm_scenario_settlements VALUES(?,?)",
                                  (active["scenario_id"], _json(settlement)))
                if inserted.rowcount == 1:
                    from live.scenario_provenance import record
                    record("settlement_recorded", {"settlement": settlement},
                           scenario_id=active["scenario_id"])
                state["active"] = None
                state["version"] += 1
        self._save(state)
        if (state["active"] is None and result.get("protection_confirmed") is True
                and result.get("protection_status") == "verified_flat_no_exposure"
                and isinstance(result.get("observation"), dict)):
            return result["observation"].get("account_version")

    def _context(self, state, *, observed_at=None):
        ctx = dict(self.broker.context())
        # Renewal authority is constructed by this runtime, never broker metadata.
        for key in ('pending_wait_policy_version', 'pending_renewal_version', 'pending_renewal_offer', 'pending_wait',
                    'entry_contract_version', 'registered_entry_contract', 'entry_contract_status',
                    'entry_contract_failure_rule', 'entry_contract_observation', 'previous_thesis_assessment',
                    'entry_convergence', 'entry_goal_source', 'entry_goal_untrusted', 'entry_goal_decision_slot', 'immediate_reduce_version',
                    'explanation_contract_version', 'explanation_source'):
            ctx.pop(key, None)
        if ('immediate_reduce_v1' in self.broker.capabilities
                and callable(getattr(self.broker, 'prepare_reduce', None))):
            ctx['immediate_reduce_version'] = 1
            ctx['unknown_intents'] = bool(incident_epoch.pending(self.conn))
        hard_risk_blocked = ctx.get("new_risk_blocked")
        for flag in ("legacy_fenced", "protection_ok", "new_risk_blocked"):
            if type(ctx.get(flag)) is not bool:
                raise ValueError("unconfirmed_account_context")
        if not isinstance(ctx.get("account_version"), str) or not ctx["account_version"]:
            raise ValueError("missing_account_version")
        # Every known live remainder must reserve its exact quantity and price.
        pending = {o["id"]: o for o in ctx["pending_entries"]}
        for ident, body in self.conn.execute("SELECT id,evidence FROM llm_scenario_intents WHERE status='LIVE_RECONCILED'"):
            if ident not in incident_epoch.pending(self.conn, ('LIVE_RECONCILED',)):
                continue
            for order in json.loads(body)["open_entries"]:
                if pending.get(order["id"]) != order:
                    raise ValueError("live_order_risk_not_reserved")
        state["breaker"] = update_circuit_breaker(state["breaker"],
            **{k: ctx[k] for k in ("day", "day_start_equity", "daily_net_pnl")})
        active = state["active"]
        observe_review(active, ctx, self.clock() if observed_at is None else observed_at)
        ctx.update(review_contract_version=1,
                   review_memory=json.loads(_json(active.get("review_memory", []))) if active else [],
                   review_status=active.get("review_status", "not_initialized") if active else "no_scenario")
        ctx.update(review_context(active))
        ctx.update(scenario_id=active["scenario_id"] if active else None,
                   revision=active["revision"] if active else 0,
                   seen_action_ids=[r[0] for r in self.conn.execute("SELECT id FROM llm_scenario_intents")])
        has_registered_contract = bool(active and self.conn.execute(
            "SELECT 1 FROM llm_scenario_entry_contracts WHERE scenario_id=?", (active["scenario_id"],)).fetchone())
        if self.entry_contract_enabled or has_registered_contract or (active and "entry_contract" in active):
            # Broker REST may finish after the caller's pre-capture timestamp.
            # Judge freshness at the actual post-capture clock; never rewrite
            # exchange/account timestamps or reset samples using the old time.
            observed_now = self.clock()
            if active and (has_registered_contract or "entry_contract" in active) and not self._entry_contract_registered(active):
                ctx.update(entry_contract_version=1, registered_entry_contract=None,
                           entry_contract_status="CORRUPT", entry_contract_observation=None,
                           entry_contract_failure_rule=None)
                registered = self.conn.execute("SELECT body FROM llm_scenario_entry_contracts WHERE scenario_id=?",
                                               (active['scenario_id'],)).fetchone()
                try:
                    saved_contract = json.loads(registered[0]) if registered else {}
                except (ValueError, TypeError):
                    saved_contract = None
                from live.scenario_entry_contract import goal_record_marker, has_goal_contract
                if (has_goal_contract(self.conn, active)
                        or goal_record_marker(saved_contract)):
                    ctx['entry_goal_untrusted'] = True
            else:
                observe_contract(active, ctx, int(observed_now // 300), observed_now)
                ctx.update(contract_context(active, True))
                if (active and (active.get('entry_contract') or {}).get('version') == 2
                        and not self._entry_goal_registered(active)):
                    ctx['entry_goal_untrusted'] = True
        if self.entry_goal_enabled and not active:
            ctx['entry_contract_version'] = 2
        if active:
            ctx.update(initial_equity=active["initial_equity"], side=active["side"],
                       previous_hard_stop=active["hard_stop"])
            plans=self.conn.execute("SELECT payload,status,evidence FROM llm_scenario_intents WHERE scenario_id=? ORDER BY rowid DESC LIMIT 5",
                                    (active["scenario_id"],)).fetchall()
            ctx["current_plan"]=active.get("current_plan") or next((json.loads(p[0]) for p in plans
                if json.loads(p[0])["action"] in {"OPEN","ADJUST"}),None)
            ctx["recent_actions"]=[{
                "action":json.loads(payload)["action"],"revision":json.loads(payload)["revision"],
                "status":status,"verified_executed_quantity":json.loads(evidence).get("filled_quantity") if evidence else None
            } for payload,status,evidence in reversed(plans)]
        else:
            ctx.update(current_plan=None,recent_actions=[])
        # Past validated WAIT reasons are observations, never executable plans.
        # Do not feed rejected/stale proposals or another active scenario back.
        slot = int((self.clock() if observed_at is None else observed_at) // 300)
        waits = []
        rows = self.conn.execute(
            "SELECT context,proposal,outcome FROM llm_scenario_decisions "
            "WHERE slot>=? AND slot<? AND outcome IS NOT NULL ORDER BY slot DESC",
            (slot - 6, slot)).fetchall()
        for context, proposal, outcome in rows:
            if json.loads(outcome).get("status") != "wait" or not context or not proposal:
                continue
            previous, decision = json.loads(context), json.loads(proposal)
            if previous.get("scenario_id") != ctx["scenario_id"] or decision.get("action") != "WAIT":
                continue
            waits.append({"as_of_ms": int(previous["input_captured_at"] * 1000),
                          "rationale": decision["rationale"],
                          "confidence": decision["confidence"]})
            if len(waits) == 3:
                break
        ctx["recent_waits"] = list(reversed(waits))
        if self.recovery_enabled:
            ctx["recovery_hard_blocked"] = ctx["new_risk_blocked"]
            if self.automatic_normalization_enabled:
                recovery.migrate(self.conn, state, ctx, self.clock() if observed_at is None else observed_at)
            allowed = recovery.observing_allowed(state, ctx)
            no_intents = not incident_epoch.unresolved(self.conn)
            if allowed and no_intents:
                ctx["recovery_observing"] = True
                ctx["scenario_risk_fraction"] = recovery.offered_fraction(state)
            elif active and recovery.managed_permit(state, active):
                permit = recovery.managed_permit(state, active)
                ctx["scenario_risk_fraction"] = recovery.risk_fraction(state, active["scenario_id"])
                ctx["recovery_contract_version"] = permit.get("policy_version", 1)
                ctx["recovery"] = {"phase": permit["phase"], "consumed": True,
                    "eligible": False, "risk_fraction": ctx["scenario_risk_fraction"],
                    "permit_id": permit["permit_id"], "scenario_id": permit["scenario_id"],
                    "budget": permit["budget"], "entry_deadline": permit["entry_deadline"],
                    "one_attempt_only": True, "automatic_normal_resume": False}
                if permit.get("policy_version") == 2:
                    ctx["recovery"].update(current_stage=permit["stage"], offered_stage=permit["stage"],
                        policy_version=2, one_attempt_only=False, automatic_normal_resume=True,
                        promotion_rule_version=permit["stage_evidence"].get("promotion_rule_version"),
                        promotion_evidence=permit["stage_evidence"])
                ctx["recovery_normal_permission"] = recovery.normal_permission(state, active, ctx)
        ctx["new_risk_blocked"] = bool(ctx["new_risk_blocked"] or (
            state["breaker"].get("blocked") and not ctx.get("recovery_observing")
            and not ctx.get("recovery_normal_permission")))
        if self.pending_renewal_enabled and not active:
            ctx['pending_wait_policy_version'] = 1
        elif (active and active.get('pending_wait_anchor')
              and ctx.get('positions') == [] and ctx.get('pending_entries')):
            from live.scenario_pending_renewal import load, offer
            lease = load(self.conn, active)
            ctx['pending_wait_policy_version'] = 1
            if lease:
                ctx['pending_wait'] = dict(lease_expires_at=lease['until'],
                                          absolute_expires_at=lease['anchor']['hard_deadline'])
                offered = (offer(self.conn, state, ctx, self.broker.children(active['scenario_id']),
                                 self.clock() if observed_at is None else observed_at)
                           if self.pending_renewal_enabled and self.calculated_review_enabled
                           and not ctx.get('entry_goal_untrusted') else None)
                if offered:
                    ctx.update(pending_renewal_version=1, pending_renewal_offer=offered)
        from core.scenario_stop_policy import build_stop_policy
        ctx.setdefault("stop_policy", build_stop_policy(active,
            hard_blocked=hard_risk_blocked,
            permit_valid=not state["breaker"].get("blocked") or bool(
                set(state["breaker"].get("reasons", [])) == {"three_losses"}
                and recovery.managed_permit(state, active)),
            accounting_status=ctx.get("accounting_status"), protection_ok=ctx.get("protection_ok"),
            legacy_fenced=ctx.get("legacy_fenced"), pending_entries=ctx.get("pending_entries"),
            positions=ctx.get("positions"), unknown_intents=bool(incident_epoch.pending(self.conn))))
        if state["breaker"].get("blocked") and not state.get("halt_notified"):
            stamp=state.setdefault("halted_at",self.clock() if observed_at is None else observed_at)
            reasons=set(state["breaker"].get("reasons",[]))
            event={"kind":"HALTED","timestamp":stamp,"reason_code":
                   "DAILY_LOSS" if "daily_loss" in reasons else "THREE_LOSSES",
                   "automatic_normalization":self.automatic_normalization_enabled and reasons=={"three_losses"}}
            state["halt_notified"]=self._notice(f"halt:{stamp}",event,observed_at=observed_at)
        self._save(state)
        return ctx

    def _entry_contract_registered(self, active):
        """The original insert-only registration is independent of mutable state."""
        from live.scenario_entry_contract import registered_contract
        return registered_contract(self.conn, active)

    def _entry_goal_registered(self, active):
        if not self._entry_contract_registered(active):
            return False
        try:
            if active['entry_contract']['version'] == 2:
                from live.scenario_entry_goal import stored_goal
                stored_goal(self.conn, active['entry_contract'])
            return True
        except (ValueError, TypeError, KeyError, AttributeError, sqlite3.Error):
            return False

    def _entry_contract_exit(self, state, ctx, slot):
        if ctx.get('management_quarantine_block'):
            return dict(status='blocked', reason={'RELEASE_REQUIRED': 'quarantine_release_required',
                'FLAT_UNSETTLED': 'flat_quarantined_unsettled'}.get(ctx['management_quarantine_block'],
                'management_quarantine_authority_invalid'), model_called=False)
        """Commit a latched sampled failure through the normal broker EXIT adapter."""
        observation = ctx.get("entry_contract_observation") or {}
        active = state.get("active") or {}
        cached_failure = active.get("entry_contract_model_failure") or active.get("entry_contract_failure")
        last_failed = isinstance(active.get("last_thesis_assessment"), dict) and active["last_thesis_assessment"].get("status") == "FAILED"
        failure_row = self.conn.execute("SELECT body FROM llm_scenario_entry_failures WHERE scenario_id=?",
            (state["active"]["scenario_id"],)).fetchone() if state.get("active") else None
        contract_id = active.get("entry_contract", {}).get("contract_id") if isinstance(active.get("entry_contract"), dict) else None
        host_intent = self.conn.execute("SELECT 1 FROM llm_scenario_intents WHERE id=? AND scenario_id=?",
            ("ec_exit_" + contract_id[:40], active.get("scenario_id"))).fetchone() if isinstance(contract_id, str) else None
        cached_observation = active.get("entry_contract_observation")
        suspect = bool(failure_row or cached_failure or host_intent or last_failed
                       or (isinstance(cached_observation, dict) and cached_observation.get("status") == "FAILED"))
        trusted = ctx.get("entry_contract_status") == "REGISTERED" and self._entry_contract_registered(active)
        if suspect and not trusted:
            return dict(status="blocked", reason="entry_contract_failed_state_untrusted", model_called=False)
        if not trusted:
            return None
        if failure_row or cached_failure or host_intent or last_failed:
            try:
                failure = json.loads(failure_row[0]) if failure_row else None
                record = ctx["registered_entry_contract"]
                if (not isinstance(failure, dict) or failure.get("contract") != record
                        or failure.get("contract_id") != record["contract_id"]
                        or failure.get("source") not in ("HOST_SAMPLED_MARK_PRICE", "MODEL_ASSESSMENT")):
                    raise ValueError("failure_registration")
                row = self.conn.execute("SELECT payload,status FROM llm_scenario_intents WHERE id=? AND scenario_id=?",
                    (failure.get("intent_id"), active["scenario_id"])).fetchone()
                if not row or json.loads(row[0]).get("action") != "EXIT":
                    raise ValueError("failure_intent")
                if failure["source"] == "MODEL_ASSESSMENT":
                    if (failure.get("assessment", {}).get("status") != "FAILED"
                            or failure.get("assessment", {}).get("contract_id") != record["contract_id"]):
                        raise ValueError("failure_assessment")
                else:
                    if (failure.get("observation", {}).get("status") != "FAILED"
                            or failure.get("observation", {}).get("contract_id") != record["contract_id"]):
                        raise ValueError("failure_samples")
            except (ValueError, TypeError, AttributeError):
                return dict(status="blocked", reason="entry_contract_failed_state_untrusted", model_called=False)
            return dict(status="blocked" if row[1] == "TERMINAL" else "intent_pending",
                        reason="entry_contract_failed_exit_unresolved", model_called=False)
        if observation.get("status") != "FAILED":
            return None
        if ctx["legacy_fenced"] or not ctx["protection_ok"]:
            return dict(status="fenced")
        if (incident_epoch.pending(self.conn)
                and not quarantine.pending_allowed(self.broker, ctx)):
            return dict(status="intent_pending")
        for body, in self.conn.execute("SELECT payload FROM llm_scenario_intents WHERE scenario_id=? AND status!='TERMINAL'",
                                       (state["active"]["scenario_id"],)):
            if json.loads(body).get("action") == "EXIT":
                return dict(status="intent_pending", reason="entry_contract_exit_already_committed")
        now = self.clock()
        captured = ctx.get("account_captured_at")
        if not _finite(captured) or not 0 <= now-captured <= 120:
            return dict(status="blocked", reason="entry_contract_exit_account_stale")
        record = ctx["registered_entry_contract"]
        identity = "ec_exit_" + record["contract_id"][:40]
        # A durable intent is never resubmitted, including uncertain responses.
        existing = self.conn.execute("SELECT status FROM llm_scenario_intents WHERE id=?", (identity,)).fetchone()
        if existing:
            return dict(status="blocked" if existing[0] == "TERMINAL" else "intent_pending",
                        reason="entry_contract_failed_exit_unresolved")
        fresh = dict(ctx, now=now, input_id="ec_" + record["contract_id"],
                     input_captured_at=captured, max_input_age_seconds=120)
        payload = dict(schema_version=1, scenario_id=state["active"]["scenario_id"],
            revision=ctx["revision"]+1, input_id=fresh["input_id"], action_id=identity,
            action="EXIT", confidence=1, expires_at=now+90, entries=[], take_profits=[],
            partial_stops=[], cancel_entry_ids=[row["id"] for row in ctx["pending_entries"]],
            rationale="Registered entry failure confirmed by consecutive sampled MARK_PRICE observations", leverage=10)
        validated = validate_execution_prices(payload, fresh)
        failure = dict(source="HOST_SAMPLED_MARK_PRICE", contract=record, contract_id=record["contract_id"],
                       intent_id=identity, observation=observation)
        self.conn.execute("INSERT INTO llm_scenario_entry_failures VALUES(?,?)",
                          (validated["scenario_id"], _json(failure)))
        state["active"]["entry_contract_failure"] = failure
        state["active"]["revision"] = validated["revision"]
        state["version"] += 1
        self.conn.execute("INSERT INTO llm_scenario_intents VALUES(?,?,?,'PENDING',NULL)",
                          (identity, validated["scenario_id"], _json(validated)))
        if ctx.get('management_quarantine'):
            quarantine.bind_exit(self.broker, validated, ctx['management_quarantine'])
        self._save(state)
        from live.scenario_provenance import record_hashed
        record_hashed("entry_contract_failure_exit", dict(contract=record, observation=observation,
            payload=validated, source="HOST_SAMPLED_MARK_PRICE"),
            scenario_id=validated["scenario_id"], intent_id=identity, decision_slot=slot)
        try:
            self.broker.execute(validated, identity)
        except Exception as exc:
            from live.scenario_broker import BrokerCancellationPending
            return dict(status="intent_pending", reason="awaiting_cancellation" if isinstance(
                exc, BrokerCancellationPending) else "submission_unknown", model_called=False)
        return dict(status="intent_pending", reason="awaiting_exact_evidence", model_called=False,
                    **({'management_quarantine': ctx['management_quarantine']} if ctx.get('management_quarantine') else {}))

    def _cancel_converging_entries(self, state, ctx, slot):
        """Host cancellation only; never fabricate a model judgment or a fill."""
        from live.scenario_contract import identity_fields
        if not state.get('active'):
            return dict(status='blocked', reason='convergence_pending_owner_missing', model_called=False)
        payload = dict(identity_fields(ctx), action='WAIT', confidence=0,
            expires_at=self.clock()+90, entries=[], take_profits=[], partial_stops=[],
            cancel_entry_ids=[row['id'] for row in ctx['pending_entries']], leverage=10,
            rationale='1시간·30분 확정 MA 간격이 모두 수렴해 미체결 진입을 취소합니다.')
        validated = validate_execution_prices(payload, dict(ctx, now=self.clock()))
        identity = validated['action_id']
        self.conn.execute('UPDATE llm_scenario_decisions SET proposal=? WHERE slot=?', (_json(payload), slot))
        self.conn.execute("INSERT INTO llm_scenario_intents VALUES(?,?,?,'PENDING',NULL)",
                          (identity, validated['scenario_id'], _json(validated)))
        state['active']['revision'] = validated['revision']
        state['version'] += 1
        self._save(state)
        from live.scenario_provenance import record_hashed
        record_hashed('intent_committed', dict(payload=validated, source='HOST_ENTRY_CONVERGENCE',
            entry_convergence=ctx['entry_convergence']), scenario_id=validated['scenario_id'],
            intent_id=identity, decision_slot=slot)
        try:
            self.broker.execute(validated, identity)
        except Exception as exc:
            from live.scenario_broker import BrokerCancellationPending
            return dict(status='intent_pending', reason='awaiting_cancellation' if isinstance(
                exc, BrokerCancellationPending) else 'submission_unknown', model_called=False)
        return dict(status='intent_pending', reason='awaiting_exact_evidence', model_called=False)

    def _tick(self, claimed):
        """One synchronous tick; callers may run independent protection separately."""
        slot = int(self.clock() // 300)
        stage = "initial_reconcile"
        snapshot_lock = {"contended": False, "lock_wait_seconds": 0.}
        try:
            if (getattr(self.broker, "environment", None) != "demo" or
                    getattr(self.broker, "lane", None) != "MAIN"):
                return {"status": "execution_disabled", "reason": "wrong_account_identity"}
            with mutation_lock(self.conn):
                state = self.state()
                self._reconcile(state)
                if not self._enabled():
                    return {"status": "execution_disabled", "reason": "broker_capabilities_unverified"}
                ctx = self._context(state)
                if ctx.get('management_quarantine_block'):
                    return dict(status='blocked', reason={'RELEASE_REQUIRED': 'quarantine_release_required',
                        'FLAT_UNSETTLED': 'flat_quarantined_unsettled'}.get(ctx['management_quarantine_block'],
                        'management_quarantine_authority_invalid'))
                if ctx["legacy_fenced"] or not ctx["protection_ok"]:
                    return {"status": "fenced"}
                if ctx["new_risk_blocked"] and state["active"] is None:
                    return {"status":"blocked","reason":"new_risk_halted",
                            "verified_flat_halt": (ctx.get("positions") == []
                                and ctx.get("pending_entries") == []
                                and ctx.get("accounting_status") == "confirmed"
                                and not incident_epoch.unresolved(self.conn))}
                if (incident_epoch.pending(self.conn)
                        and not quarantine.pending_allowed(self.broker, ctx)):
                    return {"status": "intent_pending"}
                contract_exit = self._entry_contract_exit(state, ctx, slot)
                if contract_exit is not None:
                    return contract_exit
                if self.conn.execute("SELECT 1 FROM llm_scenario_slots WHERE slot=?", (slot,)).fetchone():
                    return {"status": "duplicate_slot"}
                self.conn.execute("INSERT INTO llm_scenario_slots VALUES(?, 'CLAIMED')", (slot,))
                self.conn.execute("INSERT INTO llm_scenario_decisions(slot) VALUES(?)", (slot,))
                self.conn.commit()  # Crash during LLM deliberately burns this slot.
                claimed.append(slot)
                version = state["version"]
            stage = "snapshot_collection"
            snap = self.snapshot()
            stage = "snapshot_validation"
            if snap.get("valid") is not True:
                raise ValueError("invalid_market_snapshot")
            captured = snapshot_input_time(snap)
            input_id = str(uuid.uuid4())
            ctx.update(now=self.clock(), input_id=input_id, input_captured_at=captured,
                       max_input_age_seconds=120)
            stage = "snapshot_persist"
            with _snapshot_mutation_lock(self.conn, snapshot_lock):
                current = self.state()
                now = self.clock()
                account_at = ctx.get("account_captured_at")
                reason = None
                if not _finite(now):
                    reason = "snapshot_stale"
                elif current["version"] != version:
                    reason = "snapshot_state_changed"
                elif int(now//300) != slot:
                    reason = "snapshot_slot_changed"
                elif not 0 <= now-captured <= 120:
                    reason = "snapshot_stale"
                elif (snapshot_lock["contended"]
                        and (not _finite(account_at) or not 0 <= now-account_at <= 120)):
                    reason = "snapshot_account_stale"
                if reason:
                    outcome = {"status": "stale_proposal", "reason": reason,
                               "failure_stage": stage, "model_called": False}
                    if snapshot_lock["contended"]:
                        outcome["lock_wait_seconds"] = snapshot_lock["lock_wait_seconds"]
                    return outcome
                ctx["now"] = now
                if self.convergence_entry_enabled:
                    from core.scenario_convergence import entry_convergence
                    ctx['entry_convergence'] = entry_convergence(snap)
                if (self.calculated_review_enabled and 'single' not in self.research_review_mode
                        and ctx.get('scenario_id') is not None and ctx.get('positions')):
                    from live.scenario_explanation import prepare_source
                    ctx['explanation_contract_version'] = 1
                    prepare_source(snap, ctx)
                if ctx.get('entry_contract_version') == 2 and ctx.get('scenario_id') is None:
                    ctx['entry_goal_decision_slot'] = slot
                from live.scenario_entry_goal import prepare_goal_source
                prepare_goal_source(snap, ctx)
                from live.scenario_position_flow import build_position_flow, build_closed_continuity
                ctx["position_flow"] = build_position_flow(self.conn, current["active"], ctx, now)
                ctx['last_closed_trade'] = build_closed_continuity(self.conn, current, now)
                if ctx.get("recovery_observing"):
                    recovery.prepare_input(self.conn, current, ctx, snap, slot, self.clock())
                    # Baseline, mandatory INPUT and the full decision input must
                    # share one commit. _save() would commit the baseline early.
                    self.conn.execute("UPDATE llm_scenario_state SET body=? WHERE id=1", (_json(current),))
                self.conn.execute("UPDATE llm_scenario_decisions SET snapshot=?, context=? WHERE slot=?",
                                  (_json(snap), _json(ctx), slot))
                self.conn.commit()
                if (self.convergence_entry_enabled and ctx['entry_convergence']['status'] == 'BLOCKED'
                        and ctx.get('pending_entries')):
                    return self._cancel_converging_entries(current, ctx, slot)
            stage = "proposal_call"
            from live.scenario_provenance import record_hashed
            record_hashed("decision_input", {"snapshot": snap, "context": ctx, "input_id": input_id},
                          scenario_id=ctx.get("scenario_id"), decision_slot=slot)
            review_envelope = review_bindings = None
            if self.calculated_review_enabled:
                from live.scenario_contract import validate_wire_proposal
                from live.scenario_provenance import review_policy_binding
                from analysis.scenario_review_tape import MAX_BYTES
                review_bindings = review_policy_binding(research_mode=self.research_review_mode)
                review_envelope = self.calculated_review_propose(snap, ctx,
                    record=lambda event: self._record_calculated_review(slot, event))
                # Input projection records insertion order; sorting loses replay evidence.
                envelope_body = json.dumps(review_envelope, allow_nan=False)
                if len(envelope_body.encode()) > MAX_BYTES:
                    raise ValueError("review_tape_bound")
                from live.scenario_review_storage import tape_storage, validate_tape_for_commit
                stored_tape = tape_storage(self.conn, slot, envelope_body)
                stored_tape = validate_tape_for_commit(self.conn, slot, envelope_body, stored_tape)
                self.conn.execute("INSERT INTO llm_scenario_review_tapes VALUES(?,?)",
                                  (slot, stored_tape))
                self.conn.commit()
                stored_input = self.conn.execute(
                    "SELECT snapshot,context FROM llm_scenario_decisions WHERE slot=?", (slot,)).fetchone()
                if stored_input is None or tuple(stored_input) != (_json(snap), _json(ctx)):
                    raise ValueError("review_input_mutated")
                if review_envelope.get("outcome", {}).get("status") != "COMPLETE":
                    failed = review_envelope.get("outcome", {})
                    if (failed.get("error") == {"type": "ValueError", "message": "research_input_size"}
                            and review_envelope.get("calls") == 0
                            and all(op.get("kind") == "clock" for op in review_envelope.get("operations", []))):
                        # Preserve the failed tape; no request/generator was reached.
                        raise ScenarioModelError("input_size")
                    raise ValueError("review_tape_incomplete")
                from live.scenario_review_storage import review_text
                recorded_events = [json.loads(review_text(row[0])) for row in self.conn.execute(
                    "SELECT body FROM llm_scenario_review_events WHERE slot=? ORDER BY sequence", (slot,))]
                expected_events = [operation["event"] for operation in review_envelope["operations"]
                                   if operation["kind"] == "record"]
                if recorded_events != expected_events:
                    raise ValueError("review_durable_events_mismatch")
                if review_envelope["outcome"]["result"].get("disposition") == "REJECTED_NO_ACTION":
                    from live.scenario_preview import response_contract
                    from live.scenario_review_guard import guard_rejected_review
                    rejected = guard_rejected_review(snap, ctx, response_contract(ctx), review_envelope,
                        bindings=review_bindings, research_mode=self.research_review_mode)
                    # No selected payload, recovery assessment, review, permit,
                    # intent or cancellation authority is created on this path.
                    return dict(status="rejected_no_action", reason="economic_rejection",
                                rejection_codes=rejected["rejection_codes"],
                                rejection_receipt=rejected)
                payload = validate_wire_proposal(
                    review_envelope["outcome"]["result"]["selected"]["raw_proposal"], ctx)
                # Persist the candidate's own rationale; selection prose stays in the review tape.
            elif self.recovery_enabled:
                with recovery.capture_wire(self.conn, slot, self.clock):
                    payload = self.propose(snap, ctx)
            else:
                payload = self.propose(snap, ctx)  # No broker mutation lock held.
            if isinstance(payload, dict) and "review" in payload:
                try:
                    _json(payload["review"])
                except (ValueError, TypeError, OverflowError):
                    payload = dict(payload, review={"invalid_metadata": "not_json_serializable"})
            # Audit-only write: an already-claimed slot is owned by this caller.
            # Persist the proposal even if independent protection owns the trading
            # mutex when the model returns. This does NOT authorize execution.
            stage = "proposal_persist"
            persisted_proposal_json = _json(payload)
            self.conn.execute("UPDATE llm_scenario_decisions SET proposal=? WHERE slot=? AND proposal IS NULL",
                              (persisted_proposal_json, slot))
            self.conn.commit()
            stage = "post_model_reconcile"
            with _post_model_mutation_lock(self.conn):
                state = self.state()
                verified_flat_version = self._reconcile(state)
                fresh = self._context(state)
                if self.convergence_entry_enabled:
                    fresh['entry_convergence'] = entry_convergence(snap)
                contract_exit = self._entry_contract_exit(state, fresh, slot)
                if contract_exit is not None:
                    return contract_exit
                if self.recovery_enabled:
                    stage = "recovery_recheck"
                    try:
                        recovery.journal(self.conn, "RECHECK", {
                            "input_id": input_id, "input_state_version": version,
                            "current_state_version": state["version"],
                            "input_account_version": ctx["account_version"],
                            "context": fresh, "breaker": state["breaker"],
                            "active": state["active"],
                            "recovery_observing_requested": bool(ctx.get("recovery_observing"))},
                            self.clock(), slot=slot, scenario_id=fresh.get("scenario_id"))
                        self.conn.commit()
                    except Exception:
                        self.conn.rollback()
                        if ctx.get("recovery_observing"):
                            raise ValueError("recovery_recheck_log_unavailable") from None
                if (state["version"] != version or fresh["account_version"] != ctx["account_version"]
                        or fresh["legacy_fenced"] or not fresh["protection_ok"]):
                    return {"status": "stale_proposal"}
                fresh.update(now=self.clock(), input_id=input_id, input_captured_at=captured,
                             max_input_age_seconds=120)
                stage = "validation"
                probe = False
                if self.calculated_review_enabled:
                    from live.scenario_review_guard import guard_review
                    from live.scenario_preview import response_contract
                    from live.scenario_provenance import review_policy_binding
                    if review_policy_binding(research_mode=self.research_review_mode) != review_bindings:
                        raise ValueError("review_policy_changed")
                    guarded = guard_review(snap, ctx, response_contract(ctx), review_envelope,
                                           fresh, bindings=review_bindings, research_mode=self.research_review_mode)
                    payload, validated = guarded["payload"], guarded["validated"]
                    self._record_calculated_review(slot, dict(kind="FRESH_REVIEW_RECEIPT",
                                                             receipt=guarded["receipt"]))
                    if self.recovery_enabled:
                        # These are original phase texts, never a host-normalized
                        # proposal disguised as a model response.
                        for operation in review_envelope["operations"]:
                            if operation["kind"] == "record" and operation["event"]["kind"] in {"MODEL_REQUEST", "MODEL_RAW"}:
                                event = operation["event"]
                                recovery.journal(self.conn, event["kind"], dict(event, calculated_review_version=1),
                                                 self.clock(), slot=slot)
                        recovery.journal(self.conn, "CALCULATED_REVIEW_SELECTION", guarded["receipt"],
                                         self.clock(), slot=slot)
                        self.conn.commit()
                    self._assert_calculated_review_time(review_envelope, ctx, fresh, payload)
                if ctx.get("recovery_observing"):
                    if not fresh.get("recovery_observing"):
                        return {"status": "blocked", "reason": "recovery_hard_guard"}
                    probe = recovery.assess(self.conn, state, ctx, payload, slot, self.clock())
                    self.conn.commit()
                if self.calculated_review_enabled:
                    self._assert_calculated_review_time(review_envelope, ctx, fresh, payload)
                    # Complete all elapsed review/journal I/O before the atomic
                    # observation/intent cut, while authority is still untouched.
                    if review_policy_binding(research_mode=self.research_review_mode) != review_bindings:
                        raise ValueError("review_policy_changed")
                    commit_at = self.clock()
                    reuse_flat = (
                        validated["action"] == "WAIT" and not probe and state["active"] is None
                        and not validated.get("cancel_entry_ids") and not validated.get("pending_renewal")
                        and verified_flat_version == fresh["account_version"]
                        and fresh.get("unknown_intents") is False
                        and fresh.get("new_risk_blocked") is False
                        and not fresh.get("management_quarantine")
                        and fresh.get("positions") == [] and fresh.get("pending_entries") == []
                        and fresh.get("target_status") in ([], {})
                        and fresh.get("accounting_status") == "confirmed"
                        and 0 <= commit_at - fresh["account_captured_at"] <= 10)
                    if reuse_flat:
                        # A flat WAIT creates no authority; retain the real capture time.
                        if self.state() != state:
                            return {"status": "stale_proposal"}
                        commit_context = dict(fresh)
                    else:
                        self._reconcile(state, observed_at=commit_at)
                        commit_context = self._context(state, observed_at=commit_at)
                        if self.convergence_entry_enabled:
                            commit_context['entry_convergence'] = entry_convergence(snap)
                        contract_exit = self._entry_contract_exit(state, commit_context, slot)
                        if contract_exit is not None:
                            return contract_exit
                    # Broker REST capture occurs after observation started.
                    # Never stamp validation before the captured account itself.
                    commit_at = self.clock()
                    commit_context.update(now=commit_at,input_id=input_id,input_captured_at=captured,
                                          max_input_age_seconds=120)
                    commit_context = json.loads(_json(commit_context))
                    # Reprice ORIGINAL raw, not the effective plan. The bounded
                    # post-validation deadline check below bounds this capture.
                    final_guard = guard_review(snap,ctx,response_contract(ctx),review_envelope,
                                               commit_context,bindings=review_bindings,
                                               research_mode=self.research_review_mode)
                    self._assert_calculated_review_time(review_envelope,ctx,commit_context,payload,
                                                        observed_at=commit_at)
                    # Pure verification has real latency too. Advance once after
                    # it and recheck expiry/input/account freshness. One coherent
                    # observation is validated under the mutation lock; this is
                    # NOT a promise that exchange facts cannot change afterward.
                    # Executor capture/preflight remains mandatory before orders.
                    if reuse_flat and self.state() != state:
                        return {"status": "stale_proposal"}
                    post_validation_at = self.clock()
                    self._assert_calculated_review_time(review_envelope,ctx,commit_context,payload,
                                                        observed_at=post_validation_at)
                    if reuse_flat and post_validation_at - commit_context["account_captured_at"] > 10:
                        return {"status": "stale_proposal"}
                    if self.convergence_entry_enabled and (payload.get('entries') or payload.get('pending_renewal')):
                        from core.scenario_convergence import require_entry_convergence
                        try:
                            require_entry_convergence(dict(commit_context, now=post_validation_at))
                        except ValueError as exc:
                            raise EntryConvergenceRejection(str(exc)) from exc
                    commit_at = post_validation_at
                    validated = final_guard["validated"]
                    fresh = commit_context
                if not self.calculated_review_enabled:
                    validate_metadata(payload, fresh)
                    core_payload = {key: value for key, value in strip_metadata(payload).items() if key not in {"review", "recovery"}}
                    from live.scenario_entry_goal import validate_goal_pair
                    validate_goal_pair(payload, None, ctx)
                    validated = validate_execution_prices(core_payload, fresh)
                    validate_goal_pair(payload, validated, ctx)
                if validated.get('pending_renewal') and (
                        not self.pending_renewal_enabled or not self.calculated_review_enabled
                        or review_envelope['outcome']['result']['selected']['id'] != 'WAIT_RENEWAL'
                        or review_envelope['outcome']['result']['calls'] != 2):
                    raise ValueError('pending_renewal_selected_two_phase_required')
                if ctx.get('management_quarantine') != fresh.get('management_quarantine'):
                    raise ValueError('management_quarantine_authority_changed')
                if fresh.get('management_quarantine'):
                    quarantine.pending_allowed(self.broker, fresh)
                    quarantine.validate_action(validated, fresh)
                if validated["action"] == "WAIT" and not validated.get("cancel_entry_ids") and not validated.get('pending_renewal'):
                    if state["active"] and payload.get("thesis_assessment") is not None:
                        state["active"]["last_thesis_assessment"] = dict(payload["thesis_assessment"])
                        state["active"]["last_thesis_assessment_at"] = ctx["now"]
                    receipt = self._apply_review(state["active"], payload, validated,
                                                 ctx["review_memory"], input_id, slot,
                                                 observed_at=commit_at if self.calculated_review_enabled else None,
                                                 persisted_proposal_json=persisted_proposal_json)
                    self._save(state)
                    return {"status": "wait", **({"review_update_receipt": receipt} if receipt else {}),
                            **({'management_quarantine': fresh['management_quarantine']} if fresh.get('management_quarantine') else {})}
                stage = "execution"
                ident = validated["action_id"]
                if state["active"] is None:
                    if self.conn.execute("SELECT 1 FROM llm_scenario_settlements WHERE scenario_id=?", (validated["scenario_id"],)).fetchone():
                        return {"status": "reused_scenario"}
                    if probe:
                        recovery.consume(self.conn, state, validated, fresh, slot,
                                         commit_at if self.calculated_review_enabled else self.clock())
                    state["active"] = {"scenario_id": validated["scenario_id"], "initial_equity": fresh["initial_equity"],
                        "side": validated["side"], "hard_stop": validated["hard_stop"], "revision": 0,
                        "created_at": commit_at if self.calculated_review_enabled else self.clock(), "expires_at": validated["expires_at"],
                        "stop_policy": {"version": 1, "initial_hard_stop": validated["hard_stop"],
                                        "initial_action_id": validated["action_id"]}}
                    if fresh.get("entry_contract_version") in (1, 2):
                        from live.scenario_entry_goal import plan_goal
                        state["active"]["entry_contract"] = bind_contract(payload["entry_contract"], validated,
                            commit_at if self.calculated_review_enabled else self.clock(),
                            resolved_goal=plan_goal(payload, ctx))
                        self.conn.execute("INSERT INTO llm_scenario_entry_contracts VALUES(?,?)",
                            (validated["scenario_id"], _json(state["active"]["entry_contract"])))
                        if state['active']['entry_contract']['version'] == 2:
                            validated = dict(validated, host_entry_contract_id=state['active']['entry_contract']['contract_id'])
                    from live.scenario_pending_renewal import create_anchor
                    create_anchor(self.conn, state['active'], validated, fresh,
                                  commit_at if self.calculated_review_enabled else self.clock())
                receipt = self._apply_review(state["active"], payload, validated,
                                             ctx["review_memory"], input_id, slot,
                                             observed_at=commit_at if self.calculated_review_enabled else None,
                                             persisted_proposal_json=persisted_proposal_json)
                if payload.get("thesis_assessment") is not None:
                    state["active"]["last_thesis_assessment"] = dict(payload["thesis_assessment"])
                    state["active"]["last_thesis_assessment_at"] = ctx["now"]
                    if payload["thesis_assessment"]["status"] == "FAILED":
                        state["active"]["entry_contract_model_failure"] = dict(
                            source="MODEL_ASSESSMENT", contract=fresh["registered_entry_contract"],
                            intent_id=validated["action_id"], contract_id=payload["thesis_assessment"]["contract_id"],
                            assessment=dict(payload["thesis_assessment"]))
                        self.conn.execute("INSERT INTO llm_scenario_entry_failures VALUES(?,?)",
                            (validated["scenario_id"], _json(state["active"]["entry_contract_model_failure"])))
                state["active"]["revision"] = validated["revision"]
                if validated["action"] in ("OPEN", "ADJUST"):
                    state["active"]["desired_hard_stop"] = validated["hard_stop"]
                    state["active"]["expires_at"] = validated["expires_at"]
                    state["active"]["current_plan"] = validated
                state["version"] += 1
                if self.calculated_review_enabled:
                    self._record_calculated_review(slot,dict(kind="FINAL_FRESH_REVIEW_RECEIPT",
                        atomic_with_intent=True,post_validation_checked_at=commit_at,
                        stability_scope="COHERENT_FINAL_OBSERVATION_EXECUTOR_FRESH_PREFLIGHT_REQUIRED",
                        receipt=final_guard["receipt"]),commit=False)
                self.conn.execute("INSERT INTO llm_scenario_intents VALUES(?,?,?,'PENDING',NULL)",
                                  (ident, validated["scenario_id"], _json(validated)))
                if fresh.get('management_quarantine'):
                    quarantine.bind_exit(self.broker, validated, fresh['management_quarantine'])
                if validated['action'] == 'REDUCE':
                    self.broker.prepare_reduce(validated, ident)
                self._save(state)  # Intent precedes the first exchange side effect.
                record_hashed("intent_committed", {"payload": validated, "risk": validated.get("risk"),
                              "initial_equity": fresh["initial_equity"],
                              "pending_entries": fresh["pending_entries"]},
                              scenario_id=validated["scenario_id"], intent_id=ident, decision_slot=slot)
                if validated["action"] in {"OPEN","ADJUST"}:
                    from live.scenario_pending_renewal import load
                    waiting_lease = load(self.conn, state['active'])
                    entries=validated.get("entries",[])
                    quantity=sum(e["quantity"] for e in entries)
                    price=sum(e["price"]*e["quantity"] for e in entries)/quantity if quantity else None
                    held_quantity=sum(p["quantity"] for p in fresh["positions"])
                    held_average=(sum(p["price"]*p["quantity"] for p in fresh["positions"])/held_quantity
                                  if held_quantity else None)
                    self._notice("plan:"+ident,dict(kind="PLAN",timestamp=self.clock(),side=validated["side"],
                        conditional_entries=[e for e in entries if e.get("trigger_price") is not None],
                        reservation_expires_at=waiting_lease['until'] if waiting_lease else validated['expires_at'],
                        submission_expires_at=validated['expires_at'] if waiting_lease else None,
                        reservation_absolute_expires_at=waiting_lease['anchor']['hard_deadline'] if waiting_lease else None,
                        price=price,hard_stop=validated["hard_stop"],take_profits=validated.get("take_profits",[]),
                        **({'entry_goal': state['active']['entry_contract']['resolved_goal']}
                           if (state['active'].get('entry_contract') or {}).get('version') == 2 else {}),
                        plan_action=validated["action"],quantity=quantity,before_quantity=held_quantity,
                        before_hard_stop=fresh.get("previous_hard_stop"),reference_entry_price=held_average,
                        scenario_initial_equity=fresh["initial_equity"],
                        scenario_budget=validated["risk"]["budget"],scenario_risk=validated["risk"]["total_risk"]))
                    self.conn.commit()
                try:
                    self.broker.execute(validated, ident)
                except Exception as exc:
                    from live.scenario_broker import BrokerCancellationPending, BrokerNotReady, BrokerProtectionPending
                    self._notice("unknown:"+ident,dict(kind="PENDING",timestamp=self.clock()))
                    self.conn.commit()
                    cancellation_only_wait = (validated["action"] == "WAIT"
                        and bool(validated.get("cancel_entry_ids"))
                        and not validated.get("entries") and not validated.get("pending_renewal"))
                    management_adjust = validated["action"] == "ADJUST" and not validated.get("entries")
                    reason = ("awaiting_cancellation" if (validated["action"] in {"EXIT", "REDUCE"}
                              or cancellation_only_wait or management_adjust) and isinstance(exc, BrokerCancellationPending)
                              else "submission_unknown")
                    if (validated['action'] == 'ADJUST' and not validated.get('entries')
                            and isinstance(exc, BrokerProtectionPending)):
                        reason = "awaiting_protection"
                    if (validated['action'] == 'REDUCE' and type(exc) is BrokerNotReady
                            and str(exc) == 'reduce_result_pending'):
                        children = self.conn.execute(
                            "SELECT status,order_id FROM llm_scenario_children WHERE intent_id=? AND scenario_id=? AND kind='reduce'",
                            (ident, validated['scenario_id'])).fetchall()
                        if (len(children) == 1 and children[0][0] == 'LIVE'
                                and isinstance(children[0][1], str) and children[0][1]):
                            reason = 'awaiting_reduce_evidence'
                    return {"status": "intent_pending", "reason": reason,
                            "review_update_receipt": receipt}
                return {"status": "intent_pending", "reason": "awaiting_exact_evidence",
                        "review_update_receipt": receipt,
                        **({'management_quarantine': fresh['management_quarantine']} if fresh.get('management_quarantine') else {})}
        except EntryConvergenceRejection as exc:
            self.conn.rollback()
            return dict(status='stale_proposal' if stage == 'validation' else 'blocked',
                        reason=str(exc), failure_stage=stage)
        except ScenarioModelError as exc:
            self.conn.rollback()
            # Fixed codes only: never expose a response, account or transport error.
            code = str(exc)
            if code in {"snapshot_unavailable", "snapshot_stale", "invalid_contract_context",
                        "invalid_input", "input_size"}:
                if self.recovery_enabled and claimed:
                    try:
                        recovery.journal(self.conn, "MODEL_INPUT_ERROR", {
                            "input_id": ctx.get("input_id"), "code": code,
                            "model_called": False}, self.clock(), slot=slot)
                        self.conn.commit()
                    except Exception:
                        self.conn.rollback()
                return {"status": "blocked", "reason": "llm_input_preparation_failed",
                        "failure_stage": "model_input_preparation", "failure_code": code,
                        "model_called": False}
            reason = ("llm_call_failed" if str(exc) in {"oauth_model_failed", "late_response"}
                      else "llm_output_contract_failed")
            return {"status": "blocked", "reason": reason}
        except LockBusy:
            self.conn.rollback()
            outcome = {"status":"lock_busy","reason":"another_protection_or_execution_tick"}
            if stage == "snapshot_persist":
                outcome.update(failure_stage=stage, lock_wait_seconds=snapshot_lock["lock_wait_seconds"])
            return outcome
        except Exception as exc:
            # No exception text/account data enters public notifications. Previously
            # committed intents and slot claims survive all failures.
            self.conn.rollback()
            outcome = {"status": "blocked", "reason": "reconciliation_or_proposal_failed",
                       "failure_stage": stage}
            safe_codes = {
                "initial_reconcile": {"normalization_state_invalid"},
                "post_model_reconcile": {"normalization_state_invalid"},
                "snapshot_collection": {"empty_public_data", "collection_too_slow",
                    "candle_boundary_crossed_during_collection", "future_public_candle", "public_rate_limited"},
                "snapshot_validation": {"invalid_market_snapshot", "invalid_snapshot_time"},
                "snapshot_persist": {"recovery_market_evidence_missing", "recovery_journal_bound", "normalization_state_invalid"},
                "recovery_recheck": {"recovery_recheck_log_unavailable"},
                "validation": {"recovery_metadata_invalid", "recovery_action_mismatch",
                    "recovery_first_observation", "recovery_no_changed_evidence",
                    "recovery_missing_raw", "recovery_missing_request", "recovery_chase_forbidden",
                    "recovery_entry_deadline_invalid", "recovery_mixed_loaded_code"},
                "execution": {"recovery_probe_risk_invalid", "normalization_state_invalid"},
            }
            code = exc.args[0] if type(exc) is ValueError and len(exc.args) == 1 else None
            if isinstance(code, str) and code in safe_codes.get(stage, ()):
                outcome["failure_code"] = code
            if self.calculated_review_enabled:
                from backtest.scenario_tape import TapeMismatch
                review_code = str(exc)
                review_detail = _fresh_safety_detail(review_code)
                if isinstance(exc, TapeMismatch):
                    outcome["failure_code"] = "review_tape_failure"
                elif review_detail is not None:
                    outcome["failure_code"] = "fresh_safety_rejection"
                    outcome["failure_detail"] = review_detail
                elif review_code in {"review_final_deadline", "review_guard_stale_input",
                        "review_guard_stale_review_clock", "review_guard_invalid_time"}:
                    outcome["failure_code"] = "review_deadline_failure"
                elif review_code.startswith("review_"):
                    outcome["failure_code"] = "review_contract_failure"
            from core.llm_scenario import ScenarioValidationError
            if stage == "validation" and isinstance(exc, ScenarioValidationError):
                fixed = {"risk budget exceeded": "scenario_risk_budget_exceeded",
                         "stop widening forbidden": "scenario_stop_widening_forbidden",
                         "new risk blocked": "scenario_new_risk_blocked",
                         "stop crossed market; use EXIT": "scenario_stop_crossed_market",
                         "stale input or invalid expiry": "scenario_stale_input_or_expiry",
                         "invalid or opposite side": "scenario_opposite_side",
                         "conditional entry expiry exceeds next decision": "scenario_entry_expiry_invalid"}
                outcome["failure_code"] = ("scenario_stop_widening_forbidden"
                    if str(exc).startswith("stop widening forbidden") else
                    fixed.get(str(exc), "scenario_contract_invalid"))
            return outcome

    def tick(self):
        claimed = []
        outcome = self._tick(claimed)
        # Do not overwrite the original audit with a duplicate caller's status.
        if claimed:
            # Metadata only, atomic in SQLite; never contend with the execution
            # mutex a second time while recording an intentional lock_busy skip.
            self.conn.execute("UPDATE llm_scenario_decisions SET outcome=? WHERE slot=? AND outcome IS NULL",
                              (_json(outcome), claimed[0]))
            if self.recovery_enabled:
                try:
                    recovery.journal(self.conn, "OUTCOME", outcome, self.clock(), slot=claimed[0])
                except Exception:
                    # Never undo an intent or stop independent protection.
                    logging.getLogger(__name__).warning("AUDIT_GAP recovery_outcome_journal_failed")
            self.conn.commit()
        return outcome
