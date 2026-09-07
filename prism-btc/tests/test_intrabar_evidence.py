"""Deliberately corrupt synthetic receipts to test independent reconstruction."""
from copy import deepcopy
from dataclasses import asdict
from io import StringIO

import pytest

from analysis import intrabar_evidence as evidence
from analysis.intrabar_revalidation import StreamingCollector, registry
from backtest.intrabar_broker import Config, EntryIntent, run_replay


class Bundle:
    def regime_at(self, ts):
        return {"label": "range_normal"}


def scenario(side="long", close=True):
    prices = [(101, 102, 98, 101), (105, 109, 101, 108), (108, 112, 104, 111)]
    if side == "short":
        prices = [(200-o, 200-low, 200-h, 200-c) for o, h, low, c in prices]
    bars = [{"ts": i*300_000, "open": o, "high": h, "low": low, "close": c,
             "volume": 100., "prior_volume": 100.} for i, (o, h, low, c) in enumerate(prices)]
    marks = [{**b, "open": b["open"] + .2, "high": b["high"] + .2,
              "low": b["low"] + .2, "close": b["close"] + .2} for b in bars]
    funds = [{"ts": 300_000, "rate": .01}, {"ts": 600_000, "rate": -.002}]
    config = Config(participation=.01)
    collector = StreamingCollector(Bundle(), bars, StringIO())

    def callback(broker, ts):
        if ts == 0:
            broker.submit(EntryIntent("p", "main", side, 10, 100.,
                90. if side == "long" else 110., 110. if side == "long" else 90.,
                10, 0, 5_400_000), ts)
        elif ts == 600_000 and close:
            broker.close_parent("p", ts, "signal_exit")

    broker = run_replay(bars, funds, marks, config, callback, sink=collector.event,
                        valuation_sink=collector.valuation)
    summary, daily, _ = collector.finish(broker)
    return broker.events, summary, asdict(config), {b["ts"]: b for b in bars}, \
        {r["ts"]: r for r in funds}, {b["ts"]: b for b in marks}, daily


@pytest.mark.parametrize("side", ["long", "short"])
@pytest.mark.parametrize("close", [True, False])
def test_independent_rebuild_closed_and_open_mark_nav(side, close):
    events, summary, config, bars, funding, marks, daily = scenario(side, close)
    result = evidence.verify_ledger(events, summary, config, bars, funding, marks)
    assert result["status"] == "PASS"
    assert result["entry_lots"] == 10
    assert result["closed_parents"] == int(close)
    evidence.verify_daily(daily, summary)


@pytest.mark.parametrize("field,value,match", [
    ("lots", .1, "noninteger"), ("fee", 0., "fee_rate"),
    ("realized_price", 20., "realized_basis"), ("initial_risk", 10., "initial_risk"),
    ("price", 100.01, "non_tick"), ("action", "SELL", "direction"),
])
def test_corrupt_first_entry_receipt_rejected(field, value, match):
    events, summary, config, bars, funding, marks, _ = scenario()
    fill = next(e for e in events if e["kind"] == "fill")
    fill[field] = value
    with pytest.raises(ValueError, match=match):
        evidence.verify_ledger(events, summary, config, bars, funding, marks)


def test_omitted_funding_receipt_detected_even_with_resequenced_events():
    events, summary, config, bars, funding, marks, _ = scenario()
    events = [e for e in events if not (e["kind"] == "funding" and e["ts"] == 300_000)]
    for i, row in enumerate(events, 1):
        row["seq"] = i
    with pytest.raises(ValueError, match="funding_receipt_coverage"):
        evidence.verify_ledger(events, summary, config, bars, funding, marks)


@pytest.mark.parametrize("kind", ["parent_close", "entry_settled"])
def test_omitted_settlement_receipt_rejected(kind):
    events, summary, config, bars, funding, marks, _ = scenario()
    events = [e for e in events if e["kind"] != kind]
    for i, row in enumerate(events, 1):
        row["seq"] = i
    with pytest.raises(ValueError, match="settlement_receipt_coverage"):
        evidence.verify_ledger(events, summary, config, bars, funding, marks)


def test_current_volume_cannot_explain_previous_capacity_overfill():
    events, summary, config, bars, funding, marks, _ = scenario()
    bars[0]["prior_volume"] = 0
    with pytest.raises(ValueError, match="liquidity_exceeded"):
        evidence.verify_ledger(events, summary, config, bars, funding, marks)


def test_regime_nav_and_daily_corruption_detected():
    events, summary, config, bars, funding, marks, daily = scenario()
    summary["regimes"]["range_normal"]["interval_nav_change"] += 1
    with pytest.raises(ValueError, match="regime_nav_identity"):
        evidence.verify_ledger(events, summary, config, bars, funding, marks)
    daily[-1]["return"] += .01
    with pytest.raises(ValueError, match="daily_return_identity"):
        evidence.verify_daily(daily, summary)


def results_fixture():
    results = {}
    for case in registry():
        candidate = case["main_mode"] == "confirmed"
        daily = [{"timestamp_ms": ts + evidence.DAY_MS, "return": .001 if candidate else 0.}
                 for ts in range(case["start_ms"], case["end_ms"], evidence.DAY_MS)]
        summary = {"net_return": .5 if candidate else 0., "max_drawdown": .1,
            "yearly_returns": {str(y): .1 if candidate else 0. for y in (2022, 2023, 2024, 2025)},
            "execution": {"multi_child_parents": 1 if candidate else 0}}
        results[case["id"]] = {"case": case, "summary": summary, "daily": daily}
    return results


def test_comparison_uses_four_member_corrected_family_never_promotes():
    result = evidence.compare_results(results_fixture())
    assert result["statistics"]["family_size"] == 4
    assert result["statistics"]["draws"] == 2000
    assert result["statistics"]["seed"] == 20260907
    assert result["exploratory_verdict"] == "SUPPORTED_WITHIN_MODEL"
    assert result["profitability_status"] == "INSUFFICIENT" and result["auto_activate"] is False


def test_missing_case_rejected_and_no_additional_fill_unidentifiable():
    values = results_fixture()
    short = dict(values)
    short.pop(next(iter(short)))
    with pytest.raises(ValueError, match="incomplete_registry"):
        evidence.compare_results(short)
    for result in values.values():
        result["summary"]["execution"]["multi_child_parents"] = 0
    assert evidence.compare_results(values)["exploratory_verdict"] == "UNIDENTIFIABLE"


def test_repeat_requires_all_six_identical_artifacts(tmp_path):
    for root in ("a", "b"):
        target = tmp_path / root / "case"
        target.mkdir(parents=True)
        for name in evidence.ARTIFACTS:
            (target / name).write_text("{}\n")
    result = evidence.compare_repeats(tmp_path / "a", tmp_path / "b", ["case"])
    assert result["artifact_pairs"] == 6
    (tmp_path / "b/case/events.jsonl").write_text("[]\n")
    with pytest.raises(ValueError, match="repeat_mismatch"):
        evidence.compare_repeats(tmp_path / "a", tmp_path / "b", ["case"])


def test_unpaired_daily_grid_rejected():
    values = deepcopy(results_fixture())
    values["evaluation_joint_confirmed_BASE_OHLC"]["daily"][0]["timestamp_ms"] += 1
    with pytest.raises(ValueError, match="unpaired_daily"):
        evidence.compare_results(values)
