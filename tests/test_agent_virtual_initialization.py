"""Real initialization in isolated subprocesses, without broker/credential access."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = r'''
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import sys

source, kind = Path(sys.argv[1]), sys.argv[2]
market = "US" if kind == "US" else "KR"
sys.dont_write_bytecode = True
sys.path.insert(0, str(source))
if market == "US":
    sys.path.insert(0, str(source / "prism-us"))
root = Path.cwd() / "arm"
root.mkdir(mode=0o700)
marker = root / ".prism-agent-isolation.json"
marker.write_text(json.dumps({"schema_version": 1, "purpose": "PRISM_AGENT_SHADOW",
                             "market": market, "runtime_id": "baseline-test"}))
marker.chmod(0o600)
blocked = []
phase = "IMPORT"
def audit(event, args):
    if event.startswith("socket.") and event not in {"socket.__new__", "socket.gethostname"}:
        # urllib3 checks IPv6 support by binding an ephemeral loopback socket
        # during import. Deny it too, but distinguish this capability probe
        # from any initializer/provider network operation.
        if phase == "IMPORT" and event == "socket.bind" and args[1] in {("::1", 0), ("::1", 0, 0, 0)}:
            raise RuntimeError("IMPORT_IPV6_PROBE_DENIED")
        blocked.append(event)
        raise RuntimeError("NETWORK_FORBIDDEN")
    if event == "import" and str(args[0]).split(".")[-1] == "kis_auth":
        blocked.append("broker_import")
        raise RuntimeError("BROKER_IMPORT_FORBIDDEN")
    if event == "exec" and str(getattr(args[0], "co_filename", "")).endswith("kis_auth.py"):
        blocked.append("broker_exec")
        raise RuntimeError("BROKER_EXEC_FORBIDDEN")
    if event == "open" and isinstance(args[0], (str, bytes)):
        if Path(os.fsdecode(args[0])).name in {".env", ".env.mcp-cloud", "kis_devlp.yaml",
                "mcp_agent.secrets.yaml", "mcp_agent.config.yaml", "mcp-agent.secrets.yaml", "mcp-agent.config.yaml"}:
            blocked.append("credential_read")
            raise RuntimeError("CREDENTIAL_CONFIG_FORBIDDEN")
sys.addaudithook(audit)
if market == "US":
    import cores, tracking, trading
    spec = importlib.util.spec_from_file_location("real_virtual_us_agent", source / "prism-us/us_stock_tracking_agent.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    cls = module.USStockTrackingAgent
elif kind == "KR_ENHANCED":
    from stock_tracking_enhanced_agent import EnhancedStockTrackingAgent as cls
else:
    from stock_tracking_agent import StockTrackingAgent as cls

phase = "INITIALIZE"
calls = []
def settings_factory():
    calls.append("factory")
    return {"openai": {"base_url": "http://127.0.0.1:9999/v1", "api_key": "explicit-test-placeholder"},
            "mcp": {"servers": {}}}
accounts = [{"name": "SHADOW baseline", "account_key": "virtual:baseline", "market": market.lower(), "virtual": True}]
kwargs = {"db_path": str(root / "state.sqlite"), "enable_journal": False,
          "virtual_accounts": accounts, "isolated_db_root": str(root),
          "mcp_settings_factory": settings_factory}
async def main():
    try:
        cls(mcp_settings_factory=settings_factory)
    except ValueError:
        pass
    else:
        raise AssertionError("factory-only opt-in must not leave production execution enabled")
    try:
        cls(**kwargs, telegram_token="123456:EXPLICIT_TOKEN_FORBIDDEN_IN_VIRTUAL_MODE")
    except ValueError as error:
        assert "Telegram" in str(error)
    else:
        raise AssertionError("virtual mode must reject direct Telegram credentials")
    assert not (root / "state.sqlite").exists()
    agent = cls(**kwargs)
    accounts[0]["name"] = "caller mutation"
    assert await agent.initialize()
    assert agent.account_configs[0]["name"] == "SHADOW baseline"
    assert agent._safe_account_log_label(agent.active_account) == "SHADOW baseline (가상 계정)"
    assert agent.telegram_token is None and agent.telegram_bot is None
    assert agent.trading_agent is not None and agent.trading_agent.instruction
    if kind in {"US", "KR_ENHANCED"}:
        assert agent.sell_decision_agent is not None and agent.sell_decision_agent.instruction
    if kind == "KR_ENHANCED":
        assert agent.simple_market_condition is None
        assert agent.initialization_deviations == ["ISOLATED_INDEX_PREFETCH_UNAVAILABLE"]
        assert agent.cursor.execute("SELECT COUNT(*) FROM market_condition").fetchone()[0] == 0
    assert calls == []  # MCP settings and hosts are still lazy.
    table = "us_stock_holdings" if market == "US" else "stock_holdings"
    assert agent.cursor.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] == 0
    for name, args in [
        ("process_reports", ([],)), ("buy_stock", ("SYNTHETIC", "합성 예시", 100, {})),
        ("sell_stock", ({}, "test")), ("update_holdings", ()),
    ]:
        try:
            await getattr(agent, name)(*args)
        except RuntimeError as error:
            assert "no-order adapter" in str(error)
        else:
            raise AssertionError(name + " must fail before execution")
    agent.conn.close()
    accounts[0]["name"] = "SHADOW baseline"
    resumed = cls(**kwargs)
    assert await resumed.initialize()
    assert resumed.cursor.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] == 0
    resumed.conn.close()
    assert blocked == [], blocked
    print(json.dumps({"real_initialize": kind, "broker_or_network_attempts": 0, "restart": "PASS",
                      "execution_guarded": True, "mcp_still_lazy": True}))
asyncio.run(main())
'''


@pytest.mark.parametrize("kind", ["KR", "KR_ENHANCED", "US"])
def test_real_virtual_initialize_without_credentials_network_or_execution(tmp_path, kind):
    env = {key: value for key, value in os.environ.items()
           if not any(secret in key.upper() for secret in ("KIS", "TOKEN", "SECRET", "API_KEY", "APP_KEY"))}
    env.update(HOME=str(tmp_path), KIS_CONFIG_ROOT=str(tmp_path / "absent-kis"),
               PYTHON_DOTENV_DISABLED="1", PYTHONDONTWRITEBYTECODE="1",
               TELEGRAM_BOT_TOKEN="123456:AMBIENT_TEST_TOKEN_MUST_NOT_BE_USED")
    result = subprocess.run([sys.executable, "-I", "-c", SCRIPT, str(ROOT), kind],
                            cwd=tmp_path, env=env, text=True, capture_output=True, timeout=90)
    assert result.returncode == 0, result.stderr[-5000:]
    assert json.loads(result.stdout.strip().splitlines()[-1]) == {
        "real_initialize": kind, "broker_or_network_attempts": 0, "restart": "PASS",
        "execution_guarded": True, "mcp_still_lazy": True,
    }
