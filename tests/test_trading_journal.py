#!/usr/bin/env python3
"""
Test Trading Journal System

This module tests the trading journal functionality including:
1. Database table creation
2. Journal entry creation
3. Context retrieval for buy decisions
4. Score adjustment calculation
"""

import asyncio
import json
import logging
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from stock_tracking_agent import StockTrackingAgent

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class TestTradingJournalTables:
    """Test trading journal database tables"""

    def setup_method(self):
        """Set up test fixtures"""
        # Create a temporary database file
        self.temp_db = tempfile.NamedTemporaryFile(suffix='.sqlite', delete=False)
        self.db_path = self.temp_db.name
        self.temp_db.close()

    def teardown_method(self):
        """Clean up test fixtures"""
        try:
            os.unlink(self.db_path)
        except:
            pass

    @pytest.mark.asyncio
    async def test_table_creation(self):
        """Test that trading_journal table is created correctly"""
        agent = StockTrackingAgent(db_path=self.db_path)

        # Mock the trading_agent to avoid MCP initialization
        agent.trading_agent = MagicMock()

        # Initialize the agent (creates tables)
        await agent.initialize(language="ko")

        # Check that trading_journal table exists
        agent.cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='trading_journal'
        """)
        result = agent.cursor.fetchone()
        assert result is not None, "trading_journal table should exist"

        # Check table columns
        agent.cursor.execute("PRAGMA table_info(trading_journal)")
        columns = {row[1] for row in agent.cursor.fetchall()}

        expected_columns = {
            'id', 'ticker', 'company_name', 'trade_date', 'trade_type',
            'buy_price', 'buy_date', 'buy_scenario', 'buy_market_context',
            'sell_price', 'sell_reason', 'profit_rate', 'holding_days',
            'situation_analysis', 'judgment_evaluation', 'lessons', 'pattern_tags',
            'one_line_summary', 'confidence_score', 'compression_layer',
            'compressed_summary', 'created_at', 'last_compressed_at'
        }

        assert expected_columns.issubset(columns), f"Missing columns: {expected_columns - columns}"

        # Clean up
        agent.conn.close()

    @pytest.mark.asyncio
    async def test_intuitions_table_creation(self):
        """Test that trading_intuitions table is created correctly"""
        agent = StockTrackingAgent(db_path=self.db_path)
        agent.trading_agent = MagicMock()
        await agent.initialize(language="ko")

        # Check that trading_intuitions table exists
        agent.cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='trading_intuitions'
        """)
        result = agent.cursor.fetchone()
        assert result is not None, "trading_intuitions table should exist"

        agent.conn.close()


