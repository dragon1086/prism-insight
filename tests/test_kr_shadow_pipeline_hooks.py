"""Market-isolated SHADOW wiring; synthetic data only, no external effects."""
import ast
import asyncio
import copy
import json
import os
import subprocess
import sys
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest
from test_watchlist_micro_evidence_packet import fixture

from observability import micro_split
from prism_core.batch_run_status import (
    batch_status_message,
    fresh_result_metadata,
    result_fingerprint,
)
from tools.build_watchlist_micro_evidence_packet import (
    build_watchlist_micro_evidence_packet as build,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("empty,observer_fails", [(False, False), (True, False), (False, True)])
def test_real_kr_trigger_final_observation_preserves_selection(monkeypatch, empty, observer_fails):
    import datetime as dt
    import logging

    from observability import oneil_watchlist

    source = ROOT / "trigger_batch.py"
    method = next(n for n in ast.walk(ast.parse(source.read_text()))
                  if isinstance(n, ast.FunctionDef) and n.name == "run_batch")
    frame = pd.DataFrame(index=[] if empty else ["005930"])
    final = {"갭 상승 모멘텀 상위주": frame}
    seen = []
    def observe(results, trade_date, batch, **kwargs):
        seen.append((results, trade_date, batch, kwargs))
        if observer_fails:
            raise RuntimeError("synthetic optional observer failure")
    monkeypatch.setattr(oneil_watchlist, "observe_batch", observe)
    ns = {"datetime": dt, "logging": logging, "logger": MagicMock(), "ch": MagicMock(),
          "_resolve_trade_date": lambda _: "20260916",
          "load_market_snapshot_bundle": lambda _: SimpleNamespace(snapshot=None, prev_snapshot=None,
              prev_date="20260915", cap_df=[]), "select_final_tickers": lambda *a, **kw: final}
    for name in ("trigger_morning_volume_surge", "trigger_morning_gap_up_momentum", "trigger_morning_value_to_cap_ratio"):
        ns[name] = lambda *args: frame
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), "exec"), ns)  # noqa: S102 - repository method
    assert ns["run_batch"]("morning", watch_batch_ref="exact-batch") is final
    assert seen == [(final, "20260916", "exact-batch", {"market": "KR", "regime_context": None})]


def test_packet_market_isolation_and_legacy_default():
    us = fixture()
    kr = copy.deepcopy(us)
    for event in kr:
        event["market"] = "KR"
        event["ticker"] = "005930"
    # Identical IDs across markets must neither conflict nor join.
    assert build(us + kr) == build(us, market="US")
    packet = build(us + kr, market="KR")
    assert packet["scope"] == "KR_INITIAL_0_TO_10_DIAGNOSTIC_ONLY"
    assert packet["counts"]["natural_pipeline_joint_links"] == 1
    kr[3]["market"] = "US"
    assert build(kr, market="KR")["counts"]["natural_pipeline_joint_links"] == 0


def test_joint_packet_boundaries_never_claim_fill_or_full_baseline():
    us = fixture()
    row = build(us)["joint_observations"][0]
    assert row["entry_boundary"] == "LEGACY_ELIGIBLE_PRE_REFRESH"
    assert row["baseline_position_fraction"] is None
    assert row["baseline_sizing_status"] == "UNKNOWN"
    assert not row["broker_approved"] and not row["confirmed_fill"]
    kr = copy.deepcopy(us)
    for event in kr:
        event["market"] = "KR"
    kr[3]["attributes"].update(entry_boundary="FRESH_QUOTE_REVALIDATED", baseline_position_fraction=.5)
    row = build(kr, market="KR")["joint_observations"][0]
    assert row["entry_boundary"] == "FRESH_QUOTE_REVALIDATED"
    assert row["baseline_position_fraction"] == .5
    assert not row["broker_approved"] and not row["confirmed_fill"]


@pytest.mark.parametrize("market,boundary", [("US", "LEGACY_ELIGIBLE_PRE_REFRESH"), ("KR", "FRESH_QUOTE_REVALIDATED")])
def test_joint_event_boundary_metadata(monkeypatch, market, boundary):
    from observability import oneil_watchlist
    emitted = []
    monkeypatch.setattr(micro_split, "emit_event", lambda kind, **kw: emitted.append(kw))
    ready = {"market": market, "ticker": "AAA", "batch_ref": "batch", "watch_ref": "w",
             "seed_event_id": "s", "ready_event_id": "r", "ready_observation_event_id": "o",
             "policy_version": "v1", "observation_price_ref": "p"}
    monkeypatch.setattr(oneil_watchlist, "read_ready_context", lambda *args: ready)
    micro_split._emit_watchlist_link({"market": market, "ticker": "AAA", "event_id": "micro",
        "attributes": {"batch_ref": "batch", "policy_version": "micro-v1", "decision_ref": "d",
                       "execution_profile_ref": "a", "entry_boundary": "FRESH_QUOTE_REVALIDATED"}})
    attrs = emitted[0]["attributes"]
    assert attrs["entry_boundary"] == boundary
    assert attrs["baseline_position_fraction"] is None
    assert attrs["execution_provenance"] == "NOT_REQUESTED"
    assert not attrs["broker_approved"] and not attrs["confirmed_fill"]


