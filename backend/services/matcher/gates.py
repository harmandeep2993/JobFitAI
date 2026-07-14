# services/matcher/gates.py
"""
Hard requirement gates - pass/fail checks, never weighted points.

Years of experience and language level are dealbreakers, not degrees of fit:
a candidate with 2 of 5 required years is not "40% qualified", they are either
considered or filtered out. Scoring them as a weighted fraction both models the
world wrongly and, for an entry-level audience where everyone has 0-2 years,
shifts every score down without discriminating between candidates.

Both checks are deterministic - no embeddings, no thresholds, no LLM.
"""

import re

from core.logger import get_logger
from services.matcher.scores.languages import level_label, parse_lang_entry

logger = get_logger(__name__)

# Matches a year requirement in EN/DE: "3+ years", "3-5 years", "at least 3
# years", "mindestens 3 Jahre", "5 yrs". The captured number is the lower bound
# of any range, which is the bar the candidate actually has to clear. The range
# separator accepts a plain hyphen or a unicode en-dash (escaped in the
# pattern so this source file stays plain ASCII).
_YEARS_RE = re.compile(
    r"(\d+)\s*(?:\+|\s*[-\u2013]\s*\d+)?\s*(?:years?|yrs?|jahre[n]?)\b",
    re.IGNORECASE,
)


def parse_required_years(requirements: list[str]) -> int | None:
    """Return the highest year requirement stated in the JD, or None if none is.

    Args:
        requirements: JD experience_requirements strings.

    Returns:
        The binding (highest) year requirement, e.g. 3 for "3+ years".
        None when the JD states no numeric year requirement.
    """
    found: list[int] = []
    for req in requirements or []:
        if isinstance(req, str):
            found.extend(int(m) for m in _YEARS_RE.findall(req))
    if not found:
        return None
    # Multiple statements ("2+ years Python, 5+ years ML") - the highest is the
    # binding constraint.
    return max(found)


def check_experience_gate(resume: dict, jd: dict) -> dict | None:
    """Compare the JD's year requirement against the candidate's total years.

    Returns None when the JD states no year requirement (nothing to gate on).
    Otherwise returns {required_years, candidate_years, met, message}.
    """
    required = parse_required_years(jd.get("experience_requirements", []))
    if required is None:
        return None

    meta = resume.get("meta") or {}
    try:
        candidate = float(meta.get("total_experience_years") or 0)
    except (TypeError, ValueError):
        candidate = 0.0

    met = candidate >= required
    if met:
        message = f"Meets the {required}+ years requirement ({candidate:g} years)."
    else:
        message = (
            f"This role asks for {required}+ years of experience - "
            f"your resume shows {candidate:g}."
        )
    logger.debug("Experience gate: need %d, have %g, met=%s", required, candidate, met)
    return {
        "required_years": required,
        "candidate_years": candidate,
        "met": met,
        "message": message,
    }


def check_language_gates(resume: dict, jd: dict) -> list[dict]:
    """Check each language the JD requires against the candidate's level.

    Returns one entry per required language: {language, required, have, met,
    message}. Empty list when the JD requires no languages.
    """
    required = [
        p for p in (parse_lang_entry(e) for e in jd.get("languages", []) if e) if p[0]
    ]
    if not required:
        return []

    candidate = {
        lang: level
        for lang, level in (
            parse_lang_entry(e) for e in resume.get("languages", []) if e
        )
        if lang
    }

    gates = []
    for lang, req_level in required:
        have_level = candidate.get(lang)
        if have_level is None:
            met = False
            message = f"{lang.title()} is required and is not listed on your resume."
        elif req_level == 0 or have_level == 0:
            # Level unspecified on either side - cannot fail someone on a
            # requirement that was never quantified.
            met = True
            message = f"{lang.title()} listed (no level specified)."
        elif have_level >= req_level:
            met = True
            message = (
                f"{lang.title()} {level_label(have_level)} meets the "
                f"required {level_label(req_level)}."
            )
        else:
            met = False
            message = (
                f"{lang.title()} requires {level_label(req_level)} - "
                f"your resume shows {level_label(have_level)}."
            )
        gates.append(
            {
                "language": lang,
                "required": level_label(req_level) if req_level else "",
                "have": level_label(have_level) if have_level else "",
                "met": met,
                "message": message,
            }
        )
    return gates


def check_gates(resume: dict, jd: dict) -> dict:
    """Run every hard-requirement gate.

    Returns {experience, languages, blocking_count} where blocking_count is how
    many hard requirements the candidate fails.
    """
    experience = check_experience_gate(resume, jd)
    languages = check_language_gates(resume, jd)
    blocking = [g for g in languages if not g["met"]]
    if experience and not experience["met"]:
        blocking.append(experience)
    return {
        "experience": experience,
        "languages": languages,
        "blocking_count": len(blocking),
    }
