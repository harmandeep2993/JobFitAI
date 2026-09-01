# services/matcher/experience.py
"""
Experience duration maths, shared by extraction and the gates.

Two questions get asked of a work history, and they have different answers:

    "How long have you worked?"      -> total_experience_years (all roles)
    "How long have you done THIS?"   -> relevant years (gates.py, per JD)

Both are measured in full-time-equivalent years rather than raw calendar span. A
Werkstudent contract is capped at ~20h/week and an internship is short and
junior, so counting either as a full year inflates the total - badly, for exactly
the entry-level candidates this product serves. Recruiters discount them the same
way, so we do too.
"""

import datetime as dt
import re

from core.logger import get_logger

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

# Full-time-equivalent weight per employment type.
FTE_WEIGHTS = {
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

# An unrecognised employment type is assumed full-time: never silently discount
# a role just because the extractor could not label it.
DEFAULT_FTE = 1.0


def parse_month_year(value) -> dt.date | None:
    """Parse a resume date into a date (day = 1), or None when unparseable.

    Handles "YYYY-MM", "MM/YYYY", "DD/MM/YYYY", bare years, month-name forms
    like "mar 2020", and "present"/current-role words in several languages.
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

    # MM/YYYY - the common resume format
    m = re.match(r"^(\d{1,2})[-/.](\d{4})$", s)
    if m:
        month, year = int(m.group(1)), int(m.group(2))
        if 1900 <= year <= 2100:
            return dt.date(year, month if 1 <= month <= 12 else 1, 1)
        return None

    # DD/MM/YYYY - take the month and year
    m = re.match(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})$", s)
    if m:
        month, year = int(m.group(2)), int(m.group(3))
        if 1900 <= year <= 2100:
            return dt.date(year, month if 1 <= month <= 12 else 1, 1)
        return None

    # Any 4-digit year, plus an optional month name anywhere in the string
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


def fte_weight(entry: dict) -> float:
    """Full-time-equivalent weight for an entry's employment type."""
    raw = (entry.get("employment_type") or "").strip().lower()
    return FTE_WEIGHTS.get(raw, DEFAULT_FTE)


def entry_duration_years(entry: dict) -> float:
    """Calendar years between an entry's start and end dates."""
    start = parse_month_year(entry.get("start_date", ""))
    end = parse_month_year(entry.get("end_date", ""))
    if not start or not end or end < start:
        return 0.0
    return round((end - start).days / 365.25, 1)


def entry_fte_years(entry: dict) -> float:
    """One role's duration in full-time-equivalent years."""
    return round(entry_duration_years(entry) * fte_weight(entry), 1)


def total_experience_years(entries: list) -> float:
    """Full-time-equivalent years across a set of roles.

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
        start = parse_month_year(entry.get("start_date", ""))
        end = parse_month_year(entry.get("end_date", ""))
        if start and end and end >= start:
            spans.append((start, end, fte_weight(entry)))

    if not spans:
        return 0.0

    earliest = min(s for s, _, _ in spans)
    latest = max(e for _, e, _ in spans)

    total_months = 0.0
    year, month = earliest.year, earliest.month
    while (year, month) <= (latest.year, latest.month):
        best = 0.0
        for start, end, weight in spans:
            if (start.year, start.month) <= (year, month) <= (end.year, end.month):
                best = max(best, weight)
        total_months += best
        month += 1
        if month > 12:
            year, month = year + 1, 1

    return round(total_months / 12, 1)
