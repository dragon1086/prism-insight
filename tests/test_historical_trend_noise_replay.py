import copy
import hashlib
import json
from datetime import datetime, timedelta, timezone

import pandas_market_calendars as calendars
import pytest

from prism_core.historical_trend_replay import reconstructed_feature
from prism_core.noise_stop_research import complete_five_minute, run_noise_case, scheduled
from prism_core.trend_quality_research import compute_features, compute_wilder_values, digest
from tools.run_trend_noise_backtests import joined_price_evidence


def minute_bars(closes, missing=()):
    start = datetime(2026, 9, 8, 13, 0, tzinfo=timezone.utc)
    return [{"provider_timestamp": (start + timedelta(minutes=i)).isoformat(),
             "bar_close_at": (start + timedelta(minutes=i + 1)).isoformat(),
             "open": close, "high": close + 1, "low": close - 1, "close": close}
            for i, close in enumerate(closes) if i + 1 not in missing]


def run(bars):
    return run_noise_case(bars, [], entry_at="2026-09-08T12:59:00Z", cutoff="2026-09-08T13:20:00Z",
        reference_entry=100, normal_threshold=95, catastrophic_threshold=80,
        actual_exit_at="2026-09-08T13:04:00Z", actual_exit_price=94)


def test_catastrophic_lane_unchanged_and_fill_strictly_later():
    result = run(minute_bars([100] * 3 + [70] * 17))
    decisions = {a["decision_at"] for a in result["arms"].values()}
    assert decisions == {"2026-09-08T13:04:00+00:00"}
    assert all(a["reason"] == "catastrophic" for a in result["arms"].values())
    assert all(a["execution_at"] == "2026-09-08T13:05:00+00:00" for a in result["arms"].values())


def test_transient_breach_is_not_sustained_proof():
    result = run(minute_bars([100] * 3 + [90] + [100] * 16))
    assert result["arms"]["scheduled_immediate"]["decision_at"] is not None
    assert result["arms"]["completed_5m"]["decision_at"] is None
    assert result["arms"]["continuous_two_scheduled"]["decision_at"] is None


def test_continuous_decline_waits_for_confirmation():
    result = run(minute_bars([100] * 3 + [90] * 17))
    assert result["arms"]["completed_5m"]["decision_at"] == "2026-09-08T13:08:00+00:00"
    assert result["arms"]["continuous_two_scheduled"]["decision_at"] == "2026-09-08T13:08:00+00:00"
    assert result["arms"]["completed_5m"]["derived_complete_five_minute_bins"] > 0


def test_missing_between_observations_cannot_prove_continuity():
    result = run(minute_bars([100] * 3 + [90] * 17, missing=(6,)))
    assert result["arms"]["continuous_two_scheduled"]["decision_at"] == "2026-09-08T13:14:00+00:00"


def test_incomplete_five_minute_bin_is_missing():
    bars = minute_bars([90] * 10, missing=(3,))
    mapping = {datetime.fromisoformat(b["bar_close_at"]): b for b in bars}
    assert complete_five_minute(datetime(2026, 9, 8, 13, 8, tzinfo=timezone.utc), mapping, {})[0] is None


def test_cost_sensitivity_is_monotonic_and_unfilled_exit_is_explicit():
    result = run(minute_bars([100] * 3 + [90]))
    baseline = result["arms"]["scheduled_immediate"]
    assert baseline["valuation"] == "MISSING_NEXT_EXECUTABLE_BAR_MARK_ONLY"
    costs = baseline["return_pct_by_cost_bps_each_side"]
    assert costs["0"] > costs["10"] > costs["30"]


def test_cron_daylight_saving_and_weekend():
    assert scheduled(datetime(2026, 9, 8, 13, 4, tzinfo=timezone.utc))
    assert scheduled(datetime(2026, 12, 8, 14, 4, tzinfo=timezone.utc))
    assert not scheduled(datetime(2026, 9, 12, 13, 4, tzinfo=timezone.utc))


def test_mfe_mae_include_entry_anchor_but_retain_raw_window_extrema():
    below = run(minute_bars([90] * 20))["arms"]["scheduled_immediate"]
    assert below["mfe_pct"] == 0
    assert below["observed_window_max_excursion_pct"] < 0
    assert below["mae_pct"] < 0
    above = run(minute_bars([110] * 20))["arms"]["scheduled_immediate"]
    assert above["mae_pct"] == 0
    assert above["observed_window_min_excursion_pct"] > 0
    assert above["mfe_pct"] > 0


def historical_fixture():
    schedule = calendars.get_calendar("NASDAQ").schedule("2026-04-01", "2026-09-08").tail(60)
    bars = [{"high": 102 + i, "low": 99 + i, "close": 100 + i,
             "bar_close_at": s["market_close"].isoformat(), "session_date": day.date().isoformat(),
             "stock_splits": 0, "dividends": 0}
            for i, (day, s) in enumerate(schedule.iterrows())]
    candidate = {"eligible_for_analysis": True, "trigger_type": "Intraday Rise Top",
                 "decided_at": "2026-09-09T13:00:00Z"}
    dataset = {"bars": bars, "calendar": "NASDAQ", "retrieved_at": "2026-09-10T00:00:00Z",
               "source_sha256": digest(bars)}
    return candidate, dataset


