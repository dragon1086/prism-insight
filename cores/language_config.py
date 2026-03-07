"""
Language Configuration Module

This module provides centralized language configuration and translation management
for the PRISM-INSIGHT stock analysis system.

Supported Languages:
- Korean (ko): Default language
- English (en): International users
"""

import os
from enum import Enum
from datetime import datetime
from typing import Dict


class Language(Enum):
    """Supported languages"""
    KOREAN = "ko"
    ENGLISH = "en"


class LanguageConfig:
    """
    Centralized language configuration and translation management

    This class provides all language-specific strings, templates, and formatting
    used throughout the PRISM-INSIGHT system.
    """

    def __init__(self, language: Language = Language.KOREAN):
        """
        Initialize language configuration

        Args:
            language: Target language (default: Korean)
        """
        self.language = language

    def get_report_sections(self) -> Dict[str, str]:
        """
        Get report section titles in the specified language

        Returns:
            Dictionary mapping section keys to localized titles
        """
        if self.language == Language.ENGLISH:
            return {
                "price_volume_analysis": "Price and Volume Analysis",
                "investor_trading_analysis": "Investor Trading Trends Analysis",
                "company_status": "Company Status",
                "company_overview": "Company Overview",
                "news_analysis": "News Analysis",
                "market_index_analysis": "Market Analysis",
                "investment_strategy": "Investment Strategy and Opinion",
                "executive_summary": "Executive Summary"
            }
        else:  # Korean (default)
            return {
                "price_volume_analysis": "주가 및 거래량 분석",
                "investor_trading_analysis": "투자자별 매매 동향 분석",
                "company_status": "기업 현황",
                "company_overview": "기업 개요",
                "news_analysis": "뉴스 분석",
                "market_index_analysis": "시장 분석",
                "investment_strategy": "투자 전략 및 의견",
                "executive_summary": "요약"
            }

    def get_telegram_template(self) -> Dict[str, str]:
        """
        Get Telegram message templates in the specified language

        Returns:
            Dictionary of Telegram message templates
        """
        if self.language == Language.ENGLISH:
            return {
                # Alert titles
                "alert_title_morning": "🌅 Morning Buy Signal Alert",
                "alert_title_afternoon": "🌆 Afternoon Buy Signal Alert",

                # Time descriptions
                "time_desc_morning": "10 minutes after market open",
                "time_desc_afternoon": "10 minutes after lunch break",

                # Message templates
                "detected_stocks": "📊 Buy signals detected on {date} ({time_desc})",
                "total_stocks": "Total: {count} stocks",
                "no_signals": "No buy signals detected today.",

                # Report sections
                "buy_score": "Buy Score",
                "current_price": "Current Price",
                "target_price": "Target Price",
                "stop_loss": "Stop Loss",
                "investment_period": "Investment Period",
                "sector": "Sector",
                "rationale": "Investment Rationale",

                # Disclaimers
                "disclaimer_title": "📝 Important Notice",
                "disclaimer_simulation": "This report is an AI-based simulation result and is not related to actual trading.",
                "disclaimer_reference": "This information is for reference only. All investment decisions and responsibilities lie solely with the investor.",
                "disclaimer_not_recommendation": "This is not a leading channel and does not recommend buying/selling specific stocks.",

                # Portfolio summary
                "portfolio_summary_title": "📊 PRISM Simulator | Real-time Portfolio",
                "current_holdings": "Current Holdings",
                "best_performer": "Best Performer",
                "worst_performer": "Worst Performer",
                "sector_distribution": "Sector Distribution",
                "trading_history": "Trading History Stats",
                "total_trades": "Total Trades",
                "profitable_trades": "Profitable Trades",
                "losing_trades": "Losing Trades",
                "win_rate": "Win Rate",
                "cumulative_return": "Cumulative Return",

                # Chart labels
                "chart_title_price": "Price Chart",
                "chart_title_volume": "Trading Volume",

                # Investment periods
                "period_short": "Short-term",
                "period_medium": "Mid-term",
                "period_long": "Long-term",

                # Date format
                "date_format": "%B %d, %Y"  # January 15, 2024
            }
        else:  # Korean (default)
            return {
                # Alert titles
                "alert_title_morning": "🌅 오전 매수 신호 알림",
                "alert_title_afternoon": "🌆 오후 매수 신호 알림",

                # Time descriptions
                "time_desc_morning": "장 시작 10분 후",
                "time_desc_afternoon": "점심시간 이후 10분 후",

                # Message templates
                "detected_stocks": "📊 {date} ({time_desc}) 매수 신호 감지",
                "total_stocks": "총 {count}개 종목",
                "no_signals": "오늘은 매수 신호가 감지되지 않았습니다.",

                # Report sections
                "buy_score": "매수 점수",
                "current_price": "현재가",
                "target_price": "목표가",
                "stop_loss": "손절가",
                "investment_period": "투자 기간",
                "sector": "산업군",
                "rationale": "투자 근거",

                # Disclaimers
                "disclaimer_title": "📝 안내사항",
                "disclaimer_simulation": "이 보고서는 AI 기반 시뮬레이션 결과이며, 실제 매매와 무관합니다.",
                "disclaimer_reference": "본 정보는 단순 참고용이며, 투자 결정과 책임은 전적으로 투자자에게 있습니다.",
                "disclaimer_not_recommendation": "이 채널은 리딩방이 아니며, 특정 종목 매수/매도를 권유하지 않습니다.",

                # Portfolio summary
                "portfolio_summary_title": "📊 프리즘 시뮬레이터 | 실시간 포트폴리오",
                "current_holdings": "현재 보유 종목",
                "best_performer": "최고 수익",
                "worst_performer": "최저 수익",
                "sector_distribution": "산업군 분포",
                "trading_history": "매매 이력 통계",
                "total_trades": "총 거래 건수",
                "profitable_trades": "수익 거래",
                "losing_trades": "손실 거래",
                "win_rate": "승률",
                "cumulative_return": "누적 수익률",

                # Chart labels
                "chart_title_price": "주가 차트",
                "chart_title_volume": "거래량",

                # Investment periods
                "period_short": "단기",
                "period_medium": "중기",
                "period_long": "장기",

                # Date format
                "date_format": "%Y.%m.%d"  # 2024.01.15
            }

    def get_chart_labels(self) -> Dict[str, str]:
        """
        Get chart labels in the specified language

        Returns:
            Dictionary of chart labels
        """
        if self.language == Language.ENGLISH:
            return {
                "date": "Date",
                "price": "Price (KRW)",
                "volume": "Volume",
                "market_cap": "Market Cap (KRW Billion)",
                "per": "PER",
                "pbr": "PBR",
                "roe": "ROE (%)",
                "debt_ratio": "Debt Ratio (%)",
                "operating_margin": "Operating Margin (%)",
                "net_margin": "Net Margin (%)",
                "price_chart": "Stock Price Chart",
                "volume_chart": "Trading Volume Chart",
                "fundamental_chart": "Fundamental Analysis",
                "moving_average_5": "5-day MA",
                "moving_average_20": "20-day MA",
                "moving_average_60": "60-day MA",
                "moving_average_120": "120-day MA",
                "support_level": "Support Level",
                "resistance_level": "Resistance Level"
            }
        else:  # Korean (default)
            return {
                "date": "날짜",
                "price": "주가 (원)",
                "volume": "거래량",
                "market_cap": "시가총액 (억원)",
                "per": "PER",
                "pbr": "PBR",
                "roe": "ROE (%)",
                "debt_ratio": "부채비율 (%)",
                "operating_margin": "영업이익률 (%)",
                "net_margin": "순이익률 (%)",
                "price_chart": "주가 차트",
                "volume_chart": "거래량 차트",
                "fundamental_chart": "재무 지표 분석",
                "moving_average_5": "5일 이동평균",
                "moving_average_20": "20일 이동평균",
                "moving_average_60": "60일 이동평균",
                "moving_average_120": "120일 이동평균",
                "support_level": "지지선",
                "resistance_level": "저항선"
            }

    def format_date(self, date_str: str) -> str:
        """
        Format date string according to language preference

        Args:
            date_str: Date string in YYYYMMDD format

        Returns:
            Formatted date string
        """
        try:
            date_obj = datetime.strptime(date_str, "%Y%m%d")
            templates = self.get_telegram_template()
            return date_obj.strftime(templates["date_format"])
        except Exception:
            return date_str

    def get_trigger_emojis(self) -> Dict[str, str]:
        """
        Get emoji mappings for different trigger types

        These are universal across languages

        Returns:
            Dictionary mapping trigger types to emojis
        """
        return {
            "profit_target": "✅",
            "stop_loss": "⛔",
            "time_condition": "⏰",
            "momentum_exhaustion": "📉",
            "resistance": "🔝",
            "support": "🔻",
            "trend_reversal": "🔄",
            "buy": "📈",
            "sell": "📉",
            "hold": "✋",
            "caution": "⚠️",
            "info": "ℹ️",
            "success": "✓",
            "error": "✗",
            "morning": "🌅",
            "afternoon": "🌆",
            "portfolio": "💼"
        }

    def get_analysis_terminology(self) -> Dict[str, str]:
        """
        Get analysis terminology translations

        Returns:
            Dictionary of analysis terms
        """
        if self.language == Language.ENGLISH:
            return {
                "technical_analysis": "Technical Analysis",
                "fundamental_analysis": "Fundamental Analysis",
                "valuation": "Valuation",
                "momentum": "Momentum",
                "trend": "Trend",
                "support": "Support",
                "resistance": "Resistance",
                "breakout": "Breakout",
                "consolidation": "Consolidation",
                "overbought": "Overbought",
                "oversold": "Oversold",
                "bullish": "Bullish",
                "bearish": "Bearish",
                "neutral": "Neutral",
                "uptrend": "Uptrend",
                "downtrend": "Downtrend",
                "sideways": "Sideways",
                "volatility": "Volatility",
                "liquidity": "Liquidity",
                "market_cap": "Market Capitalization",
                "pe_ratio": "Price-to-Earnings Ratio (PER)",
                "pb_ratio": "Price-to-Book Ratio (PBR)",
                "dividend_yield": "Dividend Yield",
                "earnings_growth": "Earnings Growth",
                "revenue_growth": "Revenue Growth"
            }
        else:  # Korean (default)
            return {
                "technical_analysis": "기술적 분석",
                "fundamental_analysis": "기본적 분석",
                "valuation": "밸류에이션",
                "momentum": "모멘텀",
                "trend": "추세",
                "support": "지지선",
                "resistance": "저항선",
                "breakout": "돌파",
                "consolidation": "횡보",
                "overbought": "과매수",
                "oversold": "과매도",
                "bullish": "강세",
                "bearish": "약세",
                "neutral": "중립",
                "uptrend": "상승추세",
                "downtrend": "하락추세",
                "sideways": "횡보추세",
                "volatility": "변동성",
                "liquidity": "유동성",
                "market_cap": "시가총액",
                "pe_ratio": "주가수익비율 (PER)",
                "pb_ratio": "주가순자산비율 (PBR)",
                "dividend_yield": "배당수익률",
                "earnings_growth": "이익 성장률",
                "revenue_growth": "매출 성장률"
            }