class TestJournalContext:
    """Test journal context retrieval"""

    def setup_method(self):
        """Set up test fixtures"""
        self.temp_db = tempfile.NamedTemporaryFile(suffix='.sqlite', delete=False)
        self.db_path = self.temp_db.name
        self.temp_db.close()

    def teardown_method(self):
        """Clean up test fixtures"""
        try:
            os.unlink(self.db_path)
        except:
            pass

    @pytest.mark.asyncio
    async def test_empty_context(self):
        """Test context retrieval when no journal entries exist"""
        agent = StockTrackingAgent(db_path=self.db_path)
        agent.trading_agent = MagicMock()
        await agent.initialize(language="ko")

        # Get context for a stock with no history
        context = agent._get_relevant_journal_context(
            ticker="005930",
            sector="반도체"
        )

        # Should return empty string when no entries
        assert context == "", "Context should be empty when no journal entries exist"

        agent.conn.close()

    @pytest.mark.asyncio
    async def test_context_with_entries(self):
        """Test context retrieval when journal entries exist"""
        agent = StockTrackingAgent(db_path=self.db_path, enable_journal=True)
        agent.trading_agent = MagicMock()
        await agent.initialize(language="ko")

        # Insert test journal entry
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        agent.cursor.execute("""
            INSERT INTO trading_journal
            (ticker, company_name, trade_date, trade_type,
             buy_price, buy_date, buy_scenario, sell_price,
             sell_reason, profit_rate, holding_days,
             situation_analysis, judgment_evaluation, lessons, pattern_tags,
             one_line_summary, confidence_score, compression_layer, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "005930", "삼성전자", now, "sell",
            70000, now, '{"sector": "반도체", "buy_score": 8}', 75000,
            "목표가 달성", 7.14, 10,
            '{"buy_context_summary": "Test buy context"}',
            '{"buy_quality": "적절"}',
            '[{"condition": "급등 후", "action": "분할 매도", "reason": "변동성"}]',
            '["급등후조정", "분할매도"]',
            "급등 후 분할 매도로 수익 실현",
            0.8, 1, now
        ))
        agent.conn.commit()

        # Get context
        context = agent._get_relevant_journal_context(
            ticker="005930",
            sector="반도체"
        )

        # Verify context contains expected information
        # Context format: includes profit rate, holding days, and summary
        assert "수익률" in context or "7.1" in context, "Context should contain profit info"
        assert "급등" in context or "분할 매도" in context, "Context should contain summary"
        assert "동일 종목" in context, "Context should have same stock section"

        agent.conn.close()


class TestScoreAdjustment:
    """Test score adjustment calculation"""

    def setup_method(self):
        """Set up test fixtures"""
        self.temp_db = tempfile.NamedTemporaryFile(suffix='.sqlite', delete=False)
        self.db_path = self.temp_db.name
        self.temp_db.close()

    def teardown_method(self):
        """Clean up test fixtures"""
        try:
            os.unlink(self.db_path)
        except:
            pass

    @pytest.mark.asyncio
    async def test_no_adjustment_without_history(self):
        """Test no adjustment when no trading history exists"""
        agent = StockTrackingAgent(db_path=self.db_path)
        agent.trading_agent = MagicMock()
        await agent.initialize(language="ko")

        adjustment, reasons = agent._get_score_adjustment_from_context(
            ticker="005930",
            sector="반도체"
        )

        assert adjustment == 0, "No adjustment without history"
        assert len(reasons) == 0, "No reasons without history"

        agent.conn.close()

    @pytest.mark.asyncio
    async def test_negative_adjustment_for_losses(self):
        """Test negative adjustment for stocks with loss history"""
        agent = StockTrackingAgent(db_path=self.db_path)
        agent.trading_agent = MagicMock()
        await agent.initialize(language="ko")

        # Insert test entries with losses
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for i in range(3):
            agent.cursor.execute("""
                INSERT INTO trading_journal
                (ticker, company_name, trade_date, trade_type,
                 profit_rate, one_line_summary, compression_layer, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                "005930", "삼성전자", now, "sell",
                -8.0 - i,  # Loss of -8%, -9%, -10%
                "손절 매도", 1, now
            ))
        agent.conn.commit()

        adjustment, reasons = agent._get_score_adjustment_from_context(
            ticker="005930",
            sector="반도체"
        )

        assert adjustment < 0, "Should have negative adjustment for losses"
        assert len(reasons) > 0, "Should have reasons for adjustment"

        agent.conn.close()

    @pytest.mark.asyncio
    async def test_positive_adjustment_for_gains(self):
        """Test positive adjustment for stocks with profit history"""
        agent = StockTrackingAgent(db_path=self.db_path)
        agent.trading_agent = MagicMock()
        await agent.initialize(language="ko")

        # Insert test entries with profits
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for i in range(3):
            agent.cursor.execute("""
                INSERT INTO trading_journal
                (ticker, company_name, trade_date, trade_type,
                 profit_rate, one_line_summary, compression_layer, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                "005930", "삼성전자", now, "sell",
                12.0 + i,  # Profit of 12%, 13%, 14%
                "목표가 달성", 1, now
            ))
        agent.conn.commit()

        adjustment, reasons = agent._get_score_adjustment_from_context(
            ticker="005930",
            sector="반도체"
        )

        assert adjustment > 0, "Should have positive adjustment for gains"
        assert len(reasons) > 0, "Should have reasons for adjustment"

        agent.conn.close()


class TestParseJournalResponse:
    """Test journal response parsing"""

    def setup_method(self):
        """Set up test fixtures"""
        self.temp_db = tempfile.NamedTemporaryFile(suffix='.sqlite', delete=False)
        self.db_path = self.temp_db.name
        self.temp_db.close()

    def teardown_method(self):
        """Clean up test fixtures"""
        try:
            os.unlink(self.db_path)
        except:
            pass

    @pytest.mark.asyncio
    async def test_parse_valid_json(self):
        """Test parsing valid JSON response"""
        agent = StockTrackingAgent(db_path=self.db_path)
        agent.trading_agent = MagicMock()
        await agent.initialize(language="ko")

        response = '''
```json
{
    "situation_analysis": {
        "buy_context_summary": "Test buy context",
        "sell_context_summary": "Test sell context"
    },
    "judgment_evaluation": {
        "buy_quality": "적절"
    },
    "lessons": [
        {"condition": "급등 후", "action": "분할 매도", "reason": "변동성"}
    ],
    "pattern_tags": ["급등후조정"],
    "one_line_summary": "급등 후 분할 매도 성공",
    "confidence_score": 0.8
}
```
'''
        result = agent._parse_journal_response(response)

        assert "situation_analysis" in result
        assert result["one_line_summary"] == "급등 후 분할 매도 성공"
        assert result["confidence_score"] == 0.8

        agent.conn.close()

    @pytest.mark.asyncio
    async def test_parse_invalid_json(self):
        """Test parsing invalid JSON response returns default structure"""
        agent = StockTrackingAgent(db_path=self.db_path)
        agent.trading_agent = MagicMock()
        await agent.initialize(language="ko")

        response = "This is not valid JSON at all"

        result = agent._parse_journal_response(response)

        # Should return default structure
        assert "situation_analysis" in result
        assert "lessons" in result
        assert isinstance(result["lessons"], list)

        agent.conn.close()


class TestCompression:
    """Test memory compression functionality"""

    def setup_method(self):
        """Set up test fixtures"""
        self.temp_db = tempfile.NamedTemporaryFile(suffix='.sqlite', delete=False)
        self.db_path = self.temp_db.name
        self.temp_db.close()

    def teardown_method(self):
        """Clean up test fixtures"""
        try:
            os.unlink(self.db_path)
        except:
            pass

    @pytest.mark.asyncio
    async def test_compression_stats_empty(self):
        """Test compression stats with no entries"""
        agent = StockTrackingAgent(db_path=self.db_path, enable_journal=True)
        agent.trading_agent = MagicMock()
        await agent.initialize(language="ko")

        stats = agent.get_compression_stats()

        assert 'entries_by_layer' in stats
        assert stats['entries_by_layer']['layer1_detailed'] == 0
        assert stats['active_intuitions'] == 0

        agent.conn.close()

    @pytest.mark.asyncio
    async def test_compression_stats_with_entries(self):
        """Test compression stats with entries"""
        agent = StockTrackingAgent(db_path=self.db_path, enable_journal=True)
        agent.trading_agent = MagicMock()
        await agent.initialize(language="ko")

        # Insert test entries
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for i in range(5):
            agent.cursor.execute("""
                INSERT INTO trading_journal
                (ticker, company_name, trade_date, trade_type,
                 profit_rate, one_line_summary, compression_layer, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                f"00593{i}", f"테스트종목{i}", now, "sell",
                5.0 + i, f"테스트 요약 {i}", 1, now
            ))
        agent.conn.commit()

        stats = agent.get_compression_stats()

        assert stats['entries_by_layer']['layer1_detailed'] == 5
        assert stats['oldest_uncompressed'] is not None

        agent.conn.close()

    @pytest.mark.asyncio
    async def test_save_intuition(self):
        """Test saving intuition to database"""
        agent = StockTrackingAgent(db_path=self.db_path)
        agent.trading_agent = MagicMock()
        await agent.initialize(language="ko")

        intuition = {
            "category": "pattern",
            "subcategory": "반도체",
            "condition": "거래량 급감 3일 연속",
            "insight": "추세 전환 가능성 높음",
            "confidence": 0.8,
            "supporting_trades": 5,
            "success_rate": 0.75
        }

        result = agent._save_intuition(intuition, [1, 2, 3])
        assert result is True

        # Verify saved
        agent.cursor.execute("SELECT * FROM trading_intuitions WHERE category = 'pattern'")
        saved = agent.cursor.fetchone()

        assert saved is not None
        assert saved['condition'] == "거래량 급감 3일 연속"
        assert saved['confidence'] == 0.8

        agent.conn.close()

    @pytest.mark.asyncio
    async def test_save_duplicate_intuition_updates(self):
        """Test that saving duplicate intuition updates existing one"""
        agent = StockTrackingAgent(db_path=self.db_path)
        agent.trading_agent = MagicMock()
        await agent.initialize(language="ko")

        intuition = {
            "category": "pattern",
            "condition": "테스트 조건",
            "insight": "테스트 직관",
            "confidence": 0.6,
            "supporting_trades": 3,
            "success_rate": 0.7
        }

        # Save first time
        agent._save_intuition(intuition, [1])

        # Save second time with higher confidence
        intuition["confidence"] = 0.9
        intuition["supporting_trades"] = 5
        agent._save_intuition(intuition, [2, 3])

        # Should only have one entry, updated
        agent.cursor.execute("SELECT COUNT(*) FROM trading_intuitions")
        count = agent.cursor.fetchone()[0]
        assert count == 1

        agent.cursor.execute("SELECT supporting_trades FROM trading_intuitions")
        updated = agent.cursor.fetchone()
        assert updated['supporting_trades'] > 3  # Should be increased

        agent.conn.close()

    @pytest.mark.asyncio
    async def test_generate_simple_summary(self):
        """Test simple summary generation"""
        agent = StockTrackingAgent(db_path=self.db_path)
        agent.trading_agent = MagicMock()
        await agent.initialize(language="ko")

        entry = {
            "one_line_summary": "급등 후 분할 매도로 수익 실현",
            "buy_scenario": '{"sector": "반도체"}',
            "profit_rate": 7.5
        }

        summary = agent._generate_simple_summary(entry)
        assert "급등" in summary or "분할" in summary

        # Test without summary
        entry2 = {
            "buy_scenario": '{"sector": "반도체"}',
            "profit_rate": -3.5
        }

        summary2 = agent._generate_simple_summary(entry2)
        assert "반도체" in summary2 or "손실" in summary2

        agent.conn.close()

    @pytest.mark.asyncio
    async def test_format_entries_for_compression(self):
        """Test entry formatting for compression"""
        agent = StockTrackingAgent(db_path=self.db_path)
        agent.trading_agent = MagicMock()
        await agent.initialize(language="ko")

        entries = [
            {
                "id": 1,
                "ticker": "005930",
                "company_name": "삼성전자",
                "profit_rate": 5.5,
                "one_line_summary": "테스트 요약",
                "lessons": '[{"action": "교훈1"}]',
                "pattern_tags": '["태그1", "태그2"]'
            }
        ]

        formatted = agent._format_entries_for_compression(entries)

        assert "005930" in formatted
        assert "삼성전자" in formatted
        assert "5.5%" in formatted or "5.5" in formatted
        assert "교훈1" in formatted

        agent.conn.close()

    @pytest.mark.asyncio
    async def test_parse_compression_response(self):
        """Test parsing compression response"""
        agent = StockTrackingAgent(db_path=self.db_path)
        agent.trading_agent = MagicMock()
        await agent.initialize(language="ko")

        response = '''
```json
{
    "compressed_entries": [
        {
            "original_ids": [1, 2],
            "compressed_summary": "테스트 압축 요약",
            "key_lessons": ["교훈1", "교훈2"]
        }
    ],
    "new_intuitions": [
        {
            "category": "pattern",
            "condition": "테스트 조건",
            "insight": "테스트 직관",
            "confidence": 0.8
        }
    ]
}
```
'''
        result = agent._parse_compression_response(response)

        assert len(result['compressed_entries']) == 1
        assert result['compressed_entries'][0]['compressed_summary'] == "테스트 압축 요약"
        assert len(result['new_intuitions']) == 1
        assert result['new_intuitions'][0]['confidence'] == 0.8

        agent.conn.close()


