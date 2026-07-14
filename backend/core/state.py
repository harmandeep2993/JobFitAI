# core/state.py
"""
Runtime state: per-user resume cache and app-wide LLM provider selection.

Resume state is per-user: each user's extracted resume JSON is kept in
memory (keyed by user_id) and persisted to the `user_resume` table so it
survives server restarts without re-extraction.

LLM provider/model selection is app-wide (not per-user) and lives only in
memory - it resets to config.yaml defaults on restart.
"""

import json
from datetime import datetime, timezone

from core import database
from core.config import PROVIDER_CONFIGS
from core.logger import get_logger
from services.llm.providers import groq as _groq_p
from services.llm.providers import openai as _openai_p

logger = get_logger(__name__)

# === Scheduler state ===
# Keyed by user_id; tracks when each user's last fetch run fired so the
# scheduler can enforce the configured interval without a database round-trip.
# Exported so both main.py and job_matches.py can read/mutate it.
sched_last_ref: dict[str, float] = {}

# Providers the router has a working path for.
SUPPORTED_PROVIDERS = ["openai", "groq", "ollama"]

# Default provider used until changed at runtime.
# OpenAI gpt-4o-mini has far higher rate limits than Groq's free tier
# (which caps at 6k tokens/min - too small for this 3-call pipeline).
DEFAULT_PROVIDER = "openai"

# === App-wide LLM state ===
# model = None means "use this provider's default model from config.yaml".
_state = {
    "provider": DEFAULT_PROVIDER,
    "model": None,
}

# === Per-user resume state ===
# Keyed by user_id; lazily populated from DB on first access per user.
_resume: dict[str, dict] = {}
_resume_loaded: set[str] = set()


def _ensure_resume_loaded(user_id: str) -> None:
    """Lazily load the persisted resume for user_id from the DB on first access."""
    if user_id in _resume_loaded:
        return
    try:
        with database.connect() as conn:
            row = conn.execute(
                "SELECT name, json FROM user_resume WHERE user_id = ?", (user_id,)
            ).fetchone()
        if row and row["json"]:
            _resume[user_id] = {
                "json": json.loads(row["json"]),
                "name": row["name"] or "",
                "id": None,
            }
            logger.info(
                "Loaded persisted resume for user %s: %s",
                user_id,
                _resume[user_id]["name"] or "(unnamed)",
            )
    except Exception as e:
        logger.error("Failed to load persisted resume for user %s: %s", user_id, e)
    finally:
        _resume_loaded.add(user_id)


# === LLM provider functions (app-wide) ===


def get_provider() -> str:
    """Return the active provider name."""
    return _state["provider"]


def get_model() -> str:
    """Return the active model id, falling back to the provider's config default."""
    if _state["model"]:
        return _state["model"]
    return PROVIDER_CONFIGS.get(_state["provider"], {}).get("model", "")


def get_quality_model() -> str:
    """Return the model for low-volume work worth spending on.

    Two tiers exist because the pipeline has two very different shapes. Bulk work
    runs in loops - a fetch run extracts one JD per job and classifies hundreds
    of titles - so it must stay on the cheap model. Quality work happens once per
    user action and is either read by the user, sent to an employer, or decides a
    single job's score; that is where a stronger model pays for itself.

    Falls back to the active model when the provider nominates no quality model.
    An explicit admin pin always wins - if someone selected a model in the UI,
    honour it rather than silently calling a different one.
    """
    if _state["model"]:
        return _state["model"]
    cfg = PROVIDER_CONFIGS.get(_state["provider"], {})
    return cfg.get("quality_model") or cfg.get("model", "")


def set_active(provider: str, model: str | None = None) -> None:
    """Set the active provider and optional model override.

    Args:
        provider: One of SUPPORTED_PROVIDERS.
        model: Model id, or empty/None to use the provider default.

    Raises:
        ValueError: If the provider is not in SUPPORTED_PROVIDERS.
    """
    provider = (provider or "").strip()
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"Unsupported provider: {provider!r}")

    _state["provider"] = provider
    _state["model"] = (model or "").strip() or None

    logger.info("Active LLM set to %s / %s", provider, get_model())


def get_settings() -> dict:
    """Return the current LLM selection as {'provider', 'model'}."""
    return {"provider": get_provider(), "model": get_model()}


# === Per-user resume functions ===


