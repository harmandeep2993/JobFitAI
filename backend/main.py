# main.py
"""
Application entry point for JobsFitAI.

Responsibilities:
- Serve the static frontend (frontend/)
- Register all API routers
- Run background auto-fetch scheduler
- Start the FastAPI/uvicorn server
"""

import asyncio
import hmac as _hmac
import json as _json
import logging as _logging
import os
import secrets as _secrets
import socket
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Set

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.httpsredirect import HTTPSRedirectMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request

from core import database as db, state as session
from core.security import validate_secret
from core.config import (
    MAX_FILE_SIZE_MB,
    SEARCH_PER_TITLE,
    SUPPORTED_EXTENSIONS,
)
from core.logger import get_logger
from repositories import event_repo as event_store
from repositories import resume_repo as resume_store
from repositories import settings_repo as settings_store
from services.extractors.resume_extractor import extract_resume
from services.job_matcher import (
    begin_run,
    discover_and_score,
    end_run,
    fetch_combined,
    get_run_status,
)
from services.parsers import extract_all_text

logger = get_logger("main")

# === Scheduler state ===
# Imported from core.state so job_matches.py can reset a user's entry without
# creating a circular import (main imports job_matches router; job_matches
# cannot import main back).
_sched_last_ref = session.sched_last_ref

app = FastAPI(title="JobsFitAI")

# Gates every production-only hardening below: HTTPS redirect, HSTS, and the
# secure flag on the session cookie. Defined before any middleware reads it.
_IS_PRODUCTION = os.getenv("APP_ENV", "").strip().lower() == "production"

# Origins default to the local dev servers; production sets ALLOWED_ORIGINS
# in .env as a comma-separated list of real domains. Wildcard origins with
# credentials enabled would let any site ride authenticated sessions.
_DEFAULT_ORIGINS = "http://localhost:5173,http://localhost:8080"
_ALLOWED_ORIGINS = [
    o.strip()
    for o in os.getenv("ALLOWED_ORIGINS", _DEFAULT_ORIGINS).split(",")
    if o.strip()
]

# A wildcard here with allow_credentials would let any website on the internet
# issue authenticated requests as a logged-in user and read the response. Refuse
# to boot rather than serve that in production.
if _IS_PRODUCTION:
    if "*" in _ALLOWED_ORIGINS:
        logger.error("ALLOWED_ORIGINS must not be '*' in production")
        sys.exit(1)
    insecure = [o for o in _ALLOWED_ORIGINS if o.startswith("http://")]
    if insecure:
        logger.error("ALLOWED_ORIGINS must use https:// in production: %s", insecure)
        sys.exit(1)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# templates/ and assets/ live one level up, at the repo root (sibling of backend/).
_ROOT_DIR = Path(__file__).parent.parent

# Serve the React build (frontend/dist/) when it exists.
# During dev, Vite runs on port 5173 and proxies /api to this server.
_DIST = _ROOT_DIR / "frontend" / "dist"
if _DIST.exists():
    app.mount(
        "/assets", StaticFiles(directory=str(_DIST / "assets")), name="vite-assets"
    )


# === Transport security: HTTPS enforcement ===
# Data in transit is protected by TLS, which is terminated by the reverse proxy
# or hosting platform - the app cannot encrypt the wire itself. What the app CAN
# do is refuse to serve anything over plain HTTP in production and instruct
# browsers never to try again (HSTS), so a resume or password is never sent in
# clear text even if a user or a link starts on http://.
#
# The proxy tells us the original scheme via X-Forwarded-Proto. Run uvicorn with
# --proxy-headers (and --forwarded-allow-ips) so this is trustworthy.
if _IS_PRODUCTION:
    app.add_middleware(HTTPSRedirectMiddleware)


# === Body size cap + security headers middleware ===

# Reject request bodies larger than the biggest legitimate payload (a resume
# upload plus multipart overhead) before reading them into memory.
_MAX_BODY_BYTES = (MAX_FILE_SIZE_MB + 2) * 1024 * 1024

