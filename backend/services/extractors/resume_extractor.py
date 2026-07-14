# services/extractors/resume_extractor.py
"""
Resume extraction module.

Uses an LLM to extract structured information from resume text.
Includes light post-processing to normalize all string values to lowercase.
"""

import datetime as dt
import re

from services.prompts import get_resume_prompt
from core.config import RESUME_MAX_CHARS
from core.logger import get_logger
from services.llm.caller import call_llm, parse_json_response

logger = get_logger(__name__)

# Words that mean "still ongoing" across the languages we support.
_PRESENT_WORDS = {
    "present",
    "current",
    "now",
    "ongoing",
    "to date",
    "till date",
    "heute",
    "aktuell",
    "laufend",
    "actuel",
    "actuellement",
    "present",
    "actual",
    "presente",
    "attuale",
}

_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


def _parse_month_year(value) -> dt.date | None:
    """
    Parse a normalized resume date into a date (day = 1).

    Handles "YYYY-MM", "YYYY/MM", "YYYY", month-name forms like
    "mar 2020", and "present"/current-role words. Returns None when the
    value can't be parsed.
    """
    if not value or not isinstance(value, str):
        return None

    s = value.strip().lower()
    if not s:
        return None

    if s in _PRESENT_WORDS or s.startswith("present") or s.startswith("current"):
        return dt.date.today()

    # YYYY-MM / YYYY/MM / YYYY.MM (optionally with a day after)
    m = re.match(r"^(\d{4})[-/.](\d{1,2})", s)
    if m:
        year, month = int(m.group(1)), int(m.group(2))
        if 1900 <= year <= 2100:
            return dt.date(year, month if 1 <= month <= 12 else 1, 1)
        return None

    # MM/YYYY or M/YYYY (month-first, 4-digit year) - the common resume format.
    m = re.match(r"^(\d{1,2})[-/.](\d{4})$", s)
    if m:
        month, year = int(m.group(1)), int(m.group(2))
        if 1900 <= year <= 2100:
            return dt.date(year, month if 1 <= month <= 12 else 1, 1)
        return None

    # DD/MM/YYYY (day-month-year) - take the month and year.
    m = re.match(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})$", s)
    if m:
        month, year = int(m.group(2)), int(m.group(3))
        if 1900 <= year <= 2100:
            return dt.date(year, month if 1 <= month <= 12 else 1, 1)
        return None

    # Any 4-digit year, plus an optional month name anywhere in the string.
    ym = re.search(r"(\d{4})", s)
    if ym:
        year = int(ym.group(1))
        if not (1900 <= year <= 2100):
            return None
        month = 1
        mm = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", s)
        if mm:
            month = _MONTHS[mm.group(1)]
        return dt.date(year, month, 1)

    return None


def _entry_duration_years(entry: dict) -> float:
    """Years between an entry's start and end dates, rounded to 1 decimal."""
    start = _parse_month_year(entry.get("start_date", ""))
    end = _parse_month_year(entry.get("end_date", ""))
    if not start or not end or end < start:
        return 0.0
    return round((end - start).days / 365.25, 1)


# Full-time-equivalent weight per employment type. A Werkstudent contract is
# capped at ~20h/week during term and an internship is short and junior, so
# counting either as a full year of professional experience inflates the total -
# badly, for exactly the entry-level candidates this product serves. Recruiters
# discount them the same way.
_FTE_WEIGHTS = {
    "full-time": 1.0,
    "freelance": 1.0,
    "apprenticeship": 1.0,
    "research": 1.0,
    "teaching": 1.0,
    "part-time": 0.5,
    "working student": 0.5,
    "internship": 0.5,
    "volunteer": 0.25,
}
_DEFAULT_FTE = 1.0


def _fte_weight(entry: dict) -> float:
    """Return the full-time-equivalent weight for an entry's employment type."""
    raw = (entry.get("employment_type") or "").strip().lower()
    return _FTE_WEIGHTS.get(raw, _DEFAULT_FTE)


def _total_experience_years(entries: list) -> float:
    """
    Professional experience in full-time-equivalent years.

    Walks the timeline one month at a time and credits each month at the weight
    of the most substantial role covering it. This does two things at once:
    concurrent roles are never double-counted (a working student job held during
    a full-time role adds nothing), and part-time work is credited at its real
    weight rather than as a full year.
    """
    spans = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        start = _parse_month_year(entry.get("start_date", ""))
        end = _parse_month_year(entry.get("end_date", ""))
        if start and end and end >= start:
            spans.append((start, end, _fte_weight(entry)))

    if not spans:
        return 0.0

    earliest = min(s for s, _, _ in spans)
    latest = max(e for _, e, _ in spans)

    total_months = 0.0
    year, month = earliest.year, earliest.month
    while (year, month) <= (latest.year, latest.month):
        # Credit this month at the weight of the best role covering it.
        best = 0.0
        for start, end, weight in spans:
            if (start.year, start.month) <= (year, month) <= (end.year, end.month):
                best = max(best, weight)
        total_months += best
        month += 1
        if month > 12:
            year, month = year + 1, 1

    return round(total_months / 12, 1)


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
                entry["duration_years"] = _entry_duration_years(entry)
        total = _total_experience_years(entries)
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
