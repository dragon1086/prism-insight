"""
Firebase Bridge for PRISM-Mobile

Saves message metadata to Firestore and sends FCM push notifications
when Telegram messages are sent. Failure never affects Telegram delivery.

IMPORTANT: This module is opt-in and disabled by default.
Set FIREBASE_BRIDGE_ENABLED=true in .env to activate.
This is a PRISM-Mobile specific feature, not required for core prism-insight usage.
"""

import logging
import os
import re
from typing import Optional

logger = logging.getLogger(__name__)

# Lazy initialization - only import firebase when actually needed
_db = None
_messaging = None
_initialized = False
_checked_enabled = False
_enabled = False


def _is_enabled() -> bool:
    """Check if Firebase Bridge is enabled via environment variable."""
    global _checked_enabled, _enabled
    if _checked_enabled:
        return _enabled
    _checked_enabled = True
    _enabled = os.environ.get('FIREBASE_BRIDGE_ENABLED', '').lower() in ('true', '1', 'yes')
    if not _enabled:
        logger.debug("Firebase Bridge disabled (set FIREBASE_BRIDGE_ENABLED=true to enable)")
    return _enabled


def _initialize():
    """Lazy initialize Firebase Admin SDK."""
    global _db, _messaging, _initialized
    if _initialized:
        return _initialized

    if not _is_enabled():
        return False

    try:
        import firebase_admin
        from firebase_admin import credentials, firestore, messaging

        # Check if already initialized
        try:
            app = firebase_admin.get_app()
        except ValueError:
            # Not yet initialized
            cred_path = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS')
            if not cred_path:
                logger.warning("Firebase not configured: GOOGLE_APPLICATION_CREDENTIALS not set")
                _initialized = False
                return False

            cred = credentials.Certificate(cred_path)
            firebase_admin.initialize_app(cred)

        _db = firestore.client()
        _messaging = messaging
        _initialized = True
        logger.info("Firebase Bridge initialized successfully")
        return True
    except ImportError:
        logger.warning("firebase-admin not installed. Firebase Bridge disabled.")
        _initialized = False
        return False
    except Exception as e:
        logger.warning(f"Firebase Bridge initialization failed: {e}")
        _initialized = False
        return False


# Channel username for constructing telegram links (configurable via env)
TELEGRAM_CHANNEL_USERNAME = os.environ.get('TELEGRAM_CHANNEL_USERNAME', 'stock_ai_agent')


def detect_market(message: str) -> str:
    """Detect market from message content."""
    # Korean stock patterns: 6-digit codes, Korean company names, KRW amounts
    kr_patterns = [
        r'\d{6}',  # 6-digit stock code (Korean)
        r'[가-힣]+전자|[가-힣]+증권|[가-힣]+화학|[가-힣]+건설',  # Korean company name patterns
        r'코스피|코스닥|KOSPI|KOSDAQ',
        r'원\s|₩|KRW',
    ]

    # US stock patterns: ticker symbols, USD amounts
    us_patterns = [
        r'\b[A-Z]{1,5}\b.*\$',  # Ticker + dollar sign
        r'NYSE|NASDAQ|S&P|나스닥',
        r'\$\d+',  # Dollar amounts
        r'\bAAPL\b|\bTSLA\b|\bAMZN\b|\bGOOG\b|\bMSFT\b|\bNVDA\b|\bMETA\b',  # Common US tickers
    ]

    kr_score = sum(1 for p in kr_patterns if re.search(p, message))
    us_score = sum(1 for p in us_patterns if re.search(p, message))

    if us_score > kr_score:
        return 'us'
    return 'kr'  # Default to kr


def detect_type(message: str) -> str:
    """Detect message type from content."""
    msg_lower = message.lower()

    # PDF report detection
    if any(kw in msg_lower for kw in ['pdf', '리포트', 'report', '보고서']):
        return 'pdf'

    # Portfolio detection
    if any(kw in msg_lower for kw in ['포트폴리오', 'portfolio', '보유', '잔고', '수익률']):
        return 'portfolio'

    # Analysis detection
    if any(kw in msg_lower for kw in ['분석', 'analysis', '전망', '요약', 'summary', '리뷰', '시장']):
        return 'analysis'

    # Default: trigger (trading signal)
    return 'trigger'


def extract_title(message: str, max_length: int = 80) -> str:
    """Extract title from message - first meaningful line."""
    lines = message.strip().split('\n')
    for line in lines:
        cleaned = line.strip()
        # Skip empty lines, emoji-only lines, separator lines
        if not cleaned:
            continue
        if cleaned.startswith('---') or cleaned.startswith('==='):
            continue
        if len(cleaned) < 3:
            continue
        # Remove markdown formatting
        cleaned = re.sub(r'[*_`#]', '', cleaned).strip()
        if cleaned:
            return cleaned[:max_length]
    return message[:max_length].strip()


