"""Schema-2 fixed-slot strategy accounting; never submits orders.

Allocation / execution price gives normalized units, NOT actual shares.
Fees and slippage are dimensionless rates. Gains never replenish allocation
or finance larger campaigns. Re-entry requires a new campaign after closure.
"""

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path


class LedgerError(ValueError):
    """Invalid input, conflicting event, or policy/accounting violation."""


def _number(value, *, positive=False):
    try:
        if isinstance(value, bool):
            raise TypeError
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise LedgerError("invalid decimal") from exc
    if not result.is_finite() or result < 0 or (positive and result == 0):
        raise LedgerError(
            "decimal must be finite and nonnegative (positive when required)"
        )
    return result


def _rate(value):
    result = _number(value)
    if result >= 1:
        raise LedgerError("cost rate must be less than 1")
    return result


def _text(value):
    if not isinstance(value, str) or not value.strip():
        raise LedgerError("nonempty string required")
    return value


def _time(value):
    try:
        parsed = datetime.fromisoformat(_text(value).replace("Z", "+00:00"))
        if parsed.utcoffset() is None:
            raise ValueError
        return parsed.astimezone(timezone.utc).isoformat()
    except ValueError as exc:
        raise LedgerError("timezone-aware ISO timestamp required") from exc


def _dump(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class StrategyLedger:
    """Explicitly provisioned, separate SQLite book. Each write is atomic."""

    def __init__(self, path):
        self.path = str(Path(path).expanduser().resolve())
        if Path(self.path).name in {"stock_tracking_db.sqlite", "stock_tracking.db"}:
            raise LedgerError("use a dedicated strategy ledger database")
        with self._transaction() as db:
            names = {
                r[0]
                for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            expected = {"strategy_ledger_metadata", "books", "campaigns", "events", "legs", "executions"}
            if names and names != expected:
                raise LedgerError("refusing to migrate an unknown database")
            db.execute(
                "CREATE TABLE IF NOT EXISTS strategy_ledger_metadata (version INTEGER NOT NULL)"
            )
            version = db.execute(
                "SELECT version FROM strategy_ledger_metadata"
            ).fetchall()
            if (names or version) and version != [(2,)]:
                raise LedgerError("unsupported ledger schema")
            if not version:
                db.execute("INSERT INTO strategy_ledger_metadata VALUES (2)")
            for table in ("books", "campaigns"):
                db.execute(
                    f"CREATE TABLE IF NOT EXISTS {table} (id TEXT PRIMARY KEY, data TEXT NOT NULL)"
                )
            db.execute(
                "CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, digest TEXT NOT NULL, payload TEXT NOT NULL)"
            )
            for table in ("legs", "executions"):
                db.execute(
                    f"CREATE TABLE IF NOT EXISTS {table} (id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL, data TEXT NOT NULL)"
                )
            for table in ("events", "legs", "executions"):
                for operation in ("UPDATE", "DELETE"):
                    db.execute(
                        f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{operation} BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT, 'immutable ledger history'); END"
                    )

    @contextmanager
    def _transaction(self):
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        try:
            db.execute("BEGIN IMMEDIATE")
            with localcontext() as context:
                context.prec = 40
                yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _get(db, table, identifier):
        row = db.execute(
            f"SELECT data FROM {table} WHERE id=?", (identifier,)
        ).fetchone()
        if row is None:
            raise LedgerError(f"unknown {table} identifier")
        return json.loads(row[0])

    @staticmethod
    def _save(db, table, identifier, value):
        db.execute(
            f"INSERT INTO {table} VALUES (?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
            (identifier, _dump(value)),
        )

    @staticmethod
    def _event(db, event_id, payload):
        _text(event_id)
        encoded = _dump(payload)
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        previous = db.execute(
            "SELECT digest FROM events WHERE id=?", (event_id,)
        ).fetchone()
        if previous:
            if previous[0] != digest:
                raise LedgerError("event ID payload conflict")
            return False
        db.execute("INSERT INTO events VALUES (?,?,?)", (event_id, digest, encoded))
        return True

    @staticmethod
    def _chronology(campaign, occurred_at):
        if occurred_at < campaign["last_event_at"]:
            raise LedgerError("out-of-order campaign event")

    def _book_chronology(self, db, book, occurred_at):
        if book.get("last_accounting_at") and occurred_at < book["last_accounting_at"]:
            raise LedgerError("out-of-order book accounting event")
        book["last_accounting_at"] = occurred_at
        self._save(db, "books", book["book_id"], book)

    def create_book(
        self, book_id, market, max_slots=10, *, cohort="baseline-v1", mode="VALIDATION"
    ):
        if market not in {"KR", "US"}:
            raise LedgerError("unsupported market")
        if type(max_slots) is not int or not 1 <= max_slots <= 10:
            raise LedgerError("max_slots must be an integer from 1 to 10")
        if mode not in {"VALIDATION", "SHADOW", "VALIDATION_ONLY"}:
            raise LedgerError("unsupported strategy mode")
        if cohort is None and mode != "VALIDATION_ONLY":
            raise LedgerError("explicit strategy cohort required")
        identity = [market, None if cohort is None else _text(cohort), mode]
        key = None if cohort is None else hashlib.sha256(_dump(identity).encode()).hexdigest()
        config = {
            "book_id": _text(book_id), "market": market, "max_slots": max_slots,
            "cohort": cohort, "mode": mode, "strategy_book_key": key,
            "validation_only": mode == "VALIDATION_ONLY",
        }
        with self._transaction() as db:
            for row in db.execute("SELECT data FROM books"):
                existing = json.loads(row[0])
                if key is not None and existing["strategy_book_key"] == key and existing["book_id"] != book_id:
                    raise LedgerError("strategy identity already provisioned under another book ID")
            row = db.execute("SELECT data FROM books WHERE id=?", (book_id,)).fetchone()
            if row:
                existing = json.loads(row[0])
                if any(existing[k] != v for k, v in config.items()):
                    raise LedgerError("book configuration conflict")
            else:
                self._save(db, "books", book_id, {**config, "realized_contribution": "0"})
            return self._snapshot(db, book_id)

    def apply_target(
        self,
        event_id,
        book_id,
        campaign_id,
        symbol,
        target_pct,
        price,
        occurred_at,
        policy_version="split-ledger-v1",
        reason="target entry",
        regime="moderate_bull",
        source_hash=None,
        fee_rate=0,
        slippage_rate=0,
    ):
        target, price = (
            _number(target_pct, positive=True),
            _number(price, positive=True),
        )
        timestamp = _time(occurred_at)
        fee_rate, slippage_rate = _rate(fee_rate), _rate(slippage_rate)
        payload = {
            "kind": "target",
            "book_id": _text(book_id),
            "campaign_id": _text(campaign_id),
            "symbol": _text(symbol),
            "target_pct": str(target),
            "price": str(price),
            "occurred_at": timestamp,
            "policy_version": _text(policy_version),
            "reason": _text(reason),
            "regime": _text(regime),
            "source_hash": source_hash,
            "fee_rate": str(fee_rate),
            "slippage_rate": str(slippage_rate),
        }
        with self._transaction() as db:
            book = self._get(db, "books", book_id)
            if not self._event(db, event_id, payload):
                return {**self._snapshot(db, book_id), "event_applied": False}
            self._book_chronology(db, book, timestamp)
            if target > 100:
                raise LedgerError("target exceeds 100 percent cap")
            row = db.execute(
                "SELECT data FROM campaigns WHERE id=?", (campaign_id,)
            ).fetchone()
            campaign = (
                json.loads(row[0])
                if row
                else {
                    "campaign_id": campaign_id,
                    "book_id": book_id,
                    "symbol": symbol,
                    "target_pct": "0",
                    "normalized_units": "0",
                    "remaining_allocation": "0",
                    "cumulative_deployed_allocation": "0",
                    "realized_contribution": "0",
                    "remaining_entry_cost": "0",
                    "add_permission": "AVAILABLE",
                    "last_event_at": timestamp,
                    "mark_price": str(price),
                    "mark_at": timestamp,
                    "mark_basis": "last_trade",
                }
            )
            if campaign["book_id"] != book_id or campaign["symbol"] != symbol:
                raise LedgerError("campaign identity conflict")
            self._chronology(campaign, timestamp)
            old_target = Decimal(campaign["target_pct"])
            quantity, cost = (
                Decimal(campaign["normalized_units"]),
                Decimal(campaign["remaining_allocation"]),
            )
            if row and quantity == 0:
                raise LedgerError("closed campaign cannot reopen")
            if target < old_target:
                raise LedgerError("cumulative target cannot decrease; use sell")
            if target == old_target:
                if fee_rate or slippage_rate:
                    raise LedgerError("unchanged target cannot incur costs")
                campaign["last_event_at"] = timestamp
                self._save(db, "campaigns", campaign_id, campaign)
                return {**self._snapshot(db, book_id), "event_applied": True}
            if campaign["add_permission"] != "AVAILABLE":
                raise LedgerError("strategy reduction cancels further adds")
            if quantity and price < cost / quantity:
                raise LedgerError(
                    "policy guard: adding below average cost is forbidden"
                )
            active = [
                json.loads(r[0]) for r in db.execute("SELECT data FROM campaigns")
            ]
            active = [
                c
                for c in active
                if c["book_id"] == book_id
                and Decimal(c["normalized_units"]) > 0
                and c["campaign_id"] != campaign_id
            ]
            if any(c["symbol"] == symbol for c in active):
                raise LedgerError("symbol already has an active campaign")
            if len(active) + 1 > book["max_slots"]:
                raise LedgerError("name slot limit exceeded")
            allocation = (target - old_target) / 100
            execution_price = price * (1 + slippage_rate)
            bought = allocation / execution_price
            entry_cost = allocation * fee_rate
            leg = {
                "event_id": event_id,
                "campaign_id": campaign_id,
                "side": "BUY",
                "normalized_units": str(bought),
                "price": str(price),
                "allocation": str(allocation),
                "execution_price": str(execution_price),
                "fee_rate": str(fee_rate),
                "slippage_rate": str(slippage_rate),
                "cost_contribution": str(entry_cost),
                "occurred_at": timestamp,
                "target_pct": str(target),
                "policy_version": policy_version,
                "reason": reason,
            }
            db.execute(
                "INSERT INTO legs VALUES (?,?,?)", (event_id, campaign_id, _dump(leg))
            )
            campaign.update(
                target_pct=str(target),
                normalized_units=str(quantity + bought),
                remaining_allocation=str(cost + allocation),
                remaining_entry_cost=str(Decimal(campaign["remaining_entry_cost"]) + entry_cost),
                cumulative_deployed_allocation=str(Decimal(campaign["cumulative_deployed_allocation"]) + allocation),
                last_event_at=timestamp,
                mark_price=str(price),
                mark_at=timestamp,
                mark_basis="last_trade",
            )
            self._save(db, "campaigns", campaign_id, campaign)
            self._save(db, "books", book_id, book)
            return {**self._snapshot(db, book_id), "event_applied": True}

    def sell(
        self,
        event_id,
        campaign_id,
        price,
        occurred_at,
        quantity=None,
        fee_rate=0,
        slippage_rate=0,
        source_hash=None,
    ):
        """Reduce normalized units (quantity is NOT a broker share quantity)."""
        price = _number(price, positive=True)
        fee_rate, slippage_rate = _rate(fee_rate), _rate(slippage_rate)
        timestamp = _time(occurred_at)
        requested = None if quantity is None else str(_number(quantity, positive=True))
        payload = {
            "kind": "sell",
            "campaign_id": _text(campaign_id),
            "price": str(price),
            "occurred_at": timestamp,
            "normalized_units": requested,
            "fee_rate": str(fee_rate),
            "slippage_rate": str(slippage_rate),
            "source_hash": source_hash,
        }
        with self._transaction() as db:
            campaign = self._get(db, "campaigns", campaign_id)
            book_id = campaign["book_id"]
            if not self._event(db, event_id, payload):
                return {**self._snapshot(db, book_id), "event_applied": False}
            book = self._get(db, "books", book_id)
            self._book_chronology(db, book, timestamp)
            self._chronology(campaign, timestamp)
            held, cost = Decimal(campaign["normalized_units"]), Decimal(campaign["remaining_allocation"])
            sold = held if requested is None else Decimal(requested)
            if sold <= 0 or sold > held:
                raise LedgerError("sell quantity exceeds holding or is zero")
            allocated = cost if sold == held else cost * sold / held
            entry_cost = Decimal(campaign["remaining_entry_cost"])
            allocated_entry_cost = entry_cost if sold == held else entry_cost * sold / held
            execution_price = price * (1 - slippage_rate)
            gross_proceeds = sold * execution_price
            exit_cost = gross_proceeds * fee_rate
            pnl = gross_proceeds - exit_cost - allocated - allocated_entry_cost
            campaign.update(
                normalized_units=str(held - sold),
                remaining_allocation=str(cost - allocated),
                remaining_entry_cost=str(Decimal(campaign["remaining_entry_cost"]) - allocated_entry_cost),
                add_permission="CANCELLED_BY_REDUCTION",
                realized_contribution=str(Decimal(campaign["realized_contribution"]) + pnl),
                last_event_at=timestamp,
                mark_price=str(price),
                mark_at=timestamp,
                mark_basis="last_trade",
            )
            book.update(
                realized_contribution=str(Decimal(book["realized_contribution"]) + pnl),
            )
            leg = {
                "event_id": event_id,
                "campaign_id": campaign_id,
                "side": "SELL",
                "normalized_units": str(sold),
                "price": str(price),
                "released_allocation": str(allocated),
                "execution_price": str(execution_price),
                "fee_rate": str(fee_rate),
                "slippage_rate": str(slippage_rate),
                "cost_contribution": str(exit_cost + allocated_entry_cost),
                "occurred_at": timestamp,
                "realized_contribution": str(pnl),
            }
            db.execute(
                "INSERT INTO legs VALUES (?,?,?)", (event_id, campaign_id, _dump(leg))
            )
            self._save(db, "campaigns", campaign_id, campaign)
            self._save(db, "books", book_id, book)
            return {**self._snapshot(db, book_id), "event_applied": True}

    def mark(self, event_id, campaign_id, price, occurred_at, source_hash=None):
        price, timestamp = _number(price, positive=True), _time(occurred_at)
        payload = {
            "kind": "mark",
            "campaign_id": _text(campaign_id),
            "price": str(price),
            "occurred_at": timestamp,
            "source_hash": source_hash,
        }
        with self._transaction() as db:
            campaign = self._get(db, "campaigns", campaign_id)
            applied = self._event(db, event_id, payload)
            if applied:
                book = self._get(db, "books", campaign["book_id"])
                self._book_chronology(db, book, timestamp)
                self._chronology(campaign, timestamp)
                campaign.update(
                    mark_price=str(price),
                    mark_at=timestamp,
                    mark_basis="explicit_mark",
                    last_event_at=timestamp,
                )
                self._save(db, "campaigns", campaign_id, campaign)
            return {**self._snapshot(db, campaign["book_id"]), "event_applied": applied}

    def observe_execution(
        self,
        event_id,
        campaign_id,
        execution_profile_ref,
        status,
        observed_at,
        intent_ref=None,
        confirmed_quantity=None,
        confirmed_price=None,
        evidence_source=None,
        source_hash=None,
    ):
        status = _text(status).upper()
        if status not in {
            "UNKNOWN",
            "SUBMITTED",
            "ACCEPTED",
            "REJECTED",
            "PARTIAL",
            "FILLED",
            "CANCELLED",
        }:
            raise LedgerError("unsupported execution status")
        quantity = (
            None
            if confirmed_quantity is None
            else str(_number(confirmed_quantity, positive=True))
        )
        price = (
            None
            if confirmed_price is None
            else str(_number(confirmed_price, positive=True))
        )
        if (status in {"PARTIAL", "FILLED"} or quantity is not None or price is not None) and (
            status not in {"PARTIAL", "FILLED"}
            or quantity is None
            or price is None
            or not evidence_source
        ):
            raise LedgerError(
                "confirmed fill requires quantity, price, evidence and fill status"
            )
        payload = {
            "kind": "execution",
            "campaign_id": _text(campaign_id),
            "execution_profile_ref": _text(execution_profile_ref),
            "status": status,
            "observed_at": _time(observed_at),
            "intent_ref": None if intent_ref is None else _text(intent_ref),
            "confirmed_quantity": quantity,
            "confirmed_price": price,
            "evidence_source": None
            if evidence_source is None
            else _text(evidence_source),
            "source_hash": source_hash,
        }
        with self._transaction() as db:
            campaign = self._get(db, "campaigns", campaign_id)
            applied = self._event(db, event_id, payload)
            if applied:
                db.execute(
                    "INSERT INTO executions VALUES (?,?,?)",
                    (event_id, campaign_id, _dump({"event_id": event_id, **payload})),
                )
            return {**self._snapshot(db, campaign["book_id"]), "event_applied": applied}

    def snapshot(self, book_id):
        with self._transaction() as db:
            return self._snapshot(db, book_id)

    def list_book_ids(self):
        """Return explicitly provisioned books, without inventing defaults."""
        with self._transaction() as db:
            return [row[0] for row in db.execute("SELECT id FROM books ORDER BY id")]

    def _snapshot(self, db, book_id):
        book = self._get(db, "books", book_id)
        campaigns, executions = [], []
        marked_exposure, unrealized, remaining = Decimal(0), Decimal(0), Decimal(0)
        for row in db.execute("SELECT data FROM campaigns ORDER BY id"):
            campaign = json.loads(row[0])
            if campaign["book_id"] != book_id:
                continue
            units = Decimal(campaign["normalized_units"])
            allocation = Decimal(campaign["remaining_allocation"])
            value = units * Decimal(campaign["mark_price"])
            pnl = value - allocation - Decimal(campaign["remaining_entry_cost"])
            average = allocation / units if units else None
            campaign.update(
                average_cost=str(average) if average is not None else None,
                invested_price_return_pct=str((Decimal(campaign["mark_price"]) / average - 1) * 100) if average else None,
                marked_exposure=str(value),
                unrealized_contribution=str(pnl),
                one_slot_contribution=str(Decimal(campaign["realized_contribution"]) + pnl),
                conditional_remaining_allocation=str(1 - Decimal(campaign["cumulative_deployed_allocation"]))
                    if units and campaign["add_permission"] == "AVAILABLE" else "0",
                mark_freshness="historical_observation_not_live",
                status="OPEN" if units else "CLOSED",
            )
            leg_rows = db.execute(
                "SELECT data FROM legs WHERE campaign_id=? ORDER BY rowid",
                (campaign["campaign_id"],),
            ).fetchall()
            campaign["legs"] = [json.loads(row[0]) for row in leg_rows]
            executions.extend(
                json.loads(r[0]) for r in db.execute(
                    "SELECT data FROM executions WHERE campaign_id=? ORDER BY rowid",
                    (campaign["campaign_id"],),
                )
            )
            campaigns.append(campaign)
            marked_exposure += value
            remaining += allocation
            unrealized += pnl
        contribution = Decimal(book["realized_contribution"]) + unrealized
        isolated = book["validation_only"]
        return {
            **book, "schema_version": 2,
            "occupied_slots": sum(c["status"] == "OPEN" for c in campaigns),
            "remaining_allocation": str(remaining),
            "marked_exposure": str(marked_exposure),
            "unrealized_contribution": str(unrealized),
            "total_slot_contribution": str(contribution),
            "capacity_normalized_contribution": None if isolated else str(contribution / book["max_slots"]),
            "contribution_denominator_slots": None if isolated else book["max_slots"],
            "campaigns": campaigns, "executions": executions,
            "accounting_basis": "normalized_units_not_shares; fixed_slot_principal; dimensionless_cost_rates; no_gain_reinvestment",
            "portfolio_aggregation": "FORBIDDEN_ISOLATED_VALIDATION" if isolated else "EXPLICIT_FIXED_CAPACITY_ONLY",
        }
