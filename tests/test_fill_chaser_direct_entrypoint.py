from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def test_direct_script_resolves_live_lifecycle_without_repo_on_sys_path(
    tmp_path,
) -> None:
    state = tmp_path / "shadow_lifecycle_state.json"
    state.write_text(
        json.dumps({"features": {"fill_chaser": {"mode": "live"}}}),
        encoding="utf-8",
    )
    probe = """
import json
from pathlib import Path
import runpy
import sys

root = Path(sys.argv[1]).resolve()
tools = root / "tools"
sys.path = [str(tools)] + [
    entry for entry in sys.path
    if Path(entry or ".").resolve() != root
]
namespace = runpy.run_path(
    str(tools / "fill_chaser.py"),
    run_name="fill_chaser_direct_probe",
)
print(json.dumps({
    "lifecycle": namespace["_FILL_CHASER_LIFECYCLE_MODE"],
    "live": namespace["FILL_CHASER_LIVE"],
}))
"""
    env = os.environ.copy()
    env.update(
        {
            "FILL_CHASER_LIVE": "true",
            "SHADOW_LIFECYCLE_STATE": str(state),
        }
    )

    result = subprocess.run(
        [sys.executable, "-c", probe, str(ROOT)],
        cwd=tmp_path,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert json.loads(result.stdout) == {"lifecycle": "live", "live": True}
