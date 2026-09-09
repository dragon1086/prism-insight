"""Synthetic fixtures establish implementation correctness, NOT performance."""
import copy
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from itertools import pairwise
from pathlib import Path

import pytest

from prism_core.trend_quality_research import (
    build_study,
    compute_features,
    digest,
    passes,
    registry,
)


def snapshot(closes=None, ref="d1", date=None):
    start = date or datetime(2024, 1, 1, tzinfo=timezone.utc)
    closes = closes or [100 + i for i in range(40)]
    bars = [{"close_at": (start + timedelta(days=i)).isoformat(),
             "completed": True, "high": c + 1, "low": c - 1, "close": c}
            for i, c in enumerate(closes)]
    return {"decision_ref": ref, "decided_at": bars[-1]["close_at"],
            "observed_at": bars[-1]["close_at"],
            "market": "US", "session": "morning", "session_date": bars[-1]["close_at"][:10],
            "regime": "moderate_bull", "policy_version": "p1",
            "bar_interval": "1d", "gaps_checked": True,
            "data_quality_flags": [], "adjustment_policy": "no_actions_verified",
            "source_hash": digest(bars), "bars": bars}


def packet(snaps):
    return {"packet_schema_version": 3, "analysis_contract_version": "entry-quality-harness-v2",
            "packet_id": "canonical-test-only", "as_of": "2026-01-01T00:00:00Z",
            "analysis_rows": [{"decision_ref": s["decision_ref"], "decided_at": s["decided_at"],
                               "regime": s["regime"], "policy_version": s["policy_version"],
                               "eligible_for_analysis": True, "trigger_type": "Gap Up Momentum Top",
                               "entry": {"observed": True, "fill_status": "REJECTED"},
                               "outcomes": {"strategy_return_pct": -3.5, "strategy_holding_seconds": 600}}
                              for s in snaps]}


def test_wilder_monotonic_trend_and_efficiency():
    f = compute_features(snapshot())
    assert f["status"] == "OK"
    assert f["adx14"] == pytest.approx(100)
    assert f["plus_di14"] == pytest.approx(50)
    assert f["minus_di14"] == 0
    assert f["er20"] == 1
    assert passes("H1", f) and passes("H2", f) and passes("H4", f)
    assert not passes("H3", f)  # constant 100 is not rising


def test_falling_trend_strong_adx_not_long():
    f = compute_features(snapshot([200 - i for i in range(40)]))
    assert f["adx14"] == pytest.approx(100)
    assert passes("H1", f)
    assert not passes("H2", f) and not passes("H4", f)


def test_chop_is_not_efficient_and_flat_no_divide_zero():
    f = compute_features(snapshot([100 + i % 2 for i in range(40)]))
    assert f["er20"] == 0
    assert not passes("H4", f)
    f = compute_features(snapshot([100] * 40))
    assert f["adx14"] == 0
    assert f["er20"] == 0


def test_prefix_invariance_and_future_rejection():
    s = snapshot()
    prefix = copy.deepcopy(s)
    prefix["bars"] = prefix["bars"][:30]
    prefix["decided_at"] = prefix["bars"][-1]["close_at"]
    prefix["observed_at"] = prefix["decided_at"]
    prefix["source_hash"] = digest(prefix["bars"])
    before = compute_features(prefix)
    s["bars"][-1]["close"] = 139  # future path cannot modify prefix
    assert compute_features(prefix) == before
    contaminated = copy.deepcopy(s)
    contaminated["decided_at"] = prefix["decided_at"]
    assert "FUTURE_BAR" in compute_features(contaminated)["reasons"]


@pytest.mark.parametrize("mutation,reason", [
    (lambda s: s.update(decided_at="2024-03-01T00:00:00Z"), "STALE_SNAPSHOT"),
    (lambda s: s.update(observed_at="2030-01-01T00:00:00Z"), "FUTURE_OBSERVATION"),
    (lambda s: s.pop("observed_at"), "INVALID_BAR_OR_TIMESTAMP"),
    (lambda s: s.update(observed_at=s["bars"][-2]["close_at"]), "BAR_AFTER_OBSERVATION"),
    (lambda s: s["bars"][-1].update(completed=False), "INCOMPLETE_BAR"),
    (lambda s: s["bars"][-1].update(close_at=s["bars"][-2]["close_at"]), "BAR_TIMESTAMPS_NOT_STRICTLY_INCREASING"),
    (lambda s: s["bars"].reverse(), "BAR_TIMESTAMPS_NOT_STRICTLY_INCREASING"),
    (lambda s: s["bars"][-1].update(close_at="2024-02-09T00:00:00"), "INVALID_BAR_OR_TIMESTAMP"),
    (lambda s: s["bars"][-1].update(low=500), "INVALID_OHLC"),
    (lambda s: s.update(gaps_checked=False), "GAPS_METADATA_MISSING"),
    (lambda s: s.update(data_quality_flags=["UNRESOLVED_SPLIT"]), "DATA_QUALITY_FLAGS_OR_STATUS_MISSING"),
    (lambda s: s.pop("adjustment_policy"), "ADJUSTMENT_PROVENANCE_MISSING"),
    (lambda s: s.update(bar_interval="5m"), "UNSUPPORTED_BAR_INTERVAL"),
])
def test_bad_snapshots_are_missing_never_pass(mutation, reason):
    s = snapshot()
    mutation(s)
    s["source_hash"] = digest(s["bars"])
    f = compute_features(s)
    assert f["status"] == "MISSING" and reason in f["reasons"]
    assert passes("H1", f) is None


