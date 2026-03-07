"""
Retry utilities for PRISM-INSIGHT.

Provides resilient data fetching with exponential backoff and jitter
for external API calls (KRX, LLM, web scraping).
"""

import time
import random
import logging
import functools
from typing import TypeVar, Callable, Optional, Type, Tuple

logger = logging.getLogger(__name__)

T = TypeVar("T")


def resilient_fetch(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    jitter: bool = True,
    retry_on: Tuple[Type[Exception], ...] = (Exception,),
    on_retry: Optional[Callable[[Exception, int], None]] = None,
):
    """
    Decorator for resilient data fetching with exponential backoff.

    Args:
        max_retries: Maximum number of retry attempts.
        base_delay: Initial delay between retries in seconds.
        max_delay: Maximum delay between retries in seconds.
        jitter: Whether to add random jitter to delay.
        retry_on: Tuple of exception types to retry on.
        on_retry: Optional callback called on each retry with (exception, attempt).

    Usage:
        @resilient_fetch(max_retries=3, base_delay=2.0)
        def fetch_stock_data(ticker):
            return api.get_ohlcv(ticker)

        @resilient_fetch(retry_on=(ConnectionError, TimeoutError))
        async def fetch_news(company):
            return await news_api.search(company)
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs) -> T:
            last_exception = None
            for attempt in range(1, max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except retry_on as e:
                    last_exception = e
                    if attempt == max_retries:
                        logger.error(
                            f"[{func.__name__}] Failed after {max_retries} attempts: {e}"
                        )
                        raise

                    delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
                    if jitter:
                        delay *= (0.5 + random.random())

                    logger.warning(
                        f"[{func.__name__}] Attempt {attempt}/{max_retries} failed: {e}. "
                        f"Retrying in {delay:.1f}s..."
                    )

                    if on_retry:
                        on_retry(e, attempt)

                    time.sleep(delay)

            raise last_exception  # Should not reach here

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs) -> T:
            import asyncio
            last_exception = None
            for attempt in range(1, max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except retry_on as e:
                    last_exception = e
                    if attempt == max_retries:
                        logger.error(
                            f"[{func.__name__}] Failed after {max_retries} attempts: {e}"
                        )
                        raise

                    delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
                    if jitter:
                        delay *= (0.5 + random.random())

                    logger.warning(
                        f"[{func.__name__}] Attempt {attempt}/{max_retries} failed: {e}. "
                        f"Retrying in {delay:.1f}s..."
                    )

                    if on_retry:
                        on_retry(e, attempt)

                    await asyncio.sleep(delay)

            raise last_exception  # Should not reach here

        # Return appropriate wrapper based on function type
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator


class CircuitBreaker:
    """
    Circuit breaker pattern for external service calls.

    Prevents repeated calls to a failing service by opening the circuit
    after a threshold of failures, allowing time for recovery.

    Usage:
        breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=60)

        @breaker
        def call_external_api():
            return api.fetch_data()
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        name: str = "default",
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.name = name
        self._failure_count = 0
        self._last_failure_time: Optional[float] = None
        self._state = "closed"  # closed, open, half-open

    @property
    def is_open(self) -> bool:
        if self._state == "open":
            if self._last_failure_time and (
                time.time() - self._last_failure_time > self.recovery_timeout
            ):
                self._state = "half-open"
                logger.info(f"Circuit breaker '{self.name}' entering half-open state")
                return False
            return True
        return False

    def record_success(self):
        self._failure_count = 0
        if self._state == "half-open":
            self._state = "closed"
            logger.info(f"Circuit breaker '{self.name}' closed (service recovered)")

    def record_failure(self, error: Exception):
        self._failure_count += 1
        self._last_failure_time = time.time()

        if self._failure_count >= self.failure_threshold:
            self._state = "open"
            logger.warning(
                f"Circuit breaker '{self.name}' OPEN after {self._failure_count} failures. "
                f"Will retry after {self.recovery_timeout}s"
            )

    def __call__(self, func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> T:
            if self.is_open:
                raise RuntimeError(
                    f"Circuit breaker '{self.name}' is open. "
                    f"Service unavailable, will retry after {self.recovery_timeout}s."
                )
            try:
                result = func(*args, **kwargs)
                self.record_success()
                return result
            except Exception as e:
                self.record_failure(e)
                raise

        return wrapper
