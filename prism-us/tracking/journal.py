"""
US Trading Journal Manager

Handles trading journal creation, principle extraction, and context retrieval for US stocks.
Based on tracking/journal.py but adapted for US market with market='US' filter.
"""

import json
import logging
import re
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from cores.utils import parse_llm_json

logger = logging.getLogger(__name__)


# =============================================================================
# Helper function to import modules from main project cores/ (avoid namespace collision)
# =============================================================================
def _import_from_main_cores(module_name: str, relative_path: str):
    """
    Import module directly from main project cores/ directory.

    This function avoids namespace collision where prism-us/cores/ shadows
    the main project's cores/ directory in sys.path.

    Args:
        module_name: Module name for sys.modules registration
        relative_path: Path relative to PROJECT_ROOT (e.g., "cores/agents/trading_journal_agent.py")

    Returns:
        Loaded module object
    """
    import importlib.util
    # PROJECT_ROOT is parent of parent (prism-us/tracking -> prism-us -> prism-insight)
    project_root = Path(__file__).resolve().parent.parent.parent
    file_path = project_root / relative_path
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class USJournalManager:
    """Manages trading journal operations for US stocks."""

    MARKET = "US"  # Market identifier for shared tables

    def __init__(self, cursor, conn, language: str = "ko", enable_journal: bool = False):
        """
        Initialize USJournalManager.

        Args:
            cursor: SQLite cursor
            conn: SQLite connection
            language: Language code (en/ko)
            enable_journal: Whether journal feature is enabled
        """
        self.cursor = cursor
        self.conn = conn
        self.language = language
        self.enable_journal = enable_journal

    async def create_entry(
        self,
        stock_data: Dict[str, Any],
        sell_price: float,
        profit_rate: float,
        holding_days: int,
        sell_reason: str
    ) -> bool:
        """
        Create trading journal entry with retrospective analysis.

        Args:
            stock_data: Original stock data including buy info
            sell_price: Price at which the stock was sold (USD)
            profit_rate: Realized profit/loss percentage
            holding_days: Number of days the stock was held
            sell_reason: Reason for selling

        Returns:
            bool: True if journal entry was created successfully
        """
        if not self.enable_journal:
            logger.debug("Trading journal is disabled")
            return False

        try:
            # Import trading_journal_agent from main project (avoid namespace collision)
            _journal_module = _import_from_main_cores(
                "trading_journal_agent",
                "cores/agents/trading_journal_agent.py"
            )
            create_trading_journal_agent = _journal_module.create_trading_journal_agent
            from mcp_agent.workflows.llm.augmented_llm import RequestParams
            from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM

            ticker = stock_data.get('ticker', '')
            company_name = stock_data.get('company_name', '')
            buy_price = stock_data.get('buy_price', 0)
            buy_date = stock_data.get('buy_date', '')
            scenario_json = stock_data.get('scenario', '{}')

            logger.info(f"Creating US journal entry for {ticker}({company_name})")

            # Parse scenario
            scenario_data = {}
            if isinstance(scenario_json, str):
                try:
                    scenario_data = json.loads(scenario_json)
                except:
                    scenario_data = {}

            # Create journal agent (uses yahoo_finance instead of kospi_kosdaq)
            journal_agent = create_trading_journal_agent(self.language, market="US")

            async with journal_agent:
                llm = await journal_agent.attach_llm(OpenAIAugmentedLLM)

                prompt = self._build_analysis_prompt(
                    company_name, ticker, buy_price, buy_date,
                    scenario_data, sell_price, profit_rate, holding_days, sell_reason
                )

                response = await llm.generate_str(
                    message=prompt,
                    request_params=RequestParams(model="gpt-5.2", maxTokens=16000)
                )
                logger.info(f"US Journal agent response received: {len(response)} chars")

            # Parse and save
            journal_data = self._parse_response(response)
            journal_id = self._save_to_database(
                ticker, company_name, buy_price, buy_date, scenario_json,
                scenario_data, sell_price, sell_reason, profit_rate,
                holding_days, journal_data
            )

            logger.info(f"US Journal entry created for {ticker}: {journal_data.get('one_line_summary', '')}")

            # Extract principles
            lessons = journal_data.get('lessons', [])
            if lessons and journal_id > 0:
                extracted_count = self.extract_principles(lessons, journal_id)
                logger.info(f"Extracted {extracted_count} principles from US journal {journal_id}")

            return True

        except ImportError as ie:
            # trading_journal_agent may not exist yet - gracefully handle
            logger.warning(f"Trading journal agent not available for US market: {ie}")
            return False
        except Exception as e:
            logger.error(f"Error creating US journal entry: {str(e)}")
            logger.error(traceback.format_exc())
            return False

    def _build_analysis_prompt(
        self, company_name: str, ticker: str, buy_price: float, buy_date: str,
        scenario_data: Dict, sell_price: float, profit_rate: float,
        holding_days: int, sell_reason: str
    ) -> str:
        """Build prompt for retrospective analysis."""
        if self.language == "ko":
            return f"""
Please review the following completed US stock trade:

## Buy Information
- Stock: {company_name}({ticker})
- Buy Price: ${buy_price:,.2f}
- Buy Date: {buy_date}
- Buy Scenario:
  - Buy Score: {scenario_data.get('buy_score', 'N/A')}
  - Investment Rationale: {scenario_data.get('rationale', 'N/A')}
  - Target Price: ${scenario_data.get('target_price', 'N/A')}
  - Stop Loss: ${scenario_data.get('stop_loss', 'N/A')}
  - Investment Period: {scenario_data.get('investment_period', 'N/A')}
  - Sector: {scenario_data.get('sector', 'N/A')}
  - Market Condition: {scenario_data.get('market_condition', 'N/A')}

## Sell Information
- Sell Price: ${sell_price:,.2f}
- Return: {profit_rate:.2f}%
- Holding Days: {holding_days} days
- Sell Reason: {sell_reason}

## Analysis Request
1. Use yahoo_finance tool to check current market conditions and recent stock trends
2. Compare and analyze buy and sell timing situations
3. Evaluate decision appropriateness and extract lessons
4. Assign pattern tags
"""
        else:
            return f"""
Please review the following completed US stock trade:

## Buy Information
- Stock: {company_name}({ticker})
- Buy Price: ${buy_price:,.2f}
- Buy Date: {buy_date}
- Buy Scenario:
  - Buy Score: {scenario_data.get('buy_score', 'N/A')}
  - Rationale: {scenario_data.get('rationale', 'N/A')}
  - Target Price: ${scenario_data.get('target_price', 'N/A')}
  - Stop Loss: ${scenario_data.get('stop_loss', 'N/A')}
  - Investment Period: {scenario_data.get('investment_period', 'N/A')}
  - Sector: {scenario_data.get('sector', 'N/A')}
  - Market Condition: {scenario_data.get('market_condition', 'N/A')}

## Sell Information
- Sell Price: ${sell_price:,.2f}
- Profit Rate: {profit_rate:.2f}%
- Holding Days: {holding_days} days
- Sell Reason: {sell_reason}

## Analysis Request
1. Use yahoo_finance tools to check current market and stock trends
2. Compare buy time vs sell time situations
3. Evaluate decisions and extract lessons
4. Assign pattern tags
"""

    def _parse_response(self, response: str) -> Dict[str, Any]:
        """Parse journal agent response into structured data."""
        result = parse_llm_json(response, context='US journal response')
        if result is not None:
            return result
        logger.error(f"US journal response parse failed. Full response: {response}")
        return {
            "situation_analysis": {"raw_response": response[:500]},
            "judgment_evaluation": {},
            "lessons": [],
            "pattern_tags": [],
            "one_line_summary": "Analysis parsing failed",
            "confidence_score": 0.3
        }

    def _save_to_database(
        self, ticker: str, company_name: str, buy_price: float, buy_date: str,
        scenario_json: str, scenario_data: Dict, sell_price: float, sell_reason: str,
        profit_rate: float, holding_days: int, journal_data: Dict
    ) -> int:
        """Save journal entry to database with market='US'."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.cursor.execute(
            """
            INSERT INTO trading_journal
            (ticker, company_name, trade_date, trade_type,
             buy_price, buy_date, buy_scenario, buy_market_context,
             sell_price, sell_reason, profit_rate, holding_days,
             situation_analysis, judgment_evaluation, lessons, pattern_tags,
             one_line_summary, confidence_score, compression_layer, created_at, market)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ticker, company_name, now, 'sell',
                buy_price, buy_date, scenario_json,
                json.dumps(scenario_data.get('market_condition', ''), ensure_ascii=False),
                sell_price, sell_reason, profit_rate, holding_days,
                json.dumps(journal_data.get('situation_analysis', {}), ensure_ascii=False),
                json.dumps(journal_data.get('judgment_evaluation', {}), ensure_ascii=False),
                json.dumps(journal_data.get('lessons', []), ensure_ascii=False),
                json.dumps(journal_data.get('pattern_tags', []), ensure_ascii=False),
                journal_data.get('one_line_summary', ''),
                journal_data.get('confidence_score', 0.5),
                1, now, self.MARKET
            )
        )
        self.conn.commit()
        return self.cursor.lastrowid

    def extract_principles(self, lessons: List[Dict[str, Any]], source_journal_id: int) -> int:
        """Extract universal principles from lessons."""
        extracted_count = 0

        for lesson in lessons:
            if not isinstance(lesson, dict):
                continue

            condition = lesson.get('condition', '')
            action = lesson.get('action', '')
            reason = lesson.get('reason', '')
            priority = lesson.get('priority', 'medium')

            if not condition or not action:
                continue

            scope = 'universal' if priority == 'high' else 'sector'

            if self._save_principle(scope, None, condition, action, reason, priority, source_journal_id):
                extracted_count += 1

        return extracted_count

    def _save_principle(
        self, scope: str, scope_context: Optional[str], condition: str,
        action: str, reason: str, priority: str, source_journal_id: int
    ) -> bool:
        """Save a principle to database with market='US'."""
        try:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            self.cursor.execute("""
                SELECT id, supporting_trades, source_journal_ids
                FROM trading_principles
                WHERE condition = ? AND action = ? AND is_active = 1 AND market = ?
            """, (condition, action, self.MARKET))

            existing = self.cursor.fetchone()

            if existing:
                existing_ids = existing[2] or ''
                new_ids = f"{existing_ids},{source_journal_id}" if existing_ids else str(source_journal_id)

                self.cursor.execute("""
                    UPDATE trading_principles
                    SET supporting_trades = supporting_trades + 1,
                        confidence = MIN(1.0, confidence + 0.1),
                        source_journal_ids = ?,
                        last_validated_at = ?
                    WHERE id = ?
                """, (new_ids, now, existing[0]))
            else:
                self.cursor.execute("""
                    INSERT INTO trading_principles
                    (scope, scope_context, condition, action, reason, priority,
                     confidence, supporting_trades, source_journal_ids, created_at, is_active, market)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (scope, scope_context, condition, action, reason, priority,
                      0.5, 1, str(source_journal_id), now, 1, self.MARKET))

            self.conn.commit()
            return True

        except Exception as e:
            logger.error(f"Error saving US principle: {e}")
            return False

    def get_performance_tracker_stats(self, trigger_type: str = None) -> Dict[str, Any]:
        """
        Get performance statistics from us_analysis_performance_tracker.

        Queries actual 7/14/30-day returns for all analyzed US stocks (both traded and watched)
        to provide ground-truth performance data for buy decisions.

        Args:
            trigger_type: Filter by trigger type (optional)

        Returns:
            Dict with trigger stats, missed opportunities, and overall stats
        """
        stats = {}
        try:
            # Check if table exists
            self.cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='us_analysis_performance_tracker'"
            )
            if not self.cursor.fetchone():
                return stats

            # 1. Stats for the current trigger type (if provided)
            if trigger_type:
                self.cursor.execute("""
                    SELECT
                        COUNT(*) as total,
                        SUM(CASE WHEN return_30d > 0 THEN 1 ELSE 0 END) as wins,
                        AVG(return_7d) as avg_7d,
                        AVG(return_14d) as avg_14d,
                        AVG(return_30d) as avg_30d
                    FROM us_analysis_performance_tracker
                    WHERE trigger_type = ? AND tracking_status = 'completed'
                """, (trigger_type,))
                row = self.cursor.fetchone()
                if row and row[0] > 0:
                    stats['current_trigger'] = {
                        'trigger_type': trigger_type,
                        'total': row[0],
                        'win_rate': row[1] / row[0] if row[0] > 0 else 0,
                        'avg_7d': row[2],
                        'avg_14d': row[3],
                        'avg_30d': row[4],
                    }

            # 2. Missed opportunities: stocks we skipped but went up
            self.cursor.execute("""
                SELECT
                    COUNT(*) as total_skipped,
                    SUM(CASE WHEN return_30d > 5 THEN 1 ELSE 0 END) as missed_gains,
                    AVG(CASE WHEN return_30d > 5 THEN return_30d END) as avg_missed_gain
                FROM us_analysis_performance_tracker
                WHERE was_traded = 0 AND tracking_status = 'completed'
                    AND return_30d IS NOT NULL
            """)
            row = self.cursor.fetchone()
            if row and row[0] > 0:
                stats['missed_opportunities'] = {
                    'total_skipped': row[0],
                    'missed_gains_count': row[1] or 0,
                    'avg_missed_gain': row[2],
                }

            # 3. Traded vs watched comparison
            self.cursor.execute("""
                SELECT
                    was_traded,
                    COUNT(*) as count,
                    AVG(return_30d) as avg_30d
                FROM us_analysis_performance_tracker
                WHERE tracking_status = 'completed' AND return_30d IS NOT NULL
                GROUP BY was_traded
            """)
            traded_vs_watched = {}
            for row in self.cursor.fetchall():
                key = 'traded' if row[0] else 'watched'
                traded_vs_watched[key] = {
                    'count': row[1],
                    'avg_30d': row[2],
                }
            if traded_vs_watched:
                stats['traded_vs_watched'] = traded_vs_watched

            # 4. All trigger types performance ranking
            self.cursor.execute("""
                SELECT
                    trigger_type,
                    COUNT(*) as total,
                    SUM(CASE WHEN return_30d > 0 THEN 1 ELSE 0 END) as wins,
                    AVG(return_30d) as avg_30d
                FROM us_analysis_performance_tracker
                WHERE tracking_status = 'completed' AND return_30d IS NOT NULL
                    AND trigger_type IS NOT NULL
                GROUP BY trigger_type
                HAVING total >= 3
                ORDER BY avg_30d DESC
            """)
            trigger_ranking = []
            for row in self.cursor.fetchall():
                trigger_ranking.append({
                    'trigger_type': row[0],
                    'total': row[1],
                    'win_rate': row[2] / row[1] if row[1] > 0 else 0,
                    'avg_30d': row[3],
                })
            if trigger_ranking:
                stats['trigger_ranking'] = trigger_ranking

        except Exception as e:
            logger.warning(f"Failed to get US performance tracker stats: {e}")

        return stats

    def _format_performance_context(self, stats: Dict[str, Any]) -> List[str]:
        """Format performance tracker stats into context strings for the trading agent."""
        parts = []
        if not stats:
            return parts

        parts.append("#### 📈 Analysis Performance Tracker (Actual Results)")

        # Current trigger type stats
        if 'current_trigger' in stats:
            t = stats['current_trigger']
            win_pct = t['win_rate'] * 100
            avg_30d = t['avg_30d']
            avg_30d_str = f"{avg_30d * 100:+.1f}%" if avg_30d is not None else "N/A"
            parts.append(
                f"- **This trigger ({t['trigger_type']})**: "
                f"Win rate {win_pct:.0f}% (n={t['total']}), "
                f"30d avg return {avg_30d_str}"
            )

        # Trigger ranking
        if 'trigger_ranking' in stats:
            parts.append("- **Trigger Performance Ranking (30d, n>=3):**")
            for rank, t in enumerate(stats['trigger_ranking'][:5], 1):
                avg_30d_str = f"{t['avg_30d'] * 100:+.1f}%" if t['avg_30d'] is not None else "N/A"
                win_pct = t['win_rate'] * 100
                parts.append(
                    f"  {rank}. {t['trigger_type']}: "
                    f"{avg_30d_str} avg, {win_pct:.0f}% win (n={t['total']})"
                )

        parts.append("")
        return parts

    def get_context_for_ticker(self, ticker: str, sector: str = None, trigger_type: str = None) -> str:
        """Retrieve relevant trading journal context for buy decisions."""
        if not self.enable_journal:
            return ""

        try:
            context_parts = []

            # Performance tracker stats (ground truth data, no LLM cost)
            perf_stats = self.get_performance_tracker_stats(trigger_type)
            perf_context = self._format_performance_context(perf_stats)
            if perf_context:
                context_parts.extend(perf_context)

            # Universal principles for US market
            principles = self.get_universal_principles()
            if principles:
                context_parts.append("#### 🎯 Core Trading Principles (Applied to all trades)")
                context_parts.extend(principles)
                context_parts.append("")

            # Same stock history for US
            self.cursor.execute("""
                SELECT ticker, company_name, profit_rate, holding_days,
                       one_line_summary, lessons, pattern_tags, trade_date
                FROM trading_journal WHERE ticker = ? AND market = ?
                ORDER BY trade_date DESC LIMIT 3
            """, (ticker, self.MARKET))

            for entry in self.cursor.fetchall():
                if not context_parts or "Past Trading History" not in context_parts[-1]:
                    context_parts.append("#### Same Stock Past Trading History")

                lessons_str = ""
                try:
                    lessons = json.loads(entry[5]) if entry[5] else []
                    if lessons:
                        lessons_str = " / Lessons: " + ", ".join(
                            [l.get('action', '') for l in lessons[:2] if isinstance(l, dict)]
                        )
                except:
                    pass

                profit_emoji = "✅" if entry[2] > 0 else "❌"
                context_parts.append(
                    f"- [{entry[7][:10]}] {profit_emoji} Return {entry[2]:.1f}% "
                    f"(Held {entry[3]} days) - {entry[4]}{lessons_str}"
                )

            if context_parts and context_parts[-1].startswith("-"):
                context_parts.append("")

            # Intuitions for US market
            self.cursor.execute("""
                SELECT category, condition, insight, confidence
                FROM trading_intuitions WHERE is_active = 1 AND market = ?
                ORDER BY confidence DESC LIMIT 10
            """, (self.MARKET,))

            intuitions = self.cursor.fetchall()
            if intuitions:
                context_parts.append("#### Accumulated Trading Intuitions")
                for i in intuitions:
                    confidence_bar = "●" * int(i[3] * 5) + "○" * (5 - int(i[3] * 5))
                    context_parts.append(
                        f"- [{i[0]}] {i[1]} → {i[2]} (Confidence: {confidence_bar})"
                    )
                context_parts.append("")

            if context_parts:
                return "### 📚 Past Trading Experience Reference\n\n" + "\n".join(context_parts)
            return ""

        except Exception as e:
            logger.warning(f"Failed to get US journal context: {e}")
            return ""

    def get_universal_principles(self, limit: int = 5) -> List[str]:
        """Retrieve universal trading principles for US market.

        Only includes principles with supporting_trades >= 2 to avoid injecting
        unverified rules into LLM prompts. Limited to top 5 to reduce token usage.
        """
        try:
            self.cursor.execute("""
                SELECT condition, action, reason, priority, confidence, supporting_trades
                FROM trading_principles
                WHERE is_active = 1 AND scope = 'universal' AND market = ?
                  AND supporting_trades >= 2
                ORDER BY priority DESC, confidence DESC
                LIMIT ?
            """, (self.MARKET, limit))

            result = []
            for p in self.cursor.fetchall():
                priority_emoji = "🔴" if p[3] == 'high' else "🟡" if p[3] == 'medium' else "⚪"
                confidence_bar = "●" * int((p[4] or 0.5) * 5) + "○" * (5 - int((p[4] or 0.5) * 5))

                text = f"{priority_emoji} **{p[0]}** → {p[1]}"
                if p[2]:
                    text += f" (Reason: {p[2][:50]}...)" if len(p[2] or '') > 50 else f" (Reason: {p[2]})"
                text += f" [Confidence: {confidence_bar}, Trades: {p[5]}]"
                result.append(f"- {text}")

            return result

        except Exception as e:
            logger.warning(f"Failed to get US universal principles: {e}")
            return []

    def get_score_adjustment(self, ticker: str, sector: str = None, trigger_type: str = None) -> Tuple[int, List[str]]:
        """Calculate score adjustment based on past experiences and performance tracker data for US stocks."""
        try:
            adjustment = 0
            reasons = []

            # Same stock history for US
            self.cursor.execute("""
                SELECT profit_rate FROM trading_journal
                WHERE ticker = ? AND market = ? ORDER BY trade_date DESC LIMIT 3
            """, (ticker, self.MARKET))

            same_stock = self.cursor.fetchall()
            if same_stock:
                avg_profit = sum(s[0] for s in same_stock) / len(same_stock)
                if avg_profit < -5:
                    adjustment -= 1
                    reasons.append(f"Same stock past average loss {avg_profit:.1f}%")
                elif avg_profit > 10:
                    adjustment += 1
                    reasons.append(f"Same stock past average profit {avg_profit:.1f}%")

            # Sector performance for US
            if sector and sector.lower() != "unknown":
                self.cursor.execute("""
                    SELECT AVG(profit_rate), COUNT(*)
                    FROM trading_journal WHERE buy_scenario LIKE ? AND market = ?
                """, (f'%"{sector}"%', self.MARKET))

                sector_stats = self.cursor.fetchone()
                if sector_stats and sector_stats[1] >= 3:
                    if sector_stats[0] < -3:
                        adjustment -= 1
                        reasons.append(f"{sector} sector average loss {sector_stats[0]:.1f}%")
                    elif sector_stats[0] > 5:
                        adjustment += 1
                        reasons.append(f"{sector} sector average profit {sector_stats[0]:.1f}%")

            # Trigger type performance (from performance_tracker - ground truth)
            if trigger_type:
                perf_stats = self.get_performance_tracker_stats(trigger_type)
                if 'current_trigger' in perf_stats:
                    t = perf_stats['current_trigger']
                    if t['total'] >= 5:  # Require minimum sample size
                        if t['win_rate'] < 0.35:
                            adjustment -= 1
                            reasons.append(
                                f"Trigger '{trigger_type}' low win rate "
                                f"{t['win_rate']*100:.0f}% (n={t['total']}, actual 30d data)"
                            )
                        elif t['win_rate'] > 0.65:
                            adjustment += 1
                            reasons.append(
                                f"Trigger '{trigger_type}' high win rate "
                                f"{t['win_rate']*100:.0f}% (n={t['total']}, actual 30d data)"
                            )

            return max(-3, min(3, adjustment)), reasons

        except Exception as e:
            logger.warning(f"Failed to calculate US score adjustment: {e}")
            return 0, []