def get_language_from_env() -> Language:
    """
    Get language setting from environment variable

    Reads PRISM_LANGUAGE environment variable.
    Defaults to Korean if not set or invalid.

    Returns:
        Language enum value
    """
    lang_str = os.getenv("PRISM_LANGUAGE", "ko").lower()

    try:
        return Language(lang_str)
    except ValueError:
        # Default to Korean if invalid language specified
        return Language.KOREAN


# Centralized language name mapping (single source of truth)
LANGUAGE_NAMES = {
    "ko": "Korean",
    "en": "English",
    "ja": "Japanese",
    "zh": "Chinese",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
}


# Localized error messages for common failures
_ERROR_MESSAGES = {
    "ko": {
        "analysis_failed": "분석 중 오류가 발생했습니다.",
        "timeout": "분석 시간이 초과되었습니다.",
        "no_data": "데이터를 가져올 수 없습니다.",
        "invalid_stock_code": "유효하지 않은 종목 코드입니다.",
        "invalid_date": "유효하지 않은 날짜 형식입니다.",
        "api_error": "API 호출 중 오류가 발생했습니다.",
        "chart_failed": "차트 생성 중 오류가 발생했습니다.",
        "summary_failed": "요약 생성 중 오류가 발생했습니다.",
        "strategy_failed": "투자 전략 분석에 실패했습니다.",
        "report_save_failed": "보고서 저장에 실패했습니다.",
        "no_report": "보고서가 생성되지 않았습니다.",
    },
    "en": {
        "analysis_failed": "An error occurred during analysis.",
        "timeout": "Analysis timed out.",
        "no_data": "Unable to retrieve data.",
        "invalid_stock_code": "Invalid stock code.",
        "invalid_date": "Invalid date format.",
        "api_error": "An error occurred during API call.",
        "chart_failed": "An error occurred during chart generation.",
        "summary_failed": "An error occurred during summary generation.",
        "strategy_failed": "Investment strategy analysis failed.",
        "report_save_failed": "Failed to save report.",
        "no_report": "No report was generated.",
    },
}


def get_error_message(key: str, language: str = "ko") -> str:
    """
    Get a localized error message.

    Args:
        key: Error message key (e.g., 'analysis_failed', 'timeout')
        language: Language code ('ko' or 'en')

    Returns:
        Localized error message string
    """
    lang_messages = _ERROR_MESSAGES.get(language, _ERROR_MESSAGES["en"])
    return lang_messages.get(key, f"Unknown error: {key}")


# Convenience function for getting config
def get_config(language: str = None) -> LanguageConfig:
    """
    Get language configuration instance

    Args:
        language: Language code ("ko" or "en"). If None, reads from environment.

    Returns:
        LanguageConfig instance
    """
    if language is None:
        lang = get_language_from_env()
    else:
        try:
            lang = Language(language)
        except ValueError:
            lang = Language.KOREAN

    return LanguageConfig(lang)
