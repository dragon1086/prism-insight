"""Re-entry v3: newest decision wins (2026-10-22, KR 028670 팬오션).

Two ACTIVE watches of one ticker fired independently: a 10/07 held-off watch with stale levels (both
report resistances under the price, so the target fell to a 10/15 intraday wick) bought at 14:00 on
10/21 while the newer 10/15 stop-exit watch had rejected the same setup (R/R 0.07). Only the watch of
the latest decision row may open new positions; older ones keep managing what they own.
"""
import json
import sqlite3

import pytest

import test_reentry_campaign as T
from observability import reentry_v3_shadow as V3
from prism_core import reentry_campaign as C
from prism_core import reentry_v3_live as LIVE

NEW_LEVELS = {"primary_support": 12000, "secondary_support": 11800, "primary_resistance": 12500,
              "secondary_resistance": 13500}


def _stop_row(bars, ticker="000001", decision_id="report:a"):
    return {"source": "STOP_EXIT", "account_key": "acct-x", "ticker": ticker, "company_name": "YC",
            "entry_date": bars[65]["date"], "entry_price": 12050, "exit_date": bars[69]["date"], "exit_price": 11600,
            "key_levels": dict(T.YC_LEVELS), "decision_id": decision_id,
            "decided_at": bars[69]["date"] + " 10:00:00"}


def _skip_row(bars, k=70, ticker="000001", source="LOCATION_SKIP", levels=None, price=12400):
    return {"source": source, "account_key": "analysis", "ticker": ticker, "company_name": "YC",
            "entry_date": bars[k]["date"], "entry_price": price, "exit_date": bars[k]["date"], "exit_price": price,
            "key_levels": dict(levels or NEW_LEVELS), "decision_id": f"report:{source}:{k}",
            "decided_at": bars[k]["date"] + " 14:50:00"}


def _frames(bars, ticker="000001"):
    return {ticker: bars, "__benchmark_rows": {ticker: bars}, "__regime_rows": bars}


def _advance(state, rows, bars, k, market):
    frames = _frames(bars)
    return V3.advance(state, rows, frames, bars[k]["date"], market, V3.MarketContext(frames["__regime_rows"]))


def _by_source(state):
    return {w["source"]: w for w in state["watches"]}


def _quote(bars, k=71, price=12150.0, low=12000.0):
    def quote(ticker):
        return {"price": price, "low": low, "open": 12350.0, "high": 12380.0, "volume": 400.0,
                "observed_at": bars[k]["date"] + "T05:00:00+00:00", "source": "test"}
    return quote


# ---------------------------------------------------------------- which watch may fire
@pytest.mark.parametrize("market", ["KR", "US"])
def test_028670_two_watches_only_the_newest_decision_fires(market):
    bars = T.yc_bars()
    state = V3._new_state(market)
    _advance(state, [_stop_row(bars)], bars, 69, market)
    assert state["watches"][0]["ledger"]["next"]["L97"]["eligible"] is True and "superseded" not in state["watches"][0]
    # A newer analysis of the same ticker (held off on day 70): it wins, the stop-exit watch stops firing.
    rows = [_stop_row(bars), _skip_row(bars)]
    assert _advance(state, rows, bars, 70, market) == 1
    watches = _by_source(state)
    old, new = watches["STOP_EXIT"], watches["LOCATION_SKIP"]
    assert old["superseded"] == {"by": new["watch_id"], "after": bars[70]["date"], "reason": "superseded_by_newer",
                                 "by_source": "LOCATION_SKIP", "by_decided_on": bars[70]["date"]}
    assert "superseded" not in new
    assert old["status"] == "ACTIVE" and old["ledger"]["superseded_after"] == bars[70]["date"]
    assert old["ledger"]["next"]["L97"] == dict(old["ledger"]["next"]["L97"], eligible=False,
                                                reason="superseded_by_newer")
    assert new["ledger"]["next"]["L97"]["eligible"] is True
    # Day 71 at 14:00: without the rule the old watch fires R2S (test_reentry_campaign runtime test).
    observed = []
    frozen, counts = V3.intraday(state, market, bars[71]["date"], _frames(bars), bars[70]["date"], _quote(bars),
                                 V3.MarketContext(bars), None, None, observed=observed)
    assert frozen == [] and counts["triggers"] == 0
    assert bars[71]["date"] in new["decisions"]                      # the newest watch is evaluated
    assert bars[71]["date"] not in old.get("decisions", {})          # the old one records no decision
    assert counts["superseded_checks"] == 1 and counts["superseded_would_fire"] == 1
    (watch, decision, misses), = [o for o in observed if o[0] is old]
    assert decision["trigger"] is None
    assert misses[0][:2] == ("R2S", "superseded_by_newer") and misses[0][2]["superseded_by"] == new["watch_id"]
    # Close replay of later sessions: the old watch never opens an attempt after its supersession date.
    _advance(state, rows, bars, len(bars) - 1, market)
    for camp in _by_source(state)["STOP_EXIT"]["ledger"]["campaigns"].values():
        assert all(att["date"] <= bars[70]["date"] for att in camp["attempts"])


