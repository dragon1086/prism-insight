#!/usr/bin/env python3
"""
Telegram configuration and utility module

Encapsulates telegram usage settings following SOLID principles
and minimizes redundant conditional processing.
"""
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


class TelegramConfig:
    """
    Telegram configuration management class

    Centralizes telegram usage and related settings management.
    Also manages multi-language channel IDs.
    """

    def __init__(self, use_telegram: bool = True, channel_id: Optional[str] = None, bot_token: Optional[str] = None, broadcast_languages: list = None):
        """
        Initialize telegram configuration

        Args:
            use_telegram: Whether to use telegram (default: True)
            channel_id: Telegram channel ID (auto-loaded from environment variables if not provided)
            bot_token: Telegram bot token (auto-loaded from environment variables if not provided)
            broadcast_languages: List of languages to broadcast in parallel (e.g., ['en', 'ja', 'zh'])
        """
        self._use_telegram = use_telegram
        self._channel_id = channel_id
        self._bot_token = bot_token
        self._broadcast_languages = broadcast_languages or []
        self._broadcast_channel_ids = {}

        # Load .env file
        self._load_env()

        # Auto-load from environment variables (if not explicitly provided)
        if not self._channel_id:
            self._channel_id = os.getenv("TELEGRAM_CHANNEL_ID")
        if not self._bot_token:
            self._bot_token = os.getenv("TELEGRAM_BOT_TOKEN")

        # Load broadcast channel IDs per language
        self._load_broadcast_channels()
    
    def _load_env(self):
        """
        Load environment variables from .env file
        """
        try:
            from dotenv import load_dotenv
            load_dotenv()
            logger.debug(".env file loaded successfully")
        except ImportError:
            logger.warning("python-dotenv is not installed. Please set environment variables manually.")
        except Exception as e:
            logger.warning(f"Error loading .env file: {str(e)}")

    def _load_broadcast_channels(self):
        """
        Load telegram channel IDs for broadcast languages
        Loads from .env file in TELEGRAM_CHANNEL_ID_{LANG} format
        """
        for lang in self._broadcast_languages:
            lang_upper = lang.upper()
            env_key = f"TELEGRAM_CHANNEL_ID_{lang_upper}"
            channel_id = os.getenv(env_key)

            if channel_id:
                self._broadcast_channel_ids[lang] = channel_id
                logger.info(f"Broadcast channel loaded: {lang} -> {channel_id[:10]}...")
            else:
                logger.warning(f"Broadcast channel ID not configured for language: {lang} (env var: {env_key})")
    
    @property
    def use_telegram(self) -> bool:
        """Return whether telegram is enabled"""
        return self._use_telegram

    @property
    def channel_id(self) -> Optional[str]:
        """Return telegram channel ID"""
        return self._channel_id

    @property
    def bot_token(self) -> Optional[str]:
        """Return telegram bot token"""
        return self._bot_token

    @property
    def broadcast_languages(self) -> list:
        """Return list of broadcast languages"""
        return self._broadcast_languages

    def get_broadcast_channel_id(self, language: str) -> Optional[str]:
        """
        Return broadcast channel ID for a specific language

        Args:
            language: Language code (e.g., 'en', 'ja', 'zh')

        Returns:
            Channel ID for the language, or None if not configured
        """
        return self._broadcast_channel_ids.get(language)
    
    def is_configured(self) -> bool:
        """
        Check if telegram is properly configured

        Returns:
            bool: True if telegram is enabled and all required settings are present
        """
        if not self._use_telegram:
            return True  # Consider configured when intentionally disabled

        return bool(self._channel_id and self._bot_token)

    def validate_or_raise(self) -> None:
        """
        Validate telegram configuration (only when enabled)

        Raises:
            ValueError: When telegram is enabled but required settings are missing
        """
        if not self._use_telegram:
            logger.info("Telegram is disabled.")
            return

        if not self._channel_id:
            raise ValueError(
                "Telegram channel ID is not configured. "
                "Set environment variable TELEGRAM_CHANNEL_ID or use --no-telegram option."
            )

        if not self._bot_token:
            raise ValueError(
                "Telegram bot token is not configured. "
                "Set environment variable TELEGRAM_BOT_TOKEN or use --no-telegram option."
            )

        logger.info(f"Telegram configuration validated (channel: {self._channel_id[:10]}...)")

    def log_status(self) -> None:
        """Log current telegram configuration status"""
        if self._use_telegram:
            logger.info("✅ Telegram messaging enabled")
            logger.info(f"   - Channel ID: {self._channel_id[:10] if self._channel_id else 'None'}...")
            logger.info(f"   - Bot token: {'Configured' if self._bot_token else 'Not configured'}")
        else:
            logger.info("❌ Telegram messaging disabled")
    
    def __repr__(self) -> str:
        return (
            f"TelegramConfig(use_telegram={self._use_telegram}, "
            f"channel_id={'***' if self._channel_id else None}, "
            f"bot_token={'***' if self._bot_token else None})"
        )


