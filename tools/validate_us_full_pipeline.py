"""Operator-only real US pipeline validation; never a trading execution adapter.

Run from a disposable worktree with an allowlisted environment. The operator
must configure the report proxy and a private, read-only ``us_trading`` Codex
profile before invocation. No production account/database is imported.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import json
import logging
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
STAGES = ("run_macro_intelligence", "run_trigger_batch", "generate_reports",
          "convert_to_pdf", "generate_telegram_messages")
DEVIATIONS = [
    "BUY-analysis integration, not production process_reports/portfolio/order parity",
    "Empty private virtual account; no existing holdings or SELL analysis",
    "Journal feature follows environment but private database has no prior trading history",
    "All publisher and archive ingest calls intercepted and recorded",
    "Telegram disabled; public translated broadcasts not exercised",
    "Execution guards and EFFECTS_RUNTIME_ENABLED remain unchanged",
]


def check_environment(proxy_url: str, environ=None):
    env = os.environ if environ is None else environ
    parsed = urlsplit(proxy_url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.username or parsed.password:
        raise ValueError("Explicit local HTTP proxy required")
    if env.get("PYTHON_DOTENV_DISABLED") != "1":
        raise ValueError("PYTHON_DOTENV_DISABLED=1 required before imports")
    forbidden = [key for key, value in env.items() if value and (
        key.startswith(("TELEGRAM_", "KIS_", "BYBIT_", "AWS_", "GOOGLE_APPLICATION_CREDENTIALS"))
        or key in {"REDIS_URL", "DATABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"}
    )]
    if forbidden:
        raise ValueError("Publication/trading credentials forbidden: " + ", ".join(sorted(forbidden)))
    if not env.get("PRISM_CODEX_HOME"):
        raise ValueError("Private PRISM_CODEX_HOME required")
    if env.get("PRISM_DISABLE_SIGNAL_PUBLISH") != "1":
        raise ValueError("PRISM_DISABLE_SIGNAL_PUBLISH=1 required")


def prepare_output(path: Path):
    path = path.absolute()
    if path.is_symlink() or path.resolve().is_relative_to(ROOT):
        raise ValueError("Output must be private and outside the source worktree")
    path.mkdir(mode=0o700, parents=True, exist_ok=False)
    path.chmod(0o700)
    return path


def validate_receipt(receipt):
    errors = []
    for stage in STAGES:
        row = receipt["stages"].get(stage, {})
        if row.get("status") != "ok" or not row.get("result"):
            errors.append(f"Required stage missing, empty, or failed: {stage}")
    stages = receipt["stages"]
    selected = len(stages.get("run_trigger_batch", {}).get("result") or [])
    for stage in ("generate_reports", "convert_to_pdf", "generate_telegram_messages"):
        outputs = stages.get(stage, {}).get("result") or []
        if len(outputs) != selected:
            errors.append(f"Selected/artifact count mismatch: {stage}")
        for output in outputs:
            if not isinstance(output, (str, os.PathLike)) or not Path(output).is_file() or Path(output).stat().st_size == 0:
                errors.append(f"Missing or empty artifact: {stage}")
    analyses = receipt.get("buy_analyses", [])
    if not selected or len(analyses) != selected or any(not row.get("success") for row in analyses):
        errors.append("BUY analyses missing or unsuccessful")
    if any(any(f.get("code") == "buy_gate_error" for f in row.get("gate", {}).get("findings", [])) for row in analyses):
        errors.append("Deterministic gate failed")
    if receipt.get("codex_primary_required"):
        successful_primary = [line for line in receipt.get("usage_log", [])
                              if "[CODEX_FAST] US scenario" in line and "parse_ok=True" in line]
        if len(successful_primary) != selected:
            errors.append("Cron Codex primary backend did not successfully analyze every selected report")
    return errors


def readonly_settings(db_path, proxy_url):
    return {"openai": {"api_key": "local-proxy-placeholder", "base_url": proxy_url},
            "mcp": {"servers": {
                "sqlite": {"command": sys.executable, "args": [str(ROOT / "tools/isolated_sqlite_mcp.py"),
                                                                       "--db-path", str(db_path)]},
                "time": {"command": sys.executable, "args": [str(ROOT / "cores/llm/time_mcp_server.py")]},
            }}}


def create_empty_shared_journal(cursor, connection):
    """Match production's shared KR/US schema, never copy account/history rows."""
    spec = importlib.util.spec_from_file_location("validation_shared_schema", ROOT / "tracking/db_schema.py")
    schema = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(schema)
    for name in ("TABLE_TRADING_JOURNAL", "TABLE_TRADING_INTUITIONS", "TABLE_TRADING_PRINCIPLES"):
        cursor.execute(getattr(schema, name))
    connection.commit()


