"""
Performance metrics for PRISM-INSIGHT.

Provides timing and token usage tracking for analysis pipeline optimization.
"""

import time
import logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Dict

logger = logging.getLogger(__name__)


@dataclass
class SectionTiming:
    """Timing data for a single analysis section"""
    name: str
    start_time: float = 0.0
    end_time: float = 0.0
    duration_seconds: float = 0.0


@dataclass
class AnalysisMetrics:
    """Aggregated metrics for a complete analysis run"""
    total_start_time: float = 0.0
    total_end_time: float = 0.0
    section_timings: Dict[str, SectionTiming] = field(default_factory=dict)
    token_usage: Dict[str, int] = field(default_factory=lambda: {
        "total_input_tokens": 0,
        "total_output_tokens": 0,
    })

    @property
    def total_duration(self) -> float:
        if self.total_end_time and self.total_start_time:
            return self.total_end_time - self.total_start_time
        return 0.0

    def summary(self) -> str:
        """Generate a human-readable metrics summary"""
        lines = [
            "=" * 50,
            "📊 Analysis Performance Metrics",
            "=" * 50,
            f"Total Duration: {self.total_duration:.1f}s",
            "",
            "Section Breakdown:",
        ]

        # Sort sections by duration (longest first)
        sorted_sections = sorted(
            self.section_timings.values(),
            key=lambda s: s.duration_seconds,
            reverse=True,
        )

        for s in sorted_sections:
            pct = (s.duration_seconds / self.total_duration * 100) if self.total_duration > 0 else 0
            bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
            lines.append(f"  {s.name:<25} {s.duration_seconds:>6.1f}s  {bar} {pct:>5.1f}%")

        # Token usage
        total_input = self.token_usage.get("total_input_tokens", 0)
        total_output = self.token_usage.get("total_output_tokens", 0)
        if total_input > 0 or total_output > 0:
            lines.extend([
                "",
                "Token Usage:",
                f"  Input tokens:  {total_input:>10,}",
                f"  Output tokens: {total_output:>10,}",
                f"  Total tokens:  {total_input + total_output:>10,}",
            ])

        lines.append("=" * 50)
        return "\n".join(lines)


class AnalysisTimer:
    """
    Timer for tracking analysis pipeline performance.

    Usage:
        timer = AnalysisTimer()
        timer.start()

        with timer.section("price_volume"):
            await run_price_analysis()

        with timer.section("company_info"):
            await run_company_analysis()

        timer.stop()
        print(timer.metrics.summary())
    """

    def __init__(self):
        self.metrics = AnalysisMetrics()

    def start(self):
        """Mark the start of the full analysis pipeline"""
        self.metrics.total_start_time = time.time()
        logger.info("Analysis timer started")

    def stop(self):
        """Mark the end of the full analysis pipeline"""
        self.metrics.total_end_time = time.time()
        logger.info(f"Analysis completed in {self.metrics.total_duration:.1f}s")

    @contextmanager
    def section(self, name: str):
        """
        Context manager for timing a named section.

        Args:
            name: Section identifier (e.g., 'price_volume', 'charts')
        """
        timing = SectionTiming(name=name, start_time=time.time())
        logger.debug(f"Section '{name}' started")

        try:
            yield timing
        finally:
            timing.end_time = time.time()
            timing.duration_seconds = timing.end_time - timing.start_time
            self.metrics.section_timings[name] = timing
            logger.debug(f"Section '{name}' completed in {timing.duration_seconds:.1f}s")

    def record_tokens(self, input_tokens: int = 0, output_tokens: int = 0):
        """
        Record token usage from an LLM call.

        Args:
            input_tokens: Number of input/prompt tokens.
            output_tokens: Number of output/completion tokens.
        """
        self.metrics.token_usage["total_input_tokens"] += input_tokens
        self.metrics.token_usage["total_output_tokens"] += output_tokens


class TokenUsageTracker:
    """
    Tracks cumulative LLM token usage across an analysis run.

    Usage:
        tracker = TokenUsageTracker()
        tracker.record("price_analysis", input_tokens=1500, output_tokens=3000)
        tracker.record("news_analysis", input_tokens=2000, output_tokens=5000)
        print(tracker.summary())
    """

    # Approximate GPT pricing per 1M tokens (USD)
    PRICING = {
        "gpt-4o": {"input": 2.50, "output": 10.00},
        "gpt-4o-mini": {"input": 0.15, "output": 0.60},
        "gpt-5.2": {"input": 5.00, "output": 15.00},  # Estimated
    }

    def __init__(self, model: str = "gpt-5.2"):
        self.model = model
        self.records: Dict[str, Dict[str, int]] = {}
        self._total_input = 0
        self._total_output = 0

    def record(self, section: str, input_tokens: int = 0, output_tokens: int = 0):
        """Record token usage for a section"""
        self.records[section] = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }
        self._total_input += input_tokens
        self._total_output += output_tokens

    def estimated_cost(self) -> float:
        """Calculate estimated cost in USD"""
        pricing = self.PRICING.get(self.model, self.PRICING["gpt-5.2"])
        input_cost = (self._total_input / 1_000_000) * pricing["input"]
        output_cost = (self._total_output / 1_000_000) * pricing["output"]
        return input_cost + output_cost

    def summary(self) -> str:
        """Generate a token usage summary"""
        lines = [
            f"💰 Token Usage Summary (model: {self.model})",
            f"{'Section':<25} {'Input':>10} {'Output':>10} {'Total':>10}",
            "-" * 57,
        ]

        for section, data in self.records.items():
            inp = data["input_tokens"]
            out = data["output_tokens"]
            lines.append(f"{section:<25} {inp:>10,} {out:>10,} {inp + out:>10,}")

        lines.extend([
            "-" * 57,
            f"{'TOTAL':<25} {self._total_input:>10,} {self._total_output:>10,} {self._total_input + self._total_output:>10,}",
            f"\nEstimated cost: ${self.estimated_cost():.4f}",
        ])

        return "\n".join(lines)
