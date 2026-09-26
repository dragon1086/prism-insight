"""Best-effort Telegram alerts to the private maintenance chat.

Never falls back to a public channel, never raises: an alert must not change
the outcome of the work that triggered it.
"""
import logging
import os

logger = logging.getLogger(__name__)

_MAX_CHARS = 3500


def _target():
    chat_id = os.getenv('OPS_ALERT_CHAT_ID') or os.getenv('OAUTH_ALERT_CHAT_ID')
    token = os.getenv('OPS_ALERT_BOT_TOKEN') or os.getenv('OAUTH_ALERT_BOT_TOKEN')
    return chat_id, token


async def send_ops_alert(text: str) -> bool:
    chat_id, token = _target()
    if not chat_id or not token:
        logger.warning('Ops alert not sent (maintenance chat not configured): %s', text[:300])
        return False
    try:
        import httpx

        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                f'https://api.telegram.org/bot{token}/sendMessage',
                json={'chat_id': chat_id, 'text': text[:_MAX_CHARS], 'disable_web_page_preview': True})
        if response.status_code != 200:
            logger.warning('Ops alert rejected: HTTP %s', response.status_code)
        return response.status_code == 200
    except Exception as e:  # noqa: BLE001
        logger.warning('Ops alert failed: %s', type(e).__name__)
        return False