def extract_preview(message: str, max_length: int = 100) -> str:
    """Extract preview text from message."""
    # Remove markdown formatting
    text = re.sub(r'[*_`#]', '', message)
    # Collapse whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    if len(text) <= max_length:
        return text
    return text[:max_length - 3] + '...'


def extract_stock_info(message: str) -> tuple:
    """Extract stock code and name from message.

    Returns:
        tuple: (stock_code, stock_name) or (None, None)
    """
    # Korean stock: 6-digit code
    kr_match = re.search(r'(\d{6})', message)

    # Try to find Korean company name near the code
    name_match = re.search(r'([가-힣]{2,10}(?:전자|증권|화학|건설|바이오|제약|은행|물산|SDI|SDS))', message)
    if not name_match:
        # Broader Korean name pattern
        name_match = re.search(r'([가-힣]{2,8})\s*[\(\[]?\d{6}', message)

    stock_code = kr_match.group(1) if kr_match else None
    stock_name = name_match.group(1) if name_match else None

    # US stock: ticker symbol
    if not stock_code:
        us_match = re.search(r'\b([A-Z]{1,5})\b\s*[\(\[]?\$', message)
        if us_match:
            stock_code = us_match.group(1)

    return stock_code, stock_name


async def notify(
    message: str,
    market: Optional[str] = None,
    msg_type: Optional[str] = None,
    telegram_message_id: Optional[int] = None,
    channel_id: Optional[str] = None,
    has_pdf: bool = False,
    pdf_telegram_link: Optional[str] = None,
):
    """
    Save message metadata to Firestore and send FCM push.

    This function NEVER raises exceptions - all errors are logged and ignored
    to ensure Telegram delivery is never affected.

    Args:
        message: The telegram message text
        market: Market identifier ('kr' or 'us'). Auto-detected if None.
        msg_type: Message type. Auto-detected if None.
        telegram_message_id: Telegram message ID for deep link
        channel_id: Telegram channel ID (for reference)
        has_pdf: Whether this message has an associated PDF
        pdf_telegram_link: Direct link to PDF in Telegram
    """
    try:
        if not _initialize():
            return

        # Auto-detect if not provided
        if not market:
            market = detect_market(message)
        if not msg_type:
            msg_type = detect_type(message)

        title = extract_title(message)
        preview = extract_preview(message)
        stock_code, stock_name = extract_stock_info(message)

        # Build telegram link
        telegram_link = None
        if telegram_message_id:
            telegram_link = f"https://t.me/{TELEGRAM_CHANNEL_USERNAME}/{telegram_message_id}"

        # Save to Firestore
        from google.cloud.firestore import SERVER_TIMESTAMP

        doc_data = {
            'type': msg_type,
            'market': market,
            'title': title,
            'preview': preview,
            'telegram_link': telegram_link or '',
            'stock_code': stock_code,
            'stock_name': stock_name,
            'has_pdf': has_pdf,
            'pdf_telegram_link': pdf_telegram_link,
            'created_at': SERVER_TIMESTAMP,
        }

        _db.collection('messages').add(doc_data)
        logger.info(f"Firebase: Saved {msg_type}/{market} message to Firestore")

        # Send FCM push notification
        await _send_push(title, preview, msg_type, market)

    except Exception as e:
        logger.warning(f"Firebase Bridge notify failed (ignored): {e}")


async def _send_push(title: str, body: str, msg_type: str, market: str):
    """Send FCM push notification to subscribed devices."""
    try:
        if not _messaging:
            return

        # Query devices matching preferences
        devices_ref = _db.collection('devices')
        docs = devices_ref.stream()

        tokens = []
        for doc in docs:
            device = doc.to_dict()
            prefs = device.get('preferences', {})

            # Check market preference
            pref_markets = prefs.get('markets', ['kr', 'us'])
            if market not in pref_markets:
                continue

            # Check type preference
            pref_types = prefs.get('types', ['trigger', 'analysis', 'portfolio', 'pdf'])
            if msg_type not in pref_types:
                continue

            token = device.get('token')
            if token:
                tokens.append(token)

        if not tokens:
            logger.info("Firebase: No matching devices for push notification")
            return

        # Send in batches of 500 (FCM limit)
        for i in range(0, len(tokens), 500):
            batch_tokens = tokens[i:i + 500]
            message = _messaging.MulticastMessage(
                notification=_messaging.Notification(
                    title=title,
                    body=body,
                ),
                data={
                    'type': msg_type,
                    'market': market,
                },
                tokens=batch_tokens,
            )

            response = _messaging.send_each_for_multicast(message)
            logger.info(
                f"Firebase: FCM sent to {response.success_count}/{len(batch_tokens)} devices"
            )
    except Exception as e:
        logger.warning(f"Firebase: FCM push failed (ignored): {e}")
