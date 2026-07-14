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
from services.matcher.experience import entry_fte_years, total_experience_years
from services.matcher.scores.languages import level_label, parse_lang_entry
from services.matcher.skill_aliases import found_in_corpus, normalize_skill

logger = get_logger(__name__)

_WS_RE = re.compile(r"\s+")

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


# A past role counts toward THIS job's experience only if it demonstrates a
# meaningful share of the skills the job asks for. Employers do not credit a
# career changer's five years in another field, and neither should we.
RELEVANCE_SKILL_RATIO = 0.25


def relevant_experience(resume: dict, jd: dict) -> tuple[float, list[dict]]:
    """Return (relevant FTE years, per-role detail) for this specific job.

    A role is relevant when its title and bullets evidence at least
    RELEVANCE_SKILL_RATIO of the JD's required skills. This reuses the same
    evidence search the skill scorer uses, so it is deterministic, free, and
    consistent with how skills are matched elsewhere.

    Total years answer "how long have you worked". This answers the question a
    recruiter actually asks: "how long have you done THIS".
    """
    required = [
        normalize_skill(s)
        for s in (jd.get("required_skills") or [])
        if s and str(s).strip()
    ]
    entries = [
        e for e in (resume.get("experience_entries") or []) if isinstance(e, dict)
    ]
    if not entries:
        return 0.0, []

    # No required skills to judge against - every role counts, as before.
    threshold = max(1, round(len(required) * RELEVANCE_SKILL_RATIO)) if required else 0

    detail = []
    relevant_entries = []
    for entry in entries:
        parts = [entry.get("title") or "", entry.get("company") or ""]
        parts += [
            b for b in (entry.get("responsibilities") or []) if isinstance(b, str)
        ]
        role_text = _WS_RE.sub(" ", " ".join(parts).lower())

        hits = (
            [s for s in required if found_in_corpus(s, role_text)] if required else []
        )
        is_relevant = (not required) or len(hits) >= threshold

        years = entry_fte_years(entry)
        detail.append(
            {
                "title": entry.get("title") or "",
                "company": entry.get("company") or "",
                "years": years,
                "relevant": is_relevant,
                "matched_skills": hits,
            }
        )
        if is_relevant:
            relevant_entries.append(entry)

    return total_experience_years(relevant_entries), detail


def check_experience_gate(resume: dict, jd: dict) -> dict | None:
    """Compare the JD's year requirement against RELEVANT experience.

    Total years is the wrong number to gate on: a career changer with five years
    in another field and one in this one does not have five years of what the
    employer asked for. The gate therefore uses years spent in roles that
    actually demonstrate this job's required skills, and reports both figures so
    the user can see the difference.

    Returns None when the JD states no year requirement (nothing to gate on).
    """
    required = parse_required_years(jd.get("experience_requirements", []))
    if required is None:
        return None

    meta = resume.get("meta") or {}
    try:
        total = float(meta.get("total_experience_years") or 0)
    except (TypeError, ValueError):
        total = 0.0

    relevant, roles = relevant_experience(resume, jd)
    met = relevant >= required

    if met:
        message = (
            f"Meets the {required}+ years requirement "
            f"({relevant:g} relevant years of {total:g} total)."
        )
    elif relevant < total:
        # The distinction is the whole point - say it out loud.
        message = (
            f"This role asks for {required}+ years. You have {total:g} years of "
            f"experience, but only {relevant:g} in roles matching this job."
        )
    else:
        message = (
            f"This role asks for {required}+ years of relevant experience - "
            f"your resume shows {relevant:g}."
        )

    logger.debug(
        "Experience gate: need %d, relevant %g (total %g), met=%s",
        required,
        relevant,
        total,
        met,
    )
    return {
        "required_years": required,
        "candidate_years": relevant,
        "total_years": total,
        "roles": roles,
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
