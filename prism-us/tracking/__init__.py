"""
PRISM-US Tracking Module

Database schema and helper utilities for US stock tracking.
Uses us_* prefix tables in shared SQLite database.
Shared tables (trading_journal, trading_principles, trading_intuitions)
use 'market' column to distinguish KR vs US.
"""

from .db_schema import (
    create_us_tables,
    create_us_indexes,
    add_sector_column_if_missing,
    add_market_column_to_shared_tables,
    initialize_us_database,
    is_us_ticker_in_holdings,
    get_us_holdings_count,
    get_us_holding,
)

from .journal import USJournalManager
from .compression import USCompressionManager

__all__ = [
    # DB Schema
    "create_us_tables",
    "create_us_indexes",
    "add_sector_column_if_missing",
    "add_market_column_to_shared_tables",
    "initialize_us_database",
    "is_us_ticker_in_holdings",
    "get_us_holdings_count",
    "get_us_holding",
    # Journal
    "USJournalManager",
    # Compression
    "USCompressionManager",
]