def get_resume_id(user_id: str) -> str | None:
    """Return the resume_id of the active resume for this user, or None."""
    _ensure_resume_loaded(user_id)
    return (_resume.get(user_id) or {}).get("id")


def set_resume(
    user_id: str, resume_json: dict, name: str = "", resume_id: str | None = None
) -> None:
    """Store the extracted resume for user_id in memory and persist to DB.

    Args:
        user_id: The user this resume belongs to.
        resume_json: Structured extraction output from resume_extractor.
        name: Display name (original filename or label).
        resume_id: Row id from the `resumes` table, if uploaded via the
            resume-storage flow (None for legacy temp uploads).
    """
    _resume[user_id] = {"json": resume_json, "name": name or "", "id": resume_id}
    _resume_loaded.add(user_id)

    try:
        with database.connect() as conn:
            conn.execute(
                """
                INSERT INTO user_resume (user_id, name, json, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    name=excluded.name, json=excluded.json, created_at=excluded.created_at
                """,
                (
                    user_id,
                    name or "",
                    json.dumps(resume_json),
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                ),
            )
    except Exception as e:
        logger.error("Failed to persist resume for user %s: %s", user_id, e)

    logger.info("Resume stored for user %s: %s", user_id, name or "(unnamed)")


def get_resume(user_id: str) -> dict | None:
    """Return the stored resume JSON for this user, or None if none is loaded."""
    _ensure_resume_loaded(user_id)
    return (_resume.get(user_id) or {}).get("json")


def get_resume_name(user_id: str) -> str:
    """Return the stored resume's display name for this user."""
    _ensure_resume_loaded(user_id)
    return (_resume.get(user_id) or {}).get("name", "")


def has_resume(user_id: str) -> bool:
    """Return True if a resume has been extracted and stored for this user."""
    return get_resume(user_id) is not None


def clear_user(user_id: str) -> None:
    """Drop this user's in-memory resume state (used on account erasure).

    Only clears RAM - the caller deletes the persisted rows. Without this, the
    extracted resume JSON would survive account deletion until restart.
    """
    _resume.pop(user_id, None)
    _resume_loaded.discard(user_id)
    sched_last_ref.pop(user_id, None)


def _flatten_skills(skills) -> list:
    if isinstance(skills, list):
        return [s for s in skills if isinstance(s, str)]
    if isinstance(skills, dict):
        out = []
        for v in skills.values():
            if isinstance(v, list):
                out.extend(s for s in v if isinstance(s, str))
        return out
    return []


def resume_info(user_id: str) -> dict:
    """Return a compact view of the stored resume for this user's dashboard panel."""
    r = get_resume(user_id)
    if not r:
        return {}
    return {
        "name": get_resume_name(user_id),
        "title": (r.get("candidate") or {}).get("title", ""),
        "total_years": (r.get("meta") or {}).get("total_experience_years", 0),
        "skills": _flatten_skills(r.get("skills", [])),
        "experience": [
            {
                "title": e.get("title", ""),
                "company": e.get("company", ""),
                "start": e.get("start_date", ""),
                "end": e.get("end_date", ""),
                "years": e.get("duration_years", 0),
            }
            for e in r.get("experience_entries", [])
            if isinstance(e, dict)
        ],
        "education": [
            {
                "degree": e.get("degree", ""),
                "field": e.get("field", ""),
                "institution": e.get("institution", ""),
            }
            for e in r.get("education", [])
            if isinstance(e, dict)
        ],
        "languages": r.get("languages", []),
        "certifications": r.get("certifications", []),
    }


def provider_catalog() -> list[dict]:
    """Return the selectable providers for the UI.

    Each entry: {name, default_model, models[], needs_key, has_key}.
    has_key is a boolean only - no fragment of any API key is ever sent to a
    client, not even a masked suffix.
    """
    _meta = {
        "openai": {"needs_key": True, "mod": _openai_p},
        "groq": {"needs_key": True, "mod": _groq_p},
        "ollama": {"needs_key": False, "mod": None},
    }

    catalog = []
    for name in SUPPORTED_PROVIDERS:
        cfg = PROVIDER_CONFIGS.get(name, {})
        meta = _meta.get(name, {})
        mod = meta.get("mod")
        catalog.append(
            {
                "name": name,
                "default_model": cfg.get("model", ""),
                "models": cfg.get("models", []),
                "needs_key": meta.get("needs_key", False),
                "has_key": mod.has_key() if mod else True,
            }
        )
    return catalog
