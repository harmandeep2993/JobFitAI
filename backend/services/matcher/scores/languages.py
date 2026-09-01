# services/matcher/scores/languages.py
"""
Language name and CEFR proficiency parsing.

Not a scorer: a language requirement is a hard gate, not a fraction of a score
(needing C1 German and having B2 is a dealbreaker, not "83% of a language").
services/matcher/gates.py consumes these helpers to produce a pass/fail warning.

Handles both schema shapes and free text in either language:
    {"language": "German", "proficiency": "C1"}   and   "german (C1)"
"""

import langcodes

from core.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Proficiency level map
# ---------------------------------------------------------------------------

PROFICIENCY_LEVELS = {
    # C2 - mastery
    "c2": 6,
    "mastery": 6,
    "native": 6,
    "muttersprache": 6,
    "mother tongue": 6,
    "first language": 6,
    # C1 - advanced
    "c1": 5,
    "fluent": 5,
    "fliessend": 5,
    "advanced": 5,
    "proficient": 5,
    "verhandlungssicher": 5,
    # B2 - upper intermediate
    "b2": 4,
    "upper intermediate": 4,
    "professional": 4,
    "business": 4,
    "working proficiency": 4,
    # B1 - intermediate
    "b1": 3,
    "intermediate": 3,
    "conversational": 3,
    "konversation": 3,
    # A2 - elementary
    "a2": 2,
    "elementary": 2,
    "basic": 2,
    "grundkenntnisse": 2,
    "beginner": 2,
    # A1 - starter
    "a1": 1,
    "anfanger": 1,
    "starter": 1,
}

# Human-readable label per numeric proficiency level (used in gate messages)
_LEVEL_LABEL: dict[int, str] = {
    6: "native",
    5: "fluent",
    4: "professional",
    3: "intermediate",
    2: "basic",
    1: "beginner",
    0: "unspecified",
}


def level_label(level: int) -> str:
    """Return a human-readable name for a numeric proficiency level."""
    return _LEVEL_LABEL.get(level, str(level))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_level(text: str) -> int:
    """
    Extract proficiency level from a text string.
    Checks longest keywords first to avoid partial matches.
    Returns 0 if no level found.
    """
    sorted_keywords = sorted(PROFICIENCY_LEVELS.keys(), key=len, reverse=True)
    for keyword in sorted_keywords:
        if keyword in text:
            return PROFICIENCY_LEVELS[keyword]
    return 0


def _normalize_language(text: str) -> tuple[str, int]:
    """
    Parse a raw language string into (canonical_english_name, proficiency_level).
    Level 0 means not specified.
    """
    text = text.lower().strip()
    level = _extract_level(text)

    lang_part = text.split("(")[0].split(",")[0].strip()
    for keyword in PROFICIENCY_LEVELS:
        lang_part = lang_part.replace(keyword, "").strip()

    if not lang_part:
        return text, level

    try:
        lang = langcodes.find(lang_part)
        return lang.display_name().lower(), level
    except Exception:
        logger.warning("langcodes could not normalize '%s' - using as-is", lang_part)
        return lang_part, level


def parse_lang_entry(entry) -> tuple[str, int]:
    """
    Handle both formats:
      - dict: {"language": "german", "proficiency": "B1"}  (new schema)
      - str:  "german (B1)"                                 (legacy)

    Returns (canonical_name, proficiency_level).
    """
    if isinstance(entry, dict):
        lang_str = (entry.get("language") or "").strip()
        prof_str = (entry.get("proficiency") or "").lower().strip()
        if not lang_str:
            return "", 0
        level = _extract_level(prof_str)
        try:
            canonical = langcodes.find(lang_str.lower()).display_name().lower()
        except Exception:
            canonical = lang_str.lower()
        return canonical, level
    return _normalize_language(str(entry))
