"""Regression contract for bounded research, not evidence of profitability."""
from collections import Counter
import json
import sqlite3
from types import SimpleNamespace

import numpy as np
import pytest

from analysis import strategy_mixture as sm
from backtest.portfolio_replay import DailyFeatures, PortfolioSummary


def feature(**changes):
    defaults = dict(available_at=sm.TRAIN, sma20=100., sma60=100., atr14=5., std20=10.,
                    prior_high20=130., prior_low20=70., prior_high10=120., prior_low10=80., close=100.)
    return DailyFeatures(**(defaults | changes))


def daily_bars(days=95):
    ts = np.arange(sm.START, sm.START+days*sm.DAY, sm.BAR)
    values = 100 + np.repeat(np.arange(days, dtype=float), 288)
    return np.column_stack((ts, values, values+2, values-2, values))


def train_results():
    return {r["id"]: dict(status="OK", metrics=dict(initial_nav=10000., final_nav=11000.,
             mtm_mdd=.01, completed_campaigns=12), statistics=dict(daily_volatility=.02))
            for r in sm.planned_registry() if r["phase"] == "TRAIN"}


def test_catalogue_and_exact_registry():
    assert len(sm.MIXES) == 7 and len(sm.POLICIES) == 12
    assert tuple(sm.MIXES.values()) == ((.5,.5,0),(.5,0,.5),(0,.5,.5),(1/3,1/3,1/3),(.5,.25,.25),(.25,.5,.25),(.25,.25,.5))
    assert all(sum(v) == 1 and min(v) >= 0 for v in sm.MIXES.values())
    with pytest.raises(TypeError):
        sm.MIXES["M08"] = (1, 0, 0)
    rows = sm.planned_registry()
    assert len({r["id"] for r in rows}) == 240
    assert Counter(r["phase"] for r in rows) == {"TRAIN":24,"OOS_RAW":96,"OOS_MATCHED":112,"OOS_PARTIAL":8}
    assert {r["name"] for r in rows if r["phase"] == "OOS_PARTIAL"} == {"FROZEN"}


@pytest.mark.parametrize("a,b,d", [(110,100,1),(90,100,-1),(100,100,0)])
def test_trend(a,b,d):
    assert sm.decide_T(-1, feature(sma20=a,sma60=b)) == d


@pytest.mark.parametrize("held,close,want", [(0,131,1),(0,69,-1),(1,69,-1),(-1,131,1),(1,79,0),(-1,121,0),(1,100,1),(0,100,0)])
def test_breakout_actual_state(held, close, want):
    assert sm.decide_B(held, feature(close=close)) == want


@pytest.mark.parametrize("held,close,want", [(0,85,1),(0,115,-1),(1,100,0),(-1,100,0),(1,115,0),(-1,85,0),(1,90,1),(0,90,0)])
def test_reversion_actual_state_and_exit_priority(held, close, want):
    assert sm.decide_R(held, feature(close=close)) == want


def test_reversion_zero_std_and_range_gate():
    assert sm.decide_R(1, feature(std20=0,close=80)) == 0
    assert sm.decide_R(1, feature(sma60=90,close=80)) == 0
    assert sm.decide_R(0, feature(sma60=95,close=85)) == 1


def test_callbacks_have_no_virtual_holding_or_double_scale():
    callback = sm.callbacks("M01")["B"]
    assert callback(0, feature(close=131)).direction == 1
    assert callback(1, feature(close=100)).direction == 1
    assert callback(0, feature(close=100)).direction == 0  # rejected/stopped before next decision
    assert callback(0, feature(close=131)).weight == .5
    assert sm.callbacks("M02")["R"](0, feature(close=90)).direction == 0


