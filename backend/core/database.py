# core/database.py
"""
Database connection for JobsFitAI.

Uses Turso (cloud SQLite) when TURSO_DATABASE_URL + TURSO_AUTH_TOKEN are set,
falls back to local SQLite for development. Both paths expose the same
connect() context manager so no other file needs to know which backend is active.
"""

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from core.logger import get_logger

logger = get_logger(__name__)

# === Connection mode ===

_TURSO_URL = (os.getenv("TURSO_DATABASE_URL") or os.getenv("TURSO_URL", "")).strip()
# Force HTTP mode - libsql:// uses WebSocket which can be blocked; https:// uses HTTP pipeline
_TURSO_URL = _TURSO_URL.replace("libsql://", "https://")
_TURSO_TOKEN = os.getenv("TURSO_AUTH_TOKEN", "").strip()
_USE_TURSO = bool(_TURSO_URL and _TURSO_TOKEN)

# libsql_client is only required when Turso is configured. Import it now so
# the missing-package error surfaces at startup rather than on the first request.
try:
    from libsql_client import create_client_sync as _create_client_sync
except ImportError:
    _create_client_sync = None  # type: ignore[assignment]

DB_PATH = Path("data/jobsfitai.db")


# === Turso row/cursor/connection wrappers ===
# libsql_client has a different API from sqlite3. These thin wrappers make
# Turso connections look identical to sqlite3 connections so callers never
# need to know which backend they are talking to.


class _Row:
    """Dict-like row that supports both key and index access."""

    def __init__(self, data: dict):
        self._data = data

    def __getitem__(self, key):
        return self._data[key]

    def keys(self):
        """Return column names."""
        return self._data.keys()

    def get(self, key, default=None):
        """Return value for key, or default if missing."""
        return self._data.get(key, default)


class _Cursor:
    """Wraps a libsql_client ResultSet to match the sqlite3 cursor API."""

    def __init__(self, result_set):
        columns = list(result_set.columns)
        self._rows = [_Row(dict(zip(columns, r))) for r in result_set.rows]

    def fetchone(self):
        """Return first row or None."""
        return self._rows[0] if self._rows else None

    def fetchall(self):
        """Return all rows."""
        return self._rows

    def __iter__(self):
        return iter(self._rows)


class _TursoConn:
    """Wraps libsql_client ClientSync to match the sqlite3 connection API."""

    def __init__(self, client):
        self._client = client

    def execute(self, sql: str, params: tuple = ()) -> _Cursor:
        """Execute sql with params and return a cursor-like object."""
        rs = self._client.execute(sql, list(params))
        return _Cursor(rs)

    def execute_batch(self, statements: list[str]) -> None:
        """Send multiple parameter-free statements in a single round-trip.

        Used by init() so schema creation doesn't cost N Turso HTTP calls.
        """
        self._client.batch(statements)

    def commit(self):
        """No-op - libsql auto-commits each statement."""

    def close(self):
        """Close the underlying client."""
        self._client.close()


# === connect() context manager ===


@contextmanager
def connect():
    """Yield a database connection; commit on success, always close.

    Returns a Turso connection when TURSO_DATABASE_URL and TURSO_AUTH_TOKEN
    are set, otherwise a local SQLite connection. Both behave identically.
    """
    if _USE_TURSO:
        if _create_client_sync is None:
            raise ImportError(
                "libsql-client is required for Turso. Install it: uv add libsql-client"
            )
        client = _create_client_sync(url=_TURSO_URL, auth_token=_TURSO_TOKEN)
        conn = _TursoConn(client)
        try:
            yield conn
        finally:
            conn.close()
    else:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


# === Schema init ===


