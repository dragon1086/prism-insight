#!/usr/bin/env python3
"""
US Stock Portfolio Dashboard JSON Generation Script
Cron execution (e.g., */5 * * * * - every 5 minutes)

Usage:
    python generate_us_dashboard_json.py

Output:
    ./dashboard/public/us_dashboard_data.json - Korean language US market data
    ./dashboard/public/us_dashboard_data_en.json - English language US market data
"""
from dotenv import load_dotenv
load_dotenv()  # Load environment variables from .env

import sqlite3
import json
import sys
import yaml
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional
import logging
import os

# Logging setup (before other imports)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Path setup (before importing other modules)
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
PRISM_US_DIR = PROJECT_ROOT / "prism-us"
TRADING_DIR = PROJECT_ROOT / "trading"
sys.path.insert(0, str(SCRIPT_DIR))  # examples/ folder (for translation_utils)
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PRISM_US_DIR))
sys.path.insert(0, str(TRADING_DIR))

# yfinance import for market index data
try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False
    logger.warning("yfinance not installed. Market index data will be unavailable.")

# Translation utility import (after path setup)
try:
    from translation_utils import DashboardTranslator
    TRANSLATION_AVAILABLE = True
except ImportError:
    TRANSLATION_AVAILABLE = False
    logger.warning("Translation utility not found. English translation will be disabled.")

# Config file loading
CONFIG_FILE = PROJECT_ROOT / "prism-us" / "trading" / "config" / "kis_overseas.yaml"
try:
    with open(CONFIG_FILE, encoding="UTF-8") as f:
        _cfg = yaml.load(f, Loader=yaml.FullLoader)
except FileNotFoundError:
    _cfg = {"default_mode": "demo"}
    logger.warning(f"Config file not found: {CONFIG_FILE}. Using default mode (demo).")