@pytest.mark.parametrize("enabled", [False, True])
def test_kr_policy_independent_of_us_environment(monkeypatch, enabled):
    monkeypatch.setenv("MICRO_SPLIT_SHADOW_ENABLED", "false")
    monkeypatch.setattr(micro_split.Path, "read_text", lambda _: __import__("json").dumps({
        "mode": "SHADOW", "market": "KR", "policy_version": "oneil_watchlist_kr_v1", "enabled": enabled,
    }))
    assert micro_split.shadow_enabled(market="KR") is enabled
    assert not micro_split.shadow_enabled()
    assert not micro_split.shadow_enabled(market="XX")


def test_kr_account_units_integer_zero_and_boundary_metadata(monkeypatch):
    monkeypatch.setattr(micro_split, "shadow_enabled", lambda **_: True)
    emitted = []
    monkeypatch.setattr(micro_split, "emit_event", lambda kind, **kw: emitted.append({"event_type": kind, **kw}) or emitted[-1])
    monkeypatch.setattr(micro_split, "_emit_watchlist_link", lambda _: None)
    for account, unit in (("one", 1000000), ("two", 100000)):
        micro_split.emit_initial_shadow(market="KR", ticker="005930", decision_id="same",
            account_id=account, unit_amount=unit, current_price=70000, regime="moderate_bull")
    assert [e["attributes"]["projected_whole_share_quantity"] for e in emitted] == [1, 0]
    assert emitted[0]["event_id"] != emitted[1]["event_id"]
    assert all(e["attributes"]["entry_boundary"] == "FRESH_QUOTE_REVALIDATED" for e in emitted)
    assert all(e["attributes"]["currency"] == "KRW" for e in emitted)


@pytest.mark.parametrize("tracking_ok,reports,pdfs,raises,expected", [
    (True, ["r"], ["p"], False, 1),
    (False, ["r"], ["p"], False, 0),
    (True, [], [], False, 0),
    (True, ["r"], [], False, 0),
    (True, ["r"], ["p"], True, 0),
])
def test_real_kr_pipeline_completion(monkeypatch, tmp_path, tracking_ok, reports, pdfs, raises, expected):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(micro_split, "shadow_enabled", lambda **_: True)
    captured = []
    monkeypatch.setattr(micro_split, "emit_event", lambda kind, **kw: captured.append((kind, kw)))
    source = ROOT / "stock_analysis_orchestrator.py"
    method = next(n for n in ast.walk(ast.parse(source.read_text()))
                  if isinstance(n, ast.AsyncFunctionDef) and n.name == "run_full_pipeline")
    ns = {"logger": MagicMock(), "datetime": datetime, "asyncio": asyncio,
          "nullcontext": nullcontext, "COLLECTING": "COLLECTING",
          "SKIPPED": "SKIPPED", "result_fingerprint": result_fingerprint,
          "fresh_result_metadata": fresh_result_metadata, "batch_status_message": batch_status_message,
          "os": SimpleNamespace(path=SimpleNamespace(exists=lambda _: False))}
    for name in ("publish_batch_campaign_best_effort", "publish_batch_reports_best_effort", "publish_batch_tracking_story_best_effort"):
        ns[name] = AsyncMock()
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), "exec"), ns)  # noqa: S102 - actual repository method
    tracker = SimpleNamespace(run=AsyncMock(return_value=tracking_ok), last_batch_messages=[])
    modules = __import__("sys").modules
    monkeypatch.setitem(modules, "stock_tracking_enhanced_agent", SimpleNamespace(EnhancedStockTrackingAgent=lambda **_: tracker))
    monkeypatch.setitem(modules, "stock_tracking_agent", SimpleNamespace(_kr_codex_runtime_enabled=lambda: True, app=None))
    monkeypatch.setitem(modules, "cores.archive.ingest", SimpleNamespace(ingest_reports_async=AsyncMock()))
    monkeypatch.setitem(modules, "telegram_config", SimpleNamespace(is_openai_quota_error=lambda _: False, send_openai_quota_alert=AsyncMock()))
    instance = SimpleNamespace(run_macro_intelligence=AsyncMock(return_value={}),
        run_trigger_batch=AsyncMock(return_value=["005930"]),
        generate_reports=AsyncMock(return_value=reports, side_effect=RuntimeError("test") if raises else None),
        convert_to_pdf=AsyncMock(return_value=pdfs), generate_telegram_messages=AsyncMock(return_value=[]),
        send_trigger_alert=AsyncMock(return_value=True),
        _campaign_messages={}, _broadcast_tasks=[], telegram_config=SimpleNamespace(use_telegram=False, log_status=lambda: None))
    asyncio.run(ns["run_full_pipeline"](instance, "morning"))
    assert len(captured) == expected
    assert micro_split.get_shadow_batch_context() is None
    if raises or not reports or not pdfs:
        tracker.run.assert_not_awaited()
        instance.send_trigger_alert.assert_awaited_once()
        assert instance.send_trigger_alert.await_args.kwargs["status_message"]
        instance.generate_telegram_messages.assert_not_awaited()
    else:
        tracker.run.assert_awaited_once()
        instance.send_trigger_alert.assert_not_awaited()