async def run_validation(args):
    check_environment(args.proxy_url)
    os.umask(0o077)
    output = prepare_output(Path(args.output_root))
    sys.path[:0] = [str(ROOT / "prism-us"), str(ROOT)]
    receipt = {"schema_version": 1, "mode": args.mode, "date": args.date,
               "deviations": DEVIATIONS, "stages": {}, "blocked_effects": [],
               "buy_analyses": [], "usage_log": [], "degraded_logs": [], "errors": []}
    receipt["codex_primary_required"] = os.getenv("PRISM_US_CODEX_FAST_TRADING") == "1"
    receipt["source_hashes"] = {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        for name in ("prism-us/us_stock_analysis_orchestrator.py", "prism-us/cores/us_analysis.py",
                     "prism-us/cores/agents/trading_agents.py", "requirements.txt")
    }

    class UsageHandler(logging.Handler):
        def emit(self, record):
            message = record.getMessage()
            if "[LLM_USAGE]" in message or "[CODEX_FAST]" in message:
                receipt["usage_log"].append(message)
            if record.levelno >= logging.ERROR or any(word in message.lower() for word in (
                    "fallback", "degraded", "analysis_failed", "failed to", "timed out")):
                receipt["degraded_logs"].append({"logger": record.name, "level": record.levelname,
                                                 "message": message[:2000]})

    usage_handler = UsageHandler()
    console_handler = logging.StreamHandler()
    logging.getLogger().setLevel(logging.INFO)
    logging.getLogger().addHandler(usage_handler)
    logging.getLogger().addHandler(console_handler)
    try:
        module = importlib.import_module("us_stock_analysis_orchestrator")
        for attribute, directory in (("US_REPORTS_DIR", "reports"), ("US_PDF_REPORTS_DIR", "pdf_reports"),
                                     ("US_TELEGRAM_MSGS_DIR", "telegram_messages")):
            folder = output / "artifacts" / directory
            folder.mkdir(parents=True, mode=0o700)
            setattr(module, attribute, folder)
        tracker_module = importlib.import_module("us_stock_tracking_agent")
        from prism_core.isolated_agent_runtime import ROOT_MARKER
        from telegram_config import TelegramConfig
        db_root = output / "private-account"
        db_root.mkdir(mode=0o700)
        (db_root / ROOT_MARKER).write_text(json.dumps({
            "schema_version": 1, "purpose": "PRISM_AGENT_SHADOW", "market": "US",
            "runtime_id": "us-full-validation",
        }))
        db_path = db_root / "tracking.sqlite"
        def settings():
            return readonly_settings(db_path, args.proxy_url)

        async def blocked(name, *unused, **kwargs):
            receipt["blocked_effects"].append({"effect": name, "blocked": True})

        def blocker(name):
            async def call(*positional, **kwargs):
                return await blocked(name, *positional, **kwargs)
            return call

        for name in ("publish_batch_campaign_best_effort", "publish_batch_reports_best_effort",
                     "publish_batch_tracking_story_best_effort"):
            setattr(module, name, blocker(name))
        module._import_main_archive_ingest = lambda: SimpleNamespace(ingest_reports_async=blocker("archive_ingest"))
        for publisher in ("messaging.redis_signal_publisher", "messaging.gcp_pubsub_signal_publisher"):
            # Replace before any dynamic import in a trading method, without
            # loading provider clients or their ambient credentials.
            sys.modules[publisher] = SimpleNamespace(
                publish_buy_signal=blocker(publisher + ".buy"),
                publish_sell_signal=blocker(publisher + ".sell"),
                get_signal_publisher=blocker(publisher + ".client"),
            )

        def forbidden_broker(*args, **kwargs):
            receipt["blocked_effects"].append({"effect": "broker_access", "blocked": True})
            raise RuntimeError("Broker access forbidden in local validation")

        tracker_module._get_kis_auth = forbidden_broker

        class AnalysisOnlyTracker(tracker_module.USStockTrackingAgent):
            def __init__(self, **unused):
                super().__init__(db_path=str(db_path),
                                 virtual_accounts=[{"name": "SHADOW validation", "account_key": "virtual:validation",
                                                    "market": "us", "virtual": True}],
                                 isolated_db_root=db_root, mcp_settings_factory=settings)

            async def run(self, paths, chat_id=None, language="ko", **kwargs):
                self.telegram_config = kwargs.get("telegram_config")
                self._pipeline_market_regime = kwargs.get("market_regime")
                self._pipeline_market_context = kwargs.get("market_context")
                self.trigger_info_map = {}
                trigger_file = kwargs.get("trigger_results_file")
                if trigger_file and Path(trigger_file).is_file():
                    data = json.loads(Path(trigger_file).read_text())
                    for trigger, rows in data.items():
                        if isinstance(rows, list):
                            for row in rows:
                                self.trigger_info_map[row.get("ticker", row.get("code", ""))] = {
                                    "trigger_type": trigger, "trigger_mode": data.get("metadata", {}).get("trigger_mode", ""),
                                    "risk_reward_ratio": row.get("risk_reward_ratio"),
                                }
                await self.initialize(language)
                create_empty_shared_journal(self.cursor, self.conn)
                tracker_module.add_market_column_to_shared_tables(self.cursor, self.conn)
                try:
                    semaphore = asyncio.Semaphore(3)

                    async def analyze(path):
                        async with semaphore:
                            result = await self._analyze_report_core(path)
                            if result.get("success"):
                                result["gate"] = self._evaluate_production_buy_gate(result["scenario"], result["current_price"])
                            return result

                    receipt["buy_analyses"].extend(await asyncio.gather(*(analyze(path) for path in paths)))
                    return all(row.get("success") for row in receipt["buy_analyses"])
                finally:
                    if self.conn:
                        self.conn.close()

        tracker_module.USStockTrackingAgent = AnalysisOnlyTracker
        orchestrator = module.USStockAnalysisOrchestrator(TelegramConfig(use_telegram=False))
        for name in STAGES:
            original = getattr(orchestrator, name)

            async def observed(*positional, _original=original, _name=name, **kwargs):
                started = time.monotonic()
                row = receipt["stages"][_name] = {"status": "started"}
                (output / "progress.json").write_text(json.dumps({"stage": _name, "status": "started"}))
                try:
                    result = await _original(*positional, **kwargs)
                    row.update(status="ok", result=result)
                    return result
                except Exception as exc:
                    row.update(status="error", error_type=type(exc).__name__)
                    raise
                finally:
                    row["duration_seconds"] = round(time.monotonic() - started, 3)
            setattr(orchestrator, name, observed)
        await orchestrator.run_full_pipeline(args.mode, language="ko", override_date=args.date)
        await asyncio.sleep(0)  # Flush the intercepted archive background task.
        receipt["errors"] += validate_receipt(receipt)
        receipt["report_quality_checks"] = []
        for report in receipt["stages"].get("generate_reports", {}).get("result") or []:
            content = Path(report).read_text(encoding="utf-8")
            markers = ("analysis failed", "분석 실패", "분석 중 오류", "분석을 완료하지 못", "분석 결과 없음")
            failures = [marker for marker in markers if marker in content.lower()]
            receipt["report_quality_checks"].append({"report": report, "characters": len(content),
                                                     "degraded_markers": failures})
            if failures:
                receipt["errors"].append("Report contains failed-analysis placeholder")
    except Exception as exc:
        receipt["errors"].append({"error_type": type(exc).__name__})
        logging.getLogger(__name__).exception("Local validation failed")
    finally:
        if "module" in locals():
            cleanup = module._import_from_main_cores("validation_runtime_cleanup", "cores/llm/runtime_cleanup.py")
            receipt["cleanup_ok"] = await cleanup.shutdown_mcp_logging()
            receipt["remaining_task_types"] = cleanup.pending_runtime_tasks()
            if not receipt["cleanup_ok"]:
                receipt["errors"].append("MCP logging cleanup incomplete")
        logging.getLogger().removeHandler(usage_handler)
        logging.getLogger().removeHandler(console_handler)
        receipt["success"] = not receipt["errors"]
        receipt["degraded_log_count"] = len(receipt["degraded_logs"])
        receipt["standard_logging_clean"] = not receipt["degraded_logs"]
        receipt["log_capture_scope"] = "stdlib logging only; inspect process stderr separately for MCP framework errors"
        (output / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2, default=str))
    return 0 if receipt["success"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("morning", "afternoon"), default="morning")
    parser.add_argument("--date")
    parser.add_argument("--output-root")
    parser.add_argument("--proxy-url", default="http://127.0.0.1:8080/v1")
    args = parser.parse_args()
    if not args.output_root or not args.date:
        parser.error("--output-root and --date required")
    return asyncio.run(run_validation(args))


if __name__ == "__main__":
    raise SystemExit(main())
