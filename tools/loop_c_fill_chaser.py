"""DEPRECATED compatibility shim — use ``tools/fill_chaser.py``.

The unfilled-order fill chaser was renamed loop_c_fill_chaser -> fill_chaser.
This legacy path is kept so existing production / subscriber crontabs that invoke
``tools/loop_c_fill_chaser.py --market ... --once`` keep working unchanged. It
prints one deprecation warning to stderr, then execs the new module as
``__main__`` with the identical argv (``--market`` / ``--once`` pass through).
"""
import runpy
import sys
from pathlib import Path

_NEW = Path(__file__).resolve().parent / "fill_chaser.py"

if __name__ == "__main__":
    print(
        "[DEPRECATED] tools/loop_c_fill_chaser.py is a compatibility shim; "
        "use tools/fill_chaser.py (loop_c -> fill_chaser rename).",
        file=sys.stderr,
    )
    runpy.run_path(str(_NEW), run_name="__main__")