def test_features_warmup_causality_current_exclusion_and_atr():
    bars = daily_bars()
    assert not sm.build_daily_features(bars[:89*288])
    full = sm.build_daily_features(bars)
    assert min(full) == sm.START+90*sm.DAY == sm.TRAIN
    first = full[sm.TRAIN]
    assert first.sma20 == np.mean(np.arange(170,190))
    assert first.sma60 == np.mean(np.arange(130,190))
    assert first.prior_high20 == 190  # current high191 excluded
    assert first.prior_low10 == 177
    assert first.atr14 == 4
    prefix = sm.build_daily_features(bars[:91*288])
    changed = bars.copy()
    changed[91*288:,1:] *= 100
    mutated = sm.build_daily_features(changed)
    assert prefix == {k:v for k,v in full.items() if k <= sm.START+91*sm.DAY}
    assert prefix == {k:v for k,v in mutated.items() if k <= sm.START+91*sm.DAY}
    # Last day's 100 point gap uses previous close in simple14 true range.
    bars[89*288:90*288,1:] += 100
    assert sm.build_daily_features(bars)[sm.TRAIN].atr14 == (13*4+103)/14


@pytest.mark.parametrize("mutate", [lambda x:x[:-1],lambda x:np.delete(x,5,axis=0),lambda x:np.roll(x,1,axis=0)])
def test_incomplete_or_unordered_day_rejected(mutate):
    with pytest.raises(ValueError):
        sm.build_daily_features(mutate(daily_bars()))


def test_nonfinite_or_invalid_ohlc_rejected():
    for value in (np.nan, -1.):
        bars = daily_bars()
        bars[5,4] = value
        with pytest.raises(ValueError):
            sm.build_daily_features(bars)


def test_train_selection_worst_path_eligibility_ties_and_no_oos():
    results = train_results()
    assert sm.select_train_candidate(results)["selected_id"] == "M01"
    results["TRAIN/M01/c1/d5/OLHC/full"]["metrics"]["completed_campaigns"] = 11
    assert sm.select_train_candidate(results)["selected_id"] == "M02"
    results["TRAIN/M03/c1/d5/OHLC/full"]["metrics"]["final_nav"] = 20000
    assert sm.select_train_candidate(results)["selected_id"] == "M02"  # worst path still tie
    with pytest.raises(ValueError):
        sm.select_train_candidate(results | {"OOS_RAW/M07/c1/d5/OHLC/full": {}})
    for r in results.values():
        r["metrics"]["completed_campaigns"] = 0
    selection = sm.select_train_candidate(results)
    assert selection["selected_id"] is None and selection["diagnostic_id"] == "M01"
    assert sm.select_train_candidate({k:dict(status="INVALID") for k in results})["diagnostic_id"] == "M01"


def test_freeze_train_14_scales_and_actual_quantity_configuration():
    results = train_results()
    results["TRAIN/B/c1/d5/OLHC/full"]["statistics"]["daily_volatility"] = .04
    freeze = sm.freeze_train(results, dict(contract_hash="c", data_hash="d"))
    assert len(freeze["scales"]) == 14
    assert freeze["scales"]["M01-single"]["scale"] == .5
    before = sm.digest(freeze)
    oos = {"M07":99999}  # Entirely separate; cannot enter selector or freeze API.
    oos["M07"] = -99999
    assert sm.digest(freeze) == before
    row = next(r for r in sm.planned_registry() if r["name"] == "M01-single")
    name, policy = sm.resolved_trial(row, freeze)
    assert name == "B" and policy["scale"] == .5 and sm.callbacks(name)["B"](0,feature()).weight == 1
    freeze["scales"]["M01-single"] = dict(status="INSUFFICIENT",scale=None)
    with pytest.raises(ValueError):
        sm.resolved_trial(row, freeze)