def test_real_kr_thread_boundary_receives_explicit_batch(monkeypatch):
    import sys
    source = ROOT / "stock_analysis_orchestrator.py"
    method = next(n for n in ast.walk(ast.parse(source.read_text()))
                  if isinstance(n, ast.AsyncFunctionDef) and n.name == "run_trigger_batch")
    ns = {"logger": MagicMock(), "datetime": datetime, "asyncio": asyncio}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), "exec"), ns)  # noqa: S102 - actual repository method
    observed = []
    def run(*args, **kwargs):
        observed.append((kwargs["watch_batch_ref"], micro_split.get_shadow_batch_context()))
        return {}
    monkeypatch.setitem(sys.modules, "trigger_batch", SimpleNamespace(run_batch=run, MarketSnapshotUnavailableError=ValueError))
    monkeypatch.setattr(micro_split, "shadow_enabled", lambda **_: True)
    async def invoke():
        token = micro_split.begin_shadow_batch(market="KR", trade_date="20260916", trigger_mode="morning")
        batch = micro_split.get_shadow_batch_context()["batch_ref"]
        try:
            assert await ns["run_trigger_batch"](SimpleNamespace(), "morning") == []
            assert observed == [(batch, None)]
        finally:
            micro_split.end_shadow_batch(token)
    asyncio.run(invoke())


@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize("enabled", ["false", "true"])
def test_real_tracking_hook_preserves_isolated_no_effects(tmp_path, market, enabled):
    """Run the imported full agent, real gates and the new callback boundary."""
    from test_isolated_agent_effects_fullclass import SCRIPT

    inject = '''
    import observability.micro_split as micro
    target_module = sys.modules[cls.__module__]
    original_observe = target_module.observe_or_emit
    shadow_calls = []
    def observe(agent, emitter, *args, **kwargs):
        if emitter is target_module.emit_micro_split_shadow:
            shadow_calls.append(kwargs)
        return original_observe(agent, emitter, *args, **kwargs)
    target_module.observe_or_emit = observe
    def forbidden_callback(*args, **kwargs):
        raise AssertionError("Isolated pipeline invoked production emitter")
    micro.emit_event = forbidden_callback
'''
    script = SCRIPT.replace('    result = await agent.process_reports(["SYNTHETIC-report.pdf"])',
        inject + '\n    result = await agent.process_reports(["SYNTHETIC-report.pdf"])\n'
        '    assert len(shadow_calls) == 1, shadow_calls\n'
        '    assert shadow_calls[0]["market"] == market\n'
        '    assert shadow_calls[0]["current_price"] == 100\n'
        '    unit_key = "buy_amount_krw" if market == "KR" else "buy_amount_usd"\n'
        '    assert shadow_calls[0]["unit_amount"] == agent.account_configs[0].get(unit_key)\n')
    env = {key: value for key, value in os.environ.items()
           if not any(secret in key.upper() for secret in ("KIS", "TOKEN", "SECRET", "API_KEY", "APP_KEY"))}
    env.update(HOME=str(tmp_path), KIS_CONFIG_ROOT=str(tmp_path / "absent-kis"),
               PYTHON_DOTENV_DISABLED="1", PYTHONDONTWRITEBYTECODE="1", TZ="Asia/Seoul",
               MICRO_SPLIT_SHADOW_ENABLED=enabled)
    result = subprocess.run([sys.executable, "-I", "-c", script, str(ROOT), market, "success"],
                            cwd=tmp_path, env=env, text=True, capture_output=True, timeout=90, check=False)
    assert result.returncode == 0, result.stderr[-9000:]
    assert json.loads(result.stdout.strip().splitlines()[-1])["broker_or_network_attempts"] == 0