# All CREATE TABLE statements batched so Turso pays one HTTP round-trip at startup.
_SCHEMA_STMTS = [
    """CREATE TABLE IF NOT EXISTS users (
        id              TEXT PRIMARY KEY,
        email           TEXT UNIQUE NOT NULL,
        hashed_password TEXT NOT NULL,
        created_at      TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS matches (
        id               TEXT PRIMARY KEY,
        user_id          TEXT NOT NULL DEFAULT 'local',
        source           TEXT,
        title            TEXT,
        company          TEXT,
        location         TEXT,
        url              TEXT,
        language         TEXT,
        posted_at        TEXT,
        score            REAL,
        label            TEXT,
        matched_required TEXT,
        missing_required TEXT,
        scored_at        TEXT,
        applied          INTEGER DEFAULT 0,
        jd_json          TEXT,
        section_scores   TEXT,
        summary          TEXT,
        status           TEXT DEFAULT 'scored',
        app_status       TEXT DEFAULT '',
        deleted          INTEGER DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS seen_jobs (
        id         TEXT PRIMARY KEY,
        user_id    TEXT NOT NULL DEFAULT 'local',
        source     TEXT,
        title      TEXT,
        first_seen TEXT,
        decision   TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS events (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    TEXT NOT NULL DEFAULT 'local',
        type       TEXT,
        job_id     TEXT,
        detail     TEXT,
        created_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS resumes (
        id             TEXT PRIMARY KEY,
        user_id        TEXT NOT NULL DEFAULT 'local',
        slot           INTEGER NOT NULL DEFAULT 0,
        label          TEXT NOT NULL DEFAULT 'Base Resume',
        original_name  TEXT NOT NULL,
        file_path      TEXT NOT NULL,
        mime_type      TEXT NOT NULL,
        file_size_kb   REAL NOT NULL DEFAULT 0,
        uploaded_at    TEXT NOT NULL,
        extracted_json TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS analyses (
        id         TEXT PRIMARY KEY,
        user_id    TEXT NOT NULL DEFAULT 'local',
        resume_id  TEXT NOT NULL,
        jd_snippet TEXT,
        score      REAL,
        label      TEXT,
        cache_hash TEXT,
        scored_at  TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS analysis_cache (
        hash        TEXT PRIMARY KEY,
        result_json TEXT NOT NULL,
        created_at  TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS user_settings (
        user_id TEXT NOT NULL,
        key     TEXT NOT NULL,
        value   TEXT,
        PRIMARY KEY (user_id, key)
    )""",
    """CREATE TABLE IF NOT EXISTS user_resume (
        user_id    TEXT PRIMARY KEY,
        name       TEXT,
        json       TEXT,
        created_at TEXT
    )""",
    # Legacy singleton table kept for backward compat with older resume sessions
    """CREATE TABLE IF NOT EXISTS resume (
        id         INTEGER PRIMARY KEY CHECK (id = 1),
        name       TEXT,
        json       TEXT,
        created_at TEXT
    )""",
    "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)",
]

# Migrations that add columns to existing databases.
# Each statement is attempted individually; "duplicate column" errors are expected and ignored.
_MIGRATION_STMTS = [
    "ALTER TABLE matches   ADD COLUMN user_id TEXT NOT NULL DEFAULT 'local'",
    "ALTER TABLE events    ADD COLUMN user_id TEXT NOT NULL DEFAULT 'local'",
    "ALTER TABLE seen_jobs ADD COLUMN user_id TEXT NOT NULL DEFAULT 'local'",
    "ALTER TABLE analyses  ADD COLUMN user_id TEXT NOT NULL DEFAULT 'local'",
    "ALTER TABLE analyses  ADD COLUMN cache_hash TEXT",
    # Soft-delete flag on matches so a removed job can be restored (undo)
    "ALTER TABLE matches   ADD COLUMN deleted INTEGER DEFAULT 0",
    # Claim pre-migration rows for their owner via the resume they belong to
    """UPDATE analyses SET user_id = COALESCE(
        (SELECT r.user_id FROM resumes r WHERE r.id = analyses.resume_id), user_id
    ) WHERE user_id = 'local'""",
]


def init() -> None:
    """Create all tables and run column migrations. Safe to call multiple times."""
    with connect() as conn:
        if _USE_TURSO and hasattr(conn, "execute_batch"):
            # Single HTTP round-trip for all CREATE TABLE statements on Turso
            conn.execute_batch(_SCHEMA_STMTS)
        else:
            for stmt in _SCHEMA_STMTS:
                conn.execute(stmt)

        for stmt in _MIGRATION_STMTS:
            try:
                conn.execute(stmt)
            except Exception as _e:
                logger.debug("migration skipped (already applied?): %s", _e)

    # Log the backend, not the endpoint: the Turso URL identifies the live
    # database host and does not belong in logs or crash reports.
    mode = "Turso (cloud)" if _USE_TURSO else f"SQLite ({DB_PATH})"
    logger.info("Database ready - %s", mode)


init()
