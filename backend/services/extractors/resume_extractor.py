# services/extractors/resume_extractor.py
"""
Resume extraction module.

Uses an LLM to extract structured information from resume text.
Includes light post-processing to normalize all string values to lowercase.
"""

from services.matcher.experience import (
    entry_duration_years,
    total_experience_years,
)
from services.prompts import get_resume_prompt
from core.config import RESUME_MAX_CHARS
from core.logger import get_logger
from services.llm.caller import call_llm, parse_json_response

logger = get_logger(__name__)


def _compute_experience_durations(result: dict) -> dict:
    """
    Fill duration_years per entry and meta.total_experience_years in Python.

    Deterministic - the same dates always yield the same numbers - and
    overrides whatever the LLM put in those fields.
    """
    entries = result.get("experience_entries", [])

    if isinstance(entries, list):
        for entry in entries:
            if isinstance(entry, dict):
                entry["duration_years"] = entry_duration_years(entry)
        total = total_experience_years(entries)
    else:
        total = 0.0

    meta = result.setdefault("meta", {})
    if isinstance(meta, dict):
        meta["total_experience_years"] = total

    logger.debug("Computed total experience: %.1f years", total)
    return result


# Only these list fields are lowercased - they feed exact-match comparisons.
# All other fields (candidate name, titles, company names, etc.) keep original casing
# so the UI displays them correctly.
_COMPARISON_LIST_FIELDS = ("skills", "languages", "certifications")


def _lowercase_comparison_fields(data: dict) -> dict:
    """Lowercase only list fields used for text comparison; preserve all display fields."""
    for key in _COMPARISON_LIST_FIELDS:
        val = data.get(key)
        if not isinstance(val, list):
            continue
        if key == "languages":
            # Languages are dicts {language, proficiency} - keep as-is.
            data[key] = [v for v in val if v]
        else:
            # skills / certifications must be strings; coerce dicts so scorers
            # don't crash when the LLM pattern-matches the language dict format.
            normalized = []
            for v in val:
                if isinstance(v, str):
                    normalized.append(v.lower().strip())
                elif isinstance(v, dict):
                    name = v.get("name") or v.get("skill") or v.get("title") or str(v)
                    normalized.append(str(name).lower().strip())
            data[key] = [v for v in normalized if v]
    return data


_RESUME_FIELD_TYPES: dict = {
    "candidate": dict,
    "summary": str,
    "experience_entries": list,
    "projects": list,
    "education": list,
    "skills": list,
    "languages": list,
    "certifications": list,
    "awards": list,
    "meta": dict,
}

_RESUME_DEFAULTS: dict = {
    "candidate": {},
    "summary": "",
    "experience_entries": [],
    "projects": [],
    "education": [],
    "skills": [],
    "languages": [],
    "certifications": [],
    "awards": [],
    "meta": {},
}


def _validate_resume_schema(result: dict) -> dict:
    """Ensure all required resume fields exist with correct types; fill gaps with safe defaults."""
    for field, expected_type in _RESUME_FIELD_TYPES.items():
        val = result.get(field)
        if val is None:
            logger.debug("Resume field '%s' missing - using default", field)
            result[field] = _RESUME_DEFAULTS[field]
        elif not isinstance(val, expected_type):
            logger.warning(
                "Resume field '%s' has unexpected type %s (expected %s) - using default",
                field,
                type(val).__name__,
                expected_type.__name__,
            )
            result[field] = _RESUME_DEFAULTS[field]
    return result


def _is_empty(value) -> bool:
    """
    Check if an extracted field value is considered empty.

    Args:
        value: Any extracted field value

    Returns:
        bool: True if value is empty, False otherwise
    """
    return value in ("", None, [], {}, [""]) or value == [None]


def extract_resume(resume_text: str) -> dict:
    """
    Extract structured resume data from raw text using an LLM.

    Pipeline:
        1. Truncate input to RESUME_MAX_CHARS
        2. Build prompt
        3. Call LLM
        4. Parse JSON response
        5. Normalize all values to lowercase

    Args:
        resume_text (str): Raw resume text

    Returns:
        dict: Structured resume data with all string values lowercased

    Raises:
        ValueError: If LLM response is not a valid dict
    """
    if not resume_text or not resume_text.strip():
        logger.warning("Empty resume text received - returning empty dict")
        return {}

    # Truncate to max allowed chars - safeguard for LLM input limits
    if len(resume_text) > RESUME_MAX_CHARS:
        logger.warning(
            "Resume text truncated from %d to %d characters",
            len(resume_text),
            RESUME_MAX_CHARS,
        )
        resume_text = resume_text[:RESUME_MAX_CHARS]

    prompt = get_resume_prompt(resume_text)
    # Extraction is structured parsing, not reasoning: it wants speed and a
    # deterministic temperature, both of which a reasoning model gives up.
    _res = call_llm(prompt)
    response = _res.text if (_res and _res.text) else None
    result = parse_json_response(response)

    if not isinstance(result, dict):
        logger.error("LLM response is not a dict: %s", result)
        raise ValueError("Invalid LLM response format")

    result = _validate_resume_schema(result)
    # Durations are computed here (deterministically), not by the LLM.
    result = _compute_experience_durations(result)
    result = _lowercase_comparison_fields(result)

    empty_keys = [k for k, v in result.items() if _is_empty(v)]
    if empty_keys:
        logger.debug("Empty resume fields: %s", empty_keys)

    skills = result.get("skills", [])
    n_skills = (
        len(skills)
        if isinstance(skills, list)
        else sum(len(v) for v in skills.values() if isinstance(v, list))
        if isinstance(skills, dict)
        else 0
    )
    n_roles = len(result.get("experience_entries", []))
    years = result.get("meta", {}).get("total_experience_years", 0)
    logger.info(
        "Resume extracted: %d skills, %d roles, %.1fy experience",
        n_skills,
        n_roles,
        years,
    )
    return result
