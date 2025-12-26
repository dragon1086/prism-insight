"""
Jeon Ingu Contrarian Trading - Integrated with stock_tracking_db.sqlite

Enhanced table design:
- Each video creates one row
- Trade information recorded when action taken
- Proper linking between BUY and SELL via related_buy_id
"""

import aiosqlite
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any
import json
import logging

logger = logging.getLogger(__name__)

# Database file location - shared with main PRISM trading system
DB_FILE = Path(__file__).parent.parent / "stock_tracking_db.sqlite"


class JeoninguTradingDB:
    """Database manager for Jeon Ingu contrarian trading simulation"""

    def __init__(self, db_path: str = str(DB_FILE)):
        self.db_path = db_path

    async def initialize(self):
        """Initialize jeoningu_trades table in shared database"""
        async with aiosqlite.connect(self.db_path) as db:
            # Single table for all Jeon Ingu trading history
            # Each video = 1 row, with optional trade information
            await db.execute("""
                CREATE TABLE IF NOT EXISTS jeoningu_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,

                    -- Video information (every row has this)
                    video_id TEXT NOT NULL UNIQUE,
                    video_title TEXT NOT NULL,
                    video_date TEXT NOT NULL,
                    video_url TEXT NOT NULL,
                    analyzed_date TEXT NOT NULL,

                    -- AI Analysis results (every row has this)
                    jeon_sentiment TEXT NOT NULL,
                    jeon_reasoning TEXT,
                    contrarian_action TEXT NOT NULL,

                    -- Trade execution (only when action taken)
                    trade_type TEXT,
                    stock_code TEXT,
                    stock_name TEXT,
                    quantity INTEGER DEFAULT 0,
                    price REAL DEFAULT 0,
                    amount REAL DEFAULT 0,

                    -- Profit tracking (only for SELL)
                    related_buy_id INTEGER,
                    profit_loss REAL DEFAULT 0,
                    profit_loss_pct REAL DEFAULT 0,

                    -- Portfolio tracking
                    balance_before REAL NOT NULL,
                    balance_after REAL NOT NULL,
                    cumulative_return_pct REAL DEFAULT 0,

                    -- Metadata
                    notes TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,

                    FOREIGN KEY (related_buy_id) REFERENCES jeoningu_trades(id)
                )
            """)

            # Create indexes
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_jeoningu_video_id
                ON jeoningu_trades(video_id)
            """)

            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_jeoningu_analyzed_date
                ON jeoningu_trades(analyzed_date DESC)
            """)

            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_jeoningu_trade_type
                ON jeoningu_trades(trade_type)
            """)

            await db.commit()
            logger.info(f"Jeon Ingu tables initialized in {self.db_path}")

    async def video_id_exists(self, video_id: str) -> bool:
        """Check if video_id already exists in the database

        Also checks for suffixed versions (_SELL, _BUY) used in position switches.
        """
        async with aiosqlite.connect(self.db_path) as db:
            # Check for exact match or suffixed versions
            async with db.execute("""
                SELECT COUNT(*) FROM jeoningu_trades
                WHERE video_id = ? OR video_id = ? OR video_id = ?
            """, (video_id, f"{video_id}_SELL", f"{video_id}_BUY")) as cursor:
                count = (await cursor.fetchone())[0]
                return count > 0

    async def insert_trade(self, trade_data: Dict[str, Any]) -> int:
        """
        Insert video analysis and optional trade

        Args:
            trade_data: Dictionary with video info + analysis + optional trade info

        Returns:
            Inserted row ID
        """
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                INSERT INTO jeoningu_trades (
                    video_id, video_title, video_date, video_url, analyzed_date,
                    jeon_sentiment, jeon_reasoning, contrarian_action,
                    trade_type, stock_code, stock_name, quantity, price, amount,
                    related_buy_id, profit_loss, profit_loss_pct,
                    balance_before, balance_after, cumulative_return_pct, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade_data['video_id'],
                trade_data['video_title'],
                trade_data['video_date'],
                trade_data['video_url'],
                trade_data['analyzed_date'],
                trade_data['jeon_sentiment'],
                trade_data.get('jeon_reasoning', ''),
                trade_data['contrarian_action'],
                trade_data.get('trade_type'),
                trade_data.get('stock_code'),
                trade_data.get('stock_name'),
                trade_data.get('quantity', 0),
                trade_data.get('price', 0),
                trade_data.get('amount', 0),
                trade_data.get('related_buy_id'),
                trade_data.get('profit_loss', 0),
                trade_data.get('profit_loss_pct', 0),
                trade_data['balance_before'],
                trade_data['balance_after'],
                trade_data.get('cumulative_return_pct', 0),
                trade_data.get('notes', '')
            ))
            await db.commit()
            trade_id = cursor.lastrowid
            logger.info(f"Jeon Ingu trade inserted: ID {trade_id}, Action {trade_data['contrarian_action']}")
            return trade_id

    async def get_latest_balance(self) -> float:
        """Get latest balance after last trade"""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("""
                SELECT balance_after
                FROM jeoningu_trades
                ORDER BY id DESC
                LIMIT 1
            """) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0.0

    async def get_current_position(self) -> Optional[Dict[str, Any]]:
        """
        Get current holding position

        Logic:
        - Find last BUY
        - Check if there's a SELL after it
        - If no SELL, that's the current position
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            # Find last BUY
            async with db.execute("""
                SELECT * FROM jeoningu_trades
                WHERE trade_type = 'BUY'
                ORDER BY id DESC
                LIMIT 1
            """) as cursor:
                last_buy = await cursor.fetchone()

            if not last_buy:
                return None

            # Check if there's a SELL that references this BUY
            async with db.execute("""
                SELECT COUNT(*) FROM jeoningu_trades
                WHERE trade_type = 'SELL' AND related_buy_id = ?
            """, (last_buy['id'],)) as cursor:
                sell_count = (await cursor.fetchone())[0]

            if sell_count > 0:
                # Position was sold
                return None

            # Return current position
            return {
                'buy_id': last_buy['id'],
                'stock_code': last_buy['stock_code'],
                'stock_name': last_buy['stock_name'],
                'quantity': last_buy['quantity'],
                'buy_price': last_buy['price'],
                'buy_amount': last_buy['amount'],
                'buy_date': last_buy['analyzed_date'],
                'video_id': last_buy['video_id']
            }

    async def get_trade_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get trade history (all rows, including HOLD)"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("""
                SELECT * FROM jeoningu_trades
                ORDER BY id DESC
                LIMIT ?
            """, (limit,)) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def calculate_performance_metrics(self) -> Dict[str, Any]:
        """Calculate performance metrics from SELL trades"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            # Get all SELL trades
            async with db.execute("""
                SELECT profit_loss, profit_loss_pct
                FROM jeoningu_trades
                WHERE trade_type = 'SELL'
                ORDER BY id
            """) as cursor:
                sell_trades = await cursor.fetchall()

            if not sell_trades:
                return {
                    "total_trades": 0,
                    "winning_trades": 0,
                    "losing_trades": 0,
                    "draw_trades": 0,
                    "win_rate": 0.0,
                    "cumulative_return": 0.0,
                    "avg_return_per_trade": 0.0
                }

            total_trades = len(sell_trades)
            winning_trades = sum(1 for t in sell_trades if t['profit_loss'] > 0)
            losing_trades = sum(1 for t in sell_trades if t['profit_loss'] < 0)
            draw_trades = sum(1 for t in sell_trades if t['profit_loss'] == 0)

            # Get latest cumulative return
            async with db.execute("""
                SELECT cumulative_return_pct, balance_after
                FROM jeoningu_trades
                ORDER BY id DESC
                LIMIT 1
            """) as cursor:
                latest = await cursor.fetchone()
                cumulative_return = latest['cumulative_return_pct'] if latest else 0.0
                latest_balance = latest['balance_after'] if latest else 0.0

            # Calculate average return per trade
            avg_return = sum(t['profit_loss_pct'] for t in sell_trades) / total_trades

            return {
                "total_trades": total_trades,
                "winning_trades": winning_trades,
                "losing_trades": losing_trades,
                "draw_trades": draw_trades,
                "win_rate": (winning_trades / total_trades * 100) if total_trades > 0 else 0.0,
                "cumulative_return": cumulative_return,
                "avg_return_per_trade": avg_return,
                "latest_balance": latest_balance
            }

    async def get_dashboard_data(self) -> Dict[str, Any]:
        """Get all data for dashboard visualization"""
        metrics = await self.calculate_performance_metrics()
        history = await self.get_trade_history(limit=50)
        position = await self.get_current_position()
        balance = await self.get_latest_balance()

        return {
            "performance": metrics,
            "trade_history": history,
            "current_position": position,
            "current_balance": balance,
            "generated_at": datetime.now().isoformat()
        }


