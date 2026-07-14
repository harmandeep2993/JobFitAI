# services/resume_rewriter.py
"""
Resume bullet rewrite and suggestion service.

Pulls data from ALL stored resumes (up to 3 slots), merges and deduplicates,
then runs one LLM call to produce JD-aligned bullets.

Three source types handled:
  rewrite  - existing bullet from experience_entries.responsibilities
  suggest  - role/edu/cert/project title exists but no bullets; LLM generates
             a plausible bullet grounded in the title and candidate skills
  (empty)  - if no usable data at all, returns ok=False with reason="no_data"
"""

import json
import re
from collections import defaultdict

from repositories import resume_repo
from core import state
from core.logger import get_logger
from services.llm.caller import call_llm

logger = get_logger(__name__)

# How many items of each type to send per LLM call (keeps prompt under budget)
_MAX_EXP_ROLES = 3
_MAX_BULLETS_PER_ROLE = 5
_MAX_EDU = 2
_MAX_CERTS = 3
_MAX_PROJECTS = 3


def _collect_all(user_id: str) -> dict:
    """
    Merge data from all stored resumes for a user.

    Deduplicates experience by (company, title), education by (institution, degree),
    certifications by name, projects by title. Skills are unioned across all slots.
    Returns a dict with keys: experience, education, certifications, projects, skills.
    """
    rows = resume_repo.list_scoreable(user_id)

    seen_exp = set()
    seen_edu = set()
    seen_cert = set()
    seen_proj = set()

    experience = []
    education = []
    certifications = []
    projects = []
    skills: set[str] = set()

    for row in rows:
        try:
            data = json.loads(row["extracted_json"])
        except Exception:
            continue

        for entry in data.get("experience_entries", []):
            if not isinstance(entry, dict):
                continue
            key = (
                entry.get("company", "").lower().strip(),
                entry.get("title", "").lower().strip(),
            )
            if key == ("", "") or key in seen_exp:
                continue
            seen_exp.add(key)
            experience.append(
                {
                    "title": entry.get("title", ""),
                    "company": entry.get("company", ""),
                    "bullets": [
                        b
                        for b in entry.get("responsibilities", [])
                        if isinstance(b, str) and len(b.strip()) > 10
                    ],
                }
            )

        for edu in data.get("education", []):
            if not isinstance(edu, dict):
                continue
            key = (
                edu.get("institution", "").lower().strip(),
                edu.get("degree", "").lower().strip(),
            )
            if key == ("", "") or key in seen_edu:
                continue
            seen_edu.add(key)
            education.append(
                {
                    "degree": edu.get("degree", ""),
                    "field": edu.get("field", ""),
                    "institution": edu.get("institution", ""),
                }
            )

        for cert in data.get("certifications", []):
            name = (
                cert
                if isinstance(cert, str)
                else (cert.get("name", "") if isinstance(cert, dict) else "")
            )
            name = name.strip()
            if not name or name.lower() in seen_cert:
                continue
            seen_cert.add(name.lower())
            certifications.append(name)

        for proj in data.get("projects", []):
            if isinstance(proj, str):
                title, techs = proj, []
            elif isinstance(proj, dict):
                title = proj.get("title", proj.get("name", ""))
                techs = proj.get("technologies", proj.get("tech", []))
            else:
                continue
            title = title.strip()
            if not title or title.lower() in seen_proj:
                continue
            seen_proj.add(title.lower())
            projects.append(
                {
                    "title": title,
                    "technologies": techs if isinstance(techs, list) else [],
                }
            )

        raw_skills = data.get("skills", [])
        if isinstance(raw_skills, list):
            skills.update(
                s.lower().strip()
                for s in raw_skills
                if isinstance(s, str) and s.strip()
            )
        elif isinstance(raw_skills, dict):
            for v in raw_skills.values():
                if isinstance(v, list):
                    skills.update(
                        s.lower().strip() for s in v if isinstance(s, str) and s.strip()
                    )

    return {
        "experience": experience,
        "education": education,
        "certifications": certifications,
        "projects": projects,
        "skills": sorted(skills),
    }


def _build_items(data: dict) -> list[dict]:
    """
    Turn collected resume data into a flat list of items for the LLM.

    Each item has: id, type (rewrite|suggest), group, source, text (None for suggest).
    """
    items = []

    for exp in data["experience"][:_MAX_EXP_ROLES]:
        source = f"{exp['title']} at {exp['company']}".strip(" at")
        bullets = exp["bullets"][:_MAX_BULLETS_PER_ROLE]
        if bullets:
            for b in bullets:
                items.append(
                    {
                        "id": f"i{len(items)}",
                        "type": "rewrite",
                        "group": "Experience",
                        "source": source,
                        "text": b,
                    }
                )
        else:
            # Title exists but no bullets - ask LLM to suggest one
            items.append(
                {
                    "id": f"i{len(items)}",
                    "type": "suggest",
                    "group": "Experience",
                    "source": source,
                    "text": None,
                }
            )

    for edu in data["education"][:_MAX_EDU]:
        parts = [
            edu.get("degree", ""),
            edu.get("field", ""),
            edu.get("institution", ""),
        ]
        label = " - ".join(p for p in parts if p)
        if label:
            items.append(
                {
                    "id": f"i{len(items)}",
                    "type": "suggest",
                    "group": "Education",
                    "source": label,
                    "text": None,
                }
            )

    for cert in data["certifications"][:_MAX_CERTS]:
        items.append(
            {
                "id": f"i{len(items)}",
                "type": "suggest",
                "group": "Certifications",
                "source": cert,
                "text": None,
            }
        )

    for proj in data["projects"][:_MAX_PROJECTS]:
        techs = ", ".join(proj["technologies"][:4])
        label = proj["title"] + (f" ({techs})" if techs else "")
        items.append(
            {
                "id": f"i{len(items)}",
                "type": "suggest",
                "group": "Projects",
                "source": label,
                "text": None,
            }
        )

    return items


