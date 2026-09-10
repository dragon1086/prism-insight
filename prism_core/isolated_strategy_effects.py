"""Disabled no-order effects seam; not an eligibility engine or execution receipt.

The supervisor must authenticate registration provenance. This in-process type
check is not a security boundary; the namespace supplies that boundary. Legacy
execution guards deliberately remain unchanged until all helper effects are
reviewed. Projection failure never rolls back a committed strategy event.
"""
from dataclasses import dataclass
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from prism_core.isolated_agent_runtime import IsolatedAgentRuntime
from prism_core.strategy_ledger import StrategyLedger

EFFECTS_RUNTIME_ENABLED = False


def _source_now():
    return datetime.now(timezone.utc).isoformat()


def _encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _digest(value):
    return hashlib.sha256(_encoded(value).encode()).hexdigest()


class EffectsFailure(RuntimeError):
    """Sanitized case failure, never a successful empty batch."""


@dataclass(frozen=True)
class EffectsPipelineContext:
    """Immutable source envelopes; outer supervisor authenticates the capture.

    Hashes prove internal binding, not provider truth. No network callback is
    accepted. Missing evidence never becomes an empty/neutral market response.
    """
    case_id: str
    source_hash: str
    payload_json: str

    def __post_init__(self):
        try:
            if not isinstance(self.payload_json, str) or len(self.payload_json) > 1024 * 1024:
                raise ValueError()
            if hashlib.sha256(self.payload_json.encode()).hexdigest() != self.source_hash:
                raise ValueError()
            payload = json.loads(self.payload_json)
            if type(payload) is not dict or set(payload) - {"quote", "corporate_status", "corporate_event", "regime", "pulse", "journal"}:
                raise ValueError()
            for kind, records in payload.items():
                if type(records) is not dict or len(records) > 10:
                    raise ValueError()
                for ticker, envelope in records.items():
                    if not isinstance(ticker, str) or not re.fullmatch(r"[A-Z0-9.-]{1,12}", ticker):
                        raise ValueError()
                    if type(envelope) is not dict or set(envelope) != {"value", "source", "as_of", "observed_at", "value_hash"}:
                        raise ValueError()
                    value = envelope["value"]
                    serialized = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
                    if hashlib.sha256(serialized.encode()).hexdigest() != envelope["value_hash"]:
                        raise ValueError()
                    if not isinstance(envelope["source"], str) or not 1 <= len(envelope["source"]) <= 256:
                        raise ValueError()
                    as_of, observed = (datetime.fromisoformat(envelope[k].replace("Z", "+00:00")) for k in ("as_of", "observed_at"))
                    if as_of.utcoffset() is None or observed.utcoffset() is None or as_of > observed:
                        raise ValueError()
                    if kind == "quote" and (type(value) not in (int, float) or not Decimal(str(value)).is_finite() or value <= 0):
                        raise ValueError()
                    if kind == "corporate_status" and (not isinstance(value, str) or not re.fullmatch(r"[0-9]{2}", value)):
                        raise ValueError()
                    if kind == "corporate_event" and (type(value) is not dict or set(value) != {"should_exit", "reason"}
                            or type(value["should_exit"]) is not bool or not isinstance(value["reason"], str)
                            or len(value["reason"]) > 512
                            or (value["should_exit"] and not value["reason"].strip())):
                        raise ValueError()
                    if kind == "regime" and (type(value) is not dict or set(value) != {"regime", "summary"}
                            or value["regime"] not in {"parabolic", "strong_bull", "moderate_bull", "sideways", "moderate_bear", "strong_bear"}
                            or not isinstance(value["summary"], str) or len(value["summary"]) > 8192):
                        raise ValueError()
                    if kind == "pulse" and value not in {"UPTREND", "UNDER_PRESSURE", "CORRECTION"}:
                        raise ValueError()
                    if kind == "journal" and (type(value) is not dict or set(value) != {"context", "adjustment", "reasons"}
                            or not isinstance(value["context"], str) or len(value["context"]) > 65536
                            or type(value["adjustment"]) is not int or not -3 <= value["adjustment"] <= 3
                            or type(value["reasons"]) is not list or len(value["reasons"]) > 32
                            or any(not isinstance(v, str) or len(v) > 1024 for v in value["reasons"])):
                        raise ValueError()
        except (ValueError, TypeError, KeyError, AttributeError, InvalidOperation):
            raise EffectsFailure("Invalid source-bound effects context") from None

    def read(self, kind, ticker, occurred_at):
        try:
            envelope = json.loads(self.payload_json)[kind][ticker]
            current = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
            observed = datetime.fromisoformat(envelope["observed_at"].replace("Z", "+00:00"))
            if current.utcoffset() is None or not 0 <= (current - observed).total_seconds() <= 120:
                raise ValueError()
            if kind == "quote":
                as_of = datetime.fromisoformat(envelope["as_of"].replace("Z", "+00:00"))
                if not 0 <= (current - as_of).total_seconds() <= 120:
                    raise ValueError()
            return envelope["value"]
        except (KeyError, ValueError, TypeError, AttributeError):
            raise EffectsFailure("Required source context is missing or stale") from None


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
    context_hash: str | None = None

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
            if self.context_hash is not None and (not isinstance(self.context_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", self.context_hash)):
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
        raise RuntimeError("Virtual execution requires a reviewed no-order adapter")
    adapter = getattr(agent, "_no_order_effects", None)
    if (operation not in {"process_reports", "update_holdings"}
            or type(adapter) is not IsolatedStrategyEffects or adapter.agent is not agent):
        raise EffectsFailure("Unbound or unsupported no-order operation")
    adapter._validate_binding()
    adapter.require_pipeline_context()
    return adapter


def observe_or_emit(agent, emitter, *args, **kwargs):
    """Retain production emitter; isolated calls only record bounded metadata."""
    if getattr(agent, "_no_order_effects", None) is None:
        return emitter(*args, **kwargs)
    adapter = effects_for(agent, "process_reports")
    adapter.observe("observation", status="CAPTURED", ticker=kwargs.get("ticker"))
    return None


class IsolatedStrategyEffects:
    def __init__(self, agent, ledger, registration, *, pipeline_context=None):
        if type(registration) is not EffectsRegistration or type(ledger) is not StrategyLedger:
            raise EffectsFailure("Typed effects registration and ledger required")
        self.agent, self.ledger, self.registration = agent, ledger, registration
        self.runtime = getattr(agent, "_isolated_runtime", None)
        self.pipeline_context = pipeline_context
        self.observations = []
        self._validate_binding()
        with self.agent.conn:
            self.agent.conn.execute("CREATE TABLE IF NOT EXISTS prism_strategy_history_projection (event_id TEXT PRIMARY KEY, campaign_id TEXT UNIQUE NOT NULL, history_id INTEGER NOT NULL, payload_hash TEXT NOT NULL)")
            self.agent.conn.execute("CREATE TABLE IF NOT EXISTS prism_isolated_effect_preparations (event_id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL, spec_hash TEXT NOT NULL, spec_json TEXT NOT NULL)")
            self.agent.conn.execute("CREATE TABLE IF NOT EXISTS prism_isolated_effect_receipts (event_id TEXT PRIMARY KEY, ledger_digest TEXT NOT NULL, projected_row_hash TEXT NOT NULL)")
            self.agent.conn.execute("CREATE TRIGGER IF NOT EXISTS prism_effect_prep_immutable_update BEFORE UPDATE ON prism_isolated_effect_preparations BEGIN SELECT RAISE(ABORT,'immutable preparation'); END")
            self.agent.conn.execute("CREATE TRIGGER IF NOT EXISTS prism_effect_prep_immutable_delete BEFORE DELETE ON prism_isolated_effect_preparations BEGIN SELECT RAISE(ABORT,'immutable preparation'); END")
            self.agent.conn.execute("CREATE TRIGGER IF NOT EXISTS prism_effect_receipt_immutable_update BEFORE UPDATE ON prism_isolated_effect_receipts BEGIN SELECT RAISE(ABORT,'immutable projection receipt'); END")
            self.agent.conn.execute("CREATE TRIGGER IF NOT EXISTS prism_effect_receipt_immutable_delete BEFORE DELETE ON prism_isolated_effect_receipts BEGIN SELECT RAISE(ABORT,'immutable projection receipt'); END")
            self.agent.conn.execute("CREATE TRIGGER IF NOT EXISTS prism_history_projection_immutable_update BEFORE UPDATE ON prism_strategy_history_projection BEGIN SELECT RAISE(ABORT,'immutable history projection'); END")
            self.agent.conn.execute("CREATE TRIGGER IF NOT EXISTS prism_history_projection_immutable_delete BEFORE DELETE ON prism_strategy_history_projection BEGIN SELECT RAISE(ABORT,'immutable history projection'); END")

    def require_pipeline_context(self):
        context = self.pipeline_context
        if (type(context) is not EffectsPipelineContext or context.case_id != self.registration.case_id
                or context.source_hash != self.registration.context_hash):
            raise EffectsFailure("Registered pipeline source context required")

    def quote(self, ticker):
        return float(self._read_source("quote", ticker))

    def _read_source(self, kind, ticker):
        self.require_pipeline_context()
        if ticker != "MARKET" or kind not in {"regime", "pulse"}:
            self._identity(ticker, "source_read")
        # Both event-time provenance and execution-time freshness matter. A
        # frozen registration clock cannot make an old quote fresh after an LLM.
        self.pipeline_context.read(kind, ticker, self.registration.occurred_at)
        return self.pipeline_context.read(kind, ticker, _source_now())

    def corporate_status(self, ticker):
        return self._read_source("corporate_status", ticker)

    def market_regime(self):
        result = self._read_source("regime", "MARKET")
        self.agent._live_regime_summary = result["summary"]
        return result["regime"]

    def market_pulse(self):
        return self._read_source("pulse", "MARKET")

    def journal(self, ticker):
        return self._read_source("journal", ticker)

    def corporate_event(self, ticker):
        result = self._read_source("corporate_event", ticker)
        return result["should_exit"], result["reason"]

    def observe(self, kind, *, status, ticker=None):
        self.require_pipeline_context()
        if kind not in {"broker_window", "analysis_failure", "strategy_exit", "strategy_entry", "strategy_mark", "analysis_message", "watchlist", "journal", "observation"}:
            raise EffectsFailure("Unsupported local observation category")
        if not isinstance(status, str) or not re.fullmatch(r"[A-Z0-9_]{1,64}", status) or len(self.observations) >= 4096:
            raise EffectsFailure("Invalid or exhausted local observation sink")
        if ticker is not None:
            self._identity(ticker, "source_read")
        self.observations.append({"kind": kind, "status": status, "ticker": ticker})

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
        if operation in {"exit", "pilot_advance", "mark"}:
            candidates = self.ledger.snapshot(self.registration.book_id)["campaigns"]
            if not any(c["campaign_id"] == campaign and c["book_id"] == self.registration.book_id
                       and c["symbol"] == ticker for c in candidates):
                raise EffectsFailure("Registered campaign identity does not match the book and ticker")
        payload = [self.registration.case_id, self.registration.book_id, campaign, operation]
        return campaign, "isolated:" + hashlib.sha256(json.dumps(payload).encode()).hexdigest()

    def _finish(self, snapshot, ticker, company_name, scenario, projection_spec=None):
        try:
            if projection_spec is not None:
                company_name, scenario = projection_spec["company_name"], projection_spec["scenario"]
            self._project(snapshot, ticker, company_name, scenario, projection_spec)
        except Exception:
            raise EffectsFailure("Strategy committed; isolated projection requires recovery") from None
        return snapshot["event_applied"]

    def _row_state(self, ticker):
        sql = "SELECT company_name,buy_price,buy_date,current_price,last_updated,scenario,target_price,stop_loss,sector FROM stock_holdings WHERE account_key=? AND ticker=?"
        if self.runtime.market == "US":
            sql = sql.replace("stock_holdings", "us_stock_holdings")
        rows = self.agent.conn.execute(sql, (self.runtime.accounts()[0]["account_key"], ticker)).fetchall()
        if len(rows) > 1:
            raise EffectsFailure("Duplicate projected campaign rows")
        if not rows:
            return "ABSENT", ticker, {}
        values = list(rows[0])
        values[5] = json.loads(values[5])
        return _digest(values), values[0], values[5]

    def _prepare_spec(self, event, campaign, ticker, action, facts, company_name, scenario, *, supplied_scenario=False):
        """PREPARED is durable, but is explicitly NOT proof of ledger commit."""
        spec = {"schema_version": 1, "event_id": event, "case_id": self.registration.case_id,
            "book_id": self.registration.book_id, "campaign_id": campaign, "ticker": ticker,
            "source_hash": self.registration.source_hash, "occurred_at": self.registration.occurred_at,
            "clock_timezone": self.registration.clock_timezone, "action": action, "facts": facts,
            "company_name": company_name, "scenario": scenario, "prior_row_hash": self._row_state(ticker)[0]}
        encoded = _encoded(spec)
        if len(encoded) > 1024 * 1024:
            raise EffectsFailure("Projection preparation exceeds bound")
        with self.agent.conn:
            existing = self.agent.conn.execute("SELECT spec_hash,spec_json FROM prism_isolated_effect_preparations WHERE event_id=?", (event,)).fetchone()
            if existing:
                stored = json.loads(existing[1])
                fields = set(spec) - {"prior_row_hash", "company_name", "scenario"}
                if (existing[0] != _digest(stored) or any(stored[k] != spec[k] for k in fields)
                        or (supplied_scenario and (stored["scenario"] != scenario or stored["company_name"] != company_name))):
                    raise EffectsFailure("Conflicting immutable projection preparation")
                return stored
            self.agent.conn.execute("INSERT INTO prism_isolated_effect_preparations VALUES (?,?,?,?)",
                (event, campaign, _digest(spec), encoded))
        return spec

    def _expected_payload(self, spec):
        """Encode fixed ledger-call metadata, never recompute eligibility."""
        facts, action = spec["facts"], spec["action"]
        stamp = datetime.fromisoformat(spec["occurred_at"].replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
        common = {"campaign_id": spec["campaign_id"], "occurred_at": stamp}
        if action == "entry":
            return {**common, "kind": "target", "book_id": spec["book_id"], "symbol": spec["ticker"],
                "target_pct": "100", "price": facts["price"], "policy_version": "split-ledger-v1",
                "reason": "ISOLATED_AGENT_SELECTED_ENTRY", "regime": facts["regime"],
                "source_hash": spec["source_hash"], "fee_rate": "0", "slippage_rate": "0"}
        if action == "mark":
            return {**common, "kind": "mark", "price": facts["price"], "source_hash": spec["source_hash"]}
        if action == "exit":
            value = {**common, "kind": "sell", "price": facts["price"], "normalized_units": facts["quantity"],
                "fee_rate": "0", "slippage_rate": "0", "source_hash": spec["source_hash"]}
            for key in ("expected_campaign_hash", "expected_normalized_units"):
                if facts.get(key) is not None:
                    value[key] = facts[key]
            return value
        if action == "pilot_advance":
            return {**common, "kind": "pilot_advance", "expected_revision": facts["expected_revision"], "evidence": facts["evidence"]}
        if action == "pilot_open":
            from prism_core.pilot_lifecycle import create_pilot
            pilot = create_pilot(market=self.runtime.market, mode="SHADOW", owner="split-pilot-v1",
                entry_at=stamp, entry_price=Decimal(facts["price"]), signal_bar=facts["signal_bar"],
                calendar=facts["calendar"], entry_eligible=facts["entry_eligible"])
            return {"kind": "pilot_open", "book_id": spec["book_id"], "campaign_id": spec["campaign_id"],
                "symbol": spec["ticker"], "pilot": pilot, "signal_bar": facts["signal_bar"]}
        raise EffectsFailure("Unsupported prepared ledger action")

    def _verify_projection_source(self, spec):
        row = self.agent.conn.execute("SELECT spec_hash,spec_json FROM prism_isolated_effect_preparations WHERE event_id=?", (spec["event_id"],)).fetchone()
        latest = self.agent.conn.execute("SELECT event_id FROM prism_isolated_effect_preparations WHERE campaign_id=? ORDER BY rowid DESC LIMIT 1", (spec["campaign_id"],)).fetchone()
        if (row is None or row[0] != _digest(spec) or json.loads(row[1]) != spec
                or latest[0] != spec["event_id"]):
            raise EffectsFailure("Missing or superseded projection preparation")
        receipt = self.ledger.event_receipt(spec["event_id"])
        if (receipt["latest_strategy_event_id"] != spec["event_id"]
                or receipt["book_id"] != self.registration.book_id
                or receipt["campaign_id"] != spec["campaign_id"] or receipt["symbol"] != spec["ticker"]
                or receipt["payload"] != self._expected_payload(spec)
                or receipt["digest"] != _digest(self._expected_payload(spec))):
            raise EffectsFailure("Canonical event proof does not match projection preparation")
        previous = self.agent.conn.execute("SELECT ledger_digest,projected_row_hash FROM prism_isolated_effect_receipts WHERE event_id=?", (spec["event_id"],)).fetchone()
        row_hash = self._row_state(spec["ticker"])[0]
        if previous and previous[0] != receipt["digest"]:
            raise EffectsFailure("Canonical receipt digest changed")
        allowed = {"ABSENT", spec["prior_row_hash"]}
        if previous:
            allowed.add(previous[1])
        if row_hash not in allowed:
            raise EffectsFailure("Uncommitted or newer projection inputs require newer-spec recovery")
        return receipt, previous, row_hash

    def repair_projection(self, *, ticker, operation):
        """Replay ONLY a proved projection. No ledger/model/quote/order writes."""
        if operation not in {"entry", "exit", "mark", "pilot_open", "pilot_advance"}:
            raise EffectsFailure("Unsupported projection repair operation")
        campaign, event = self._identity(ticker, operation)
        try:
            row = self.agent.conn.execute("SELECT spec_hash,spec_json FROM prism_isolated_effect_preparations WHERE event_id=?", (event,)).fetchone()
            if row is None:
                raise ValueError()
            spec = json.loads(row[1])
            if (row[0] != _digest(spec) or spec["case_id"] != self.registration.case_id
                    or spec["book_id"] != self.registration.book_id or spec["campaign_id"] != campaign
                    or spec["ticker"] != ticker or spec["source_hash"] != self.registration.source_hash
                    or spec["occurred_at"] != self.registration.occurred_at or spec["action"] != operation):
                raise ValueError()
            self._verify_projection_source(spec)
            snapshot = self.ledger.snapshot(self.registration.book_id)
            return self._project(snapshot, ticker, spec["company_name"], spec["scenario"], spec)
        except Exception:
            raise EffectsFailure("Projection-only repair proof unavailable or superseded") from None

    def record_entry(self, *, ticker, company_name, price, scenario, is_add):
        campaign, event = self._identity(ticker, "entry")
        self._validate_effect_price(ticker, price)
        if is_add is not False:
            raise EffectsFailure("Ordinary pyramiding is unavailable; use the pilot lifecycle")
        if (type(scenario) is not dict
                or self.ledger.snapshot(self.registration.book_id)["cohort"] == "split-pilot-v1"
                or not isinstance(scenario.get("regime_entry_policy", {}), dict)
                or scenario.get("regime_entry_policy", {}).get("mode") == "rebound_pilot"):
            raise EffectsFailure("Explicit pilot entry contract required")
        try:
            regime = self.market_regime() if self.pipeline_context is not None else "isolated_unverified"
            spec = self._prepare_spec(event, campaign, ticker, "entry", {"price": str(Decimal(str(price))), "regime": regime},
                company_name, scenario, supplied_scenario=True)
            snapshot = self.ledger.apply_target(event, self.registration.book_id, campaign,
                ticker, 100, price, self.registration.occurred_at,
                source_hash=self.registration.source_hash, reason="ISOLATED_AGENT_SELECTED_ENTRY", regime=regime)
        except Exception:
            raise EffectsFailure("Strategy entry failed") from None
        return self._finish(snapshot, ticker, company_name, scenario, spec)

    def open_pilot(self, *, ticker, company_name, price, scenario, signal_bar, calendar, entry_eligible):
        campaign, event = self._identity(ticker, "pilot_open")
        self._validate_effect_price(ticker, price)
        try:
            spec = self._prepare_spec(event, campaign, ticker, "pilot_open", {"price": str(Decimal(str(price))),
                "signal_bar": signal_bar, "calendar": calendar, "entry_eligible": entry_eligible},
                company_name, scenario, supplied_scenario=True)
            snapshot = self.ledger.open_pilot(event, self.registration.book_id, campaign,
                ticker, price, self.registration.occurred_at, owner="split-pilot-v1",
                signal_bar=signal_bar, calendar=calendar, entry_eligible=entry_eligible)
        except Exception:
            raise EffectsFailure("Strategy pilot entry failed") from None
        return self._finish(snapshot, ticker, company_name, scenario, spec)

    def advance_pilot(self, *, ticker, company_name, scenario, expected_revision, evidence):
        campaign, event = self._identity(ticker, "pilot_advance")
        try:
            spec = self._prepare_spec(event, campaign, ticker, "pilot_advance",
                {"expected_revision": expected_revision, "evidence": evidence}, company_name, scenario,
                supplied_scenario=True)
            snapshot = self.ledger.advance_pilot(event, campaign, expected_revision=expected_revision,
                evidence=evidence, occurred_at=self.registration.occurred_at)
        except Exception:
            raise EffectsFailure("Strategy pilot transition failed") from None
        return self._finish(snapshot, ticker, company_name, scenario, spec)

    def record_exit(self, *, ticker, price, fraction=1, sell_reason=None):
        campaign, event = self._identity(ticker, "exit")
        self._validate_effect_price(ticker, price)
        try:
            fraction = Decimal(str(fraction))
            if not fraction.is_finite() or not 0 < fraction <= 1:
                raise ValueError()
            state = self.ledger.snapshot(self.registration.book_id)
            holding = next(c for c in state["campaigns"] if c["campaign_id"] == campaign)
            basis = next((b for b in self.registration.exit_bases if b[0] == ticker), None)
            if fraction != 1 and basis is None:
                raise ValueError("Partial exits require a registered unit basis")
            if sell_reason is not None and (not isinstance(sell_reason, str) or len(sell_reason) > 8192):
                raise ValueError()
            kwargs, scenario = {}, {}
            if basis is not None:
                with localcontext() as context:
                    context.prec = 40  # Match the canonical ledger's unit arithmetic.
                    quantity = Decimal(basis[2]) if fraction == 1 else Decimal(basis[2]) * fraction
                kwargs = {"quantity": quantity, "expected_campaign_hash": basis[1],
                          "expected_normalized_units": basis[2]}
                scenario = json.loads(basis[3])
            _, company_name, current_scenario = self._row_state(ticker)
            if basis is None:
                scenario = current_scenario
            facts = {"price": str(Decimal(str(price))), "fraction": str(fraction), "sell_reason": sell_reason,
                "quantity": str(kwargs["quantity"]) if "quantity" in kwargs else None,
                "expected_campaign_hash": kwargs.get("expected_campaign_hash"),
                "expected_normalized_units": str(Decimal(kwargs["expected_normalized_units"])) if "expected_normalized_units" in kwargs else None}
            spec = self._prepare_spec(event, campaign, ticker, "exit", facts, company_name, scenario)
            snapshot = self.ledger.sell(event, campaign, price, self.registration.occurred_at,
                source_hash=self.registration.source_hash, **kwargs)
        except Exception:
            raise EffectsFailure("Strategy exit failed") from None
        return self._finish(snapshot, ticker, holding["symbol"], scenario, spec)

    def record_mark(self, *, ticker, price):
        campaign, event = self._identity(ticker, "mark")
        self._validate_effect_price(ticker, price)
        try:
            state = self.ledger.snapshot(self.registration.book_id)
            holding = next(c for c in state["campaigns"] if c["campaign_id"] == campaign)
            if holding["status"] != "OPEN":
                raise ValueError()
            sql = "SELECT company_name, scenario FROM stock_holdings WHERE ticker=? AND account_key=?"
            if self.runtime.market == "US":
                sql = sql.replace("stock_holdings", "us_stock_holdings")
            rows = self.agent.conn.execute(sql, (ticker, self.runtime.accounts()[0]["account_key"])).fetchall()
            if len(rows) != 1:
                raise ValueError()
            company_name, scenario = rows[0][0], json.loads(rows[0][1])
            spec = self._prepare_spec(event, campaign, ticker, "mark", {"price": str(Decimal(str(price)))},
                company_name, scenario)
            snapshot = self.ledger.mark(event, campaign, price, self.registration.occurred_at,
                source_hash=self.registration.source_hash)
        except Exception:
            raise EffectsFailure("Strategy mark failed") from None
        return self._finish(snapshot, ticker, company_name, scenario, spec)

    def _validate_effect_price(self, ticker, price):
        if self.pipeline_context is not None:
            quote = self.quote(ticker)
            if Decimal(str(price)) != Decimal(str(quote)):
                raise EffectsFailure("Effect price differs from registered source quote")

    def _project(self, snapshot, ticker, company_name, scenario, projection_spec=None):
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
            proof = self._verify_projection_source(projection_spec) if projection_spec is not None else None
            rows = query("SELECT id, scenario, company_name FROM stock_holdings WHERE account_key=? AND ticker=?",
                (account["account_key"], ticker)).fetchall()
            if len(rows) > 1:
                raise ValueError("Duplicate projected slot")
            if rows:
                existing = json.loads(rows[0][1]).get("_strategy_projection", {})
                if (existing.get("campaign_id") != campaign["campaign_id"]
                        or existing.get("book_id") != self.registration.book_id):
                    raise ValueError("Foreign projected slot")
            if campaign["status"] == "CLOSED":
                if rows:
                    scenario = {**json.loads(rows[0][1]), **scenario}
                self._project_closed_history(campaign, account,
                    rows[0][2] if rows else company_name, scenario, projection_spec)
                query("DELETE FROM stock_holdings WHERE account_key=? AND ticker=?",
                    (account["account_key"], ticker))
                return self._save_projection_receipt(projection_spec, proof) if proof else True
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
            return self._save_projection_receipt(projection_spec, proof) if proof else True

    def _save_projection_receipt(self, spec, proof):
        receipt, previous, before_hash = proof
        after_hash = self._row_state(spec["ticker"])[0]
        if previous:
            if previous[0] != receipt["digest"] or previous[1] != after_hash:
                raise EffectsFailure("Projection receipt conflict")
        else:
            self.agent.conn.execute("INSERT INTO prism_isolated_effect_receipts VALUES (?,?,?)",
                (spec["event_id"], receipt["digest"], after_hash))
        return previous is None or before_hash != after_hash

    def _project_closed_history(self, campaign, account, company_name, scenario, spec):
        """Called inside the same arm transaction as holding deletion."""
        with localcontext() as context:
            context.prec = 40
            return self._project_closed_history_precise(campaign, account, company_name, scenario, spec)

    def _project_closed_history_precise(self, campaign, account, company_name, scenario, spec):
        if not spec or spec.get("action") != "exit":
            raise EffectsFailure("Closed history requires its canonical exit identity")
        buys = [leg for leg in campaign["legs"] if leg["side"] == "BUY"]
        sells = [leg for leg in campaign["legs"] if leg["side"] == "SELL"]
        if not sells or sells[-1]["event_id"] != spec["event_id"]:
            raise EffectsFailure("Closed history exit identity mismatch")
        mapping = self.agent.conn.execute("SELECT event_id, history_id, payload_hash FROM prism_strategy_history_projection WHERE campaign_id=?",
            (campaign["campaign_id"],)).fetchone()
        select = "SELECT account_key,account_name,ticker,company_name,buy_price,buy_date,sell_price,sell_date,profit_rate,holding_days,scenario,trigger_type,trigger_mode,sector,exit_kind FROM trading_history WHERE id=?"
        insert = "INSERT INTO trading_history (account_key,account_name,ticker,company_name,buy_price,buy_date,sell_price,sell_date,profit_rate,holding_days,scenario,trigger_type,trigger_mode,sector,exit_kind) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
        if self.runtime.market == "US":
            select, insert = select.replace("trading_history", "us_trading_history"), insert.replace("trading_history", "us_trading_history")
        if mapping:
            stored = self.agent.conn.execute(select, (mapping[1],)).fetchone()
            if (mapping[0] != spec["event_id"] or stored is None
                    or hashlib.sha256(json.dumps(list(stored), sort_keys=True, allow_nan=False).encode()).hexdigest() != mapping[2]):
                raise EffectsFailure("Existing history projection identity conflict")
            return
        bought = sum(Decimal(leg["normalized_units"]) for leg in buys)
        sold = sum(Decimal(leg["normalized_units"]) for leg in sells)
        if not bought or sold != bought:
            raise EffectsFailure("Closed campaign unit accounting does not reconcile")
        buy_price = sum(Decimal(leg["execution_price"]) * Decimal(leg["normalized_units"]) for leg in buys) / bought
        sell_price = sum(Decimal(leg["execution_price"]) * Decimal(leg["normalized_units"]) for leg in sells) / sold
        contribution = Decimal(campaign["realized_contribution"])
        profit_rate = contribution / Decimal(campaign["cumulative_deployed_allocation"]) * 100
        zone = ZoneInfo(self.registration.clock_timezone)
        opened = datetime.fromisoformat(buys[0]["occurred_at"]).astimezone(zone)
        closed = datetime.fromisoformat(sells[-1]["occurred_at"]).astimezone(zone)
        from reentry_cooldown import classify_exit_kind
        reason = spec["facts"].get("sell_reason")
        exit_kind = classify_exit_kind(reason) if reason else None
        scenario = dict(scenario)
        event_sources = []
        for leg in campaign["legs"]:
            receipt = self.ledger.event_receipt(leg["event_id"])
            event_sources.append({"event_id": leg["event_id"], "payload_hash": receipt["digest"],
                "source_hash": receipt["payload"].get("source_hash")})
        scenario["_strategy_projection"] = {**scenario["_strategy_projection"],
            "return_basis": "CAMPAIGN_NET_RETURN_ON_CUMULATIVE_ALLOCATION",
            "one_slot_contribution": str(contribution), "source_event_id": spec["event_id"],
            "price_basis": "NORMALIZED_UNIT_WEIGHTED_EXECUTION_PRICES",
            "deterministic_exit_reason": reason, "model_generated_journal": "NOT_GENERATED",
            "canonical_legs": campaign["legs"], "canonical_event_sources": event_sources,
            "cost_contribution_total": str(sum(Decimal(leg["cost_contribution"]) for leg in buys)
                + sum(Decimal(leg["normalized_units"]) * Decimal(leg["execution_price"]) * Decimal(leg["fee_rate"]) for leg in sells)),
            "cost_basis": "ENTRY_FEES_ONCE_PLUS_EXIT_FEES; SLIPPAGE_IN_EXECUTION_PRICES"}
        values = (account["account_key"], account["name"], campaign["symbol"], company_name,
            float(buy_price), opened.strftime("%Y-%m-%d %H:%M:%S"), float(sell_price),
            closed.strftime("%Y-%m-%d %H:%M:%S"), float(profit_rate), (closed - opened).days,
            json.dumps(scenario, allow_nan=False), scenario.get("trigger_type"), scenario.get("trigger_mode"),
            scenario.get("sector"), exit_kind)
        cursor = self.agent.conn.execute(insert, values)
        digest = hashlib.sha256(json.dumps(list(values), sort_keys=True, allow_nan=False).encode()).hexdigest()
        self.agent.conn.execute("INSERT INTO prism_strategy_history_projection VALUES (?,?,?,?)",
            (spec["event_id"], campaign["campaign_id"], cursor.lastrowid, digest))
