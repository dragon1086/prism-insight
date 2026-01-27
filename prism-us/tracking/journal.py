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
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class USJournalManager:
    """Manages trading journal operations for US stocks."""

    MARKET = "US"  # Market identifier for shared tables

    def __init__(self, cursor, conn, language: str = "en", enable_journal: bool = False):
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
            from cores.agents.trading_journal_agent import create_trading_journal_agent
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
다음 완료된 미국 주식 매매를 복기해주세요:

## 매수 정보
- 종목: {company_name}({ticker})
- 매수가: ${buy_price:,.2f}
- 매수일: {buy_date}
- 매수 시나리오:
  - 매수 점수: {scenario_data.get('buy_score', 'N/A')}
  - 투자 근거: {scenario_data.get('rationale', 'N/A')}
  - 목표가: ${scenario_data.get('target_price', 'N/A')}
  - 손절가: ${scenario_data.get('stop_loss', 'N/A')}
  - 투자 기간: {scenario_data.get('investment_period', 'N/A')}
  - 섹터: {scenario_data.get('sector', 'N/A')}
  - 시장 상황: {scenario_data.get('market_condition', 'N/A')}

## 매도 정보
- 매도가: ${sell_price:,.2f}
- 수익률: {profit_rate:.2f}%
- 보유일수: {holding_days}일
- 매도 사유: {sell_reason}

## 분석 요청
1. yahoo_finance 도구로 현재 시장 상황과 해당 종목의 최근 흐름을 확인하세요
2. 매수 시점과 매도 시점의 상황을 비교 분석하세요
3. 판단의 적절성을 평가하고 교훈을 추출하세요
4. 패턴 태그를 부여하세요
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
        try:
            # Try markdown code block
            markdown_match = re.search(r'```(?:json)?\s*({[\s\S]*?})\s*```', response, re.DOTALL)
            if markdown_match:
                return json.loads(markdown_match.group(1))

            # Try direct JSON
            json_match = re.search(r'({[\s\S]*})', response, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
                json_str = re.sub(r',(\s*[}\]])', r'\1', json_str)
                return json.loads(json_str)

            # Try json_repair
            try:
                import json_repair
                repaired = json_repair.repair_json(response)
                return json.loads(repaired)
            except:
                pass

            return {
                "situation_analysis": {"raw_response": response[:500]},
                "judgment_evaluation": {},
                "lessons": [],
                "pattern_tags": [],
                "one_line_summary": "Analysis parsing failed",
                "confidence_score": 0.3
            }

        except Exception as e:
            logger.warning(f"Failed to parse US journal response: {e}")
            return {
                "situation_analysis": {"error": str(e)},
                "judgment_evaluation": {},
                "lessons": [],
                "pattern_tags": [],
                "one_line_summary": "Analysis parsing error",
                "confidence_score": 0.2
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

    def get_context_for_ticker(self, ticker: str, sector: str = None) -> str:
        """Retrieve relevant trading journal context for buy decisions."""
        if not self.enable_journal:
            return ""

        try:
            context_parts = []

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

    def get_universal_principles(self, limit: int = 10) -> List[str]:
        """Retrieve universal trading principles for US market."""
        try:
            self.cursor.execute("""
                SELECT condition, action, reason, priority, confidence, supporting_trades
                FROM trading_principles
                WHERE is_active = 1 AND scope = 'universal' AND market = ?
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

    def get_score_adjustment(self, ticker: str, sector: str = None) -> Tuple[int, List[str]]:
        """Calculate score adjustment based on past experiences for US stocks."""
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

            return max(-2, min(2, adjustment)), reasons

        except Exception as e:
            logger.warning(f"Failed to calculate US score adjustment: {e}")
            return 0, []
