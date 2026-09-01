# services/matcher/engine.py
"""
Main matcher for JOBsFitAI.

Splits the JD's demands into two kinds of question, because they need two
different mechanisms:

SCORES - things the candidate can act on, combined into the weighted number:
    required_skills, responsibilities, preferred_skills, education, certifications

GATES - hard requirements that are pass/fail, reported as warnings and never
scored as a fraction (a candidate with 2 of 5 required years is not "40%
qualified"):
    years of experience, language level

Responsibility coverage is judged by an LLM and therefore opt-in: the Analyser
turns it on, the job-matching pipeline (hundreds of jobs per run) leaves it off,
in which case the section is None and its weight redistributes to the others.

Output format:
    {
        "overall_score":    74.5,
        "label":            "Good Match",
        "section_scores": {
            "required_skills":  88.0,
            "preferred_skills": 55.0,
            "responsibilities": 72.0,    # None when not LLM-judged
            "education":        80.0,
            "certifications":   None,    # None = JD had no data, excluded
        },
        "gates": {
            "experience": {"required_years": 5, "candidate_years": 2, "met": False, ...},
            "languages":  [{"language": "german", "required": "fluent", "met": False, ...}],
            "blocking_count": 2,
        },
        "matched_required":  ["python", "git"],
        "partial_required":  ["tensorflow"],   # related skill, half credit
        "missing_required":  ["docker"],
        "demonstrated_duties": [{"duty": "...", "evidence": "..."}],
        "partial_duties":     [...],
        "missing_duties":     ["..."],
    }
"""

from services.matcher.gates import check_gates
from services.matcher.responsibility_coverage import judge_coverage
from services.matcher.scores import (
    score_certifications,
    score_education,
    score_preferred_skills,
    score_required_skills,
)
from services.matcher.scoring_utils import get_score_label
from core.config import WEIGHTS
from core.logger import get_logger

logger = get_logger(__name__)


def match(resume: dict, jd: dict, llm_judge: bool = False) -> dict:
    """
    Score a resume against a JD and check its hard requirement gates.

    Args:
        resume: Extracted resume data.
        jd: Extracted JD data.
        llm_judge: When True, responsibility coverage is judged by an LLM
            (one call). Leave False for bulk job scoring - the section is
            then excluded and its weight redistributed.

    Returns:
        Full match results (see module docstring). Empty dict on invalid input.
    """
    if not resume or not jd:
        logger.error("Invalid inputs - resume or JD is empty")
        return {}

    # --- Scores: what the candidate can act on ---
    # Each scorer returns None when the JD gives it nothing to judge against.
    (
        req_score,
        matched_required,
        partial_required,
        missing_required,
        required_evidence,
    ) = score_required_skills(resume, jd)
    (
        pref_score,
        matched_preferred,
        partial_preferred,
        missing_preferred,
        preferred_evidence,
    ) = score_preferred_skills(resume, jd)
    edu_score = score_education(resume, jd)
    cert_score = score_certifications(resume, jd)

    if llm_judge:
        resp_score, demonstrated, partial_duties, missing_duties = judge_coverage(
            resume, jd
        )
    else:
        # Bulk scoring path - no LLM budget for a per-job coverage judgement.
        resp_score, demonstrated, partial_duties, missing_duties = None, [], [], []

    def _clamp(v: float | None) -> float | None:
        if v is None:
            return None
        return round(max(0.0, min(100.0, float(v))), 1)

    section_scores = {
        "required_skills": _clamp(req_score),
        "responsibilities": _clamp(resp_score),
        "preferred_skills": _clamp(pref_score),
        "education": _clamp(edu_score),
        "certifications": _clamp(cert_score),
    }

    # --- Gates: hard requirements, reported not scored ---
    gates = check_gates(resume, jd)

    # --- Weighted overall over the sections that had data ---
    # None sections are excluded and their weight redistributed, so an absent
    # JD section never drags the overall toward a fake neutral value.
    active = {s: v for s, v in section_scores.items() if v is not None}
    active_weight_total = sum(WEIGHTS.get(s, 0) for s in active)

    if active and active_weight_total > 0:
        overall_score = round(
            sum(v * WEIGHTS.get(s, 0) / active_weight_total for s, v in active.items()),
            1,
        )
    else:
        overall_score = 0.0
        logger.warning("No scoreable sections - JD extraction produced no data")

    overall_score = max(0.0, min(100.0, overall_score))
    label = get_score_label(overall_score)

    logger.debug(
        "Scored %.0f%% %s (sections: %s | blocking gates: %d)",
        overall_score,
        label,
        ", ".join(f"{s} {v:.0f}" for s, v in active.items()),
        gates["blocking_count"],
    )

    return {
        "overall_score": overall_score,
        "label": label,
        "section_scores": section_scores,
        "gates": gates,
        "matched_required": matched_required,
        "partial_required": partial_required,
        "missing_required": missing_required,
        "matched_preferred": matched_preferred,
        "partial_preferred": partial_preferred,
        "missing_preferred": missing_preferred,
        # How each skill was resolved - lets the UI show proof, and surfaces the
        # skills proven in experience but absent from the skills section.
        "required_evidence": required_evidence,
        "preferred_evidence": preferred_evidence,
        "demonstrated_duties": demonstrated,
        "partial_duties": partial_duties,
        "missing_duties": missing_duties,
    }
