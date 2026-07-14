# services/matcher/responsibility_coverage.py
"""
LLM-judged responsibility coverage.

Replaces the cosine-similarity scorer, which was measurably unable to do this
job: genuine matches between duty-phrased JD lines and achievement-phrased
resume bullets land at 0.30-0.47 cosine, while bullets from entirely unrelated
professions reach 0.34 - the distributions overlap, so no threshold separates
signal from noise.

An LLM has no trouble seeing that "reduced inference latency by optimising
model serving" demonstrates "deploy ML models to production", because it judges
the WORK rather than the wording. Output is a per-duty verdict, which is more
useful to the user than a number.

This costs one LLM call, so it is opt-in: the Analyser uses it, the job-matching
pipeline (which scores hundreds of jobs per run) does not.
"""

from core.logger import get_logger
from services.llm.caller import call_llm, parse_json_response

logger = get_logger(__name__)

# Credit for a duty the resume relates to but does not clearly demonstrate.
_PARTIAL_CREDIT = 0.5

# Cap the prompt: JDs list a handful of duties, resumes a few dozen bullets.
_MAX_DUTIES = 15
_MAX_BULLETS = 40

_PROMPT = """Judge whether a candidate's resume demonstrates each job duty.

For each DUTY choose one verdict:
- "yes"     the resume clearly shows the candidate has done this work
- "partial" related work, but narrower scope or not clearly stated
- "no"      no evidence anywhere in the resume

Judge the WORK, not the wording. Resumes describe achievements ("reduced
inference latency 40% by optimising model serving") while job ads describe
duties ("deploy ML models to production") - those are the SAME work and count
as "yes". Do not reward keyword overlap without real evidence.

DUTIES ({count}):
{duties}

RESUME EVIDENCE:
{bullets}

Return ONLY JSON:
{{"verdicts":[{{"n":1,"verdict":"yes","evidence":"<the resume line that proves it, or empty>"}}]}}"""


def _collect_bullets(resume: dict) -> list[str]:
    """Gather every line of the resume that can evidence doing work."""
    bullets: list[str] = []
    for entry in resume.get("experience_entries", []) or []:
        title = (entry.get("title") or "").strip()
        company = (entry.get("company") or "").strip()
        for b in entry.get("responsibilities", []) or []:
            if isinstance(b, str) and b.strip():
                # Carry the role so the judge knows the context of the bullet
                prefix = f"[{title}]" if title else ""
                bullets.append(f"{prefix} {b.strip()}".strip())
        if not entry.get("responsibilities") and title:
            bullets.append(f"{title} at {company}".strip())

    for project in resume.get("projects", []) or []:
        title = (project.get("title") or "").strip()
        desc = (project.get("description") or "").strip()
        if title or desc:
            bullets.append(f"[Project] {title}: {desc}".strip(": ").strip())

    return bullets[:_MAX_BULLETS]


def judge_coverage(resume: dict, jd: dict) -> tuple[float | None, list, list, list]:
    """Score how many JD duties the resume actually demonstrates.

    Returns:
        (score, demonstrated, partial, missing) where score is 0-100.
        score is None when the JD lists no duties (section excluded from the
        overall) or when the LLM is unavailable - a wrong number is worse than
        no number, so failure never invents a score.
    """
    duties = [
        d.strip()
        for d in (jd.get("responsibilities") or [])
        if isinstance(d, str) and d.strip()
    ][:_MAX_DUTIES]

    if not duties:
        logger.debug("JD lists no responsibilities - section excluded")
        return None, [], [], []

    bullets = _collect_bullets(resume)
    if not bullets:
        logger.warning("Resume has no experience bullets - all duties unmet")
        return 0.0, [], [], duties

    prompt = _PROMPT.format(
        count=len(duties),
        duties="\n".join(f"{i}. {d}" for i, d in enumerate(duties, 1)),
        bullets="\n".join(f"- {b}" for b in bullets),
    )

    _res = call_llm(prompt)
    data = parse_json_response(_res.text) if (_res and _res.text) else None
    verdicts = data.get("verdicts") if isinstance(data, dict) else None

    if not isinstance(verdicts, list):
        logger.warning("Coverage judge returned no usable verdicts - excluding section")
        return None, [], [], []

    # Index verdicts by duty number; anything the LLM skipped counts as unproven
    # rather than silently passing.
    by_n: dict[int, dict] = {}
    for v in verdicts:
        if isinstance(v, dict) and isinstance(v.get("n"), int):
            by_n[v["n"]] = v

    demonstrated, partial, missing = [], [], []
    for i, duty in enumerate(duties, 1):
        v = by_n.get(i) or {}
        verdict = str(v.get("verdict", "")).strip().lower()
        evidence = str(v.get("evidence", "")).strip()
        item = {"duty": duty, "evidence": evidence}
        if verdict == "yes":
            demonstrated.append(item)
        elif verdict == "partial":
            partial.append(item)
        else:
            missing.append(duty)

    score = round(
        (len(demonstrated) + _PARTIAL_CREDIT * len(partial)) / len(duties) * 100, 1
    )
    logger.info(
        "Responsibility coverage: %.1f (%d yes, %d partial, %d no of %d)",
        score,
        len(demonstrated),
        len(partial),
        len(missing),
        len(duties),
    )
    return score, demonstrated, partial, missing
