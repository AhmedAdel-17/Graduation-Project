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


_NO_RESULT = object()  # sentinel: distinguishes "no provider ran" from a falsy result


def fetch_with_fallback(providers: list, method_name: str, is_valid=None, *args, **kwargs):
    """
    Try multiple providers in order, return first SUCCESSFUL result.

    A provider counts as successful only if it neither raises NOR returns a
    result rejected by ``is_valid``. This is the crucial bit for the OHLCV
    chain: yfinance returns ``{"data": [], "error": ...}`` (an empty dict, NOT
    an exception) for thin / delisted EGX names. Without a validity check that
    empty dict was treated as "success", so the EODHD and local-CSV fallbacks
    NEVER fired — the whole fallback chain was dead for the most common failure
    mode. Passing ``is_valid`` makes an empty result fall through to the next
    provider.

    Args:
        providers: List of (name, callable) tuples in priority order
        method_name: Human-readable name for logging
        is_valid: Optional ``callable(result) -> bool``. When provided, a result
            for which it returns False is treated as a soft failure and the next
            provider is tried. When None, any non-exception result is accepted
            (legacy behaviour — preserved for the realtime/technical callers).
        *args, **kwargs: Arguments to pass to each provider

    Returns:
        Tuple of (result, source_name). If no provider produced a VALID result
        but at least one returned without raising, the best-effort last result
        is returned (so the caller still gets the most informative error
        payload). Raises RuntimeError only if every provider raised.
    """
    errors = []
    last_result = _NO_RESULT
    last_name = None

    for name, fetch_fn in providers:
        try:
            logger.info("Trying %s via '%s'...", method_name, name)
            start_time = time.time()
            result = fetch_fn()
            elapsed = (time.time() - start_time) * 1000

            if is_valid is not None and not is_valid(result):
                logger.warning(
                    "EMPTY/INVALID: %s via '%s' in %.0fms — falling through to next provider",
                    method_name, name, elapsed,
                )
                errors.append(f"{name}: empty/invalid result")
                last_result, last_name = result, name
                continue

            logger.info("SUCCESS: %s via '%s' in %.0fms", method_name, name, elapsed)
            return result, name
        except Exception as e:
            logger.warning("FAILED: %s via '%s': %s", method_name, name, e)
            errors.append(f"{name}: {e}")

    # No provider returned a VALID result. If one at least returned (empty)
    # without raising, hand that back so the caller sees the real error payload
    # rather than a generic RuntimeError.
    if last_result is not _NO_RESULT:
        logger.warning(
            "All providers returned empty/invalid for %s; returning best-effort from '%s'",
            method_name, last_name,
        )
        return last_result, last_name

    error_msg = f"All providers failed for {method_name}: {'; '.join(errors)}"
    logger.error(error_msg)
    raise RuntimeError(error_msg)
