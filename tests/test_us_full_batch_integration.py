"""US real orchestration smoke, isolated from accounts, network and production files.

Unlike AST contract checks, these import the real prism-us namespace and execute
run_full_pipeline, trigger JSON loading, report persistence, PDF routing, summary
routing and Telegram routing. External inference/rendering/tracking execution is
replaced at its boundary. Provider/selection algorithms have their own real-module
coverage in test_us_batch_ohlcv_shape_integration.py.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

RUN = r'''
import asyncio
from contextlib import ExitStack
import json
from pathlib import Path
import socket
import sys
from types import SimpleNamespace
from unittest.mock import patch

root, output, mode, scenario = sys.argv[1:]
root, output = Path(root), Path(output)
sys.path[:0] = [str(root / "prism-us"), str(root)]
events = []
network_attempts = []

def no_network(*args, **kwargs):
    network_attempts.append(repr(args))
    raise AssertionError("Network, broker and live publication forbidden in integration")

with ExitStack() as stack:
    stack.enter_context(patch.object(socket.socket, "connect", no_network))
    stack.enter_context(patch.object(socket, "create_connection", no_network))
    # Never discover the developer's .env while importing real production modules.
    stack.enter_context(patch("dotenv.load_dotenv", return_value=False))
    import pandas as pd
    import us_stock_analysis_orchestrator as orchestration
    import us_trigger_batch as batch
    import cores.us_analysis as analysis
    import us_telegram_summary_agent as summaries
    import us_stock_tracking_agent as tracking
    import telegram_bot_agent as telegram
    import pdf_converter
    from observability import micro_split

    provenance = {name: str(Path(module.__file__).resolve()) for name, module in {
        "orchestrator": orchestration, "analysis": analysis,
        "summary": summaries, "tracking": tracking,
    }.items()}
    assert all(str(root / "prism-us") in path for path in provenance.values())
    for attribute, leaf in [("PRISM_US_DIR", "batch"), ("US_REPORTS_DIR", "reports"),
                            ("US_PDF_REPORTS_DIR", "pdfs"), ("US_TELEGRAM_MSGS_DIR", "messages")]:
        directory = output / leaf
        directory.mkdir(parents=True, exist_ok=True)
        stack.enter_context(patch.object(orchestration, attribute, directory))

    async def macro(self, reference_date=None, language="ko"):
        events.append(["macro", reference_date])
        if scenario == "pipeline_failure":
            raise RuntimeError("private-fixture-detail-must-not-be-published")
        return {"market_regime": "sideways", "leading_sectors": ["Healthcare"]}

    def screening(run_mode, level, destination, **kwargs):
        events.append(["screen", run_mode, kwargs.get("override_date")])
        if scenario == "stale_results":
            return {}
        symbols = [] if scenario in {"empty", "incomplete_prices"} else ["DGX", "AAPL"]
        records = [{"ticker": symbol, "name": symbol, "current_price": 100.,
                    "change_rate": 2.} for symbol in symbols]
        metadata = {"trade_date": "20260923"}
        if scenario == "incomplete_prices":
            metadata["snapshot_coverage"] = {"status": "PARTIAL", "requested_count": 518,
                                              "current_count": 518, "previous_count": 1,
                                              "comparable_count": 1}
        Path(destination).write_text(json.dumps({"metadata": metadata,
                                                "Closing Strength Top": records}))
        return {"Closing Strength Top": pd.DataFrame({"CompanyName": symbols}, index=symbols)} if symbols else {}

    async def model(**kwargs):
        symbol = kwargs["ticker"]
        events.append(["model", symbol, kwargs["reference_date"]])
        assert kwargs["macro_context"]["market_regime"] == "sideways"
        if scenario == "report_failure" or (scenario == "partial_report" and symbol == "AAPL"):
            raise RuntimeError("fixture report generation failure")
        return "# " + symbol + "\nVerified fixture narrative.\nEND_OF_REPORT_" + symbol

    def render(source, destination, *args, **kwargs):
        text = Path(source).read_text()
        symbol = Path(source).name.split("_")[0]
        events.append(["pdf", symbol])
        if scenario == "pdf_failure":
            raise RuntimeError("fixture PDF renderer failure")
        assert text.endswith("END_OF_REPORT_" + symbol)
        Path(destination).write_text(text)

    async def summary(self, pdf, destination, language="ko"):
        symbol, name = Path(pdf).stem.split("_")[:2]
        events.append(["summary", symbol])
        assert Path(pdf).read_text().endswith("END_OF_REPORT_" + symbol)
        Path(destination, f"{symbol}_{name}_telegram.txt").write_text("summary " + symbol)

    async def send_message(self, chat, message, **kwargs):
        events.append(["telegram", kwargs.get("msg_type"), message])
        return True

    async def send_directory(self, directory, chat, sent, **kwargs):
        paths = kwargs["message_paths"]
        assert all(Path(path).is_file() for path in paths)
        assert not any(Path(path).name == "OLD_OLD_telegram.txt" for path in paths)
        events.append(["telegram_summaries", sorted(Path(p).name for p in paths)])

    async def send_document(self, chat, path, **kwargs):
        assert Path(path).is_file()
        events.append(["telegram_pdf", Path(path).name.split("_")[0]])
        return True

    def tracker_init(self, *args, **kwargs):
        self.last_batch_messages = ["isolated holdings management fixture"]

    async def tracker_run(self, pdfs, *args, **kwargs):
        events.append(["tracking", [Path(p).name.split("_")[0] for p in pdfs]])
        assert kwargs["market_regime"] == "sideways"
        assert Path(kwargs["trigger_results_file"]).is_file()
        return scenario != "tracking_failure"

    async def publish(**kwargs):
        events.append(["campaign", kwargs.get("status", "story"), kwargs.get("trade_date")])

    async def archive(*args, **kwargs):
        events.append(["archive"])

    stack.enter_context(patch.object(orchestration.USStockAnalysisOrchestrator, "run_macro_intelligence", macro))
    stack.enter_context(patch.object(batch, "run_batch", screening))
    stack.enter_context(patch.object(analysis, "analyze_us_stock", model))
    stack.enter_context(patch.object(pdf_converter, "markdown_to_pdf", render))
    stack.enter_context(patch.object(summaries.USTelegramSummaryGenerator, "process_report", summary))
    stack.enter_context(patch.object(telegram.TelegramBotAgent, "__init__", lambda self: None))
    for name, replacement in [("send_message", send_message), ("process_messages_directory", send_directory),
                              ("send_document", send_document)]:
        stack.enter_context(patch.object(telegram.TelegramBotAgent, name, replacement))
    stack.enter_context(patch.object(tracking.USStockTrackingAgent, "__init__", tracker_init))
    stack.enter_context(patch.object(tracking.USStockTrackingAgent, "run", tracker_run))
    stack.enter_context(patch.object(tracking, "_us_codex_runtime_enabled", return_value=True))
    for name in ["publish_batch_campaign_best_effort", "publish_batch_reports_best_effort",
                 "publish_batch_tracking_story_best_effort"]:
        stack.enter_context(patch.object(orchestration, name, publish))
    stack.enter_context(patch.object(orchestration, "_import_main_archive_ingest",
                                    return_value=SimpleNamespace(ingest_reports_async=archive)))
    stack.enter_context(patch.object(micro_split, "begin_shadow_batch", return_value="fixture"))
    stack.enter_context(patch.object(micro_split, "end_shadow_batch", side_effect=lambda token: events.append(["end", token])))
    stack.enter_context(patch.object(micro_split, "complete_shadow_batch",
                                    side_effect=lambda **kwargs: events.append(["complete", kwargs])))
    config = SimpleNamespace(use_telegram=True, bot_token="fixture-not-a-token", channel_id="fixture-channel",
                             broadcast_languages=[], validate_or_raise=lambda: None, log_status=lambda: None)
    if scenario in {"empty", "stale_results", "incomplete_prices", "pipeline_failure"}:
        config.broadcast_languages = ["en"]
        async def no_translation(*args, **kwargs):
            events.append(["unexpected_translation"])
            raise AssertionError("Status notices must not start a model-powered translation")
        stack.enter_context(patch.object(orchestration.USStockAnalysisOrchestrator,
                                        "_send_translated_trigger_alert", no_translation))
    (orchestration.US_TELEGRAM_MSGS_DIR / "OLD_OLD_telegram.txt").write_text("old unrelated summary")
    if scenario == "stale_results":
        (orchestration.PRISM_US_DIR / f"trigger_results_us_{mode}_20260923.json").write_text(
            json.dumps({"metadata": {"trade_date": "20260922"}, "Closing Strength Top": []}))
    orchestrator = orchestration.USStockAnalysisOrchestrator(config)
    asyncio.run(orchestrator.run_full_pipeline(mode, override_date="20260923"))
    assert not network_attempts, network_attempts
    assert not any(event[0] == "unexpected_translation" for event in events)
    (output / "receipt.json").write_text(json.dumps({"events": events, "provenance": provenance}))
'''


def _run(tmp_path, mode, scenario):
    env = {key: value for key, value in os.environ.items()
           if not any(part in key for part in ("TOKEN", "API_KEY", "SECRET", "PASSWORD"))}
    env.update(PRISM_DISABLE_SIGNAL_PUBLISH="1", PRISM_FEATURE_INSIGHT_IMAGE="false",
               PRISM_OBSERVABILITY_SPOOL=str(tmp_path / "events.jsonl"))
    result = subprocess.run([sys.executable, "-c", RUN, str(ROOT), str(tmp_path), mode, scenario],
                            cwd=tmp_path, env=env, text=True, capture_output=True, timeout=50, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    receipt = json.loads((tmp_path / "receipt.json").read_text())
    return receipt["events"]


@pytest.mark.parametrize("mode", ["morning", "afternoon"])
@pytest.mark.parametrize("scenario, expected", [("success", ["DGX", "AAPL"]),
                                               ("partial_report", ["DGX"])])
def test_real_us_pipeline_reports_pdf_tracking_and_delivery(tmp_path, mode, scenario, expected):
    events = _run(tmp_path, mode, scenario)
    assert ["screen", mode, "20260923"] in events
    assert ["tracking", expected] in events
    assert [event[1] for event in events if event[0] == "telegram_pdf"] == expected
    assert any(event[:2] == ["telegram", "trigger"] for event in events)
    completed = [event[1] for event in events if event[0] == "complete"]
    assert completed and completed[0]["selected_count"] == 2
    assert completed[0]["report_count"] == len(expected)
    assert events.count(["end", "fixture"]) == 1


@pytest.mark.parametrize("mode", ["morning", "afternoon"])
@pytest.mark.parametrize("scenario", ["empty", "report_failure", "pdf_failure"])
def test_empty_or_failed_reports_current_us_holding_management_contract(tmp_path, mode, scenario):
    events = _run(tmp_path, mode, scenario)
    # Pin the existing no-new-PDF contract before deciding whether an empty
    # tracking call is safe (it must not discover historical reports and BUY).
    assert not any(event[0] == "tracking" for event in events)
    messages = [event[2] for event in events if event[0] == "telegram"]
    assert messages, "Empty/error batches must explicitly notify without invoking trading"
    if scenario == "empty":
        assert "신규 분석 대상으로 선정된 종목이 없습니다" in messages[-1]
        assert ["campaign", "SKIPPED", "20260923"] in events
    else:
        assert "모두 실패" in messages[-1]
        assert not any(event[:2] == ["campaign", "SKIPPED"] for event in events)
    assert not any(event[0] == "telegram_pdf" for event in events)
    assert not any(event[0] == "complete" for event in events)
    assert not any(event[0] == "telegram_summaries" for event in events)
    assert events.count(["end", "fixture"]) == 1


@pytest.mark.parametrize("scenario, expected", [
    ("stale_results", "결과 미확인"), ("incomplete_prices", "정상적인 신호 없음으로 해석하지 마세요"),
])
def test_us_empty_result_diagnostics_distinguish_stale_and_missing_prices(tmp_path, scenario, expected):
    events = _run(tmp_path, "afternoon", scenario)
    messages = [event[2] for event in events if event[0] == "telegram"]
    assert len(messages) == 1 and expected in messages[0]
    assert not any(event[0] in {"model", "tracking", "complete"} for event in events)


def test_unexpected_us_pipeline_failure_sends_only_safe_status(tmp_path):
    events = _run(tmp_path, "afternoon", "pipeline_failure")
    messages = [event[2] for event in events if event[0] == "telegram"]
    assert len(messages) == 1 and "전체 완료로 처리하지 않았습니다" in messages[0]
    assert "private-fixture-detail" not in messages[0]
    assert not any(event[0] in {"tracking", "complete"} for event in events)


def test_tracking_failure_does_not_mark_us_shadow_batch_complete(tmp_path):
    events = _run(tmp_path, "afternoon", "tracking_failure")
    assert ["tracking", ["DGX", "AAPL"]] in events
    assert not any(event[0] == "complete" for event in events)
    assert events.count(["end", "fixture"]) == 1


RUN_TRIGGER_EDGES = r'''
import json
from contextlib import ExitStack
from pathlib import Path
import socket
import sys
from unittest.mock import patch

root, mode, scenario, output = sys.argv[1:]
root, output = Path(root), Path(output)
sys.path[:0] = [str(root / "prism-us"), str(root)]
def forbidden(*args, **kwargs):
    raise AssertionError("Unexpected external access")
with ExitStack() as stack:
    stack.enter_context(patch.object(socket.socket, "connect", forbidden))
    stack.enter_context(patch("dotenv.load_dotenv", return_value=False))
    import pandas as pd
    import us_trigger_batch as batch
    snapshot = pd.DataFrame({"Open": [102., 103.], "High": [106., 108.],
        "Low": [100., 101.], "Close": [105., 107.], "Volume": [100., 200.],
        "Amount": [10500., 21400.]}, index=["AAA", "BBB"])
    previous = snapshot.copy()
    previous["Close"] = [100., 101.]
    previous["Volume"] = [50., 100.]
    if scenario == "liquidity_empty":
        trigger = (batch.trigger_morning_gap_up_momentum if mode == "morning"
                   else batch.trigger_afternoon_closing_strength)
        # Actual absolute liquidity filter removes all rows; index alignment
        # must not recreate previous-only rows and then crash on NaN/bool masks.
        assert batch.apply_absolute_filters(snapshot.copy(), min_value=batch.MIN_TRADING_VALUE).empty
        assert trigger("20260923", snapshot, previous).empty
    else:
        snapshot["Volume"] = [4e6, 5e6]
        snapshot["Amount"] = snapshot["Volume"] * snapshot["Close"]
        previous["Volume"] = 1e6
        stack.enter_context(patch.object(batch, "get_major_tickers", return_value=list(snapshot.index)))
        stack.enter_context(patch.object(batch, "get_snapshot", return_value=snapshot))
        stack.enter_context(patch.object(batch, "get_previous_snapshot", return_value=(previous, "20260922")))
        def failed(*args, **kwargs):
            raise ValueError("private provider response must not reach metadata")
        names = ["trigger_morning_volume_surge", "trigger_morning_gap_up_momentum",
                 "trigger_morning_value_to_cap_ratio", "trigger_afternoon_daily_rise_top",
                 "trigger_afternoon_closing_strength", "trigger_afternoon_volume_surge_flat",
                 "trigger_macro_sector_leader", "trigger_contrarian_value"]
        for name in names:
            stack.enter_context(patch.object(batch, name, side_effect=failed))
        cap = pd.DataFrame({"MarketCap": [1e11, 2e11]}, index=snapshot.index)
        stack.enter_context(patch.object(batch, "get_market_cap_df", side_effect=failed)
                            if scenario == "cap_error" else patch.object(batch, "get_market_cap_df", return_value=cap))
        # Fault injection only: the actual run_batch fail-open boundaries,
        # selection, metadata construction and JSON serialization still execute.
        result = batch.run_batch(mode, "ERROR", str(output), override_date="20260923",
                                 macro_context={"market_regime": "sideways"})
        assert result == {}
        metadata = json.loads(output.read_text())["metadata"]
        errors = metadata["trigger_errors"]
        expected = ({"Volume Surge Top", "Gap Up Momentum Top", "Value-to-Cap Ratio Top"}
                    if mode == "morning" else
                    {"Intraday Rise Top", "Closing Strength Top", "Volume Surge Sideways"})
        if scenario == "cap_error":
            expected.remove("Value-to-Cap Ratio Top")
            expected.add("Market Cap Lookup")
        expected |= {"Macro Sector Leader", "Contrarian Value Pick"}
        assert {row["trigger"] for row in errors} == expected, errors
        assert all(row["error_type"] == "ValueError" and set(row) == {"trigger", "error_type"} for row in errors)
        assert "private provider response" not in json.dumps(metadata)
        assert metadata["snapshot_coverage"]["status"] == "COMPLETE"
'''


@pytest.mark.parametrize("mode, scenario", [
    ("morning", "liquidity_empty"), ("afternoon", "liquidity_empty"),
    ("morning", "trigger_errors"), ("afternoon", "trigger_errors"), ("morning", "cap_error"),
])
def test_real_us_trigger_empty_liquidity_and_failure_metadata(tmp_path, mode, scenario):
    output = tmp_path / "trigger.json"
    result = subprocess.run(
        [sys.executable, "-c", RUN_TRIGGER_EDGES, str(ROOT), mode, scenario, str(output)],
        cwd=tmp_path, text=True, capture_output=True, timeout=35, check=False,
        env=dict(os.environ, PRISM_DISABLE_SIGNAL_PUBLISH="1", PRISM_OBSERVABILITY_SPOOL=str(tmp_path / "events.jsonl")),
    )
    assert result.returncode == 0, result.stdout + result.stderr
