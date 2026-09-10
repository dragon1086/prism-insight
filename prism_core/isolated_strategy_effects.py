"""Disabled no-order effects seam; not an eligibility engine or execution receipt.

The supervisor must authenticate registration provenance. This in-process type
check is not a security boundary; the namespace supplies that boundary. Legacy
execution guards deliberately remain unchanged until all helper effects are
reviewed. Projection failure never rolls back a committed strategy event.
"""
from dataclasses import dataclass
from contextlib import closing
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from prism_core.isolated_agent_runtime import IsolatedAgentRuntime
from prism_core.strategy_ledger import StrategyLedger

EFFECTS_RUNTIME_ENABLED = False


class EffectsFailure(RuntimeError):
    """Sanitized case failure, never a successful empty batch."""


@dataclass(frozen=True)
class EffectsRegistration:
    case_id: str
    book_id: str
    source_hash: str
    occurred_at: str
    campaigns: tuple
    clock_timezone: str = "Asia/Seoul"
    # (ticker, canonical campaign hash, original normalized units, frozen scenario JSON)
    exit_bases: tuple = ()

    def __post_init__(self):
        try:
            identifiers = [self.case_id, self.book_id]
            if type(self.campaigns) is not tuple or not 1 <= len(self.campaigns) <= 10:
                raise ValueError()
            for pair in self.campaigns:
                if type(pair) is not tuple or len(pair) != 2:
                    raise ValueError()
                identifiers.extend(pair)
            if any(not isinstance(v, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", v)
                   for v in identifiers):
                raise ValueError()
            if len(dict(self.campaigns)) != len(self.campaigns) or len({c for _, c in self.campaigns}) != len(self.campaigns):
                raise ValueError()
            if not isinstance(self.source_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", self.source_hash):
                raise ValueError()
            if datetime.fromisoformat(self.occurred_at.replace("Z", "+00:00")).utcoffset() is None:
                raise ValueError()
            # This must match the fixed isolated namespace TZ, NOT exchange TZ.
            # Legacy KR and US analyzers both use naive datetime.now().
            if self.clock_timezone != "Asia/Seoul":
                raise ValueError()
            if type(self.exit_bases) is not tuple or len(self.exit_bases) > len(self.campaigns):
                raise ValueError()
            seen = set()
            for basis in self.exit_bases:
                if type(basis) is not tuple or len(basis) != 4:
                    raise ValueError()
                ticker, digest, units, scenario_json = basis
                if ticker in seen or ticker not in dict(self.campaigns):
                    raise ValueError()
                seen.add(ticker)
                if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
                    raise ValueError()
                if not isinstance(units, str) or not Decimal(units).is_finite() or Decimal(units) <= 0:
                    raise ValueError()
                if not isinstance(scenario_json, str) or len(scenario_json) > 262144 or type(json.loads(scenario_json)) is not dict:
                    raise ValueError()
        except (TypeError, ValueError, AttributeError, InvalidOperation):
            raise EffectsFailure("Invalid effects registration") from None


def effects_for(agent, operation):
    if getattr(agent, "_isolated_runtime", None) is None:
        return None
    if not EFFECTS_RUNTIME_ENABLED:
        raise RuntimeError("No-order effects require independent runtime review")
    adapter = getattr(agent, "_no_order_effects", None)
    if (operation not in {"process_reports", "update_holdings"}
            or type(adapter) is not IsolatedStrategyEffects or adapter.agent is not agent):
        raise EffectsFailure("Unbound or unsupported no-order operation")
    adapter._validate_binding()
    return adapter


class IsolatedStrategyEffects:
    def __init__(self, agent, ledger, registration):
        if type(registration) is not EffectsRegistration or type(ledger) is not StrategyLedger:
            raise EffectsFailure("Typed effects registration and ledger required")
        self.agent, self.ledger, self.registration = agent, ledger, registration
        self.runtime = getattr(agent, "_isolated_runtime", None)
        self._validate_binding()

    def _validate_binding(self):
        try:
            runtime = self.runtime
            if type(runtime) is not IsolatedAgentRuntime or self.agent._isolated_runtime is not runtime:
                raise ValueError()
            # Multi-account production passes must not multiply strategy rows.
            # This first integration supports one explicit virtual portfolio.
            if len(runtime.accounts()) != 1 or self.agent.db_path != runtime.db_path:
                raise ValueError()
            with closing(runtime.connect()) as check:
                check.execute("SELECT owner_hash FROM prism_virtual_runtime WHERE id=1").fetchone()
            databases = self.agent.conn.execute("PRAGMA database_list").fetchall()
            if len(databases) != 1 or Path(databases[0][2]).resolve() != Path(runtime.db_path):
                raise ValueError()
            ledger_path = Path(self.ledger.path)
            if (not ledger_path.is_relative_to(runtime.root) or ledger_path == Path(runtime.db_path)
                    or ledger_path.is_symlink()):
                raise ValueError()
            book = self.ledger.snapshot(self.registration.book_id)
            if book["market"] != runtime.market or book["mode"] != "SHADOW":
                raise ValueError()
        except Exception:
            raise EffectsFailure("Isolated effects binding is invalid") from None

    def _identity(self, ticker, operation):
        self._validate_binding()
        campaign = dict(self.registration.campaigns).get(ticker)
        if campaign is None:
            raise EffectsFailure("Ticker is not registered for this case")
        if operation in {"exit", "pilot_advance"}:
            candidates = self.ledger.snapshot(self.registration.book_id)["campaigns"]
            if not any(c["campaign_id"] == campaign and c["book_id"] == self.registration.book_id
                       and c["symbol"] == ticker for c in candidates):
                raise EffectsFailure("Registered campaign identity does not match the book and ticker")
        payload = [self.registration.case_id, self.registration.book_id, campaign, operation]
        return campaign, "isolated:" + hashlib.sha256(json.dumps(payload).encode()).hexdigest()

    def _finish(self, snapshot, ticker, company_name, scenario):
        try:
            self._project(snapshot, ticker, company_name, scenario)
        except Exception:
            raise EffectsFailure("Strategy committed; isolated projection requires recovery") from None
        return snapshot["event_applied"]

    def record_entry(self, *, ticker, company_name, price, scenario, is_add):
        campaign, event = self._identity(ticker, "entry")
        if is_add is not False:
            raise EffectsFailure("Ordinary pyramiding is unavailable; use the pilot lifecycle")
        if (type(scenario) is not dict
                or self.ledger.snapshot(self.registration.book_id)["cohort"] == "split-pilot-v1"
                or not isinstance(scenario.get("regime_entry_policy", {}), dict)
                or scenario.get("regime_entry_policy", {}).get("mode") == "rebound_pilot"):
            raise EffectsFailure("Explicit pilot entry contract required")
        try:
            snapshot = self.ledger.apply_target(event, self.registration.book_id, campaign,
                ticker, 100, price, self.registration.occurred_at,
                source_hash=self.registration.source_hash, reason="ISOLATED_AGENT_SELECTED_ENTRY")
        except Exception:
            raise EffectsFailure("Strategy entry failed") from None
        return self._finish(snapshot, ticker, company_name, scenario)

    def open_pilot(self, *, ticker, company_name, price, scenario, signal_bar, calendar, entry_eligible):
        campaign, event = self._identity(ticker, "pilot_open")
        try:
            snapshot = self.ledger.open_pilot(event, self.registration.book_id, campaign,
                ticker, price, self.registration.occurred_at, owner="split-pilot-v1",
                signal_bar=signal_bar, calendar=calendar, entry_eligible=entry_eligible)
        except Exception:
            raise EffectsFailure("Strategy pilot entry failed") from None
        return self._finish(snapshot, ticker, company_name, scenario)

    def advance_pilot(self, *, ticker, company_name, scenario, expected_revision, evidence):
        campaign, event = self._identity(ticker, "pilot_advance")
        try:
            snapshot = self.ledger.advance_pilot(event, campaign, expected_revision=expected_revision,
                evidence=evidence, occurred_at=self.registration.occurred_at)
        except Exception:
            raise EffectsFailure("Strategy pilot transition failed") from None
        return self._finish(snapshot, ticker, company_name, scenario)

    def record_exit(self, *, ticker, price, fraction=1):
        campaign, event = self._identity(ticker, "exit")
        try:
            fraction = Decimal(str(fraction))
            if not fraction.is_finite() or not 0 < fraction <= 1:
                raise ValueError()
            state = self.ledger.snapshot(self.registration.book_id)
            holding = next(c for c in state["campaigns"] if c["campaign_id"] == campaign)
            basis = next((b for b in self.registration.exit_bases if b[0] == ticker), None)
            if fraction != 1 and basis is None:
                raise ValueError("Partial exits require a registered unit basis")
            kwargs, scenario = {}, {}
            if basis is not None:
                kwargs = {"quantity": Decimal(basis[2]) * fraction, "expected_campaign_hash": basis[1],
                          "expected_normalized_units": basis[2]}
                scenario = json.loads(basis[3])
            snapshot = self.ledger.sell(event, campaign, price, self.registration.occurred_at,
                source_hash=self.registration.source_hash, **kwargs)
        except Exception:
            raise EffectsFailure("Strategy exit failed") from None
        return self._finish(snapshot, ticker, holding["symbol"], scenario)

    def _project(self, snapshot, ticker, company_name, scenario):
        if snapshot["book_id"] != self.registration.book_id:
            raise EffectsFailure("Projection book identity mismatch")
        campaign = next(c for c in snapshot["campaigns"] if c["campaign_id"] == dict(self.registration.campaigns)[ticker])
        if campaign["book_id"] != self.registration.book_id or campaign["symbol"] != ticker:
            raise EffectsFailure("Projection campaign identity mismatch")
        account = self.runtime.accounts()[0]
        # Projection is explicitly price/weight based; no fictional cash or
        # whole-share quantity is introduced to satisfy a legacy schema.
        scenario = dict(scenario)
        scenario["_strategy_projection"] = {
            "book_id": self.registration.book_id, "campaign_id": campaign["campaign_id"],
            "units_basis": "NORMALIZED_UNITS_NOT_SHARES", "account_execution_status": "UNKNOWN",
            "normalized_units": campaign["normalized_units"],
            "remaining_allocation": campaign["remaining_allocation"],
            "cumulative_deployed_allocation": campaign["cumulative_deployed_allocation"],
            "source_hash": self.registration.source_hash,
            "strategy_event_at": campaign["last_event_at"],
            "strategy_entry_at": campaign["legs"][0]["occurred_at"],
        }
        serialized = json.dumps(scenario, allow_nan=False)
        # Only these two source-defined table names exist. No registration,
        # model output, or caller-provided SQL participates in this mapping.
        def query(sql, values):
            if self.runtime.market == "US":
                sql = sql.replace("stock_holdings", "us_stock_holdings")
            return self.agent.conn.execute(sql, values)

        with self.agent.conn:
            rows = query("SELECT id, scenario FROM stock_holdings WHERE account_key=? AND ticker=?",
                (account["account_key"], ticker)).fetchall()
            if len(rows) > 1:
                raise ValueError("Duplicate projected slot")
            if rows:
                existing = json.loads(rows[0][1]).get("_strategy_projection", {})
                if (existing.get("campaign_id") != campaign["campaign_id"]
                        or existing.get("book_id") != self.registration.book_id):
                    raise ValueError("Foreign projected slot")
            if campaign["status"] == "CLOSED":
                query("DELETE FROM stock_holdings WHERE account_key=? AND ticker=?",
                    (account["account_key"], ticker))
                return
            zone = ZoneInfo(self.registration.clock_timezone)
            buy_date = datetime.fromisoformat(campaign["legs"][0]["occurred_at"].replace("Z", "+00:00")).astimezone(zone).strftime("%Y-%m-%d %H:%M:%S")
            values = (account["account_key"], account["name"], ticker, company_name,
                float(campaign["average_cost"]), buy_date,
                float(campaign["mark_price"]), campaign["last_event_at"], serialized,
                scenario.get("target_price"), scenario.get("stop_loss"), scenario.get("sector"))
            if rows:
                query("UPDATE stock_holdings SET account_key=?, account_name=?, ticker=?, company_name=?, buy_price=?, buy_date=?, current_price=?, last_updated=?, scenario=?, target_price=?, stop_loss=?, sector=? WHERE id=?", (*values, rows[0][0]))
            else:
                query("INSERT INTO stock_holdings (account_key, account_name, ticker, company_name, buy_price, buy_date, current_price, last_updated, scenario, target_price, stop_loss, sector) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", values)