# Content Security Policy. The JWT lives in localStorage, so a single injected
# script could exfiltrate it: locking script-src to our own origin is the main
# defence against that. Google Fonts is the only third party the page loads.
_CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        # Tailwind and the React style props need inline styles; no inline scripts.
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
        "font-src 'self' data: https://fonts.gstatic.com",
        "img-src 'self' data: blob:",
        # Resume previews are rendered from blob: URLs in an iframe.
        "frame-src 'self' blob:",
        # The API is same-origin; no other host may receive fetch/XHR traffic.
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'self'",
    ]
)

# One year, and eligible for browser preload lists.
_HSTS = "max-age=31536000; includeSubDomains"


@app.middleware("http")
async def _body_limit_and_security_headers(request: Request, call_next):
    """Cap declared request body size and attach standard security headers."""
    if request.url.path.startswith("/api/"):
        length = request.headers.get("content-length", "")
        if length.isdigit() and int(length) > _MAX_BODY_BYTES:
            return JSONResponse(
                {"ok": False, "error": "payload_too_large"}, status_code=413
            )
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Content-Security-Policy", _CSP)
    response.headers.setdefault(
        "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
    )
    # Only meaningful over HTTPS, and sending it in dev would pin localhost to
    # https:// in the developer's browser for a year.
    if _IS_PRODUCTION:
        response.headers.setdefault("Strict-Transport-Security", _HSTS)
    return response


# === Request logging middleware ===

# High-frequency poll endpoints logged at DEBUG to avoid console spam.
_POLL_PATHS = {"/api/match/state", "/api/resumes", "/api/llm-ping"}


@app.middleware("http")
async def _request_logger(request: Request, call_next):
    """Log each /api/* and /health request with a short request ID and duration."""
    path = request.url.path
    if path.startswith("/api/") or path == "/health":
        req_id = uuid.uuid4().hex[:8]
        request.state.req_id = req_id
        method = request.method
        _log = logger.debug if path in _POLL_PATHS else logger.info
        _log("[%s] --> %s %s", req_id, method, path)
        t0 = time.perf_counter()
        response = await call_next(request)
        duration_ms = round((time.perf_counter() - t0) * 1000)
        _log(
            "[%s] <-- %s %s %s (%dms)",
            req_id,
            method,
            path,
            response.status_code,
            duration_ms,
        )
        return response
    return await call_next(request)


# === Auth middleware (opt-in: only active when APP_PASSWORD is set in .env) ===

_AUTH_ENABLED = bool(os.getenv("APP_PASSWORD", "").strip())
_AUTH_USER = os.getenv("APP_USERNAME", "admin").strip()
_AUTH_PASS = os.getenv("APP_PASSWORD", "").strip()
_SESSION_SECRET = os.getenv("SESSION_SECRET") or _secrets.token_hex(32)
_SESSION_COOKIE = "jfai_sess"
_LOGIN_HTML_PATH = _ROOT_DIR / "frontend" / "login-app-password.html"
# Only read the file when APP_PASSWORD auth is actually enabled; avoids a crash
# if the file is absent (which is the normal case when using JWT auth).
_LOGIN_HTML = (
    _LOGIN_HTML_PATH.read_text(encoding="utf-8")
    if _AUTH_ENABLED and _LOGIN_HTML_PATH.exists()
    else ""
)

_OPEN_PATHS = {"/login", "/logout"}


# Stateless session tokens: no database needed.
# Token = "username:HMAC(secret, username)" stored in a cookie.
# _make_token() creates one on login; _verify_token() checks it on every request.
# If SESSION_SECRET changes (or server restarts without a fixed secret), all existing
# tokens become invalid and users are logged out -- that is intentional.
def _make_token() -> str:
    sig = _hmac.new(_SESSION_SECRET.encode(), _AUTH_USER.encode(), "sha256").hexdigest()
    return f"{_AUTH_USER}:{sig}"


def _verify_token(token: str) -> bool:
    if not token:
        return False
    try:
        user, sig = token.rsplit(":", 1)
        expected = _hmac.new(
            _SESSION_SECRET.encode(), user.encode(), "sha256"
        ).hexdigest()
        return _hmac.compare_digest(sig, expected) and _hmac.compare_digest(
            user, _AUTH_USER
        )
    except Exception:
        return False


