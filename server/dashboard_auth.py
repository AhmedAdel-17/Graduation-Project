"""
JWT auth helpers for the dashboard backend.

Hashing: bcrypt with default 12 rounds (~250ms per check on modern CPUs).
Tokens: HS256 signed JWT, 7-day expiry by default.

In production, set DASHBOARD_JWT_SECRET to a long random string and use HTTPS.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .dashboard_db import User, get_session

JWT_SECRET = os.environ.get(
    "DASHBOARD_JWT_SECRET",
    "dev-only-change-me-" + "x" * 40,  # weak default → must override in prod
)
JWT_ALG = "HS256"
TOKEN_EXPIRY_DAYS = int(os.environ.get("DASHBOARD_TOKEN_EXPIRY_DAYS", "90"))

bearer_scheme = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

def hash_password(plain: str) -> str:
    """bcrypt-hash a plaintext password. Returns string for DB storage."""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Compare plaintext to bcrypt hash. Returns False on any error."""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, AttributeError):
        return False


# ---------------------------------------------------------------------------
# JWT tokens
# ---------------------------------------------------------------------------

def create_access_token(user_id: str) -> str:
    """Mint a JWT for a user_id with TOKEN_EXPIRY_DAYS lifetime."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=TOKEN_EXPIRY_DAYS)).timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


def decode_token(token: str) -> Optional[str]:
    """Return user_id from a valid token, or None if invalid/expired."""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
        return payload.get("sub")
    except (jwt.InvalidTokenError, jwt.ExpiredSignatureError):
        return None


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------

def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: Session = Depends(get_session),
) -> User:
    """
    Resolve the current user from the Authorization: Bearer <token> header.
    Raises 401 if missing/invalid token or user not found.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = decode_token(credentials.credentials)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User no longer exists",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


def get_user_from_token(token: str, db: Session) -> Optional[User]:
    """Resolve a user from a raw token string (for WebSocket query-param auth)."""
    user_id = decode_token(token)
    if user_id is None:
        return None
    return db.query(User).filter(User.id == user_id).first()