def test_reconstructed_features_use_pure_core_without_faking_observed_at():
    candidate, dataset = historical_fixture()
    result = reconstructed_feature(candidate, dataset)
    assert result["status"] == "OK"
    pure = compute_wilder_values([(b["high"], b["low"], b["close"]) for b in dataset["bars"]])
    assert result["adx14"] == pure["adx14"]
    assert "observed_at" not in result
    assert result["retrieved_at"] > candidate["decided_at"]


@pytest.mark.parametrize("change,reason", [
    (lambda d: d["bars"][10].update(stock_splits=2), "SPLIT_OR_ACTION_STATUS_UNRESOLVED"),
    (lambda d: d["bars"].pop(10), "WARMUP_REQUIRES_60_COMPLETED_BARS"),
])
def test_reconstructed_actions_and_missing_bars_fail_closed(change, reason):
    candidate, dataset = historical_fixture()
    change(dataset)
    assert reason in reconstructed_feature(candidate, dataset)["reasons"]


def test_existing_observed_snapshot_guard_still_rejects_future_observation():
    _, dataset = historical_fixture()
    bars = [{"high": b["high"], "low": b["low"], "close": b["close"],
             "close_at": b["bar_close_at"], "completed": True} for b in dataset["bars"]]
    snapshot = {"bar_interval": "1d", "gaps_checked": True, "adjustment_policy": "no_actions_verified",
                "data_quality_flags": [], "bars": bars, "source_hash": digest(bars),
                "decided_at": "2026-09-09T13:00:00Z", "observed_at": "2026-09-10T00:00:00Z"}
    assert "FUTURE_OBSERVATION" in compute_features(snapshot)["reasons"]


def test_supplement_must_match_exact_original_entry_context():
    original = {"analysis_rows": [{"decision_ref": "ref", "ticker": "SNDK", "decided_at": "now",
        "entry": {"observed": True, "position_ref": "p"}, "outcomes": {"strategy_entry_at": "entry"}}]}
    supplement = copy.deepcopy(original)
    supplement["analysis_rows"][0]["outcomes"]["strategy_entry_at"] = "other"
    with pytest.raises(ValueError, match="supplementary_entry_mismatch"):
        joined_price_evidence(original, supplement)


def test_training_purges_trade_closed_inside_embargo(monkeypatch):
    from prism_core import historical_trend_replay as historical
    feature = {"status": "OK", "reasons": [], "adx14": 25, "plus_di14": 30,
               "minus_di14": 10, "adx_rising_3": True, "er20": .5, "net20": 5}
    monkeypatch.setattr(historical, "reconstructed_feature", lambda *args: feature)
    dates = ("2026-06-01", "2026-06-15", "2026-08-01", "2026-09-01")
    closes = ("2026-07-05", "2026-06-20", "2026-08-02", "2026-09-02")
    rows = [{"decision_ref": str(i), "ticker": "AAA", "decided_at": day + "T14:00:00Z",
             "trigger_type": "Intraday Rise Top", "entry": {"observed": True},
             "decision": {"decision": "entry", "gate_allowed": True},
             "outcomes": {"strategy_entry_at": day + "T14:00:01Z",
                          "strategy_closed_at": closed + "T15:00:00Z", "strategy_return_pct": 1}}
            for i, (day, closed) in enumerate(zip(dates, closes))]
    source = {"kind": "RECONSTRUCTED_REPLAY", "datasets": [{
        "request": {"market": "US", "ticker": "AAA", "interval": "1d"}, "bars": []}]}
    result = historical.build_historical_study([{"market": "US", "analysis_rows": rows}], source)
    assert result["holdout_boundary"] == "2026-08-01"
    assert result["train_rows_after_30_day_embargo"] == 1  # June 1 entry closes too late.


def test_registered_price_change_rejected_even_with_same_identity(tmp_path, monkeypatch):
    from tools import run_trend_noise_backtests as runner
    packet = {"analysis_rows": [{"decision_ref": "same", "ticker": "SNDK",
              "entry": {"price_evidence": {"reference_price": 1794.175}}}]}
    path = tmp_path / "price-packet.json"
    raw = json.dumps(packet).encode()
    path.write_bytes(raw)
    monkeypatch.setattr(runner, "FROZEN_SUPPLEMENT_FILE_SHA256", hashlib.sha256(raw).hexdigest())
    monkeypatch.setattr(runner, "FROZEN_SUPPLEMENT_CANONICAL_SHA256", runner.data.sha(packet))
    assert runner.read_frozen_price_packet(path) == packet
    packet["analysis_rows"][0]["entry"]["price_evidence"]["reference_price"] = 1800
    path.write_text(json.dumps(packet))
    with pytest.raises(ValueError, match="registered_price_packet_file_changed"):
        runner.read_frozen_price_packet(path)
    with pytest.raises(ValueError, match="registered_price_packet_content_changed"):
        runner.studies({}, {}, [], packet, None, None)
