"""Tests for API path traversal prevention.

Verifies that _safe_path_component and _resolve_safe reject traversal
attempts, and that the two vulnerable endpoints use them correctly.

Two test tiers:

1. **Helper-level** (TestSafePathComponent, TestResolveSafe) — pure unit tests
   against the validator functions. These cover URL-encoded payloads like
   ``%2e%2e%2f`` because the helpers receive *already-decoded* strings (FastAPI
   / Starlette decodes percent-encoding before the path parameter reaches the
   endpoint function).

2. **Endpoint-level** (TestEndpointPathSafety) — calls the async endpoint
   coroutines directly with ``asyncio.run()``. These are *not* full HTTP tests
   (no TestClient), so they exercise the code path *after* URL decoding. The
   helper-level tests cover the raw decoded values that would result from
   encoded traversal attempts.

3. **TestClient-level** (TestHTTPTraversal) — full HTTP round-trips through
   FastAPI's TestClient, which performs real percent-decoding. This proves that
   encoded payloads like ``%2e%2e%2f`` are decoded and then rejected.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from fastapi import HTTPException
from server.api_server import _safe_path_component, _resolve_safe


# ---------------------------------------------------------------------------
# _safe_path_component
# ---------------------------------------------------------------------------


class TestSafePathComponent:
    """Unit tests for the low-level path-component validator."""

    def test_clean_value_passes(self):
        assert _safe_path_component("COMI.CA") == "COMI.CA"

    def test_uuid_passes(self):
        val = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        assert _safe_path_component(val) == val

    def test_strips_whitespace(self):
        assert _safe_path_component("  COMI.CA  ") == "COMI.CA"

    @pytest.mark.parametrize("malicious", [
        "../etc/passwd",
        "..\\windows\\system32",
        "foo/../bar",
        "foo/bar",
        "foo\\bar",
        "/etc/passwd",
        "",
        "   ",
        "foo\x00bar",
    ])
    def test_rejects_traversal_and_invalid(self, malicious):
        with pytest.raises(HTTPException) as exc_info:
            _safe_path_component(malicious)
        assert exc_info.value.status_code == 400

    @pytest.mark.parametrize("decoded_payload", [
        # These are the strings that FastAPI hands to the endpoint after
        # percent-decoding encoded traversal attempts:
        #   %2e%2e%2f  ->  ../
        #   %2e%2e/    ->  ../
        #   ..%5c      ->  ..\
        "../",
        "..\\",
        "..%2f",   # partially encoded — still contains literal ".."
        "..%5c",   # partially encoded — still contains literal ".."
    ])
    def test_rejects_decoded_encoded_traversal(self, decoded_payload):
        """After URL decoding, encoded traversal payloads contain '..' or
        '/' or '\\' and must be rejected."""
        with pytest.raises(HTTPException) as exc_info:
            _safe_path_component(decoded_payload)
        assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# _resolve_safe
# ---------------------------------------------------------------------------


class TestResolveSafe:
    """Unit tests for the directory-containment guard."""

    def test_single_component_resolves_inside_base(self, tmp_path):
        result = _resolve_safe(tmp_path, "report.json")
        assert result == (tmp_path / "report.json").resolve()

    def test_multiple_components_resolve_inside_base(self, tmp_path):
        result = _resolve_safe(tmp_path, "COMI.CA", "audit_log.jsonl")
        assert result == (tmp_path / "COMI.CA" / "audit_log.jsonl").resolve()

    def test_rejects_dotdot(self, tmp_path):
        with pytest.raises(HTTPException):
            _resolve_safe(tmp_path, "..", "secret.txt")

    def test_rejects_slash_in_component(self, tmp_path):
        with pytest.raises(HTTPException):
            _resolve_safe(tmp_path, "a/b")

    def test_rejects_empty_component(self, tmp_path):
        with pytest.raises(HTTPException):
            _resolve_safe(tmp_path, "")


# ---------------------------------------------------------------------------
# Endpoint-level: direct coroutine calls (post-URL-decoding)
# ---------------------------------------------------------------------------


class TestEndpointPathSafety:
    """Call endpoint coroutines directly via asyncio.run().

    These test the code path *after* FastAPI has already percent-decoded the
    URL. For full HTTP decoding tests, see TestHTTPTraversal below.
    """

    @pytest.mark.parametrize("bad_ticker", [
        "../etc",
        "..%2F..%2Fetc",
        "COMI.CA/../../etc",
    ])
    def test_results_endpoint_rejects_traversal_ticker(self, bad_ticker):
        from server.api_server import get_result_detail
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(get_result_detail(ticker=bad_ticker, session_id="safe-id"))
        assert exc_info.value.status_code == 400

    @pytest.mark.parametrize("bad_session_id", [
        "../../../etc/passwd",
        "foo/../bar",
        "id/../../secret",
    ])
    def test_backtests_endpoint_rejects_traversal_session_id(self, bad_session_id):
        from server.api_server import get_backtest_detail
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(get_backtest_detail(session_id=bad_session_id))
        assert exc_info.value.status_code == 400

    def test_results_endpoint_valid_ticker_reaches_filesystem(self, tmp_path, monkeypatch):
        """A clean ticker + session_id passes validation and proceeds to the
        file-existence check (404), proving the guard doesn't block valid input."""
        from server.api_server import get_result_detail
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(get_result_detail(ticker="COMI.CA", session_id="abc123"))
        # 404 = passed validation, just no file on disk
        assert exc_info.value.status_code == 404

    def test_backtests_endpoint_valid_session_reaches_filesystem(self):
        """A clean session_id passes validation and hits 404 (no file)."""
        from server.api_server import get_backtest_detail
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(get_backtest_detail(session_id="abc123"))
        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# TestClient-level: full HTTP with real percent-decoding