def run_quick_test():
    """Run a quick integration test"""
    logger.info("Starting quick trading journal test")

    # Create temporary database
    with tempfile.NamedTemporaryFile(suffix='.sqlite', delete=False) as f:
        db_path = f.name

    async def async_test():
        try:
            agent = StockTrackingAgent(db_path=db_path)
            agent.trading_agent = MagicMock()

            # Initialize
            await agent.initialize(language="ko")
            logger.info("Agent initialized successfully")

            # Check tables exist
            agent.cursor.execute("""
                SELECT name FROM sqlite_master WHERE type='table'
            """)
            tables = [row[0] for row in agent.cursor.fetchall()]
            logger.info(f"Created tables: {tables}")

            assert "trading_journal" in tables, "trading_journal table missing"
            assert "trading_intuitions" in tables, "trading_intuitions table missing"

            # Test context retrieval (empty)
            context = agent._get_relevant_journal_context("005930", "반도체")
            logger.info(f"Empty context test: {'PASS' if context == '' else 'FAIL'}")

            # Test score adjustment (no history)
            adjustment, reasons = agent._get_score_adjustment_from_context("005930", "반도체")
            logger.info(f"No adjustment test: {'PASS' if adjustment == 0 else 'FAIL'}")

            # Test JSON parsing
            test_json = '{"one_line_summary": "테스트", "confidence_score": 0.9}'
            parsed = agent._parse_journal_response(test_json)
            logger.info(f"JSON parse test: {'PASS' if parsed.get('confidence_score') == 0.9 else 'FAIL'}")

            # Test compression stats
            stats = agent.get_compression_stats()
            logger.info(f"Compression stats test: {'PASS' if 'entries_by_layer' in stats else 'FAIL'}")

            # Test save intuition
            intuition = {
                "category": "test",
                "condition": "테스트 조건",
                "insight": "테스트 직관",
                "confidence": 0.7
            }
            save_result = agent._save_intuition(intuition, [1])
            logger.info(f"Save intuition test: {'PASS' if save_result else 'FAIL'}")

            # Test simple summary generation
            entry = {"profit_rate": 5.0, "buy_scenario": '{"sector": "테스트"}'}
            summary = agent._generate_simple_summary(entry)
            logger.info(f"Simple summary test: {'PASS' if summary else 'FAIL'}")

            agent.conn.close()
            logger.info("Quick test completed successfully!")
            return True

        except Exception as e:
            logger.error(f"Quick test failed: {e}")
            import traceback
            traceback.print_exc()
            return False

        finally:
            # Cleanup
            try:
                os.unlink(db_path)
            except:
                pass

    return asyncio.run(async_test())


