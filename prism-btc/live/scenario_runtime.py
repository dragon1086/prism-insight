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
import time
import uuid
from contextlib import ExitStack, contextmanager

from core.llm_scenario import update_circuit_breaker
from core.scenario_limit_prices import validate_execution_prices
from live.shared_entry_coordinator import database_path, mutation_lock
from live.entry_reservations import LockBusy
from live.scenario_llm import ScenarioModelError
from live.scenario_review_memory import apply_review, observe_review
from live import scenario_recovery as recovery

REQUIRED_CAPABILITIES = frozenset({"exact_fills", "atomic_protection",
    "exact_settlement", "idempotent_intents", "fresh_risk_context"})


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
                 recovery_enabled=False, automatic_normalization_enabled=False):
        database_path(conn)  # In-memory state cannot provide restart safety.
        self.conn, self.broker = conn, broker
        self.propose, self.snapshot, self.clock = propose, snapshot, clock
        self.recovery_enabled = recovery_enabled or automatic_normalization_enabled
        self.automatic_normalization_enabled = automatic_normalization_enabled
        with mutation_lock(conn):
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS llm_scenario_state (
                    id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS llm_scenario_slots (
                    slot INTEGER PRIMARY KEY, status TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS llm_scenario_decisions (
                    slot INTEGER PRIMARY KEY, snapshot TEXT, context TEXT,
                    proposal TEXT, outcome TEXT);
                CREATE TABLE IF NOT EXISTS llm_scenario_intents (
                    id TEXT PRIMARY KEY, scenario_id TEXT NOT NULL,
                    payload TEXT NOT NULL, status TEXT NOT NULL, evidence TEXT);
                CREATE TABLE IF NOT EXISTS llm_scenario_settlements (
                    scenario_id TEXT PRIMARY KEY, evidence TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS llm_scenario_notice_errors (
                    captured_at REAL NOT NULL, reason TEXT NOT NULL);
            """)
            conn.execute("INSERT OR IGNORE INTO llm_scenario_state VALUES(1, ?)",
                         (_json({"version": 0, "active": None, "breaker": {}}),))
            if self.recovery_enabled:
                recovery.ensure_schema(conn)
            conn.commit()

    def state(self):
        return json.loads(self.conn.execute(
            "SELECT body FROM llm_scenario_state WHERE id=1").fetchone()[0])

    def _save(self, state):
        self.conn.execute("UPDATE llm_scenario_state SET body=? WHERE id=1", (_json(state),))
        self.conn.commit()

    def _notice(self, identity, event):
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
                              (self.clock(),"notice_validation_or_enqueue_failed"))
            return False

    def _enabled(self):
        return (getattr(self.broker, "environment", None) == "demo" and
                getattr(self.broker, "lane", None) == "MAIN" and
                REQUIRED_CAPABILITIES <= set(getattr(self.broker, "capabilities", ())))

    def _reconcile(self, state):
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
                                  (self.clock(), "notice_validation_or_enqueue_failed"))
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
            if ident not in seen_live:
                self.conn.execute("UPDATE llm_scenario_intents SET status='PENDING' WHERE id=?", (ident,))
                state["version"] += 1
        settlement = result.get("settlement")
        active = state["active"]
        pending = self.conn.execute("SELECT count(*) FROM llm_scenario_intents WHERE status!='TERMINAL'").fetchone()[0]
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
                    recovery.settled(self.conn, state, settlement, self.clock())
                inserted = self.conn.execute("INSERT OR IGNORE INTO llm_scenario_settlements VALUES(?,?)",
                                  (active["scenario_id"], _json(settlement)))
                if inserted.rowcount == 1:
                    from live.scenario_provenance import record
                    record("settlement_recorded", {"settlement": settlement},
                           scenario_id=active["scenario_id"])
                state["active"] = None
                state["version"] += 1
        self._save(state)

    def _context(self, state):
        ctx = dict(self.broker.context())
        for flag in ("legacy_fenced", "protection_ok", "new_risk_blocked"):
            if type(ctx.get(flag)) is not bool:
                raise ValueError("unconfirmed_account_context")
        if not isinstance(ctx.get("account_version"), str) or not ctx["account_version"]:
            raise ValueError("missing_account_version")
        # Every known live remainder must reserve its exact quantity and price.
        pending = {o["id"]: o for o in ctx["pending_entries"]}
        for (body,) in self.conn.execute("SELECT evidence FROM llm_scenario_intents WHERE status='LIVE_RECONCILED'"):
            for order in json.loads(body)["open_entries"]:
                if pending.get(order["id"]) != order:
                    raise ValueError("live_order_risk_not_reserved")
        state["breaker"] = update_circuit_breaker(state["breaker"],
            **{k: ctx[k] for k in ("day", "day_start_equity", "daily_net_pnl")})
        active = state["active"]
        observe_review(active, ctx, self.clock())
        ctx.update(review_contract_version=1,
                   review_memory=json.loads(_json(active.get("review_memory", []))) if active else [],
                   review_status=active.get("review_status", "not_initialized") if active else "no_scenario")
        ctx.update(scenario_id=active["scenario_id"] if active else None,
                   revision=active["revision"] if active else 0,
                   seen_action_ids=[r[0] for r in self.conn.execute("SELECT id FROM llm_scenario_intents")])
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
        slot = int(self.clock() // 300)
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
                recovery.migrate(self.conn, state, ctx, self.clock())
            allowed = recovery.observing_allowed(state, ctx)
            no_intents = not self.conn.execute("SELECT 1 FROM llm_scenario_intents WHERE status IS NULL OR status!='TERMINAL'").fetchone()
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
                        promotion_evidence=permit["stage_evidence"])
                ctx["recovery_normal_permission"] = recovery.normal_permission(state, active, ctx)
        ctx["new_risk_blocked"] = bool(ctx["new_risk_blocked"] or (
            state["breaker"].get("blocked") and not ctx.get("recovery_observing")
            and not ctx.get("recovery_normal_permission")))
        if state["breaker"].get("blocked") and not state.get("halt_notified"):
            stamp=state.setdefault("halted_at",self.clock())
            reasons=set(state["breaker"].get("reasons",[]))
            event={"kind":"HALTED","timestamp":stamp,"reason_code":
                   "DAILY_LOSS" if "daily_loss" in reasons else "THREE_LOSSES",
                   "automatic_normalization":self.automatic_normalization_enabled and reasons=={"three_losses"}}
            state["halt_notified"]=self._notice(f"halt:{stamp}",event)
        self._save(state)
        return ctx

    def _tick(self, claimed):
        """One synchronous tick; callers may run independent protection separately."""
        slot = int(self.clock() // 300)
        stage = "initial_reconcile"
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
                if ctx["legacy_fenced"] or not ctx["protection_ok"]:
                    return {"status": "fenced"}
                if ctx["new_risk_blocked"] and state["active"] is None:
                    return {"status":"blocked","reason":"new_risk_halted",
                            "verified_flat_halt": (ctx.get("positions") == []
                                and ctx.get("pending_entries") == []
                                and ctx.get("accounting_status") == "confirmed"
                                and not self.conn.execute("SELECT 1 FROM llm_scenario_intents WHERE status IS NULL OR status!='TERMINAL'").fetchone())}
                if self.conn.execute("SELECT 1 FROM llm_scenario_intents WHERE status='PENDING'").fetchone():
                    return {"status": "intent_pending"}
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
            with mutation_lock(self.conn):
                if ctx.get("recovery_observing"):
                    current = self.state()
                    if current["version"] != version:
                        return {"status": "stale_proposal"}
                    recovery.prepare_input(self.conn, current, ctx, snap, slot, self.clock())
                    # Baseline, mandatory INPUT and the full decision input must
                    # share one commit. _save() would commit the baseline early.
                    self.conn.execute("UPDATE llm_scenario_state SET body=? WHERE id=1", (_json(current),))
                self.conn.execute("UPDATE llm_scenario_decisions SET snapshot=?, context=? WHERE slot=?",
                                  (_json(snap), _json(ctx), slot))
                self.conn.commit()
            stage = "proposal_call"
            from live.scenario_provenance import record_hashed
            record_hashed("decision_input", {"snapshot": snap, "context": ctx, "input_id": input_id},
                          scenario_id=ctx.get("scenario_id"), decision_slot=slot)
            if self.recovery_enabled:
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
            self.conn.execute("UPDATE llm_scenario_decisions SET proposal=? WHERE slot=? AND proposal IS NULL",
                              (_json(payload), slot))
            self.conn.commit()
            stage = "post_model_reconcile"
            with _post_model_mutation_lock(self.conn):
                state = self.state()
                self._reconcile(state)
                fresh = self._context(state)
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
                if ctx.get("recovery_observing"):
                    if not fresh.get("recovery_observing"):
                        return {"status": "blocked", "reason": "recovery_hard_guard"}
                    probe = recovery.assess(self.conn, state, ctx, payload, slot, self.clock())
                    self.conn.commit()
                core_payload = {key: value for key, value in payload.items() if key not in {"review", "recovery"}}
                validated = validate_execution_prices(core_payload, fresh)
                if validated["action"] == "WAIT" and not validated.get("cancel_entry_ids"):
                    apply_review(state["active"], payload.get("review"), validated["action_id"],
                                 now=self.clock(), presented=ctx["review_memory"])
                    self._save(state)
                    return {"status": "wait"}
                stage = "execution"
                ident = validated["action_id"]
                if state["active"] is None:
                    if self.conn.execute("SELECT 1 FROM llm_scenario_settlements WHERE scenario_id=?", (validated["scenario_id"],)).fetchone():
                        return {"status": "reused_scenario"}
                    if probe:
                        recovery.consume(self.conn, state, validated, fresh, slot, self.clock())
                    state["active"] = {"scenario_id": validated["scenario_id"], "initial_equity": fresh["initial_equity"],
                        "side": validated["side"], "hard_stop": validated["hard_stop"], "revision": 0,
                        "created_at": self.clock(), "expires_at": validated["expires_at"]}
                apply_review(state["active"], payload.get("review"), validated["action_id"],
                             now=self.clock(), presented=ctx["review_memory"])
                state["active"]["revision"] = validated["revision"]
                if validated["action"] in ("OPEN", "ADJUST"):
                    state["active"]["desired_hard_stop"] = validated["hard_stop"]
                    state["active"]["expires_at"] = validated["expires_at"]
                    state["active"]["current_plan"] = validated
                state["version"] += 1
                self.conn.execute("INSERT INTO llm_scenario_intents VALUES(?,?,?,'PENDING',NULL)",
                                  (ident, validated["scenario_id"], _json(validated)))
                self._save(state)  # Intent precedes the first exchange side effect.
                record_hashed("intent_committed", {"payload": validated, "risk": validated.get("risk"),
                              "initial_equity": fresh["initial_equity"],
                              "pending_entries": fresh["pending_entries"]},
                              scenario_id=validated["scenario_id"], intent_id=ident, decision_slot=slot)
                if validated["action"] in {"OPEN","ADJUST"}:
                    entries=validated.get("entries",[])
                    quantity=sum(e["quantity"] for e in entries)
                    price=sum(e["price"]*e["quantity"] for e in entries)/quantity if quantity else None
                    held_quantity=sum(p["quantity"] for p in fresh["positions"])
                    held_average=(sum(p["price"]*p["quantity"] for p in fresh["positions"])/held_quantity
                                  if held_quantity else None)
                    self._notice("plan:"+ident,dict(kind="PLAN",timestamp=self.clock(),side=validated["side"],
                        conditional_entries=[e for e in entries if e.get("trigger_price") is not None],
                        reservation_expires_at=validated["expires_at"],
                        price=price,hard_stop=validated["hard_stop"],take_profits=validated.get("take_profits",[]),
                        plan_action=validated["action"],quantity=quantity,before_quantity=held_quantity,
                        before_hard_stop=fresh.get("previous_hard_stop"),reference_entry_price=held_average,
                        scenario_initial_equity=fresh["initial_equity"],
                        scenario_budget=validated["risk"]["budget"],scenario_risk=validated["risk"]["total_risk"]))
                    self.conn.commit()
                try:
                    self.broker.execute(validated, ident)
                except Exception:
                    self._notice("unknown:"+ident,dict(kind="PENDING",timestamp=self.clock()))
                    self.conn.commit()
                    return {"status": "intent_pending", "reason": "submission_unknown"}
                return {"status": "intent_pending", "reason": "awaiting_exact_evidence"}
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
            return {"status":"lock_busy","reason":"another_protection_or_execution_tick"}
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
            from core.llm_scenario import ScenarioValidationError
            if stage == "validation" and isinstance(exc, ScenarioValidationError):
                fixed = {"risk budget exceeded": "scenario_risk_budget_exceeded",
                         "stop widening forbidden": "scenario_stop_widening_forbidden",
                         "new risk blocked": "scenario_new_risk_blocked",
                         "stop crossed market; use EXIT": "scenario_stop_crossed_market",
                         "stale input or invalid expiry": "scenario_stale_input_or_expiry",
                         "invalid or opposite side": "scenario_opposite_side",
                         "conditional entry expiry exceeds next decision": "scenario_entry_expiry_invalid"}
                outcome["failure_code"] = fixed.get(str(exc), "scenario_contract_invalid")
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
