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

TASK: Rewrite the resume below so it passes ATS keyword matching for the job description provided.

RULES:
- Keep all real experience, education, and skills - never invent anything
- Mirror exact keywords and phrases from the job description wherever they truthfully apply
- Use standard ATS-friendly section headings: Summary, Work Experience, Education, Skills
- Write clean plain text - no tables, no columns, no decorative symbols
- Each bullet point should start with a strong action verb
- Return ONLY a JSON object with this exact shape:

{
  "summary": "2-3 sentence professional summary using JD keywords",
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

_PLAIN_TEXT_TEMPLATE = """{summary}

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

    return _PLAIN_TEXT_TEMPLATE.format(
        summary=parsed.get("summary", ""),
        experience="\n".join(exp_lines).strip(),
        skills=", ".join(parsed.get("skills") or []),
        education="\n".join(edu_lines),
    )


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

    prompt = _GENERATE_PROMPT.format(resume=resume_text[:6000], jd=jd_text[:3000])
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
    }
