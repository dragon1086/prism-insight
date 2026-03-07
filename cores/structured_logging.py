"""
Structured Logging Module

Provides JSON-formatted structured logging with correlation IDs,
component tags, and configurable output for production debugging
and monitoring.
"""

import json
import logging
import uuid
import time
from typing import Optional


class StructuredFormatter(logging.Formatter):
    """JSON structured log formatter with correlation IDs and component tags."""

    def __init__(self, component: str = "prism-insight", correlation_id: Optional[str] = None):
        super().__init__()
        self.component = component
        self.correlation_id = correlation_id or str(uuid.uuid4())[:8]

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "component": self.component,
            "correlation_id": self.correlation_id,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add exception info if present
        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = {
                "type": record.exc_info[0].__name__,
                "message": str(record.exc_info[1]),
            }

        # Add extra fields
        for key in ("stock_code", "section", "duration_ms", "token_count"):
            if hasattr(record, key):
                log_entry[key] = getattr(record, key)

        return json.dumps(log_entry, ensure_ascii=False)


class CompactFormatter(logging.Formatter):
    """Compact human-readable formatter with correlation ID prefix."""

    def __init__(self, correlation_id: Optional[str] = None):
        self.correlation_id = correlation_id or str(uuid.uuid4())[:8]
        fmt = f"%(asctime)s [{self.correlation_id}] %(name)s %(levelname)s %(message)s"
        super().__init__(fmt=fmt)


def setup_structured_logging(
    level: int = logging.INFO,
    format: str = "json",
    correlation_id: Optional[str] = None,
    component: str = "prism-insight",
    log_file: Optional[str] = None,
) -> str:
    """
    Configure structured logging for the application.

    Args:
        level: Logging level (default: INFO)
        format: Log format - 'json' for structured JSON, 'compact' for human-readable
        correlation_id: Optional correlation ID (auto-generated if None)
        component: Component name for log entries
        log_file: Optional file path for log output

    Returns:
        The correlation_id being used (for passing to sub-processes)
    """
    cid = correlation_id or str(uuid.uuid4())[:8]

    # Choose formatter
    if format == "json":
        formatter = StructuredFormatter(component=component, correlation_id=cid)
    else:
        formatter = CompactFormatter(correlation_id=cid)

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # Optional file handler
    if log_file:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

    root_logger.info(f"Structured logging initialized (format={format}, correlation_id={cid})")
    return cid


class TimingLogger:
    """Context manager that logs execution time of a code block."""

    def __init__(self, logger: logging.Logger, operation: str, level: int = logging.INFO):
        self.logger = logger
        self.operation = operation
        self.level = level
        self._start = None

    def __enter__(self):
        self._start = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        duration_ms = (time.perf_counter() - self._start) * 1000
        extra = {"duration_ms": round(duration_ms, 2)}
        self.logger.log(
            self.level,
            f"{self.operation} completed in {duration_ms:.1f}ms",
            extra=extra,
        )
        return False
