"""
FastAPI Prometheus middleware for HTTP request metrics.

Records:
  - tradingagents_http_requests_total (method, endpoint, status_code)
  - tradingagents_http_request_duration_seconds (method, endpoint)

Endpoint path normalization:
  - UUIDs replaced with {id}
  - Known EGX ticker patterns replaced with {ticker}
  - Prevents label cardinality explosion from path parameters
"""
from __future__ import annotations

import re
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from .metrics import http_request_duration_seconds, http_requests_total

# Patterns for path normalization
_UUID_PATTERN = re.compile(r"[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}", re.I)
_HEX_ID_PATTERN = re.compile(r"/[0-9a-f]{16,}/")
_TICKER_PATTERN = re.compile(r"/[A-Z]{2,6}\.CA/", re.I)


def _normalize_path(path: str) -> str:
    """Normalize a URL path to prevent label cardinality explosion."""
    path = _UUID_PATTERN.sub("{id}", path)
    path = _HEX_ID_PATTERN.sub("/{id}/", path)
    path = _TICKER_PATTERN.sub("/{ticker}/", path)
    return path


class PrometheusMiddleware(BaseHTTPMiddleware):
    """Starlette middleware that records HTTP request metrics to Prometheus."""

    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.perf_counter()
        response = await call_next(request)
        elapsed = time.perf_counter() - start

        endpoint = _normalize_path(request.url.path)
        method = request.method

        http_requests_total.labels(
            method=method,
            endpoint=endpoint,
            status_code=str(response.status_code),
        ).inc()

        http_request_duration_seconds.labels(
            method=method,
            endpoint=endpoint,
        ).observe(elapsed)

        return response