@pytest.mark.parametrize("market", ["KR", "US"])
def test_a_single_watch_still_fires(market, tmp_path):
    bars = T.yc_bars()
    state = V3._new_state(market)
    _advance(state, [_stop_row(bars)], bars, 70, market)
    frozen, counts = V3.intraday(state, market, bars[71]["date"], _frames(bars), bars[70]["date"], _quote(bars),
                                 V3.MarketContext(bars), tmp_path, None, observed=[])
    assert [item["trigger"] for _, item in frozen] == ["R2S"] and counts["superseded_checks"] == 0
    assert "superseded" not in state["watches"][0]


def test_watches_of_other_tickers_do_not_supersede_each_other():
    bars = T.yc_bars()
    state = V3._new_state("KR")
    frames = dict(_frames(bars), **{"000002": bars})
    frames["__benchmark_rows"]["000002"] = bars
    V3.advance(state, [_stop_row(bars), _skip_row(bars, ticker="000002")], frames, bars[70]["date"], "KR",
               V3.MarketContext(bars))
    assert [w.get("superseded") for w in state["watches"]] == [None, None]


@pytest.mark.parametrize("market", ["KR", "US"])
def test_a_same_source_rediscussion_continues_the_running_campaign(market):
    """A newer analysis of the same source while that watch is live is not a new campaign (V2._blocked):
    restarting on every re-analysis cost US big winners in the 2026-10-22 replay (11 -> 9)."""
    bars = T.yc_bars()
    state = V3._new_state(market)
    first = _skip_row(bars, k=69, price=11600, levels=T.YC_LEVELS)
    _advance(state, [first], bars, 69, market)
    second = _skip_row(bars, k=70)
    assert _advance(state, [first, second], bars, 70, market) == 0
    assert len(state["watches"]) == 1 and "superseded" not in state["watches"][0]


def test_same_session_tie_follows_the_decision_stamp():
    bars = T.yc_bars()
    state = V3._new_state("KR")
    held = _skip_row(bars, k=69, price=11600, levels=T.YC_LEVELS)
    blocked = dict(_skip_row(bars, k=69, price=11600, levels=T.YC_LEVELS, source="ENTER_BLOCKED"),
                   decided_at=bars[69]["date"] + " 09:40:00")             # morning analysis, before `held`
    _advance(state, [held, blocked], bars, 69, "KR")
    watches = _by_source(state)
    assert watches["ENTER_BLOCKED"]["superseded"]["by"] == watches["LOCATION_SKIP"]["watch_id"]
    assert "superseded" not in watches["LOCATION_SKIP"]


