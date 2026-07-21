"""Regression tests for the prism-us `cores` shadow import bug.

Background
----------
prism-us runs as ``python prism-us/us_stock_analysis_orchestrator.py``, so
``sys.path[0]`` is the ``prism-us/`` directory and ``import cores`` binds to
``prism-us/cores`` — which shadows the ROOT ``cores`` package. The US
orchestrator loads ROOT ``cores/report_generation.py`` by file path via
``prism-us/cores/us_analysis.py``. report_generation.py has TOP-LEVEL absolute
imports (``from cores.agents.report_agent import ReportAgent``, ``from
cores.llm...``, ``from cores.openai_error_logging import ...``) which, under the
shadow, resolve against ``prism-us/cores`` and fail with::

    ModuleNotFoundError: No module named 'cores.agents.report_agent'

This broke US morning/afternoon report generation in production
(us_morning.log 2026-07-20 23:22, reports 0/3).

These tests run in fresh subprocesses (the shadow manipulation is global and
must not corrupt the pytest interpreter's sys.modules) and assert:

1. Without the cores-swap, loading report_generation under the shadow raises the
   exact production ModuleNotFoundError (documents pre-fix behavior).
2. With the fix, importing ``cores.us_analysis`` under the shadow succeeds, the
   ReportAgent-backed callables are present, and ``sys.modules['cores']`` is
   restored to the prism-us package afterwards (no global corruption).
"""
import subprocess
import sys
import textwrap
from pathlib import Path

PRISM_US_DIR = Path(__file__).resolve().parent.parent          # .../prism-us
PROJECT_ROOT = PRISM_US_DIR.parent                              # repo root


def _run_in_shadow_subprocess(body: str) -> subprocess.CompletedProcess:
    """Run ``body`` in a fresh interpreter with the prism-us `cores` shadow.

    Mirrors production: prism-us/ precedes the project root on sys.path, so
    ``import cores`` binds to prism-us/cores.
    """
    preamble = textwrap.dedent(
        f"""
        import sys
        # Mirror `python prism-us/...`: prism-us dir is sys.path[0], root after it.
        sys.path.insert(0, {str(PROJECT_ROOT)!r})
        sys.path.insert(0, {str(PRISM_US_DIR)!r})
        import cores  # binds to prism-us/cores (the shadow)
        assert cores.__file__.replace("\\\\", "/").endswith("prism-us/cores/__init__.py"), (
            "test setup failed: cores is not the prism-us shadow: " + cores.__file__
        )
        """
    )
    script = preamble + textwrap.dedent(body)
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=str(PRISM_US_DIR),
    )


def test_shadow_reproduces_module_not_found_without_swap():
    """Pre-fix behavior: a plain file-path exec of report_generation under the
    shadow raises the exact production ModuleNotFoundError."""
    body = f"""
        import importlib.util
        from pathlib import Path
        rg = Path({str(PROJECT_ROOT)!r}) / "cores" / "report_generation.py"
        spec = importlib.util.spec_from_file_location("main_report_generation", rg)
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except ModuleNotFoundError as e:
            print("REPRO:" + str(e))
        else:
            print("NO_REPRO")
    """
    result = _run_in_shadow_subprocess(body)
    assert result.returncode == 0, (
        "subprocess crashed unexpectedly:\n" + result.stdout + result.stderr
    )
    assert "REPRO:No module named 'cores.agents.report_agent'" in result.stdout, (
        "Expected the production ModuleNotFoundError but got:\n"
        + result.stdout + result.stderr
    )


def test_us_analysis_loader_resolves_root_cores_under_shadow():
    """Post-fix behavior: importing cores.us_analysis under the shadow succeeds,
    exposes the ReportAgent-backed callables, and restores sys.modules['cores']
    to the prism-us package (no global corruption)."""
    body = """
        import cores.us_analysis as ua

        required = [
            "generate_report",
            "generate_summary",
            "generate_investment_strategy",
            "get_disclaimer",
            "generate_market_report",
        ]
        missing = [name for name in required if not callable(getattr(ua, name, None))]
        assert not missing, "us_analysis missing callables: " + repr(missing)

        # After the swap, `cores` must be restored to the prism-us shadow so the
        # rest of the prism-us process is unaffected.
        import sys
        restored = sys.modules["cores"].__file__.replace("\\\\", "/")
        assert restored.endswith("prism-us/cores/__init__.py"), (
            "cores not restored to prism-us after load: " + restored
        )
        print("FIX_OK")
    """
    result = _run_in_shadow_subprocess(body)
    assert result.returncode == 0, (
        "Importing cores.us_analysis under the shadow failed (the bug):\n"
        + result.stdout + result.stderr
    )
    assert "FIX_OK" in result.stdout, (
        "Post-fix assertions did not pass:\n" + result.stdout + result.stderr
    )
