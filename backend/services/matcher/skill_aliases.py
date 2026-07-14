# services/matcher/skill_aliases.py
"""
Skill normalization, alias resolution, and full-resume evidence search.

Solves two matcher blind spots:
- Alias mismatch: 'k8s' vs 'kubernetes' embed far apart and fail the
  similarity threshold even though they are the same skill.
- Buried evidence: a skill used in an experience bullet ("deployed with
  Docker") but absent from the skills section was reported missing.
"""

import re

# Variant -> canonical form. Both sides of a comparison are normalized
# through this map, so the canonical choice only has to be consistent.
# Keep keys and values lowercase.
ALIASES = {
    # Infrastructure / cloud
    "k8s": "kubernetes",
    "k8": "kubernetes",
    "gcp": "google cloud",
    "google cloud platform": "google cloud",
    "amazon web services": "aws",
    "microsoft azure": "azure",
    "ci/cd": "ci cd",
    "cicd": "ci cd",
    "ci-cd": "ci cd",
    "iac": "infrastructure as code",
    # Languages / frameworks
    "js": "javascript",
    "ts": "typescript",
    "reactjs": "react",
    "react.js": "react",
    "nodejs": "node.js",
    "node": "node.js",
    "vuejs": "vue",
    "vue.js": "vue",
    "nextjs": "next.js",
    "expressjs": "express",
    "express.js": "express",
    "dotnet": ".net",
    "c sharp": "c#",
    "csharp": "c#",
    "cpp": "c++",
    "golang": "go",
    "py": "python",
    # Data / ML
    "ml": "machine learning",
    "dl": "deep learning",
    "nlp": "natural language processing",
    "cv": "computer vision",
    "ai": "artificial intelligence",
    "genai": "generative ai",
    "gen ai": "generative ai",
    "llm": "large language models",
    "llms": "large language models",
    "sklearn": "scikit-learn",
    "scikit learn": "scikit-learn",
    "tf": "tensorflow",
    "postgres": "postgresql",
    "ms sql": "sql server",
    "mssql": "sql server",
    "pbi": "power bi",
    "powerbi": "power bi",
    "etl pipelines": "etl",
    "rest api": "rest",
    "restful": "rest",
    "restful api": "rest",
    "oop": "object oriented programming",
    "a/b testing": "ab testing",
    "a/b tests": "ab testing",
}

# Canonical skills too ambiguous for free-text search: as bare words they
# collide with ordinary prose ("go to", the letter "r"). They still match
# via the skills-list comparison, just never via the evidence corpus.
AMBIGUOUS_IN_TEXT = {"go", "r", "c", "d", "rest"}

_WS_RE = re.compile(r"\s+")


def normalize_skill(skill: str) -> str:
    """Lowercase, trim, collapse whitespace, and resolve known aliases."""
    s = _WS_RE.sub(" ", (skill or "").lower().strip().strip("."))
    return ALIASES.get(s, s)


def _surface_forms(canonical: str) -> list[str]:
    """All spellings that mean this canonical skill (itself + its aliases)."""
    return [canonical] + [v for v, c in ALIASES.items() if c == canonical]


def _walk_strings(node, out: list) -> None:
    """Collect every string in a nested dict/list structure."""
    if isinstance(node, str):
        out.append(node)
    elif isinstance(node, dict):
        for v in node.values():
            _walk_strings(v, out)
    elif isinstance(node, list):
        for v in node:
            _walk_strings(v, out)


def build_evidence_corpus(resume: dict) -> str:
    """Flatten the whole extracted resume into one lowercase text blob.

    Used as a fallback: a JD skill found anywhere in the resume (bullets,
    projects, summary) counts as evidenced even if the skills list omits it.
    """
    parts: list[str] = []
    _walk_strings(resume, parts)
    return _WS_RE.sub(" ", " ".join(parts).lower())


def build_evidence_lines(resume: dict) -> list[str]:
    """Return the resume's individual lines, original casing preserved.

    build_evidence_corpus flattens everything into one blob, which answers
    "is this skill present?" but not "where?". Keeping the lines separate lets
    the UI quote the exact bullet a skill was found in, which is what turns a
    keyword chip into evidence the user can act on.
    """
    lines: list[str] = []
    for entry in resume.get("experience_entries", []) or []:
        for bullet in entry.get("responsibilities", []) or []:
            if isinstance(bullet, str) and bullet.strip():
                lines.append(bullet.strip())
    for project in resume.get("projects", []) or []:
        desc = (project.get("description") or "").strip()
        title = (project.get("title") or "").strip()
        if desc or title:
            lines.append(f"{title}: {desc}".strip(": ").strip())
    for pub in resume.get("publications", []) or []:
        if isinstance(pub, str) and pub.strip():
            lines.append(pub.strip())
    summary = (resume.get("summary") or "").strip()
    if summary:
        lines.append(summary)
    return lines


def find_evidence_line(skill: str, lines: list[str]) -> str | None:
    """Return the first resume line that evidences the skill, or None."""
    canonical = normalize_skill(skill)
    if not canonical or canonical in AMBIGUOUS_IN_TEXT:
        return None
    for form in _surface_forms(canonical):
        pattern = r"(?<![\w+#])" + re.escape(form) + r"(?![\w+#])"
        for line in lines:
            if re.search(pattern, line.lower()):
                return line
    return None


def found_in_corpus(skill: str, corpus: str) -> bool:
    """True when the skill (or any alias of it) appears in the resume text.

    Matches on token boundaries that treat +, #, and . as part of the skill
    so 'c++', 'c#', and 'node.js' match exactly and 'java' does not match
    inside 'javascript'. Skills in AMBIGUOUS_IN_TEXT never match free text.
    """
    canonical = normalize_skill(skill)
    if not corpus or not canonical or canonical in AMBIGUOUS_IN_TEXT:
        return False
    for form in _surface_forms(canonical):
        pattern = r"(?<![\w+#])" + re.escape(form) + r"(?![\w+#])"
        if re.search(pattern, corpus):
            return True
    return False
