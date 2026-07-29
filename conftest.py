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

import os

from messaging.publish_guard import DISABLE_ENV_VAR

# Set at import time — before pytest imports a single test module, and before
# any of them can import a trading agent and trigger load_dotenv().
os.environ[DISABLE_ENV_VAR] = "1"


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
