"""
Health check module for PRISM-INSIGHT.

Validates system prerequisites before running analysis:
- Environment variables and API keys
- Network connectivity to required services
- Disk space for chart/report output
"""

import os
import logging
from dataclasses import dataclass, field
from typing import List

logger = logging.getLogger(__name__)


@dataclass
class HealthCheckResult:
    """Result of a system health check"""
    passed: bool = True
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    details: dict = field(default_factory=dict)

    def add_error(self, message: str):
        self.errors.append(message)
        self.passed = False

    def add_warning(self, message: str):
        self.warnings.append(message)

    def summary(self) -> str:
        if self.passed and not self.warnings:
            return "✅ All health checks passed"
        lines = []
        if self.errors:
            lines.append(f"❌ {len(self.errors)} error(s):")
            for e in self.errors:
                lines.append(f"   - {e}")
        if self.warnings:
            lines.append(f"⚠️ {len(self.warnings)} warning(s):")
            for w in self.warnings:
                lines.append(f"   - {w}")
        return "\n".join(lines)


# Required environment variables and their descriptions
REQUIRED_ENV_VARS = {
    "OPENAI_API_KEY": "OpenAI API key for LLM analysis",
}

OPTIONAL_ENV_VARS = {
    "PERPLEXITY_API_KEY": "Perplexity API key for news/sector analysis",
    "FIRECRAWL_API_KEY": "Firecrawl API key for web scraping",
    "PRISM_LANGUAGE": "Language setting (ko/en, defaults to ko)",
}


def validate_env_config() -> HealthCheckResult:
    """
    Validate that all required environment variables are set.

    Returns:
        HealthCheckResult with details about missing/present env vars.
    """
    result = HealthCheckResult()

    # Check required vars
    for var, description in REQUIRED_ENV_VARS.items():
        value = os.environ.get(var, "")
        if not value or value in ("your-api-key", "YOUR_API_KEY", "example key", ""):
            result.add_error(
                f"Missing required env var: {var} ({description}). "
                f"Set it in your .env file or environment."
            )
        else:
            result.details[var] = "configured"

    # Check optional vars
    for var, description in OPTIONAL_ENV_VARS.items():
        value = os.environ.get(var, "")
        if not value:
            result.add_warning(f"Optional env var not set: {var} ({description})")
        else:
            result.details[var] = "configured"

    return result


def check_disk_space(output_dir: str = ".", min_mb: int = 100) -> HealthCheckResult:
    """
    Check that sufficient disk space is available for output.

    Args:
        output_dir: Directory where reports will be saved.
        min_mb: Minimum required free space in MB.

    Returns:
        HealthCheckResult with disk space details.
    """
    import shutil

    result = HealthCheckResult()
    try:
        total, used, free = shutil.disk_usage(output_dir)
        free_mb = free // (1024 * 1024)
        result.details["free_disk_mb"] = free_mb
        if free_mb < min_mb:
            result.add_error(
                f"Insufficient disk space: {free_mb}MB free, {min_mb}MB required"
            )
        else:
            logger.debug(f"Disk space OK: {free_mb}MB free")
    except Exception as e:
        result.add_warning(f"Could not check disk space: {e}")

    return result


async def check_llm_connectivity() -> HealthCheckResult:
    """
    Verify LLM API connectivity with a minimal test call.

    Returns:
        HealthCheckResult with API connectivity status.
    """
    result = HealthCheckResult()
    try:
        import openai
        client = openai.AsyncOpenAI()
        # Minimal API call to verify connectivity
        await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=5,
        )
        result.details["llm_api"] = "connected"
        logger.debug("LLM API connectivity: OK")
    except Exception as e:
        result.add_error(f"LLM API unreachable: {e}")

    return result


def run_health_check(
    check_env: bool = True,
    check_disk: bool = True,
    output_dir: str = ".",
) -> HealthCheckResult:
    """
    Run all synchronous health checks.

    Args:
        check_env: Whether to validate environment configuration.
        check_disk: Whether to check disk space.
        output_dir: Output directory for disk check.

    Returns:
        Combined HealthCheckResult.
    """
    combined = HealthCheckResult()

    if check_env:
        env_result = validate_env_config()
        combined.errors.extend(env_result.errors)
        combined.warnings.extend(env_result.warnings)
        combined.details.update(env_result.details)

    if check_disk:
        disk_result = check_disk_space(output_dir)
        combined.errors.extend(disk_result.errors)
        combined.warnings.extend(disk_result.warnings)
        combined.details.update(disk_result.details)

    combined.passed = len(combined.errors) == 0

    if combined.passed:
        logger.info("Health check passed")
    else:
        logger.error(f"Health check failed:\n{combined.summary()}")

    return combined