@pytest.mark.parametrize("market", ["KR", "US"])
def test_a_reentry_positions_own_stop_is_part_of_its_watch(market):
    """The stop of a LIVE re-entry buy is a STOP_EXIT row with decision_id reentry_v3:...; it is closed in
    the watch that bought it (find_real_exit) and must not start a fresh 3-attempt watch or supersede it."""
    bars = T.yc_bars()
    state = V3._new_state(market)
    held = _skip_row(bars, k=69, price=11600, levels=T.YC_LEVELS)
    _advance(state, [held], bars, 69, market)
    own_stop = dict(_stop_row(bars, decision_id=LIVE.DECISION_PREFIX + "ev1"), entry_date=bars[70]["date"],
                    exit_date=bars[71]["date"], exit_price=12100)
    assert V3.reentry_own_exit(own_stop) and not V3.reentry_own_exit(held)
    assert _advance(state, [held, own_stop], bars, 71, market) == 0
    assert len(state["watches"]) == 1 and "superseded" not in state["watches"][0]


def test_the_supersession_date_stays_when_an_even_newer_decision_arrives():
    bars = T.yc_bars()
    state = V3._new_state("KR")
    rows = [_stop_row(bars), _skip_row(bars, k=70)]
    _advance(state, rows, bars, 70, "KR")
    rows.append(_skip_row(bars, k=72, price=bars[72]["close"], source="ENTER_BLOCKED"))
    _advance(state, rows, bars, 72, "KR")
    old = _by_source(state)["STOP_EXIT"]
    mid, newest = _by_source(state)["LOCATION_SKIP"], _by_source(state)["ENTER_BLOCKED"]
    assert old["superseded"]["after"] == bars[70]["date"] and old["superseded"]["by"] == newest["watch_id"]
    assert mid["superseded"] == dict(mid["superseded"], by=newest["watch_id"], after=bars[72]["date"])


# ---------------------------------------------------------------- the older watch owns a LIVE position
def _bought(trigger, exit_info=None):
    return {"by_rule": {"L97": trigger, "SS": trigger}, "decision_price": 12150.0, "day_low": 12000.0,
            "live": {"status": "BOUGHT", "key": "k", "holding_ids": [7], "entry_price": 12160.0, "exit": exit_info}}


def _trigger(bars, setup):
    before = C.replay(setup, bars[:71], 69)
    return C.evaluate(setup, bars=bars[:71], i=71, flags=before["flags"], price=12150.0, day_low=12000.0,
                      regime=None)["trigger"]


def test_replay_keeps_managing_the_live_position_of_a_superseded_watch():
    bars = T.yc_bars()
    setup = T.yc_setup(bars)
    trigger = _trigger(bars, setup)
    sold = _bought(trigger, {"date": bars[74]["date"], "price": 11500.0, "stop": True, "exit_kind": "stop"})
    led = C.replay(setup, bars[:80], 69, decisions={bars[71]["date"]: _bought(trigger)},
                   superseded_after=bars[72]["date"])
    first = led["campaigns"]["L97"]["attempts"][0]
    assert first["real"] is True and first["status"] == "OPEN"                # still managed, not dropped
    led = C.replay(setup, bars[:80], 69, decisions={bars[71]["date"]: sold}, superseded_after=bars[72]["date"])
    attempts = led["campaigns"]["L97"]["attempts"]
    assert [(a["status"], a["exit_reason"], a["exit_date"]) for a in attempts] == \
        [("CLOSED", "stop", bars[74]["date"])]                                  # no new try after the sale
    assert led["next"]["L97"]["reason"] == "superseded_by_newer"
    unsuperseded = C.replay(setup, bars[:80], 69, decisions={bars[71]["date"]: sold})
    assert unsuperseded["next"]["L97"]["eligible"] or unsuperseded["next"]["L97"]["reason"] != "superseded_by_newer"


def test_a_live_buy_recorded_after_the_supersession_date_is_still_followed():
    """The real order exists (028670 bought on 10/21): the ledger follows it even if the replay says no."""
    bars = T.yc_bars()
    setup = T.yc_setup(bars)
    trigger = _trigger(bars, setup)
    led = C.replay(setup, bars[:75], 69, decisions={bars[71]["date"]: _bought(trigger)},
                   superseded_after=bars[70]["date"])
    first = led["campaigns"]["L97"]["attempts"][0]
    assert first["real"] is True and first["date"] == bars[71]["date"]
    assert {"date": bars[71]["date"], "reason": "real_buy_forced"} in led["conflicts"]
    assert led["campaigns"]["SS"]["attempts"] == []                           # no virtual SS try after the date


