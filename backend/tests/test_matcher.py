# tests/test_matcher.py
"""
Unit tests for the matcher.

Covers the deterministic layer (alias resolution, full-resume evidence search,
graded skill credit, calibration, None-sentinel weighting) and the gates that
replaced the experience and language scorers.

These tests load the local embedding model (a few seconds on first import) but
make no network or LLM calls - the responsibility-coverage judge is stubbed.
"""

from services.matcher import engine
from services.matcher.gates import (
    check_experience_gate,
    check_language_gates,
    parse_required_years,
)
from services.matcher.skill_aliases import (
    build_evidence_corpus,
    found_in_corpus,
    normalize_skill,
)
from services.matcher.scores.skills import score_required_skills
from services.matcher.scores.certifications import score_certifications
from services.matcher.scoring_utils import calibrate_similarity


RESUME = {
    "skills": ["Python", "k8s", "Power BI"],
    "experience_entries": [
        {
            "title": "Data Analyst",
            "company": "Acme GmbH",
            "responsibilities": [
                "Deployed ML models with Docker on AWS",
                "Built SQL dashboards for management reporting",
            ],
        }
    ],
    "projects": [],
    "education": [{"degree": "BSc", "field": "Computer Science"}],
    "languages": [{"language": "German", "proficiency": "B1"}],
    "certifications": [],
    "meta": {"total_experience_years": 2},
}


# === Skills: aliases, evidence, graded credit ===


def test_alias_normalization():
    assert normalize_skill("K8s") == "kubernetes"
    assert normalize_skill(" JS ") == "javascript"
    assert normalize_skill("Machine Learning") == "machine learning"
    assert normalize_skill("PostgreSQL") == "postgresql"


def test_evidence_corpus_finds_buried_skills():
    corpus = build_evidence_corpus(RESUME)
    # In a bullet, not in the skills list
    assert found_in_corpus("docker", corpus)
    assert found_in_corpus("aws", corpus)
    # Must not match inside longer tokens or random text
    assert not found_in_corpus("java", corpus)
    # Ambiguous one-letter skills never match free text
    assert not found_in_corpus("r", corpus)


def test_required_skills_alias_and_evidence():
    jd = {"required_skills": ["Kubernetes", "Docker", "Python", "SQL"]}
    score, matched, partial, missing = score_required_skills(RESUME, jd)
    # kubernetes via alias (k8s), docker+sql via evidence bullets, python direct
    assert "kubernetes" in matched
    assert "docker" in matched
    assert "python" in matched
    assert "sql" in matched
    assert missing == []
    assert score == 100.0


def test_required_skills_unrelated_stays_missing():
    jd = {"required_skills": ["terraform", "react"]}
    score, matched, partial, missing = score_required_skills(RESUME, jd)
    assert "react" in missing
    assert "terraform" not in matched
    assert score < 60


def test_no_required_skills_returns_none():
    score, matched, partial, missing = score_required_skills(RESUME, {})
    assert score is None
    assert matched == [] and partial == [] and missing == []


def test_calibration_band():
    assert calibrate_similarity(0.20) == 0.0
    assert calibrate_similarity(0.35) == 0.0
    assert calibrate_similarity(0.55) == 50.0
    assert calibrate_similarity(0.75) == 100.0
    assert calibrate_similarity(0.95) == 100.0


def test_certifications_none_when_jd_empty():
    assert score_certifications(RESUME, {}) is None


# === Gates: years and language level are pass/fail, never points ===


def test_parse_required_years():
    assert parse_required_years(["3+ years of experience"]) == 3
    assert parse_required_years(["at least 5 years in ML"]) == 5
    assert parse_required_years(["mindestens 3 Jahre Berufserfahrung"]) == 3
    # A range is cleared by its lower bound
    assert parse_required_years(["3-5 years"]) == 3
    # Multiple statements: the highest is the binding constraint
    assert parse_required_years(["2+ years Python", "5+ years leadership"]) == 5
    # No number stated -> no gate
    assert parse_required_years(["several years of experience"]) is None
    assert parse_required_years([]) is None


def test_experience_gate_compares_actual_years():
    jd = {"experience_requirements": ["5+ years of machine learning engineering"]}
    gate = check_experience_gate(RESUME, jd)  # resume has 2 years
    assert gate["required_years"] == 5
    assert gate["candidate_years"] == 2
    assert gate["met"] is False

    senior = {**RESUME, "meta": {"total_experience_years": 8}}
    assert check_experience_gate(senior, jd)["met"] is True

    # JD states no years -> no gate at all
    assert check_experience_gate(RESUME, {"experience_requirements": []}) is None


