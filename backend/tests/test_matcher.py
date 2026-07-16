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
    score, matched, partial, missing, evidence = score_required_skills(RESUME, jd)
    # kubernetes via alias (k8s), docker+sql via evidence bullets, python direct
    assert "kubernetes" in matched
    assert "docker" in matched
    assert "python" in matched
    assert "sql" in matched
    assert missing == []
    assert score == 100.0


def test_required_skills_unrelated_stays_missing():
    jd = {"required_skills": ["terraform", "react"]}
    score, matched, partial, missing, evidence = score_required_skills(RESUME, jd)
    assert "react" in missing
    assert "terraform" not in matched
    assert score < 60


def test_no_required_skills_returns_none():
    score, matched, partial, missing, evidence = score_required_skills(RESUME, {})
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


# === Experience years: full-time-equivalent, not raw calendar span ===


def test_total_experience_years_is_fte_weighted():
    """Part-time work must not count as full years, and concurrent roles once."""
    from services.matcher.experience import (
        total_experience_years as _total_experience_years,
    )

    # 2 years as a working student (20h/week) is 1.0 FTE year, not 2.
    ws_only = [
        {
            "employment_type": "working student",
            "start_date": "01/2020",
            "end_date": "01/2022",
        }
    ]
    assert 0.9 <= _total_experience_years(ws_only) <= 1.2

    # Same span full-time is worth double
    ft_only = [
        {"employment_type": "full-time", "start_date": "01/2020", "end_date": "01/2022"}
    ]
    assert _total_experience_years(ft_only) > _total_experience_years(ws_only) * 1.7

    # A working student job held during a full-time role adds nothing
    concurrent = [
        {
            "employment_type": "full-time",
            "start_date": "01/2022",
            "end_date": "01/2024",
        },
        {
            "employment_type": "working student",
            "start_date": "01/2022",
            "end_date": "01/2024",
        },
    ]
    assert _total_experience_years(concurrent) == _total_experience_years(
        [
            {
                "employment_type": "full-time",
                "start_date": "01/2022",
                "end_date": "01/2024",
            }
        ]
    )

    # Unknown employment type is assumed full-time rather than silently discounted
    unknown = [{"start_date": "01/2022", "end_date": "01/2024"}]
    assert _total_experience_years(unknown) > 1.8


def test_two_model_tiers(monkeypatch):
    """Bulk work runs cheap; low-volume quality work may use a stronger model."""
    from core import state

    state.set_active("openai", None)  # no admin pin -> config decides
    # Bulk: one JD extraction per fetched job, hundreds of relevance calls
    assert state.get_model() == "gpt-4o-mini"
    # Quality: summary, ATS rewrite, coverage judge - read or sent by the user
    assert state.get_quality_model() == "gpt-5-mini"

    # An explicit admin pin must win over both
    state.set_active("openai", "gpt-4o-mini")
    assert state.get_quality_model() == "gpt-4o-mini"
    state.set_active("openai", None)


def test_reasoning_models_get_different_request_params():
    """gpt-5 rejects max_tokens/temperature; sending them would 400."""
    from services.llm.providers.openai import _is_reasoning_model

    assert _is_reasoning_model("gpt-5-mini")
    assert _is_reasoning_model("o3-mini")
    assert not _is_reasoning_model("gpt-4o-mini")


# === Gates: years and language level are pass/fail, never points ===


def test_jd_entry_gate_uses_extracted_data_not_title():
    """After scoring, the JD's own fields catch seniors the title hid."""
    from services.matcher.gates import jd_requires_seniority

    # Title said "ML Engineer" but the extracted JD asks for 5 years
    assert jd_requires_seniority(
        {
            "job": {"job_level": "not specified"},
            "experience_requirements": ["5+ years"],
        },
        2,
    )
    # job_level field alone is enough
    assert jd_requires_seniority({"job": {"job_level": "senior"}}, 2)
    assert jd_requires_seniority({"job": {"job_level": "lead"}}, 2)

    # Genuine entry roles pass
    assert (
        jd_requires_seniority(
            {"job": {"job_level": "junior"}, "experience_requirements": ["1+ years"]}, 2
        )
        is None
    )
    assert jd_requires_seniority({"job": {"job_level": "not specified"}}, 2) is None
    # mid-level within the year bar is allowed - many accept strong juniors
    assert (
        jd_requires_seniority(
            {"job": {"job_level": "mid-level"}, "experience_requirements": ["2 years"]},
            2,
        )
        is None
    )


