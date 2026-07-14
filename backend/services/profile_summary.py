# services/profile_summary.py
"""
Generates the LLM-powered candidate summary shown in the Summary tab.

Called after scoring so the narrative can reference concrete match data
(which required skills were matched/missing, section score breakdowns).
Produces structured JSON with four sections: profile, strengths, gaps, focus.
Falls back to a deterministic template if the LLM call fails.
"""

from core import state
from services.llm.caller import call_llm


def _fmt_score(value) -> str:
    """Format a section score for LLM prompts; None means the JD had no data."""
    return "n/a" if value is None else str(value)


def generate_summary(resume_json, jd_json, results):
    """
    Generate a concise candidate summary using LLM.
    Called after scoring so narrative includes score data.

    Args:
        resume_json (dict): Extracted resume data
        jd_json (dict): Extracted JD data
        results (dict): get_match_score() output

    Returns:
        str: 3-4 sentence summary or fallback text
    """
    # Build focused context - no raw text, only structured data.
    # Keys must match match() output (overall_score, section_scores,
    # matched_*/missing_*) and the resume/jd JSON schemas (nested
    # candidate/job objects, meta.total_experience_years).
    candidate_years = resume_json.get("meta", {}).get("total_experience_years", 0)
    section_scores = results.get("section_scores", {})
    gates = results.get("gates") or {}
    exp_gate = gates.get("experience") or {}

    context = {
        "score": results.get("overall_score", 0),
        "label": results.get("label", ""),
        "matched_skills": results.get("matched_required", []),
        "missing_skills": results.get("missing_required", []),
        "matched_pref": results.get("matched_preferred", []),
        "missing_pref": results.get("missing_preferred", []),
        "candidate_years": candidate_years,
        # Parsed from the JD by the experience gate; 0 when the JD states none
        "required_years": exp_gate.get("required_years", 0),
        "missing_duties": results.get("missing_duties", []),
        "breakdown": {
            "required_skills": section_scores.get("required_skills", 0),
            "responsibilities": section_scores.get("responsibilities", 0),
            "education": section_scores.get("education", 0),
        },
        "candidate": {
            "title": resume_json.get("candidate", {}).get("title", ""),
            "years": candidate_years,
            "education": [
                f"{e.get('degree', '')} {e.get('field', '')}"
                for e in resume_json.get("education", [])
            ],
            "languages": resume_json.get("languages", []),
            "skills": _flatten_skills(resume_json.get("skills", [])),
        },
        "role": {
            "title": jd_json.get("job", {}).get("title", "this role"),
            "required_skills": jd_json.get("required_skills", []),
            "required_edu": jd_json.get("education_requirements", []),
        },
    }

    role = context["role"]["title"]
    matched_skills = ", ".join(context["matched_skills"][:6]) or "none identified"
    missing_skills = ", ".join(context["missing_skills"][:4]) or "none"
    missing_pref = ", ".join(context["missing_pref"][:3]) or "none"
    candidate_years = context["candidate_years"]
    required_years = context["required_years"]
    edu_score = context["breakdown"]["education"]
    resp_score = context["breakdown"]["responsibilities"]
    overall_fit = (
        "strong match"
        if context["score"] >= 80
        else "solid foundation with a few gaps"
        if context["score"] >= 60
        else "partial match with notable gaps"
        if context["score"] >= 40
        else "significant gaps to close"
    )

    def _band(score: float | None) -> str:
        """Label a section score; None means the JD said nothing about it."""
        if score is None:
            return "not specified in the JD"
        if score >= 70:
            return "strong"
        return "moderate" if score >= 40 else "weak"

    # Experience is a gate, not a score: report whether the bar is cleared.
    if not required_years:
        exp_lbl = "no explicit requirement stated"
    elif candidate_years >= required_years:
        exp_lbl = f"meets the {required_years}+ years required"
    else:
        exp_lbl = f"below the {required_years}+ years required"

    edu_lbl = _band(edu_score)
    resp_lbl = _band(resp_score)

    prompt = f"""You are generating a candidate-facing job fit analysis for JobsFitAI.

ROLE: {role}
OVERALL FIT: {overall_fit}
CANDIDATE EXPERIENCE: {candidate_years} years ({exp_lbl} alignment)
MATCHED REQUIRED SKILLS: {matched_skills}
MISSING REQUIRED SKILLS: {missing_skills}
MISSING PREFERRED SKILLS: {missing_pref}
EDUCATION ALIGNMENT: {edu_lbl}
CV-TO-JD LANGUAGE MATCH: {resp_lbl}

Output ONLY a valid JSON object with exactly these 4 keys. No markdown, no code fences, no commentary:
{{
  "profile":   ["bullet 1", "bullet 2"],
  "strengths": ["bullet 1", "bullet 2"],
  "gaps":      ["bullet 1", "bullet 2"],
  "focus":     ["bullet 1", "bullet 2"]
}}

Rules:
- 2-4 items per section (focus: 1-3)
- Each item is a plain English string
- Wrap specific skills, tools, and qualifications in <strong>...</strong> within each string
- profile: candidate title, years of experience, education - what they bring overall
- strengths: concrete things the candidate has that this role specifically needs
- gaps: concrete missing skills or requirements from the JD - name them explicitly
- focus: specific actionable steps tied directly to the real gaps
- No percentages, no em-dashes, no emojis, no score numbers
- Be specific - name actual skills, not vague phrases"""

    _res = call_llm(prompt, model=state.get_quality_model())
    response = _res.text if (_res and _res.text) else None

    if response and len(response.strip()) > 20:
        import json
        import re

        cleaned = re.sub(r"^```(?:json)?\s*", "", response.strip(), flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        match_obj = re.search(r"\{[\s\S]*\}", cleaned)
        if match_obj:
            try:
                data = json.loads(match_obj.group())
                required_keys = {"profile", "strengths", "gaps", "focus"}
                if required_keys.issubset(data.keys()):
                    for k in required_keys:
                        if not isinstance(data[k], list):
                            data[k] = []
                        data[k] = [str(x) for x in data[k][:4]]
                    return json.dumps(data)
            except (json.JSONDecodeError, ValueError):
                pass

    return _fallback_summary(context)


def _flatten_skills(skills_dict):
    """Flatten skills - handles both dict and list format."""
    if isinstance(skills_dict, list):
        return skills_dict[:15]
    if isinstance(skills_dict, dict):
        all_skills = []
        for category_skills in skills_dict.values():
            if isinstance(category_skills, list):
                all_skills.extend(category_skills)
        return all_skills[:15]
    return []


def generate_swot(resume_json, jd_json, results):
    """
    Generate a SWOT analysis for the candidate vs this role.

    Returns:
        dict: {strengths, weaknesses, opportunities, threats} - each a list of strings
    """
    import json
    import re

    candidate_years = resume_json.get("meta", {}).get("total_experience_years", 0)
    section_scores = results.get("section_scores", {})
    matched_req = results.get("matched_required", [])
    missing_req = results.get("missing_required", [])
    matched_pref = results.get("matched_preferred", [])
    missing_pref = results.get("missing_preferred", [])
    score = results.get("overall_score", 0)
    c_langs = resume_json.get("languages", [])
    r_langs = jd_json.get("languages", [])
    role_title = jd_json.get("job", {}).get("title", "this role")

    prompt = f"""You are producing a SWOT analysis for a job candidate applying to a specific role.

ROLE: {role_title}
OVERALL SCORE: {score}/100
CANDIDATE EXPERIENCE: {candidate_years} years
SECTION SCORES: required_skills={_fmt_score(section_scores.get('required_skills'))}, responsibilities={_fmt_score(section_scores.get('responsibilities'))}, education={_fmt_score(section_scores.get('education'))}

MATCHED REQUIRED SKILLS: {', '.join(matched_req[:8]) or 'none'}
MISSING REQUIRED SKILLS:  {', '.join(missing_req[:8]) or 'none'}
MATCHED PREFERRED SKILLS: {', '.join(matched_pref[:5]) or 'none'}
MISSING PREFERRED SKILLS: {', '.join(missing_pref[:5]) or 'none'}
CANDIDATE LANGUAGES: {', '.join(c_langs) or 'not specified'}
REQUIRED LANGUAGES:  {', '.join(r_langs) or 'none specified'}

Produce a SWOT analysis with exactly 3 bullet points per quadrant.
Each bullet is a short phrase (5-12 words), no full sentences, no numbers, no percentages, no em dashes.

Strengths:    what the candidate clearly has that this specific role needs
Weaknesses:   concrete gaps relative to this role (not generic advice)
Opportunities:actionable ways to improve candidacy or grow in the role
Threats:      external or competitive factors that could hinder their application

Return ONLY valid JSON, nothing else:
{{
  "strengths":     ["phrase 1", "phrase 2", "phrase 3"],
  "weaknesses":    ["phrase 1", "phrase 2", "phrase 3"],
  "opportunities": ["phrase 1", "phrase 2", "phrase 3"],
  "threats":       ["phrase 1", "phrase 2", "phrase 3"]
}}"""

    _res = call_llm(prompt, model=state.get_quality_model())
    response = _res.text if (_res and _res.text) else None

    if response:
        match_obj = re.search(r"\{[\s\S]*\}", response)
        if match_obj:
            try:
                data = json.loads(match_obj.group())
                required_keys = {"strengths", "weaknesses", "opportunities", "threats"}
                if required_keys.issubset(data.keys()):
                    for key in required_keys:
                        if not isinstance(data[key], list):
                            data[key] = []
                        data[key] = [str(item) for item in data[key][:4]]
                    return data
            except (json.JSONDecodeError, ValueError):
                pass

    return _fallback_swot(
        matched_req, missing_req, matched_pref, missing_pref, score, candidate_years
    )


def _fallback_swot(matched_req, missing_req, matched_pref, missing_pref, score, c_yrs):
    strengths = []
    if matched_req:
        strengths.append(f"Matches required skills: {', '.join(matched_req[:3])}")
    if c_yrs >= 3:
        strengths.append(f"{c_yrs} years of relevant professional experience")
    if matched_pref:
        strengths.append(f"Preferred skills aligned: {', '.join(matched_pref[:2])}")
    if not strengths:
        strengths = ["Some relevant background for this role"]

    weaknesses = []
    if missing_req:
        weaknesses.append(f"Missing required skills: {', '.join(missing_req[:3])}")
    if score < 60:
        weaknesses.append("Overall profile alignment is below target threshold")
    if missing_pref:
        weaknesses.append(f"Preferred skills absent: {', '.join(missing_pref[:2])}")
    if not weaknesses:
        weaknesses = ["Minor gaps in preferred qualifications"]

    opportunities = [
        "Tailor CV language to mirror job description keywords",
        "Acquire missing skills via online courses or side projects",
        "Highlight transferable experience more prominently in CV",
    ]
    threats = [
        "Other candidates may have closer skills alignment",
        "ATS screening may filter on missing required skills",
        "High competition from specialists in this domain",
    ]

    return {
        "strengths": strengths[:3],
        "weaknesses": weaknesses[:3],
        "opportunities": opportunities,
        "threats": threats,
    }


def _fallback_summary(ctx):
    import json

    score = ctx["score"]
    matched = ctx["matched_skills"]
    missing = ctx["missing_skills"]
    c_yrs = ctx["candidate_years"]
    title = ctx["candidate"]["title"]

    def bold(t):
        return f"<strong>{t}</strong>"

    profile = []
    if title:
        profile.append(f"Currently working as {bold(title)}")
    if c_yrs:
        profile.append(f"{bold(str(c_yrs) + ' years')} of professional experience")
    if not profile:
        profile.append("Profile details extracted from resume")

    strengths = [
        f"{bold(s)} is present in your profile and required by this role"
        for s in matched[:3]
    ]
    if not strengths:
        strengths = ["No direct required skill matches were identified"]

    gaps = [
        f"{bold(s)} is required by the role but not visible in your profile"
        for s in missing[:3]
    ]
    if not gaps:
        gaps = ["No critical skill gaps identified"]

    if score >= 60:
        focus = [
            "Tailor your CV to mirror the job description language exactly",
            "Apply with a cover letter that highlights your matched skills",
        ]
    else:
        focus = [
            f"Build hands-on experience in {bold(missing[0])} and similar skills"
            if missing
            else "Address the identified skill gaps through courses or projects",
            "Revisit this role once the core required skills are covered",
        ]

    return json.dumps(
        {
            "profile": profile,
            "strengths": strengths,
            "gaps": gaps,
            "focus": focus,
        }
    )