def is_openai_quota_error(error: Exception) -> bool:
    """
    Check if an exception is an OpenAI insufficient_quota error (429).

    Args:
        error: The caught exception

    Returns:
        True if this is an OpenAI quota exceeded error
    """
    error_str = str(error)
    return "insufficient_quota" in error_str or (
        "429" in error_str and "exceeded" in error_str.lower() and "quota" in error_str.lower()
    )


async def send_openai_quota_alert(telegram_config: "TelegramConfig", market: str = "KR"):
    """
    Send a Telegram alert when OpenAI API quota is exceeded.

    Args:
        telegram_config: TelegramConfig instance
        market: Market identifier ("KR" or "US")
    """
    if not telegram_config or not telegram_config.use_telegram:
        return

    try:
        from telegram import Bot
        from telegram.request import HTTPXRequest

        request = HTTPXRequest(connect_timeout=10.0, read_timeout=10.0)
        bot = Bot(token=telegram_config.bot_token, request=request)

        alert_message = (
            f"🚨 [{market}] OpenAI API 크레딧 소진 알림\n\n"
            f"OpenAI API 크레딧이 소진되어 분석 파이프라인이 중단되었습니다.\n\n"
            f"• 오류: insufficient_quota (HTTP 429)\n"
            f"• 조치 필요: OpenAI Platform → Billing에서 크레딧 충전 또는 Organization Budget 상향\n"
            f"• https://platform.openai.com/settings/organization/billing"
        )

        await bot.send_message(
            chat_id=telegram_config.channel_id,
            text=alert_message
        )
        logger.info(f"[{market}] OpenAI quota alert sent to Telegram")
    except Exception as e:
        logger.error(f"[{market}] Failed to send OpenAI quota alert: {e}")


# --- Market Pulse "batch resting" subscriber notice (static, no LLM) -----------
# When the Market Pulse hook rests a LIVE batch, subscribers would otherwise see
# silence. These pre-translated notices explain the pause. Deterministic /
# fast / free (no translation agent). The batch label ({label}) is the only
# per-batch variable. dedup: cron runs each (mode, day) exactly once, so a
# resting batch fires this at most once per language channel — no dedup needed.
_MP_REST_BATCH_LABELS = {
    "ko": {"morning": "오전", "afternoon": "오후", "both": "오늘"},
    "en": {"morning": "morning", "afternoon": "afternoon", "both": "today's"},
    "ja": {"morning": "午前", "afternoon": "午後", "both": "本日"},
    "zh": {"morning": "上午", "afternoon": "下午", "both": "今日"},
    "es": {"morning": "de la mañana", "afternoon": "de la tarde", "both": "de hoy"},
}

