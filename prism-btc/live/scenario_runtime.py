"""Durable orchestration boundary, deliberately without a production broker adapter.

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
import math
import time
import uuid

from core.llm_scenario import validate_scenario, update_circuit_breaker
from live.shared_entry_coordinator import database_path, mutation_lock

REQUIRED_CAPABILITIES = frozenset({"exact_fills", "atomic_protection",
    "exact_settlement", "idempotent_intents", "fresh_risk_context"})


def _json(value):
    return json.dumps(value, allow_nan=False, sort_keys=True)


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


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
    def __init__(self, conn, broker, propose, snapshot, *, clock=time.time):
        database_path(conn)  # In-memory state cannot provide restart safety.
        self.conn, self.broker = conn, broker
        self.propose, self.snapshot, self.clock = propose, snapshot, clock
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
            conn.commit()

    def state(self):
        return json.loads(self.conn.execute(
            "SELECT body FROM llm_scenario_state WHERE id=1").fetchone()[0])

    def _save(self, state):
        self.conn.execute("UPDATE llm_scenario_state SET body=? WHERE id=1", (_json(state),))
        self.conn.commit()

    def _enabled(self):
        return (getattr(self.broker, "environment", None) == "demo" and
                getattr(self.broker, "lane", None) == "MAIN" and
                REQUIRED_CAPABILITIES <= set(getattr(self.broker, "capabilities", ())))

    def _reconcile(self, state):
        result = self.broker.reconcile() or {}
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
                        len(set(ids)) != len(ids) or not isinstance(orders, list) or not orders or
                        any(not isinstance(o, dict) or set(o) != {"id", "price", "quantity"} or
                            not isinstance(o["id"], str) or not o["id"] or
                            not _finite(o["price"]) or o["price"] <= 0 or
                            not _finite(o["quantity"]) or o["quantity"] <= 0 for o in orders)):
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
                     and settlement["fees"] >= 0
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
                    state["breaker"] = update_circuit_breaker(state["breaker"],
                        **{k: context[k] for k in ("day", "day_start_equity", "daily_net_pnl")},
                        completed_scenario_id=active["scenario_id"], completed_net_pnl=settlement["net_pnl"])
                self.conn.execute("INSERT OR IGNORE INTO llm_scenario_settlements VALUES(?,?)",
                                  (active["scenario_id"], _json(settlement)))
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
        ctx.update(scenario_id=active["scenario_id"] if active else None,
                   revision=active["revision"] if active else 0,
                   seen_action_ids=[r[0] for r in self.conn.execute("SELECT id FROM llm_scenario_intents")])
        if active:
            ctx.update(initial_equity=active["initial_equity"], side=active["side"],
                       previous_hard_stop=active["hard_stop"])
        ctx["new_risk_blocked"] = bool(ctx["new_risk_blocked"] or state["breaker"].get("blocked"))
        self._save(state)
        return ctx

    def _tick(self, claimed):
        """One synchronous tick; callers may run independent protection separately."""
        slot = int(self.clock() // 300)
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
                if self.conn.execute("SELECT 1 FROM llm_scenario_intents WHERE status='PENDING'").fetchone():
                    return {"status": "intent_pending"}
                if self.conn.execute("SELECT 1 FROM llm_scenario_slots WHERE slot=?", (slot,)).fetchone():
                    return {"status": "duplicate_slot"}
                self.conn.execute("INSERT INTO llm_scenario_slots VALUES(?, 'CLAIMED')", (slot,))
                self.conn.execute("INSERT INTO llm_scenario_decisions(slot) VALUES(?)", (slot,))
                self.conn.commit()  # Crash during LLM deliberately burns this slot.
                claimed.append(slot)
                version = state["version"]
            snap = self.snapshot()
            if snap.get("valid") is not True:
                raise ValueError("invalid_market_snapshot")
            captured = snap.get("as_of_ms", 0) / 1000
            input_id = str(uuid.uuid4())
            ctx.update(now=self.clock(), input_id=input_id, input_captured_at=captured,
                       max_input_age_seconds=120)
            with mutation_lock(self.conn):
                self.conn.execute("UPDATE llm_scenario_decisions SET snapshot=?, context=? WHERE slot=?",
                                  (_json(snap), _json(ctx), slot))
                self.conn.commit()
            payload = self.propose(snap, ctx)  # No broker mutation lock held.
            with mutation_lock(self.conn):
                self.conn.execute("UPDATE llm_scenario_decisions SET proposal=? WHERE slot=?", (_json(payload), slot))
                self.conn.commit()
                state = self.state()
                self._reconcile(state)
                fresh = self._context(state)
                if (state["version"] != version or fresh["account_version"] != ctx["account_version"]
                        or fresh["legacy_fenced"] or not fresh["protection_ok"]):
                    return {"status": "stale_proposal"}
                fresh.update(now=self.clock(), input_id=input_id, input_captured_at=captured,
                             max_input_age_seconds=120)
                validated = validate_scenario(payload, fresh)
                if validated["action"] == "WAIT" and not validated.get("cancel_entry_ids"):
                    return {"status": "wait"}
                ident = validated["action_id"]
                if state["active"] is None:
                    if self.conn.execute("SELECT 1 FROM llm_scenario_settlements WHERE scenario_id=?", (validated["scenario_id"],)).fetchone():
                        return {"status": "reused_scenario"}
                    state["active"] = {"scenario_id": validated["scenario_id"], "initial_equity": fresh["initial_equity"],
                        "side": validated["side"], "hard_stop": validated["hard_stop"], "revision": 0}
                state["active"]["revision"] = validated["revision"]
                if validated["action"] in ("OPEN", "ADJUST"):
                    state["active"]["hard_stop"] = validated["hard_stop"]
                state["version"] += 1
                self.conn.execute("INSERT INTO llm_scenario_intents VALUES(?,?,?,'PENDING',NULL)",
                                  (ident, validated["scenario_id"], _json(validated)))
                self._save(state)  # Intent precedes the first exchange side effect.
                try:
                    self.broker.execute(validated, ident)
                except Exception:
                    return {"status": "intent_pending", "reason": "submission_unknown"}
                return {"status": "intent_pending", "reason": "awaiting_exact_evidence"}
        except Exception:
            # No exception text/account data enters public notifications. Previously
            # committed intents and slot claims survive all failures.
            self.conn.rollback()
            return {"status": "blocked", "reason": "reconciliation_or_proposal_failed"}

    def tick(self):
        claimed = []
        outcome = self._tick(claimed)
        # Do not overwrite the original audit with a duplicate caller's status.
        if claimed:
            with mutation_lock(self.conn):
                self.conn.execute("UPDATE llm_scenario_decisions SET outcome=? WHERE slot=? AND outcome IS NULL",
                                  (_json(outcome), claimed[0]))
                self.conn.commit()
        return outcome
