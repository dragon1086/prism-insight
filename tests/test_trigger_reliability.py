"""
Trigger Reliability Card — Unit Tests (Phase 1)

Tests for get_trigger_reliability() in generate_dashboard_json.py
and get_us_trigger_reliability() in generate_us_dashboard_json.py
"""
import sqlite3
import sys
import pytest
from pathlib import Path
from datetime import datetime

# Path setup
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "examples"))


@pytest.fixture
def kr_db():
    """Create in-memory KR DB with test data"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # Create analysis_performance_tracker
    c.execute("""
        CREATE TABLE analysis_performance_tracker (
            id INTEGER PRIMARY KEY,
            ticker TEXT NOT NULL,
            company_name TEXT,
            trigger_type TEXT,
            trigger_mode TEXT,
            analyzed_date TEXT NOT NULL,
            analyzed_price REAL,
            decision TEXT,
            was_traded INTEGER DEFAULT 0,
            skip_reason TEXT,
            buy_score REAL,
            min_score REAL,
            target_price REAL,
            stop_loss REAL,
            risk_reward_ratio REAL,
            tracked_7d_date TEXT, tracked_7d_price REAL, tracked_7d_return REAL,
            tracked_14d_date TEXT, tracked_14d_price REAL, tracked_14d_return REAL,
            tracked_30d_date TEXT, tracked_30d_price REAL, tracked_30d_return REAL,
            tracking_status TEXT DEFAULT 'pending',
            created_at TEXT, updated_at TEXT
        )
    """)

    # Insert test data — 거래량 급증: 5 completed (4 positive, 1 negative) = 80% win rate
    for i in range(5):
        ret = 0.05 + i * 0.02 if i < 4 else -0.03
        c.execute("""
            INSERT INTO analysis_performance_tracker
            (ticker, trigger_type, analyzed_date, analyzed_price, tracking_status, tracked_30d_return)
            VALUES (?, '거래량 급증', '2026-01-01', 10000, 'completed', ?)
        """, (f'00{i}', ret))

    # 거래량 급증: 3 pending
    for i in range(3):
        c.execute("""
            INSERT INTO analysis_performance_tracker
            (ticker, trigger_type, analyzed_date, analyzed_price, tracking_status)
            VALUES (?, '거래량 급증', '2026-02-01', 10000, 'pending')
        """, (f'01{i}',))

    # 기술적 돌파: 2 completed (1 positive, 1 negative) = 50% win rate
    for i, ret in enumerate([0.08, -0.05]):
        c.execute("""
            INSERT INTO analysis_performance_tracker
            (ticker, trigger_type, analyzed_date, analyzed_price, tracking_status, tracked_30d_return)
            VALUES (?, '기술적 돌파', '2026-01-01', 5000, 'completed', ?)
        """, (f'02{i}', ret))

    # 뉴스 촉발: 1 completed = too few for grade
    c.execute("""
        INSERT INTO analysis_performance_tracker
        (ticker, trigger_type, analyzed_date, analyzed_price, tracking_status, tracked_30d_return)
        VALUES ('030', '뉴스 촉발', '2026-01-01', 8000, 'completed', 0.1)
    """)

    # Create trading_history
    c.execute("""
        CREATE TABLE trading_history (
            id INTEGER PRIMARY KEY,
            ticker TEXT, company_name TEXT, trigger_type TEXT,
            buy_price REAL, sell_price REAL, profit_rate REAL,
            buy_date TEXT, sell_date TEXT
        )
    """)

    # 거래량 급증: 6 trades, 5 wins = 83% win rate
    for i in range(6):
        pr = 5.0 + i if i < 5 else -3.0
        c.execute("""
            INSERT INTO trading_history
            (ticker, trigger_type, buy_price, sell_price, profit_rate, buy_date, sell_date)
            VALUES (?, '거래량 급증', 10000, ?, ?, '2026-01-15', '2026-01-20')
        """, (f'10{i}', 10000 + pr * 100, pr))

    # 기술적 돌파: 3 trades, 2 wins = 67% win rate
    for i, pr in enumerate([8.0, 4.0, -2.0]):
        c.execute("""
            INSERT INTO trading_history
            (ticker, trigger_type, buy_price, sell_price, profit_rate, buy_date, sell_date)
            VALUES (?, '기술적 돌파', 5000, ?, ?, '2026-01-18', '2026-01-25')
        """, (f'11{i}', 5000 + pr * 50, pr))

    # Create trading_principles
    c.execute("""
        CREATE TABLE trading_principles (
            id INTEGER PRIMARY KEY,
            scope TEXT DEFAULT 'universal',
            scope_context TEXT,
            condition TEXT NOT NULL,
            action TEXT NOT NULL,
            reason TEXT,
            priority TEXT DEFAULT 'medium',
            confidence REAL DEFAULT 0.5,
            supporting_trades INTEGER DEFAULT 1,
            source_journal_ids TEXT,
            created_at TEXT,
            last_validated_at TEXT,
            is_active INTEGER DEFAULT 1,
            market TEXT DEFAULT 'KR'
        )
    """)

    # Principle matching 거래량 급증
    c.execute("""
        INSERT INTO trading_principles
        (scope, condition, action, confidence, supporting_trades, is_active)
        VALUES ('universal', '거래량 2배 이상 급증 시 수급 확인 후 진입', '분할 매수 고려', 0.8, 3, 1)
    """)

    # Principle matching 기술적 돌파
    c.execute("""
        INSERT INTO trading_principles
        (scope, condition, action, confidence, supporting_trades, is_active)
        VALUES ('universal', '저항선 돌파 확인 후 눌림목 매수', '손절 5% 설정', 0.7, 2, 1)
    """)

    # Inactive principle (should be excluded)
    c.execute("""
        INSERT INTO trading_principles
        (scope, condition, action, confidence, supporting_trades, is_active)
        VALUES ('universal', '거래량 폭발 시 즉시 매수', '전량 매수', 0.3, 1, 0)
    """)

    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def us_db():
    """Create in-memory US DB with test data"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # Create us_analysis_performance_tracker
    c.execute("""
        CREATE TABLE us_analysis_performance_tracker (
            id INTEGER PRIMARY KEY,
            ticker TEXT NOT NULL,
            company_name TEXT NOT NULL,
            analysis_date TEXT NOT NULL,
            analysis_price REAL NOT NULL,
            predicted_direction TEXT,
            target_price REAL, stop_loss REAL,
            buy_score INTEGER, decision TEXT, skip_reason TEXT,
            risk_reward_ratio REAL,
            price_7d REAL, price_14d REAL, price_30d REAL,
            return_7d REAL, return_14d REAL, return_30d REAL,
            hit_target INTEGER DEFAULT 0, hit_stop_loss INTEGER DEFAULT 0,
            tracking_status TEXT DEFAULT 'pending',
            was_traded INTEGER DEFAULT 0,
            trigger_type TEXT, trigger_mode TEXT, sector TEXT,
            created_at TEXT NOT NULL, last_updated TEXT
        )
    """)

    # Gap Up Momentum Top: 4 completed (3 positive, 1 negative) = 75%
    for i in range(4):
        ret = 0.06 + i * 0.02 if i < 3 else -0.04
        c.execute("""
            INSERT INTO us_analysis_performance_tracker
            (ticker, company_name, analysis_date, analysis_price, trigger_type, return_30d, created_at)
            VALUES (?, 'Test Corp', '2026-01-01', 100.0, 'Gap Up Momentum Top', ?, datetime('now'))
        """, (f'TST{i}', ret))

    # Volume Surge Top: 10 pending (no return_30d)
    for i in range(10):
        c.execute("""
            INSERT INTO us_analysis_performance_tracker
            (ticker, company_name, analysis_date, analysis_price, trigger_type, created_at)
            VALUES (?, 'Pending Corp', '2026-02-01', 50.0, 'Volume Surge Top', datetime('now'))
        """, (f'PND{i}',))

    # Create us_trading_history
    c.execute("""
        CREATE TABLE us_trading_history (
            id INTEGER PRIMARY KEY,
            ticker TEXT, company_name TEXT, trigger_type TEXT,
            buy_price REAL, sell_price REAL, profit_rate REAL,
            buy_date TEXT, sell_date TEXT
        )
    """)

    # Gap Up Momentum Top: 2 trades, 1 win
    c.execute("""
        INSERT INTO us_trading_history
        (ticker, trigger_type, buy_price, sell_price, profit_rate, buy_date, sell_date)
        VALUES ('AAPL', 'Gap Up Momentum Top', 100, 108, 8.0, '2026-01-20', '2026-01-25')
    """)
    c.execute("""
        INSERT INTO us_trading_history
        (ticker, trigger_type, buy_price, sell_price, profit_rate, buy_date, sell_date)
        VALUES ('MSFT', 'Gap Up Momentum Top', 200, 190, -5.0, '2026-01-22', '2026-01-28')
    """)

    conn.commit()
    yield conn
    conn.close()