class TestTradingPrinciples:
    """Test trading principles table and extraction"""

    def setup_method(self):
        """Set up test fixtures"""
        self.temp_db = tempfile.NamedTemporaryFile(suffix='.sqlite', delete=False)
        self.db_path = self.temp_db.name
        self.temp_db.close()

    def teardown_method(self):
        """Clean up test fixtures"""
        try:
            os.unlink(self.db_path)
        except:
            pass

    @pytest.mark.asyncio
    async def test_trading_principles_table_exists(self):
        """Test that trading_principles table is created correctly"""
        agent = StockTrackingAgent(db_path=self.db_path)
        agent.trading_agent = MagicMock()
        await agent.initialize(language="ko")

        # Check that trading_principles table exists
        agent.cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='trading_principles'
        """)
        result = agent.cursor.fetchone()
        assert result is not None, "trading_principles table should exist"

        # Check table columns
        agent.cursor.execute("PRAGMA table_info(trading_principles)")
        columns = {row[1] for row in agent.cursor.fetchall()}

        expected_columns = {
            'id', 'scope', 'scope_context', 'condition', 'action', 'reason',
            'priority', 'confidence', 'supporting_trades', 'source_journal_ids',
            'created_at', 'last_validated_at', 'is_active'
        }

        assert expected_columns.issubset(columns), f"Missing columns: {expected_columns - columns}"
        agent.conn.close()

    @pytest.mark.asyncio
    async def test_trading_intuitions_has_scope_column(self):
        """Test that trading_intuitions has scope column"""
        agent = StockTrackingAgent(db_path=self.db_path)
        agent.trading_agent = MagicMock()
        await agent.initialize(language="ko")

        # Check scope column exists
        agent.cursor.execute("PRAGMA table_info(trading_intuitions)")
        columns = {row[1] for row in agent.cursor.fetchall()}

        assert 'scope' in columns, "scope column should exist in trading_intuitions"
        agent.conn.close()

    @pytest.mark.asyncio
    async def test_extract_principles_from_lessons(self):
        """Test extracting principles from lessons"""
        agent = StockTrackingAgent(db_path=self.db_path)
        agent.trading_agent = MagicMock()
        await agent.initialize(language="ko")

        # Test lessons
        lessons = [
            {
                "condition": "대량거래 급락 시",
                "action": "즉시 전량 정리",
                "reason": "시나리오 무효 가능성",
                "priority": "high"
            },
            {
                "condition": "반등 매수 시",
                "action": "비중 조절",
                "reason": "과열 신호",
                "priority": "medium"
            }
        ]

        # Extract principles
        count = agent._extract_principles_from_lessons(lessons, 1)

        assert count == 2, f"Expected 2 principles, got {count}"

        # Verify principles were saved
        agent.cursor.execute("SELECT * FROM trading_principles ORDER BY id")
        saved = agent.cursor.fetchall()

        assert len(saved) == 2
        assert saved[0]['scope'] == 'universal'  # high priority -> universal
        assert saved[0]['priority'] == 'high'
        assert saved[1]['scope'] == 'sector'  # medium priority -> sector
        assert saved[1]['priority'] == 'medium'

        agent.conn.close()

    @pytest.mark.asyncio
    async def test_high_priority_lessons_become_universal(self):
        """Test that high priority lessons are classified as universal"""
        agent = StockTrackingAgent(db_path=self.db_path)
        agent.trading_agent = MagicMock()
        await agent.initialize(language="ko")

        lessons = [
            {
                "condition": "손절가 7% 초과 시",
                "action": "해당 종목 보유 금지",
                "reason": "회복 확률 낮음",
                "priority": "high"
            }
        ]

        agent._extract_principles_from_lessons(lessons, 1)

        agent.cursor.execute("""
            SELECT scope, priority FROM trading_principles
            WHERE priority = 'high'
        """)
        result = agent.cursor.fetchone()

        assert result['scope'] == 'universal', "High priority should be universal scope"
        agent.conn.close()

    @pytest.mark.asyncio
    async def test_get_universal_principles(self):
        """Test retrieving universal principles"""
        agent = StockTrackingAgent(db_path=self.db_path)
        agent.trading_agent = MagicMock()
        await agent.initialize(language="ko")

        # Insert test principles
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        agent.cursor.execute("""
            INSERT INTO trading_principles
            (scope, condition, action, reason, priority, confidence, supporting_trades, created_at, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            'universal',
            '대량거래 급락 시',
            '즉시 전량 정리',
            '시나리오 무효',
            'high',
            0.8,
            3,
            now,
            1
        ))
        agent.cursor.execute("""
            INSERT INTO trading_principles
            (scope, condition, action, reason, priority, confidence, supporting_trades, created_at, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            'sector',
            '반도체 3일 고점 후',
            '조정 대비',
            '패턴',
            'medium',
            0.6,
            2,
            now,
            1
        ))
        agent.conn.commit()

        # Get universal principles
        principles = agent._get_universal_principles()

        assert len(principles) == 1, "Should only return universal scope principles"
        assert "대량거래 급락 시" in principles[0]
        assert "🔴" in principles[0]  # High priority emoji

        agent.conn.close()

    @pytest.mark.asyncio
    async def test_context_includes_universal_principles(self):
        """Test that context includes universal principles section"""
        agent = StockTrackingAgent(db_path=self.db_path, enable_journal=True)
        agent.trading_agent = MagicMock()
        await agent.initialize(language="ko")

        # Insert universal principle
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        agent.cursor.execute("""
            INSERT INTO trading_principles
            (scope, condition, action, reason, priority, confidence, supporting_trades, created_at, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            'universal',
            '테스트 조건',
            '테스트 행동',
            '테스트 이유',
            'high',
            0.9,
            5,
            now,
            1
        ))
        agent.conn.commit()

        # Get context
        context = agent._get_relevant_journal_context(
            ticker="005930",
            sector="반도체"
        )

        assert "핵심 매매 원칙" in context, "Context should include principles section"
        assert "테스트 조건" in context, "Context should include principle content"

        agent.conn.close()

    @pytest.mark.asyncio
    async def test_save_principle_updates_existing(self):
        """Test that saving duplicate principle updates existing one"""
        agent = StockTrackingAgent(db_path=self.db_path)
        agent.trading_agent = MagicMock()
        await agent.initialize(language="ko")

        # Save first principle
        agent._save_principle(
            scope='universal',
            scope_context=None,
            condition='테스트 조건',
            action='테스트 행동',
            reason='테스트 이유',
            priority='high',
            source_journal_id=1
        )

        # Save duplicate
        agent._save_principle(
            scope='universal',
            scope_context=None,
            condition='테스트 조건',
            action='테스트 행동',
            reason='테스트 이유',
            priority='high',
            source_journal_id=2
        )

        # Should have only 1 entry with updated evidence
        agent.cursor.execute("SELECT COUNT(*) FROM trading_principles")
        count = agent.cursor.fetchone()[0]
        assert count == 1, "Should have only 1 principle"

        agent.cursor.execute("SELECT supporting_trades, source_journal_ids FROM trading_principles")
        result = agent.cursor.fetchone()
        assert result['supporting_trades'] == 2, "Supporting trades should be 2"
        assert "1" in result['source_journal_ids'] and "2" in result['source_journal_ids']

        agent.conn.close()


