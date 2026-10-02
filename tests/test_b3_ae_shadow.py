"""v3-ae: B3 for every actual entry (KR/US) — policy, virtual ledger, KR intraday inputs."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from prism_core import oneil_adaptive_policy as P
from prism_core.b3_ae_shadow import B3AeShadowStore, campaign_returns, plan_for_entry
from prism_core.oneil_intraday_inputs import build_intraday_inputs

SEOUL, NY = ZoneInfo("Asia/Seoul"), ZoneInfo("America/New_York")


def _iso(local):
    return local.astimezone(timezone.utc).isoformat()


def kr_plan(entry=10000, stop=9300, atr=300, entered=datetime(2026, 10, 2, 9, 35, tzinfo=SEOUL)):
    return plan_for_entry(
        market="KR", symbol="005930", entry_price=entry, initial_stop=stop, decision_ref="report:x.pdf",
        entered_at=_iso(entered), atr14=atr, atr14_source_ref="kis-daily", atr14_as_of=_iso(entered - timedelta(minutes=5)),
        atr14_last_trade_date="2026-10-01", price_basis_ref="kis-raw")


def kr_evidence(plan, *, closes=(10450, 10460), price=10460, day=date(2026, 10, 5), minute=60,
                regime="moderate_bull", pulse="UPTREND", zone=SEOUL, open_hm=(9, 0), close_hm=(15, 30)):
    opened = datetime(day.year, day.month, day.day, *open_hm, tzinfo=zone)
    closed = datetime(day.year, day.month, day.day, *close_hm, tzinfo=zone)
    now = opened + timedelta(minutes=minute)
    bars = []
    for index, close in enumerate(closes):
        start = now - timedelta(minutes=5 * (len(closes) - index))
        bars.append(dict(start_at=_iso(start), end_at=_iso(start + timedelta(minutes=5)), close=str(close),
                         complete=True, regular=True, source_ref="kis-1m"))
    trend_days = [(day - timedelta(days=d)).isoformat() for d in range(28, 0, -1)
                  if (day - timedelta(days=d)).weekday() < 5][-20:]
    evidence = dict(
        contract_version=P.evidence_version(plan), symbol=plan["symbol"], source="mechanical",
        price_basis_ref=plan["setup"]["price_basis_ref"], source_ref="ev",
        quote=dict(price=str(price), observed_at=_iso(now), source_ref="kis-quote"),
        gates=dict(observed_at=_iso(now), source_ref="g", admission=True, risk=True, RR=True, sector=True,
                   slot=True, market_pulse=pulse, regime=regime),
        market_window=dict(trade_date=day.isoformat(), open_at=_iso(opened), close_at=_iso(closed),
                           verified=True, source_ref="XKRX"),
        bars=bars, volume=None,
        trend=dict(basis="COMPLETED_DAILY_CLOSE_SMA20",
                   as_of=_iso(datetime.fromisoformat(trend_days[-1]).replace(hour=15, minute=30, tzinfo=zone)),
                   trade_dates=trend_days, closes=[str(9000 + 50 * i) for i in range(20)],
                   calendar_ref="XKRX", source_ref="kis-daily"))
    return evidence, _iso(now)


def test_kr_plan_uses_seoul_session_and_b3_initial_sizing():
    plan = kr_plan()
    assert plan["market"] == "KR" and plan["policy_version"] == P.V3_AE_VERSION
    # stop proxy = 1.5*300/10000 = 4.5% -> 0.5*0.07/0.045
    assert Decimal(plan["initial_nominal"]) == Decimal("0.7777")
    P._validate(plan)
    with pytest.raises(ValueError):  # ATR from today's (same) Seoul session
        plan_for_entry(market="KR", symbol="005930", entry_price=10000, initial_stop=9300, decision_ref="r",
                       entered_at=_iso(datetime(2026, 10, 2, 9, 35, tzinfo=SEOUL)), atr14=300,
                       atr14_source_ref="k", atr14_as_of=_iso(datetime(2026, 10, 2, 9, 30, tzinfo=SEOUL)),
                       atr14_last_trade_date="2026-10-02", price_basis_ref="kis-raw")
    with pytest.raises(ValueError):
        P.create_plan(symbol="005930", entry_reference=10000, initial_stop=9300, source_decision_ref="r",
                      created_at=plan["created_at"], setup=dict(plan["setup"], pivot="9900"),
                      entry_eligible=True, policy_version=P.V3_AE_VERSION, market="KR")
    with pytest.raises(ValueError):
        P.create_plan(symbol="X", entry_reference=10000, initial_stop=9300, source_decision_ref="r",
                      created_at=plan["created_at"], setup=plan["setup"], entry_eligible=True,
                      policy_version=P.V3_AE_VERSION, market="JP")


def test_v2_plans_stay_us_only():
    with pytest.raises(ValueError):
        P.create_plan(symbol="X", entry_reference=100, initial_stop=93, source_decision_ref="r",
                      created_at="2026-10-02T14:00:00+00:00", setup={}, entry_eligible=True, market="KR")


def test_ledger_adds_on_b3_ladder_with_risk_clip_once_per_bar(tmp_path):
    store = B3AeShadowStore(tmp_path / "b3.sqlite")
    plan = kr_plan()
    state = store.open_campaign(account_key="acc", position_id="legacy:KR:7", plan=plan, entered_at=plan["created_at"])
    assert store.open_campaign(account_key="acc", position_id="legacy:KR:7", plan=plan,
                               entered_at=plan["created_at"]) == state  # idempotent
    evidence, now = kr_evidence(plan)
    decision = store.evaluate(state["campaign_id"], evidence, now=now, current_stop=9300)
    assert decision["action"] == "ADD" and decision["risk_clipped"] is True
    after = store.snapshot(state["campaign_id"])
    assert [leg["kind"] for leg in after["legs"]] == ["INITIAL", "ADD"]
    assert Decimal("0.9") < sum(Decimal(leg["allocation"]) for leg in after["legs"]) < Decimal("0.91")
    again = store.evaluate(state["campaign_id"], evidence, now=now, current_stop=9300)
    assert again["action"] == "WAIT"  # the same completed bar never adds twice

    closed = store.close(market="KR", account_key="acc", position_id="legacy:KR:7",
                         exit_price=11000, exit_at=now, reason="TIER3")
    assert closed["status"] == "CLOSED" and Decimal(closed["returns"]["baseline"]) == Decimal("0.1")
    assert Decimal(closed["returns"]["b3"]) < Decimal("0.1")
    assert store.close(market="KR", account_key="acc", position_id="legacy:KR:7", exit_price=1,
                       exit_at=now, reason="x")["exit"]["price"] == "11000"  # idempotent


@pytest.mark.parametrize("kwargs, reason", [
    (dict(regime="sideways"), "ADD_GATE_NOT_MET"),
    (dict(pulse="UNDER_PRESSURE"), "ADD_GATE_NOT_MET"),
    (dict(closes=(10050, 10060), price=10060), "TARGET_ALREADY_REACHED"),
    (dict(zone=NY, open_hm=(9, 30), close_hm=(16, 0)), "ADD_EVIDENCE_REJECTED"),  # US session for a KR plan
])
def test_ledger_waits_when_b3_conditions_fail(tmp_path, kwargs, reason):
    store = B3AeShadowStore(tmp_path / "b3.sqlite")
    plan = kr_plan()
    state = store.open_campaign(account_key="acc", position_id="p", plan=plan, entered_at=plan["created_at"])
    evidence, now = kr_evidence(plan, **kwargs)
    decision = store.evaluate(state["campaign_id"], evidence, now=now, current_stop=9300)
    assert decision["action"] == "WAIT" and decision["reason"] == reason


def test_campaign_returns_weight_each_leg():
    plan = kr_plan()
    state = dict(plan=plan, legs=[dict(kind="INITIAL", allocation="0.5", price="10000"),
                                  dict(kind="ADD", allocation="0.5", price="10400")])
    returns = campaign_returns(state, 10920)
    assert Decimal(returns["b3"]) == Decimal("0.5") * Decimal("0.092") + Decimal("0.5") * (Decimal(10920) / 10400 - 1)
    assert Decimal(returns["b3_after_add_cost"]) == Decimal(returns["b3"]) - Decimal("0.00125")


def test_kr_intraday_inputs_today_bars_plus_daily_trend():
    day = date(2026, 10, 5)
    sessions, closes, cursor = [], {}, day
    while len(sessions) < 21:
        if cursor.weekday() < 5:
            opened = datetime(cursor.year, cursor.month, cursor.day, 9, tzinfo=SEOUL)
            sessions.insert(0, dict(trade_date=cursor.isoformat(), open_at=_iso(opened),
                                    close_at=_iso(opened + timedelta(minutes=390))))
            closes[cursor.isoformat()] = 9000 + len(sessions)
        cursor -= timedelta(days=1)
    opened = datetime(day.year, day.month, day.day, 9, tzinfo=SEOUL)
    now = opened + timedelta(minutes=30)
    bars = [dict(provider_timestamp=_iso(opened + timedelta(minutes=5 * i)), open=100, high=110, low=99,
                 close=100 + i, volume=10, dividends=0, stock_splits=0) for i in range(6)]
    result = build_intraday_inputs(symbol="005930", bars=bars, calendar=dict(calendar_ref="XKRX", sessions=sessions),
                                   as_of=_iso(now), retrieved_at=_iso(now + timedelta(seconds=5)),
                                   retrieval_started_at=_iso(now + timedelta(seconds=1)),
                                   price_basis_ref="kis-raw", source_ref="kis-1m", kind="LIVE_CAPTURE",
                                   volume_required=False, market="KR", prior_session_closes=closes)
    assert result["status"] == "OK", result["reason_codes"]
    assert [b["close"] for b in result["bars"]] == ["104", "105"]
    assert len(result["trend"]["closes"]) == 20 and result["market_window"]["source_ref"] == "XKRX"
    gap = build_intraday_inputs(symbol="005930", bars=bars[:3] + bars[4:], calendar=dict(calendar_ref="XKRX", sessions=sessions),
                                as_of=_iso(now), retrieved_at=_iso(now + timedelta(seconds=5)),
                                retrieval_started_at=_iso(now + timedelta(seconds=1)),
                                price_basis_ref="kis-raw", source_ref="kis-1m", kind="LIVE_CAPTURE",
                                volume_required=False, market="KR", prior_session_closes=closes)
    assert gap["reason_codes"] == ["REGULAR_PREFIX_GAP"]  # never filled
