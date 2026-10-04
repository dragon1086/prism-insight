"""Telegram bot tokens never reach a log handler (2026-10-04)."""
import logging

from prism_core import log_redaction

TOKEN = "1234567890:AAFakeTokenValue_abcdefghijklmnopqrstu"


def test_redact_replaces_tokens_in_urls_and_bare_values():
    assert log_redaction.redact(f"https://api.telegram.org/bot{TOKEN}/sendMessage") == \
        "https://api.telegram.org/bot<redacted-token>/sendMessage"
    assert log_redaction.redact(f"token={TOKEN}") == "token=<redacted-token>"
    assert log_redaction.redact("price 12345:67 no token here") == "price 12345:67 no token here"


def test_installed_factory_redacts_messages_and_args(caplog):
    log_redaction.install()
    log_redaction.install()  # idempotent
    assert logging.getLogger("httpx").level == logging.WARNING
    logger = logging.getLogger("redaction-test")
    with caplog.at_level(logging.INFO, logger="redaction-test"):
        logger.info("HTTP Request: POST https://api.telegram.org/bot%s/getUpdates", TOKEN)
        logger.info(f"direct {TOKEN}")
    text = caplog.text
    assert TOKEN not in text and text.count("<redacted-token>") == 2