@app.middleware("http")
async def _auth_guard(request: Request, call_next):
    if not _AUTH_ENABLED:
        return await call_next(request)
    path = request.url.path
    if path in _OPEN_PATHS or path.startswith("/assets/"):
        return await call_next(request)
    if not _verify_token(request.cookies.get(_SESSION_COOKIE, "")):
        if path.startswith("/api/") or path == "/health":
            return JSONResponse(
                {"ok": False, "error": "unauthenticated"}, status_code=401
            )
        return RedirectResponse("/login", status_code=302)
    return await call_next(request)


@app.post("/login")
async def login_submit(request: Request):
    form = await request.form()
    username = (form.get("username") or "").strip()
    password = str(form.get("password") or "")
    ok = _AUTH_ENABLED and (
        _hmac.compare_digest(username, _AUTH_USER)
        and _hmac.compare_digest(password, _AUTH_PASS)
    )
    if not ok:
        return RedirectResponse("/login?error=1", status_code=302)
    resp = RedirectResponse("/", status_code=302)
    # secure=True outside local dev: without it the session cookie is sent
    # over plain HTTP and can be captured in transit.
    resp.set_cookie(
        _SESSION_COOKIE,
        _make_token(),
        httponly=True,
        secure=_IS_PRODUCTION,
        samesite="lax",
        max_age=60 * 60 * 24 * 30,
    )
    return resp


@app.get("/logout")
async def logout():
    resp = RedirectResponse("/login", status_code=302)
    resp.delete_cookie(_SESSION_COOKIE)
    return resp


# === Config constants ===

ALLOWED_EXTENSIONS: Set[str] = SUPPORTED_EXTENSIONS
MAX_FILE_MB: int = MAX_FILE_SIZE_MB


# === Frontend routes ===
# In production, serve the React build (frontend/dist/index.html) for all
# non-API routes so React Router handles client-side navigation.
# In dev, Vite serves the frontend on port 5173.


def _serve_spa() -> HTMLResponse:
    """Return the React SPA entry point from the production build."""
    index = _DIST / "index.html"
    if index.exists():
        return HTMLResponse(index.read_text(encoding="utf-8"))
    return HTMLResponse(
        "<h2>Frontend not built.</h2><p>Run <code>npm run build</code> in the frontend/ folder.</p>",
        status_code=503,
    )


@app.get("/")
async def index() -> HTMLResponse:
    return _serve_spa()


@app.get("/login")
async def login_page_react(error: str = "") -> HTMLResponse:
    if _AUTH_ENABLED:
        body = _LOGIN_HTML.replace(
            "{ERROR_HTML}",
            '<div class="lc-err">Invalid username or password.</div>' if error else "",
        )
        return HTMLResponse(body)
    return _serve_spa()


@app.get("/app")
async def app_page() -> HTMLResponse:
    return _serve_spa()


# === API routers ===

from api.routes import (  # noqa: E402
    auth,
    resume_analyzer,
    ats_maker,
    history,
    resume_improve,
    job_matches,
    resumes,
    settings,
)

app.include_router(auth.router, prefix="/api/auth")
app.include_router(resumes.router, prefix="/api/resumes")
app.include_router(resume_analyzer.router, prefix="/api")
app.include_router(job_matches.router, prefix="/api/match")
app.include_router(ats_maker.router, prefix="/api/ats")
app.include_router(resume_improve.router, prefix="/api")
app.include_router(history.router, prefix="/api")
app.include_router(settings.router, prefix="/api")


# Registered after the API routers so every /api path wins first. Serves the
# SPA for client-routed pages (/about, /pricing, /privacy, ...) so a hard
# refresh or direct link does not 404 in production.
@app.get("/{full_path:path}")
async def spa_catch_all(full_path: str):
    if full_path.startswith("api/"):
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
    return _serve_spa()


# === Background scheduler ===