class USDashboardDataGenerator:
    """US Stock Market Dashboard Data Generator"""

    # US market start date (Season 2)
    US_SEASON2_START_DATE = "2026-01-20"
    US_SEASON2_START_AMOUNT = 10000  # $10,000 USD

    def __init__(
        self,
        db_path: str = None,
        output_path: str = None,
        trading_mode: str = None,
        enable_translation: bool = True
    ):
        # Default db_path: project root stock_tracking_db.sqlite
        if db_path is None:
            db_path = str(PROJECT_ROOT / "stock_tracking_db.sqlite")

        # Default output_path: examples/dashboard/public/us_dashboard_data.json
        if output_path is None:
            output_path = str(SCRIPT_DIR / "dashboard" / "public" / "us_dashboard_data.json")

        self.db_path = db_path
        self.output_path = output_path
        self.trading_mode = trading_mode if trading_mode is not None else _cfg.get("default_mode", "demo")
        self.enable_translation = enable_translation and TRANSLATION_AVAILABLE

        # Initialize translator
        if self.enable_translation:
            try:
                self.translator = DashboardTranslator(model="gpt-5-nano")
                logger.info("Translation feature enabled.")
            except Exception as e:
                self.enable_translation = False
                logger.error(f"Translator initialization failed: {str(e)}")
        else:
            logger.info("Translation feature disabled.")

    def connect_db(self):
        """Connect to database"""
        return sqlite3.connect(self.db_path)

    def parse_json_field(self, json_str: str) -> Dict:
        """Parse JSON string (with error handling)"""
        if not json_str:
            return {}
        try:
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.warning(f"JSON parsing failed: {str(e)}")
            return {}

    def dict_from_row(self, row, cursor) -> Dict:
        """Convert SQLite Row to Dictionary"""
        return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}

    def get_us_stock_holdings(self, conn) -> List[Dict]:
        """Get current US stock holdings data"""
        cursor = conn.cursor()

        # Check if table exists
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='us_stock_holdings'
        """)
        if not cursor.fetchone():
            logger.warning("us_stock_holdings table not found")
            return []

        cursor.execute("""
            SELECT ticker, company_name, buy_price, buy_date, current_price,
                   last_updated, scenario, target_price, stop_loss, trigger_type,
                   trigger_mode, sector
            FROM us_stock_holdings
            ORDER BY buy_date DESC
        """)

        holdings = []
        for row in cursor.fetchall():
            holding = self.dict_from_row(row, cursor)

            # Parse scenario JSON
            holding['scenario'] = self.parse_json_field(holding.get('scenario', ''))

            # Calculate profit rate
            buy_price = holding.get('buy_price', 0)
            current_price = holding.get('current_price', 0)
            if buy_price > 0:
                holding['profit_rate'] = ((current_price - buy_price) / buy_price) * 100
            else:
                holding['profit_rate'] = 0

            # Calculate holding days
            buy_date = holding.get('buy_date', '')
            if buy_date:
                try:
                    buy_dt = datetime.strptime(buy_date, "%Y-%m-%d %H:%M:%S")
                    holding['holding_days'] = (datetime.now() - buy_dt).days
                except:
                    try:
                        buy_dt = datetime.strptime(buy_date, "%Y-%m-%d")
                        holding['holding_days'] = (datetime.now() - buy_dt).days
                    except:
                        holding['holding_days'] = 0
            else:
                holding['holding_days'] = 0

            holdings.append(holding)

        return holdings

    def get_us_trading_history(self, conn) -> List[Dict]:
        """Get US trading history data"""
        cursor = conn.cursor()

        # Check if table exists
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='us_trading_history'
        """)
        if not cursor.fetchone():
            logger.warning("us_trading_history table not found")
            return []

        cursor.execute("""
            SELECT id, ticker, company_name, buy_price, buy_date, sell_price,
                   sell_date, profit_rate, holding_days, scenario, trigger_type,
                   trigger_mode, sector
            FROM us_trading_history
            ORDER BY sell_date DESC
        """)

        history = []
        for row in cursor.fetchall():
            trade = self.dict_from_row(row, cursor)

            # Parse scenario JSON
            trade['scenario'] = self.parse_json_field(trade.get('scenario', ''))

            history.append(trade)

        return history

    def get_us_watchlist_history(self, conn) -> List[Dict]:
        """Get US watchlist (not entered stocks) data"""
        cursor = conn.cursor()

        # Check if table exists
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='us_watchlist_history'
        """)
        if not cursor.fetchone():
            logger.warning("us_watchlist_history table not found")
            return []

        cursor.execute("""
            SELECT id, ticker, company_name, analyzed_date, buy_score, decision,
                   skip_reason, scenario, trigger_type, trigger_mode, sector,
                   market_cap, current_price
            FROM us_watchlist_history
            ORDER BY analyzed_date DESC
        """)

        watchlist = []
        for row in cursor.fetchall():
            item = self.dict_from_row(row, cursor)

            # Parse scenario JSON
            item['scenario'] = self.parse_json_field(item.get('scenario', ''))

            watchlist.append(item)

        return watchlist

    def get_us_market_condition(self) -> List[Dict]:
        """Get US market condition data - S&P 500 and NASDAQ from yfinance"""
        if not YFINANCE_AVAILABLE:
            logger.warning("yfinance not available. Cannot fetch market data.")
            return []

        try:
            # Use US Season1 start date
            start_date = self.US_SEASON2_START_DATE.replace("-", "")
            today = datetime.now().strftime("%Y%m%d")

            logger.info(f"Fetching US market index data... ({start_date} ~ {today})")

            # S&P 500 index data (ticker: ^GSPC)
            sp500 = yf.Ticker("^GSPC")
            sp500_df = sp500.history(start=self.US_SEASON2_START_DATE, end=datetime.now().strftime("%Y-%m-%d"))

            # NASDAQ index data (ticker: ^IXIC)
            nasdaq = yf.Ticker("^IXIC")
            nasdaq_df = nasdaq.history(start=self.US_SEASON2_START_DATE, end=datetime.now().strftime("%Y-%m-%d"))

            if sp500_df.empty or nasdaq_df.empty:
                logger.warning("Failed to fetch US index data from yfinance.")
                return []

            # Merge data
            market_data = []

            for date_idx in sp500_df.index:
                date_str = date_idx.strftime("%Y-%m-%d")

                sp500_close = sp500_df.loc[date_idx, 'Close']

                # Use NASDAQ only if same date exists
                if date_idx in nasdaq_df.index:
                    nasdaq_close = nasdaq_df.loc[date_idx, 'Close']
                else:
                    nasdaq_close = 0

                market_data.append({
                    'date': date_str,
                    'spx_index': float(sp500_close),
                    'nasdaq_index': float(nasdaq_close),
                    'condition': 0,  # Default
                    'volatility': 0  # Default
                })

            # Sort by date ascending (for charts)
            market_data.sort(key=lambda x: x['date'])

            logger.info(f"US market index data collected: {len(market_data)} days")
            return market_data

        except Exception as e:
            logger.error(f"Error fetching US market index data: {str(e)}")
            return []

    def get_us_trading_insights(self, conn) -> Dict:
        """Get US trading insights data (trading_journal, trading_principles, trading_intuitions with market='US')"""
        try:
            cursor = conn.cursor()

            # 1. Query trading_principles (market='US')
            principles = []
            try:
                cursor.execute("""
                    SELECT id, scope, scope_context, condition, action, reason,
                           priority, confidence, supporting_trades, is_active,
                           created_at, last_validated_at
                    FROM trading_principles
                    WHERE is_active = 1 AND market = 'US'
                    ORDER BY
                        CASE priority
                            WHEN 'high' THEN 1
                            WHEN 'medium' THEN 2
                            WHEN 'low' THEN 3
                        END,
                        confidence DESC
                """)

                for row in cursor.fetchall():
                    principle = self.dict_from_row(row, cursor)
                    principle['is_active'] = bool(principle.get('is_active', 0))
                    principles.append(principle)
            except sqlite3.OperationalError:
                # market column might not exist
                pass

            logger.info(f"US Trading principles: {len(principles)} items")

            # 2. Query trading_journal (market='US')
            journal_entries = []
            try:
                cursor.execute("""
                    SELECT id, ticker, company_name, trade_date, trade_type,
                           buy_price, sell_price, profit_rate, holding_days,
                           one_line_summary, situation_analysis, judgment_evaluation,
                           lessons, pattern_tags, compression_layer
                    FROM trading_journal
                    WHERE market = 'US'
                    ORDER BY trade_date DESC
                    LIMIT 50
                """)

                for row in cursor.fetchall():
                    entry = self.dict_from_row(row, cursor)
                    entry['lessons'] = self.parse_json_field(entry.get('lessons', '[]'))
                    entry['pattern_tags'] = self.parse_json_field(entry.get('pattern_tags', '[]'))
                    journal_entries.append(entry)
            except sqlite3.OperationalError:
                pass

            logger.info(f"US Trading journal: {len(journal_entries)} entries")

            # 3. Query trading_intuitions (market='US')
            intuitions = []
            try:
                cursor.execute("""
                    SELECT id, category, condition, insight, confidence,
                           success_rate, supporting_trades, is_active, subcategory
                    FROM trading_intuitions
                    WHERE is_active = 1 AND market = 'US'
                    ORDER BY confidence DESC
                """)

                for row in cursor.fetchall():
                    intuition = self.dict_from_row(row, cursor)
                    intuition['is_active'] = bool(intuition.get('is_active', 0))
                    intuitions.append(intuition)
            except sqlite3.OperationalError:
                pass

            logger.info(f"US Trading intuitions: {len(intuitions)} items")

            # 4. Calculate summary statistics
            high_priority_count = sum(1 for p in principles if p.get('priority') == 'high')
            avg_profit_rate = sum(e.get('profit_rate', 0) for e in journal_entries) / len(journal_entries) if journal_entries else 0
            avg_confidence = sum(p.get('confidence', 0) for p in principles) / len(principles) if principles else 0

            summary = {
                'total_principles': len(principles),
                'active_principles': len(principles),
                'high_priority_count': high_priority_count,
                'total_journal_entries': len(journal_entries),
                'avg_profit_rate': avg_profit_rate,
                'total_intuitions': len(intuitions),
                'avg_confidence': avg_confidence
            }

            return {
                'summary': summary,
                'principles': principles,
                'journal_entries': journal_entries,
                'intuitions': intuitions
            }

        except Exception as e:
            logger.error(f"Error collecting US trading insights: {str(e)}")
            return {
                'summary': {
                    'total_principles': 0,
                    'active_principles': 0,
                    'high_priority_count': 0,
                    'total_journal_entries': 0,
                    'avg_profit_rate': 0,
                    'total_intuitions': 0,
                    'avg_confidence': 0
                },
                'principles': [],
                'journal_entries': [],
                'intuitions': []
            }

    def get_us_performance_analysis(self, conn) -> Dict:
        """Get US performance analysis data (us_analysis_performance_tracker table)"""
        try:
            cursor = conn.cursor()

            # Check if table exists
            cursor.execute("""
                SELECT name FROM sqlite_master
                WHERE type='table' AND name='us_analysis_performance_tracker'
            """)
            if not cursor.fetchone():
                logger.warning("us_analysis_performance_tracker table not found")
                return self._empty_us_performance_analysis()

            # 1. Overview - tracking status counts
            cursor.execute("""
                SELECT
                    COALESCE(tracking_status,
                        CASE
                            WHEN return_30d IS NOT NULL THEN 'completed'
                            WHEN return_7d IS NOT NULL THEN 'in_progress'
                            ELSE 'pending'
                        END
                    ) as status,
                    COUNT(*) as count
                FROM us_analysis_performance_tracker
                GROUP BY status
            """)
            status_counts = {row[0]: row[1] for row in cursor.fetchall()}

            # Get traded/watched counts
            cursor.execute("""
                SELECT
                    COALESCE(was_traded, 0) as was_traded,
                    COUNT(*) as count
                FROM us_analysis_performance_tracker
                GROUP BY was_traded
            """)
            traded_counts = {}
            for row in cursor.fetchall():
                key = 'traded' if row[0] else 'watched'
                traded_counts[key] = row[1]

            overview = {
                'total': sum(status_counts.values()),
                'pending': status_counts.get('pending', 0),
                'in_progress': status_counts.get('in_progress', 0),
                'completed': status_counts.get('completed', 0),
                'traded_count': traded_counts.get('traded', 0),
                'watched_count': traded_counts.get('watched', 0)
            }

            # 2. Trigger performance (completed tracking only)
            cursor.execute("""
                SELECT
                    trigger_type,
                    COUNT(*) as count,
                    AVG(return_7d) as avg_7d_return,
                    AVG(return_14d) as avg_14d_return,
                    AVG(return_30d) as avg_30d_return,
                    SUM(CASE WHEN return_30d > 0 THEN 1 ELSE 0 END) * 1.0 /
                        NULLIF(SUM(CASE WHEN return_30d IS NOT NULL THEN 1 ELSE 0 END), 0) as win_rate_30d
                FROM us_analysis_performance_tracker
                WHERE return_30d IS NOT NULL
                GROUP BY trigger_type
                ORDER BY count DESC
            """)

            trigger_performance = []
            for row in cursor.fetchall():
                trigger_type = row[0] or 'unknown'
                trigger_performance.append({
                    'trigger_type': trigger_type,
                    'count': row[1],
                    'avg_7d_return': row[2],
                    'avg_14d_return': row[3],
                    'avg_30d_return': row[4],
                    'win_rate_30d': row[5]
                })

            logger.info(f"US trigger performance: {len(trigger_performance)} types")

            # 3. Actual trading stats (from us_trading_history, last 30 days)
            actual_trading = {}
            try:
                cursor.execute("""
                    SELECT
                        COUNT(*) as count,
                        AVG(profit_rate) as avg_profit_rate,
                        SUM(CASE WHEN profit_rate > 0 THEN 1 ELSE 0 END) as win_count,
                        SUM(CASE WHEN profit_rate <= 0 THEN 1 ELSE 0 END) as loss_count,
                        AVG(CASE WHEN profit_rate > 0 THEN profit_rate END) as avg_profit,
                        AVG(CASE WHEN profit_rate <= 0 THEN profit_rate END) as avg_loss,
                        MAX(profit_rate) as max_profit,
                        MIN(profit_rate) as max_loss,
                        SUM(CASE WHEN profit_rate > 0 THEN profit_rate ELSE 0 END) as total_profit,
                        SUM(CASE WHEN profit_rate < 0 THEN ABS(profit_rate) ELSE 0 END) as total_loss
                    FROM us_trading_history
                    WHERE sell_date >= date('now', '-30 days')
                """)
                row = cursor.fetchone()
                if row and row[0] > 0:
                    count = row[0]
                    win_count = row[2] or 0
                    loss_count = row[3] or 0
                    total_profit = row[8] or 0
                    total_loss = row[9] or 0
                    profit_factor = total_profit / total_loss if total_loss > 0 else None

                    # profit_rate is already a percentage, convert to decimal
                    actual_trading = {
                        'count': count,
                        'avg_profit_rate': (row[1] or 0) / 100,
                        'win_rate': win_count / count if count > 0 else 0,
                        'win_count': win_count,
                        'loss_count': loss_count,
                        'avg_profit': (row[4] or 0) / 100,
                        'avg_loss': (row[5] or 0) / 100,
                        'max_profit': (row[6] or 0) / 100,
                        'max_loss': (row[7] or 0) / 100,
                        'profit_factor': profit_factor
                    }
            except sqlite3.OperationalError:
                pass  # us_trading_history table doesn't exist

            # 4. Actual trading by trigger type (from us_trading_history)
            actual_trading_by_trigger = []
            US_TRIGGER_TRACKING_START_DATE = '2026-01-20'
            try:
                cursor.execute("""
                    SELECT
                        COALESCE(trigger_type, 'AI Analysis') as trigger_type,
                        COUNT(*) as count,
                        AVG(profit_rate) as avg_profit_rate,
                        SUM(CASE WHEN profit_rate > 0 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) as win_rate,
                        SUM(CASE WHEN profit_rate > 0 THEN profit_rate ELSE 0 END) as total_profit,
                        SUM(CASE WHEN profit_rate < 0 THEN ABS(profit_rate) ELSE 0 END) as total_loss,
                        SUM(CASE WHEN profit_rate > 0 THEN 1 ELSE 0 END) as win_count,
                        SUM(CASE WHEN profit_rate <= 0 THEN 1 ELSE 0 END) as loss_count,
                        AVG(CASE WHEN profit_rate > 0 THEN profit_rate END) as avg_profit,
                        AVG(CASE WHEN profit_rate <= 0 THEN profit_rate END) as avg_loss
                    FROM us_trading_history
                    WHERE sell_date >= ?
                    GROUP BY trigger_type
                    ORDER BY count DESC
                """, (US_TRIGGER_TRACKING_START_DATE,))

                for row in cursor.fetchall():
                    trigger_type = row[0] or 'AI Analysis'
                    total_profit = row[4] or 0
                    total_loss = row[5] or 0
                    profit_factor = total_profit / total_loss if total_loss > 0 else None

                    actual_trading_by_trigger.append({
                        'trigger_type': trigger_type,
                        'count': row[1],
                        'avg_profit_rate': (row[2] or 0) / 100,
                        'win_rate': row[3] or 0,
                        'profit_factor': profit_factor,
                        'win_count': row[6] or 0,
                        'loss_count': row[7] or 0,
                        'avg_profit': (row[8] or 0) / 100 if row[8] else None,
                        'avg_loss': (row[9] or 0) / 100 if row[9] else None
                    })

                logger.info(f"US actual trading by trigger: {len(actual_trading_by_trigger)} types")
            except sqlite3.OperationalError:
                pass

            # 5. Risk/Reward ratio threshold analysis
            rr_ranges = [
                (0, 1.0, '0~1.0'),
                (1.0, 1.5, '1.0~1.5'),
                (1.5, 1.75, '1.5~1.75'),
                (1.75, 2.0, '1.75~2.0'),
                (2.0, 2.5, '2.0~2.5'),
                (2.5, 100, '2.5+')
            ]

            rr_threshold_analysis = []
            for low, high, label in rr_ranges:
                try:
                    cursor.execute("""
                        SELECT
                            COUNT(*) as total_count,
                            SUM(CASE WHEN was_traded = 1 THEN 1 ELSE 0 END) as traded_count,
                            SUM(CASE WHEN COALESCE(was_traded, 0) = 0 THEN 1 ELSE 0 END) as watched_count,
                            AVG(return_30d) as avg_all_return,
                            AVG(CASE WHEN COALESCE(was_traded, 0) = 0 THEN return_30d END) as avg_watched_return
                        FROM us_analysis_performance_tracker
                        WHERE return_30d IS NOT NULL
                          AND risk_reward_ratio IS NOT NULL
                          AND risk_reward_ratio >= ? AND risk_reward_ratio < ?
                    """, (low, high))

                    row = cursor.fetchone()
                    if row and row[0] > 0:
                        rr_threshold_analysis.append({
                            'range': label,
                            'total_count': row[0],
                            'traded_count': row[1] or 0,
                            'watched_count': row[2] or 0,
                            'avg_all_return': row[3],
                            'avg_watched_return': row[4]
                        })
                except sqlite3.OperationalError:
                    pass

            # 6. Missed opportunities (watched but gained >10%)
            missed_opportunities = []
            try:
                cursor.execute("""
                    SELECT
                        ticker, company_name, trigger_type, analysis_price,
                        price_30d, return_30d, skip_reason,
                        analysis_date, decision
                    FROM us_analysis_performance_tracker
                    WHERE return_30d IS NOT NULL
                      AND COALESCE(was_traded, 0) = 0
                      AND return_30d > 0.1
                    ORDER BY return_30d DESC
                    LIMIT 5
                """)

                for row in cursor.fetchall():
                    missed_opportunities.append({
                        'ticker': row[0],
                        'company_name': row[1],
                        'trigger_type': row[2] or 'unknown',
                        'analyzed_price': row[3],
                        'tracked_30d_price': row[4],
                        'tracked_30d_return': row[5],
                        'skip_reason': row[6] or '',
                        'analyzed_date': row[7] or '',
                        'decision': row[8] or ''
                    })
            except sqlite3.OperationalError:
                pass

            # 7. Avoided losses (watched but dropped >10%)
            avoided_losses = []
            try:
                cursor.execute("""
                    SELECT
                        ticker, company_name, trigger_type, analysis_price,
                        price_30d, return_30d, skip_reason,
                        analysis_date, decision
                    FROM us_analysis_performance_tracker
                    WHERE return_30d IS NOT NULL
                      AND COALESCE(was_traded, 0) = 0
                      AND return_30d < -0.1
                    ORDER BY return_30d ASC
                    LIMIT 5
                """)

                for row in cursor.fetchall():
                    avoided_losses.append({
                        'ticker': row[0],
                        'company_name': row[1],
                        'trigger_type': row[2] or 'unknown',
                        'analyzed_price': row[3],
                        'tracked_30d_price': row[4],
                        'tracked_30d_return': row[5],
                        'skip_reason': row[6] or '',
                        'analyzed_date': row[7] or '',
                        'decision': row[8] or ''
                    })
            except sqlite3.OperationalError:
                pass

            # 8. Data-driven recommendations
            recommendations = []

            # Best performing trigger recommendation (min 3 samples)
            if trigger_performance:
                valid_triggers = [t for t in trigger_performance
                                  if t['count'] >= 3 and t.get('avg_30d_return') is not None]
                if valid_triggers:
                    best = max(valid_triggers, key=lambda x: x['avg_30d_return'] or 0)
                    recommendations.append(
                        f"Best trigger: '{best['trigger_type']}' "
                        f"(30D avg {(best['avg_30d_return'] or 0)*100:.1f}%, "
                        f"win rate {(best['win_rate_30d'] or 0)*100:.0f}%)"
                    )

            # Insufficient data warning
            if overview['completed'] < 10:
                recommendations.append(
                    f"Tracking data limited ({overview['completed']} completed). "
                    f"Recommend accumulating at least 10 records for reliable analysis."
                )

            logger.info(f"US performance analysis: {overview['total']} tracked, {overview['completed']} completed")

            return {
                'overview': overview,
                'trigger_performance': trigger_performance,
                'actual_trading': actual_trading,
                'actual_trading_by_trigger': actual_trading_by_trigger,
                'rr_threshold_analysis': rr_threshold_analysis,
                'missed_opportunities': missed_opportunities,
                'avoided_losses': avoided_losses,
                'recommendations': recommendations
            }

        except sqlite3.OperationalError as e:
            if "no such table" in str(e):
                logger.warning(f"us_analysis_performance_tracker table not found: {str(e)}")
                return self._empty_us_performance_analysis()
            else:
                raise
        except Exception as e:
            logger.error(f"Error collecting US performance analysis: {str(e)}")
            return self._empty_us_performance_analysis()

    def _empty_us_performance_analysis(self) -> Dict:
        """Return empty US performance analysis data"""
        return {
            'overview': {
                'total': 0,
                'pending': 0,
                'in_progress': 0,
                'completed': 0,
                'traded_count': 0,
                'watched_count': 0
            },
            'trigger_performance': [],
            'actual_trading': {},
            'actual_trading_by_trigger': [],
            'rr_threshold_analysis': [],
            'missed_opportunities': [],
            'avoided_losses': [],
            'recommendations': []
        }

    def calculate_portfolio_summary(self, holdings: List[Dict]) -> Dict:
        """Calculate portfolio summary statistics"""
        if not holdings:
            return {
                'total_stocks': 0,
                'total_profit': 0,
                'avg_profit_rate': 0,
                'slot_usage': '0/10',
                'slot_percentage': 0
            }

        total_profit = sum(h.get('profit_rate', 0) for h in holdings)
        avg_profit_rate = total_profit / len(holdings) if holdings else 0

        # Sector distribution
        sector_distribution = {}
        for h in holdings:
            sector = h.get('sector', 'Other')
            sector_distribution[sector] = sector_distribution.get(sector, 0) + 1

        return {
            'total_stocks': len(holdings),
            'total_profit': total_profit,
            'avg_profit_rate': avg_profit_rate,
            'slot_usage': f'{len(holdings)}/10',
            'slot_percentage': (len(holdings) / 10) * 100,
            'sector_distribution': sector_distribution
        }

    def calculate_trading_summary(self, history: List[Dict]) -> Dict:
        """Calculate trading history summary statistics"""
        if not history:
            return {
                'total_trades': 0,
                'win_count': 0,
                'loss_count': 0,
                'win_rate': 0,
                'avg_profit_rate': 0,
                'avg_holding_days': 0
            }

        win_count = sum(1 for h in history if h.get('profit_rate', 0) > 0)
        loss_count = len(history) - win_count
        win_rate = (win_count / len(history)) * 100 if history else 0

        avg_profit_rate = sum(h.get('profit_rate', 0) for h in history) / len(history)
        avg_holding_days = sum(h.get('holding_days', 0) for h in history) / len(history)

        return {
            'total_trades': len(history),
            'win_count': win_count,
            'loss_count': loss_count,
            'win_rate': win_rate,
            'avg_profit_rate': avg_profit_rate,
            'avg_holding_days': avg_holding_days
        }

    def calculate_cumulative_realized_profit(
        self,
        trading_history: List[Dict],
        market_data: List[Dict]
    ) -> List[Dict]:
        """
        Calculate daily Prism US simulator cumulative realized profit

        - Calculate profit rate based on 10 slots (sum of profit_rate from sold stocks / 10)
        - Return cumulative profit for each market trading day
        """
        if not market_data:
            return []

        # Sort trading history by date (sell_date)
        sorted_trades = sorted(
            [t for t in trading_history if t.get('sell_date')],
            key=lambda x: x.get('sell_date', '')
        )

        # Calculate cumulative profit by date
        cumulative_profit = 0.0
        cumulative_by_date = {}

        for trade in sorted_trades:
            sell_date = trade.get('sell_date', '')
            if sell_date:
                # Extract date only if datetime format
                if ' ' in sell_date:
                    sell_date = sell_date.split(' ')[0]

                profit_rate = trade.get('profit_rate', 0)
                cumulative_profit += profit_rate
                cumulative_by_date[sell_date] = cumulative_profit

        # Generate Prism profit data for each market data date
        result = []
        last_cumulative = 0.0

        for market_item in market_data:
            date = market_item.get('date', '')

            if date < self.US_SEASON2_START_DATE:
                continue

            # Find cumulative realized profit up to this date
            for trade_date, cum_profit in cumulative_by_date.items():
                if trade_date <= date:
                    last_cumulative = cum_profit

            # Calculate profit based on 10 slots
            prism_return = last_cumulative / 10

            result.append({
                'date': date,
                'cumulative_realized_profit': last_cumulative,
                'prism_simulator_return': prism_return
            })

        return result

    def generate(self) -> Dict:
        """Generate all US dashboard data"""
        try:
            logger.info(f"Connecting to DB: {self.db_path}")
            conn = self.connect_db()
            conn.row_factory = sqlite3.Row

            logger.info("Starting US data collection...")

            # Collect data from each table
            holdings = self.get_us_stock_holdings(conn)
            trading_history = self.get_us_trading_history(conn)
            watchlist = self.get_us_watchlist_history(conn)
            market_condition = self.get_us_market_condition()

            # Get US trading insights
            trading_insights = self.get_us_trading_insights(conn)

            # Get US performance analysis and add to trading_insights
            performance_analysis = self.get_us_performance_analysis(conn)
            trading_insights['performance_analysis'] = performance_analysis

            # Calculate summary statistics
            portfolio_summary = self.calculate_portfolio_summary(holdings)
            trading_summary = self.calculate_trading_summary(trading_history)

            # Calculate Prism US simulator cumulative profit by date
            prism_performance = self.calculate_cumulative_realized_profit(
                trading_history, market_condition
            )

            # Operating costs (shared across markets)
            current_month = datetime.now().strftime("%Y-%m")
            operating_costs = {
                'month': current_month,
                'server_hosting': 31.68,
                'openai_api': 95.82,
                'anthropic_api': 18.2,
                'firecrawl_api': 19,
                'perplexity_api': 9.9
            }

            # Compose all data
            dashboard_data = {
                'generated_at': datetime.now().isoformat(),
                'trading_mode': self.trading_mode,
                'market': 'US',  # Market identifier
                'currency': 'USD',  # Currency
                'operating_costs': operating_costs,
                'summary': {
                    'portfolio': portfolio_summary,
                    'trading': trading_summary,
                    'ai_decisions': {
                        'total_decisions': 0,
                        'sell_signals': 0,
                        'hold_signals': 0,
                        'adjustment_needed': 0,
                        'avg_confidence': 0
                    },
                    'real_trading': {
                        'total_stocks': 0,
                        'total_eval_amount': 0,
                        'total_profit_amount': 0,
                        'total_profit_rate': 0,
                        'deposit': 0,
                        'total_cash': 0,
                        'available_amount': 0
                    }
                },
                'holdings': holdings,
                'real_portfolio': [],  # Real US trading portfolio (future KIS overseas API integration)
                'account_summary': {},
                'trading_history': trading_history,
                'watchlist': watchlist,
                'market_condition': market_condition,
                'prism_performance': prism_performance,
                'holding_decisions': [],  # US holding decisions (future)
                'trading_insights': trading_insights
            }

            conn.close()

            logger.info(f"US data collection complete: Holdings {len(holdings)}, Trades {len(trading_history)}, Watchlist {len(watchlist)}")

            return dashboard_data

        except Exception as e:
            logger.error(f"Error during data generation: {str(e)}")
            raise

    def save(self, data: Dict, output_file: str = None):
        """Save to JSON file"""
        try:
            if output_file is None:
                output_file = self.output_path

            output_path = Path(output_file)

            # Create directory if not exists
            output_path.parent.mkdir(parents=True, exist_ok=True)

            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            file_size = output_path.stat().st_size
            logger.info(f"JSON file saved: {output_path} ({file_size:,} bytes)")

        except Exception as e:
            logger.error(f"Error saving file: {str(e)}")
            raise


def main():
    """Main execution function"""
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(description="US Dashboard JSON Generation")
    parser.add_argument("--mode", choices=["demo", "real"],
                       help=f"Trading mode (demo: simulation, real: live trading, default: {_cfg.get('default_mode', 'demo')})")
    parser.add_argument("--no-translation", action="store_true",
                       help="Disable English translation (generate Korean version only)")

    args = parser.parse_args()

    async def async_main():
        try:
            logger.info("=== US Dashboard JSON Generation Start ===")

            enable_translation = not args.no_translation
            generator = USDashboardDataGenerator(
                trading_mode=args.mode,
                enable_translation=enable_translation
            )

            # Generate Korean data
            logger.info("Generating Korean data...")
            dashboard_data_ko = generator.generate()

            # Save Korean JSON file
            ko_output = str(SCRIPT_DIR / "dashboard" / "public" / "us_dashboard_data.json")
            generator.save(dashboard_data_ko, ko_output)

            # English translation and save
            if generator.enable_translation:
                try:
                    logger.info("Starting English translation...")
                    dashboard_data_en = await generator.translator.translate_dashboard_data(dashboard_data_ko)

                    # Save English JSON file
                    en_output = str(SCRIPT_DIR / "dashboard" / "public" / "us_dashboard_data_en.json")
                    generator.save(dashboard_data_en, en_output)

                    logger.info("English translation complete!")
                except Exception as e:
                    logger.error(f"Error during English translation: {str(e)}")
                    logger.warning("Only Korean version was generated.")
            else:
                logger.info("Translation disabled. Only Korean version generated.")

            logger.info("=== US Dashboard JSON Generation Complete ===")

        except Exception as e:
            logger.error(f"Execution error: {str(e)}")
            exit(1)

    # Run asyncio event loop
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
