"""Public re-entry watch list: only buyable watches, no prices, unknown is never shown as empty."""
import json

import pytest

from tools import export_reentry_watch as E


def watch(ticker, source="LOCATION_SKIP", status="ACTIVE", rule="L97", eligible=True, level=100.0, elapsed=10,
          enrolled="2026-11-10", window=None):
    return {"ticker": ticker, "source": source, "status": status, "enrolled_at": enrolled,
            "row": {"company_name": ticker + " Inc"}, "setup": {"L": level, "anchor_date": enrolled},
            "ledger": {"elapsed": elapsed, "next": {rule: {"eligible": eligible, "window": window}}}}


def bars(close, n=25):
    return [{"date": str(i), "close": close} for i in range(n)]


def build(market, watches, rows, held=()):
    state = {"last_completed": "2026-11-24", "watches": watches}
    return E.build(market, state, "2026-11-24", set(held), lambda tickers: rows)


@pytest.fixture(autouse=True)
def us_ma20r(monkeypatch):
    monkeypatch.setenv("REENTRY_V3_MA20R_LIVE_MARKETS", "US")


def test_only_watches_the_live_rule_can_buy():
    out = build("KR", [watch("A"), watch("B", eligible=False), watch("C", status="CLOSED"), watch("D")],
                {"A": bars(99.0)}, held=["D"])
    assert [i["ticker"] for i in out["items"]] == ["A"]


def test_us_ignores_records_of_the_level_rule():
    out = build("US", [watch("OLD", rule="L97"), watch("NEW", rule="MA20R")], {"NEW": bars(50.0)})
    assert [i["ticker"] for i in out["items"]] == ["NEW"]


def test_duplicate_ticker_keeps_the_stop_exit():
    out = build("KR", [watch("A", source="STOP_EXIT"), watch("A", enrolled="2026-11-20")], {})
    assert [(i["ticker"], i["source"]) for i in out["items"]] == [("A", "stop_exit")]


def test_us_distance_uses_ma20_not_the_skip_level():
    rows = bars(100.0, 24) + [{"date": "24", "close": 80.0}]
    out = build("US", [watch("A", rule="MA20R", level=80.0)], {"A": rows})
    assert out["items"][0]["proximity"] == "far"


def test_kr_shakeout_uses_reclaim_level_and_its_own_deadline():
    window = {"R": 80.0, "sessions_left": 1}
    out = build("KR", [watch("A", window=window)], {"A": bars(79.0)})
    assert (out["items"][0]["proximity"], out["items"][0]["sessions_left"]) == ("near", 1)


def test_missing_bars_stay_unknown_and_sort_last():
    out = build("KR", [watch("A"), watch("B")], {"B": bars(50.0)})
    assert [(i["ticker"], i["proximity"]) for i in out["items"]] == [("B", "far"), ("A", None)]


def test_stale_state_is_unknown_not_empty():
    state = {"last_completed": "2026-11-23", "watches": [watch("A")]}
    assert E.build("KR", state, "2026-11-24", set(), lambda t: {})["items"] is None


def test_no_price_is_published():
    out = build("KR", [watch("A", level=123456.0)], {"A": bars(123457.0)})
    assert "12345" not in json.dumps(out["items"])
    assert set(out["items"][0]) == {"ticker", "name", "source", "since", "sessions_left", "proximity"}