# ---------------------------------------------------------------------------


class TestHTTPTraversal:
    """Full HTTP round-trips via FastAPI TestClient.

    Two defence layers are verified:

    1. **Starlette routing** — encoded forward slashes (``%2f``) are decoded
       before route matching, splitting the value into multiple path segments.
       The route pattern no longer matches, so the request gets a 404 from the
       router itself. The traversal payload never reaches our endpoint code.

    2. **_safe_path_component** — encoded backslashes (``%5c``) and literal
       ``..`` survive routing (backslash is not a URL path separator). These
       reach the endpoint and are caught by our validator → 400.

    Both layers prevent traversal; the tests document which layer fires.
    """

    @pytest.fixture(scope="class")
    def client(self):
        from fastapi.testclient import TestClient
        from server.api_server import app
        return TestClient(app, raise_server_exceptions=False)

    # -- Layer 2: backslash-based traversals reach the endpoint → 400 ----------

    @pytest.mark.parametrize("encoded_ticker", [
        "..%5cwindows",        # ..\ — backslash not a URL separator, reaches endpoint
        "..%5c..%5cetc",       # ..\..\ — double traversal via backslash
    ])
    def test_results_backslash_traversal_blocked_by_validator(self, client, encoded_ticker):
        resp = client.get(f"/api/results/{encoded_ticker}/session1")
        assert resp.status_code == 400

    @pytest.mark.parametrize("encoded_session", [
        "..%5csecret",         # ..\ — reaches endpoint
        "..%5c..%5cetc",       # ..\..\etc
    ])
    def test_backtests_backslash_traversal_blocked_by_validator(self, client, encoded_session):
        resp = client.get(f"/api/backtests/{encoded_session}")
        assert resp.status_code == 400

    # -- Layer 1: forward-slash traversals blocked by Starlette routing → 404 --

    @pytest.mark.parametrize("encoded_ticker", [
        "%2e%2e%2fetc",        # ../etc — decoded slash splits path segments
        "%2e%2e/etc",          # ../etc — literal slash
        "COMI.CA%2f..%2f..",   # COMI.CA/../.. — decoded slashes
    ])
    def test_results_slash_traversal_blocked_by_router(self, client, encoded_ticker):
        """Encoded forward slashes are decoded by Starlette before routing,
        creating extra path segments that don't match the route → 404."""
        resp = client.get(f"/api/results/{encoded_ticker}/session1")
        assert resp.status_code == 404

    @pytest.mark.parametrize("encoded_session", [
        "%2e%2e%2f%2e%2e%2fetc%2fpasswd",   # ../../etc/passwd
        "foo%2f..%2fbar",                     # foo/../bar
    ])
    def test_backtests_slash_traversal_blocked_by_router(self, client, encoded_session):
        """Same as above — Starlette routing rejects before endpoint runs."""
        resp = client.get(f"/api/backtests/{encoded_session}")
        assert resp.status_code == 404

    # -- Valid inputs pass through both layers ---------------------------------

    def test_results_valid_ticker_returns_404(self, client):
        """Valid path components pass the guard — 404 because no file exists."""
        resp = client.get("/api/results/COMI.CA/session123")
        assert resp.status_code == 404

    def test_backtests_valid_session_returns_404(self, client):
        resp = client.get("/api/backtests/session123")
        assert resp.status_code == 404
