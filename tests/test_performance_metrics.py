"""
Tests for cores/performance_metrics.py timing and token tracking.
"""

import time
from cores.performance_metrics import AnalysisTimer, TokenUsageTracker


class TestAnalysisTimer:
    """Tests for AnalysisTimer"""

    def test_basic_timing(self):
        timer = AnalysisTimer()
        timer.start()
        time.sleep(0.1)
        timer.stop()
        assert timer.metrics.total_duration >= 0.1

    def test_section_timing(self):
        timer = AnalysisTimer()
        timer.start()

        with timer.section("fast_section"):
            time.sleep(0.05)

        with timer.section("slow_section"):
            time.sleep(0.1)

        timer.stop()

        assert "fast_section" in timer.metrics.section_timings
        assert "slow_section" in timer.metrics.section_timings
        assert timer.metrics.section_timings["slow_section"].duration_seconds >= 0.1

    def test_record_tokens(self):
        timer = AnalysisTimer()
        timer.record_tokens(input_tokens=1000, output_tokens=2000)
        timer.record_tokens(input_tokens=500, output_tokens=1000)
        assert timer.metrics.token_usage["total_input_tokens"] == 1500
        assert timer.metrics.token_usage["total_output_tokens"] == 3000

    def test_summary_output(self):
        timer = AnalysisTimer()
        timer.start()
        with timer.section("test"):
            pass
        timer.stop()
        summary = timer.metrics.summary()
        assert "Analysis Performance Metrics" in summary
        assert "test" in summary


class TestTokenUsageTracker:
    """Tests for TokenUsageTracker"""

    def test_record_and_totals(self):
        tracker = TokenUsageTracker(model="gpt-4o-mini")
        tracker.record("section1", input_tokens=1000, output_tokens=2000)
        tracker.record("section2", input_tokens=500, output_tokens=1000)
        assert tracker._total_input == 1500
        assert tracker._total_output == 3000

    def test_estimated_cost(self):
        tracker = TokenUsageTracker(model="gpt-4o-mini")
        tracker.record("test", input_tokens=1_000_000, output_tokens=1_000_000)
        cost = tracker.estimated_cost()
        # gpt-4o-mini: $0.15/M input + $0.60/M output = $0.75
        assert abs(cost - 0.75) < 0.01

    def test_summary_output(self):
        tracker = TokenUsageTracker()
        tracker.record("analysis", input_tokens=100, output_tokens=200)
        summary = tracker.summary()
        assert "Token Usage Summary" in summary
        assert "analysis" in summary
        assert "Estimated cost" in summary

    def test_unknown_model_uses_default_pricing(self):
        tracker = TokenUsageTracker(model="unknown-model")
        tracker.record("test", input_tokens=1_000_000, output_tokens=1_000_000)
        cost = tracker.estimated_cost()
        # Falls back to gpt-5.2 pricing: $5/M + $15/M = $20
        assert cost == 20.0
