"""Independent, fractional-quantity strategy accounting (never submits orders).

Targets are cumulative purchases as a percentage of a fixed book unit budget,
not mark-to-market weights. Explicit buy/sell fees default to zero. Target
budgets exclude fees; cash and cost basis include them.
Marks are historical observations, not a promise of current market prices.
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
            if version and version != [(1,)]:
                raise LedgerError("unsupported ledger schema")
            if not version:
                db.execute("INSERT INTO strategy_ledger_metadata VALUES (1)")
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
        self, book_id, market, currency, initial_capital, unit_budget, max_slots=10
    ):
        if {"KR": "KRW", "US": "USD"}.get(market) != currency:
            raise LedgerError("market/currency mismatch; cross-currency accounting is unsupported")
        capital, unit = (
            _number(initial_capital, positive=True),
            _number(unit_budget, positive=True),
        )
        if type(max_slots) is not int or not 1 <= max_slots <= 10:
            raise LedgerError("max_slots must be an integer from 1 to 10")
        config = {
            "book_id": _text(book_id),
            "market": _text(market),
            "currency": _text(currency),
            "initial_capital": str(capital),
            "unit_budget": str(unit),
            "max_slots": max_slots,
        }
        with self._transaction() as db:
            row = db.execute("SELECT data FROM books WHERE id=?", (book_id,)).fetchone()
            if row:
                existing = json.loads(row[0])
                if any(existing[k] != v for k, v in config.items()):
                    raise LedgerError("book configuration conflict")
            else:
                self._save(
                    db,
                    "books",
                    book_id,
                    {**config, "free_cash": str(capital), "realized_pnl": "0"},
                )
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
        fee=0,
    ):
        target, price = (
            _number(target_pct, positive=True),
            _number(price, positive=True),
        )
        timestamp = _time(occurred_at)
        fee = _number(fee)
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
            "fee": str(fee),
        }
        with self._transaction() as db:
            book = self._get(db, "books", book_id)
            if not self._event(db, event_id, payload):
                return {**self._snapshot(db, book_id), "event_applied": False}
            self._book_chronology(db, book, timestamp)
            if target > 300 or (
                target > 100 and regime not in {"strong_bull", "parabolic"}
            ):
                raise LedgerError("target exceeds regime cap")
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
                    "quantity": "0",
                    "cost_basis": "0",
                    "invested_budget": "0",
                    "realized_pnl": "0",
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
                Decimal(campaign["quantity"]),
                Decimal(campaign["cost_basis"]),
            )
            if row and quantity == 0:
                raise LedgerError("closed campaign cannot reopen")
            if target < old_target:
                raise LedgerError("cumulative target cannot decrease; use sell")
            if target == old_target:
                if fee:
                    raise LedgerError("unchanged target cannot incur a buy fee")
                campaign["last_event_at"] = timestamp
                self._save(db, "campaigns", campaign_id, campaign)
                return {**self._snapshot(db, book_id), "event_applied": True}
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
                and Decimal(c["quantity"]) > 0
                and c["campaign_id"] != campaign_id
            ]
            if any(c["symbol"] == symbol for c in active):
                raise LedgerError("symbol already has an active campaign")
            if len(active) + 1 > book["max_slots"]:
                raise LedgerError("name slot limit exceeded")
            if sum((Decimal(c["target_pct"]) for c in active), target) > 1000:
                raise LedgerError("book target units exceed 10")
            budget = Decimal(book["unit_budget"]) * (target - old_target) / 100
            cash = Decimal(book["free_cash"])
            if budget + fee > cash:
                raise LedgerError("insufficient strategy cash")
            bought = budget / price
            leg = {
                "event_id": event_id,
                "campaign_id": campaign_id,
                "side": "BUY",
                "quantity": str(bought),
                "price": str(price),
                "amount": str(budget),
                "fee": str(fee),
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
                quantity=str(quantity + bought),
                cost_basis=str(cost + budget + fee),
                invested_budget=str(Decimal(campaign["invested_budget"]) + budget),
                last_event_at=timestamp,
                mark_price=str(price),
                mark_at=timestamp,
                mark_basis="last_trade",
            )
            book["free_cash"] = str(cash - budget - fee)
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
        fee=0,
        source_hash=None,
    ):
        price, fee = _number(price, positive=True), _number(fee)
        timestamp = _time(occurred_at)
        requested = None if quantity is None else str(_number(quantity, positive=True))
        payload = {
            "kind": "sell",
            "campaign_id": _text(campaign_id),
            "price": str(price),
            "occurred_at": timestamp,
            "quantity": requested,
            "fee": str(fee),
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
            held, cost = Decimal(campaign["quantity"]), Decimal(campaign["cost_basis"])
            sold = held if requested is None else Decimal(requested)
            if sold <= 0 or sold > held:
                raise LedgerError("sell quantity exceeds holding or is zero")
            allocated = cost if sold == held else cost * sold / held
            proceeds = sold * price - fee
            book = self._get(db, "books", book_id)
            if Decimal(book["free_cash"]) + proceeds < 0:
                raise LedgerError("fee would create negative cash")
            pnl = proceeds - allocated
            campaign.update(
                quantity=str(held - sold),
                cost_basis=str(cost - allocated),
                realized_pnl=str(Decimal(campaign["realized_pnl"]) + pnl),
                last_event_at=timestamp,
                mark_price=str(price),
                mark_at=timestamp,
                mark_basis="last_trade",
            )
            book.update(
                free_cash=str(Decimal(book["free_cash"]) + proceeds),
                realized_pnl=str(Decimal(book["realized_pnl"]) + pnl),
            )
            leg = {
                "event_id": event_id,
                "campaign_id": campaign_id,
                "side": "SELL",
                "quantity": str(sold),
                "price": str(price),
                "amount": str(sold * price),
                "fee": str(fee),
                "occurred_at": timestamp,
                "allocated_cost": str(allocated),
                "realized_pnl": str(pnl),
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
        market_value, unrealized = Decimal(0), Decimal(0)
        for row in db.execute("SELECT data FROM campaigns ORDER BY id"):
            campaign = json.loads(row[0])
            if campaign["book_id"] != book_id:
                continue
            quantity, cost = (
                Decimal(campaign["quantity"]),
                Decimal(campaign["cost_basis"]),
            )
            value = quantity * Decimal(campaign["mark_price"])
            pnl = value - cost
            campaign.update(
                average_cost=str(cost / quantity) if quantity else None,
                market_value=str(value),
                unrealized_pnl=str(pnl),
                mark_freshness="historical_observation_not_live",
                status="OPEN" if quantity else "CLOSED",
            )
            campaign["legs"] = [
                json.loads(r[0])
                for r in db.execute(
                    "SELECT data FROM legs WHERE campaign_id=? ORDER BY rowid",
                    (campaign["campaign_id"],),
                )
            ]
            executions.extend(
                json.loads(r[0])
                for r in db.execute(
                    "SELECT data FROM executions WHERE campaign_id=? ORDER BY rowid",
                    (campaign["campaign_id"],),
                )
            )
            campaigns.append(campaign)
            market_value += value
            unrealized += pnl
        equity = Decimal(book["free_cash"]) + market_value
        return {
            **book,
            "market_value": str(market_value),
            "unrealized_pnl": str(unrealized),
            "equity": str(equity),
            "portfolio_return_pct": str(
                (equity / Decimal(book["initial_capital"]) - 1) * 100
            ),
            "campaigns": campaigns,
            "executions": executions,
            "accounting_basis": "fractional_strategy_quantity; buy_fee_explicit; sell_fee_explicit; target_budget_excludes_fees; no_fx_aggregation",
        }
