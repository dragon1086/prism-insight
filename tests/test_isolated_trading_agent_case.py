"""Single-case harness boundaries; backend doubles are never real model evidence."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tools import isolated_trading_agent_case as case_tool
from tools.isolated_codex_invoker import InvocationAborted


@pytest.fixture
def case(tmp_path):
    pdf = tmp_path / "005930_Synthetic.pdf"
    pdf.write_bytes(b"synthetic PDF boundary fixture")
    return {
        "schema_version": 1, "case_id": "test_case", "arm_id": "baseline",
        "profile_id": "kr_trading", "market": "KR", "side": "BUY", "ticker": "005930",
        "deadline_seconds": 60,
        "report": {"path": pdf.name, "sha256": hashlib.sha256(pdf.read_bytes()).hexdigest()},
        "contexts": {name: None for name in ("quote", "rank", "trend", "regime", "journal")},
        "codex": {"request_id": "request1", "revision": "registered1", "settings_sha256": "a" * 64,
                  "expected_request": {"model": "gpt-6-astra", "reasoning_effort": "high",
                                       "timeout": 30, "mcp_profile": "kr_trading", "require_mcp_calls": True}},
    }


def test_hash_and_path_validation(case, tmp_path):
    assert case_tool.validate_case(case, tmp_path)["case_id"] == "test_case"
    case["report"]["sha256"] = "b" * 64
    with pytest.raises(ValueError, match="INVALID_CASE"):
        case_tool.validate_case(case, tmp_path)
    case["report"]["path"] = "../outside.pdf"
    with pytest.raises(ValueError, match="INVALID_CASE"):
        case_tool.validate_case(case, tmp_path)


def test_missing_invoker_does_not_import_agent(case, tmp_path, monkeypatch):
    monkeypatch.setattr(case_tool, "_load_agent", lambda *_: pytest.fail("agent imported"))
    result = asyncio.run(case_tool.run_inner_case(case, arm_root=tmp_path,
        evidence_root=tmp_path, mcp_settings_factory=lambda: {}))
    assert result["status"] == "CODEX_INVOKER_PENDING"
    assert result["eligibility_evaluated"] is False


def test_unknown_receipt_bypasses_ordinary_fallback(case):
    async def bad(_):
        return {"category": "ok", "cleanup_ack": False, "secret": "CANARY"}
    attempts = []
    callback = case_tool._primary_callback(case, bad, attempts)
    with pytest.raises(InvocationAborted):
        asyncio.run(callback(system_prompt="system", user_prompt="user",
                             **case["codex"]["expected_request"]))
    assert "CANARY" not in json.dumps(attempts)


def test_request_mismatch_never_calls_host(case):
    async def forbidden(_):
        pytest.fail("host invoked")
    callback = case_tool._primary_callback(case, forbidden, [])
    with pytest.raises(InvocationAborted):
        asyncio.run(callback(system_prompt="system", user_prompt="user", model="wrong"))


def test_transport_error_cannot_spoof_clean_model_failure(case):
    async def unknown(_):
        raise RuntimeError("ACKED_MODEL_FAILURE")
    with pytest.raises(InvocationAborted):
        asyncio.run(case_tool._primary_callback(case, unknown, [])(
            system_prompt="system", user_prompt="user", **case["codex"]["expected_request"]))


def test_clean_model_error_raises_ordinary_fallback_signal(case):
    async def error(_):
        return {"category": "model_error", "cleanup_ack": True, "fallback_allowed": True,
                "snapshot_sha256": "b" * 64, "revision": "registered1",
                "revision_basis": "HOST_REGISTERED_CASE_REVISION", "settings_sha256": "a" * 64,
                "cleanup_basis": "TRUSTED_CALLBACK_CONTRACT_NOT_EXTERNAL_PROCESS_PROOF"}
    with pytest.raises(RuntimeError, match="ACKED_MODEL_FAILURE"):
        asyncio.run(case_tool._primary_callback(case, error, [])(
            system_prompt="system", user_prompt="user", **case["codex"]["expected_request"]))


def test_context_clock_future_rejected(case, tmp_path):
    case["contexts"]["quote"] = {"value": 100, "source": "fixture", "as_of": "2099-01-01T00:00:00Z",
                                    "observed_at": "2099-01-01T00:00:00Z"}
    with pytest.raises(ValueError, match="INVALID_CASE"):
        case_tool.validate_case(case, tmp_path)


@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize("side", ["BUY", "SELL"])
@pytest.mark.parametrize("backend", ["primary", "legacy"])
def test_real_source_methods_on_private_db(case, tmp_path, market, side, backend):
    """Genuine source methods; only PDF reader and model transport are doubles."""
    case.update(market=market, side=side, ticker="005930" if market == "KR" else "AAPL")
    if side == "SELL":
        case.pop("report")
    else:
        pdf = tmp_path / (case["ticker"] + "_SYNTHETIC.pdf")
        pdf.write_bytes(b"synthetic reader fixture")
        case["report"] = {"path": pdf.name, "sha256": hashlib.sha256(pdf.read_bytes()).hexdigest()}
    case["codex"]["expected_request"]["mcp_profile"] = market.lower() + "_trading"
    case["codex"]["expected_request"].update(model="gpt-6-astra", reasoning_effort="high", timeout=30)
    case["contexts"]["quote"] = {"value": 101, "source": "SYNTHETIC test double",
        "as_of": "2026-01-01T00:00:00Z", "observed_at": "2026-01-01T00:00:00Z"}
    case["synthetic_holding"] = {"synthetic": True, "company_name": "SYNTHETIC boundary only",
        "buy_price": 100, "buy_date": "2026-01-01 09:00:00", "target_price": 120,
        "stop_loss": 90, "scenario": {"highest_price": 101}}
    path = tmp_path / "case.json"
    path.write_text(json.dumps(case))
    script = r'''
import asyncio, json, os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from tools.isolated_trading_agent_case import run_inner_case
import tools.isolated_trading_agent_case as harness
import pdf_converter
pdf_converter.pdf_to_markdown_text = lambda path: 'SYNTHETIC PDF READER CANARY'
case = json.loads(Path(sys.argv[2]).read_text())
arm = Path.cwd() / 'arm'
arm.mkdir(mode=0o700)
(arm / '.prism-agent-isolation.json').write_text(json.dumps({
    'schema_version':1, 'purpose':'PRISM_AGENT_SHADOW', 'market':case['market'], 'runtime_id':'test'}))
(arm / '.prism-agent-isolation.json').chmod(0o600)
os.environ['PRISM_' + case['market'] + '_CODEX_FAST_SELL'] = '1'
os.environ['PRISM_SELL_CODEX_MODEL'] = 'gpt-6-astra'
os.environ['PRISM_SELL_CODEX_EFFORT'] = 'high'
os.environ['PRISM_SELL_CODEX_TIMEOUT'] = '30'
os.environ['PRISM_' + case['market'] + '_CODEX_FAST_TRADING'] = '1'
os.environ['PRISM_BUY_CODEX_MODEL'] = 'gpt-6-astra'
os.environ['PRISM_BUY_CODEX_EFFORT'] = 'high'
os.environ['PRISM_BUY_CODEX_TIMEOUT'] = '30'
calls = []
legacy_calls = []
text = json.dumps({'should_sell':False, 'sell_reason':'SYNTHETIC backend fixture', 'confidence':5,
    'decision':'No Entry', 'buy_score':1, 'target_price':120, 'stop_loss':90,
    'investment_period':'Short-term','sector':'Unknown','rationale':'SYNTHETIC'})
load = harness._load_agent
def observed_load(*args):
    module, cls = load(*args)
    async def synthetic_legacy(self, message, *args, **kwargs):
        legacy_calls.append(message)
        return text
    module.OpenAIAugmentedLLM.generate_str = synthetic_legacy
    return module, cls
harness._load_agent = observed_load
async def synthetic_backend(request):
    calls.append(request)
    receipt = {'category':'ok','cleanup_ack':True,'fallback_allowed':False,
        'snapshot_sha256':'b'*64,'revision':'registered1','settings_sha256':'a'*64,
        'revision_basis':'HOST_REGISTERED_CASE_REVISION',
        'cleanup_basis':'TRUSTED_CALLBACK_CONTRACT_NOT_EXTERNAL_PROCESS_PROOF',
        'result_json':json.dumps({'text':text,
            'latency_s':0.01,'usage':None,'mcp_calls':[{'server':'sqlite','tool':'list_tables',
                'arguments':{},'status':'completed','error':None}]})}
    if sys.argv[3] == 'legacy':
        receipt.update(category='model_error', fallback_allowed=True)
        receipt.pop('result_json')
    return receipt
result = asyncio.run(run_inner_case(case, arm_root=arm, evidence_root=Path.cwd(),
    mcp_settings_factory=lambda: {'openai':{'base_url':'http://127.0.0.1:1/v1','api_key':'SYNTHETIC'},'mcp':{'servers':{}}},
    codex_invoker=synthetic_backend))
print(json.dumps({key:value for key,value in result.items() if key != 'source_hashes'}))
assert result['status'] == 'ANALYSIS_COMPLETE', result
assert calls and result['attempts'][0]['path'] == 'CODEX_RPC', result
assert result['eligibility_evaluated'] is False
assert bool(legacy_calls) == (sys.argv[3] == 'legacy')
if case['side'] == 'BUY':
    assert 'SYNTHETIC PDF READER CANARY' in calls[0]['user_prompt']
'''
    output = subprocess.run([sys.executable, "-c", script, str(Path(case_tool.__file__).resolve().parents[1]), str(path), backend],
        cwd=tmp_path, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, capture_output=True, text=True, timeout=40)
    assert output.returncode == 0, output.stdout[-3000:] + output.stderr[-2000:]


def test_failed_scenario_is_not_success():
    assert not case_tool._buy_succeeded({"success": True, "scenario": {"analysis_status": "failed"}})
    assert not case_tool._buy_succeeded({"success": True, "scenario": {"analysis_failed": True}})
    assert case_tool._buy_succeeded({"success": True, "scenario": {"decision": "No Entry"}})


def test_registration_cannot_choose_endpoint(case):
    raw = json.dumps(case).encode()
    marker = {"schema_version": 1, "case_sha256": hashlib.sha256(raw).hexdigest(),
        **{key: case[key] for key in ("arm_id", "profile_id", "case_id")},
        **{key: case["codex"][key] for key in ("request_id", "revision", "settings_sha256")}}
    case_tool.validate_registration(case, raw, marker)
    marker["endpoint"] = "http://untrusted"
    with pytest.raises(ValueError, match="INVALID_PARENT_REGISTRATION"):
        case_tool.validate_registration(case, raw, marker)


def test_cli_default_and_invalid_execution_never_load(case, tmp_path, monkeypatch, capsys):
    path = tmp_path / "case.json"
    path.write_text(json.dumps(case))
    monkeypatch.setattr(case_tool, "_load_agent", lambda *_: pytest.fail("agent loaded"))
    monkeypatch.setattr(case_tool, "_execute_registered", lambda *_: pytest.fail("executor entered"))
    assert case_tool.main(["--case", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "PENDING_PARENT_NAMESPACE"
    assert case_tool.main(["--case", str(path), "--execute-registered"]) == 2
    assert case_tool.main(["--case", str(path), "--CANARY_SECRET"]) == 2
    assert "CANARY_SECRET" not in capsys.readouterr().out


def test_fixed_services_have_no_manifest_selected_commands():
    for market, name in (("KR", "kospi_kosdaq"), ("US", "yahoo_finance")):
        settings = case_tool.fixed_mcp_settings(market, "http://127.0.0.1:123/v1")
        servers = settings["mcp"]["servers"]
        assert set(servers) == {"sqlite", "perplexity", "time", name}
        assert all(server["command"] == "/app/runtime/bin/python3.11" for server in servers.values())
        assert servers[name]["args"] == ["/app/src/tools/codex_probe_mcp_bridge.py", "--client", "/market.sock"]
        assert servers["perplexity"]["args"][-1] == "/perplexity.sock"
        assert servers["sqlite"]["args"] == ["/app/src/tools/isolated_sqlite_mcp.py", "--db-path", "/arm/state.sqlite"]
        assert servers["time"]["args"] == ["/app/src/cores/llm/time_mcp_server.py"]


@pytest.mark.parametrize("regime", ["parabolic", "strong_bull", "moderate_bull", "sideways", "moderate_bear", "strong_bear"])
def test_source_regime_vocabulary_preserved(case, tmp_path, regime):
    case["contexts"]["regime"] = {"value": {"regime": regime}, "source": "fixture",
        "as_of": "2026-01-01T00:00:00Z", "observed_at": "2026-01-01T00:00:00Z"}
    case["codex"]["expected_request"]["model"] = "gpt-5.6-sol"
    assert case_tool.validate_case(case, tmp_path)["contexts"]["regime"]["value"]["regime"] == regime