def test_preregister_only_no_pnl_and_immutable(tmp_path, monkeypatch):
    monkeypatch.setattr(sm,"run_portfolio",lambda *a,**k:pytest.fail("preregistration computed PnL"))
    monkeypatch.setattr(sm,"load_inputs",lambda *a,**k:pytest.fail("preregistration loaded market"))
    assert sm.main(["--preregister-only","--output-dir",str(tmp_path)]) == 0
    envelope = sm.verify_contract(tmp_path/"contract.json")
    assert len(envelope["contract"]["registry"]) == 240
    sm.preregister(tmp_path)
    with pytest.raises(ValueError):
        sm.write_json(tmp_path/"contract.json", {}, immutable=True)
    envelope["contract"]["catalogue"]["M01"] = [1,0,0]
    (tmp_path/"contract.json").write_text(json.dumps(envelope))
    with pytest.raises(ValueError):
        sm.verify_contract(tmp_path/"contract.json")


def fake_replay(bars, funding, features, callbacks, policy, sink):
    policy.resource_check(dict(ts_ms=policy.start_ms,cash=10000,event_digest="fixture"))
    count = (policy.end_ms-policy.start_ms)//sm.DAY
    nav = tuple((policy.start_ms+(i+1)*sm.DAY,10000.+10*(i+1)+2*(i%2)) for i in range(count))
    sink(dict(kind="synthetic",ts_ms=policy.start_ms,scale=policy.scale))
    return PortfolioSummary(nav,dict(initial_nav=10000.,final_nav=nav[-1][1],mtm_mdd=.01,
        completed_campaigns=12,turnover=10.,underwater_ms=0),{},dict(events="fixture",daily_nav=sm.digest(nav)),{})


@pytest.fixture
def tiny_calendar(monkeypatch):
    monkeypatch.setattr(sm,"TRAIN",sm.START+90*sm.DAY)
    monkeypatch.setattr(sm,"OOS",sm.START+93*sm.DAY)
    monkeypatch.setattr(sm,"END",sm.START+96*sm.DAY)
    return daily_bars(96),np.array([[sm.START,0.]])


def test_synthetic_e2e_full240_aliases_and_verified_resume(tmp_path,tiny_calendar):
    bars,funding = tiny_calendar
    contract = sm.preregister(tmp_path)
    report = sm.run_registry(tmp_path,contract,bars,funding,{"synthetic":True},replay=fake_replay)
    rows = json.loads((tmp_path/"registry.json").read_text())
    assert Counter(r["status"] for r in rows) == {"OK":240}
    assert any(r.get("alias_of") for r in rows)
    assert report["status"] == "INVALID_RESEARCH"  # tiny historical calendar cannot qualify
    assert report["auto_activate"] is False
    expected, freeze = sm.digest(report),(tmp_path/"freeze.json").read_bytes()
    resumed = sm.run_registry(tmp_path,contract,bars,funding,{"synthetic":True},replay=lambda *a:pytest.fail("completed trial rerun"))
    assert sm.digest(resumed) == expected and (tmp_path/"freeze.json").read_bytes() == freeze
    with pytest.raises(ValueError):
        sm.run_registry(tmp_path,contract,bars,funding,{"synthetic":False},replay=fake_replay)
    path = next((tmp_path/"trials").rglob("daily_nav.csv"))
    path.write_text("corrupted")
    with pytest.raises(RuntimeError,match="artifact hash"):
        sm.run_registry(tmp_path,contract,bars,funding,{"synthetic":True},replay=fake_replay)


def test_budget_incomplete_preserves240_and_cumulative_time(tmp_path,tiny_calendar):
    bars,funding = tiny_calendar
    contract = sm.preregister(tmp_path)
    limits = dict(seconds=0,rss_bytes=2**63,artifact_bytes=2**63)
    report = sm.run_registry(tmp_path,contract,bars,funding,{},limits=limits,replay=fake_replay,preparation_seconds=1)
    assert report["status"] == "INCOMPLETE" and report["registry_counts"] == {"INCOMPLETE":240}
    previous = json.loads((tmp_path/"run_state.json").read_text())["elapsed_seconds"]
    assert previous >= 1
    sm.run_registry(tmp_path,contract,bars,funding,{},replay=fake_replay)
    assert json.loads((tmp_path/"run_state.json").read_text())["elapsed_seconds"] > previous