def test_jd_entry_gate_reads_experience_stated_without_a_number():
    """Seniority is often stated in words, not digits - EN and DE."""
    from services.matcher.gates import jd_requires_seniority

    def jd(reqs, summary=""):
        return {
            "job": {"job_level": "not specified"},
            "experience_requirements": reqs,
            "job_summary": summary,
        }

    for phrase in [
        "several years of experience",
        "proven experience in machine learning",
        "extensive experience with cloud platforms",
        "mehrjaehrige Berufserfahrung",
        "mehrjährige Berufserfahrung",
        "einschlaegige Berufserfahrung erforderlich",
        "fundierte Kenntnisse in Python",
        "years of professional experience required",
    ]:
        assert jd_requires_seniority(jd([phrase]), 2), f"missed: {phrase}"

    # The phrase can live in the summary rather than the requirements list
    assert jd_requires_seniority(
        jd([], "we want several years of hands-on experience"), 2
    )

    # Entry phrasings must survive, even next to the word "experience"
    for phrase in [
        "no prior experience required",
        "erste Berufserfahrung von Vorteil",
        "Berufseinsteiger willkommen",
        "recent graduates welcome",
        "entry-level position",
        "keine Berufserfahrung noetig",
    ]:
        assert (
            jd_requires_seniority(jd([phrase]), 2) is None
        ), f"false positive: {phrase}"


def test_seniority_detection_is_robust():
    """The deterministic net must catch how senior roles actually hide."""
    from services.job_relevance import title_is_senior

    senior = [
        "Software Engineer II",
        "Data Scientist III",
        "ML Engineer IV",
        "Software Engineer 3",
        "L5 Software Engineer",
        "SDE II",
        "Teamlead Data Engineering",
        "TechLead Backend",
        "Expert Data Scientist",
        "Chief Data Officer",
        "VP Engineering",
        "Vice President of Data",
        "Distinguished Engineer",
        "Softwareentwickler (m/w/d) mit Berufserfahrung",
        "Erfahrener ML Engineer",
        "Abteilungsleitung IT",
        "Senior ML Engineer",
        "Staff Engineer",
    ]
    for t in senior:
        assert title_is_senior(t), f"leaked senior title: {t}"

    # Version numbers are not seniority, and an explicit entry word always wins.
    entry = [
        "Junior ML Engineer",
        "ML Engineer (m/w/d)",
        "Machine Learning Engineer",
        "Web3 Developer",
        "Python 3 Developer",
        "Angular 2 Developer",
        "S3 Data Pipeline Engineer",
        "Junior Engineer II",
        "Werkstudent KI",
        "Azubi Fachinformatiker",
    ]
    for t in entry:
        assert not title_is_senior(t), f"false positive on entry title: {t}"


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


