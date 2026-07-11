"""DEPRECATED compatibility shim — use ``tools/hardstop_seller.py``.

The intraday hard-stop seller was renamed loop_a_hardstop -> hardstop_seller.
This legacy path is kept so existing production / subscriber crontabs that invoke
``tools/loop_a_hardstop.py --market ... --once`` keep working unchanged. It prints
one deprecation warning to stderr, then execs the new module as ``__main__`` with
the identical argv (``--market`` / ``--once`` pass straight through).
"""
import runpy
import sys
from pathlib import Path

_NEW = Path(__file__).resolve().parent / "hardstop_seller.py"

if __name__ == "__main__":
    print(
        "[DEPRECATED] tools/loop_a_hardstop.py is a compatibility shim; "
        "use tools/hardstop_seller.py (loop_a -> hardstop rename).",
        file=sys.stderr,
    )
    runpy.run_path(str(_NEW), run_name="__main__")