def test_nan_and_hash_tamper_fail_closed():
    s = snapshot()
    s["bars"][-1]["close"] = float("nan")
    assert compute_features(s)["status"] == "MISSING"
    s = snapshot()
    s["source_hash"] = "bad"
    assert "SOURCE_HASH_MISMATCH" in compute_features(s)["reasons"]


def test_warmup_exact_boundary():
    assert compute_features(snapshot(list(range(100, 129))))["status"] == "MISSING"
    assert compute_features(snapshot(list(range(100, 130))))["status"] == "OK"


def test_missing_input_reports_no_effect_estimate():
    s = snapshot()
    result = build_study(packet([s]))
    assert result["status"] == "INPUT_UNAVAILABLE"
    assert result["strata"] == []
    assert result["rows"][0]["feature"]["reasons"] == ["FEATURE_SNAPSHOT_MISSING"]


def test_exact_ref_join_no_ticker_fallback_and_context_guard():
    s = snapshot()
    p = packet([s])
    other = copy.deepcopy(s)
    other["decision_ref"] = "other"
    assert build_study(p, {"rows": [other]})["status"] == "INPUT_UNAVAILABLE"
    s["policy_version"] = "future-policy"
    assert "JOIN_CONTEXT_MISMATCH" in build_study(p, {"rows": [s]})["rows"][0]["feature"]["reasons"]


def test_unsupported_trigger_does_not_get_filter():
    s = snapshot()
    p = packet([s])
    p["analysis_rows"][0]["trigger_type"] = "거래량 증가 상위 횡보주"
    assert "OUT_OF_SCOPE_TRIGGER" in build_study(p, {"rows": [s]})["rows"][0]["feature"]["reasons"]


def test_registry_copy_and_input_reorder_idempotent():
    a, b = snapshot(), snapshot(ref="d2", date=datetime(2024, 2, 1, tzinfo=timezone.utc))
    p = packet([a, b])
    first = build_study(p, {"rows": [a, b]})
    second = build_study(p, {"rows": [b, a]})
    assert first == second
    reordered = copy.deepcopy(p)
    reordered["analysis_rows"].reverse()
    assert first == build_study(reordered, {"rows": [b, a]})
    x = registry()
    x["triggers"].append("not registered")
    assert first["registry_hash"] == digest(registry())
    assert p == packet([a, b])  # no mutation


def test_duplicate_ids_rejected():
    s = snapshot()
    with pytest.raises(ValueError, match="duplicate"):
        build_study(packet([s]), {"rows": [s, s]})
    with pytest.raises(ValueError, match="duplicate"):
        build_study(packet([s, s]))


def test_broker_rejection_retains_strategy_return_and_no_candidate_pooling():
    a = snapshot()
    b = snapshot(ref="d2", date=datetime(2024, 3, 1, tzinfo=timezone.utc))
    p = packet([a, b])
    p["analysis_rows"][1]["outcomes"]["candidate"] = {"return_30d_pct": 1000}
    study = build_study(p, {"rows": [a, b]})
    assert study["rows"][1]["strategy_return_pct"] == -3.5
    assert study["rows"][0]["partition"] == "PURGED_OR_EMBARGOED"
    assert study["strata"][0]["trials"]["baseline"]["paired_closed_strategy_count"] == 1
    assert study["status"] == "INSUFFICIENT"


def test_future_exit_does_not_enter_closed_diagnostics():
    s = snapshot()
    p = packet([s])
    p["analysis_rows"][0]["outcomes"]["strategy_closed_at"] = "2027-01-01T00:00:00Z"
    assert build_study(p, {"rows": [s]})["rows"][0]["strategy_return_pct"] is None


