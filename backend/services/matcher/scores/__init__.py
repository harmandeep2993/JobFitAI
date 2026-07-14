# services/matcher/scores/__init__.py
"""
Scored sections of the matcher - the parts of a JD a candidate can act on.

Years of experience and language level are NOT scorers: they are pass/fail
gates (services/matcher/gates.py), not weighted points. Responsibility coverage
is LLM-judged (services/matcher/responsibility_coverage.py) because cosine
similarity provably cannot separate a genuine match from an unrelated one.
"""

from .certifications import score_certifications
from .education import score_education
from .skills import score_preferred_skills, score_required_skills

__all__ = [
    "score_required_skills",
    "score_preferred_skills",
    "score_education",
    "score_certifications",
]