def test_language_gate_compares_cefr_levels():
    # Resume has German B1; JD wants C1 -> not met
    gates = check_language_gates(
        RESUME, {"languages": [{"language": "German", "proficiency": "C1"}]}
    )
    assert len(gates) == 1
    assert gates[0]["language"] == "german"
    assert gates[0]["met"] is False

    # JD wants German B1 -> met
    gates = check_language_gates(
        RESUME, {"languages": [{"language": "German", "proficiency": "B1"}]}
    )
    assert gates[0]["met"] is True

    # Language not on the resume at all -> not met
    gates = check_language_gates(
        RESUME, {"languages": [{"language": "French", "proficiency": "B2"}]}
    )
    assert gates[0]["met"] is False

    # No language requirement -> no gates
    assert check_language_gates(RESUME, {"languages": []}) == []


def test_gates_never_enter_the_score(monkeypatch):
    """A failed gate warns but must not reduce the weighted score."""
    monkeypatch.setattr(
        engine, "score_required_skills", lambda r, j: (100.0, ["python"], [], [])
    )
    monkeypatch.setattr(
        engine, "score_preferred_skills", lambda r, j: (None, [], [], [])
    )
    monkeypatch.setattr(engine, "score_education", lambda r, j: None)
    monkeypatch.setattr(engine, "score_certifications", lambda r, j: None)

    jd = {
        "required_skills": ["python"],
        "experience_requirements": ["10+ years"],
        "languages": [{"language": "German", "proficiency": "C1"}],
    }
    result = engine.match(RESUME, jd)

    # Skills are perfect, so the score is perfect...
    assert result["overall_score"] == 100.0
    # ...while both gates are reported as unmet
    assert result["gates"]["experience"]["met"] is False
    assert result["gates"]["languages"][0]["met"] is False
    assert result["gates"]["blocking_count"] == 2
    # Gates are not sections
    assert "experience" not in result["section_scores"]
    assert "languages" not in result["section_scores"]


# === Engine weighting ===


def test_engine_excludes_none_sections_but_keeps_real_sixty(monkeypatch):
    """None sections are excluded from the overall; a legitimate 60.0 is not."""
    monkeypatch.setattr(
        engine, "score_required_skills", lambda r, j: (90.0, ["python"], [], [])
    )
    monkeypatch.setattr(
        engine, "score_preferred_skills", lambda r, j: (None, [], [], [])
    )
    monkeypatch.setattr(engine, "score_education", lambda r, j: None)
    monkeypatch.setattr(engine, "score_certifications", lambda r, j: None)
    monkeypatch.setattr(
        engine, "judge_coverage", lambda r, j: (60.0, [], [], ["some duty"])
    )

    result = engine.match(
        {"skills": ["python"]}, {"required_skills": ["python"]}, llm_judge=True
    )

    assert result["section_scores"]["preferred_skills"] is None
    assert result["section_scores"]["responsibilities"] == 60.0
    # required_skills 0.40 x 90 + responsibilities 0.35 x 60, over weight 0.75
    assert result["overall_score"] == 76.0


def test_responsibilities_excluded_without_llm_judge(monkeypatch):
    """Bulk job scoring skips the LLM judge; the section is None, not zero."""
    monkeypatch.setattr(
        engine, "score_required_skills", lambda r, j: (80.0, ["python"], [], [])
    )
    monkeypatch.setattr(
        engine, "score_preferred_skills", lambda r, j: (None, [], [], [])
    )
    monkeypatch.setattr(engine, "score_education", lambda r, j: None)
    monkeypatch.setattr(engine, "score_certifications", lambda r, j: None)

    jd = {"required_skills": ["python"], "responsibilities": ["Do the thing"]}
    result = engine.match({"skills": ["python"]}, jd)

    assert result["section_scores"]["responsibilities"] is None
    # Only required_skills is active, so it carries the whole score
    assert result["overall_score"] == 80.0


def test_engine_reports_partial_lists(monkeypatch):
    monkeypatch.setattr(
        engine,
        "score_required_skills",
        lambda r, j: (75.0, ["python"], ["tensorflow"], ["terraform"]),
    )
    monkeypatch.setattr(
        engine, "score_preferred_skills", lambda r, j: (None, [], [], [])
    )
    monkeypatch.setattr(engine, "score_education", lambda r, j: None)
    monkeypatch.setattr(engine, "score_certifications", lambda r, j: None)

    result = engine.match({"skills": ["python"]}, {"required_skills": ["python"]})
    assert result["partial_required"] == ["tensorflow"]
    assert result["missing_required"] == ["terraform"]