class TestMigrationScript:
    """Test migration script functionality"""

    def setup_method(self):
        """Set up test fixtures"""
        self.temp_db = tempfile.NamedTemporaryFile(suffix='.sqlite', delete=False)
        self.db_path = self.temp_db.name
        self.temp_db.close()

    def teardown_method(self):
        """Clean up test fixtures"""
        try:
            os.unlink(self.db_path)
        except:
            pass

    @pytest.mark.asyncio
    async def test_migration_extracts_existing_lessons(self):
        """Test that migration extracts lessons from existing journals"""
        agent = StockTrackingAgent(db_path=self.db_path)
        agent.trading_agent = MagicMock()
        await agent.initialize(language="ko")

        # Insert test journal entries with lessons (simulating 카카오, 삼성전자 data)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lessons1 = json.dumps([
            {"condition": "대량거래 급락 시", "action": "즉시 정리", "reason": "무효", "priority": "high"},
            {"condition": "반등 매수 시", "action": "비중 조절", "reason": "과열", "priority": "medium"}
        ], ensure_ascii=False)

        lessons2 = json.dumps([
            {"condition": "3주 급등 시", "action": "수익 확정", "reason": "조정 위험", "priority": "high"}
        ], ensure_ascii=False)

        agent.cursor.execute("""
            INSERT INTO trading_journal
            (ticker, company_name, trade_date, trade_type, lessons, compression_layer, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, ('035720', '카카오', now, 'sell', lessons1, 1, now))

        agent.cursor.execute("""
            INSERT INTO trading_journal
            (ticker, company_name, trade_date, trade_type, lessons, compression_layer, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, ('005930', '삼성전자', now, 'sell', lessons2, 1, now))
        agent.conn.commit()

        # Simulate migration
        agent.cursor.execute("""
            SELECT id, lessons FROM trading_journal
            WHERE lessons IS NOT NULL AND lessons != '[]'
        """)
        journals = agent.cursor.fetchall()

        for journal in journals:
            lessons = json.loads(journal['lessons'])
            agent._extract_principles_from_lessons(lessons, journal['id'])

        # Verify migration results
        agent.cursor.execute("SELECT COUNT(*) FROM trading_principles")
        total = agent.cursor.fetchone()[0]
        assert total == 3, f"Expected 3 principles, got {total}"

        agent.cursor.execute("""
            SELECT COUNT(*) FROM trading_principles
            WHERE scope = 'universal' AND priority = 'high'
        """)
        universal_count = agent.cursor.fetchone()[0]
        assert universal_count == 2, f"Expected 2 universal high-priority, got {universal_count}"

        agent.conn.close()


class TestCleanupStaleData:
    """Test cleanup_stale_data functionality"""

    def setup_method(self):
        """Set up test fixtures"""
        self.temp_db = tempfile.NamedTemporaryFile(suffix='.sqlite', delete=False)
        self.db_path = self.temp_db.name
        self.temp_db.close()

    def teardown_method(self):
        """Clean up test fixtures"""
        try:
            os.unlink(self.db_path)
        except:
            pass

    @pytest.mark.asyncio
    async def test_cleanup_low_confidence_principles(self):
        """Test that low confidence principles are deactivated"""
        agent = StockTrackingAgent(db_path=self.db_path, enable_journal=True)
        agent.trading_agent = MagicMock()
        await agent.initialize(language="ko")

        # Insert principles with varying confidence
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for i, conf in enumerate([0.1, 0.2, 0.5, 0.8]):
            agent.cursor.execute("""
                INSERT INTO trading_principles
                (scope, condition, action, priority, confidence, created_at, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, ('universal', f'조건{i}', f'행동{i}', 'high', conf, now, 1))
        agent.conn.commit()

        # Run cleanup with dry_run first
        dry_stats = agent.cleanup_stale_data(min_confidence_threshold=0.3, dry_run=True)
        assert dry_stats["low_confidence_principles"] == 2  # 0.1 and 0.2

        # Run actual cleanup
        stats = agent.cleanup_stale_data(min_confidence_threshold=0.3, dry_run=False)
        assert stats["principles_deactivated"] >= 2

        # Verify only high-confidence principles remain active
        agent.cursor.execute("SELECT COUNT(*) FROM trading_principles WHERE is_active = 1")
        active = agent.cursor.fetchone()[0]
        assert active == 2  # 0.5 and 0.8

        agent.conn.close()

    @pytest.mark.asyncio
    async def test_cleanup_enforces_max_limit(self):
        """Test that max_principles limit is enforced"""
        agent = StockTrackingAgent(db_path=self.db_path, enable_journal=True)
        agent.trading_agent = MagicMock()
        await agent.initialize(language="ko")

        # Insert 10 principles
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for i in range(10):
            agent.cursor.execute("""
                INSERT INTO trading_principles
                (scope, condition, action, priority, confidence, created_at, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, ('universal', f'조건{i}', f'행동{i}', 'high', 0.5 + i * 0.05, now, 1))
        agent.conn.commit()

        # Run cleanup with max_principles=5
        stats = agent.cleanup_stale_data(max_principles=5, dry_run=False)

        # Verify only top 5 remain active
        agent.cursor.execute("SELECT COUNT(*) FROM trading_principles WHERE is_active = 1")
        active = agent.cursor.fetchone()[0]
        assert active == 5

        # Verify highest confidence ones are kept
        agent.cursor.execute("""
            SELECT confidence FROM trading_principles
            WHERE is_active = 1
            ORDER BY confidence DESC
        """)
        confidences = [row[0] for row in agent.cursor.fetchall()]
        assert min(confidences) >= 0.7  # Top 5 should have conf >= 0.7

        agent.conn.close()

    @pytest.mark.asyncio
    async def test_cleanup_archives_old_layer3(self):
        """Test that old Layer 3 entries are archived (deleted)"""
        agent = StockTrackingAgent(db_path=self.db_path, enable_journal=True)
        agent.trading_agent = MagicMock()
        await agent.initialize(language="ko")

        # Insert journal entries with different dates
        old_date = (datetime.now() - timedelta(days=400)).strftime("%Y-%m-%d %H:%M:%S")
        recent_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")

        # Old Layer 3 entry (should be archived)
        agent.cursor.execute("""
            INSERT INTO trading_journal
            (ticker, company_name, trade_date, trade_type, profit_rate,
             one_line_summary, compression_layer, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, ("005930", "삼성전자", old_date, "sell", 5.0, "오래된 거래", 3, old_date))

        # Recent Layer 3 entry (should NOT be archived)
        agent.cursor.execute("""
            INSERT INTO trading_journal
            (ticker, company_name, trade_date, trade_type, profit_rate,
             one_line_summary, compression_layer, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, ("035720", "카카오", recent_date, "sell", 3.0, "최근 거래", 3, recent_date))

        agent.conn.commit()

        # Run cleanup
        stats = agent.cleanup_stale_data(archive_layer3_days=365, dry_run=False)
        assert stats["journal_entries_archived"] == 1

        # Verify only recent entry remains
        agent.cursor.execute("SELECT COUNT(*) FROM trading_journal WHERE compression_layer = 3")
        remaining = agent.cursor.fetchone()[0]
        assert remaining == 1

        agent.conn.close()

    @pytest.mark.asyncio
    async def test_cleanup_dry_run_no_changes(self):
        """Test that dry_run mode doesn't modify data"""
        agent = StockTrackingAgent(db_path=self.db_path, enable_journal=True)
        agent.trading_agent = MagicMock()
        await agent.initialize(language="ko")

        # Insert low confidence principle
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        agent.cursor.execute("""
            INSERT INTO trading_principles
            (scope, condition, action, priority, confidence, created_at, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, ('universal', '테스트', '테스트', 'high', 0.1, now, 1))
        agent.conn.commit()

        # Run dry_run cleanup
        stats = agent.cleanup_stale_data(min_confidence_threshold=0.3, dry_run=True)
        assert stats["low_confidence_principles"] == 1
        assert stats["dry_run"] is True

        # Verify data is unchanged
        agent.cursor.execute("SELECT COUNT(*) FROM trading_principles WHERE is_active = 1")
        active = agent.cursor.fetchone()[0]
        assert active == 1  # Still active because dry_run

        agent.conn.close()


if __name__ == "__main__":
    # Run quick test when executed directly
    success = run_quick_test()
    sys.exit(0 if success else 1)
