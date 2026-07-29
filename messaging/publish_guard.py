"""Fail-closed guard that stops trading signals escaping a test run.

WHY THIS EXISTS
---------------
The trading agents call ``load_dotenv()`` at *module import* time, so merely
importing one inside a test populates the process with the production
``.env`` — including ``GCP_PROJECT_ID`` / ``GCP_PUBSUB_TOPIC_ID`` and the
Upstash Redis credentials. The signal publishers treat "credentials present"
as "publish for real", so a plain ``pytest`` run broadcast synthetic fixture
sells to the LIVE topic.

That topic is a public feed (``docs/EXTERNAL_SUBSCRIBER_GUIDE.md``) consumed by
real mirroring subscribers that place real orders. Between 2026-07-11 and
2026-07-29, 44 fixture signals reached it — buy price 100.00, sell 92.00/98.00,
``TIER1_ABS7`` / ``TIER1.5_MA50`` reasons — on tickers including 005930
(삼성전자). Any subscriber holding those symbols would have sold on a test.

DESIGN
------
Fail closed at the transport boundary rather than mocking per test: a new test
file, a new test directory, or a forgotten monkeypatch must not be able to
reopen this hole. Two independent triggers, either of which blocks publishing:

  * ``PYTEST_CURRENT_TEST`` — set by pytest around every test's
    setup/call/teardown. Covers anything that runs during a test.
  * ``PRISM_DISABLE_SIGNAL_PUBLISH`` — set by ``conftest.py`` at import time.
    Covers collection and module-level code, where ``PYTEST_CURRENT_TEST`` is
    not yet set, and gives operators a manual kill switch.

Production sets neither, so live behaviour is unchanged.
"""

from __future__ import annotations

import os

_TRUTHY = ("1", "true", "yes", "on")

#: Operators/conftest set this to force publishing off for the whole process.
DISABLE_ENV_VAR = "PRISM_DISABLE_SIGNAL_PUBLISH"


def signal_publishing_disabled() -> bool:
    """True when no trading signal may leave this process.

    Checked at connect() time in every publisher, so a disabled process never
    builds a transport client and every downstream publish call no-ops.
    """
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return True
    return str(os.environ.get(DISABLE_ENV_VAR, "")).strip().lower() in _TRUTHY


def block_reason() -> str:
    """Human-readable reason, for the warning a blocked publisher logs."""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return "running under pytest (PYTEST_CURRENT_TEST is set)"
    return f"{DISABLE_ENV_VAR} is set"
