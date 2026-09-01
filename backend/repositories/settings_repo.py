# repositories/settings_repo.py
"""
Per-user search settings, persisted in SQLite so they survive restarts.

Settings are stored in the `user_settings` table keyed by (user_id, key).
Each public function takes user_id as its first argument so every user's
preferences are fully isolated.
"""

import json

from core import database as db
from core.config import (
    AUTO_FETCH_MINUTES,
    SEARCH_COUNTRIES,
    SEARCH_COUNTRY,
    TARGET_TITLES,
)
from core.logger import get_logger

logger = get_logger(__name__)

# Country name -> Adzuna country code (Adzuna has per-country endpoints).
COUNTRY_CODES = {
    "germany": "de",
    "deutschland": "de",
    "de": "de",
    "netherlands": "nl",
    "holland": "nl",
    "nl": "nl",
    "belgium": "be",
    "belgique": "be",
    "be": "be",
    "austria": "at",
    "osterreich": "at",
    "at": "at",
    "switzerland": "ch",
    "schweiz": "ch",
    "ch": "ch",
    "france": "fr",
    "fr": "fr",
    "united kingdom": "gb",
    "uk": "gb",
    "gb": "gb",
    "england": "gb",
    "spain": "es",
    "es": "es",
    "italy": "it",
    "it": "it",
    "poland": "pl",
    "pl": "pl",
    "ireland": "ie",
    "ie": "ie",
}
_CODE_TO_NAME = {
    "de": "germany",
    "nl": "netherlands",
    "be": "belgium",
    "at": "austria",
    "ch": "switzerland",
    "fr": "france",
    "gb": "uk",
    "es": "spain",
    "it": "italy",
    "pl": "poland",
    "ie": "ireland",
}


def _get(user_id: str, key: str, default):
    """Read one setting for a user, returning default if not set or unparseable."""
    with db.connect() as conn:
        r = conn.execute(
            "SELECT value FROM user_settings WHERE user_id = ? AND key = ?",
            (user_id, key),
        ).fetchone()
    if r and r["value"]:
        try:
            return json.loads(r["value"])
        except (ValueError, TypeError):
            pass
    return default


def _set(user_id: str, key: str, value) -> None:
    """Write one setting for a user, upserting on conflict."""
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO user_settings (user_id, key, value) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value",
            (user_id, key, json.dumps(value)),
        )


# === Target titles ===


def get_titles(user_id: str) -> list[str]:
    """Return the list of target job titles for this user."""
    return _get(user_id, "target_titles", list(TARGET_TITLES))


def set_titles(user_id: str, titles: list[str]) -> None:
    """Persist deduplicated, lowercased target titles for this user."""
    cleaned, seen = [], set()
    for t in titles:
        t = (t or "").strip().lower()
        if t and t not in seen:
            seen.add(t)
            cleaned.append(t)
    _set(user_id, "target_titles", cleaned)
    logger.info("Target titles updated for user %s: %d", user_id, len(cleaned))


# === Countries (codes) / location ===


def get_countries(user_id: str) -> list[str]:
    """Return Adzuna country codes for this user."""
    return _get(user_id, "countries", list(SEARCH_COUNTRIES))


def set_countries(user_id: str, names: list[str]) -> None:
    """Convert country names to Adzuna codes and persist for this user."""
    codes = []
    for n in names:
        code = COUNTRY_CODES.get((n or "").strip().lower())
        if code and code not in codes:
            codes.append(code)
    _set(user_id, "countries", codes or [SEARCH_COUNTRY])
    logger.info("Countries updated for user %s: %s", user_id, codes or [SEARCH_COUNTRY])


def country_names(user_id: str) -> list[str]:
    """Return this user's country codes as display names for the UI."""
    return [_CODE_TO_NAME.get(c, c) for c in get_countries(user_id)]


def get_location(user_id: str) -> str:
    """Return the city/region filter for this user (empty = whole country)."""
    return _get(user_id, "location", "")


def set_location(user_id: str, loc: str) -> None:
    """Persist the location filter for this user."""
    _set(user_id, "location", (loc or "").strip())


# === Background scheduler ===


def get_scheduler_enabled(user_id: str) -> bool:
    """Return whether the auto-fetch scheduler is enabled for this user."""
    # Defaults to on only if config seeded a positive interval.
    return bool(_get(user_id, "scheduler_enabled", AUTO_FETCH_MINUTES > 0))


def set_scheduler_enabled(user_id: str, value: bool) -> None:
    """Enable or disable the auto-fetch scheduler for this user."""
    _set(user_id, "scheduler_enabled", bool(value))
    logger.info("Scheduler %s for user %s", "enabled" if value else "disabled", user_id)


def get_scheduler_interval(user_id: str) -> int:
    """Return the scheduler interval in minutes for this user."""
    default = AUTO_FETCH_MINUTES if AUTO_FETCH_MINUTES > 0 else 60
    return int(_get(user_id, "scheduler_interval", default))


# Clamp bounds for user-supplied numbers so one account cannot configure the
# scheduler or fetchers into hammering external APIs (each scored job also
# costs an LLM call downstream).
SCHEDULER_INTERVAL_MIN_MINUTES = 5
SCHEDULER_INTERVAL_MAX_MINUTES = 1440
ARBEITNOW_LIMIT_MAX = 300
BUNDESAGENTUR_LIMIT_MAX = 200


def set_scheduler_interval(user_id: str, minutes: int) -> None:
    """Persist the scheduler interval in minutes, clamped to a sane range."""
    clamped = max(
        SCHEDULER_INTERVAL_MIN_MINUTES,
        min(SCHEDULER_INTERVAL_MAX_MINUTES, int(minutes)),
    )
    _set(user_id, "scheduler_interval", clamped)


def get_users_with_scheduler_enabled() -> list[str]:
    """Return user_ids of all users who have enabled the auto-fetch scheduler."""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT user_id FROM user_settings WHERE key = 'scheduler_enabled' AND value = 'true'"
        ).fetchall()
    return [r["user_id"] for r in rows]


# === Fetch pipeline options ===


def get_entry_only(user_id: str) -> bool:
    """Return whether to limit fetched jobs to entry-level roles for this user."""
    return bool(_get(user_id, "entry_only", True))


def set_entry_only(user_id: str, value: bool) -> None:
    """Persist the entry-level filter preference for this user."""
    _set(user_id, "entry_only", bool(value))


def get_arbeitnow_limit(user_id: str) -> int:
    """Return the maximum number of Arbeitnow jobs to fetch for this user."""
    return int(_get(user_id, "arbeitnow_limit", 100))


def set_arbeitnow_limit(user_id: str, n: int) -> None:
    """Persist the Arbeitnow fetch limit for this user, clamped to a sane range."""
    _set(user_id, "arbeitnow_limit", max(1, min(ARBEITNOW_LIMIT_MAX, int(n))))


def get_bundesagentur_limit(user_id: str) -> int:
    """Return the maximum number of Bundesagentur jobs to fetch for this user."""
    return int(_get(user_id, "bundesagentur_limit", 50))


def set_bundesagentur_limit(user_id: str, n: int) -> None:
    """Persist the Bundesagentur fetch limit for this user, clamped to a sane range."""
    _set(user_id, "bundesagentur_limit", max(1, min(BUNDESAGENTUR_LIMIT_MAX, int(n))))