def test_purge_embargo_and_deterministic_date_bootstrap():
    snaps = [snapshot([200 - i for i in range(40)], ref=f"d{n:03}",
                      date=datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(days=n)) for n in range(120)]
    p = packet(snaps)
    for row in p["analysis_rows"]:
        row["outcomes"]["strategy_closed_at"] = (datetime.fromisoformat(row["decided_at"]) + timedelta(days=2)).isoformat()
    study = build_study(p, {"rows": snaps})
    assert study == build_study(p, {"rows": list(reversed(snaps))})
    assert study["status"] == "EXPLORATORY_ONLY"
    assert any(r["partition"] == "PURGED_OR_EMBARGOED" for r in study["rows"])
    holdout = next(g for g in study["strata"] if g["partition"] == "HOLDOUT")
    h2 = holdout["trials"]["H2"]
    assert h2["paired_equal_opportunity_delta_pp"] == 3.5
    assert h2["date_block_adjusted_interval_pp"] == [3.5, 3.5]
    assert h2["losing_trade_removal_rate"] == 1


def test_cli_missing_features_and_overwrite_guard(tmp_path):
    inp, out = tmp_path / "packet.json", tmp_path / "study.json"
    inp.write_text(json.dumps(packet([snapshot()])))
    script = Path(__file__).resolve().parents[1] / "tools/build_trend_quality_study.py"
    run = subprocess.run([sys.executable, str(script), "--packet", str(inp), "--output", str(out)], capture_output=True, text=True, check=False)
    assert run.returncode == 0, run.stderr
    assert json.loads(out.read_text())["status"] == "INPUT_UNAVAILABLE"
    run = subprocess.run([sys.executable, str(script), "--packet", str(inp), "--output", str(inp)], capture_output=True, text=True, check=False)
    assert run.returncode != 0


def test_research_module_has_no_order_or_network_imports():
    import ast
    source = Path(__file__).resolve().parents[1] / "prism_core/trend_quality_research.py"
    imports = [n.module for n in ast.walk(ast.parse(source.read_text())) if isinstance(n, ast.ImportFrom)]
    assert all(not (m or "").startswith(("trading", "requests", "http", "stock_tracking")) for m in imports)


def test_wilder_against_independent_closed_form_weights_and_h3():
    # Closed-form geometric weights are independent of implementation's recurrence.
    closes = [100 + (i % 2) for i in range(30)] + list(range(102, 122))
    s = snapshot(closes)
    f = compute_features(s)
    bars = s["bars"]
    tr, pdm, mdm = [], [], []
    for a, b in pairwise(bars):
        up, down = b["high"] - a["high"], a["low"] - b["low"]
        tr.append(max(b["high"] - b["low"], abs(b["high"] - a["close"]), abs(b["low"] - a["close"])))
        pdm.append(max(up, 0) if up > down else 0)
        mdm.append(max(down, 0) if down > up else 0)
    ratio = 13 / 14

    def weighted(series, index):
        return sum(series[:14]) * ratio ** (index - 13) + sum(
            series[j] * ratio ** (index - j) for j in range(14, index + 1))

    dx = []
    for i in range(13, len(tr)):
        p, m = weighted(pdm, i), weighted(mdm, i)
        dx.append(100 * abs(p - m) / (p + m) if p + m else 0)
    end = len(dx) - 1
    expected = sum(dx[:14]) / 14 * ratio ** (end - 13) + sum(
        dx[j] / 14 * ratio ** (end - j) for j in range(14, end + 1))
    assert f["adx14"] == pytest.approx(expected, abs=1e-12)
    assert f["plus_di14"] == pytest.approx(100 * weighted(pdm, len(tr) - 1) / weighted(tr, len(tr) - 1))
    assert f["adx_rising_3"] is True
    assert passes("H3", f) is True


def test_hypothesis_boundaries_fixed():
    f = {"status": "OK", "adx14": 20, "plus_di14": 20, "minus_di14": 10,
         "adx_rising_3": True, "er20": 0.30, "net20": 0.1}
    assert passes("H1", f) is True
    assert passes("H2", f) is False
    assert passes("H3", f) is True
    assert passes("H4", f) is True
    f.update(adx14=25, plus_di14=10)
    assert passes("H2", f) is False  # tie not long confirmation
    f.update(plus_di14=11)
    assert passes("H2", f) is True


def test_metadata_hash_covers_adjustment_provenance():
    s = snapshot()
    first = compute_features(s)
    s["adjustment_policy"] = "point_in_time_adjusted"
    second = compute_features(s)
    assert first["source_hash"] == second["source_hash"]
    assert first["input_hash"] != second["input_hash"]


def test_packet_market_and_asof_constraints():
    s = snapshot()
    p = packet([s])
    p["market"] = "KR"
    assert "MARKET_MISMATCH" in build_study(p, {"rows": [s]})["rows"][0]["feature"]["reasons"]
    p["market"] = "US"
    p["as_of"] = "2020-01-01T00:00:00Z"
    assert "DECISION_AFTER_PACKET_AS_OF" in build_study(p, {"rows": [s]})["rows"][0]["feature"]["reasons"]
