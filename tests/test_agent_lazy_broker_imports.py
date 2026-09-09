"""Real credential-free imports; API doubles only at explicitly tested account seams."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = r'''
import importlib.util
import json
import os
from pathlib import Path
import sys

root, market, operation = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
sys.dont_write_bytecode = True
sys.path.insert(0, str(root))
if market == "US":
    sys.path.insert(0, str(root / "prism-us"))
events = []
phase = "IMPORT"

def audit(event, args):
    if event.startswith("socket.") and event not in {"socket.__new__", "socket.gethostname"}:
        raise RuntimeError("NETWORK_FORBIDDEN")
    broker = False
    location = None
    if event == "import" and args and str(args[0]).split(".")[-1] == "kis_auth":
        broker, location = True, str(args[0])
    if event == "exec" and args and str(getattr(args[0], "co_filename", "")).endswith("kis_auth.py"):
        broker, location = True, str(args[0].co_filename)
    if event == "open" and args and isinstance(args[0], (str, bytes)):
        if Path(os.fsdecode(args[0])).name in {"kis_devlp.yaml", ".env", "mcp_agent.secrets.yaml"}:
            raise RuntimeError("CREDENTIAL_CONFIG_READ_FORBIDDEN")
    if broker:
        events.append({"phase": phase, "location": location})
        raise RuntimeError("BROKER_ACCESS_BEFORE_REQUEST" if phase == "IMPORT" else "BROKER_ACCESS_REQUESTED")

sys.addaudithook(audit)
if market == "US":
    # Real US packages, not mocks: pin the same-name market namespaces before
    # the agent imports shared root utilities. They obey the same audit guard.
    import cores
    import tracking
    import trading
    assert Path(trading.__file__).resolve() == (root / "prism-us/trading/__init__.py").resolve()
path = root / ("stock_tracking_agent.py" if market == "KR" else "prism-us/us_stock_tracking_agent.py")
spec = importlib.util.spec_from_file_location("isolated_actual_tracking_agent", path)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
assert events == [], events
cls = module.StockTrackingAgent if market == "KR" else module.USStockTrackingAgent
assert cls._safe_account_log_label({"name": "virtual"}) == "virtual"
assert events == []
phase = "ACCOUNT_METHOD"
if operation == "behavior":
    from types import SimpleNamespace
    calls, masks = [], []
    config = {}
    accounts = [{"name": "configured-primary", "account_key": "opaque-test-ref"}]
    def configured(**kwargs):
        calls.append(kwargs)
        return accounts
    def mask(value):
        masks.append(value)
        return "MASKED"
    backend = SimpleNamespace(getEnv=lambda: config, get_configured_accounts=configured,
                              mask_account_number=mask)
    if market == "KR":
        import trading
        trading.kis_auth = backend  # API-only test double; no credentials/config file.
    else:
        module.ka = backend
    agent = cls.__new__(cls)
    assert agent._get_trading_accounts() is accounts
    assert calls[-1] == {"svr": "vps", "market": market.lower()}
    for value, expected in [(" DEMO ", "vps"), ("prod", "prod"), ("REAL", "prod")]:
        config["default_mode"] = value
        assert agent._get_trading_accounts() is accounts
        assert calls[-1] == {"svr": expected, "market": market.lower()}
    assert cls._safe_account_log_label({"name": "primary", "account_key": "vps:12345678:01"}) == "primary (vps:MASKED:01)"
    assert cls._safe_account_log_label({"name": "primary", "account_key": "opaque"}) == "primary (MASKED)"
    assert masks == ["12345678", "opaque"]
    assert events == []
    print(json.dumps({"account_selection_and_mask_delegation": "PASS", "market": market}))
    raise SystemExit(0)

if operation == "cache":
    assert market == "US"
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace
    requested, executed = [], []
    def spec_from_file_location(name, path):
        requested.append((name, str(path)))
        class Loader:
            def create_module(self, spec):
                return None
            def exec_module(self, auth):
                executed.append(auth)
                auth.fixture = True
        return importlib.util.spec_from_loader(name, Loader())
    module._importlib_util.spec_from_file_location = spec_from_file_location
    with ThreadPoolExecutor(max_workers=2) as pool:
        loaded = list(pool.map(lambda _: module._get_kis_auth(), range(4)))
    assert len(requested) == len(executed) == 1
    assert requested[0] == ("kis_auth", str(root / "trading/kis_auth.py"))
    assert all(auth is loaded[0] and auth.fixture for auth in loaded)
    assert events == []
    print(json.dumps({"main_path_lazy_singleton": "PASS", "market": market}))
    raise SystemExit(0)

try:
    if operation == "accounts":
        cls.__new__(cls)._get_trading_accounts()
    else:
        cls._safe_account_log_label({"name": "primary", "account_key": "vps:12345678:01"})
except RuntimeError as error:
    assert str(error) == "BROKER_ACCESS_REQUESTED", str(error)
else:
    raise AssertionError("account method must reach real broker configuration seam")
assert len(events) == 1 and events[0]["phase"] == "ACCOUNT_METHOD"
if market == "US":
    assert Path(events[0]["location"]).resolve() == (root / "trading/kis_auth.py").resolve()
assert not list(Path(os.environ["KIS_CONFIG_ROOT"]).glob("*"))
print(json.dumps({"real_agent_import": "PASS", "credential_files": 0,
                  "broker_access_after_account_method_only": True, "market": market}))
'''


@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize("operation", ["accounts", "mask"])
def test_actual_module_import_without_broker_credentials(tmp_path, market, operation):
    env = {key: value for key, value in os.environ.items()
           if not any(secret in key.upper() for secret in ("KIS", "TOKEN", "SECRET", "API_KEY", "APP_KEY"))}
    env.update(HOME=str(tmp_path), KIS_CONFIG_ROOT=str(tmp_path / "absent-broker-config"),
               PYTHON_DOTENV_DISABLED="1", PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run([sys.executable, "-I", "-c", SCRIPT, str(ROOT), market, operation],
                            cwd=tmp_path, env=env, text=True, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stderr[-4000:]
    evidence = json.loads(result.stdout.strip().splitlines()[-1])
    assert evidence == {"real_agent_import": "PASS", "credential_files": 0,
                        "broker_access_after_account_method_only": True, "market": market}


@pytest.mark.parametrize("market,operation,key", [
    ("KR", "behavior", "account_selection_and_mask_delegation"),
    ("US", "behavior", "account_selection_and_mask_delegation"),
    ("US", "cache", "main_path_lazy_singleton"),
])
def test_lazy_account_behavior_remains_compatible(tmp_path, market, operation, key):
    env = {key: value for key, value in os.environ.items()
           if not any(secret in key.upper() for secret in ("KIS", "TOKEN", "SECRET", "API_KEY", "APP_KEY"))}
    env.update(HOME=str(tmp_path), KIS_CONFIG_ROOT=str(tmp_path / "absent-broker-config"),
               PYTHON_DOTENV_DISABLED="1", PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run([sys.executable, "-I", "-c", SCRIPT, str(ROOT), market, operation],
                            cwd=tmp_path, env=env, text=True, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stderr[-4000:]
    assert json.loads(result.stdout.strip().splitlines()[-1]) == {key: "PASS", "market": market}