def test_invalid_dependencies_remain_in_full_registry(tmp_path,tiny_calendar):
    bars,funding = tiny_calendar
    contract = sm.preregister(tmp_path)
    def invalid(*args):
        raise ValueError("synthetic execution invalid")
    report = sm.run_registry(tmp_path,contract,bars,funding,{},replay=invalid)
    assert report["status"] == "INVALID_RESEARCH"
    assert report["registry_counts"] == {"INVALID":240}
    freeze = json.loads((tmp_path/"freeze.json").read_text())
    assert freeze["selected_id"] is None and freeze["diagnostic_id"] == "M01"
    assert all(v["status"] == "INSUFFICIENT" for v in freeze["scales"].values())
    resumed = sm.run_registry(tmp_path,contract,bars,funding,{},replay=lambda *a:pytest.fail("verified INVALID rerun"))
    assert sm.digest(resumed) == sm.digest(report)


def test_loader_never_falls_back_to_zero_funding(monkeypatch,tmp_path):
    path = tmp_path/"data.db"
    path.write_bytes(b"fixture")
    monkeypatch.setattr(sm,"load_bars",lambda *a:SimpleNamespace(rows=[dict(open_time=sm.START,open=1,high=1,low=1,close=1)],manifest={}))
    def missing(*args):
        raise ValueError("incomplete funding coverage")
    monkeypatch.setattr(sm,"load_funding",missing)
    with pytest.raises(ValueError,match="funding coverage"):
        sm.load_inputs(path,path)


def make_synthetic_db(tmp_path, bars):
    db = tmp_path/"synthetic.db"
    with sqlite3.connect(db) as connection:
        connection.execute("CREATE TABLE klines(timeframe TEXT,open_time INTEGER,open REAL,high REAL,low REAL,close REAL,volume REAL,turnover REAL,confirmed INTEGER)")
        connection.execute("CREATE TABLE funding(funding_time INTEGER,rate REAL)")
        connection.executemany("INSERT INTO klines VALUES(?,?,?,?,?,?,?,?,?)",[("5m",int(t),o,h,low,c,1.,c,1) for t,o,h,low,c in bars])
        connection.executemany("INSERT INTO funding VALUES(?,?)",[(int(t),.0001) for t in np.arange(sm.START,sm.END,8*3600_000)])
    return db


def test_real_strict_loader_features_engine_statistics_prefix(tmp_path,tiny_calendar):
    bars, _ = tiny_calendar
    db = make_synthetic_db(tmp_path,bars)
    before = sm.file_hash(db)
    bars,funding,manifest = sm.load_inputs(db,db)
    assert sm.file_hash(db) == before and manifest["funding"]["missing_count"] == 0
    features = sm.build_daily_features(bars)
    full = sm.run_portfolio(bars,funding,features,sm.callbacks("M04"),sm.PortfolioPolicy(sm.TRAIN,sm.END))
    partial = sm.run_portfolio(bars,funding,features,sm.callbacks("M04"),sm.PortfolioPolicy(sm.TRAIN,sm.OOS))
    assert partial.daily_nav == tuple((t,n) for t,n in full.daily_nav if t <= sm.OOS)
    summarized = sm.summarize(full,sm.TRAIN,sm.END)
    assert summarized["status"] == "OK" and summarized["statistics"]["daily_count"] == 6
    assert full.metrics["fees"] > 0 and full.metrics["funding"] > 0


def test_resume_refuses_missing_completed_result(tmp_path,tiny_calendar):
    bars,funding = tiny_calendar
    contract = sm.preregister(tmp_path)
    sm.run_registry(tmp_path,contract,bars,funding,{},replay=fake_replay)
    next((tmp_path/"trials").rglob("result.json")).unlink()
    with pytest.raises(RuntimeError,match="result missing"):
        sm.run_registry(tmp_path,contract,bars,funding,{},replay=fake_replay)


