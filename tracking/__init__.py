"""
Tracking Package

Helper modules for stock tracking operations.
Extracted from stock_tracking_agent.py for LLM context efficiency.
"""

from tracking.db_schema import (
    create_all_tables,
    create_indexes,
    add_scope_column_if_missing,
)
from tracking.helpers import (
    extract_ticker_info,
    get_current_stock_price,
    get_trading_value_rank_change,
    is_ticker_in_holdings,
    get_current_slots_count,
    check_sector_diversity,
    parse_price_value,
    default_scenario,
)
from tracking.trading_ops import (
    analyze_sell_decision,
    format_buy_message,
    format_sell_message,
    calculate_profit_rate,
    calculate_holding_days,
)
from tracking.journal import JournalManager
from tracking.compression import CompressionManager
from tracking.telegram import TelegramSender

__all__ = [
    # Database
    "create_all_tables",
    "create_indexes",
    "add_scope_column_if_missing",
    # Helpers
    "extract_ticker_info",
    "get_current_stock_price",
    "get_trading_value_rank_change",
    "is_ticker_in_holdings",
    "get_current_slots_count",
    "check_sector_diversity",
    "parse_price_value",
    "default_scenario",
    # Trading ops
    "analyze_sell_decision",
    "format_buy_message",
    "format_sell_message",
    "calculate_profit_rate",
    "calculate_holding_days",
    # Managers
    "JournalManager",
    "CompressionManager",
    "TelegramSender",
]