class TestKRTriggerReliability:
    """KR get_trigger_reliability() tests"""

    def test_basic_structure(self, kr_db):
        from generate_dashboard_json import DashboardDataGenerator
        gen = DashboardDataGenerator.__new__(DashboardDataGenerator)
        result = gen.get_trigger_reliability(kr_db)

        assert 'trigger_reliability' in result
        assert 'best_trigger' in result
        assert 'last_updated' in result
        assert isinstance(result['trigger_reliability'], list)
        assert len(result['trigger_reliability']) > 0

    def test_grade_a(self, kr_db):
        """거래량 급증: analysis 80% + trading 83% + >= 5 trades = Grade A"""
        from generate_dashboard_json import DashboardDataGenerator
        gen = DashboardDataGenerator.__new__(DashboardDataGenerator)
        result = gen.get_trigger_reliability(kr_db)

        vol_surge = next(
            (t for t in result['trigger_reliability'] if t['trigger_type'] == '거래량 급증'),
            None
        )
        assert vol_surge is not None
        assert vol_surge['grade'] == 'A'
        assert vol_surge['analysis_accuracy']['completed'] == 5
        assert vol_surge['analysis_accuracy']['win_rate_30d'] == 0.8
        assert vol_surge['actual_trading']['count'] == 6

    def test_grade_d_insufficient_data(self, kr_db):
        """뉴스 촉발: only 1 completed = Grade D"""
        from generate_dashboard_json import DashboardDataGenerator
        gen = DashboardDataGenerator.__new__(DashboardDataGenerator)
        result = gen.get_trigger_reliability(kr_db)

        news = next(
            (t for t in result['trigger_reliability'] if t['trigger_type'] == '뉴스 촉발'),
            None
        )
        assert news is not None
        assert news['grade'] == 'D'

    def test_principles_matching(self, kr_db):
        """Principles should match via keywords"""
        from generate_dashboard_json import DashboardDataGenerator
        gen = DashboardDataGenerator.__new__(DashboardDataGenerator)
        result = gen.get_trigger_reliability(kr_db)

        vol_surge = next(
            (t for t in result['trigger_reliability'] if t['trigger_type'] == '거래량 급증'),
            None
        )
        assert vol_surge is not None
        assert len(vol_surge['related_principles']) >= 1
        assert '거래량' in vol_surge['related_principles'][0]['condition']

    def test_inactive_principles_excluded(self, kr_db):
        """Inactive principles should not be matched"""
        from generate_dashboard_json import DashboardDataGenerator
        gen = DashboardDataGenerator.__new__(DashboardDataGenerator)
        result = gen.get_trigger_reliability(kr_db)

        vol_surge = next(
            (t for t in result['trigger_reliability'] if t['trigger_type'] == '거래량 급증'),
            None
        )
        # The inactive principle about "거래량 폭발" should NOT appear
        for p in vol_surge.get('related_principles', []):
            assert p['confidence'] != 0.3, "Inactive principle should be excluded"

    def test_best_trigger(self, kr_db):
        """Best trigger should be the one with highest grade"""
        from generate_dashboard_json import DashboardDataGenerator
        gen = DashboardDataGenerator.__new__(DashboardDataGenerator)
        result = gen.get_trigger_reliability(kr_db)

        assert result['best_trigger'] == '거래량 급증'

    def test_sorting(self, kr_db):
        """Triggers should be sorted by grade (A first), then completed desc"""
        from generate_dashboard_json import DashboardDataGenerator
        gen = DashboardDataGenerator.__new__(DashboardDataGenerator)
        result = gen.get_trigger_reliability(kr_db)

        triggers = result['trigger_reliability']
        grades = [t['grade'] for t in triggers]
        grade_order = {'A': 0, 'B': 1, 'C': 2, 'D': 3}
        grade_nums = [grade_order[g] for g in grades]
        assert grade_nums == sorted(grade_nums), "Should be sorted A > B > C > D"

    def test_empty_db(self):
        """Should handle empty/missing tables gracefully"""
        from generate_dashboard_json import DashboardDataGenerator
        gen = DashboardDataGenerator.__new__(DashboardDataGenerator)

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        result = gen.get_trigger_reliability(conn)
        conn.close()

        assert result['trigger_reliability'] == []
        assert result['best_trigger'] is None


