"""DEPRECATED compatibility shim — use ``tools/trend_exit_seller.py``.

The 50MA trend-exit seller was renamed loop_b_trend_exit -> trend_exit_seller.
This legacy path is kept so existing production / subscriber crontabs that invoke
``tools/loop_b_trend_exit.py --market ... --once`` keep working unchanged. It
prints one deprecation warning to stderr, then execs the new module as
``__main__`` with the identical argv (``--market`` / ``--once`` pass through).
"""
import runpy
import sys
from pathlib import Path

_NEW = Path(__file__).resolve().parent / "trend_exit_seller.py"

if __name__ == "__main__":
    print(
        "[DEPRECATED] tools/loop_b_trend_exit.py is a compatibility shim; "
        "use tools/trend_exit_seller.py (loop_b -> trend_exit rename).",
        file=sys.stderr,
    )
    runpy.run_path(str(_NEW), run_name="__main__")
