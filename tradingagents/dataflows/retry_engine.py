"""
Retry Engine for TradingAgents
===============================
Provides retry decorators with exponential backoff for data source calls.
Uses tenacity if available, falls back to a simple built-in retry.

Usage:
    @retry_api_call(max_retries=3, base_wait=2.0)
    def fetch_from_eodhd(symbol):
        ...
"""

import time
import logging
import functools
from typing import Optional, Tuple, Type

logger = logging.getLogger("tradingagents.retry")

# Try to import tenacity; fall back to built-in retry if not installed
try:
    from tenacity import (
        retry,
        stop_after_attempt,
        wait_exponential,
        retry_if_exception_type,
        before_sleep_log,
    )
    TENACITY_AVAILABLE = True
except ImportError:
    TENACITY_AVAILABLE = False
    logger.info("tenacity not installed. Using built-in retry logic.")


def retry_api_call(
    max_retries: int = 3,
    base_wait: float = 2.0,
    max_wait: float = 30.0,
    retry_on: Optional[Tuple[Type[Exception], ...]] = None,
):
    """
    Decorator that adds retry with exponential backoff to any function.
    
    Args:
        max_retries: Maximum number of retry attempts
        base_wait: Initial wait time in seconds
        max_wait: Maximum wait time in seconds
        retry_on: Tuple of exception types to retry on (default: all Exceptions)
    
    Usage:
        @retry_api_call(max_retries=3, base_wait=2.0)
        def fetch_stock_data(symbol):
            response = requests.get(...)
            return response.json()
    """
    if retry_on is None:
        retry_on = (Exception,)

    if TENACITY_AVAILABLE:
        # Use tenacity for robust retry
        def decorator(func):
            @retry(
                stop=stop_after_attempt(max_retries + 1),  # +1 because first attempt isn't a "retry"
                wait=wait_exponential(multiplier=base_wait, max=max_wait),
                retry=retry_if_exception_type(retry_on),
                before_sleep=before_sleep_log(logger, logging.WARNING),
                reraise=True,
            )
            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                return func(*args, **kwargs)
            return wrapper
        return decorator
    else:
        # Built-in simple retry (no external dependency)
        def decorator(func):
            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                last_exception = None
                for attempt in range(max_retries + 1):
                    try:
                        return func(*args, **kwargs)
                    except retry_on as e:
                        last_exception = e
                        if attempt < max_retries:
                            wait_time = min(base_wait * (2 ** attempt), max_wait)
                            logger.warning(
                                "Retry %d/%d for %s after error: %s (waiting %.1fs)",
                                attempt + 1, max_retries, func.__name__, e, wait_time
                            )
                            time.sleep(wait_time)
                        else:
                            logger.error(
                                "All %d retries exhausted for %s: %s",
                                max_retries, func.__name__, e
                            )
                raise last_exception
            return wrapper
        return decorator


def fetch_with_fallback(providers: list, method_name: str, *args, **kwargs):
    """
    Try multiple providers in order, return first successful result.
    
    Args:
        providers: List of (name, callable) tuples in priority order
        method_name: Human-readable name for logging
        *args, **kwargs: Arguments to pass to each provider
        
    Returns:
        Tuple of (result, source_name) or raises RuntimeError if all fail
    
    Usage:
        result, source = fetch_with_fallback(
            providers=[
                ("yfinance", lambda: yf_fetch(symbol, start, end)),
                ("eodhd", lambda: eodhd_fetch(symbol, start, end)),
            ],
            method_name="stock_data"
        )
    """
    errors = []
    
    for name, fetch_fn in providers:
        try:
            logger.info("Trying %s via '%s'...", method_name, name)
            start_time = time.time()
            result = fetch_fn()
            elapsed = (time.time() - start_time) * 1000
            logger.info("SUCCESS: %s via '%s' in %.0fms", method_name, name, elapsed)
            return result, name
        except Exception as e:
            logger.warning("FAILED: %s via '%s': %s", method_name, name, e)
            errors.append(f"{name}: {e}")
    
    error_msg = f"All providers failed for {method_name}: {'; '.join(errors)}"
    logger.error(error_msg)
    raise RuntimeError(error_msg)
