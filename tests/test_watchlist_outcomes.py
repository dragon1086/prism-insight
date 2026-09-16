import json
from copy import deepcopy
from datetime import date, timedelta

import pytest

from observability import watchlist_outcomes as observer
from prism_core.watchlist_outcomes import evaluate, new_anchor
from tools.build_watchlist_outcome_evidence_packet import build_packet


def fixture(market="US", count=22):
    dates = [(date(2026, 8, 3) + timedelta(days=i)).isoformat() for i in range(count)]
    symbol, benchmark = ("ABC", "SPY") if market == "US" else ("005930", "1001")
    rows = [{"date": d, "open": 100, "high": 112, "low": 95, "close": 110, "volume": 10} for d in dates]
    frames = {symbol: rows, benchmark: [{**r, "close": 105} for r in rows], "__market_days": dates,
              "__benchmarks": {symbol: benchmark}, "__expected_completed_date": dates[-1]}
    row = {"watch_id": "w", "ticker": symbol, "status": "EXPIRED", "seeded_trade_date": "20260803", "policy_version": "test"}
    anchor = new_anchor(market, row, "BASELINE", "2026-08-03T15:00:00+00:00", "hash", "batch")
    return row, anchor, frames


def test_exact_horizons_and_costs_no_buy_required():
    _, anchor, frames = fixture()
    updated, events = evaluate(anchor, frames, frames)
    assert updated["done"]
    assert updated["reference_date"] == "2026-08-04"
    assert len(events) == 5
    for event in events:
        assert event["status"] == "COMPLETE"
        assert event["gross_return"] == pytest.approx(.1)
        assert event["relative_return"] == pytest.approx(.05)
        assert event["mfe"] == pytest.approx(.12)
        assert event["mae"] == pytest.approx(-.05)
        assert event["hypothetical_roundtrip_cost_returns"]["50"] == pytest.approx(.095)


def test_no_same_day_open_or_prior_close():
    _, anchor, frames = fixture(count=1)
    _, events = evaluate(anchor, frames, frames)
    assert events[0]["status"] == "PENDING"
    _, anchor, frames = fixture("KR")
    assert anchor["observed_local_date"] == "2026-08-04"  # UTC -> KST midnight
    updated, _ = evaluate(anchor, frames, frames)
    assert updated["reference_date"] == "2026-08-05"


def test_missing_anchor_never_next_available_and_calendar_required():
    _, anchor, frames = fixture()
    frames["ABC"] = [r for r in frames["ABC"] if r["date"] != "2026-08-04"]
    _, events = evaluate(anchor, frames, frames)
    assert events[0]["status"] == "MISSING"
    frames.pop("__market_days")
    _, events = evaluate(anchor, frames, {})
    assert events[0]["status"] == "MISSING"


def test_basis_revision_not_rewritten():
    _, anchor, frames = fixture()
    revised = deepcopy(frames)
    revised["ABC"][0]["close"] = 109
    updated, events = evaluate(anchor, revised, frames)
    assert updated["done"] and events[0]["status"] == "BASIS_CHANGED"