def test_out_of_range_collector_updates_do_not_change_identity_or_resume(tmp_path,tiny_calendar):
    db = make_synthetic_db(tmp_path,tiny_calendar[0])
    provenance = {}
    bars,funding,manifest = sm.load_inputs(db,db,provenance=provenance)
    output = tmp_path/"run"
    contract = sm.preregister(output)
    report = sm.run_registry(output,contract,bars,funding,manifest,replay=fake_replay)
    with sqlite3.connect(db) as connection:  # External collector, not research loader.
        connection.execute("INSERT INTO klines VALUES('5m',?,500,510,490,505,1,505,1)",(sm.END,))
        connection.execute("INSERT INTO funding VALUES(?,.0003)",(sm.END,))
    new_bars,new_funding,new_manifest = sm.load_inputs(db,db)
    assert sm.digest(manifest) == sm.digest(new_manifest)
    assert set(manifest) == {"bars","funding"}
    assert sm.file_hash(db) != provenance["source_files_before_load"][str(db.resolve())]
    assert sm.recheck_inputs(db,db,manifest) == sm.digest(manifest)
    resumed = sm.run_registry(output,contract,new_bars,new_funding,new_manifest,replay=lambda *a:pytest.fail("collector update reran trial"))
    assert sm.digest(resumed) == sm.digest(report)
    elapsed = json.loads((output/"run_state.json").read_text())["elapsed_seconds"]
    checked = sm.finalize_recheck(output,resumed,manifest,db,db,provenance)
    assert sm.digest(checked) == sm.digest(report)
    assert json.loads((output/"provenance.json").read_text())["whole_files_changed"] is True
    assert json.loads((output/"run_state.json").read_text())["elapsed_seconds"] > elapsed


@pytest.mark.parametrize("table",["klines","funding"])
def test_in_range_mutation_rejects_recheck_and_resume(tmp_path,tiny_calendar,table):
    db = make_synthetic_db(tmp_path,tiny_calendar[0])
    bars,funding,manifest = sm.load_inputs(db,db)
    output = tmp_path/"run"
    contract = sm.preregister(output)
    report = sm.run_registry(output,contract,bars,funding,manifest,replay=fake_replay)
    with sqlite3.connect(db) as connection:
        if table == "klines":
            connection.execute("UPDATE klines SET close=close+.1 WHERE open_time=?",(sm.TRAIN,))
        else:
            connection.execute("UPDATE funding SET rate=.0002 WHERE funding_time=?",(sm.TRAIN,))
    new_bars,new_funding,new_manifest = sm.load_inputs(db,db)
    assert sm.digest(new_manifest) != sm.digest(manifest)
    with pytest.raises(ValueError,match="SELECTED_HISTORY_CHANGED"):
        sm.recheck_inputs(db,db,manifest)
    with pytest.raises(ValueError,match="immutable artifact mismatch"):
        sm.run_registry(output,contract,new_bars,new_funding,new_manifest,replay=fake_replay)
    checked = sm.finalize_recheck(output,report,manifest,db,db,{})
    assert checked["status"] == "INVALID_RESEARCH"
    assert checked["reason_codes"] == ["SELECTED_HISTORY_CHANGED_DURING_RUN"]
    assert json.loads((output/"run_state.json").read_text())["status"] == "INVALID_RESEARCH"


def test_end_recheck_resource_limit_is_cumulative(tmp_path,tiny_calendar):
    bars,funding = tiny_calendar
    contract = sm.preregister(tmp_path)
    report = sm.run_registry(tmp_path,contract,bars,funding,{},replay=fake_replay)
    previous = json.loads((tmp_path/"run_state.json").read_text())["elapsed_seconds"]
    checked = sm.finalize_recheck(tmp_path,report,{},"unused","unused",{},
        limits=dict(seconds=0,rss_bytes=2**63,artifact_bytes=2**63))
    assert checked["status"] == "INCOMPLETE"
    assert json.loads((tmp_path/"run_state.json").read_text())["elapsed_seconds"] >= previous
