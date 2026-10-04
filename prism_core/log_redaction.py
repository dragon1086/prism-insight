"""Keep Telegram bot tokens out of every log line.

httpx logs each request URL at INFO, and Telegram puts the bot token in the URL
(``https://api.telegram.org/bot<TOKEN>/sendMessage``), so orchestrator and bot logs
carried tokens in plain text. ``install()`` lowers the httpx/httpcore loggers to
WARNING and wraps the log-record factory so any token-shaped string in a message or
its arguments is redacted before a handler sees it. Idempotent; call it at import
time of every module that talks to Telegram.
"""
import logging
import re

_TOKEN = re.compile(r"(bot)?\d{6,12}:[A-Za-z0-9_-]{30,}")
_installed = False


def redact(text):
    """Replace Telegram bot tokens in ``text`` with a placeholder."""
    return _TOKEN.sub(lambda m: (m.group(1) or "") + "<redacted-token>", text)


def _clean(value):
    return redact(value) if isinstance(value, str) else value


def install():
    global _installed
    if _installed:
        return
    _installed = True
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
    base = logging.getLogRecordFactory()

    def factory(*args, **kwargs):
        record = base(*args, **kwargs)
        record.msg = _clean(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(_clean(a) for a in record.args)
        elif isinstance(record.args, dict):
            record.args = {k: _clean(v) for k, v in record.args.items()}
        return record

    logging.setLogRecordFactory(factory)