def test_experience_gate_uses_relevant_years_not_total():
    """A career changer's years in another field must not clear this job's bar."""
    from services.matcher.gates import relevant_experience

    changer = {
        "experience_entries": [
            {
                "title": "Mechanical Engineer",
                "employment_type": "full-time",
                "start_date": "01/2018",
                "end_date": "01/2023",
                "responsibilities": ["Designed CAD assemblies", "Ran FEA simulations"],
            },
            {
                "title": "ML Engineer",
                "employment_type": "full-time",
                "start_date": "02/2023",
                "end_date": "05/2024",
                "responsibilities": ["Built models in Python with PyTorch on AWS"],
            },
        ],
        "meta": {"total_experience_years": 6.3},
    }
    jd = {
        "required_skills": ["python", "pytorch", "aws", "machine learning"],
        "experience_requirements": ["5+ years of machine learning engineering"],
    }

    years, roles = relevant_experience(changer, jd)
    # Only the ML role demonstrates the JD's skills
    assert years < 2.0, f"irrelevant years leaked into the total: {years}"
    assert [r["relevant"] for r in roles] == [False, True]

    gate = check_experience_gate(changer, jd)
    assert gate["required_years"] == 5
    assert gate["total_years"] == 6.3
    assert gate["candidate_years"] < 2.0
    # 6.3 total would have passed; 1.3 relevant must not
    assert gate["met"] is False

    # A genuine 8-year ML engineer clears the same bar
    veteran = {
        "experience_entries": [
            {
                "title": "ML Engineer",
                "employment_type": "full-time",
                "start_date": "01/2016",
                "end_date": "01/2024",
                "responsibilities": ["Built models in Python with PyTorch on AWS"],
            }
        ],
        "meta": {"total_experience_years": 8},
    }
    assert check_experience_gate(veteran, jd)["met"] is True

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
        engine, "score_required_skills", lambda r, j: (100.0, ["python"], [], [], {})
    )
    monkeypatch.setattr(
        engine, "score_preferred_skills", lambda r, j: (None, [], [], [], {})
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
        engine, "score_required_skills", lambda r, j: (90.0, ["python"], [], [], {})
    )
    monkeypatch.setattr(
        engine, "score_preferred_skills", lambda r, j: (None, [], [], [], {})
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
        engine, "score_required_skills", lambda r, j: (80.0, ["python"], [], [], {})
    )
    monkeypatch.setattr(
        engine, "score_preferred_skills", lambda r, j: (None, [], [], [], {})
    )
    monkeypatch.setattr(engine, "score_education", lambda r, j: None)
    monkeypatch.setattr(engine, "score_certifications", lambda r, j: None)

    jd = {"required_skills": ["python"], "responsibilities": ["Do the thing"]}
    result = engine.match({"skills": ["python"]}, jd)

    assert result["section_scores"]["responsibilities"] is None
    # Only required_skills is active, so it carries the whole score
    assert result["overall_score"] == 80.0


def test_summary_consumes_match_output_without_keyerror(monkeypatch):
    """generate_summary must survive the real match() shape.

    Regression: it indexed breakdown["experience"], which no longer exists now
    that experience is a gate, and crashed /api/analyze with KeyError after the
    LLM calls had already been paid for. It must also tolerate None sections.
    """
    import services.profile_summary as ps

    monkeypatch.setattr(
        ps,
        "call_llm",
        lambda p, **k: type(
            "R", (), {"text": '{"profile":[],"strengths":[],"gaps":[],"focus":[]}'}
        )(),
    )
    monkeypatch.setattr(
        engine, "judge_coverage", lambda r, j: (62.5, [], [], ["a duty"])
    )

    jd = {
        "required_skills": ["python"],
        "responsibilities": ["Develop models"],
        "experience_requirements": ["5+ years of ML engineering"],
        "job": {"title": "ML Engineer"},
    }
    results = engine.match(RESUME, jd, llm_judge=True)

    # preferred_skills / certifications are None here - the summary must cope
    assert results["section_scores"]["preferred_skills"] is None
    assert "experience" not in results["section_scores"]

    out = ps.generate_summary(RESUME, jd, results)
    assert out is not None


def test_engine_reports_partial_lists(monkeypatch):
    monkeypatch.setattr(
        engine,
        "score_required_skills",
        lambda r, j: (75.0, ["python"], ["tensorflow"], ["terraform"], {}),
    )
    monkeypatch.setattr(
        engine, "score_preferred_skills", lambda r, j: (None, [], [], [], {})
    )
    monkeypatch.setattr(engine, "score_education", lambda r, j: None)
    monkeypatch.setattr(engine, "score_certifications", lambda r, j: None)

    result = engine.match({"skills": ["python"]}, {"required_skills": ["python"]})
    assert result["partial_required"] == ["tensorflow"]
    assert result["missing_required"] == ["terraform"]
