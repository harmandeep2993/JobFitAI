# services/ats.py
"""
ATS services: lightweight scan (no LLM) and full LLM-powered optimisation.

ats_check() - deterministic scan: keyword coverage, section flags, formatting warnings.
generate_ats_resume() - LLM pipeline: rewrites the resume to maximise ATS keyword match.
"""

import json
import re

from core import state
from core.logger import get_logger
from services.llm.caller import call_llm
from services.matcher.skill_aliases import build_evidence_corpus, found_in_corpus

logger = get_logger(__name__)

# Standard section headings ATS parsers reliably recognise
_EXPECTED_SECTIONS = [
    {
        "name": "Work Experience",
        "keywords": [
            "experience",
            "work experience",
            "professional experience",
            "employment",
            "work history",
            "career history",
        ],
        "suggestion": "Use 'Work Experience' or 'Professional Experience' as your heading",
    },
    {
        "name": "Education",
        "keywords": [
            "education",
            "academic background",
            "qualifications",
            "academic history",
        ],
        "suggestion": "Use 'Education' as your heading",
    },
    {
        "name": "Skills",
        "keywords": [
            "skills",
            "technical skills",
            "core competencies",
            "expertise",
            "competencies",
        ],
        "suggestion": "Use 'Skills' or 'Technical Skills' as your heading",
    },
    {
        "name": "Contact Information",
        "keywords": ["email", "phone", "@", "linkedin", "contact"],
        "suggestion": "Include email, phone, and LinkedIn URL at the top of your resume",
    },
    {
        "name": "Summary / Profile",
        "keywords": [
            "summary",
            "profile",
            "objective",
            "about me",
            "professional summary",
        ],
        "suggestion": "Add a 2-3 line Professional Summary at the top below contact details",
    },
]


def ats_score(coverage_pct: int | None) -> dict:
    """
    ATS score = keyword coverage % only (0-100).

    Real ATS systems score on exact keyword matches. Sections and formatting
    are pass/fail gates - missing sections or bad formatting causes parsing
    failures, not point deductions. We report them separately as checklists.

    Returns score=None when no JD is provided (cannot score without keywords).

    Args:
        coverage_pct: percentage of required skills found in resume, or None if no JD.

    Returns:
        Dict with score and has_jd flag.
    """
    return {
        "score": coverage_pct,
        "has_jd": coverage_pct is not None,
    }


def exact_coverage(resume_text: str, required_skills: list[str]) -> dict:
    """
    Count how many required skills appear VERBATIM in the resume text.

    Case-insensitive string match - mirrors how most ATS systems score
    keyword presence. Semantic similarity does not count here.

    Returns {matched, missing, total, pct}.
    """
    text_lower = resume_text.lower()
    matched = [s for s in required_skills if s.lower() in text_lower]
    missing = [s for s in required_skills if s.lower() not in text_lower]
    total = len(required_skills)
    pct = round(len(matched) / total * 100) if total else 0
    return {"matched": matched, "missing": missing, "total": total, "pct": pct}


def section_flags(resume_text: str) -> list[dict]:
    """
    Check for ATS-expected section headings in the resume text.

    Returns a list of {name, found, suggestion} - suggestion is None when
    the section is present. Missing sections are flagged for the user to add.
    """
    text_lower = resume_text.lower()
    flags = []
    for sec in _EXPECTED_SECTIONS:
        found = any(kw in text_lower for kw in sec["keywords"])
        flags.append(
            {
                "name": sec["name"],
                "found": found,
                "suggestion": None if found else sec["suggestion"],
            }
        )
    return flags


def formatting_flags(resume_text: str) -> list[str]:
    """
    Detect patterns in the extracted plain text that commonly cause ATS
    parse failures. Works on the text layer only - cannot catch graphical
    elements (images, icons, header/footer boxes) that disappeared during
    extraction, but flags what survives.
    """
    flags = []

    # Box-drawing / table characters - signs a table was used
    if re.search(r"[|+-]", resume_text) and re.search(
        r"[│├─┼┤┌┐└┘╔╗╚╝║═]", resume_text
    ):
        flags.append(
            "Table or box-drawing characters detected - ATS parsers often skip table content entirely. Use plain bullet points."
        )

    # Heavy decorative symbols
    if re.search(r"[★●◆▶►◄▲▼□■✓✗✔✘]", resume_text):
        flags.append(
            "Decorative symbols detected - replace with plain hyphens (-) or asterisks (*) for reliable ATS parsing."
        )

    # Non-ASCII runs that may confuse parsers
    non_ascii = re.findall(r"[^\x00-\x7F]{2,}", resume_text)
    if len(non_ascii) > 5:
        flags.append(
            "Extended Unicode characters detected - some ATS systems cannot parse them. Use plain ASCII where possible."
        )

    # All-caps overuse (common in styled resumes; some ATS miss them)
    caps_count = len(re.findall(r"\b[A-Z]{5,}\b", resume_text))
    if caps_count > 10:
        flags.append(
            "Heavy use of ALL CAPS text - some ATS systems fail to normalise it for keyword matching. Use mixed case."
        )

    # Very long lines suggest multi-column layout
    long_lines = [ln for ln in resume_text.split("\n") if len(ln) > 130]
    if len(long_lines) > 4:
        flags.append(
            "Multiple very long lines detected - likely indicates a multi-column layout. ATS parsers read left-to-right and will scramble column content."
        )

    # Email present check (common ATS rejection reason)
    if not re.search(r"[\w.+-]+@[\w-]+\.\w+", resume_text):
        flags.append(
            "No email address detected in the extracted text - ensure your email is in plain text, not inside a header image or text box."
        )

    return flags


