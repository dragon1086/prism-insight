"""One real analysis case, not a scheduler, eligibility engine or OS sandbox.

CLI defaults to validation only. Programmatic execution requires the trusted
parent's namespace, lease, fixed invoker and explicit read-only MCP settings.
Production execution guards remain closed. Evidence clocks describe captured
inputs, never original decision snapshots or verified quote accuracy.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import hashlib
import importlib
import json
import logging
import math
import os
from pathlib import Path
import re
import stat
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.isolated_codex_invoker import InvocationAborted  # noqa: E402

SOURCE_ROOT = Path(__file__).resolve().parents[1]
RESULT_NAME = "case-result.json"
_ID = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
_SHA = re.compile(r"[a-f0-9]{64}\Z")
_CONTEXTS = {"quote", "rank", "trend", "regime", "journal"}


def _hash(value):
    data = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(data).hexdigest()


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _clock(value):
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError()
    return stamp


def validate_case(case, evidence_root):
    """Strict bounded JSON and source binding; never fetch/repair missing inputs."""
    try:
        if type(case) is not dict or len(json.dumps(case)) > 1024 * 1024:
            raise ValueError()
        required = {"schema_version", "case_id", "arm_id", "profile_id", "market", "side",
                    "ticker", "deadline_seconds", "contexts", "codex"}
        if not required <= case.keys() or set(case) - required - {"report", "synthetic_holding"}:
            raise ValueError()
        if type(case["schema_version"]) is not int or case["schema_version"] != 1:
            raise ValueError()
        if any(not isinstance(case[k], str) or not _ID.fullmatch(case[k])
               for k in ("case_id", "arm_id", "profile_id")):
            raise ValueError()
        if case["market"] not in {"KR", "US"} or case["side"] not in {"BUY", "SELL"}:
            raise ValueError()
        if not re.fullmatch(r"[A-Z0-9.-]{1,12}", case["ticker"]):
            raise ValueError()
        if not _number(case["deadline_seconds"]) or not 1 <= case["deadline_seconds"] <= 1800:
            raise ValueError()
        contexts = case["contexts"]
        if type(contexts) is not dict or set(contexts) != _CONTEXTS:
            raise ValueError()
        for envelope in contexts.values():
            if envelope is None:
                continue
            if type(envelope) is not dict or set(envelope) != {"value", "source", "as_of", "observed_at"}:
                raise ValueError()
            if not isinstance(envelope["source"], str) or not 1 <= len(envelope["source"]) <= 256:
                raise ValueError()
            if not _clock(envelope["as_of"]) <= _clock(envelope["observed_at"]) <= datetime.now(timezone.utc):
                raise ValueError()
        codex = case["codex"]
        if set(codex) != {"request_id", "revision", "settings_sha256", "expected_request"}:
            raise ValueError()
        if not _ID.fullmatch(codex["request_id"]) or not _SHA.fullmatch(codex["settings_sha256"]):
            raise ValueError()
        if not isinstance(codex["revision"], str) or not 1 <= len(codex["revision"]) <= 128:
            raise ValueError()
        expected = codex["expected_request"]
        if set(expected) != {"model", "reasoning_effort", "timeout", "mcp_profile", "require_mcp_calls"}:
            raise ValueError()
        if expected["mcp_profile"] != case["market"].lower() + "_trading" or expected["require_mcp_calls"] is not True:
            raise ValueError()
        if (expected["model"] not in {"gpt-6-astra", "gpt-5.6-sol"}
                or expected["reasoning_effort"] not in {None, "low", "medium", "high", "xhigh", "max", "ultra"}
                or not _number(expected["timeout"]) or not 0 < expected["timeout"] <= 600):
            raise ValueError()
        for name, envelope in contexts.items():
            if envelope is None:
                continue
            value = envelope["value"]
            if name == "quote" and (not _number(value) or value <= 0):
                raise ValueError()
            if name == "rank" and (type(value) is not dict or set(value) != {"percentage", "message"}
                    or not _number(value["percentage"]) or not isinstance(value["message"], str)):
                raise ValueError()
            if name == "trend" and (type(value) is not dict or not isinstance(value.get("facts"), str)
                    or set(value) - {"facts", "direction"}
                    or ("direction" in value and (type(value["direction"]) is not int or value["direction"] not in range(-2, 3)))):
                raise ValueError()
            if name == "regime" and (type(value) is not dict or value.get("regime") not in {"parabolic", "strong_bull", "moderate_bull", "sideways", "moderate_bear", "strong_bear"}
                    or set(value) - {"regime", "summary", "simple_market_condition"}
                    or ("summary" in value and not isinstance(value["summary"], str))
                    or ("simple_market_condition" in value and (type(value["simple_market_condition"]) is not int
                        or value["simple_market_condition"] not in {-1, 0, 1}))):
                raise ValueError()
            if name == "journal" and (type(value) is not dict or set(value) != {"context", "adjustment", "reasons"}
                    or not isinstance(value["context"], str) or not _number(value["adjustment"])
                    or type(value["reasons"]) is not list or not all(isinstance(v, str) for v in value["reasons"])):
                raise ValueError()
        if case["side"] == "BUY":
            report = case["report"]
            root = Path(evidence_root).resolve()
            relative = Path(report["path"])
            path = root / relative
            if set(report) != {"path", "sha256"} or relative.is_absolute() or ".." in relative.parts:
                raise ValueError()
            if path.resolve() != path or not path.is_file() or path.suffix.lower() != ".pdf":
                raise ValueError()
            if path.stat().st_size > 20 * 1024 * 1024 or _hash(path.read_bytes()) != report["sha256"]:
                raise ValueError()
        else:
            holding = case["synthetic_holding"]
            if holding.get("synthetic") is not True or not holding.get("company_name", "").startswith("SYNTHETIC "):
                raise ValueError()
            allowed = {"synthetic", "company_name", "buy_price", "buy_date", "target_price", "stop_loss", "scenario"}
            if set(holding) != allowed or not _number(holding["buy_price"]) or holding["buy_price"] <= 0:
                raise ValueError()
            if any(not _number(holding[key]) or holding[key] <= 0 for key in ("target_price", "stop_loss")) or type(holding["scenario"]) is not dict:
                raise ValueError()
            datetime.strptime(holding["buy_date"], "%Y-%m-%d %H:%M:%S")
        return json.loads(json.dumps(case))
    except (ValueError, TypeError, KeyError, OSError, OverflowError):
        raise ValueError("INVALID_CASE") from None


def _summary(case, status):
    return {"schema_version": 1, **{k: case[k] for k in ("case_id", "arm_id", "profile_id")},
            "status": status, "eligibility_evaluated": False, "prospective": False,
            "deviations": [], "source_hashes": {"case": _hash(case)}, "attempts": [],
            "selected_decision": None, "latency_s": 0.0}


def _buy_succeeded(result):
    if type(result) is not dict or result.get("success") is not True:
        return False
    scenario = result.get("scenario")
    return (type(scenario) is dict and scenario.get("analysis_status") != "failed"
            and not scenario.get("analysis_failed") and not scenario.get("analysis_error"))


def _primary_callback(case, invoker, attempts):
    invoked = False
    async def primary(**kwargs):
        nonlocal invoked
        if invoked:
            raise InvocationAborted("DUPLICATE_PRIMARY_ATTEMPT")
        invoked = True
        actual = {key: value for key, value in kwargs.items() if key not in {"system_prompt", "user_prompt"}}
        if actual != case["codex"]["expected_request"]:
            raise InvocationAborted("REQUEST_BINDING_MISMATCH")
        attempt = {"path": "CODEX_RPC", "system_sha256": _hash(kwargs["system_prompt"]),
                   "user_sha256": _hash(kwargs["user_prompt"]), "status": "UNKNOWN"}
        attempts.append(attempt)
        start = time.monotonic()
        acked_failure = False
        try:
            request = {key: case[key] for key in ("arm_id", "profile_id", "case_id")}
            request.update(request_id=case["codex"]["request_id"],
                           system_prompt=kwargs["system_prompt"], user_prompt=kwargs["user_prompt"])
            receipt = await invoker(request)
            if (type(receipt) is not dict or receipt.get("cleanup_ack") is not True
                    or receipt.get("revision") != case["codex"]["revision"]
                    or receipt.get("settings_sha256") != case["codex"]["settings_sha256"]
                    or receipt.get("revision_basis") != "HOST_REGISTERED_CASE_REVISION"
                    or receipt.get("cleanup_basis") != "TRUSTED_CALLBACK_CONTRACT_NOT_EXTERNAL_PROCESS_PROOF"
                    or not _SHA.fullmatch(receipt.get("snapshot_sha256", ""))):
                raise InvocationAborted("INVALID_RECEIPT")
            category = receipt.get("category")
            if category not in {"ok", "model_error", "model_timeout"} or receipt.get("fallback_allowed") is not (category != "ok"):
                raise InvocationAborted("INVALID_RECEIPT")
            attempt.update(status=category, snapshot_sha256=receipt["snapshot_sha256"])
            if category != "ok":
                acked_failure = True
                raise RuntimeError("ACKED_MODEL_FAILURE")
            try:
                result = json.loads(receipt["result_json"])
                if (set(result) != {"text", "latency_s", "usage", "mcp_calls"}
                        or not isinstance(result["text"], str) or not _number(result["latency_s"])
                        or result["latency_s"] < 0 or not isinstance(result["mcp_calls"], list)
                        or not result["mcp_calls"] or type(result["usage"]) not in (dict, type(None))):
                    raise ValueError()
                for call in result["mcp_calls"]:
                    if (type(call) is not dict or set(call) != {"server", "tool", "arguments", "status", "error"}
                            or not isinstance(call["server"], str) or not isinstance(call["tool"], str)
                            or type(call["arguments"]) is not dict or type(call["status"]) not in (str, type(None))):
                        raise ValueError()
            except (KeyError, ValueError, TypeError):
                raise InvocationAborted("INVALID_BACKEND_RESULT") from None
            attempt["response_sha256"] = _hash(result["text"])
            return SimpleNamespace(**result)
        except InvocationAborted:
            raise
        except RuntimeError as error:
            if acked_failure and str(error) == "ACKED_MODEL_FAILURE":
                raise
            raise InvocationAborted("INVOKER_UNKNOWN") from None
        except Exception:
            raise InvocationAborted("INVOKER_UNKNOWN") from None
        finally:
            attempt["latency_s"] = time.monotonic() - start
    return primary


def _load_agent(market, side):
    # Single fresh process only: KR and US have colliding package namespaces.
    root = SOURCE_ROOT / "prism-us" if market == "US" else SOURCE_ROOT
    for name in ("cores", "tracking", "trading"):
        module = sys.modules.get(name)
        if module is not None and Path(module.__file__).resolve().parent.parent != root:
            raise ValueError("FRESH_MARKET_PROCESS_REQUIRED")
    sys.path.insert(0, str(root))
    if market == "US":
        for name in ("cores", "tracking", "trading"):
            importlib.import_module(name)
    name = "us_stock_tracking_agent" if market == "US" else (
        "stock_tracking_enhanced_agent" if side == "SELL" else "stock_tracking_agent")
    module = importlib.import_module(name)
    cls = getattr(module, "USStockTrackingAgent" if market == "US" else (
        "EnhancedStockTrackingAgent" if side == "SELL" else "StockTrackingAgent"))
    return module, cls


def _bind_contexts(agent, case, summary):
    contexts = case["contexts"]
    def value(name):
        envelope = contexts[name]
        if envelope is None:
            summary["deviations"].append("MISSING_" + name.upper())
            return None
        summary["source_hashes"][name] = _hash(envelope)
        return envelope["value"]
    quote, rank, trend, regime, journal = (value(name) for name in ("quote", "rank", "trend", "regime", "journal"))
    if not _number(quote) or quote <= 0:
        raise ValueError("SOURCE_QUOTE_UNAVAILABLE")
    async def price(*args, **kwargs):
        return quote
    async def rank_change(*args, **kwargs):
        if rank is None:
            return None, "MISSING_CAPTURED_RANK_CONTEXT"
        return rank["percentage"], rank["message"]
    agent._get_current_stock_price = price
    agent._get_trading_value_rank_change = rank_change
    agent._get_trend_facts = lambda *a, **k: "" if trend is None else trend["facts"]
    agent._buy_floor_regime = lambda: None if regime is None else regime["regime"]
    agent._pipeline_market_regime = None if regime is None else regime["regime"]
    agent.simple_market_condition = None if regime is None else regime.get("simple_market_condition")
    agent._live_regime_summary = "MISSING_CAPTURED_REGIME" if regime is None else regime.get("summary", "")
    agent._live_regime_cache = None if regime is None else regime["regime"]
    async def trend_direction(*args, **kwargs):
        if trend is None or "direction" not in trend:
            raise ValueError("SOURCE_TREND_UNAVAILABLE")
        return trend["direction"]
    agent._analyze_trend = trend_direction
    if journal is None:
        summary["deviations"].append("JOURNAL_DISABLED_NOT_RECONSTRUCTED")
    else:
        for name in ("_get_relevant_journal_context", "get_journal_context"):
            setattr(agent, name, lambda *a, **k: journal["context"])
        for name in ("_get_score_adjustment_from_context", "get_score_adjustment"):
            setattr(agent, name, lambda *a, **k: (journal["adjustment"], journal["reasons"]))
    return quote


def _seed_holding(agent, case, quote):
    row = {key: value for key, value in case["synthetic_holding"].items() if key != "synthetic"}
    row.update(ticker=case["ticker"], current_price=quote,
               account_key="virtual:" + case["arm_id"], account_name="SHADOW " + case["arm_id"])
    row["scenario"] = json.dumps(row["scenario"], ensure_ascii=False)
    table = "us_stock_holdings" if case["market"] == "US" else "stock_holdings"
    columns = ",".join(row)
    agent.cursor.execute(f"INSERT INTO {table} ({columns}) VALUES ({','.join('?' for _ in row)})", tuple(row.values()))
    row["id"] = agent.cursor.lastrowid
    agent.conn.commit()
    return row


async def run_inner_case(case, *, arm_root, evidence_root, mcp_settings_factory, codex_invoker=None):
    """Trusted-parent API. Caller owns process isolation, lease and cleanup join."""
    case = validate_case(case, evidence_root)
    summary = _summary(case, "CODEX_INVOKER_PENDING")
    summary["deviations"] = ["MISSING_" + name.upper() for name in sorted(_CONTEXTS) if case["contexts"][name] is None]
    summary["deviations"] += ["CAPTURED_CONTEXT_NOT_ORIGINAL_DECISION_SNAPSHOT",
        "CAPTURED_QUOTE_NOT_LIVE_BROKER", "NOT_ELIGIBILITY_OR_PRODUCTION_PARITY"]
    summary["deviations"].append("MISSING_TRIGGER_CONTEXT" if case["side"] == "BUY" else "SYNTHETIC_ADJUSTMENT_HISTORY_EMPTY")
    if case["market"] == "KR" and case["side"] == "SELL":
        summary["deviations"].append("MISSING_KIS_CORPORATE_STATUS")
    if not callable(codex_invoker):
        return summary
    if case["contexts"]["quote"] is None:
        summary["status"] = "SOURCE_QUOTE_UNAVAILABLE"
        return summary
    if any((Path(arm_root) / name).exists() for name in (RESULT_NAME, "case-private-result.json")):
        summary["status"] = "CASE_ALREADY_HAS_RESULT"
        return summary
    start = time.monotonic()
    agent = None
    previous_logging = logging.root.manager.disable
    try:
        # The parent runs exactly one case per process. Suppress source logs,
        # never return arbitrary provider exceptions or source-generated prose.
        logging.disable(logging.CRITICAL)
        with open(os.devnull, "w") as sink, redirect_stdout(sink), redirect_stderr(sink), ExitStack() as stack:
            module, cls = _load_agent(case["market"], case["side"])
            account = {"name": "SHADOW " + case["arm_id"], "account_key": "virtual:" + case["arm_id"],
                       "market": case["market"].lower(), "virtual": True}
            agent = cls(db_path=str(Path(arm_root) / "state.sqlite"), enable_journal=case["contexts"]["journal"] is not None,
                        virtual_accounts=[account], isolated_db_root=str(arm_root), mcp_settings_factory=mcp_settings_factory)
            if not await agent.initialize():
                raise ValueError("INITIALIZE_FAILED")
            quote = _bind_contexts(agent, case, summary)
            stack.enter_context(patch.object(module, "generate_codex_fast_async",
                _primary_callback(case, codex_invoker, summary["attempts"])))
            original_llm = module.OpenAIAugmentedLLM
            class ObservedLLM(original_llm):
                async def generate_str(self, message, *args, **kwargs):
                    source_agent = agent.trading_agent if case["side"] == "BUY" else agent.sell_decision_agent
                    attempt = {"path": "LEGACY_RESPONSES", "user_sha256": _hash(message),
                               "system_sha256": _hash(source_agent.instruction), "status": "UNKNOWN"}
                    summary["attempts"].append(attempt)
                    then = time.monotonic()
                    try:
                        response = await super().generate_str(message, *args, **kwargs)
                        attempt.update(status="ok", response_sha256=_hash(response))
                        return response
                    finally:
                        attempt["latency_s"] = time.monotonic() - then
            stack.enter_context(patch.object(module, "OpenAIAugmentedLLM", ObservedLLM))
            flag = f"PRISM_{case['market']}_CODEX_FAST_{'TRADING' if case['side'] == 'BUY' else 'SELL'}"
            if os.environ.get(flag, "").lower() not in {"1", "true", "yes", "on"}:
                raise ValueError("PRIMARY_NOT_ENABLED_BY_PARENT")
            async with asyncio.timeout(case["deadline_seconds"]):
                if case["side"] == "BUY":
                    report = Path(evidence_root) / case["report"]["path"]
                    ticker, _ = await agent._extract_ticker_info(str(report))
                    if ticker != case["ticker"]:
                        raise ValueError("REPORT_TICKER_MISMATCH")
                    result = await agent._analyze_report_core(str(report))
                    if not _buy_succeeded(result):
                        raise ValueError("SOURCE_ANALYSIS_FAILED")
                    decision = result.get("decision")
                else:
                    summary["deviations"].append("SYNTHETIC_HOLDING_NOT_PROSPECTIVE")
                    original_fallback = agent._fallback_sell_decision
                    async def observed_fallback(*args, **kwargs):
                        summary["attempts"].append({"path": "SOURCE_RULE_FALLBACK"})
                        return await original_fallback(*args, **kwargs)
                    agent._fallback_sell_decision = observed_fallback
                    result = await agent._analyze_sell_decision(_seed_holding(agent, case, quote))
                    decision = "SELL" if result[0] else "HOLD"
            summary["selected_decision"] = decision if decision in {"BUY", "SELL", "HOLD", "Enter", "Watch", "No Entry", "Entry", "No entry", "entry", "no_entry", "skip", "Skip"} else "OTHER_SOURCE_DECISION"
            summary["status"] = ("SOURCE_RULE_FALLBACK" if any(a["path"] == "SOURCE_RULE_FALLBACK" for a in summary["attempts"])
                else "ANALYSIS_COMPLETE" if summary["attempts"] else "SOURCE_NON_MODEL_DECISION")
            _write_exclusive(Path(arm_root) / "case-private-result.json", result)
    except InvocationAborted:
        summary["status"] = "UNKNOWN"
    except (TimeoutError, asyncio.CancelledError):
        summary["status"] = "UNKNOWN"
    except Exception:
        summary["status"] = "ANALYSIS_NOT_COMPLETED"
    finally:
        if agent is not None and getattr(agent, "conn", None) is not None:
            agent.conn.close()
        logging.disable(previous_logging)
        summary["latency_s"] = time.monotonic() - start
        summary["deviations"] = sorted(set(summary["deviations"]))
        for name, module in tuple(sys.modules.items()):
            file = getattr(module, "__file__", None)
            if file:
                path = Path(file).resolve()
                if path.is_relative_to(SOURCE_ROOT) and path.is_file() and path.suffix == ".py":
                    summary["source_hashes"][str(path.relative_to(SOURCE_ROOT))] = _hash(path.read_bytes())
    return summary


def _write_exclusive(path, value):
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())


def validate_registration(case, case_bytes, registration):
    """Host RO marker binding, NOT cryptographic authorization or OS proof."""
    expected = {"schema_version": 1, "case_sha256": _hash(case_bytes),
                **{key: case[key] for key in ("arm_id", "profile_id", "case_id")},
                **{key: case["codex"][key] for key in ("request_id", "revision", "settings_sha256")}}
    if type(registration) is not dict or registration != expected:
        raise ValueError("INVALID_PARENT_REGISTRATION")


def fixed_mcp_settings(market, base_url):
    """Only parent-mounted fixed read services; no manifest-selected argv/env."""
    python = "/app/runtime/bin/python3.11"
    bridge = "/app/src/tools/codex_probe_mcp_bridge.py"
    def server(args):
        return {"command": python, "args": args, "transport": "stdio"}
    return {"openai": {"base_url": base_url, "api_key": "isolated-parent-relay"}, "mcp": {"servers": {
        "sqlite": server(["/app/src/tools/isolated_sqlite_mcp.py", "--db-path", "/arm/state.sqlite"]),
        "perplexity": server([bridge, "--client", "/perplexity.sock"]),
        "kospi_kosdaq" if market == "KR" else "yahoo_finance": server([bridge, "--client", "/market.sock"]),
        "time": server(["/app/src/cores/llm/time_mcp_server.py"]),
    }}}


async def _execute_registered(case, raw):
    from tools.isolated_codex_invoker import invoke
    from tools.isolated_responses_relay import ResponsesRelay
    marker = Path("/parent-case.json")
    if marker.is_symlink() or not marker.is_file() or marker.stat().st_size > 16384:
        raise ValueError("INVALID_PARENT_REGISTRATION")
    validate_registration(case, raw, json.loads(marker.read_text()))
    for name in ("codex-invoke", "responses", "perplexity", "market"):
        path = Path("/" + name + ".sock")
        if path.is_symlink() or not stat.S_ISSOCK(path.stat().st_mode):
            raise ValueError("PARENT_SOCKET_UNAVAILABLE")
    async def invoker(request):
        return await invoke("/codex-invoke.sock", request, deadline=case["deadline_seconds"])
    # The relay joins its own connections before the parent reads completion.
    with ResponsesRelay() as relay:
        result = await run_inner_case(case, arm_root=Path("/arm"), evidence_root=Path("/evidence"),
            mcp_settings_factory=lambda: fixed_mcp_settings(case["market"], relay.base_url), codex_invoker=invoker)
    _write_exclusive(Path("/arm") / RESULT_NAME, result)
    return result


def main(argv=None):
    class SafeParser(argparse.ArgumentParser):
        def error(self, message):
            raise ValueError("INVALID_ARGUMENTS")
    parser = SafeParser(description=__doc__)
    parser.add_argument("--case", required=True)
    parser.add_argument("--execute-registered", action="store_true")
    try:
        args = parser.parse_args(argv)
        path = Path(args.case)
        raw = path.read_bytes()
        case = validate_case(json.loads(raw), path.parent)
        if not args.execute_registered:
            print(json.dumps(_summary(case, "PENDING_PARENT_NAMESPACE")))
            return 0
        if path.parent != Path("/evidence") or path.is_symlink():
            raise ValueError("INVALID_REGISTERED_CASE_PATH")
        result = asyncio.run(_execute_registered(case, raw))
        print(json.dumps({key: result[key] for key in ("schema_version", "case_id", "arm_id", "profile_id", "status")}))
        return 0 if result["status"] in {"ANALYSIS_COMPLETE", "SOURCE_NON_MODEL_DECISION", "SOURCE_RULE_FALLBACK"} else (3 if result["status"] == "UNKNOWN" else 2)
    except Exception:
        print('{"schema_version":1,"status":"INVALID_CASE"}')
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
