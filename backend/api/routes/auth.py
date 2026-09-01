# api/routes/auth.py
"""
Auth endpoints: /api/auth/register, /api/auth/login, /api/auth/me

Protected routes read the current user via get_current_user() dependency.
"""

import hmac
import os
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from core import state as session
from core.logger import get_logger
from core.security import create_token, decode_token, hash_password, verify_password
from models import user as user_model
from repositories import resume_repo as resume_store
from schemas.auth import (
    ChangePasswordRequest,
    DeleteAccountRequest,
    LoginRequest,
    RegisterRequest,
)

logger = get_logger(__name__)

router = APIRouter()

_bearer = HTTPBearer()

# === Invite gate ===
# When INVITE_CODE is set in .env, registration requires it. Leave unset
# to allow open registration (local development).
_INVITE_CODE = os.getenv("INVITE_CODE", "").strip()


# === Admin role ===
# ADMIN_EMAILS in .env: comma-separated list of emails that get admin rights.
# Role is derived from the email at request time, so the same account is admin
# both locally and in production without any database flag or migration.
_ADMIN_EMAILS = {
    e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()
}


def is_admin_email(email: str) -> bool:
    """Return True if this email is configured as an admin in .env."""
    return (email or "").strip().lower() in _ADMIN_EMAILS


# === Rate limiting ===
# Sliding windows, in-memory: reset on restart and per-process, which is
# enough for a single-instance deployment.
# - Credential endpoints are limited per client IP to stop online brute force.
# - LLM-backed endpoints are limited per user so one account cannot burn the
#   provider budget by hammering analyse/optimise in a loop.
_RATE_LIMIT_ATTEMPTS = 5
_RATE_LIMIT_WINDOW_SECONDS = 60

_LLM_RATE_LIMIT_ATTEMPTS = 30
_LLM_RATE_LIMIT_WINDOW_SECONDS = 300

_attempts: dict[str, list[float]] = {}
_llm_attempts: dict[str, list[float]] = {}
_attempts_lock = threading.Lock()


def _sliding_window_check(
    store: dict[str, list[float]],
    key: str,
    limit: int,
    window_seconds: int,
    detail: str,
) -> None:
    """Record one attempt for key; raise 429 when the window budget is spent."""
    now = time.monotonic()
    with _attempts_lock:
        window = [t for t in store.get(key, []) if now - t < window_seconds]
        if len(window) >= limit:
            store[key] = window
            raise HTTPException(status_code=429, detail=detail)
        window.append(now)
        store[key] = window


def _check_rate_limit(request: Request) -> None:
    """Raise 429 if this client IP exceeded the attempt budget for the window."""
    ip = request.client.host if request.client else "unknown"
    _sliding_window_check(
        _attempts,
        ip,
        _RATE_LIMIT_ATTEMPTS,
        _RATE_LIMIT_WINDOW_SECONDS,
        "too_many_attempts_try_again_later",
    )


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
) -> dict:
    """
    FastAPI dependency - extracts and validates the Bearer token.
    Inject into any route that requires an authenticated user:

        @router.get("/protected")
        async def protected(user: dict = Depends(get_current_user)):
            ...user["id"], user["email"]...
    """
    payload = decode_token(credentials.credentials)
    if not payload:
        raise HTTPException(status_code=401, detail="invalid_or_expired_token")

    user = user_model.get_by_id(payload["sub"])
    if not user:
        raise HTTPException(status_code=401, detail="user_not_found")

    user["is_admin"] = is_admin_email(user["email"])
    return user


def get_current_user_llm_limited(user: dict = Depends(get_current_user)) -> dict:
    """get_current_user plus a per-user request budget for LLM-backed endpoints.

    Use on routes whose handler triggers paid LLM calls (analyse, ATS optimise,
    improve, recommend, score-jd). Raises 429 with detail 'rate_limited' when
    the user exceeds the budget.
    """
    _sliding_window_check(
        _llm_attempts,
        user["id"],
        _LLM_RATE_LIMIT_ATTEMPTS,
        _LLM_RATE_LIMIT_WINDOW_SECONDS,
        "rate_limited",
    )
    return user


def require_admin(user: dict = Depends(get_current_user)) -> dict:
    """Dependency for admin-only routes; 403 for everyone else."""
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="admin_required")
    return user


@router.post("/register")
async def register(body: RegisterRequest, request: Request) -> JSONResponse:
    _check_rate_limit(request)
    # Constant-time compare so response timing does not leak code prefixes.
    if _INVITE_CODE and not hmac.compare_digest(
        (body.invite_code or "").strip(), _INVITE_CODE
    ):
        raise HTTPException(status_code=403, detail="invalid_invite_code")
    if user_model.email_exists(body.email):
        raise HTTPException(status_code=409, detail="email_already_registered")

    hashed = hash_password(body.password)
    user = user_model.create(body.email, hashed)
    token = create_token(user["id"])

    return JSONResponse(
        {"token": token, "user_id": user["id"], "email": user["email"]}, status_code=201
    )


@router.post("/login")
async def login(body: LoginRequest, request: Request) -> JSONResponse:
    _check_rate_limit(request)
    user = user_model.get_by_email(body.email)
    if not user or not verify_password(body.password, user["hashed_password"]):
        raise HTTPException(status_code=401, detail="invalid_credentials")

    token = create_token(user["id"])
    return JSONResponse({"token": token, "user_id": user["id"], "email": user["email"]})


@router.post("/change-password")
async def change_password(
    body: ChangePasswordRequest,
    user: dict = Depends(get_current_user),
) -> JSONResponse:
    """Change the current user's password after verifying the old one."""
    full = user_model.get_by_email(user["email"])
    if not full or not verify_password(body.current_password, full["hashed_password"]):
        raise HTTPException(status_code=401, detail="wrong_current_password")

    user_model.set_password(user["id"], hash_password(body.new_password))
    return JSONResponse({"ok": True})


@router.delete("/account")
async def delete_account(
    body: DeleteAccountRequest,
    user: dict = Depends(get_current_user),
) -> JSONResponse:
    """Permanently erase the account and every piece of data derived from it.

    Requires the current password: a stolen token alone must not be able to
    destroy someone's account. Deletion is irreversible - resume files are
    removed from disk and all rows are hard-deleted, not flagged.
    """
    full = user_model.get_by_email(user["email"])
    if not full or not verify_password(body.password, full["hashed_password"]):
        raise HTTPException(status_code=401, detail="wrong_current_password")

    user_id = user["id"]

    # Delete resume files from disk BEFORE the rows that hold their paths.
    for record in resume_store.list_all(user_id):
        resume_store.delete(user_id, record["id"])

    # Drop the in-memory job-matching session so nothing survives in RAM.
    session.clear_user(user_id)

    user_model.delete_account(user_id)
    logger.info("Account erased for user %s", user_id)
    return JSONResponse({"ok": True})


@router.get("/me")
async def me(user: dict = Depends(get_current_user)) -> JSONResponse:
    return JSONResponse(
        {
            "user_id": user["id"],
            "email": user["email"],
            "created_at": user["created_at"],
            "is_admin": user["is_admin"],
        }
    )