async def _auto_fetch_loop() -> None:
    while True:
        await asyncio.sleep(30)
        try:
            user_ids = settings_store.get_users_with_scheduler_enabled()
            for uid in user_ids:
                interval = settings_store.get_scheduler_interval(uid) * 60
                last_run = _sched_last_ref.get(uid, 0.0)
                if last_run and (time.monotonic() - last_run) < interval:
                    continue
                if not session.has_resume(uid) or get_run_status(uid)["running"]:
                    continue
                # Claim the run slot before fetching so a manual fetch started
                # during this (slow, paginated) fetch is refused rather than
                # running a second scoring loop against the same status dict.
                if not begin_run(uid):
                    continue

                _sched_last_ref[uid] = time.monotonic()

                def _run(user_id=uid) -> dict:
                    titles = settings_store.get_titles(user_id)
                    entry_only = settings_store.get_entry_only(user_id)
                    jobs = fetch_combined(
                        titles,
                        location=settings_store.get_location(user_id),
                        countries=settings_store.get_countries(user_id),
                        per_title=SEARCH_PER_TITLE,
                        arbeitnow_limit=settings_store.get_arbeitnow_limit(user_id),
                        bundesagentur_limit=settings_store.get_bundesagentur_limit(
                            user_id
                        ),
                        entry_only=entry_only,
                        # Seen-stop paging: no job in the age window is skipped.
                        seen_ids=event_store.seen_ids(user_id),
                    )
                    return discover_and_score(
                        jobs,
                        user_id=user_id,
                        entry_only=entry_only,
                        titles=titles,
                    )

                try:
                    out = await run_in_threadpool(_run)
                    logger.info(
                        "[scheduler] user=%s: %d checked, %d scored",
                        uid,
                        out.get("checked", 0),
                        out.get("scored", 0),
                    )
                except Exception as run_err:
                    # fetch_combined can raise before discover_and_score's
                    # finally clears the flag; release the slot so the next
                    # tick (and manual fetches) are not blocked forever.
                    logger.error("[scheduler] run failed for user %s: %s", uid, run_err)
                    end_run(uid)
        except Exception as e:
            logger.error("[scheduler] error: %s", e)


async def _backfill_extractions() -> None:
    """Extract resume JSON for any stored resumes that were uploaded before caching was added."""
    # Backfill runs across all users - query without user filter
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT id, user_id, label, file_path FROM resumes WHERE extracted_json IS NULL"
        ).fetchall()

    if not rows:
        return
    logger.info("[backfill] extracting %d resume(s) without cached JSON", len(rows))

    def _run(r) -> None:
        try:
            text = extract_all_text(r["file_path"])
            if not text or len(text) < 50:
                return
            extracted = extract_resume(text)
            if extracted:
                resume_store.set_extracted(
                    r["user_id"], r["id"], _json.dumps(extracted)
                )
                logger.info("[backfill] done: %s (%s)", r["label"], r["id"])
        except Exception as e:
            logger.warning("[backfill] failed for %s: %s", r["id"], e)

    for r in rows:
        await run_in_threadpool(_run, r)


@app.on_event("startup")
async def _start_scheduler() -> None:
    """Wire up logging, seed scheduler state, and start background tasks."""
    # Uvicorn re-initialises its own loggers after our logger.py setup runs,
    # restoring the access log to INFO. Re-silence it here so requests are not
    # printed twice (our middleware already covers /api/* with richer context).
    _logging.getLogger("uvicorn.access").setLevel(_logging.WARNING)
    _logging.getLogger("uvicorn.access").propagate = False

    # Seed _sched_last_ref from the last run event per user so server restarts
    # don't trigger an immediate re-fetch when the interval hasn't elapsed yet.
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT user_id, MAX(created_at) as created_at FROM events "
            "WHERE type='run' GROUP BY user_id"
        ).fetchall()
    for row in rows:
        try:
            last_ts = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
            elapsed = (datetime.now(timezone.utc) - last_ts).total_seconds()
            _sched_last_ref[row["user_id"]] = time.monotonic() - elapsed
        except Exception:
            pass

    validate_secret()
    asyncio.create_task(_auto_fetch_loop())
    asyncio.create_task(_backfill_extractions())
    logger.info("[scheduler] loop started (per-user scheduler enabled)")


# === Entry point ===

PORT = 8080


def _port_in_use(port: int) -> bool:
    """Return True if something is already listening on the given port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


if __name__ == "__main__":
    import uvicorn

    if _port_in_use(PORT):
        logger.error(
            "Port %d is already in use - stop the old server first.\n"
            "  Windows:    taskkill /F /IM python.exe\n"
            "  macOS/Linux: kill $(lsof -ti tcp:%d)",
            PORT,
            PORT,
        )
        sys.exit(1)

    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=False)