class TestUSTriggerReliability:
    """US get_us_trigger_reliability() tests"""

    def test_basic_structure(self, us_db):
        from generate_us_dashboard_json import USDashboardDataGenerator
        gen = USDashboardDataGenerator.__new__(USDashboardDataGenerator)
        result = gen.get_us_trigger_reliability(us_db)

        assert 'trigger_reliability' in result
        assert 'best_trigger' in result
        assert 'last_updated' in result
        assert len(result['trigger_reliability']) > 0

    def test_us_column_names(self, us_db):
        """US uses return_30d not tracked_30d_return"""
        from generate_us_dashboard_json import USDashboardDataGenerator
        gen = USDashboardDataGenerator.__new__(USDashboardDataGenerator)
        result = gen.get_us_trigger_reliability(us_db)

        gap = next(
            (t for t in result['trigger_reliability'] if t['trigger_type'] == 'Gap Up Momentum Top'),
            None
        )
        assert gap is not None
        assert gap['analysis_accuracy']['completed'] == 4
        assert gap['analysis_accuracy']['win_rate_30d'] == 0.75

    def test_no_principles_for_us(self, us_db):
        """US has no trading_principles table — related_principles should be empty"""
        from generate_us_dashboard_json import USDashboardDataGenerator
        gen = USDashboardDataGenerator.__new__(USDashboardDataGenerator)
        result = gen.get_us_trigger_reliability(us_db)

        for t in result['trigger_reliability']:
            assert t['related_principles'] == []

    def test_grade_with_trading_data(self, us_db):
        """Gap Up: 4 completed 75% + 2 trades 50% (< 5 trades) = B"""
        from generate_us_dashboard_json import USDashboardDataGenerator
        gen = USDashboardDataGenerator.__new__(USDashboardDataGenerator)
        result = gen.get_us_trigger_reliability(us_db)

        gap = next(
            (t for t in result['trigger_reliability'] if t['trigger_type'] == 'Gap Up Momentum Top'),
            None
        )
        assert gap is not None
        assert gap['grade'] == 'B'

    def test_grade_d_pending(self, us_db):
        """Volume Surge Top: 10 pending, 0 completed = D"""
        from generate_us_dashboard_json import USDashboardDataGenerator
        gen = USDashboardDataGenerator.__new__(USDashboardDataGenerator)
        result = gen.get_us_trigger_reliability(us_db)

        vol = next(
            (t for t in result['trigger_reliability'] if t['trigger_type'] == 'Volume Surge Top'),
            None
        )
        assert vol is not None
        assert vol['grade'] == 'D'
        assert vol['analysis_accuracy']['total_tracked'] == 10
        assert vol['analysis_accuracy']['completed'] == 0

    def test_english_recommendations(self, us_db):
        """US recommendations should be in English"""
        from generate_us_dashboard_json import USDashboardDataGenerator
        gen = USDashboardDataGenerator.__new__(USDashboardDataGenerator)
        result = gen.get_us_trigger_reliability(us_db)

        for t in result['trigger_reliability']:
            # Should not contain Korean
            assert not any(ord(c) >= 0xAC00 and ord(c) <= 0xD7A3 for c in t['recommendation']), \
                f"US recommendation should be English: {t['recommendation']}"

    def test_empty_db(self):
        """Should handle missing table gracefully"""
        from generate_us_dashboard_json import USDashboardDataGenerator
        gen = USDashboardDataGenerator.__new__(USDashboardDataGenerator)

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        result = gen.get_us_trigger_reliability(conn)
        conn.close()

        assert result['trigger_reliability'] == []
        assert result['best_trigger'] is None