def ats_check(resume_text: str, required_skills: list[str] | None = None) -> dict:
    """
    Lightweight scan - no LLM, no resume generation.
    Returns section flags, formatting flags, and a composite ATS score.
    When required_skills is provided (JD was pasted), keyword coverage is
    included in the score calculation.
    """
    sec_flags = section_flags(resume_text)
    fmt_flags = formatting_flags(resume_text)
    coverage = exact_coverage(resume_text, required_skills) if required_skills else None
    score = ats_score(coverage["pct"] if coverage else None)
    return {
        "section_flags": sec_flags,
        "formatting_flags": fmt_flags,
        "coverage": coverage,
        "ats_score": score,
    }


# === ATS resume generation ===

_GENERATE_PROMPT = """You are an expert resume writer specialising in ATS (Applicant Tracking System) optimisation.

TASK: Rewrite the resume below so a recruiter searching for this job's keywords finds it, WITHOUT ever claiming something the candidate has not done.

THE ONE UNBREAKABLE RULE: every fact in your output must be traceable to the resume.
- Never add a skill the resume does not evidence, even if the job demands it. A missing skill stays missing.
- Never invent an employer, job title, date, degree, metric, or achievement.
- Never inflate scope ("led a team" when the resume says "worked in a team").
- You MAY re-word, re-order, and surface things already buried in the text.
- If the candidate demonstrably used a skill inside a bullet, you MAY also list it in Skills. That is surfacing, not inventing.

TARGET KEYWORDS (use ONLY those the resume genuinely supports): {keywords}

STYLE:
- Mirror the job description's exact wording wherever it truthfully applies, so keyword search matches.
- Start each bullet with a strong action verb; keep the candidate's real numbers.
- Standard ATS headings, plain text, no tables or columns or symbols.

Return ONLY a JSON object with this exact shape:

{
  "contact": {
    "name": "candidate's full name exactly as written in the resume",
    "email": "email from the resume, or empty",
    "phone": "phone from the resume, or empty",
    "location": "city, country, or empty",
    "links": ["linkedin/github/portfolio urls from the resume"]
  },
  "summary": "2-3 sentence professional summary using JD keywords the candidate genuinely supports",
  "experience": [
    {
      "title": "Job Title",
      "company": "Company Name",
      "dates": "Start - End",
      "bullets": ["bullet 1", "bullet 2"]
    }
  ],
  "skills": ["skill1", "skill2"],
  "education": [
    {
      "degree": "Degree Name",
      "institution": "University",
      "year": "2020"
    }
  ]
}

RESUME:
{resume}

JOB DESCRIPTION:
{jd}
"""

_PLAIN_TEXT_TEMPLATE = """{contact}

SUMMARY
{summary}

WORK EXPERIENCE
{experience}

SKILLS
{skills}

EDUCATION
{education}"""


def _render_plain_text(parsed: dict) -> str:
    """Convert the parsed LLM JSON into a plain-text resume string."""
    exp_lines = []
    for job in parsed.get("experience") or []:
        header = f"{job.get('title', '')} | {job.get('company', '')} | {job.get('dates', '')}"
        exp_lines.append(header)
        for b in job.get("bullets") or []:
            exp_lines.append(f"- {b}")
        exp_lines.append("")

    edu_lines = []
    for edu in parsed.get("education") or []:
        edu_lines.append(
            f"{edu.get('degree', '')} - {edu.get('institution', '')} ({edu.get('year', '')})"
        )

    # Contact block first: an ATS that finds no email has nothing to attach the
    # application to, and the user must see it in the preview before downloading.
    contact = parsed.get("contact") or {}
    contact_bits = [
        (contact.get("email") or "").strip(),
        (contact.get("phone") or "").strip(),
        (contact.get("location") or "").strip(),
    ]
    contact_bits += [
        link.strip()
        for link in (contact.get("links") or [])
        if isinstance(link, str) and link.strip()
    ]
    contact_lines = [(contact.get("name") or "").strip()]
    joined = " | ".join(b for b in contact_bits if b)
    if joined:
        contact_lines.append(joined)

    return _PLAIN_TEXT_TEMPLATE.format(
        contact="\n".join(ln for ln in contact_lines if ln),
        summary=parsed.get("summary", ""),
        experience="\n".join(exp_lines).strip(),
        skills=", ".join(parsed.get("skills") or []),
        education="\n".join(edu_lines),
    )


