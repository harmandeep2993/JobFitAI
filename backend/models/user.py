# models/user.py
"""
User persistence - create, lookup, and existence checks against the users table.
"""

import uuid
from datetime import datetime, timezone

from core import database as db


def create(email: str, hashed_password: str) -> dict:
    """Insert a new user and return the created row."""
    user_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO users (id, email, hashed_password, created_at) VALUES (?, ?, ?, ?)",
            (user_id, email.lower().strip(), hashed_password, now),
        )
    return {"id": user_id, "email": email.lower().strip(), "created_at": now}


def get_by_email(email: str) -> dict | None:
    """Return the user row for the given email, or None if not found."""
    with db.connect() as conn:
        row = conn.execute(
            "SELECT id, email, hashed_password, created_at FROM users WHERE email = ?",
            (email.lower().strip(),),
        ).fetchone()
    return dict(row) if row else None


def get_by_id(user_id: str) -> dict | None:
    """Return the user row for the given id, or None if not found."""
    with db.connect() as conn:
        row = conn.execute(
            "SELECT id, email, created_at FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    return dict(row) if row else None


def set_password(user_id: str, hashed_password: str) -> None:
    """Update the stored password hash for a user."""
    with db.connect() as conn:
        conn.execute(
            "UPDATE users SET hashed_password = ? WHERE id = ?",
            (hashed_password, user_id),
        )


def delete_account(user_id: str) -> None:
    """Erase every trace of a user (GDPR right to erasure).

    Removes the account and all data derived from it, in dependency order:
    cached analysis payloads (they hold resume-derived content), then every
    per-user table, then the user row itself. Resume FILES on disk are deleted
    by the caller before this runs - the file paths live in the resumes table,
    so they must be read while the rows still exist.

    The vector store is intentionally untouched: it holds embeddings of public
    job postings, never resume content.
    """
    with db.connect() as conn:
        # Cached analyses are keyed by hash, not user, so they are reachable
        # only through this user's analyses rows - delete them first.
        conn.execute(
            """DELETE FROM analysis_cache WHERE hash IN (
                   SELECT cache_hash FROM analyses
                   WHERE user_id = ? AND cache_hash IS NOT NULL
               )""",
            (user_id,),
        )
        for table in (
            "analyses",
            "resumes",
            "user_resume",
            "matches",
            "events",
            "seen_jobs",
            "user_settings",
        ):
            conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))


def email_exists(email: str) -> bool:
    """Return True if the email is already registered, False otherwise."""
    with db.connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM users WHERE email = ?",
            (email.lower().strip(),),
        ).fetchone()
    return row is not None
