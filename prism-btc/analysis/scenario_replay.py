"""Isolated LLM scenario replay through the actual runtime; NEVER real orders.

Fresh mode permits only a configured loopback OAuth connection. Frozen mode
permits no network. All exchange calls target a concrete local simulator.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
import hashlib
import json
import math
from pathlib import Path
import socket
import sqlite3
import sys
import time
import uuid
import threading
from types import SimpleNamespace
from urllib.parse import urlsplit
import pandas as pd
import numpy as np

from analysis.scenario_dataset import load_bundle
from backtest.scenario_exchange import OfflineBybitSession
from backtest.scenario_tape import DecisionTape,TapeMismatch,digest
from backtest.intrabar_broker import Config
from live.scenario_broker import ScenarioDemoBroker
from live.scenario_control import _write as write_control
from live.scenario_runtime import ScenarioRuntime
from live.scenario_preview import response_contract
from live.scenario_llm import propose,SYSTEM_PROMPT,FLAT_ENTRY_PROMPT,MA_STRUCTURE_PROMPT,ScenarioModelError
from live import scenario_llm
from core.scenario_limit_prices import POLICY_VERSION
from analysis.scenario_calculated_replay import (
    ECONOMIC_PROFILES, REVIEW_PROFILES, RESEARCH_PROFILES, REJECTION_PROFILES,
)
from live.shared_entry_coordinator import mutation_lock

ROOT=Path(__file__).resolve().parents[2]
POLICY_PROFILES = {
    "current-live-v2": {"recovery_enabled": True, "automatic_normalization_enabled": True},
    "legacy-no-recovery": {"recovery_enabled": False, "automatic_normalization_enabled": False},
    "single-pass-economic-v4": {"recovery_enabled": True, "automatic_normalization_enabled": True},
    "calculated-economic-v4": {"recovery_enabled": True, "automatic_normalization_enabled": True},
    "allocation-single-economic-v1": {"recovery_enabled": True, "automatic_normalization_enabled": True},
    "allocation-calculated-economic-v1": {"recovery_enabled": True, "automatic_normalization_enabled": True},
    "economic-reject-single-v1": {"recovery_enabled": True, "automatic_normalization_enabled": True},
    "economic-reject-calculated-v1": {"recovery_enabled": True, "automatic_normalization_enabled": True},
    "economic-reject-goal-v2": {"recovery_enabled": True, "automatic_normalization_enabled": True,
        "entry_contract_enabled": True, "entry_goal_enabled": True, "pending_renewal_enabled": True,
        "convergence_entry_enabled": True},
}
_identity_scope_lock = threading.Lock()
INPUT_PREPARATION_CODES = {"snapshot_unavailable", "snapshot_stale", "invalid_contract_context",
                           "invalid_input", "input_size"}
RUNTIME_EVENT_QUERIES = {
    "llm_scenario_entry_contracts": "SELECT * FROM llm_scenario_entry_contracts ORDER BY rowid",
    "llm_scenario_entry_failures": "SELECT * FROM llm_scenario_entry_failures ORDER BY rowid",
    "llm_scenario_audit_events": "SELECT * FROM llm_scenario_audit_events ORDER BY rowid",
    "llm_scenario_audit_manifests": "SELECT * FROM llm_scenario_audit_manifests ORDER BY rowid",
    "llm_scenario_broker_evidence": "SELECT * FROM llm_scenario_broker_evidence ORDER BY rowid",
    "llm_scenario_broker_incidents": "SELECT * FROM llm_scenario_broker_incidents ORDER BY rowid",
    "llm_scenario_broker_notices": "SELECT * FROM llm_scenario_broker_notices ORDER BY rowid",
    "llm_scenario_children": "SELECT * FROM llm_scenario_children ORDER BY rowid",
    "llm_scenario_control": "SELECT * FROM llm_scenario_control ORDER BY rowid",
    "llm_scenario_daily_accounting": "SELECT * FROM llm_scenario_daily_accounting ORDER BY rowid",
    "llm_scenario_daily_accounting_history": "SELECT * FROM llm_scenario_daily_accounting_history ORDER BY rowid",
    "llm_scenario_decisions": "SELECT * FROM llm_scenario_decisions ORDER BY rowid",
    "llm_scenario_execution_batches": "SELECT * FROM llm_scenario_execution_batches ORDER BY rowid",
    "llm_scenario_exit_basis": "SELECT * FROM llm_scenario_exit_basis ORDER BY rowid",
    "llm_scenario_intents": "SELECT * FROM llm_scenario_intents ORDER BY rowid",
    "llm_scenario_model_incident": "SELECT * FROM llm_scenario_model_incident ORDER BY rowid",
    "llm_scenario_notice_errors": "SELECT * FROM llm_scenario_notice_errors ORDER BY rowid",
    "llm_scenario_notice_positions": "SELECT * FROM llm_scenario_notice_positions ORDER BY rowid",
    "llm_scenario_outbox": "SELECT * FROM llm_scenario_outbox ORDER BY rowid",
    "llm_scenario_pending_leases": "SELECT * FROM llm_scenario_pending_leases ORDER BY rowid",
    "llm_scenario_pending_renewals": "SELECT * FROM llm_scenario_pending_renewals ORDER BY rowid",
    "llm_scenario_recovery_events": "SELECT * FROM llm_scenario_recovery_events ORDER BY rowid",
    "llm_scenario_reductions": "SELECT * FROM llm_scenario_reductions ORDER BY rowid",
    "llm_scenario_review_events": "SELECT * FROM llm_scenario_review_events ORDER BY rowid",
    "llm_scenario_review_tapes": "SELECT * FROM llm_scenario_review_tapes ORDER BY rowid",
    "llm_scenario_runner_incident": "SELECT * FROM llm_scenario_runner_incident ORDER BY rowid",
    "llm_scenario_settlements": "SELECT * FROM llm_scenario_settlements ORDER BY rowid",
    "llm_scenario_slots": "SELECT * FROM llm_scenario_slots ORDER BY rowid",
    "llm_scenario_state": "SELECT * FROM llm_scenario_state ORDER BY rowid",
}


def _runtime_event_hash(conn):
    from live.scenario_review_storage import review_text
    from utils.btc_review_event_references import read_event
    from utils.btc_review_references import read_tape
    from utils.btc_financial_references import restore as restore_financial
    from utils.btc_audit_references import read_audit
    from utils.btc_accounting_audit_references import read_accounting
    from utils.btc_recovery_references import read_recovery
    event_tables = {}
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'llm_scenario%' ORDER BY name"):
        # Schema completeness is part of the hash contract, not optional coverage.
        if name not in RUNTIME_EVENT_QUERIES:
            raise TapeMismatch("unknown_runtime_event_table")
        cursor = conn.execute(RUNTIME_EVENT_QUERIES[name])
        columns = [field[0] for field in cursor.description]
        rows = cursor.fetchall()
        if name in ('llm_scenario_review_events', 'llm_scenario_review_tapes') and 'body' in columns:
            index = columns.index('body')
            slot_index = columns.index('slot')
            sequence_index = columns.index('sequence') if name == 'llm_scenario_review_events' else None
            rows = [tuple((read_tape(value, conn, row[slot_index], decode_legacy=review_text)
                           if sequence_index is None else read_event(value, conn, row[slot_index], row[sequence_index],
                                                                     decode_legacy=review_text)) if i == index else value
                          for i, value in enumerate(row)) for row in rows]
        if name == 'llm_scenario_recovery_events' and 'body' in columns:
            index = columns.index('body')
            slot_index = columns.index('slot') if 'slot' in columns else None
            kind_index = columns.index('kind') if 'kind' in columns else None
            rows = [tuple((read_recovery(value, conn, row[slot_index] if slot_index is not None else None,
                                         decode_legacy=review_text,
                                         expected_kind=row[kind_index] if kind_index is not None else None)
                           if kind_index is None or row[kind_index] in ('MODEL_REQUEST', 'MODEL_RAW', 'INPUT') else review_text(value))
                          if i == index else value
                          for i, value in enumerate(row)) for row in rows]
        if name == 'llm_scenario_broker_evidence' and {'body', 'kind'} <= set(columns):
            index = columns.index('body')
            kind_index = columns.index('kind')
            rows = [tuple(restore_financial(value) if i == index and row[kind_index] == 'financial' else value
                          for i, value in enumerate(row)) for row in rows]
        if name == 'llm_scenario_audit_events' and {'body', 'kind', 'decision_slot'} <= set(columns):
            index, kind_index, slot_index = (columns.index(k) for k in ('body', 'kind', 'decision_slot'))
            rows = [tuple((read_audit(value, conn, row[slot_index]) if row[kind_index] == 'decision_input'
                           else read_accounting(value, conn) if row[kind_index] == 'accounting_observation' else value)
                          if i == index else value
                          for i, value in enumerate(row)) for row in rows]
        event_tables[name] = dict(columns=columns, rows=rows)
    return digest(event_tables)


def _replay_uuid_factory(name):
    counts = Counter()

    def deterministic_uuid():
        caller = sys._getframe(1).f_code.co_name
        counts[caller] += 1
        return uuid.uuid5(uuid.NAMESPACE_URL, f"prism-isolated-replay:{name}:{caller}:{counts[caller]}")

    return deterministic_uuid


@contextmanager
def replay_identity_scope(*, economic=False, stable_process_id=False):
    """Replay-only IDs, not authority: restore module adapters even after failure.

    Namespace counters by call site so extra audit events cannot alter permit IDs.
    Never patch the shared uuid module or reuse an existing/production database.
    Concurrent replay in one interpreter is deliberately unsupported. Call only
    in an isolated replay process, never alongside a running trading worker.
    """
    if not _identity_scope_lock.acquire(blocking=False):
        raise ValueError("concurrent_replay_identity_scope_forbidden")
    originals = []
    try:
        from live import scenario_runtime, scenario_recovery
        modules = [scenario_runtime, scenario_recovery]
        if economic:
            from live import scenario_execution
            modules.append(scenario_execution)
        if stable_process_id:
            if not economic:
                raise ValueError("process_identity_requires_economic_replay")
            originals.append((scenario_execution, "os", scenario_execution.os))
            scenario_execution.os = SimpleNamespace(getpid=lambda: 0)
        for module in modules:
            originals.append((module, "uuid", module.uuid))
            module.uuid = SimpleNamespace(uuid4=_replay_uuid_factory(module.__name__))
        yield
    finally:
        for module, name, original in reversed(originals):
            setattr(module, name, original)
        _identity_scope_lock.release()


@contextmanager
def network_boundary(mode,endpoint=None):
    class AuditLog(list):
        pass
    active=[True];attempts=AuditLog()
    url=urlsplit(endpoint or "")
    allowed=(url.hostname,url.port or 80) if mode=="fresh" else None
    if mode=="fresh" and (url.scheme!="http" or url.hostname!="127.0.0.1" or url.path!="/v1/responses"):
        raise ValueError("explicit_loopback_oauth_endpoint_required")
    def audit(event,args):
        if not active[0]:return
        target=None
        if event=="socket.getaddrinfo":target=(str(args[0]),args[1])
        elif event in {"socket.connect","socket.sendto"} and getattr(args[0],"family",None) in {socket.AF_INET,socket.AF_INET6}:
            address=args[1] if event=="socket.connect" else args[-1]
            target=(str(address[0]),address[1])
        if target is not None and target!=allowed:
            attempts.append("blocked_network")
            raise RuntimeError("backtest_network_forbidden")
    sys.addaudithook(audit)
    attempts.guard=audit  # Allows a pure policy test without a real socket attempt.
    try:yield attempts
    finally:active[0]=False


def contract_for(bundle,path,cost_multiplier,initial_equity,origin="luna",policy_profile="current-live-v2",
                 initial_state="fresh-unblocked", *, observed_manifest=None,
                 market_input_mode="historical", allow_input_gaps=False):
    if policy_profile not in POLICY_PROFILES:
        raise ValueError("explicit_replay_policy_profile_required")
    if initial_state not in {"fresh-unblocked","recovery-stage0"}:
        raise ValueError("explicit_replay_initial_state_required")
    if initial_state=="recovery-stage0" and policy_profile=="legacy-no-recovery":
        raise ValueError("recovery_seed_requires_current_live_v2")
    if observed_manifest is None and market_input_mode != "historical":
        raise ValueError("observed_input_contract_required")
    if observed_manifest is not None and market_input_mode not in {"observed", "reconstructed_at_observed_schedule"}:
        raise ValueError("explicit_observed_input_mode_required")
    package=ROOT/"prism-btc"
    sources={str(p.relative_to(package)):hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(package.rglob("*.py")) if "tests" not in p.relative_to(package).parts}
    contract = dict(schema=2,kind="SIMULATED_LLM_SCENARIO",decision_origin=origin,data_hash=bundle["data_hash"],
        policy_profile=policy_profile,runtime_flags=dict(POLICY_PROFILES[policy_profile]),
        initial_state=dict(profile=initial_state,
            provenance="SYNTHETIC_INITIAL_SOFT_LATCH_NO_PRIOR_PNL" if initial_state=="recovery-stage0" else "FRESH_UNBLOCKED_NO_SYNTHETIC_LOSSES",
            live_ledger_imported=False,prior_pnl_imported=False,prior_settlements_imported=False),
        identity_scheme="REPLAY_ONLY_UUID5_MODULE_CALLSITE_SEQUENCE_V1",
        context_contracts=dict(review_contract_version=1,conditional_entry_version=1,
                              execution_price_policy=POLICY_VERSION,ma_structure_version=1),
        assembled_prompt_hash_basis="sha256_utf8",
        assembled_prompt_hashes={"active":hashlib.sha256((SYSTEM_PROMPT+MA_STRUCTURE_PROMPT).encode()).hexdigest(),
                                 "flat":hashlib.sha256((SYSTEM_PROMPT+MA_STRUCTURE_PROMPT+FLAT_ENTRY_PROMPT).encode()).hexdigest()},
        request_audit="EXACT_ASSEMBLED_REQUEST_IN_TAPE_RAW_ENVELOPE;FIXTURE_EXPLICITLY_SYNTHETIC",
        start_ms=bundle["start_ms"],end_ms=bundle["end_ms"],path=path,initial_equity=initial_equity,
        model=scenario_llm.MODEL,effort=scenario_llm.EFFORT,tier="fast",prompt_hash=hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest(),
        source_hashes=sources,runtime_versions={"python":sys.version.split()[0],"pandas":pd.__version__,"numpy":np.__version__},
        costs=dict(maker_fee=.0002*cost_multiplier,taker_fee=.00055*cost_multiplier,
                   slippage=.0005*cost_multiplier,spread=.0001,participation=.01),
        latencies=dict(entry=5000,market=5000,cancel=2000,amend=2000,model="measured_and_taped_ms"),
        decision_interval_ms=300000,protection_interval_ms=60000,
        terminal_policy="NO_FORCED_CLOSE_MARK_OPEN_POSITION",liquidity="previous_minute_volume_shared_across_four_path_points")
    if policy_profile == "economic-reject-goal-v2":
        contract["context_contracts"].update(entry_contract_version=2, pending_renewal_version=1, entry_convergence_version=1)
    if policy_profile in ECONOMIC_PROFILES:
        import inspect
        from live.scenario_contract import response_schema
        contract.update(schema=4,
            economic_clock_contract="NATIVE_MONOTONIC_INTEGER_MS_FULL_TICK_V1",
            identity_scheme="EXACT_REPLAY_UUID5_MODULE_CALLSITE_SEQUENCE_V1",
            review_binding=None,
            phase_contracts={"profile":policy_profile,
                "schema_builder_sha256":hashlib.sha256(inspect.getsource(response_schema).encode()).hexdigest(),
                "SINGLE_PASS":hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()})
        if policy_profile == "economic-reject-goal-v2":
            contract["identity_scheme"] = "EXACT_REPLAY_UUID5_CALLSITE_PID0_V2"
        if policy_profile in REVIEW_PROFILES:
            from live.scenario_calculated_review import DRAFT_PROMPT, SELECT_PROMPT, research_review
            from live.scenario_provenance import review_policy_binding
            contract["review_binding"] = review_policy_binding()
            contract["context_contracts"]["calculated_review_version"] = 1
            contract["phase_contracts"] = dict(profile=policy_profile,
                DRAFT=hashlib.sha256(DRAFT_PROMPT.encode()).hexdigest(),
                SELECT=hashlib.sha256(SELECT_PROMPT.encode()).hexdigest(),
                schema_builder_sha256=hashlib.sha256(inspect.getsource(research_review).encode()).hexdigest(),
                wire_schema_builder_sha256=contract["phase_contracts"]["schema_builder_sha256"],
                actual_phase_schema_binding="EXACT_GENERATE_REQUEST_RESPONSE_SCHEMA_IN_FULL_INPUT_TAPE")
            if policy_profile in RESEARCH_PROFILES:
                from live.scenario_calculated_review import (
                    allocation_response_schema, allocation_system_prompt, ALLOCATION_SINGLE_PROMPT,
                )
                research_mode = RESEARCH_PROFILES[policy_profile]
                single = research_mode in {"allocation-single", "economic-reject-single-v1"}
                contract["review_binding"] = review_policy_binding(research_mode=research_mode)
                allocation_draft = DRAFT_PROMPT.replace("SL, TP, quantities and fractions yourself",
                    "SL, TP, confidence and entry allocation_fraction yourself") + ALLOCATION_SINGLE_PROMPT
                phase_prompts = ({"SINGLE": ALLOCATION_SINGLE_PROMPT} if single
                                 else {"DRAFT": allocation_draft, "SELECT": SELECT_PROMPT})
                if policy_profile == "economic-reject-single-v1":
                    phase_prompts["SINGLE"] += "\nReturn exactly the root object {proposal: <action-specific allocation proposal>}. Follow its action branch; inactive fields must be null or empty as specified.\n"
                contract["phase_contracts"] = dict(profile=policy_profile, research_mode=research_mode,
                    max_model_calls=1 if single else 2,
                    **{key: hashlib.sha256(value.encode()).hexdigest() for key, value in phase_prompts.items()},
                    base_transform_sha256=hashlib.sha256(inspect.getsource(allocation_system_prompt).encode()).hexdigest(),
                    schema_builder_sha256=hashlib.sha256(inspect.getsource(research_review).encode()).hexdigest(),
                    wire_schema_builder_sha256=hashlib.sha256(inspect.getsource(allocation_response_schema).encode()).hexdigest(),
                    actual_phase_schema_binding="EXACT_GENERATE_REQUEST_RESPONSE_SCHEMA_IN_FULL_INPUT_TAPE")
                if policy_profile in REJECTION_PROFILES:
                    from live.scenario_calculated_review import action_allocation_response_schema
                    contract["phase_contracts"].update(
                        rejection_policy="preauthority-economic-reject-v1",
                        rejection_execution="NO_AUTHORITY_NO_RETRY_NEXT_PLANNED_TICK",
                        model_error_policy="STOP_FIRST_ERROR",
                        wire_schema_builder_sha256=hashlib.sha256(
                            inspect.getsource(action_allocation_response_schema).encode()).hexdigest())
        contract["observed_data"] = dict(mode=market_input_mode, provider_manifest=observed_manifest,
            allow_input_gaps=allow_input_gaps, clock="OBSERVED_AVAILABILITY_BEFORE_SIMULATED_CONTEXT",
            account_source="INDEPENDENT_SIMULATED_ACCOUNT_ONLY")
    elif observed_manifest is not None:
        contract.update(schema=3, market_input=dict(mode=market_input_mode,
            provider_manifest=observed_manifest, allow_input_gaps=allow_input_gaps,
            clock="OBSERVED_AVAILABILITY_BEFORE_SIMULATED_CONTEXT",
            account_source="INDEPENDENT_SIMULATED_ACCOUNT_ONLY"),
            replay_scope="HYBRID_OBSERVED_INPUT_ASSUMED_EXECUTION")
    return contract


class Driver:
    def __init__(self,bundle,market,session,path,broker,conn):
        self.session,self.broker,self.conn=session,broker,conn
        self.events=[];self.index=0;self.capacity={};self.curve=[];self.protection=Counter()
        self.next_protect=bundle["start_ms"]+15000
        self.end=bundle["end_ms"]
        self._advance_depth = 0
        self.strict_protection = False
        keys=("open","high","low","close") if path=="OHLC" else ("open","low","high","close")
        funding_times={f["timestamp"] for f in bundle["funding"]}
        for i,(when,bar) in enumerate(market.source.iterrows()):
            start=when.value//1_000_000
            if not bundle["start_ms"]<=start<self.end:continue
            mark=market.mark.loc[when]
            if not i or market.source.index[i-1].value//1_000_000 != start-60000:
                raise ValueError('replay_previous_minute_volume_missing')
            try:
                previous_volume = float(market.source.iloc[i-1].volume)
            except (TypeError, ValueError, OverflowError):
                raise ValueError('replay_previous_minute_volume_invalid') from None
            if not math.isfinite(previous_volume) or previous_volume < 0:
                raise ValueError('replay_previous_minute_volume_invalid')
            cap=int(previous_volume*.01/.001)
            self.capacity[start]=cap
            if start in funding_times:
                previous=float(market.source.iloc[i-1].close) if i else float(bar.open)
                previous_mark=float(market.mark.loc[market.source.index[i-1]].close) if i else float(mark.open)
                self.events.append((start,previous,previous_mark,None))
            for offset,key in zip((1,20000,40000,59999),keys):
                self.events.append((start+offset,float(bar[key]),float(mark[key]),start))
        if set(self.capacity) != set(range(bundle["start_ms"], self.end, 60000)):
            raise ValueError('replay_minute_window_incomplete')
        self.events.sort()

    def _value(self):
        value=float(self.session.equity)
        if not math.isfinite(value) or value<=0:
            raise ValueError("simulated_insolvency")
        self.curve.append((self.session.ts_ms,value))

    def advance_to(self,target):
        if self._advance_depth:
            raise ValueError("replay_advance_recursion")
        self._advance_depth += 1
        try:
            return self._advance_to(target)
        finally:
            self._advance_depth -= 1

    def _advance_to(self,target):
        target=min(int(target),self.end)
        if target<self.session.ts_ms:
            raise ValueError("replay_clock_reversal")
        while True:
            quote=self.events[self.index][0] if self.index<len(self.events) else self.end+1
            protect=self.next_protect if self.next_protect<self.end else self.end+1
            at=min(quote,protect)
            if at>target:break
            if quote<=protect:
                ts,price,mark,bucket=self.events[self.index];self.index+=1
                if bucket is None:self.session.funding_price_proxy_used=True
                available=self.capacity[bucket] if bucket is not None else 0
                used=self.session.advance(ts,price,mark,available)
                if bucket is not None:self.capacity[bucket]-=used
                self._value()
            else:
                self.session.elapse(at);self.next_protect+=60000
                try:
                    with mutation_lock(self.conn):
                        evidence=self.broker.reconcile()
                    self.protection["confirmed" if evidence.get("protection_confirmed") else "unconfirmed"]+=1
                except Exception:
                    self.protection["failed"]+=1
                    if self.strict_protection:
                        raise ValueError("economic_protection_failed") from None
        self.session.elapse(target)
        self._value()


def _single_pass_audit_rows(conn, slot):
    from live.scenario_review_storage import review_text
    from utils.btc_recovery_references import read_recovery
    rows = conn.execute("SELECT kind,body FROM llm_scenario_recovery_events WHERE slot=? AND kind IN ('MODEL_REQUEST','MODEL_RAW') ORDER BY rowid", (slot,)).fetchall()
    rows = [(kind, read_recovery(body, conn, slot, decode_legacy=review_text, expected_kind=kind)) for kind, body in rows]
    groups = {kind: [body for row_kind, body in rows if row_kind == kind]
              for kind in ('MODEL_REQUEST', 'MODEL_RAW')}
    if (any(len(group) > 1 for group in groups.values())
            or any('calculated_review_version' in json.loads(body) or 'phase' in json.loads(body)
                   for _, body in rows)):
        raise TapeMismatch('multi_phase_review_requires_exact_review_tape')
    return tuple((groups[kind][0],) if groups[kind] else None for kind in ('MODEL_REQUEST', 'MODEL_RAW'))


class Decisions:
    def __init__(self,tape,driver,mode,*,policy=None,model_generator=None,fixed_latency_ms=2000):
        self.tape,self.driver,self.mode=tape,driver,mode
        self.policy,self.model_generator,self.fixed_latency_ms=policy,model_generator,fixed_latency_ms
        self.fatal=None;self.calls=0;self.model_calls=0;self.failures=0;self.input_failures=0
        from live.scenario_recovery import ensure_schema
        ensure_schema(driver.conn)
        driver.conn.commit()

    def __call__(self,snapshot,context):
        from live.scenario_recovery import capture_wire
        # Audit capture is local even for the explicit legacy runtime profile.
        # It does not activate recovery or change its authorization flags.
        with capture_wire(self.driver.conn,int(context["now"]//300),self.driver.session.clock):
            return self._call(snapshot,context)

    def _call(self,snapshot,context):
        from live import scenario_recovery
        from live.scenario_recovery import record_model_request, record_model_wire
        at=self.driver.session.ts_ms
        if self.mode=="frozen":
            try:record=self.tape.lookup(snapshot,context)
            except TapeMismatch as exc:
                self.fatal=str(exc);raise
            # propose() loads the wire contract lazily, after its snapshot checks.
            # Reproduce that lifecycle, not its model call: the next recovery
            # context attests which modules are loaded. Preloading before lookup
            # changes the first context; skipping it leaves later ones UNKNOWN.
            if (self.tape.contract["decision_origin"] == "luna" and record["error"] not in {
                    "ScenarioModelError:snapshot_unavailable", "ScenarioModelError:snapshot_stale"}):
                from live import scenario_contract  # noqa: F401
            audit=record.get("raw_response")
            if isinstance(audit,dict) and any(key in audit for key in ('calculated_review_version','phase','operations')):
                self.fatal='multi_phase_review_requires_exact_review_tape'
                raise TapeMismatch(self.fatal)
            if isinstance(audit,dict) and audit.get("request"):
                if scenario_recovery.digest(audit["request"]) != audit.get("request_hash"):
                    self.fatal="tape_request_audit_mismatch"
                    raise TapeMismatch(self.fatal)
                record_model_request(**audit["request"])
            if isinstance(audit,dict) and audit.get("wire_recorded"):
                record_model_wire(audit["text"],**audit["wire_metadata"])
            self.driver.advance_to(at+record["latency_ms"])
            if record["error"]:
                self.failures+=1
                if record["error"].removeprefix("ScenarioModelError:") in INPUT_PREPARATION_CODES:
                    self.input_failures+=1
                if record["error"].startswith("ScenarioModelError:"):
                    raise ScenarioModelError(record["error"].split(":",1)[1])
                raise ValueError("recorded_model_failure")
            return record["proposal"]
        self.calls+=1;response=None;error=None;raw=[];request=None
        started=time.monotonic()
        try:
            if self.policy is not None:
                request=dict(system_prompt="SYNTHETIC_FIXTURE_POLICY_NOT_LLM",
                             user_prompt=json.dumps({"snapshot":snapshot,"context":context},sort_keys=True),
                             response_schema={},model="fixture",effort=None,fast=None)
                record_model_request(**request)
                response=self.policy(snapshot,context)
                raw.append(json.dumps(response,sort_keys=True))
                record_model_wire(raw[0],model="fixture")
            else:
                from live.scenario_oauth import generate_scenario
                generate=self.model_generator or generate_scenario
                def retain(**kwargs):
                    self.model_calls+=1
                    result=generate(**kwargs);raw.append(result.text);return result
                response=propose(snapshot,context,response_contract(context),generate=retain,clock=self.driver.session.clock)
        except ScenarioModelError as exc:
            # Preserve only runtime classification, never arbitrary exception text.
            safe_codes = {"snapshot_unavailable", "snapshot_stale", "invalid_contract_context",
                          "invalid_input", "input_size", "oauth_model_failed", "late_response",
                          "response_size", "invalid_json", "json_object_required",
                          "response_contract_mismatch", "response_unknown_fields",
                          "response_missing_scenario_id", "response_revision_mismatch",
                          "response_identity_mismatch", "response_contract_inactive_protection",
                          "response_contract_inactive_orders", "response_contract_missing_protection"}
            category = str(exc) if str(exc) in safe_codes else "response_contract_mismatch"
            error = "ScenarioModelError:" + category
        except Exception as exc:
            error=type(exc).__name__
        latency=self.fixed_latency_ms if self.policy is not None else max(1,int((time.monotonic()-started)*1000))
        if latency>75000:error="ScenarioModelError:late_response"
        slot=int(context["now"]//300)
        try:
            request_row,wire_row=_single_pass_audit_rows(self.driver.conn,slot)
        except TapeMismatch as exc:
            self.fatal=str(exc)
            raise
        request=None;request_hash=None
        if request_row:
            body=json.loads(request_row[0])
            request={key:body[key] for key in ("system_prompt","user_prompt","response_schema",
                                               "model","effort","fast","input_projection") if key in body}
            request_hash=body.get("request_hash")
            if scenario_recovery.digest(request)!=request_hash:
                self.fatal="model_request_audit_mismatch"
                raise TapeMismatch(self.fatal)
        wire=json.loads(wire_row[0]) if wire_row else None
        self.tape.append(snapshot,context,response,latency,error=error,
                         raw_response={"request":request,"request_hash":request_hash,
                                       "text":raw[0] if raw else None,"wire_recorded":wire is not None,
                                       "wire_metadata":{key:wire[key] for key in ("model","effort","fast")} if wire else None})
        self.driver.advance_to(at+latency)
        if error:
            self.failures+=1
            if error.removeprefix("ScenarioModelError:") in INPUT_PREPARATION_CODES:
                self.input_failures+=1
            if error.startswith("ScenarioModelError:"):
                raise ScenarioModelError(error.split(":",1)[1])
            raise ValueError("model_proposal_failed")
        return response


def run_replay(bundle,market,output,**kwargs):
    if str(ROOT) not in sys.path:
        sys.path.append(str(ROOT))
    from observability.events import suppress_events
    with suppress_events(), replay_identity_scope(economic=kwargs.get("policy_profile", "") in ECONOMIC_PROFILES,
                               stable_process_id=kwargs.get("policy_profile") == "economic-reject-goal-v2"):
        return _run_replay(bundle,market,output,**kwargs)


def _run_replay(bundle,market,output,*,mode,tape_path,path="OHLC",cost_multiplier=1.,
               initial_equity=10000.,policy=None,model_generator=None,max_decisions=288,
               policy_profile="current-live-v2",initial_state="fresh-unblocked",clock_monotonic=None,
               observed_packet=None,observed_packet_sha256=None,market_input_mode="historical",
               allow_input_gaps=False,before_http=None,failure_callback=None):
    """No production paths accepted; fresh DB per run and explicit data provenance."""
    if mode not in {"fresh","frozen","fixture"} or path not in {"OHLC","OLHC"}:
        raise ValueError("explicit_replay_mode_required")
    if mode=="fixture" and policy is None and not (policy_profile in ECONOMIC_PROFILES and model_generator is not None):
        raise ValueError("fixture_policy_required")
    if mode=="fixture" and policy_profile in REVIEW_PROFILES and model_generator is None:
        raise ValueError("calculated_fixture_generator_required")
    if mode!="fixture" and policy is not None:raise ValueError("fixture_must_not_be_labeled_llm")
    if type(max_decisions) is not int or not 1<=max_decisions<=288:
        raise ValueError("bounded_decision_count_required")
    if not market.manifest()["mark"]["complete"]:
        raise ValueError("complete_mark_history_required")
    if bundle["source_interval_ms"]!=60000 or bundle["symbol"]!="BTCUSDT":
        raise ValueError("minute_btc_bundle_required")
    if not 0<cost_multiplier<=3 or not math.isfinite(initial_equity) or initial_equity<=0:
        raise ValueError("invalid_replay_cost_or_equity")
    observed = None
    schedule = None
    if market_input_mode == "historical":
        if observed_packet is not None or observed_packet_sha256 is not None or allow_input_gaps:
            raise ValueError("explicit_observed_input_mode_required")
    elif market_input_mode in {"observed", "reconstructed_at_observed_schedule"}:
        if observed_packet is None or not observed_packet_sha256:
            raise ValueError("observed_packet_and_external_pin_required")
        from backtest.observed_scenario_data import ObservedScenarioData
        observed = ObservedScenarioData(observed_packet,observed_packet_sha256,allow_gaps=allow_input_gaps)
        schedule = observed.schedule(bundle["start_ms"],bundle["end_ms"])
        for item in schedule:
            if item["status"] == "AVAILABLE":
                observed.snapshot(item["available_at_ms"])
                if market_input_mode == "reconstructed_at_observed_schedule":
                    captured = market.snapshot(item["slot"]*300000)
                    if (not captured.get("valid") or any(
                        item["available_at_ms"]-captured["timeframes"][tf]["forming"]["observed_at_ms"] > 120000
                        for tf in ("15m","30m","1h"))):
                        raise ValueError("reconstructed_snapshot_stale_at_observed_schedule")
    else:
        raise ValueError("explicit_observed_input_mode_required")
    origin="fixture" if mode=="fixture" else "luna"
    if mode=="frozen":
        header=json.loads(Path(tape_path).read_text().splitlines()[0])
        if header["contract"].get("schema") != (4 if policy_profile in ECONOMIC_PROFILES else 3 if observed is not None else 2):
            raise TapeMismatch("legacy_tape_requires_original_code; record_explicit_legacy-no-recovery_profile_for_new_comparison")
        origin=header["contract"]["decision_origin"]
        if origin not in {"fixture","luna"}:raise ValueError("unknown_decision_origin")
    contract=contract_for(bundle,path,cost_multiplier,initial_equity,origin,policy_profile,initial_state,
        observed_manifest=observed.manifest() if observed is not None else None,
        market_input_mode=market_input_mode,allow_input_gaps=allow_input_gaps)
    output=Path(output).resolve()
    if output.exists():raise ValueError("never_overwrite_replay")
    if any(p in output.parts for p in ("state","logs","trading")):
        raise ValueError("dedicated_research_directory_required")
    output.mkdir(parents=True)
    economic_profile = policy_profile in ECONOMIC_PROFILES
    if economic_profile:
        from analysis.scenario_calculated_replay import EconomicTape, EconomicClock, EconomicDecisions
    tape_type = EconomicTape if economic_profile else DecisionTape
    tape=tape_type(tape_path,"frozen" if mode=="frozen" else "record",contract)
    conn=sqlite3.connect(output/"replay.sqlite")
    from live import tracking
    tracking.ensure_schema(conn)
    write_control(conn,{"version":1,"state":"active","main_uid":"42"})
    prior=market.source[market.source.index.view('i8')//1_000_000+60000<=bundle["start_ms"]].iloc[-1]
    cost=contract["costs"]
    session=OfflineBybitSession(initial_equity=initial_equity,start_ms=bundle["start_ms"]-1,price=float(prior.close),
        funding_events=bundle["funding"],config=Config(path=path,**cost))
    if type(session) is not OfflineBybitSession or session.simulation is not True:
        raise ValueError("offline_exchange_required")
    broker=ScenarioDemoBroker(conn,session=session,expected_main_uid="42",clock=session.clock,execution_enabled=True)
    driver=Driver(bundle,market,session,path,broker,conn)
    driver.strict_protection = economic_profile
    clock = (EconomicClock(driver,tape,monotonic=clock_monotonic or time.monotonic)
             if economic_profile else session.clock)
    provider=(EconomicDecisions(tape,driver,mode,clock,policy=policy,model_generator=model_generator,
                               before_http=before_http)
              if economic_profile else Decisions(tape,driver,mode,policy=policy,model_generator=model_generator))
    def snapshot():
        if observed is None:
            return market.snapshot((session.ts_ms//300000)*300000)
        captured = observed.snapshot(session.ts_ms)
        return captured if market_input_mode == "observed" else market.snapshot((session.ts_ms//300000)*300000)
    runtime=ScenarioRuntime(conn,broker,provider,snapshot,
                            clock=clock,**POLICY_PROFILES[policy_profile],
                            calculated_review_enabled=policy_profile in REVIEW_PROFILES,
                            calculated_review_propose=provider.calculated if policy_profile in REVIEW_PROFILES else None,
                            **({"research_review_mode": RESEARCH_PROFILES[policy_profile]}
                               if policy_profile in RESEARCH_PROFILES else {}))
    if initial_state=="recovery-stage0":
        with mutation_lock(conn):
            state=runtime.state()
            if state!={"version":0,"active":None,"breaker":{}}:
                raise ValueError("recovery_seed_requires_pristine_replay_state")
            # Seed only an explicitly synthetic soft latch. No closed trades,
            # prior-loss counter, PnL, permit or baseline evidence is invented.
            state.update(version=1,breaker={"blocked":True,"reasons":["three_losses"],
                                           "consecutive_losses":0,"completed_ids":[]})
            runtime._save(state)
    statuses=Counter();outcome_reasons=Counter();start=bundle["start_ms"];end=bundle["end_ms"]
    decisions=list(range(start,end,300000))
    stopped = None
    drain_status = "COMPLETE"
    if len(decisions)>max_decisions:raise ValueError("decision_budget_exceeded")
    try:
        for i,at in enumerate(decisions):
            release = at if schedule is None else schedule[i]["available_at_ms"]
            if not at <= release < min(at+300000,end):
                raise ValueError("observed_release_outside_decision_window")
            driver.advance_to(release)
            if schedule is not None and schedule[i]["status"] == "MISSING":
                statuses["INPUT_GAP"] += 1
                outcome_reasons["INPUT_GAP:observed_snapshot_missing"] += 1
                continue
            if economic_profile:
                tape.begin(at);clock.begin()
            try:
                result=runtime.tick()
            except Exception as exc:
                if not economic_profile:
                    raise
                provider.fatal = clock.fatal or type(exc).__name__
                result = dict(status="blocked",reason="economic_tick_failed",
                              failure_code=provider.fatal)
            statuses[result["status"]]+=1
            if economic_profile:
                clock.finish();tape.finish(result)
                if clock.fatal:
                    provider.fatal = clock.fatal
            reason=result.get("failure_code") or result.get("reason")
            if isinstance(reason,str):outcome_reasons[result["status"]+":"+reason]+=1
            if economic_profile:
                technical_reason = provider.fatal
                if (result.get("reason") in {"submission_unknown", "llm_call_failed",
                        "llm_output_contract_failed", "llm_input_preparation_failed"}
                        or result.get("failure_code") in {"review_deadline_failure",
                            "review_contract_failure", "review_tape_failure"}
                        or (result.get("reason") == "reconciliation_or_proposal_failed"
                            and result.get("failure_code") != "fresh_safety_rejection")
                        or result.get("status") == "lock_busy"):
                    technical_reason = technical_reason or result.get("failure_code") or result.get("reason")
                if technical_reason:
                    stopped = dict(status="STOPPED_TECHNICAL_FAILURE", reason=technical_reason,
                        slot_ms=at, economic_time_ms=session.ts_ms, profile=policy_profile,
                        remaining_decision_slots=len(decisions)-i-1,
                        protect_only_end_ms=end, new_model_calls_forbidden=True)
                    if failure_callback is not None:
                        failure_callback(dict(stopped))
                    break
            elif provider.fatal:
                raise TapeMismatch(provider.fatal)
            if (i+1)%12==0:print(json.dumps({"progress":i+1,"total":len(decisions),"status":result["status"]}),flush=True)
        try:
            driver.advance_to(end)
            with mutation_lock(conn):runtime._reconcile(runtime.state())
        except Exception:
            if not economic_profile:
                raise
            drain_status = "INCOMPLETE_PROTECTION_OR_SETTLEMENT"
            if stopped is None:
                stopped = dict(status="STOPPED_TECHNICAL_FAILURE",reason="economic_terminal_drain_failed",
                    slot_ms=None,economic_time_ms=session.ts_ms,profile=policy_profile,
                    remaining_decision_slots=0,protect_only_end_ms=end,new_model_calls_forbidden=True)
                if failure_callback is not None:
                    failure_callback(dict(stopped))
        tape.assert_exhausted()
        settlements=[json.loads(r[0]) for r in conn.execute("SELECT evidence FROM llm_scenario_settlements ORDER BY rowid")]
        completed=[r for r in settlements if r.get("execution_ids")]
        budgets={}
        for scenario_id,payload in conn.execute("SELECT scenario_id,payload FROM llm_scenario_intents ORDER BY rowid"):
            plan=json.loads(payload)
            if plan["action"]=="OPEN":budgets.setdefault(scenario_id,plan["risk"]["budget"])
        equity=[initial_equity]+[p[1] for p in driver.curve]
        peak=equity[0];drawdown=0.
        for value in equity:
            peak=max(peak,value);drawdown=max(drawdown,(peak-value)/peak)
        fees=sum(float(e["execFee"]) for e in session.executions)
        funding=sum(float(t.get("funding",0)) for t in session.transactions)
        state=runtime.state();net=[r["net_pnl"] for r in completed]
        economic=dict(final_equity=float(session.equity),net_change=float(session.equity)-initial_equity,
            return_pct=(float(session.equity)/initial_equity-1)*100,max_drawdown_pct=drawdown*100,
            fees=fees,funding_net=funding,completed_scenarios=len(completed),
            wins=sum(x>0 for x in net),losses=sum(x<0 for x in net),
            win_rate=sum(x>0 for x in net)/len(net) if net else None,
            profit_factor=sum(x for x in net if x>0)/-sum(x for x in net if x<0) if any(x<0 for x in net) else None,
            largest_winner_removed_net=sum(net)-max([0.]+net),open_quantity=float(session.position),
            unclosed_scenario=state.get("active") is not None,
            pending_intents=conn.execute("SELECT count(*) FROM llm_scenario_intents WHERE status='PENDING'").fetchone()[0],
            unknown_exchange_children=conn.execute("SELECT count(*) FROM llm_scenario_children WHERE status='UNKNOWN'").fetchone()[0],
            halted=state.get("breaker",{}).get("blocked",False),
            halted_field_basis="HISTORICAL_BREAKER_LATCH_NOT_EFFECTIVE_ENTRY_PERMISSION",
            scenario_loss_budget_breaches=sum(r['net_pnl'] < -budgets[r['scenario_id']] for r in completed),
            scenario_risk_budgets=budgets)
        if economic_profile:
            economic["terminal_mark_price"] = str(session.mark_price)
        report=dict(schema=1,contract=contract,contract_hash=digest(contract),data_coverage=market.manifest(),
            simulated_only=True,mode="SYNTHETIC_FIXTURE" if origin=="fixture" else "HISTORICAL_LLM_RESEARCH",
            decisions=len(decisions),model_decisions=len(tape.rows),
            model_failures=provider.failures-provider.input_failures,input_preparation_failures=provider.input_failures,
            decision_outcomes=dict(statuses),protection_outcomes=dict(driver.protection),economic=economic,
            runtime_outcome_reasons=dict(outcome_reasons),
            recovery_state=dict(policy_version=state.get("recovery",{}).get("policy_version"),
                                phase=state.get("recovery",{}).get("phase"),stage=state.get("recovery",{}).get("stage")),
            tape_hash=tape.transcript_hash(),exchange_hash=session.digest(),cash_baseline_net=0,
            assumptions=["OHLC_ORDER_IS_ASSUMED_NOT_TICKS","NO_ORDERBOOK_QUEUE_MODEL","NO_LIQUIDATION_RECONSTRUCTION",
                "MODEL_HISTORICAL_KNOWLEDGE_NOT_EXCLUDED","NO_UNTOUCHED_HOLDOUT","NO_FORCED_TERMINAL_CLOSE"],
            funding_boundary_mark_assumption="previous_confirmed_mark_close_before_new_minute_trade",
            verdict="PREREGISTER_REPLAY",automatic_promotion=False)
        reasons=["HISTORICAL_NOT_UNTOUCHED","INTRABAR_PATH_ASSUMED","NO_LIQUIDATION_RECONSTRUCTION",
                 "NO_FORWARD_PROFITABILITY_EVIDENCE"]
        if len(completed)<60:reasons.append("CLOSED_SCENARIOS_LT_60")
        if provider.failures>provider.input_failures:reasons.append("MODEL_FAILURES")
        if provider.input_failures:reasons.append("INPUT_PREPARATION_FAILURES")
        if economic["unclosed_scenario"] or economic["pending_intents"]:reasons.append("UNSETTLED_END_STATE")
        if economic["unknown_exchange_children"]:reasons.append("UNKNOWN_EXECUTION_EVIDENCE")
        if observed is not None and not observed.manifest()["input_coverage_eligible"]:
            reasons.append("OBSERVED_INPUT_COVERAGE_INELIGIBLE")
        if statuses.get("fenced"):reasons.append("FENCED_OBSERVATIONS")
        report["readiness"]={"profitability_proven":False,"insufficiency_reasons":reasons}
        if economic_profile:
            report["final_state_hash"] = digest(state)
            report["economic_clock_trace_hash"] = digest([row["trace"] for row in tape.ticks])
            report["runtime_event_hash"] = _runtime_event_hash(conn)
            report["execution_status"] = "STOPPED" if stopped else "COMPLETE"
            report["failure_evidence"] = stopped
            report["coverage"] = dict(planned_slots=len(decisions),processed_slots=sum(statuses.values()),
                model_free_drain_status=drain_status,model_free_drain_end_ms=end,
                final_economic_time_ms=session.ts_ms,eligible=stopped is None and drain_status=="COMPLETE")
            if stopped:
                report["readiness"]["insufficiency_reasons"].append("STOPPED_TECHNICAL_FAILURE")
        if policy_profile in REJECTION_PROFILES:
            from live.scenario_review_storage import review_text
            from utils.btc_review_event_references import read_event
            events = [(slot, json.loads(read_event(body, conn, slot, sequence, decode_legacy=review_text)))
                      for slot, sequence, body in conn.execute(
                "SELECT slot,sequence,body FROM llm_scenario_review_events ORDER BY slot,sequence")]
            rejected_slots = set()
            candidates = {}
            for slot, event in events:
                if event["kind"] == "RESEARCH_REJECTION":
                    rejected_slots.add(slot)
                    bundle = event["body"]["candidates"]
                elif event["kind"] == "CALCULATED_CANDIDATES":
                    bundle = event["body"]
                else:
                    continue
                for candidate in bundle["candidates"]:
                    if candidate.get("rejection_type") == "PREAUTHORITY_ECONOMIC":
                        candidates[(slot, candidate["id"])] = candidate["rejection"]
            if stopped is None and len(rejected_slots) != statuses.get("rejected_no_action", 0):
                raise TapeMismatch("economic_rejection_outcome_evidence_mismatch")
            report["economic_rejections"] = dict(
                policy="preauthority-economic-reject-v1", rejected_ticks=len(rejected_slots),
                rejected_candidates=len(candidates), codes=dict(Counter(candidates.values())),
                durable_events_sha256=digest(events))
        report["result_hash"]=digest(report)
        # Run mechanics are outside the reproducible economic result hash.
        report["execution_metadata"]={"mode":mode,"actual_model_calls":provider.model_calls,
                                      "synthetic_policy_calls":provider.calls if policy is not None else 0,
                                      "tape_records_consumed":len(tape.used)}
        if economic_profile:
            report["execution_metadata"]["fixture_generator_calls"] = provider.fixture_generator_calls
            report["execution_metadata"]["model_generator_dispatches"] = provider.model_calls
            actual_count = getattr(model_generator,"actual_http_calls",None)
            report["execution_metadata"]["actual_model_calls"] = (
                0 if mode in {"fixture","frozen"} or provider.model_calls==0 else
                actual_count-provider.initial_http_calls if type(actual_count) is int and type(provider.initial_http_calls) is int else None)
        (output/"report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False))
        (output/"exchange.json").write_text(json.dumps(session.snapshot(),indent=2,allow_nan=False))
        (output/"equity.json").write_text(json.dumps(driver.curve))
        return report
    finally:
        tape.close();conn.close()


def main():
    import os
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input",type=Path,required=True);parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--tape",type=Path,required=True);parser.add_argument("--mode",choices=("fresh","frozen"),required=True)
    parser.add_argument("--path",choices=("OHLC","OLHC"),default="OHLC")
    parser.add_argument("--cost-multiplier",type=float,default=1.)
    parser.add_argument("--max-decisions",type=int,default=288)
    parser.add_argument("--policy-profile",choices=tuple(POLICY_PROFILES),default="current-live-v2")
    parser.add_argument("--initial-state",choices=("fresh-unblocked","recovery-stage0"),default="fresh-unblocked")
    parser.add_argument("--observed-packet",type=Path)
    parser.add_argument("--observed-packet-sha256")
    parser.add_argument("--market-input-mode",choices=("historical","observed","reconstructed_at_observed_schedule"),default="historical")
    parser.add_argument("--allow-input-gaps",action="store_true")
    args=parser.parse_args()
    bundle,market=load_bundle(args.input)
    with network_boundary(args.mode,os.environ.get("PRISM_BTC_SCENARIO_OAUTH_URL")) as blocked:
        report=run_replay(bundle,market,args.output,mode=args.mode,tape_path=args.tape,path=args.path,
            cost_multiplier=args.cost_multiplier,max_decisions=args.max_decisions,policy_profile=args.policy_profile,
            initial_state=args.initial_state,observed_packet=args.observed_packet,
            observed_packet_sha256=args.observed_packet_sha256,market_input_mode=args.market_input_mode,
            allow_input_gaps=args.allow_input_gaps)
        if blocked:raise RuntimeError("forbidden_network_attempt_detected")
    print(json.dumps({"status":"complete","result_hash":report["result_hash"],"economic":report["economic"],
                      "execution_metadata":report["execution_metadata"]},ensure_ascii=False))


if __name__=="__main__":
    main()
