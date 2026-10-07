"""Repo-root pytest configuration.

Its ONE job today is to make it impossible for a test run to emit a real
trading signal.

Background: the trading agents (and the publisher modules themselves) call
``load_dotenv()`` at import time, so importing one inside a test hands the
process the production ``GCP_PROJECT_ID`` / ``GCP_PUBSUB_TOPIC_ID`` and Upstash
Redis credentials. The publishers read "credentials present" as "publish for
real". A plain ``pytest`` run therefore broadcast fixture sells — buy 100.00,
sell 92.00/98.00, ``TIER1_ABS7`` / ``TIER1.5_MA50`` — onto the LIVE public
signal topic that real mirroring subscribers trade on. 44 such signals reached
production between 2026-07-11 and 2026-07-29, on tickers including 005930.

pytest imports the nearest ``conftest.py`` before any test module, so setting
the kill switch here closes the window that ``PYTEST_CURRENT_TEST`` alone does
not cover: collection and module-level code in test files.

This is deliberately at the repo root rather than in ``tests/`` so it also
applies to any future test directory.
"""

import atexit
import os
import textwrap
from pathlib import Path

import pytest

from messaging.publish_guard import DISABLE_ENV_VAR

# Set at import time — before pytest imports a single test module, and before
# any of them can import a trading agent and trigger load_dotenv().
os.environ[DISABLE_ENV_VAR] = "1"
# Never write the runtime eligibility cache or reach KIS from the default test run;
# tests that exercise either opt in explicitly.
os.environ.setdefault("US_ELIGIBILITY_CACHE_PATH", "")
os.environ.setdefault("US_SCREENING_KIS_SHORTLIST", "false")
# The runner hold rule fetches daily bars (KIS/yfinance) in the sell loops; tests that
# exercise it opt in explicitly (tests/test_runner_hold*.py).
os.environ.setdefault("RUNNER_HOLD_ENABLED", "false")
# Batch integration tests must not write the candidate ledger into the repo DB;
# tests/test_candidate_ledger.py opts in with a temporary DB.
os.environ.setdefault("CANDIDATE_LEDGER_ENABLED", "false")
# BUY/SELL prompts read official filings (SEC / KIS 공시) on every decision; hermetic
# prompt tests must not reach the network. tests/test_corporate_filings.py opts in.
os.environ.setdefault("CORP_FILINGS_ENABLED", "false")


# ---------------------------------------------------------------------------
# Demo KIS config bootstrap
#
# Test modules in tests/, prism-us/tests/, and elsewhere import the KIS
# trading stack at module level, which reads
# ``trading/config/kis_devlp.yaml`` during import (see ``trading/kis_auth.py``
# and the ``import kis_auth`` inside ``prism-us/trading/us_stock_trading.py``).
# When the file is absent — a fresh clone or any environment without real
# credentials — those imports fail and abort collection. This guarantees a
# demo config exists before ANY test module is imported, regardless of which
# test directory is being collected.
#
# The file is only ever created when missing and removed again at process
# exit when this conftest created it, so a real ``kis_devlp.yaml`` is never
# touched.
# ---------------------------------------------------------------------------
_CONFIG_DIR = Path(__file__).parent / "trading" / "config"
_CONFIG_FILE = _CONFIG_DIR / "kis_devlp.yaml"

if not _CONFIG_FILE.exists():
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _CONFIG_FILE.write_text(
        textwrap.dedent(
            """
            my_agent: test-agent
            default_mode: demo
            auto_trading: true
            default_product_code: "01"
            default_unit_amount: 100000
            default_unit_amount_usd: 250
            my_app: PSREALKEY
            my_sec: real-secret
            paper_app: PSVTTESTKEY
            paper_sec: paper-secret
            my_htsid: test-user
            prod: https://example.com
            vps: https://example.com
            ops: wss://example.com
            vops: wss://example.com
            accounts:
              - name: bootstrap-demo
                mode: demo
                account: "12345678"
                product: "01"
                market: all
                primary: true
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    atexit.register(lambda: _CONFIG_FILE.unlink(missing_ok=True))


def pytest_configure(config):
    """Re-assert the kill switch and record it in the header.

    Re-asserted because a test may legitimately manipulate os.environ; the
    header line makes it visible in CI logs that the guard was active for
    the run.
    """
    os.environ[DISABLE_ENV_VAR] = "1"
    config.addinivalue_line(
        "markers",
        "publishes_signals: test intentionally exercises a signal publisher "
        "(still blocked from reaching a real transport by publish_guard)",
    )


def pytest_report_header(config):
    return f"signal publishing: DISABLED ({DISABLE_ENV_VAR}=1)"


@pytest.fixture(autouse=True, scope="session")
def _temporary_entry_lock_dir(tmp_path_factory):
    """Keep the per-market entry lock (prism_core/entry_lock.py) out of runtime/ for every test run."""
    previous = os.environ.get("PRISM_ENTRY_LOCK_DIR")
    os.environ["PRISM_ENTRY_LOCK_DIR"] = str(tmp_path_factory.mktemp("entry_locks"))
    yield
    if previous is None:
        os.environ.pop("PRISM_ENTRY_LOCK_DIR", None)
    else:
        os.environ["PRISM_ENTRY_LOCK_DIR"] = previous
