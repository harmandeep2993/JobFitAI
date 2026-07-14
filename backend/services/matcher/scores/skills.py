# services/matcher/scores/skills.py
"""
Skill scoring for JOBsFitAI.

Match order per JD skill (cheapest first):
    1. Exact match in the resume skills list (after alias normalization)
    2. Evidence search across the full resume text (bullets, projects, ...)
    3. Embedding similarity, but ONLY to catch a rephrasing of the same skill

A skill is present or it is not - there is no half-credit "related" band, and
the partial list this module still returns is always empty (kept so callers and
the cached payload shape stay stable). See the FULL_MATCH_SIM comment for the
measurements that killed it.

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

# Embeddings can only be trusted to spot a REPHRASING of the same skill
# ('python' vs 'python programming' = 0.93). They cannot judge whether two
# different skills are related, because the model has no skill ontology - it
# matches surface tokens. Measured against our model:
#
#     java vs fastapi        0.822   <- nonsense, yet the highest score here
#     aws vs azure           0.706   <- genuinely related
#     kubernetes vs docker   0.412   <- genuinely related, scored as unrelated
#
# The wrong pair outranks both right ones, so no "related" threshold exists.
# A half-credit band on top of this would hand out score for skills the
# candidate does not have, so there is none: a skill is present or it is not.
FULL_MATCH_SIM = 0.85


def _score_skill_list(
    jd_skills: list, resume: dict
) -> tuple[float | None, list, list, list, dict]:
    """Score one JD skill list against the resume.

    Returns:
        (score 0-100 or None when the JD lists nothing, matched, partial,
         missing, evidence). `partial` is always empty. Evidence maps each
         skill to how it was resolved:
            listed        - present in the resume's skills section
            in_experience - proven by a bullet/project, but absent from skills
            similar       - a rephrasing of a skill in the skills list
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

    # Embedding pass only for skills the cheap checks could not resolve, and
    # only to catch a rephrasing of the SAME skill - never to award credit for a
    # merely similar-sounding one.
    if unresolved and candidate:
        model = load_model()
        unresolved_vecs = model.encode(unresolved, convert_to_tensor=True)
        candidate_vecs = model.encode(candidate, convert_to_tensor=True)
        sim_matrix = util.cos_sim(unresolved_vecs, candidate_vecs)

        for i, skill in enumerate(unresolved):
            best_idx = int(sim_matrix[i].argmax().item())
            best_sim = sim_matrix[i].max().item()
            logger.debug("Skill '%s' best similarity: %.4f", skill, best_sim)
            if best_sim >= FULL_MATCH_SIM:
                matched.append(skill)
                evidence[skill] = {"how": "similar", "detail": candidate[best_idx]}
            else:
                # No "closest skill" hint: the same measurement that made the
                # related band untrustworthy would name fastapi as the nearest
                # thing to java. A wrong hint is worse than none.
                missing.append(skill)
                evidence[skill] = {"how": "missing", "detail": ""}
    else:
        missing.extend(unresolved)
        for skill in unresolved:
            evidence[skill] = {"how": "missing", "detail": ""}

    score = round(len(matched) / len(required) * 100, 1)

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