def _verify_against_source(parsed: dict, resume_text: str) -> tuple[dict, list[str]]:
    """Strip anything the generated resume claims that the original does not support.

    "Never invent anything" is an instruction, not a guarantee - so it is enforced
    here rather than trusted. Two checks, both against the raw resume text:

      skills     - a skill must appear in the source (via the same alias-aware
                   evidence search used for scoring). An invented skill is the
                   most damaging fabrication: it is exactly what a recruiter
                   screens on, and the candidate cannot defend it in interview.
      employers  - a company or job title that appears nowhere in the source is
                   a fabricated role and is removed entirely.

    Bullets are not machine-verifiable (they are prose about real work), so they
    are left to the prompt and to the user's review of the before/after diff.

    Returns (cleaned_resume, removed) where removed names what was stripped.
    """
    corpus = build_evidence_corpus({"text": resume_text})
    removed: list[str] = []

    skills = []
    for skill in parsed.get("skills") or []:
        if not isinstance(skill, str) or not skill.strip():
            continue
        if found_in_corpus(skill, corpus) or skill.lower().strip() in corpus:
            skills.append(skill)
        else:
            removed.append(f"skill: {skill}")
            logger.warning("ATS generate: dropped unsupported skill '%s'", skill)
    parsed["skills"] = skills

    experience = []
    for job in parsed.get("experience") or []:
        if not isinstance(job, dict):
            continue
        company = (job.get("company") or "").lower().strip()
        title = (job.get("title") or "").lower().strip()
        # One of the two must appear verbatim in the source for the role to be real.
        if (company and company in corpus) or (title and title in corpus):
            experience.append(job)
        else:
            label = f"{job.get('title', '?')} at {job.get('company', '?')}"
            removed.append(f"role: {label}")
            logger.warning("ATS generate: dropped unsupported role '%s'", label)
    parsed["experience"] = experience

    return parsed, removed


def generate_ats_resume(resume_text: str, jd_text: str) -> dict | None:
    """
    Generate a complete ATS-optimised resume via LLM.

    Extracts required skills from the JD first (via LLM), then runs ats_check
    before and after rewriting so the caller gets a real before/after coverage delta.

    Returns {resume, plain_text, coverage_before, coverage_after,
             section_flags, formatting_flags} or None if the LLM is unavailable.
    """
    from services.extractors.jd_extractor import extract_jd

    # Extract JD skills first so we can compute coverage_before against real keywords
    jd_json = extract_jd(jd_text)
    required_skills = (jd_json.get("required_skills") or []) if jd_json else []

    sec_flags = section_flags(resume_text)
    fmt_flags = formatting_flags(resume_text)
    coverage_before = (
        exact_coverage(resume_text, required_skills) if required_skills else None
    )

    # Give the writer the exact keywords to aim for - it may still only use the
    # ones the resume genuinely supports.
    keywords = ", ".join(required_skills[:20]) or "none extracted"
    # str.format() cannot be used here: the prompt embeds a literal JSON schema,
    # and format() reads its braces as placeholders (KeyError '"contact"').
    prompt = (
        _GENERATE_PROMPT.replace("{keywords}", keywords)
        .replace("{resume}", resume_text[:6000])
        .replace("{jd}", jd_text[:3000])
    )
    _res = call_llm(prompt, model=state.get_quality_model())
    if not _res or not _res.text:
        return None

    # Extract JSON block from LLM response
    raw = _res.text.strip()
    try:
        start = raw.index("{")
        end = raw.rindex("}") + 1
        parsed = json.loads(raw[start:end])
    except (ValueError, json.JSONDecodeError) as e:
        logger.warning("ATS generate: failed to parse LLM JSON: %s", e)
        return None

    # Enforce the no-fabrication rule instead of trusting it.
    parsed, removed = _verify_against_source(parsed, resume_text)

    plain_text = _render_plain_text(parsed)
    coverage_after = (
        exact_coverage(plain_text, required_skills) if required_skills else None
    )

    return {
        "resume": parsed,
        "plain_text": plain_text,
        "coverage_before": coverage_before,
        "coverage_after": coverage_after,
        "section_flags": sec_flags,
        "formatting_flags": fmt_flags,
        # Anything the model claimed that the source resume does not support.
        "removed_unsupported": removed,
    }
