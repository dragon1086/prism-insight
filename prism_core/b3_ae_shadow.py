"""B3 for every actual entry (``oneil-adaptive-v3-ae``): SHADOW virtual ledger, KR/US.

The initial allocation is filled at the actual entry price and time; adds come
only from ``evaluate_target`` (the v2 B3 ladder); the campaign closes with the
original strategy exit. No orders, holdings writes or messages. One SQLite file.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from decimal import Decimal

from prism_core.oneil_adaptive_policy import (
    MARKETS,
    V3_AE_VERSION,
    _hash,
    _num,
    _time,
    _validate,
    create_plan,
    evaluate_target,
)

CONTRACT = "b3-ae-shadow-v1"
ADD_COST_RATE = Decimal("0.0025")  # Supplementary report only (preregistered).

_SCHEMA = """
CREATE TABLE IF NOT EXISTS b3_campaigns (
    campaign_id TEXT PRIMARY KEY,
    market TEXT NOT NULL,
    account_key TEXT NOT NULL,
    position_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    status TEXT NOT NULL,
    data TEXT NOT NULL,
    UNIQUE (market, account_key, position_id)
);
CREATE TABLE IF NOT EXISTS b3_events (
    event_id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL,
    at TEXT NOT NULL,
    kind TEXT NOT NULL,
    data TEXT NOT NULL
);
"""


def plan_for_entry(*, market, symbol, entry_price, initial_stop, decision_ref, entered_at,
                   atr14, atr14_source_ref, atr14_as_of, atr14_last_trade_date, price_basis_ref):
    """Freeze the v3-ae plan for an actual entry (pivot = entry price)."""
    setup = {"basis": "ACTUAL_ENTRY", "pivot": str(_num(entry_price, True)), "as_of": entered_at,
             "source_ref": _hash([market, symbol, decision_ref, str(entry_price), entered_at]),
             "price_basis_ref": price_basis_ref, "atr14": str(_num(atr14, True)),
             "atr14_source_ref": atr14_source_ref, "atr14_as_of": atr14_as_of,
             "atr14_last_trade_date": atr14_last_trade_date}
    return create_plan(symbol=symbol, entry_reference=entry_price, initial_stop=initial_stop,
                       source_decision_ref=decision_ref, created_at=entered_at, setup=setup,
                       entry_eligible=True, policy_version=V3_AE_VERSION, market=market)


def _deployed(state):
    return sum((_num(leg["allocation"]) for leg in state["legs"]), Decimal(0))


def ledger_inputs(state):
    """Budget-normalized ledger in the units ``evaluate_target`` expects."""
    fee = _num(state["plan"]["fee_rate"])
    deployed = _deployed(state)
    units = sum((_num(leg["allocation"]) / _num(leg["price"], True) for leg in state["legs"]), Decimal(0))
    return dict(cumulative_allocation=deployed, remaining_allocation=deployed,
                normalized_units=units, remaining_entry_cost=deployed * fee)


def campaign_returns(state, exit_price):
    """Slot returns of B3 versus the 100% baseline on the same entry and exit."""
    exit_price = _num(exit_price, True)
    entry = _num(state["plan"]["entry_reference"], True)
    b3 = sum((_num(leg["allocation"]) * (exit_price / _num(leg["price"], True) - 1)
              for leg in state["legs"]), Decimal(0))
    adds = sum((_num(leg["allocation"]) for leg in state["legs"] if leg["kind"] == "ADD"), Decimal(0))
    return {"baseline": str(exit_price / entry - 1), "b3": str(b3),
            "b3_after_add_cost": str(b3 - adds * ADD_COST_RATE),
            "final_allocation": str(_deployed(state))}


class B3AeShadowStore:
    def __init__(self, path):
        self.path = str(path)
        db = sqlite3.connect(self.path, timeout=30)
        try:
            db.executescript(_SCHEMA)  # executescript manages its own transaction
        finally:
            db.close()

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    @staticmethod
    def _event(db, campaign_id, at, kind, data):
        event_id = _hash([campaign_id, at, kind, data])
        db.execute("INSERT OR IGNORE INTO b3_events VALUES (?,?,?,?,?)",
                   (event_id, campaign_id, at, kind, json.dumps(data, sort_keys=True)))

    def open_campaign(self, *, account_key, position_id, plan, entered_at, mode="SHADOW"):
        """Idempotent per (market, account, position); initial fill at the actual entry."""
        _validate(plan)
        if plan["policy_version"] != V3_AE_VERSION or plan["market"] not in MARKETS:
            raise ValueError("v3-ae plan required")
        if _time(entered_at) != _time(plan["created_at"]):
            raise ValueError("initial fill must be the actual entry time")
        cid = _hash([CONTRACT, plan["market"], account_key, position_id, plan["plan_hash"]])
        with self._db() as db:
            row = db.execute("SELECT data FROM b3_campaigns WHERE market=? AND account_key=? AND position_id=?",
                             (plan["market"], account_key, position_id)).fetchone()
            if row:
                return json.loads(row[0])
            if mode not in ("SHADOW", "LIVE"):
                raise ValueError("unknown campaign mode")
            state = dict(contract=CONTRACT, campaign_id=cid, mode=mode, market=plan["market"], account_key=account_key,
                         position_id=position_id, symbol=plan["symbol"], plan=plan, status="ACTIVE",
                         legs=[dict(kind="INITIAL", allocation=plan["initial_nominal"],
                                    price=plan["entry_reference"], at=plan["created_at"])],
                         last_add_bar_end=None, last_reason=None, exit=None)
            db.execute("INSERT INTO b3_campaigns VALUES (?,?,?,?,?,?,?)",
                       (cid, plan["market"], account_key, position_id, plan["symbol"], "ACTIVE",
                        json.dumps(state, sort_keys=True)))
            self._event(db, cid, plan["created_at"], "OPEN",
                        dict(initial=plan["initial_nominal"], price=plan["entry_reference"]))
            return state

    def snapshot(self, campaign_id):
        with self._db() as db:
            row = db.execute("SELECT data FROM b3_campaigns WHERE campaign_id=?", (campaign_id,)).fetchone()
        if row is None:
            raise ValueError("unknown campaign")
        return json.loads(row[0])

    def active(self, market):
        with self._db() as db:
            rows = db.execute("SELECT data FROM b3_campaigns WHERE market=? AND status='ACTIVE'", (market,))
            return [json.loads(row[0]) for row in rows]

    def by_position(self, market, account_key, position_id):
        with self._db() as db:
            row = db.execute("SELECT data FROM b3_campaigns WHERE market=? AND account_key=? AND position_id=?",
                             (market, account_key, position_id)).fetchone()
        return json.loads(row[0]) if row else None

    def evaluate(self, campaign_id, evidence, *, now, current_stop):
        """Run the B3 add decision once and book a virtual add at the quote."""
        with self._db() as db:
            row = db.execute("SELECT data FROM b3_campaigns WHERE campaign_id=?", (campaign_id,)).fetchone()
            if row is None:
                raise ValueError("unknown campaign")
            state = json.loads(row[0])
            if state["status"] != "ACTIVE":
                return dict(action="WAIT", reason="CLOSED")
            stop = max(_num(state["plan"]["initial_stop"]), _num(current_stop, True))
            decision = evaluate_target(state["plan"], evidence, now=now, current_stop=stop,
                                       last_add_bar_end=state["last_add_bar_end"], **ledger_inputs(state))
            state["last_reason"] = decision["reason"]
            if decision["action"] == "ADD":
                delta = _num(decision["target_allocation"]) - _deployed(state)
                state["legs"].append(dict(kind="ADD", allocation=str(delta), price=decision["price"], at=now,
                                          bar_end=decision["bar_end"], evidence_hash=decision["evidence_hash"]))
                state["last_add_bar_end"] = decision["bar_end"]
            db.execute("UPDATE b3_campaigns SET data=? WHERE campaign_id=?",
                       (json.dumps(state, sort_keys=True), campaign_id))
            self._event(db, campaign_id, now, "DECISION",
                        {k: decision[k] for k in ("action", "reason", "target_allocation", "price",
                                                   "bar_end", "evidence_hash", "evidence_status")})
            return decision

    def close(self, *, market, account_key, position_id, exit_price, exit_at, reason):
        """Close with the original strategy exit (same price/time); idempotent."""
        with self._db() as db:
            row = db.execute("SELECT data FROM b3_campaigns WHERE market=? AND account_key=? AND position_id=?",
                             (market, account_key, position_id)).fetchone()
            if row is None:
                return None
            state = json.loads(row[0])
            if state["status"] == "CLOSED":
                return state
            state.update(status="CLOSED", exit=dict(price=str(_num(exit_price, True)), at=exit_at,
                                                    reason=str(reason)[:200]))
            state["returns"] = campaign_returns(state, exit_price)
            db.execute("UPDATE b3_campaigns SET status='CLOSED', data=? WHERE campaign_id=?",
                       (json.dumps(state, sort_keys=True), state["campaign_id"]))
            self._event(db, state["campaign_id"], exit_at, "CLOSE", dict(state["exit"], **state["returns"]))
            return state
