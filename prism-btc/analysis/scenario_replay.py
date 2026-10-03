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
from live.scenario_llm import propose,SYSTEM_PROMPT,ScenarioModelError
from live.shared_entry_coordinator import mutation_lock

ROOT=Path(__file__).resolve().parents[2]


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


def contract_for(bundle,path,cost_multiplier,initial_equity,origin="luna"):
    package=ROOT/"prism-btc"
    sources={str(p.relative_to(package)):hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(package.rglob("*.py")) if "tests" not in p.relative_to(package).parts}
    return dict(schema=1,kind="SIMULATED_LLM_SCENARIO",decision_origin=origin,data_hash=bundle["data_hash"],
        start_ms=bundle["start_ms"],end_ms=bundle["end_ms"],path=path,initial_equity=initial_equity,
        model="gpt-6-luna",effort="high",tier="fast",prompt_hash=hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest(),
        source_hashes=sources,runtime_versions={"python":sys.version.split()[0],"pandas":pd.__version__,"numpy":np.__version__},
        costs=dict(maker_fee=.0002*cost_multiplier,taker_fee=.00055*cost_multiplier,
                   slippage=.0005*cost_multiplier,spread=.0001,participation=.01),
        latencies=dict(entry=5000,market=5000,cancel=2000,amend=2000,model="measured_and_taped_ms"),
        decision_interval_ms=300000,protection_interval_ms=60000,
        terminal_policy="NO_FORCED_CLOSE_MARK_OPEN_POSITION",liquidity="previous_minute_volume_shared_across_four_path_points")


class Driver:
    def __init__(self,bundle,market,session,path,broker,conn):
        self.session,self.broker,self.conn=session,broker,conn
        self.events=[];self.index=0;self.capacity={};self.curve=[];self.protection=Counter()
        self.next_protect=bundle["start_ms"]+15000
        self.end=bundle["end_ms"]
        keys=("open","high","low","close") if path=="OHLC" else ("open","low","high","close")
        funding_times={f["timestamp"] for f in bundle["funding"]}
        for i,(when,bar) in enumerate(market.source.iterrows()):
            start=when.value//1_000_000
            if not bundle["start_ms"]<=start<self.end:continue
            mark=market.mark.loc[when]
            cap=int(float(market.source.iloc[i-1].volume)*.01/.001) if i else 0
            self.capacity[start]=cap
            if start in funding_times:
                previous=float(market.source.iloc[i-1].close) if i else float(bar.open)
                previous_mark=float(market.mark.loc[market.source.index[i-1]].close) if i else float(mark.open)
                self.events.append((start,previous,previous_mark,None))
            for offset,key in zip((1,20000,40000,59999),keys):
                self.events.append((start+offset,float(bar[key]),float(mark[key]),start))
        self.events.sort()

    def _value(self):
        value=float(self.session.equity)
        if not math.isfinite(value) or value<=0:
            raise ValueError("simulated_insolvency")
        self.curve.append((self.session.ts_ms,value))

    def advance_to(self,target):
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
        self.session.elapse(target)
        self._value()


class Decisions:
    def __init__(self,tape,driver,mode,*,policy=None,model_generator=None,fixed_latency_ms=2000):
        self.tape,self.driver,self.mode=tape,driver,mode
        self.policy,self.model_generator,self.fixed_latency_ms=policy,model_generator,fixed_latency_ms
        self.fatal=None;self.calls=0;self.failures=0

    def __call__(self,snapshot,context):
        at=self.driver.session.ts_ms
        if self.mode=="frozen":
            try:record=self.tape.lookup(snapshot,context)
            except TapeMismatch as exc:
                self.fatal=str(exc);raise
            self.driver.advance_to(at+record["latency_ms"])
            if record["error"]:
                self.failures+=1
                if record["error"].startswith("ScenarioModelError:"):
                    raise ScenarioModelError(record["error"].split(":",1)[1])
                raise ValueError("recorded_model_failure")
            return record["proposal"]
        self.calls+=1;response=None;error=None;raw=[]
        started=time.monotonic()
        try:
            if self.policy is not None:
                response=self.policy(snapshot,context)
            else:
                from live.scenario_oauth import generate_scenario
                generate=self.model_generator or generate_scenario
                def retain(**kwargs):
                    result=generate(**kwargs);raw.append(result.text);return result
                response=propose(snapshot,context,response_contract(context),generate=retain,clock=self.driver.session.clock)
        except ScenarioModelError as exc:
            # Preserve only runtime classification, never arbitrary exception text.
            category = "oauth_model_failed" if str(exc) in {"oauth_model_failed", "late_response"} else "response_contract_mismatch"
            error = "ScenarioModelError:" + category
        except Exception as exc:
            error=type(exc).__name__
        latency=self.fixed_latency_ms if self.policy is not None else max(1,int((time.monotonic()-started)*1000))
        if latency>75000:error="model_latency_exceeded"
        self.tape.append(snapshot,context,response,latency,error=error,raw_response=raw[0] if raw else None)
        self.driver.advance_to(at+latency)
        if error:
            self.failures+=1
            if error.startswith("ScenarioModelError:"):
                raise ScenarioModelError(error.split(":",1)[1])
            raise ValueError("model_proposal_failed")
        return response


