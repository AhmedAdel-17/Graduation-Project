"""
Firebase Authentication middleware for the FastAPI backend.

Verifies Firebase ID tokens from the Authorization header and injects
the authenticated user_id (Firebase UID) into the request state.

Setup:
  1. pip install firebase-admin
  2. Download your Firebase service account key from:
     Firebase Console → Project Settings → Service Accounts → Generate New Private Key
  3. Set the environment variable:
     GOOGLE_APPLICATION_CREDENTIALS=/path/to/serviceAccountKey.json
     OR set FIREBASE_SERVICE_ACCOUNT_JSON to the JSON content directly.

Usage:
  from server.firebase_auth import get_current_user, get_optional_user

  @app.get("/api/protected")
  async def protected(user_id: str = Depends(get_current_user)):
      ...

  @app.get("/api/public")
  async def public(user_id: str | None = Depends(get_optional_user)):
      ...
"""

import os
import json
import logging
from typing import Optional

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger("tradingagents.firebase_auth")

# ---------------------------------------------------------------------------
# Firebase Admin SDK initialization (lazy singleton)
# ---------------------------------------------------------------------------
_firebase_app = None
_init_attempted = False


def _ensure_firebase_admin():
    """Initialize Firebase Admin SDK once. Fail-open: if no credentials are
    configured, token verification is disabled and all requests fall through
    as user_id='local' (single-user demo mode)."""
    global _firebase_app, _init_attempted
    if _init_attempted:
        return _firebase_app
    _init_attempted = True

    try:
        import firebase_admin  # type: ignore
        from firebase_admin import credentials  # type: ignore
    except ImportError:
        logger.warning(
            "firebase-admin not installed — auth verification disabled. "
            "Install with: pip install firebase-admin"
        )
        return None

    # Already initialized by another module?
    try:
        _firebase_app = firebase_admin.get_app()
        logger.info("Firebase Admin SDK already initialized")
        return _firebase_app
    except ValueError:
        pass  # No default app — we need to initialize

    # Option 1: GOOGLE_APPLICATION_CREDENTIALS env var (standard Firebase path)
    cred_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if cred_path and os.path.isfile(cred_path):
        try:
            cred = credentials.Certificate(cred_path)
            _firebase_app = firebase_admin.initialize_app(cred)
            logger.info("Firebase Admin SDK initialized from %s", cred_path)
            return _firebase_app
        except Exception as exc:
            logger.error("Failed to init Firebase Admin from %s: %s", cred_path, exc)

    # Option 2: FIREBASE_SERVICE_ACCOUNT_JSON env var (JSON string — for Docker/CI)
    cred_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON")
    if cred_json:
        try:
            cred_dict = json.loads(cred_json)
            cred = credentials.Certificate(cred_dict)
            _firebase_app = firebase_admin.initialize_app(cred)
            logger.info("Firebase Admin SDK initialized from FIREBASE_SERVICE_ACCOUNT_JSON")
            return _firebase_app
        except Exception as exc:
            logger.error("Failed to init Firebase Admin from JSON env: %s", exc)

    # Option 3: Default credentials (GCE / Cloud Run / emulator)
    try:
        cred = credentials.ApplicationDefault()
        _firebase_app = firebase_admin.initialize_app(cred)
        logger.info("Firebase Admin SDK initialized with default credentials")
        return _firebase_app
    except Exception:
        pass

    logger.warning(
        "No Firebase credentials found — auth verification disabled. "
        "Set GOOGLE_APPLICATION_CREDENTIALS or FIREBASE_SERVICE_ACCOUNT_JSON."
    )
    return None


# ---------------------------------------------------------------------------
# Token verification
# ---------------------------------------------------------------------------
_bearer_scheme = HTTPBearer(auto_error=False)


def _verify_id_token(token: str) -> Optional[dict]:
    """Verify a Firebase ID token. Returns the decoded claims dict or None."""
    app = _ensure_firebase_admin()
    if app is None:
        return None  # No Firebase — can't verify

    try:
        from firebase_admin import auth as fb_auth  # type: ignore
        decoded = fb_auth.verify_id_token(token, app=app)
        return decoded
    except Exception as exc:
        logger.debug("Token verification failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------

async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> str:
    """Require authentication. Returns the Firebase UID.

    When Firebase Admin is not configured (no credentials), falls through
    to 'local' for single-user demo mode.
    """
    app = _ensure_firebase_admin()

    # No Firebase configured → demo mode
    if app is None:
        return "local"

    if not credentials or not credentials.credentials:
        raise HTTPException(status_code=401, detail="Missing authentication token")

    decoded = _verify_id_token(credentials.credentials)
    if decoded is None:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    return decoded["uid"]


async def get_optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> Optional[str]:
    """Optional authentication. Returns Firebase UID or None.

    When Firebase Admin is not configured, returns 'local'.
    """
    app = _ensure_firebase_admin()

    # No Firebase configured → demo mode
    if app is None:
        return "local"

    if not credentials or not credentials.credentials:
        return None

    decoded = _verify_id_token(credentials.credentials)
    return decoded["uid"] if decoded else None


# ---------------------------------------------------------------------------
# User upsert helper (call after successful auth to keep `users` table fresh)
# ---------------------------------------------------------------------------

def upsert_user(firebase_uid: str, email: Optional[str] = None,
                display_name: Optional[str] = None,
                photo_url: Optional[str] = None) -> None:
    """Upsert user into the Postgres `users` table. Best-effort — never raises."""
    try:
        from tradingagents.db import cursor as db_cursor, is_postgres_available
        if not is_postgres_available():
            return
        with db_cursor() as cur:
            cur.execute(
                """
                INSERT INTO users (firebase_uid, email, display_name, photo_url, last_seen_at)
                VALUES (%s, %s, %s, %s, NOW())
                ON CONFLICT (firebase_uid)
                DO UPDATE SET
                    email = COALESCE(EXCLUDED.email, users.email),
                    display_name = COALESCE(EXCLUDED.display_name, users.display_name),
                    photo_url = COALESCE(EXCLUDED.photo_url, users.photo_url),
                    last_seen_at = NOW()
                """,
                (firebase_uid, email, display_name, photo_url),
            )
    except Exception as exc:
        logger.debug("User upsert failed (non-fatal): %s", exc)
