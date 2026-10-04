"""Trigger-quality priority in US final selection (mirror of the KR tests)."""
import sqlite3

import pytest

us_trigger_batch = pytest.importorskip("us_trigger_batch")
pd = us_trigger_batch.pd

from prism_core import trigger_quality as tq  # noqa: E402

WEIGHTS = {"Volume Surge Top": 0.86, "Gap Up Momentum Top": 1.05, "Macro Sector Leader": 0.89}


@pytest.fixture(autouse=True)
def _sector_map(monkeypatch):
    monkeypatch.setattr(us_trigger_batch, "get_us_sector_map",
                        lambda tickers: {ticker: "Technology" for ticker in tickers})
    monkeypatch.setattr(us_trigger_batch, "_get_regime_slots", lambda _regime: (0, 2))


def _frame(rows):
    return pd.DataFrame({"CompositeScore": [s for _, s in rows],
                         "CompanyName": [t for t, _ in rows]}, index=[t for t, _ in rows])


def _morning():
    return {
        "Volume Surge Top": _frame([("VST1", 0.62)]),
        "Gap Up Momentum Top": _frame([("GAP1", 0.70), ("GAP2", 0.60)]),
        "Macro Sector Leader": _frame([("MAC1", 0.58)]),
    }


def _select(triggers, weights):
    result = us_trigger_batch.select_final_tickers(
        triggers, use_hybrid=False, trigger_weights=weights,
        macro_context={"market_regime": "sideways", "leading_sectors": []})
    return [(ticker, name) for name, frame in result.items() for ticker in frame.index]


def test_us_weak_trigger_loses_guarantee_and_strong_wins_close_call():
    assert sorted(_select(_morning(), None)) == [
        ("GAP1", "Gap Up Momentum Top"), ("VST1", "Volume Surge Top")]
    assert sorted(_select(_morning(), WEIGHTS)) == [
        ("GAP1", "Gap Up Momentum Top"), ("GAP2", "Gap Up Momentum Top")]


def test_us_weak_trigger_still_picked_when_best_available():
    triggers = _morning()
    triggers["Macro Sector Leader"] = _frame([("MAC1", 0.99)])
    assert ("MAC1", "Macro Sector Leader") in _select(triggers, WEIGHTS)


def test_us_neutral_weights_keep_legacy_selection():
    legacy = _select(_morning(), None)
    assert _select(_morning(), {}) == legacy
    assert _select(_morning(), {name: 1.0 for name in _morning()}) == legacy


def test_us_capacity_fill_keeps_its_last_position():
    triggers = _morning()
    triggers["Value-to-Cap Ratio Top"] = _frame([("V2C1", 0.99)])
    triggers["Value-to-Cap Ratio Top"]["CapacityFill"] = True
    weights = dict(WEIGHTS, **{"Volume Surge Top": 1.0, "Macro Sector Leader": 1.0})
    # Neutral capacity fill stays behind the primary triggers in the guaranteed pass.
    assert sorted(_select(triggers, weights)) == [
        ("GAP1", "Gap Up Momentum Top"), ("VST1", "Volume Surge Top")]


def test_us_selection_evidence_records_the_displaced_legacy_pick():
    """Two-week review evidence: VST1 lost its guaranteed slot to GAP2."""
    diagnostics = {}
    us_trigger_batch.select_final_tickers(
        _morning(), use_hybrid=False, trigger_weights=WEIGHTS, selection_diagnostics=diagnostics,
        macro_context={"market_regime": "sideways", "leading_sectors": []})
    record = diagnostics["trigger_quality"]
    assert [(c["ticker"], c["trigger"], c["weight"]) for c in record["displaced"]] == [
        ("VST1", "Volume Surge Top", 0.86)]
    assert [c["ticker"] for c in record["fill_picks"]] == ["GAP2"]
    assert record["excluded_triggers"] == ["Volume Surge Top", "Macro Sector Leader"]
    neutral = {}
    us_trigger_batch.select_final_tickers(
        _morning(), use_hybrid=False, trigger_weights={}, selection_diagnostics=neutral,
        macro_context={"market_regime": "sideways", "leading_sectors": []})
    assert neutral["trigger_quality"] is None


def test_us_topdown_pool_scales_by_trigger_weight():
    triggers = {
        "Macro Sector Leader": pd.DataFrame({"FinalScore": [0.80]}, index=["MAC1"]),
        "Intraday Rise Top": pd.DataFrame({"FinalScore": [0.74]}, index=["INT1"]),
    }
    macro = {"leading_sectors": [{"sector": "Technology", "confidence": 0.5}]}
    sectors = {"MAC1": "Technology", "INT1": "Technology"}
    assert us_trigger_batch._build_topdown_pool(triggers, macro, "FinalScore", sectors)[0][0] == "MAC1"
    weighted = us_trigger_batch._build_topdown_pool(
        triggers, macro, "FinalScore", sectors, {"Macro Sector Leader": 0.89, "Intraday Rise Top": 1.10})
    assert weighted[0][0] == "INT1"


def test_us_history_tables_feed_weights(tmp_path, monkeypatch):
    monkeypatch.delenv("TRIGGER_QUALITY_PRIORITY", raising=False)
    db = tmp_path / "db.sqlite"
    connection = sqlite3.connect(db)
    connection.executescript("""
        CREATE TABLE us_analysis_performance_tracker (id INTEGER PRIMARY KEY, trigger_type TEXT,
          analysis_date TEXT, return_7d REAL, return_14d REAL, return_30d REAL);
        CREATE TABLE us_trading_history (id INTEGER PRIMARY KEY, trigger_type TEXT, sell_date TEXT,
          profit_rate REAL, scenario TEXT);
    """)
    connection.executemany(
        "INSERT INTO us_analysis_performance_tracker (trigger_type, analysis_date, return_7d, return_14d, return_30d) "
        "VALUES (?, '2026-08-01 03:00:00', 0, 0, ?)",
        [("Intraday Rise Top", 0.35)] * 40 + [("Volume Surge Top", 0.01)] * 40)
    connection.executemany(
        "INSERT INTO us_trading_history (trigger_type, sell_date, profit_rate, scenario) "
        "VALUES (?, '2026-08-10 03:00:00', ?, NULL)",
        [("Intraday Rise Top", 4.0)] * 20 + [("Volume Surge Top", -3.0)] * 20)
    connection.commit()
    connection.close()
    tq._CACHE.clear()
    snapshot = tq.load_trigger_quality("US", "20260914", db_path=db, use_cache=False)
    assert snapshot.status == "ok"
    assert snapshot.weight("Intraday Rise Top") > 1.0 > tq.GUARANTEE_FLOOR >= snapshot.weight("Volume Surge Top")
