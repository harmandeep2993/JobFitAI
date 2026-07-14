# prompts/resume_prompt.py
"""
Prompt builder for resume extraction.

Loads the resume schema and builds a structured extraction prompt
for the LLM to return a JSON object matching the schema.
"""

import json
from pathlib import Path

from core.logger import get_logger

logger = get_logger(__name__)

RESUME_SCHEMA_PATH = Path(__file__).parent / "schemas" / "resume_schema.json"


def _get_resume_schema() -> str:
    """
    Load and return the resume schema as a formatted JSON string.

    Returns:
        str: Resume schema as indented JSON string

    Raises:
        FileNotFoundError: If schema file does not exist
    """
    with open(RESUME_SCHEMA_PATH, "r") as f:
        schema = json.load(f)

    # Compact separators - indentation costs ~100 prompt tokens per call
    # and the LLM needs none of it.
    schema_text = json.dumps(schema, separators=(",", ":"))

    logger.info("Loaded resume schema from %s", RESUME_SCHEMA_PATH)
    logger.info("Resume schema length: %d characters", len(schema_text))

    return schema_text


def get_resume_prompt(resume_text: str) -> str:
    """Build the prompt for resume extraction.
    Args:
        resume_text (str): The raw text of the resume to be extracted.
    Returns:
        str: The complete prompt to be sent to the LLM for extraction.
    """
    schema_text = _get_resume_schema()

    prompt = f"""Extract resume data into this JSON schema. The <hints> in the schema describe each field - replace every <hint> with extracted data.

    RULES:
    1. Return ONLY valid minified JSON. Use "" / [] / 0 for anything absent - never output a <hint> or an empty template object.
    2. NEVER extract contact details. The candidate's name, email address, phone number, postal address, date of birth, and personal URLs must not appear anywhere in the output - not in candidate, not inside a bullet, not in a project description. Scoring never needs them.
    3. Resume may be in any language - return ALL values in English.
    4. skills: scan EVERY section (experience bullets, projects, summary, certifications, publications) - every technology, tool, framework, platform, methodology, and domain expertise mentioned anywhere goes in skills[]. Use canonical lowercase names: expand abbreviations (k8s -> kubernetes, js -> javascript, ml -> machine learning, nlp -> natural language processing, gcp -> google cloud, postgres -> postgresql) but keep standard acronyms (aws, sql, etl, sap). No duplicate spellings. Skills are THINGS, not traits: never list personality traits or work styles ("team player", "motivated", "detail-oriented", "communication skills").
    5. experience_entries: ALL roles count - jobs, internships, working student, freelance, research positions, teaching, volunteer work.
    6. Dates: normalize to MM/YYYY (or YYYY when no month is shown); ongoing roles get end_date "present". Never invent dates. Always set duration_years and meta.total_experience_years to 0 - they are computed after extraction.
    7. Academic profiles: research positions belong in experience_entries, papers in publications[], thesis title in education. Vocational profiles: an Ausbildung/apprenticeship goes in education AND its work placement as an experience entry with employment_type "apprenticeship".

    SCHEMA:
    {schema_text}

    RESUME:
    {resume_text}

    JSON:"""

    logger.info("Resume length: %d characters", len(resume_text))
    logger.info("Resume prompt length: %d characters", len(prompt))
    logger.info("Resume prompt ready")

    return prompt