def _badge(item_type: str, group: str, changed: bool) -> str:
    if item_type == "rewrite":
        return "Improved" if changed else "Unchanged"
    return {
        "Experience": "From Role",
        "Education": "From Education",
        "Certifications": "From Certification",
        "Projects": "From Project",
    }.get(group, "Generated")


def improve_resume(user_id: str, jd_json: dict, gaps: list, strengths: list) -> dict:
    """
    Generate JD-aligned bullets from all stored resumes for a user.

    Works regardless of resume completeness:
    - Has bullets -> rewrites them to better match the JD
    - Has titles only -> suggests bullets grounded in the title and candidate skills
    - Has nothing -> returns ok=False with reason="no_data"

    Args:
        user_id:   user identifier (always "local" for single-user mode)
        jd_json:   structured JD dict from extract_jd()
        gaps:      missing required skills from the last analysis
        strengths: matched required skills (keep these visible in rewrites)

    Returns:
        {ok, sections: [{group, items: [{before, after, badge, source, changed}]}]}
        or {ok: False, reason: str}
    """
    data = _collect_all(user_id)
    items = _build_items(data)

    if not items:
        logger.info("improve_resume: no usable data found across stored resumes")
        return {"ok": False, "reason": "no_data", "sections": []}

    role_title = jd_json.get("job", {}).get("title", "this role")
    responsibilities = jd_json.get("responsibilities", [])[:6]
    gaps_str = ", ".join(gaps[:8]) or "none identified"
    strengths_str = ", ".join(strengths[:6]) or "none"
    resp_str = "; ".join(responsibilities) or "not specified"
    skills_str = ", ".join(data["skills"][:15]) or "not listed"

    lines = []
    for item in items:
        if item["type"] == "rewrite":
            lines.append(
                f'- id:{item["id"]} source:"{item["source"]}" existing:"{item["text"]}"'
            )
        else:
            lines.append(
                f'- id:{item["id"]} source:"{item["source"]}" existing:none (generate from title and candidate skills)'
            )

    prompt = f"""You are a professional resume writer tailoring a candidate's resume for a specific role.

ROLE: {role_title}
ROLE RESPONSIBILITIES: {resp_str}
SKILL GAPS (missing from profile, try to surface if any experience relates): {gaps_str}
MATCHED STRENGTHS (already visible, keep them in rewrites): {strengths_str}
CANDIDATE SKILLS: {skills_str}

For each item produce ONE concise resume bullet (max 20 words):
- existing bullet: rewrite to better align with the role using action verbs and JD language. Stay grounded in what they actually did.
- existing:none: generate a plausible bullet from the source title and the candidate's listed skills. Do not fabricate specific metrics or project names not given.
- Match the language style of the job description.
- No em-dashes. Plain hyphens only if needed.

Items:
{chr(10).join(lines)}

Return ONLY JSON, one object per item:
{{"bullets": [{{"id": "...", "bullet": "..."}}]}}"""

    logger.info(
        "improve_resume: sending %d items to LLM for user=%s", len(items), user_id
    )
    _res = call_llm(prompt, model=state.get_quality_model())
    response = _res.text if (_res and _res.text) else None

    if not response:
        logger.warning("improve_resume: LLM returned no response")
        return {"ok": False, "reason": "llm_failed", "sections": []}

    # JSON-mode shape is {"bullets": [...]}; accept a bare array too for
    # models that ignore the wrapper.
    llm_out = None
    try:
        parsed = json.loads(response)
        llm_out = parsed.get("bullets") if isinstance(parsed, dict) else parsed
    except (ValueError, TypeError):
        arr_match = re.search(r"\[[\s\S]*\]", response)
        if arr_match:
            try:
                llm_out = json.loads(arr_match.group())
            except (ValueError, TypeError):
                pass

    if not isinstance(llm_out, list):
        logger.warning("improve_resume: could not parse bullets from LLM response")
        return {"ok": False, "reason": "parse_failed", "sections": []}

    bullet_map = {
        r["id"]: r.get("bullet", "").strip()
        for r in llm_out
        if isinstance(r, dict) and "id" in r
    }

    groups: dict[str, list] = defaultdict(list)
    for item in items:
        after = bullet_map.get(item["id"], "")
        before = item["text"]
        changed = bool(after) and after != before
        groups[item["group"]].append(
            {
                "before": before,
                "after": after,
                "badge": _badge(item["type"], item["group"], changed),
                "source": item["source"],
                "changed": changed,
            }
        )

    # Only include groups where at least one bullet was generated
    sections = [
        {"group": grp, "items": grp_items}
        for grp, grp_items in groups.items()
        if any(i["after"] for i in grp_items)
    ]

    logger.info(
        "improve_resume: generated %d sections, %d total bullets",
        len(sections),
        sum(len(s["items"]) for s in sections),
    )
    return {"ok": True, "sections": sections}