def run_replay(bundle,market,output,*,mode,tape_path,path="OHLC",cost_multiplier=1.,
               initial_equity=10000.,policy=None,model_generator=None,max_decisions=288):
    """No production paths accepted; fresh DB per run and explicit data provenance."""
    if mode not in {"fresh","frozen","fixture"} or path not in {"OHLC","OLHC"}:
        raise ValueError("explicit_replay_mode_required")
    if mode=="fixture" and policy is None:raise ValueError("fixture_policy_required")
    if mode!="fixture" and policy is not None:raise ValueError("fixture_must_not_be_labeled_llm")
    if type(max_decisions) is not int or not 1<=max_decisions<=288:
        raise ValueError("bounded_decision_count_required")
    if not market.manifest()["mark"]["complete"]:
        raise ValueError("complete_mark_history_required")
    if bundle["source_interval_ms"]!=60000 or bundle["symbol"]!="BTCUSDT":
        raise ValueError("minute_btc_bundle_required")
    if not 0<cost_multiplier<=3 or not math.isfinite(initial_equity) or initial_equity<=0:
        raise ValueError("invalid_replay_cost_or_equity")
    origin="fixture" if mode=="fixture" else "luna"
    if mode=="frozen":
        header=json.loads(Path(tape_path).read_text().splitlines()[0])
        origin=header["contract"]["decision_origin"]
        if origin not in {"fixture","luna"}:raise ValueError("unknown_decision_origin")
    contract=contract_for(bundle,path,cost_multiplier,initial_equity,origin)
    output=Path(output).resolve()
    if output.exists():raise ValueError("never_overwrite_replay")
    if any(p in output.parts for p in ("state","logs","trading")):
        raise ValueError("dedicated_research_directory_required")
    output.mkdir(parents=True)
    tape=DecisionTape(tape_path,"frozen" if mode=="frozen" else "record",contract)
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
    provider=Decisions(tape,driver,mode,policy=policy,model_generator=model_generator)
    runtime=ScenarioRuntime(conn,broker,provider,lambda:market.snapshot((session.ts_ms//300000)*300000),clock=session.clock)
    statuses=Counter();start=bundle["start_ms"];end=bundle["end_ms"]
    decisions=list(range(start,end,300000))
    if len(decisions)>max_decisions:raise ValueError("decision_budget_exceeded")
    try:
        for i,at in enumerate(decisions):
            driver.advance_to(at)
            result=runtime.tick();statuses[result["status"]]+=1
            if provider.fatal:raise TapeMismatch(provider.fatal)
            if (i+1)%12==0:print(json.dumps({"progress":i+1,"total":len(decisions),"status":result["status"]}),flush=True)
        driver.advance_to(end)
        with mutation_lock(conn):runtime._reconcile(runtime.state())
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
            halted=state.get("breaker",{}).get("blocked",False),
            scenario_loss_budget_breaches=sum(r['net_pnl'] < -budgets[r['scenario_id']] for r in completed),
            scenario_risk_budgets=budgets)
        report=dict(schema=1,contract=contract,contract_hash=digest(contract),data_coverage=market.manifest(),
            simulated_only=True,mode="SYNTHETIC_FIXTURE" if origin=="fixture" else "HISTORICAL_LLM_RESEARCH",
            decisions=len(decisions),model_decisions=len(tape.rows),model_failures=provider.failures,
            decision_outcomes=dict(statuses),protection_outcomes=dict(driver.protection),economic=economic,
            tape_hash=tape.transcript_hash(),exchange_hash=session.digest(),cash_baseline_net=0,
            assumptions=["OHLC_ORDER_IS_ASSUMED_NOT_TICKS","NO_ORDERBOOK_QUEUE_MODEL","NO_LIQUIDATION_RECONSTRUCTION",
                "MODEL_HISTORICAL_KNOWLEDGE_NOT_EXCLUDED","NO_UNTOUCHED_HOLDOUT","NO_FORCED_TERMINAL_CLOSE"],
            funding_boundary_mark_assumption="previous_confirmed_mark_close_before_new_minute_trade",
            verdict="PREREGISTER_REPLAY",automatic_promotion=False)
        reasons=["HISTORICAL_NOT_UNTOUCHED","INTRABAR_PATH_ASSUMED","NO_LIQUIDATION_RECONSTRUCTION",
                 "NO_FORWARD_PROFITABILITY_EVIDENCE"]
        if len(completed)<60:reasons.append("CLOSED_SCENARIOS_LT_60")
        if provider.failures:reasons.append("MODEL_FAILURES")
        if economic["unclosed_scenario"] or economic["pending_intents"]:reasons.append("UNSETTLED_END_STATE")
        report["readiness"]={"profitability_proven":False,"insufficiency_reasons":reasons}
        report["result_hash"]=digest(report)
        # Run mechanics are outside the reproducible economic result hash.
        report["execution_metadata"]={"mode":mode,"actual_model_calls":provider.calls,
                                      "tape_records_consumed":len(tape.used)}
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
    args=parser.parse_args()
    bundle,market=load_bundle(args.input)
    with network_boundary(args.mode,os.environ.get("PRISM_BTC_SCENARIO_OAUTH_URL")) as blocked:
        report=run_replay(bundle,market,args.output,mode=args.mode,tape_path=args.tape,path=args.path,
            cost_multiplier=args.cost_multiplier,max_decisions=args.max_decisions)
        if blocked:raise RuntimeError("forbidden_network_attempt_detected")
    print(json.dumps({"status":"complete","result_hash":report["result_hash"],"economic":report["economic"],
                      "execution_metadata":report["execution_metadata"]},ensure_ascii=False))


if __name__=="__main__":
    main()
