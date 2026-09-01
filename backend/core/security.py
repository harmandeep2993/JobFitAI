# core/security.py
"""
Password hashing and JWT token utilities.

All auth logic routes through here - nothing else should import
jose or passlib directly.
"""

import os
import secrets
import sys
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from passlib.context import CryptContext

from core.logger import get_logger

logger = get_logger(__name__)

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Secret used to sign tokens. Must be set in .env for production.
# If missing, a random secret is generated per process - tokens cannot be
# forged but will be invalidated on every server restart.
_SECRET = os.getenv("JWT_SECRET") or secrets.token_hex(32)

_ALGORITHM = "HS256"
_TOKEN_EXPIRE_DAYS = 30


def validate_secret() -> None:
    """Enforce JWT_SECRET at startup.

    Without a fixed secret, tokens are valid only for the lifetime of the
    process - every restart logs all users out. Acceptable for local dev,
    fatal in production (APP_ENV=production refuses to boot without it).
    """
    if os.getenv("JWT_SECRET"):
        return
    if os.getenv("APP_ENV", "").strip().lower() == "production":
        logger.error("JWT_SECRET is required in production - set it in .env")
        sys.exit(1)
    logger.warning(
        "JWT_SECRET not set in .env - tokens will be invalidated on every restart. "
        "Set JWT_SECRET in .env for production."
    )


def hash_password(plain: str) -> str:
    """Return a bcrypt hash of plain. Store this, never the plain password."""
    return _pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Return True if plain matches the bcrypt hash, False otherwise."""
    return _pwd_context.verify(plain, hashed)


def create_token(user_id: str) -> str:
    """Create a signed HS256 JWT that expires in 30 days.

    The payload carries the user id only. A JWT is not encrypted - anyone
    holding it can read its claims - and the client stores it in
    localStorage, so no personal data (email, name) goes inside it. The
    server resolves the user from `sub`; clients that need the email call
    GET /api/auth/me over an authenticated request.

    Args:
        user_id: The user's UUID, stored in the `sub` claim.

    Returns:
        Encoded JWT string ready to send to the client.
    """
    expire = datetime.now(timezone.utc) + timedelta(days=_TOKEN_EXPIRE_DAYS)
    payload = {"sub": user_id, "exp": expire}
    return jwt.encode(payload, _SECRET, algorithm=_ALGORITHM)


def decode_token(token: str) -> dict | None:
    """Decode and verify a JWT.

    Args:
        token: The raw JWT string from the Authorization header.

    Returns:
        Payload dict on success, None if the token is invalid or expired.
    """
    try:
        return jwt.decode(token, _SECRET, algorithms=[_ALGORITHM])
    except JWTError:
        return None
