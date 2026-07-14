# services/matcher/scores/skills.py
"""
Skill scoring for JOBsFitAI.

Match order per JD skill (cheapest first):
    1. Exact match in the resume skills list (after alias normalization)
    2. Evidence search across the full resume text (bullets, projects, ...)
    3. Embedding similarity vs the skills list: full match, partial, or missing

Partial matches (a related but not identical skill, e.g. tensorflow when
pytorch is required) earn half credit and are reported separately so the UI
can show them as "related" rather than matched or missing.

Returns None as the score when the JD lists no skills of that kind - the
engine excludes such sections from the weighted overall entirely.
"""

from sentence_transformers import util

from services.matcher.embedding_model import load_model
from services.matcher.skill_aliases import (
    build_evidence_corpus,
    build_evidence_lines,
    find_evidence_line,
    found_in_corpus,
    normalize_skill,
)
from services.matcher.scoring_utils import get_all_skills
from core.logger import get_logger

logger = get_logger(__name__)

# Embedding similarity bands, validated against the multilingual MiniLM
# model: same-skill rephrasings score >= 0.87 ('python' vs 'python
# programming' 0.93), related-but-different skills land 0.50-0.74 ('docker'
# vs 'containerization' 0.52, 'aws' vs 'azure' 0.71), unrelated pairs fall
# below 0.40 ('react' vs 'angular' 0.28). Above FULL counts as the same
# skill; between PARTIAL and FULL earns half credit as related.
FULL_MATCH_SIM = 0.85
PARTIAL_MATCH_SIM = 0.50
PARTIAL_CREDIT = 0.5


def _score_skill_list(
    jd_skills: list, resume: dict
) -> tuple[float | None, list, list, list, dict]:
    """Score one JD skill list against the resume.

    Returns:
        (score 0-100 or None when the JD lists nothing, matched, partial,
         missing, evidence) - lists are normalized skill names; evidence maps
         each skill to how it was resolved:
            listed        - present in the resume's skills section
            in_experience - proven by a bullet/project, but absent from skills
            similar       - a near-identical skill name in the skills list
            related       - a related skill (half credit)
            missing       - no evidence anywhere
    """
    required = list(
        dict.fromkeys(normalize_skill(s) for s in jd_skills if s and s.strip())
    )
    if not required:
        return None, [], [], [], {}

    candidate = list(
        dict.fromkeys(
            normalize_skill(s) for s in get_all_skills(resume.get("skills", []))
        )
    )
    corpus = build_evidence_corpus(resume)
    lines = build_evidence_lines(resume)

    matched: list[str] = []
    partial: list[str] = []
    missing: list[str] = []
    unresolved: list[str] = []
    # How each skill was resolved, so the UI can show proof rather than a chip.
    evidence: dict[str, dict] = {}

    for skill in required:
        if skill in candidate:
            matched.append(skill)
            evidence[skill] = {"how": "listed", "detail": ""}
        elif found_in_corpus(skill, corpus):
            # Evidenced outside the skills section. This is the ATS-relevant
            # case: the candidate demonstrably has the skill, but a recruiter
            # searching their skills list will not find it.
            line = find_evidence_line(skill, lines) or ""
            logger.debug("Skill '%s' found in resume text outside skills list", skill)
            matched.append(skill)
            evidence[skill] = {"how": "in_experience", "detail": line}
        else:
            unresolved.append(skill)

    # Embedding pass only for skills the cheap checks could not resolve.
    if unresolved and candidate:
        model = load_model()
        unresolved_vecs = model.encode(unresolved, convert_to_tensor=True)
        candidate_vecs = model.encode(candidate, convert_to_tensor=True)
        sim_matrix = util.cos_sim(unresolved_vecs, candidate_vecs)

        for i, skill in enumerate(unresolved):
            best_idx = int(sim_matrix[i].argmax().item())
            best_sim = sim_matrix[i].max().item()
            nearest = candidate[best_idx]
            logger.debug("Skill '%s' best similarity: %.4f", skill, best_sim)
            if best_sim >= FULL_MATCH_SIM:
                matched.append(skill)
                evidence[skill] = {"how": "similar", "detail": nearest}
            elif best_sim >= PARTIAL_MATCH_SIM:
                partial.append(skill)
                evidence[skill] = {"how": "related", "detail": nearest}
            else:
                missing.append(skill)
                evidence[skill] = {"how": "missing", "detail": ""}
    else:
        missing.extend(unresolved)
        for skill in unresolved:
            evidence[skill] = {"how": "missing", "detail": ""}

    score = round(
        (len(matched) + PARTIAL_CREDIT * len(partial)) / len(required) * 100, 1
    )

    logger.info(
        "Skill score %.1f - matched=%s partial=%s missing=%s",
        score,
        matched,
        partial,
        missing,
    )
    return score, matched, partial, missing, evidence


def score_required_skills(
    resume: dict, jd: dict
) -> tuple[float | None, list, list, list, dict]:
    """Score resume skills against the JD's required skills.

    Returns (score|None, matched, partial, missing, evidence).
    """
    return _score_skill_list(jd.get("required_skills", []), resume)


def score_preferred_skills(
    resume: dict, jd: dict
) -> tuple[float | None, list, list, list, dict]:
    """Score resume skills against the JD's preferred (nice-to-have) skills.

    Returns (score|None, matched, partial, missing, evidence).
    """
    return _score_skill_list(jd.get("preferred_skills", []), resume)