@pytest.mark.parametrize("market", ["KR", "US"])
def test_live_exit_sync_still_reaches_a_superseded_watch(market, monkeypatch):
    bars = T.yc_bars()
    state = V3._new_state(market)
    _advance(state, [_stop_row(bars)], bars, 70, market)
    old = state["watches"][0]
    old["live"] = {bars[71]["date"]: {"status": "BOUGHT", "ticker": "000001", "key": "k", "holding_ids": [7]}}
    old["decisions"][bars[71]["date"]] = dict(_bought(_trigger(bars, old["setup"])), phase="intraday")
    rows = [_stop_row(bars), _skip_row(bars, k=72, price=bars[72]["close"])]
    _advance(state, rows, bars, 72, market)
    assert old["superseded"]["after"] == bars[72]["date"]
    sale = {"date": bars[74]["date"], "price": 11500.0, "profit_rate": -5.4, "exit_kind": "stop", "stop": True}
    monkeypatch.setattr(LIVE, "find_real_exit", lambda db, m, live, wid, day: sale if wid == old["watch_id"] else None)
    V3._sync_live_exits(state, market, None)
    assert old["live"][bars[71]["date"]]["exit"] == sale
    _advance(state, rows, bars, 80, market)
    attempts = old["ledger"]["campaigns"]["L97"]["attempts"]
    assert [(a.get("real"), a["exit_reason"]) for a in attempts] == [(True, "stop")]
    events = [e for e in V3._ledger_events(state, market) if e[0] == "reentry_v3.shadow_superseded"]
    assert [(e[1]["watch_id"], e[3]["reason"]) for e in events] == [(old["watch_id"], "superseded_by_newer")]
    assert not [e for e in V3._ledger_events(state, market) if e[0] == "reentry_v3.shadow_superseded"]  # once


# ---------------------------------------------------------------- runtime: enrolment rows from the trade DB
def test_runtime_newer_analysis_supersedes_and_logs_the_near_miss(tmp_path, monkeypatch):
    bars, root, sent, run, quote = T._runtime(tmp_path, monkeypatch)
    run(69)
    conn = sqlite3.connect(tmp_path / "t.sqlite")
    scenario = {"fundamental_check": {"all_passed": True},
                "trading_scenarios": {"key_levels": {k: str(v) for k, v in NEW_LEVELS.items()}}}
    conn.execute("INSERT INTO watchlist_history (ticker, company_name, analyzed_date, current_price, buy_score, "
                 "min_score, decision, skip_reason, trigger_type, scenario) VALUES (?,?,?,?,?,?,?,?,?,?)",
                 ("000001", "YC", bars[70]["date"] + " 14:50:00", 12400, 6, 7, "관망", "점수 부족", "t",
                  json.dumps(scenario)))
    conn.commit()
    conn.close()
    summary = run(70)
    assert summary["newly_enrolled"] == 1 and summary["superseded_watches"] == 1
    summary = run(70, phase="intraday", decision_day=bars[71]["date"], quote_fn=quote, llm_recheck=False)
    assert summary["new_triggers"] == 0 and summary["intraday"]["superseded_would_fire"] == 1
    near = [kw["attributes"] for n, kw in sent if n == "reentry_v3.shadow_near_miss"]
    assert [(a["source"], a["reason"]) for a in near] == [("STOP_EXIT", "superseded_by_newer")]
    assert [n for n, _ in sent].count("reentry_v3.shadow_superseded") == 1
    state = json.loads((root / "reentry_v3_state_kr.json").read_text())
    new = next(w for w in state["watches"] if w["source"] == "LOCATION_SKIP")
    assert bars[71]["date"] in new["decisions"]
