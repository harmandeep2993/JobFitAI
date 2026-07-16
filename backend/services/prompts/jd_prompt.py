# prompts/jd_prompt.py
"""
Prompt builder for job description extraction.

Loads the JD schema and builds a structured extraction prompt
for the LLM to return a JSON object matching the schema.
"""

import json
from pathlib import Path

from core.logger import get_logger

logger = get_logger(__name__)

# Absolute path so it resolves correctly regardless of working directory
JD_SCHEMA_PATH = Path(__file__).parent / "schemas" / "jd_schema.json"


def _get_jd_schema() -> str:
    """
    Load and return the JD schema as a formatted JSON string.

    Returns:
        str: JD schema as indented JSON string

    Raises:
        FileNotFoundError: If schema file does not exist
    """
    with open(JD_SCHEMA_PATH, "r") as f:
        schema = json.load(f)

    # Compact separators - indentation costs prompt tokens the LLM ignores.
    schema_text = json.dumps(schema, separators=(",", ":"))

    logger.info("Loaded JD schema from %s", JD_SCHEMA_PATH)
    logger.info("JD schema length: %d characters", len(schema_text))

    return schema_text


def get_jd_prompt(jd_text: str) -> str:
    """Build the LLM extraction prompt for a raw job description string."""
    schema_text = _get_jd_schema()

    prompt = f"""Extract job description data into this JSON schema. Follow all rules strictly.

    RULES:
    1. Return ONLY valid JSON. No markdown, no explanation, no extra text.
    2. JD may be in any language - extract and return ALL values in English.
    3. required_skills: scan the ENTIRE JD - not just a "Requirements" section. Extract every skill, tool, technology, framework, platform, methodology, or domain competency that is stated as required or essential, or implied by responsibilities. Split compound entries into individual items (e.g. "Python incl. Pydantic-AI, LangGraph" -> ["python", "pydantic-ai", "langgraph"]). Write each skill in its canonical lowercase industry name: expand abbreviations ("k8s" -> "kubernetes", "js" -> "javascript", "ts" -> "typescript", "ml" -> "machine learning", "nlp" -> "natural language processing", "gcp" -> "google cloud", "sklearn" -> "scikit-learn", "postgres" -> "postgresql") but keep acronyms that ARE the standard name ("aws", "sql", "etl", "sap"). Never list the same skill twice in different spellings.
    4. preferred_skills: bonus, nice-to-have, or "would be a plus" skills only. Do not repeat items already in required_skills.
    4b. SKILLS ARE THINGS, NOT TRAITS. required_skills and preferred_skills contain only concrete, checkable competencies: technologies, tools, frameworks, platforms, languages, methodologies, and domain expertise. NEVER include personality traits, attitudes, or work styles ("team-oriented", "pragmatic", "motivated", "curious", "interest in AI trends", "hands-on mentality", "communication skills"). Those cannot be matched against a resume and must be omitted entirely.
    5. responsibilities: split into individual action items. Each item should be a single clear task or duty. Translate to English.
    6. experience_requirements: capture EVERY experience statement, whether it names a number or not. Numeric ("3+ years of Python"), non-numeric seniority ("several years of experience", "proven track record", "extensive experience", "mehrjaehrige/einschlaegige Berufserfahrung", "fundierte Kenntnisse"), AND entry signals ("no prior experience required", "Berufseinsteiger willkommen", "erste Berufserfahrung", "graduates welcome") all go here verbatim. This field is how seniority is judged, so do not drop a phrase just because it has no number. Empty list only if the JD says nothing about experience.
    7. education_requirements: explicit degree or field statements only (e.g. "Bachelor in Computer Science", "Master preferred"). Empty list if none stated.
    8. languages: for each language extract BOTH the name into "language" AND the exact proficiency level stated into "proficiency" (use the form given: native, fluent, C1, B2, B1, intermediate, basic, or CEFR code). Leave proficiency as "" if not stated. Leave languages as [] if no language requirements exist.
    9. certifications: extract all required or preferred professional certifications AND online credentials (Coursera, Udemy, edX, AWS, Google, Microsoft certificates, etc.) as plain strings.
    10. work_mode: remote|hybrid|on-site|not specified
    11. employment_type: full-time|part-time|contract|internship|not specified
    12. job_level: junior|mid-level|senior|lead|principal|staff|not specified
    13. job_summary: 1-2 sentences describing the ROLE and KEY RESPONSIBILITIES only. Not the company intro. Write in English.
    14. Missing fields -> empty string, empty list.

    SCHEMA:
    {schema_text}

    JOB DESCRIPTION:
    {jd_text}

    JSON:"""

    logger.info("JD length: %d characters", len(jd_text))
    logger.info("JD prompt length: %d characters", len(prompt))
    logger.info("JD prompt ready")

    return prompt
