import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_kis_auth_honors_external_config_root(tmp_path):
    config_root = tmp_path / "external-kis"
    config_root.mkdir()
    (config_root / "kis_devlp.yaml").write_text(
        "my_agent: stance-config-test\n", encoding="utf-8"
    )

    env = os.environ.copy()
    env["KIS_CONFIG_ROOT"] = str(config_root)
    env["PYTHONPATH"] = str(PROJECT_ROOT / "trading")
    script = """
import sys
import types

crypto = types.ModuleType("Crypto")
cipher = types.ModuleType("Crypto.Cipher")
cipher.AES = types.SimpleNamespace()
util = types.ModuleType("Crypto.Util")
padding = types.ModuleType("Crypto.Util.Padding")
padding.unpad = lambda value, _block_size: value
crypto.Cipher = cipher
crypto.Util = util
util.Padding = padding
sys.modules["Crypto"] = crypto
sys.modules["Crypto.Cipher"] = cipher
sys.modules["Crypto.Util"] = util
sys.modules["Crypto.Util.Padding"] = padding

import kis_auth
print(kis_auth.config_root)
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.strip() == str(config_root.resolve())


def test_domestic_trading_reuses_kis_auth_config_root():
    source = (PROJECT_ROOT / "trading" / "domestic_stock_trading.py").read_text(
        encoding="utf-8"
    )

    assert 'CONFIG_FILE = Path(ka.config_root) / "kis_devlp.yaml"' in source
