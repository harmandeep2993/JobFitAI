# services/extractors/jd_extractor.py
"""
Job Description extraction module.

Uses an LLM to extract structured requirements from a job description.
Includes light post-processing to normalize all string values to lowercase.
"""

from services.prompts import get_jd_prompt
from core.config import JD_MAX_CHARS
from core.logger import get_logger
from services.llm.caller import call_llm, parse_json_response

logger = get_logger(__name__)


def _lowercase_all(data):
    """
    Recursively convert all string values in a nested structure to lowercase.

    Args:
        data: dict, list, or str - any nested combination

    Returns:
        Same structure with all strings lowercased and stripped
    """
    if isinstance(data, str):
        return data.lower().strip()

    elif isinstance(data, list):
        return [_lowercase_all(v) for v in data]

    elif isinstance(data, dict):
        return {k: _lowercase_all(v) for k, v in data.items()}

    else:
        # Numbers, booleans, None - preserved as-is (expected, not an error).
        return data


_JD_FIELD_TYPES: dict = {
    "job": dict,
    "required_skills": list,
    "preferred_skills": list,
    "responsibilities": list,
    "experience_requirements": list,
    "education_requirements": list,
    "languages": list,
    "certifications": list,
    "job_summary": str,
}

_JD_DEFAULTS: dict = {
    "job": {},
    "required_skills": [],
    "preferred_skills": [],
    "responsibilities": [],
    "experience_requirements": [],
    "education_requirements": [],
    "languages": [],
    "certifications": [],
    "job_summary": "",
}


def _validate_jd_schema(result: dict) -> dict:
    """Ensure all required JD fields exist with correct types; fill gaps with safe defaults."""
    for field, expected_type in _JD_FIELD_TYPES.items():
        val = result.get(field)
        if val is None:
            logger.debug("JD field '%s' missing - using default", field)
            result[field] = _JD_DEFAULTS[field]
        elif not isinstance(val, expected_type):
            logger.warning(
                "JD field '%s' has unexpected type %s (expected %s) - using default",
                field,
                type(val).__name__,
                expected_type.__name__,
            )
            result[field] = _JD_DEFAULTS[field]
    return result


def _is_empty(value) -> bool:
    """
    Check if a value is considered empty.

    Args:
        value: Any extracted field value

    Returns:
        bool: True if value is empty, False otherwise
    """
    return value in ("", None, [], {}, [""]) or value == [None]


def extract_jd(jd_text: str) -> dict:
    """
    Extract structured JD data from raw text using an LLM.

    Pipeline:
        1. Truncate input to JD_MAX_CHARS
        2. Build prompt
        3. Call LLM
        4. Parse JSON response
        5. Normalize all values to lowercase

    Args:
        jd_text (str): Raw job description text

    Returns:
        dict: Structured JD data with all string values lowercased

    Raises:
        ValueError: If LLM response is not a valid dict
    """
    if not jd_text or not jd_text.strip():
        logger.warning("Empty JD text received - returning empty dict")
        return {}

    # Truncate to max allowed chars - safeguard for LLM input limits
    if len(jd_text) > JD_MAX_CHARS:
        logger.warning(
            "JD text truncated from %d to %d characters", len(jd_text), JD_MAX_CHARS
        )
        jd_text = jd_text[:JD_MAX_CHARS]

    prompt = get_jd_prompt(jd_text)
    # Extraction is structured parsing, not reasoning - and the fetch pipeline
    # runs one of these per job, so it must stay fast and cheap.
    _res = call_llm(prompt)
    response = _res.text if (_res and _res.text) else None
    result = parse_json_response(response)

    if not isinstance(result, dict):
        logger.error("LLM response is not a dict: %s", result)
        raise ValueError("Invalid LLM response format")

    result = _validate_jd_schema(result)
    result = _lowercase_all(result)

    empty_keys = [k for k, v in result.items() if _is_empty(v)]
    if empty_keys:
        logger.debug("Empty JD fields: %s", empty_keys)

    n_req = len(result.get("required_skills", []))
    n_resp = len(result.get("responsibilities", []))
    logger.info("JD extracted: %d required skills, %d responsibilities", n_req, n_resp)
    return result