# Utility functions

async def init_database():
    """Initialize database tables"""
    db = JeoninguTradingDB()
    await db.initialize()
    logger.info("Jeon Ingu database initialized")


# Test function
async def test_database():
    """Test database operations"""
    db = JeoninguTradingDB()
    await db.initialize()

    # Scenario 1: First video → BUY
    buy_trade = {
        "video_id": "test001",
        "video_title": "전인구: 시장 상승할 것 같다",
        "video_date": "2025-11-23",
        "video_url": "https://youtube.com/watch?v=test001",
        "analyzed_date": datetime.now().isoformat(),
        "jeon_sentiment": "상승",
        "jeon_reasoning": "긍정적 지표 언급",
        "contrarian_action": "인버스2X매수",
        "trade_type": "BUY",
        "stock_code": "252670",
        "stock_name": "KODEX 200선물인버스2X",
        "quantity": 2000,
        "price": 5000,
        "amount": 10000000,
        "balance_before": 10000000,
        "balance_after": 10000000,  # 현금→주식 전환이므로 balance 변동 없음
        "notes": "첫 매수 (전액 투자)"
    }
    buy_id = await db.insert_trade(buy_trade)
    print(f"✅ BUY trade inserted: ID {buy_id}")

    # Scenario 2: Second video → SELL (neutral sentiment)
    sell_trade = {
        "video_id": "test002",
        "video_title": "전인구: 잘 모르겠다",
        "video_date": "2025-11-24",
        "video_url": "https://youtube.com/watch?v=test002",
        "analyzed_date": datetime.now().isoformat(),
        "jeon_sentiment": "중립",
        "jeon_reasoning": "명확한 방향성 없음",
        "contrarian_action": "전량매도",
        "trade_type": "SELL",
        "stock_code": "252670",
        "stock_name": "KODEX 200선물인버스2X",
        "quantity": 2000,
        "price": 5250,
        "amount": 10500000,
        "related_buy_id": buy_id,  # Link to previous BUY
        "profit_loss": 500000,
        "profit_loss_pct": 5.0,
        "balance_before": 10000000,
        "balance_after": 10500000,
        "cumulative_return_pct": 5.0,  # (10500000 - 10000000) / 10000000 * 100
        "notes": "중립 기조로 전량 매도"
    }
    sell_id = await db.insert_trade(sell_trade)
    print(f"✅ SELL trade inserted: ID {sell_id}, linked to BUY ID {buy_id}")

    # Scenario 3: Third video → HOLD (same sentiment, no action)
    hold_trade = {
        "video_id": "test003",
        "video_title": "전인구: 여전히 중립",
        "video_date": "2025-11-25",
        "video_url": "https://youtube.com/watch?v=test003",
        "analyzed_date": datetime.now().isoformat(),
        "jeon_sentiment": "중립",
        "jeon_reasoning": "계속 애매함",
        "contrarian_action": "관망",
        "trade_type": "HOLD",  # No actual trade
        "balance_before": 10500000,
        "balance_after": 10500000,
        "cumulative_return_pct": 5.0,
        "notes": "보유 종목 없음, 현금 보유"
    }
    hold_id = await db.insert_trade(hold_trade)
    print(f"✅ HOLD record inserted: ID {hold_id}")

    # Check current position
    position = await db.get_current_position()
    print(f"Current position: {position}")

    # Get metrics
    metrics = await db.calculate_performance_metrics()
    print(f"Metrics: {metrics}")

    print("✅ Test completed!")


if __name__ == "__main__":
    asyncio.run(test_database())
