#!/usr/bin/env python3
"""
Analysis Performance Deep Dive Report

Statistically analyzes performance by trigger type and trade/watch decisions
to help determine whether filters should be adjusted.

Usage:
    python performance_analysis_report.py                    # Full report
    python performance_analysis_report.py --format markdown  # Markdown output
    python performance_analysis_report.py --output report.md # Save to file
"""
from dotenv import load_dotenv
load_dotenv()

import sqlite3
import argparse
import logging
from datetime import datetime
from typing import Dict, List, Any, Optional
from pathlib import Path
from collections import defaultdict

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Project root path
PROJECT_ROOT = Path(__file__).parent
DB_PATH = PROJECT_ROOT / "stock_tracking_db.sqlite"

# Statistics library (optional)
try:
    from scipy import stats
    SCIPY_AVAILABLE = True
except ImportError:
    SCIPY_AVAILABLE = False
    logger.warning("scipy package not available, statistical significance testing disabled.")


class PerformanceAnalyzer:
    """Deep performance analyzer for analyzed stocks"""

    def __init__(self, db_path: str = None):
        """
        Args:
            db_path: SQLite DB path
        """
        self.db_path = db_path or str(DB_PATH)
        self.today = datetime.now().strftime("%Y-%m-%d")

    def connect_db(self) -> sqlite3.Connection:
        """Connect to database"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def get_all_completed_data(self) -> List[Dict[str, Any]]:
        """Query all completed tracking data"""
        conn = self.connect_db()
        try:
            cursor = conn.execute("""
                SELECT *
                FROM analysis_performance_tracker
                WHERE tracking_status = 'completed'
                ORDER BY analyzed_date DESC
            """)
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def get_overview_stats(self) -> Dict[str, Any]:
        """Overall status statistics"""
        conn = self.connect_db()
        try:
            # Count by tracking status
            cursor = conn.execute("""
                SELECT
                    tracking_status,
                    COUNT(*) as count
                FROM analysis_performance_tracker
                GROUP BY tracking_status
            """)
            status_counts = {row['tracking_status']: row['count'] for row in cursor.fetchall()}

            # Count by traded/watched
            cursor = conn.execute("""
                SELECT
                    was_traded,
                    COUNT(*) as count
                FROM analysis_performance_tracker
                GROUP BY was_traded
            """)
            traded_counts = {}
            for row in cursor.fetchall():
                key = 'traded' if row['was_traded'] else 'watched'
                traded_counts[key] = row['count']

            # Count by period
            cursor = conn.execute("""
                SELECT
                    strftime('%Y-%m', analyzed_date) as month,
                    COUNT(*) as count
                FROM analysis_performance_tracker
                GROUP BY month
                ORDER BY month DESC
                LIMIT 6
            """)
            monthly_counts = [(row['month'], row['count']) for row in cursor.fetchall()]

            return {
                'status_counts': status_counts,
                'traded_counts': traded_counts,
                'monthly_counts': monthly_counts,
                'total': sum(status_counts.values())
            }
        finally:
            conn.close()

    def analyze_by_trigger_type(self) -> Dict[str, Dict[str, Any]]:
        """Analyze performance by trigger type"""
        conn = self.connect_db()
        try:
            cursor = conn.execute("""
                SELECT
                    trigger_type,
                    was_traded,
                    tracked_7d_return,
                    tracked_14d_return,
                    tracked_30d_return,
                    buy_score,
                    risk_reward_ratio,
                    target_price,
                    stop_loss,
                    analyzed_price
                FROM analysis_performance_tracker
                WHERE tracking_status = 'completed'
            """)

            # Collect data by trigger type
            trigger_data = defaultdict(lambda: {
                'returns_7d': [],
                'returns_14d': [],
                'returns_30d': [],
                'traded_returns_30d': [],
                'watched_returns_30d': [],
                'buy_scores': [],
                'rr_ratios': [],
                'count': 0,
                'traded_count': 0
            })

            for row in cursor.fetchall():
                trigger_type = row['trigger_type'] or 'unknown'
                data = trigger_data[trigger_type]

                data['count'] += 1
                if row['was_traded']:
                    data['traded_count'] += 1

                if row['tracked_7d_return'] is not None:
                    data['returns_7d'].append(row['tracked_7d_return'])
                if row['tracked_14d_return'] is not None:
                    data['returns_14d'].append(row['tracked_14d_return'])
                if row['tracked_30d_return'] is not None:
                    data['returns_30d'].append(row['tracked_30d_return'])
                    if row['was_traded']:
                        data['traded_returns_30d'].append(row['tracked_30d_return'])
                    else:
                        data['watched_returns_30d'].append(row['tracked_30d_return'])

                if row['buy_score'] is not None:
                    data['buy_scores'].append(row['buy_score'])
                if row['risk_reward_ratio'] is not None:
                    data['rr_ratios'].append(row['risk_reward_ratio'])

            # Calculate statistics
            results = {}
            for trigger_type, data in trigger_data.items():
                results[trigger_type] = {
                    'count': data['count'],
                    'traded_count': data['traded_count'],
                    'traded_rate': data['traded_count'] / data['count'] if data['count'] > 0 else 0,
                    'avg_7d_return': self._safe_mean(data['returns_7d']),
                    'avg_14d_return': self._safe_mean(data['returns_14d']),
                    'avg_30d_return': self._safe_mean(data['returns_30d']),
                    'std_30d_return': self._safe_std(data['returns_30d']),
                    'win_rate_30d': self._win_rate(data['returns_30d']),
                    'traded_avg_30d': self._safe_mean(data['traded_returns_30d']),
                    'watched_avg_30d': self._safe_mean(data['watched_returns_30d']),
                    'avg_buy_score': self._safe_mean(data['buy_scores']),
                    'avg_rr_ratio': self._safe_mean(data['rr_ratios']),
                    # Raw data (for statistical testing)
                    '_returns_30d': data['returns_30d'],
                    '_traded_returns_30d': data['traded_returns_30d'],
                    '_watched_returns_30d': data['watched_returns_30d']
                }

            return results
        finally:
            conn.close()

    def analyze_traded_vs_watched(self) -> Dict[str, Any]:
        """Compare traded vs watched performance"""
        conn = self.connect_db()
        try:
            cursor = conn.execute("""
                SELECT
                    was_traded,
                    tracked_7d_return,
                    tracked_14d_return,
                    tracked_30d_return
                FROM analysis_performance_tracker
                WHERE tracking_status = 'completed'
            """)

            traded_returns = {'7d': [], '14d': [], '30d': []}
            watched_returns = {'7d': [], '14d': [], '30d': []}

            for row in cursor.fetchall():
                target = traded_returns if row['was_traded'] else watched_returns

                if row['tracked_7d_return'] is not None:
                    target['7d'].append(row['tracked_7d_return'])
                if row['tracked_14d_return'] is not None:
                    target['14d'].append(row['tracked_14d_return'])
                if row['tracked_30d_return'] is not None:
                    target['30d'].append(row['tracked_30d_return'])

            result = {
                'traded': {
                    'count': len(traded_returns['30d']),
                    'avg_7d': self._safe_mean(traded_returns['7d']),
                    'avg_14d': self._safe_mean(traded_returns['14d']),
                    'avg_30d': self._safe_mean(traded_returns['30d']),
                    'std_30d': self._safe_std(traded_returns['30d']),
                    'win_rate': self._win_rate(traded_returns['30d']),
                    '_returns_30d': traded_returns['30d']
                },
                'watched': {
                    'count': len(watched_returns['30d']),
                    'avg_7d': self._safe_mean(watched_returns['7d']),
                    'avg_14d': self._safe_mean(watched_returns['14d']),
                    'avg_30d': self._safe_mean(watched_returns['30d']),
                    'std_30d': self._safe_std(watched_returns['30d']),
                    'win_rate': self._win_rate(watched_returns['30d']),
                    '_returns_30d': watched_returns['30d']
                }
            }

            # Statistical significance test
            if SCIPY_AVAILABLE and traded_returns['30d'] and watched_returns['30d']:
                if len(traded_returns['30d']) >= 5 and len(watched_returns['30d']) >= 5:
                    t_stat, p_value = stats.ttest_ind(
                        traded_returns['30d'],
                        watched_returns['30d'],
                        equal_var=False  # Welch's t-test
                    )
                    result['t_test'] = {
                        't_statistic': t_stat,
                        'p_value': p_value,
                        'significant': p_value < 0.05
                    }

            return result
        finally:
            conn.close()

    def analyze_rr_threshold_impact(self) -> Dict[str, Any]:
        """Analyze performance by risk-reward ratio threshold

        Analyzes how performance differs between traded/watched decisions
        at various RR thresholds (1.5, 1.75, 2.0, 2.5)
        """
        conn = self.connect_db()
        try:
            cursor = conn.execute("""
                SELECT
                    risk_reward_ratio,
                    was_traded,
                    tracked_30d_return
                FROM analysis_performance_tracker
                WHERE tracking_status = 'completed'
                  AND risk_reward_ratio IS NOT NULL
            """)

            # Collect data by RR range
            thresholds = [0, 1.0, 1.5, 1.75, 2.0, 2.5, 100]
            rr_data = {}

            for i in range(len(thresholds) - 1):
                low, high = thresholds[i], thresholds[i+1]
                label = f"{low:.1f}~{high:.1f}" if high < 100 else f"{low:.1f}+"
                rr_data[label] = {
                    'traded_returns': [],
                    'watched_returns': [],
                    'all_returns': []
                }

            for row in cursor.fetchall():
                rr = row['risk_reward_ratio']
                ret = row['tracked_30d_return']

                if rr is None or ret is None:
                    continue

                # Find matching range
                for i in range(len(thresholds) - 1):
                    low, high = thresholds[i], thresholds[i+1]
                    if low <= rr < high:
                        label = f"{low:.1f}~{high:.1f}" if high < 100 else f"{low:.1f}+"
                        rr_data[label]['all_returns'].append(ret)
                        if row['was_traded']:
                            rr_data[label]['traded_returns'].append(ret)
                        else:
                            rr_data[label]['watched_returns'].append(ret)
                        break

            # Calculate statistics
            result = {}
            for label, data in rr_data.items():
                result[label] = {
                    'total_count': len(data['all_returns']),
                    'traded_count': len(data['traded_returns']),
                    'watched_count': len(data['watched_returns']),
                    'avg_all_return': self._safe_mean(data['all_returns']),
                    'avg_traded_return': self._safe_mean(data['traded_returns']),
                    'avg_watched_return': self._safe_mean(data['watched_returns']),
                    'win_rate_all': self._win_rate(data['all_returns']),
                    'win_rate_watched': self._win_rate(data['watched_returns'])
                }

            return result
        finally:
            conn.close()

    def get_missed_opportunities(self, top_n: int = 10) -> List[Dict[str, Any]]:
        """Missed opportunities analysis (watched stocks that surged)"""
        conn = self.connect_db()
        try:
            cursor = conn.execute("""
                SELECT
                    ticker,
                    company_name,
                    trigger_type,
                    analyzed_date,
                    analyzed_price,
                    tracked_30d_price,
                    tracked_30d_return,
                    buy_score,
                    min_score,
                    risk_reward_ratio,
                    skip_reason
                FROM analysis_performance_tracker
                WHERE tracking_status = 'completed'
                  AND was_traded = 0
                  AND tracked_30d_return > 0.1
                ORDER BY tracked_30d_return DESC
                LIMIT ?
            """, (top_n,))

            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def get_avoided_losses(self, top_n: int = 10) -> List[Dict[str, Any]]:
        """Avoided losses analysis (watched stocks that dropped)"""
        conn = self.connect_db()
        try:
            cursor = conn.execute("""
                SELECT
                    ticker,
                    company_name,
                    trigger_type,
                    analyzed_date,
                    analyzed_price,
                    tracked_30d_price,
                    tracked_30d_return,
                    buy_score,
                    min_score,
                    risk_reward_ratio,
                    skip_reason
                FROM analysis_performance_tracker
                WHERE tracking_status = 'completed'
                  AND was_traded = 0
                  AND tracked_30d_return < -0.1
                ORDER BY tracked_30d_return ASC
                LIMIT ?
            """, (top_n,))

            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def generate_recommendations(self) -> List[str]:
        """Generate data-driven recommendations"""
        recommendations = []

        # Traded vs watched analysis
        tv = self.analyze_traded_vs_watched()
        if tv['traded']['count'] >= 10 and tv['watched']['count'] >= 10:
            traded_avg = tv['traded']['avg_30d'] or 0
            watched_avg = tv['watched']['avg_30d'] or 0

            if watched_avg > traded_avg and watched_avg - traded_avg > 0.05:
                recommendations.append(
                    f"⚠️ Watched stocks ({watched_avg*100:.1f}%) outperform traded stocks ({traded_avg*100:.1f}%) "
                    f"in 30-day avg return. Consider relaxing filters."
                )
            elif traded_avg > watched_avg and traded_avg - watched_avg > 0.05:
                recommendations.append(
                    f"✅ Traded stocks ({traded_avg*100:.1f}%) outperform watched stocks ({watched_avg*100:.1f}%) "
                    f"in 30-day avg return. Current filters are effective."
                )

            # Statistical significance
            if 't_test' in tv and tv['t_test']['significant']:
                recommendations.append(
                    f"📊 Traded/watched return difference is statistically significant (p={tv['t_test']['p_value']:.4f})"
                )

        # Trigger type analysis
        trigger_stats = self.analyze_by_trigger_type()
        if trigger_stats:
            # Best performing trigger
            best_trigger = max(
                trigger_stats.items(),
                key=lambda x: x[1]['avg_30d_return'] or -999
            )
            if best_trigger[1]['avg_30d_return'] and best_trigger[1]['count'] >= 5:
                recommendations.append(
                    f"🏆 Best trigger: '{best_trigger[0]}' "
                    f"(30d avg {best_trigger[1]['avg_30d_return']*100:.1f}%, "
                    f"win rate {best_trigger[1]['win_rate_30d']*100:.0f}%)"
                )

            # Triggers with strong watched performance
            for trigger, data in trigger_stats.items():
                watched_avg = data.get('watched_avg_30d')
                if watched_avg and watched_avg > 0.1 and len(data['_watched_returns_30d']) >= 3:
                    recommendations.append(
                        f"💡 '{trigger}' watched stocks gained {watched_avg*100:.1f}% on avg in 30 days. "
                        f"Consider relaxing filters for this trigger type."
                    )

        # Risk-reward threshold analysis
        rr_stats = self.analyze_rr_threshold_impact()
        if rr_stats:
            # Watched performance in 1.5~2.0 range
            for label in ['1.5~1.75', '1.75~2.0']:
                if label in rr_stats:
                    data = rr_stats[label]
                    if data['watched_count'] >= 5:
                        avg_ret = data['avg_watched_return']
                        win_rate = data['win_rate_watched']
                        if avg_ret and avg_ret > 0.05 and win_rate and win_rate > 0.5:
                            recommendations.append(
                                f"📈 RR {label} watched stocks: "
                                f"avg {avg_ret*100:.1f}%, win rate {win_rate*100:.0f}%. "
                                f"Potential gains if RR threshold is relaxed."
                            )

        # Data insufficiency warning
        total_completed = sum(
            1 for d in [tv['traded'], tv['watched']]
            for _ in range(d['count'])
        )
        if total_completed < 30:
            recommendations.append(
                f"⏳ Only {total_completed} completed tracking records. "
                f"Recommend accumulating at least 30 before making filter decisions."
            )

        return recommendations

    def generate_full_report(self, format: str = "text") -> str:
        """Generate full analysis report

        Args:
            format: "text" or "markdown"

        Returns:
            Report string
        """
        if format == "markdown":
            return self._generate_markdown_report()
        else:
            return self._generate_text_report()

    def _generate_text_report(self) -> str:
        """Generate text format report"""
        lines = []
        sep = "="*70

        lines.append(sep)
        lines.append("📊 PRISM-INSIGHT Trigger Performance Analysis Report")
        lines.append(f"Generated: {self.today}")
        lines.append(sep)
        lines.append("")

        # 1. Overview
        overview = self.get_overview_stats()
        lines.append("## 1. Overview")
        lines.append("-"*40)
        lines.append(f"  Total tracked: {overview['total']}")
        for status, count in overview['status_counts'].items():
            status_en = {'pending': 'Pending', 'in_progress': 'In Progress', 'completed': 'Completed'}.get(status, status)
            lines.append(f"    - {status_en}: {count} records")
        lines.append(f"  Traded: {overview['traded_counts'].get('traded', 0)} records")
        lines.append(f"  Watched: {overview['traded_counts'].get('watched', 0)} records")
        lines.append("")

        # 2. Traded vs Watched
        lines.append("## 2. Traded vs Watched Performance")
        lines.append("-"*40)
        tv = self.analyze_traded_vs_watched()
        for label, data in [('Traded', tv['traded']), ('Watched', tv['watched'])]:
            if data['count'] > 0:
                lines.append(f"  [{label}] (n={data['count']})")
                lines.append(f"    7d avg: {self._fmt_pct(data['avg_7d'])}")
                lines.append(f"    14d avg: {self._fmt_pct(data['avg_14d'])}")
                lines.append(f"    30d avg: {self._fmt_pct(data['avg_30d'])} (std: {self._fmt_pct(data['std_30d'])})")
                lines.append(f"    Win rate: {self._fmt_pct(data['win_rate'])}")
        if 't_test' in tv:
            sig = "Significant" if tv['t_test']['significant'] else "Not significant"
            lines.append(f"  [t-test] p-value={tv['t_test']['p_value']:.4f} ({sig})")
        lines.append("")

        # 3. By Trigger Type
        lines.append("## 3. Performance by Trigger Type")
        lines.append("-"*40)
        trigger_stats = self.analyze_by_trigger_type()
        if trigger_stats:
            # Header
            lines.append(f"{'Trigger':<25} {'Count':>6} {'Trade%':>8} {'30d Avg':>10} {'Win%':>8}")
            lines.append("-"*60)
            # Sort by 30d avg return
            sorted_triggers = sorted(
                trigger_stats.items(),
                key=lambda x: x[1]['avg_30d_return'] or -999,
                reverse=True
            )
            for trigger, data in sorted_triggers:
                lines.append(
                    f"{trigger:<25} {data['count']:>6} "
                    f"{self._fmt_pct(data['traded_rate']):>8} "
                    f"{self._fmt_pct(data['avg_30d_return']):>10} "
                    f"{self._fmt_pct(data['win_rate_30d']):>8}"
                )
        else:
            lines.append("  No data")
        lines.append("")

        # 4. By Risk-Reward Range
        lines.append("## 4. Performance by Risk-Reward Range")
        lines.append("-"*40)
        rr_stats = self.analyze_rr_threshold_impact()
        if rr_stats:
            lines.append(f"{'Range':<12} {'Total':>6} {'Trade':>6} {'Watch':>6} {'All Avg':>10} {'Watch Avg':>10}")
            lines.append("-"*55)
            for label, data in rr_stats.items():
                if data['total_count'] > 0:
                    lines.append(
                        f"{label:<12} {data['total_count']:>6} "
                        f"{data['traded_count']:>6} {data['watched_count']:>6} "
                        f"{self._fmt_pct(data['avg_all_return']):>10} "
                        f"{self._fmt_pct(data['avg_watched_return']):>10}"
                    )
        else:
            lines.append("  No data")
        lines.append("")

        # 5. Missed Opportunities
        lines.append("## 5. Missed Opportunities (Watched → 10%+ gain)")
        lines.append("-"*40)
        missed = self.get_missed_opportunities(5)
        if missed:
            for item in missed:
                lines.append(
                    f"  [{item['ticker']}] {item['company_name']} "
                    f"({item['trigger_type'] or 'unknown'})"
                )
                lines.append(
                    f"    {item['analyzed_price']:,.0f} → {item['tracked_30d_price']:,.0f} "
                    f"({self._fmt_pct(item['tracked_30d_return'])})"
                )
                lines.append(f"    Score: {item['buy_score']}/{item['min_score']}, RR: {item['risk_reward_ratio']:.2f}")
                lines.append(f"    Reason: {item['skip_reason']}")
        else:
            lines.append("  None")
        lines.append("")

        # 6. Avoided Losses
        lines.append("## 6. Avoided Losses (Watched → 10%+ drop)")
        lines.append("-"*40)
        avoided = self.get_avoided_losses(5)
        if avoided:
            for item in avoided:
                lines.append(
                    f"  [{item['ticker']}] {item['company_name']} "
                    f"({item['trigger_type'] or 'unknown'})"
                )
                lines.append(
                    f"    {item['analyzed_price']:,.0f} → {item['tracked_30d_price']:,.0f} "
                    f"({self._fmt_pct(item['tracked_30d_return'])})"
                )
                lines.append(f"    Score: {item['buy_score']}/{item['min_score']}, RR: {item['risk_reward_ratio']:.2f}")
        else:
            lines.append("  None")
        lines.append("")

        # 7. Recommendations
        lines.append("## 7. Data-Driven Recommendations")
        lines.append("-"*40)
        recommendations = self.generate_recommendations()
        if recommendations:
            for rec in recommendations:
                lines.append(f"  • {rec}")
        else:
            lines.append("  No recommendations (need more data)")
        lines.append("")

        lines.append(sep)
        lines.append("© PRISM-INSIGHT Investment Strategy Analysis System")
        lines.append(sep)

        return "\n".join(lines)

    def _generate_markdown_report(self) -> str:
        """Generate markdown format report"""
        lines = []

        lines.append("# 📊 PRISM-INSIGHT Trigger Performance Analysis Report")
        lines.append("")
        lines.append(f"**Generated**: {self.today}")
        lines.append("")

        # 1. Overview
        overview = self.get_overview_stats()
        lines.append("## 1. Overview")
        lines.append("")
        lines.append(f"- **Total tracked**: {overview['total']}")
        for status, count in overview['status_counts'].items():
            status_en = {'pending': 'Pending', 'in_progress': 'In Progress', 'completed': 'Completed'}.get(status, status)
            lines.append(f"  - {status_en}: {count} records")
        lines.append(f"- **Traded**: {overview['traded_counts'].get('traded', 0)} records")
        lines.append(f"- **Watched**: {overview['traded_counts'].get('watched', 0)} records")
        lines.append("")

        # 2. Traded vs Watched
        lines.append("## 2. Traded vs Watched Performance")
        lines.append("")
        tv = self.analyze_traded_vs_watched()
        lines.append("| Type | Count | 7d | 14d | 30d | Win Rate |")
        lines.append("|------|------|-----|------|------|------|")
        for label, data in [('Traded', tv['traded']), ('Watched', tv['watched'])]:
            lines.append(
                f"| {label} | {data['count']} | "
                f"{self._fmt_pct(data['avg_7d'])} | "
                f"{self._fmt_pct(data['avg_14d'])} | "
                f"{self._fmt_pct(data['avg_30d'])} | "
                f"{self._fmt_pct(data['win_rate'])} |"
            )
        if 't_test' in tv:
            sig = "✅ Significant" if tv['t_test']['significant'] else "❌ Not significant"
            lines.append("")
            lines.append(f"> **t-test**: p-value = {tv['t_test']['p_value']:.4f} ({sig})")
        lines.append("")

        # 3. By Trigger Type
        lines.append("## 3. Performance by Trigger Type")
        lines.append("")
        trigger_stats = self.analyze_by_trigger_type()
        if trigger_stats:
            lines.append("| Trigger Type | Count | Trade % | 30d Avg | Win Rate |")
            lines.append("|-------------|------|--------|-----------|------|")
            sorted_triggers = sorted(
                trigger_stats.items(),
                key=lambda x: x[1]['avg_30d_return'] or -999,
                reverse=True
            )
            for trigger, data in sorted_triggers:
                lines.append(
                    f"| {trigger} | {data['count']} | "
                    f"{self._fmt_pct(data['traded_rate'])} | "
                    f"{self._fmt_pct(data['avg_30d_return'])} | "
                    f"{self._fmt_pct(data['win_rate_30d'])} |"
                )
        else:
            lines.append("*No data*")
        lines.append("")

        # 4. By Risk-Reward Range
        lines.append("## 4. Performance by Risk-Reward Range")
        lines.append("")
        rr_stats = self.analyze_rr_threshold_impact()
        if rr_stats:
            lines.append("| RR Range | Total | Traded | Watched | All Avg | Watch Avg |")
            lines.append("|-------------|------|------|------|-----------|-----------|")
            for label, data in rr_stats.items():
                if data['total_count'] > 0:
                    lines.append(
                        f"| {label} | {data['total_count']} | "
                        f"{data['traded_count']} | {data['watched_count']} | "
                        f"{self._fmt_pct(data['avg_all_return'])} | "
                        f"{self._fmt_pct(data['avg_watched_return'])} |"
                    )
        else:
            lines.append("*No data*")
        lines.append("")

        # 5. Recommendations
        lines.append("## 5. Data-Driven Recommendations")
        lines.append("")
        recommendations = self.generate_recommendations()
        if recommendations:
            for rec in recommendations:
                lines.append(f"- {rec}")
        else:
            lines.append("*No recommendations (need more data)*")
        lines.append("")

        lines.append("---")
        lines.append("*© PRISM-INSIGHT Investment Strategy Analysis System*")

        return "\n".join(lines)

    # === Utility methods ===

    def _safe_mean(self, values: List[float]) -> Optional[float]:
        """Safe mean calculation"""
        if not values:
            return None
        return sum(values) / len(values)

    def _safe_std(self, values: List[float]) -> Optional[float]:
        """Safe standard deviation calculation"""
        if not values or len(values) < 2:
            return None
        mean = sum(values) / len(values)
        variance = sum((x - mean) ** 2 for x in values) / (len(values) - 1)
        return variance ** 0.5

    def _win_rate(self, returns: List[float]) -> Optional[float]:
        """Win rate calculation (ratio of returns > 0)"""
        if not returns:
            return None
        winners = sum(1 for r in returns if r > 0)
        return winners / len(returns)

    def _fmt_pct(self, value: Optional[float]) -> str:
        """Percentage formatting"""
        if value is None:
            return "N/A"
        return f"{value*100:+.1f}%"


def main():
    parser = argparse.ArgumentParser(
        description="Analysis Performance Deep Dive Report",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python performance_analysis_report.py                    # Text report
    python performance_analysis_report.py --format markdown  # Markdown report
    python performance_analysis_report.py --output report.md # Save to file
        """
    )
    parser.add_argument(
        "--format",
        choices=["text", "markdown"],
        default="text",
        help="Output format (default: text)"
    )
    parser.add_argument(
        "--output",
        type=str,
        help="File path to save report"
    )
    parser.add_argument(
        "--db",
        type=str,
        default=None,
        help="SQLite DB path (default: ./stock_tracking_db.sqlite)"
    )

    args = parser.parse_args()

    analyzer = PerformanceAnalyzer(db_path=args.db)
    report = analyzer.generate_full_report(format=args.format)

    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(report)
        print(f"Report saved: {args.output}")
    else:
        print(report)


if __name__ == "__main__":
    main()