def test_registry_duplicate_terminal_and_market_isolation(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(observer, "emit_event", lambda *a, **kw: sent.append(kw) or "ok")
    row, _, frames = fixture()
    row["delivery_observation_payload"] = {"observed_at": "2026-08-03T15:00:00+00:00"}
    path = tmp_path / "us.json"
    first = observer.observe_outcomes("US", [row], frames, "b", "20260825", path)
    assert sum(e["status"] == "COMPLETE" for e in first) == 5
    second = observer.observe_outcomes("US", [row], frames, "b2", "20260825", path)
    assert second == []
    assert observer.observe_outcomes("KR", [row], frames, "b", "20260825", path) is None
    assert observer.pending_symbols("US", path) == []
    assert any(path.with_suffix(".archive").glob("*.json"))


def test_archive_failure_fail_open(tmp_path, monkeypatch):
    monkeypatch.setattr(observer, "_archive", lambda *a: (_ for _ in ()).throw(OSError("disk")))
    row, _, frames = fixture()
    assert observer.observe_outcomes("US", [row], frames, "b", "20260825", tmp_path / "s.json") is None


def test_legacy_late_enrollment_and_fair_queue(tmp_path, monkeypatch):
    row, anchor, _ = fixture()
    row["seeded_trade_date"] = "20260701"
    late = new_anchor("US", row, "BASELINE", "2026-08-03T15:00:00+00:00", "h", "b")
    assert late["enrollment"] == "LATE_ENROLLMENT"
    state = {"market": "US", "version": observer.VERSION, "anchors": [dict(anchor, ticker="A", last_attempt="z"), dict(anchor, ticker="B", last_attempt="a")]}
    monkeypatch.setattr(observer, "_load", lambda *a: state)
    assert observer.pending_symbols("US", tmp_path / "x") == ["B", "A"]


def test_packet_is_stable_and_does_not_pool_markets(tmp_path, monkeypatch):
    monkeypatch.setattr(observer, "emit_event", lambda *a, **kw: "ok")
    events = []
    for market in ("US", "KR"):
        row, _, frames = fixture(market)
        row["delivery_observation_payload"] = {"observed_at": "2026-08-03T15:00:00+00:00"}
        events.extend(observer.observe_outcomes(market, [row], frames, "b", "20260825", tmp_path / (market + ".json")))
    packet = build_packet(events)
    assert packet == build_packet(list(reversed(events)) + events)
    assert packet["verdict"] == "CONTINUE_CAPTURE"
    assert packet["markets"]["US"]["currency"] == "USD"
    assert packet["markets"]["KR"]["currency"] == "KRW"
    assert packet["markets"]["US"]["mature_20_session_lifetimes"] == 1
    changed = deepcopy(events[0]); changed["trigger"] = "conflict"
    assert build_packet(events + [changed])["conflicting_event_ids"]


def test_real_sanitized_spool_exact_ids_and_regime(tmp_path, monkeypatch):
    spool = tmp_path / "events.jsonl"
    monkeypatch.setenv("PRISM_OBSERVABILITY_SPOOL", str(spool))
    row, _, frames = fixture()
    row["delivery_observation_payload"] = {"observed_at": "2026-08-03T15:00:00+00:00"}
    row["regime_context"] = {"market_regime": "weak", "source": "batch", "asof": "2026-08-03"}
    observer.observe_outcomes("US", [row], frames, "b", "20260825", tmp_path / "us.json")
    events = [json.loads(line) for line in spool.read_text().splitlines()]
    assert all(e["event_id"] == e["attributes"]["event_id"] for e in events)
    packet = build_packet(events)
    assert packet["markets"]["US"]["strata"][0]["regime"] == "weak"
    assert packet["source_event_ids"] == sorted(e["event_id"] for e in events)
    assert all(e["attributes"]["trading_impact"] == "none" for e in events)


@pytest.mark.parametrize("deferred", [False, True])
def test_permanent_missing_expires_registry_after_twenty_days(tmp_path, monkeypatch, deferred):
    monkeypatch.setattr(observer, "emit_event", lambda *a, **kw: "ok")
    row, _, frames = fixture()
    row["delivery_observation_payload"] = {"observed_at": "2026-08-03T15:00:00+00:00"}
    frames.pop("ABC")
    if deferred:
        frames["__deferred_symbols"] = ["ABC"]
    path = tmp_path / "state.json"
    events = observer.observe_outcomes("US", [row], frames, "b", "20260825", path)
    assert len([e for e in events if e["status"] == "MISSING"]) == 5
    assert observer.pending_symbols("US", path) == []


def test_registry_cap_is_explicit_and_outbox_retries(tmp_path, monkeypatch):
    monkeypatch.setattr(observer, "MAX_ANCHORS", 0)
    monkeypatch.setattr(observer, "emit_event", lambda *a, **kw: None)
    row, _, frames = fixture()
    path = tmp_path / "state.json"
    events = observer.observe_outcomes("US", [row], frames, "b", "20260825", path)
    assert events[0]["status"] == "EXCLUDED"
    assert json.loads(path.read_text())["outbox"]
    monkeypatch.setattr(observer, "emit_event", lambda *a, **kw: "ok")
    assert observer.observe_outcomes("US", [row], frames, "b", "20260825", path) == []
    assert json.loads(path.read_text())["outbox"] == []


def test_restart_after_registry_write_before_retirement_archive(tmp_path, monkeypatch):
    monkeypatch.setattr(observer, "emit_event", lambda *a, **kw: "ok")
    row, _, frames = fixture()
    row["delivery_observation_payload"] = {"observed_at": "2026-08-03T15:00:00+00:00"}
    path = tmp_path / "state.json"
    atomic = observer._atomic

    def crash(path, value):
        if str(path).endswith(".done.json"):
            raise OSError("simulated_crash_after_registry_commit")
        atomic(path, value)

    monkeypatch.setattr(observer, "_atomic", crash)
    assert observer.observe_outcomes("US", [row], frames, "b", "20260825", path) is None
    assert json.loads(path.read_text())["retirements"]
    monkeypatch.setattr(observer, "_atomic", atomic)
    row["delivery_observation_payload"]["observed_at"] = "2026-08-25T15:00:00+00:00"
    assert observer.observe_outcomes("US", [row], frames, "b2", "20260825", path) == []
    assert json.loads(path.read_text())["anchors"] == []


def test_more_than_eighty_unrequested_symbols_keep_priority(tmp_path, monkeypatch):
    monkeypatch.setattr(observer, "emit_event", lambda *a, **kw: "ok")
    row, _, frames = fixture(count=1)
    observations = [{**row, "watch_id": str(i), "ticker": f"T{i:03d}",
                     "delivery_observation_payload": {"observed_at": "2026-08-03T15:00:00+00:00"}} for i in range(85)]
    frames["__requested_symbols"] = ["T000"]
    frames["T000"] = frames["ABC"]
    path = tmp_path / "state.json"
    observer.observe_outcomes("US", observations, frames, "b", "20260803", path)
    state = json.loads(path.read_text())
    assert len(state["anchors"]) == 85
    assert all("last_attempt" not in a for a in state["anchors"] if a["ticker"] != "T000")
    assert "T000" not in observer.pending_symbols("US", path)


def test_expected_denominator_keeps_missing_excluded_and_legacy(tmp_path, monkeypatch):
    monkeypatch.setattr(observer, "emit_event", lambda *a, **kw: "ok")
    monkeypatch.setattr(observer, "MAX_ANCHORS", 0)
    row, _, frames = fixture()
    excluded = observer.observe_outcomes("US", [row], frames, "b", "20260825", tmp_path / "us.json")
    expected = [{"event_type": "watchlist.shadow_evaluated", "event_id": name, "market": "US",
                 "attributes": {"watch_ref": name, "status": "READY", "outcome_collection_expected": True,
                                "outcome_contract_version": observer.VERSION}} for name in ("w", "missing")]
    legacy = {"event_type": "watchlist.shadow_evaluated", "event_id": "old", "market": "US", "attributes": {"watch_ref": "old"}}
    packet = build_packet(excluded + expected + [legacy])
    coverage = packet["markets"]["US"]["registration_coverage"]
    assert coverage["total_anchors"] == 4
    assert coverage["registered"] == 0 and coverage["excluded"] == 1
    assert coverage["missing_registration"] == 3
    assert coverage["legacy_outside_contract_lifetimes"] == 1
    assert packet == build_packet(list(reversed(excluded + expected + [legacy])))


def test_archive_failure_emits_one_safe_unavailable(tmp_path, monkeypatch):
    emitted = []
    monkeypatch.setattr(observer, "emit_event", lambda *a, **kw: emitted.append((a, kw)))
    monkeypatch.setattr(observer, "_archive", lambda *a: (_ for _ in ()).throw(OSError("sensitive-path")))
    row, _, frames = fixture()
    assert observer.observe_outcomes("US", [row], frames, "b", "20260825", tmp_path / "s.json") is None
    assert len(emitted) == 1 and emitted[0][0][0] == "watchlist.outcomes_unavailable"
    assert "sensitive-path" not in json.dumps(emitted)


def test_prior_ready_same_day_is_late_enrollment():
    row, _, _ = fixture()
    row["delivery_ready_payload"] = {"observed_at": "2026-08-03T14:00:00+00:00"}
    anchor = new_anchor("US", row, "FIRST_READY", "2026-08-03T15:00:00+00:00", "h", "b")
    assert anchor["enrollment"] == "LATE_ENROLLMENT"
    assert anchor["observed_at"] == "2026-08-03T15:00:00+00:00"
    row["seeded_trade_date"] = "20260701"
    row["delivery_ready_payload"]["observed_at"] = "2026-08-03T15:00:00+00:00"
    timely = new_anchor("US", row, "FIRST_READY", "2026-08-03T15:00:00+00:00", "h", "b")
    assert timely["enrollment"] == "ON_TIME"


def test_pairs_never_mix_policy_or_enrollment(tmp_path, monkeypatch):
    monkeypatch.setattr(observer, "emit_event", lambda *a, **kw: "ok")
    row, _, frames = fixture()
    row.update(status="READY", delivery_observation_payload={"observed_at": "2026-08-03T15:00:00+00:00"})
    events = observer.observe_outcomes("US", [row], frames, "b", "20260825", tmp_path / "us.json")
    assert len(build_packet(events)["markets"]["US"]["common_endpoint_pairs"]) == 5
    for field, value in (("policy_version", "different"), ("enrollment", "LATE_ENROLLMENT")):
        changed = deepcopy(events)
        for event in changed:
            if event["anchor_kind"] == "FIRST_READY":
                event[field] = value
        assert build_packet(changed)["markets"]["US"]["common_endpoint_pairs"] == []


def test_observation_days_never_use_prior_price_date_and_dedupe_batches():
    _, anchor, _ = fixture()
    events = [{**anchor, "status": "PENDING", "event_id": str(i), "asof": "2026-08-03",
               "observed_at": stamp} for i, stamp in enumerate(("2026-08-04T14:00:00+00:00", "2026-08-04T20:00:00+00:00"))]
    market = build_packet(events)["markets"]["US"]
    assert market["observed_market_days"] == 1
    assert market["completed_price_dates"] == ["2026-08-03"]
    assert market["missing_observation_date_events"] == 0
    for event in events:
        event.pop("observed_at")
    assert build_packet(events)["markets"]["US"]["observed_market_days"] == 0
    assert build_packet(events)["markets"]["US"]["missing_observation_date_events"] == 2


def test_regime_reference_date_preserves_runtime_source():
    row, _, _ = fixture()
    row["regime_context"] = {"market_regime": "weak", "source": "batch", "reference_date": "20260803", "asof": "old"}
    anchor = new_anchor("US", row, "BASELINE", "2026-08-04T15:00:00+00:00", "h", "b")
    assert anchor["regime_asof"] == "20260803"
    assert anchor["regime_reference_date"] == "20260803"


@pytest.mark.parametrize("delay", [1, 4])
def test_delayed_ready_has_exact_baseline_twentieth_endpoint(tmp_path, monkeypatch, delay):
    monkeypatch.setattr(observer, "emit_event", lambda *a, **kw: "ok")
    row, _, initial = fixture(count=1)
    row.update(status="WATCHING", delivery_observation_payload={"observed_at": "2026-08-03T15:00:00+00:00"})
    path = tmp_path / "us.json"
    events = observer.observe_outcomes("US", [row], initial, "b", "20260803", path)
    ready_date = (date(2026, 8, 3) + timedelta(days=delay)).isoformat()
    row.update(status="READY", delivery_observation_payload={"observed_at": ready_date + "T15:00:00+00:00"},
               delivery_ready_payload={"observed_at": ready_date + "T15:00:00+00:00"})
    _, _, immature = fixture(count=20)
    for i, bar in enumerate(immature["ABC"]):
        bar["open"] = 100 + i / 10
    before = observer.observe_outcomes("US", [row], immature, "b2", "20260822", path)
    assert not any(e.get("measurement") and e["status"] == "COMPLETE" for e in before)
    events.extend(before)
    _, _, mature = fixture(count=21)
    for i, bar in enumerate(mature["ABC"]):
        bar["open"] = 100 + i / 10
    after = observer.observe_outcomes("US", [], mature, "b3", "20260823", path)
    events.extend(after)
    primary = [e for e in after if e.get("measurement") == "BASELINE20_COMMON_ENDPOINT"]
    assert len(primary) == 1 and primary[0]["status"] == "COMPLETE"
    assert primary[0]["actual_holding_bars"] == 20 - delay
    assert primary[0]["endpoint_date"] == "2026-08-23"
    packet = build_packet(events)
    pairs = packet["markets"]["US"]["primary_baseline20_pairs"]
    assert len(pairs) == 1
    assert pairs[0]["ready_holding_bars"] == 20 - delay
    assert pairs[0]["ready_minus_baseline_relative"] == pytest.approx(110 / (100 + (delay + 1) / 10) - 110 / 100.1)
    again = observer.observe_outcomes("US", [], mature, "b4", "20260823", path)
    assert not any(e.get("measurement") for e in again)


def test_primary_missing_link_and_missing_exact_endpoint_are_unknown():
    row, _, frames = fixture()
    ready = new_anchor("US", row, "FIRST_READY", "2026-08-04T15:00:00+00:00", "h", "b")
    _, events = evaluate(ready, frames, frames)
    primary = next(e for e in events if e.get("measurement"))
    assert primary["status"] == "MISSING" and primary["reason"] == "baseline_link_unavailable"
    ready.update(baseline_anchor_id="baseline", baseline_observed_local_date="2026-08-03")
    frames["ABC"] = [r for r in frames["ABC"] if r["date"] != "2026-08-23"]
    _, events = evaluate(ready, frames, frames)
    primary = next(e for e in events if e.get("measurement"))
    assert primary["status"] == "MISSING" and "gross_return" not in primary


def test_primary_packet_deduplicates_receipts_and_rejects_economic_conflicts(tmp_path, monkeypatch):
    monkeypatch.setattr(observer, "emit_event", lambda *a, **kw: "ok")
    row, _, frames = fixture()
    row.update(status="READY", delivery_observation_payload={"observed_at": "2026-08-03T15:00:00+00:00"})
    events = observer.observe_outcomes("US", [row], frames, "b", "20260825", tmp_path / "us.json")
    primary = next(e for e in events if e.get("measurement") and e["status"] == "COMPLETE")
    duplicate = {**primary, "event_id": "z-duplicate", "captured_at": "2026-08-26T15:00:00+00:00", "input_snapshot_hash": "another-receipt"}
    packet = build_packet(events + [duplicate])
    assert len(packet["markets"]["US"]["primary_baseline20_pairs"]) == 1
    assert packet["markets"]["US"]["primary_baseline20_conflicts"] == []
    assert packet == build_packet(list(reversed(events + [duplicate])))
    conflicting = {**duplicate, "event_id": "z-conflict", "gross_return": .999}
    market = build_packet(events + [duplicate, conflicting])["markets"]["US"]
    assert market["primary_baseline20_pairs"] == []
    assert market["primary_baseline20_conflicts"] == [[primary["anchor_id"], "BASELINE20_COMMON_ENDPOINT"]]
    assert market["primary_baseline20_coverage"] == {"eligible_first_ready_anchors": 1, "statuses": {"CONFLICT": 1}}


def test_primary_coverage_counts_expected_ready_without_any_capture():
    event = {"event_type": "watchlist.shadow_evaluated", "event_id": "expected", "market": "US",
             "attributes": {"watch_ref": "missing", "status": "READY", "outcome_collection_expected": True,
                            "outcome_contract_version": observer.VERSION}}
    assert build_packet([event])["markets"]["US"]["primary_baseline20_coverage"] == {
        "eligible_first_ready_anchors": 1, "statuses": {"UNKNOWN": 1}}


def test_normal_baseline_benchmark_conflict_excludes_primary_pair(tmp_path, monkeypatch):
    monkeypatch.setattr(observer, "emit_event", lambda *a, **kw: "ok")
    row, _, frames = fixture()
    row.update(status="READY", delivery_observation_payload={"observed_at": "2026-08-03T15:00:00+00:00"})
    events = observer.observe_outcomes("US", [row], frames, "b", "20260825", tmp_path / "us.json")
    baseline = next(e for e in events if e["anchor_kind"] == "BASELINE" and e.get("horizon") == 20 and e["status"] == "COMPLETE")
    transport_duplicate = {**baseline, "event_id": "z-receipt", "captured_at": "later", "input_snapshot_hash": "new-receipt"}
    assert len(build_packet(events + [transport_duplicate])["markets"]["US"]["primary_baseline20_pairs"]) == 1
    conflict = {**transport_duplicate, "event_id": "z-conflict", "benchmark_return": .9, "relative_return": -.8}
    market = build_packet(events + [transport_duplicate, conflict])["markets"]["US"]
    assert market["primary_baseline20_pairs"] == []
    assert [baseline["anchor_id"], 20] in market["conflicting_outcomes"]
    assert market["mature_20_session_lifetimes"] == 0
    coverage = next(e for e in market["horizon_coverage"] if e["anchor_kind"] == "BASELINE" and e["horizon"] == 20)
    assert coverage["statuses"] == {"CONFLICT": 1}