_MP_REST_MESSAGES = {
    "ko": (
        "📉 시장 조정 구간 감지 — 이번 {label} 분석은 쉬어갑니다.\n\n"
        "시장이 고점 대비 크게 하락한 조정 구간이라, 무리한 신규 진입을 줄이기 위해 "
        "오늘은 하루 한 번(종가 확인 시간대)만 분석을 진행합니다. 보유 종목의 손절·청산 "
        "모니터링은 평소처럼 계속 가동 중입니다. 시장 회복 신호가 확인되면 자동으로 재개됩니다. 🙏"
    ),
    "en": (
        "📉 Market correction detected — this {label} analysis is taking a break.\n\n"
        "The market is in a correction (well off recent highs), so to avoid forcing new "
        "entries we run only one analysis window per day. Position monitoring "
        "(stop-loss / trend exits) continues as usual. Normal schedule resumes "
        "automatically once the market confirms recovery."
    ),
    "ja": (
        "📉 相場の調整局面を検知 — 今回の{label}の分析はお休みします。\n\n"
        "相場は直近高値から大きく下落した調整局面にあるため、無理な新規エントリーを避けるべく、"
        "本日は分析を1回のみ実施します。保有銘柄のモニタリング（損切り・トレンド離脱）は"
        "通常どおり継続しています。相場の回復が確認され次第、自動的に通常スケジュールへ再開します。"
    ),
    "zh": (
        "📉 检测到市场回调阶段 — 本次{label}分析暂停一次。\n\n"
        "当前市场处于较高点大幅回落的回调阶段，为避免勉强开新仓，今日仅进行一轮分析。"
        "持仓监控（止损／趋势离场）照常运行。一旦市场确认回暖，将自动恢复正常节奏。"
    ),
    "es": (
        "📉 Corrección del mercado detectada — este análisis {label} se toma un descanso.\n\n"
        "El mercado está en corrección (muy por debajo de los máximos recientes), por lo que, "
        "para evitar forzar nuevas entradas, ejecutamos solo una ventana de análisis por día. "
        "El seguimiento de posiciones (stop-loss / salidas por tendencia) continúa como de "
        "costumbre. El horario normal se reanuda automáticamente una vez que el mercado "
        "confirme la recuperación."
    ),
}


def _mp_rest_message(language: str, batch_mode: str) -> str:
    """Render the static resting notice for a language + batch mode (fallbacks safe)."""
    template = _MP_REST_MESSAGES.get(language, _MP_REST_MESSAGES["en"])
    labels = _MP_REST_BATCH_LABELS.get(language, _MP_REST_BATCH_LABELS["en"])
    label = labels.get(batch_mode, batch_mode)
    return template.format(label=label)


async def send_market_pulse_rest_notice(
    telegram_config: "TelegramConfig", batch_mode: str, market: str = "KR"
) -> None:
    """
    Notify subscriber channels that a Market Pulse LIVE batch is resting.

    Static, pre-translated (no LLM). Fail-open: any Telegram error is logged and
    swallowed so it can NEVER block the batch's clean early exit. Sends the ko
    notice to the main channel and each configured broadcast-language channel.

    Args:
        telegram_config: TelegramConfig (main channel + broadcast languages)
        batch_mode: resting batch mode (e.g. "morning"/"afternoon") -> label
        market: "KR" or "US" (for logging only)
    """
    if not telegram_config or not telegram_config.use_telegram:
        return

    try:
        from telegram_bot_agent import TelegramBotAgent

        bot_agent = TelegramBotAgent()

        # Main channel is Korean for both KR and US pipelines.
        targets = []
        if telegram_config.channel_id:
            targets.append(("ko", telegram_config.channel_id))
        for lang in telegram_config.broadcast_languages:
            channel_id = telegram_config.get_broadcast_channel_id(lang)
            if channel_id:
                targets.append((lang, channel_id))
            else:
                logger.warning(
                    f"[MARKET_PULSE][{market}] rest notice: no channel for '{lang}', skipping"
                )

        for lang, channel_id in targets:
            try:
                message = _mp_rest_message(lang, batch_mode)
                # Plain text (no Markdown) so the static copy never trips parsing.
                ok = await bot_agent.send_message(
                    channel_id, message, parse_mode=None, msg_type="market_pulse_rest"
                )
                logger.info(
                    f"[MARKET_PULSE][{market}] rest notice -> {lang} "
                    f"({'ok' if ok else 'failed'})"
                )
            except Exception as e:  # per-channel: one failure must not skip the rest
                logger.warning(
                    f"[MARKET_PULSE][{market}] rest notice to '{lang}' failed: {e}"
                )
    except Exception as e:  # fail-open: never propagate
        logger.error(f"[MARKET_PULSE][{market}] rest notice aborted: {e}")
