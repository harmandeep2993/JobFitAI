# tests/test_contract.py
"""
Frontend-backend contract tests.

Each test calls an endpoint with the exact request shape the frontend sends
(see frontend/src/components and pages) and asserts the exact response fields
the frontend reads. If either side drifts, a test fails here instead of a tab
silently breaking in the browser.

LLM-dependent endpoints (/api/analyze, /api/ats/optimise, /api/improve-resume,
job fetch runs) are excluded - their contracts are covered indirectly through
the shapes their consumers share with the endpoints tested here.
"""

# NOTE: do not import from tests.conftest here - importing it as a module
# re-runs its chdir/tempdir setup a second time and orphans the test database.
RESUME_TEXT = (
    "John Doe\njohn@example.com\nSUMMARY\nData analyst with 3 years experience.\n"
    "WORK EXPERIENCE\nData Analyst at Acme GmbH 2021-2024: built dashboards in "
    "Power BI, SQL queries, Python ETL pipelines.\n"
    "EDUCATION\nBSc Computer Science, TU Berlin, 2020\n"
    "SKILLS\nPython, SQL, Power BI, Excel"
)


def assert_keys(payload: dict, keys: list[str]) -> None:
    """Assert every key the frontend reads is present in the response."""
    missing = [k for k in keys if k not in payload]
    assert not missing, f"response missing keys the frontend reads: {missing}"


# === Auth (Login.jsx, Settings.jsx, AppShell) ===


def test_register_and_login_contract(client):
    email = "contract-auth@example.com"
    r = client.post(
        "/api/auth/register", json={"email": email, "password": "testpass123"}
    )
    assert r.status_code == 201
    assert_keys(r.json(), ["token", "user_id", "email"])

    r = client.post("/api/auth/login", json={"email": email, "password": "testpass123"})
    assert r.status_code == 200
    assert_keys(r.json(), ["token", "user_id", "email"])


def test_me_contract(client, auth):
    r = client.get("/api/auth/me", headers=auth)
    assert r.status_code == 200
    # Settings.jsx reads email, created_at, is_admin
    assert_keys(r.json(), ["user_id", "email", "created_at", "is_admin"])


def test_change_password_contract(client):
    email = "contract-pw@example.com"
    r = client.post(
        "/api/auth/register", json={"email": email, "password": "testpass123"}
    )
    h = {"Authorization": f"Bearer {r.json()['token']}"}

    r = client.post(
        "/api/auth/change-password",
        headers=h,
        json={"current_password": "wrong", "new_password": "newpass1234"},
    )
    assert r.status_code == 401

    r = client.post(
        "/api/auth/change-password",
        headers=h,
        json={"current_password": "testpass123", "new_password": "newpass1234"},
    )
    assert r.status_code == 200 and r.json()["ok"]


# === Temp upload + preview (Analyzer.jsx, ATS.jsx) ===


def test_upload_returns_opaque_token(client, auth):
    r = client.post(
        "/api/upload",
        headers=auth,
        files={"file": ("cv.txt", RESUME_TEXT.encode(), "text/plain")},
    )
    assert r.status_code == 200
    body = r.json()
    assert_keys(body, ["ok", "name", "tmp", "kb", "ext"])
    # The token must never be a filesystem path
    assert "/" not in body["tmp"] and "\\" not in body["tmp"]


def test_resume_preview_contract(client, auth, tmp_token):
    r = client.post(
        "/api/resume-preview",
        headers=auth,
        json={"tmp": tmp_token, "resume_id": ""},
    )
    assert r.status_code == 200
    assert_keys(r.json(), ["ok", "text", "total_chars"])


def test_preview_rejects_raw_paths(client, auth):
    r = client.post(
        "/api/resume-preview",
        headers=auth,
        json={"tmp": "C:\\Windows\\win.ini", "resume_id": ""},
    )
    assert r.status_code == 400


# === Resume vault (Resumes.jsx, ResumePicker.jsx) ===


def test_resumes_crud_contract(client, auth):
    r = client.post(
        "/api/resumes/upload",
        headers=auth,
        files={"file": ("base.txt", RESUME_TEXT.encode(), "text/plain")},
        data={"slot": "0"},
    )
    assert r.status_code == 200
    assert_keys(r.json(), ["ok", "id", "slot", "label", "name", "kb", "uploaded_at"])
    rid = r.json()["id"]

    r = client.get("/api/resumes", headers=auth)
    assert r.status_code == 200
    rows = r.json()["resumes"]
    assert rows, "uploaded resume missing from list"
    # Resumes.jsx + ResumePicker.jsx read these fields per row
    assert_keys(
        rows[0],
        [
            "id",
            "slot",
            "label",
            "original_name",
            "file_size_kb",
            "extracted_json",
            "uploaded_at",
        ],
    )

    r = client.post(
        f"/api/resumes/{rid}/label", headers=auth, json={"label": "Renamed"}
    )
    assert r.status_code == 200

    r = client.get(f"/api/resumes/{rid}/file", headers=auth)
    assert r.status_code == 200

    r = client.post(f"/api/resumes/{rid}/re-extract", headers=auth)
    assert r.status_code == 200

    r = client.delete(f"/api/resumes/{rid}", headers=auth)
    assert r.status_code == 200


# === ATS (ATS.jsx) ===


def test_ats_check_contract(client, auth, tmp_token):
    r = client.post("/api/ats/check", headers=auth, json={"tmp": tmp_token, "jd": ""})
    assert r.status_code == 200
    body = r.json()
    assert_keys(
        body, ["ok", "ats_score", "section_flags", "formatting_flags", "coverage"]
    )
    assert_keys(body["ats_score"], ["score", "has_jd"])
    assert body["section_flags"], "section flags empty"
    assert_keys(body["section_flags"][0], ["name", "found", "suggestion"])


def test_ats_generation_strips_fabrications():
    """The no-invention rule is enforced, not merely requested of the model."""
    from services.ats import _verify_against_source

    source = (
        "Jane Doe | jane@example.com\n"
        "WORK EXPERIENCE\nData Analyst, Acme GmbH (2021-2024)\n"
        "- Built dashboards in Power BI and wrote SQL queries\n"
        "- Deployed a churn model with Docker\n"
        "SKILLS\nPython, SQL, Power BI, Docker"
    )
    hallucinated = {
        "skills": ["Python", "SQL", "Docker", "Kubernetes", "Terraform"],
        "experience": [
            {
                "title": "Data Analyst",
                "company": "Acme GmbH",
                "bullets": ["Built dashboards"],
            },
            {
                "title": "Senior ML Engineer",
                "company": "Google",
                "bullets": ["Led a team of 8"],
            },
        ],
    }
    clean, removed = _verify_against_source(hallucinated, source)

    # Skills the resume evidences survive; invented ones do not
    assert set(clean["skills"]) == {"Python", "SQL", "Docker"}
    assert any("Kubernetes" in r for r in removed)
    assert any("Terraform" in r for r in removed)

    # An entirely fabricated employer is dropped
    assert len(clean["experience"]) == 1
    assert clean["experience"][0]["company"] == "Acme GmbH"
    assert any("Google" in r for r in removed)


def test_ats_docx_includes_contact_header(client, auth):
    """A resume with no name or email is not sendable, and ATS cannot route it."""
    r = client.post(
        "/api/ats/docx",
        headers=auth,
        json={
            "resume": {
                "contact": {
                    "name": "Jane Doe",
                    "email": "jane@example.com",
                    "phone": "+49 123",
                    "links": ["linkedin.com/in/jane"],
                },
                "summary": "Data analyst.",
                "skills": ["Python"],
                "experience": [],
                "education": [],
            }
        },
    )
    assert r.status_code == 200

    # A DOCX is a zip - read the real paragraphs back rather than the raw bytes
    import io as _io

    from docx import Document

    doc = Document(_io.BytesIO(r.content))
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "Jane Doe" in text
    assert "jane@example.com" in text
    assert "+49 123" in text
    assert "linkedin.com/in/jane" in text


def test_ats_docx_contract(client, auth):
    r = client.post(
        "/api/ats/docx",
        headers=auth,
        json={
            "resume": {
                "summary": "x",
                "skills": ["Python"],
                "experience": [],
                "education": [],
            }
        },
    )
    assert r.status_code == 200
    assert "wordprocessingml" in r.headers["content-type"]
    assert len(r.content) > 1000


# === Job matches (JobMatches.jsx, Settings.jsx) ===


def test_match_state_contract(client, auth):
    r = client.get("/api/match/state", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert_keys(
        body,
        [
            "ok",
            "has_resume",
            "resume_name",
            "filters",
            "last_run",
            "run_status",
            "resume",
            "scheduler",
            "results",
        ],
    )
    # Settings.jsx reads filters.target_titles / countries / location
    assert_keys(body["filters"], ["target_titles", "countries", "location"])
    # JobMatches.jsx reads run_status.running / phase / checked / scored / total
    assert_keys(body["run_status"], ["running", "phase", "checked", "scored", "total"])
    # Settings.jsx reads scheduler.enabled / interval
    assert_keys(body["scheduler"], ["enabled", "interval"])


def test_match_filters_contract(client, auth):
    r = client.post(
        "/api/match/filters",
        headers=auth,
        json={
            "target_titles": ["data analyst"],
            "location": "Berlin",
            "countries": ["de"],
        },
    )
    assert r.status_code == 200
    assert r.json()["target_titles"] == ["data analyst"]


def _seed_job(client, auth, job_id="job-1"):
    """Insert a scored match for the authed user directly via the repo."""
    from repositories import match_repo

    me = client.get("/api/auth/me", headers=auth).json()
    match_repo.upsert(
        me["user_id"],
        [
            {
                "id": job_id,
                "source": "adzuna",
                "title": "Junior ML Engineer",
                "company": "Acme",
                "location": "Berlin",
                "url": "https://example.com/job",
                "language": "en",
                "posted_at": "1750000000",
                "score": 72,
                "label": "good",
                "matched_required": ["python"],
                "missing_required": ["docker"],
                "section_scores": {"required_skills": 70},
                "scored_at": "2026-07-11T00:00:00+00:00",
                "jd_json": {"required_skills": ["python"]},
                "status": "scored",
            }
        ],
    )
    return me["user_id"]


def test_match_applied_requires_flag(client, auth):
    # JobMatches.jsx must send both id and applied - omitting applied is a 422
    r = client.post("/api/match/applied", headers=auth, json={"id": "job-1"})
    assert r.status_code == 422
    # Unknown job id is a 404, not a silent success
    r = client.post(
        "/api/match/applied", headers=auth, json={"id": "no-such-job", "applied": True}
    )
    assert r.status_code == 404
    _seed_job(client, auth)
    r = client.post(
        "/api/match/applied", headers=auth, json={"id": "job-1", "applied": True}
    )
    assert r.status_code == 200
    assert_keys(r.json(), ["ok", "id", "applied"])


def test_match_app_status_contract(client, auth):
    """Detail drawer status dropdown: valid transitions accepted, junk rejected."""
    _seed_job(client, auth, "job-status")
    r = client.post(
        "/api/match/app-status",
        headers=auth,
        json={"id": "job-status", "status": "interview"},
    )
    assert r.status_code == 200
    assert r.json()["app_status"] == "interview"

    r = client.post(
        "/api/match/app-status",
        headers=auth,
        json={"id": "job-status", "status": "hired-maybe"},
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "invalid_status"

    r = client.post(
        "/api/match/app-status",
        headers=auth,
        json={"id": "missing", "status": "applied"},
    )
    assert r.status_code == 404


def test_match_detail_contract(client, auth):
    """Detail drawer reads job, section_scores, matched/missing, and summary."""
    from repositories import match_repo

    user_id = _seed_job(client, auth, "job-detail")
    # Pre-cache a summary so the endpoint does not call the LLM
    match_repo.set_summary(user_id, "job-detail", "Strong python match.")

    r = client.get("/api/match/detail?id=job-detail", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert_keys(
        body,
        [
            "ok",
            "job",
            "section_scores",
            "matched_required",
            "missing_required",
            "summary",
        ],
    )
    assert_keys(body["job"], ["id", "title", "company", "score", "label", "url"])

    r = client.get("/api/match/detail?id=missing", headers=auth)
    assert r.status_code == 404


def test_match_delete_restore_contract(client, auth):
    """Deleting a job hides it from state; restore brings it back."""
    _seed_job(client, auth, "job-del")

    r = client.post("/api/match/delete", headers=auth, json={"id": "job-del"})
    assert r.status_code == 200
    ids = [
        j["id"] for j in client.get("/api/match/state", headers=auth).json()["results"]
    ]
    assert "job-del" not in ids

    # Second delete of the same job is a 404 (already gone)
    r = client.post("/api/match/delete", headers=auth, json={"id": "job-del"})
    assert r.status_code == 404

    r = client.post("/api/match/restore", headers=auth, json={"id": "job-del"})
    assert r.status_code == 200
    ids = [
        j["id"] for j in client.get("/api/match/state", headers=auth).json()["results"]
    ]
    assert "job-del" in ids

    r = client.post("/api/match/restore", headers=auth, json={"id": "job-del"})
    assert r.status_code == 404


def test_match_scheduler_contract(client, auth):
    r = client.post("/api/match/scheduler", headers=auth, json={"enabled": False})
    assert r.status_code == 200
    assert_keys(r.json(), ["ok", "enabled", "interval"])


def test_match_export_contract(client, auth):
    r = client.get("/api/match/export", headers=auth)
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]


# === History (History.jsx) ===


def test_history_contract(client, auth):
    r = client.get("/api/history", headers=auth)
    assert r.status_code == 200
    assert_keys(r.json(), ["ok", "analyses", "fetcher_runs", "applications"])


def test_history_analysis_unknown_hash(client, auth):
    r = client.get("/api/history/analysis?hash=deadbeef", headers=auth)
    assert r.status_code == 404


# === LLM settings (Settings.jsx) ===


def test_llm_settings_contract(client, auth):
    r = client.get("/api/llm-settings", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert_keys(body, ["ok", "current", "providers"])
    assert_keys(body["current"], ["provider", "model"])
    assert body["providers"], "provider catalog empty"
    assert_keys(
        body["providers"][0],
        ["name", "default_model", "models", "needs_key", "has_key"],
    )


def test_llm_settings_post_requires_admin(client, auth):
    r = client.post(
        "/api/llm-settings",
        headers=auth,
        json={"provider": "openai", "model": ""},
    )
    assert r.status_code == 403


# === Invite gate (Login.jsx register form) ===


def test_invite_gate(client, monkeypatch):
    import api.routes.auth as auth_route

    monkeypatch.setattr(auth_route, "_INVITE_CODE", "test-code")
    r = client.post(
        "/api/auth/register",
        json={"email": "gated@example.com", "password": "testpass123"},
    )
    assert r.status_code == 403
    assert r.json()["detail"] == "invalid_invite_code"

    r = client.post(
        "/api/auth/register",
        json={
            "email": "gated@example.com",
            "password": "testpass123",
            "invite_code": "test-code",
        },
    )
    assert r.status_code == 201


# === Auth boundaries ===


def test_protected_endpoints_reject_anonymous(client):
    for method, path in [
        ("get", "/api/history"),
        ("get", "/api/resumes"),
        ("get", "/api/match/state"),
        ("get", "/api/llm-settings"),
        ("get", "/api/llm-ping"),
        ("post", "/api/upload"),
    ]:
        r = getattr(client, method)(path)
        assert r.status_code in (401, 403), f"{path} not protected: {r.status_code}"


def test_llm_endpoints_rate_limited_per_user(client, auth, tmp_token, monkeypatch):
    """LLM-backed endpoints return 429 rate_limited once the user budget is spent."""
    import api.routes.auth as auth_route

    monkeypatch.setattr(auth_route, "_LLM_RATE_LIMIT_ATTEMPTS", 1)
    r = client.post("/api/ats/check", headers=auth, json={"tmp": tmp_token, "jd": ""})
    assert r.status_code == 200

    r = client.post("/api/ats/check", headers=auth, json={"tmp": tmp_token, "jd": ""})
    assert r.status_code == 429
    assert r.json()["detail"] == "rate_limited"


def test_spa_catch_all_serves_client_routes(client):
    """Hard refresh on a client-routed page serves the SPA, never a JSON 404."""
    for path in ["/about", "/pricing", "/privacy"]:
        r = client.get(path)
        # 200 when frontend/dist exists, 503 hint when it was never built
        assert r.status_code in (200, 503), f"{path}: {r.status_code}"
        assert "text/html" in r.headers["content-type"]

    r = client.get("/api/does-not-exist")
    assert r.status_code == 404
    assert r.json()["error"] == "not_found"


def test_delete_account_erases_all_user_data(client):
    """GDPR erasure: wrong password is rejected; deletion removes every row."""
    from core import database as db

    email = "erase-me@example.com"
    r = client.post(
        "/api/auth/register", json={"email": email, "password": "testpass123"}
    )
    assert r.status_code == 201
    body = r.json()
    h = {"Authorization": f"Bearer {body['token']}"}
    user_id = body["user_id"]

    # Give the account some data to erase
    client.post(
        "/api/resumes/upload",
        headers=h,
        files={"file": ("cv.txt", RESUME_TEXT.encode(), "text/plain")},
        data={"slot": "0"},
    )
    client.post(
        "/api/match/filters", headers=h, json={"target_titles": ["ml engineer"]}
    )

    # A stolen token alone must not be enough to destroy the account
    r = client.request(
        "DELETE", "/api/auth/account", headers=h, json={"password": "wrong-password"}
    )
    assert r.status_code == 401

    r = client.request(
        "DELETE", "/api/auth/account", headers=h, json={"password": "testpass123"}
    )
    assert r.status_code == 200 and r.json()["ok"]

    # Every per-user table is empty, and the account no longer authenticates
    with db.connect() as conn:
        for table in ("users", "resumes", "analyses", "matches", "user_settings"):
            col = "id" if table == "users" else "user_id"
            rows = conn.execute(
                f"SELECT COUNT(*) AS n FROM {table} WHERE {col} = ?", (user_id,)
            ).fetchone()["n"]
            assert rows == 0, f"{table} still holds data after erasure"

    assert client.get("/api/auth/me", headers=h).status_code == 401


def test_jwt_carries_no_personal_data(client):
    """The token sits in localStorage, so it must not contain the email."""
    import json as _json
    import base64

    r = client.post(
        "/api/auth/register",
        json={"email": "claims@example.com", "password": "testpass123"},
    )
    token = r.json()["token"]
    payload_b64 = token.split(".")[1]
    payload_b64 += "=" * (-len(payload_b64) % 4)
    claims = _json.loads(base64.urlsafe_b64decode(payload_b64))

    assert "sub" in claims
    assert "email" not in claims, "email must not be a JWT claim"
    assert "claims@example.com" not in _json.dumps(claims)


def test_match_stop_contract(client, auth):
    """Stop endpoint responds ok with stopped=false when no run is active."""
    r = client.post("/api/match/stop", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["stopped"] is False


def test_entry_search_expansion():
    """Plain titles combine with entry keywords; qualified titles pass as-is."""
    from services.title_expander import entry_search_queries

    queries = entry_search_queries(["ml engineer"])
    assert "ml engineer" in queries
    assert "junior ml engineer" in queries
    assert "intern ml engineer" in queries
    assert len(queries) > 3

    # Already qualified - searched exactly as typed, no expansion
    assert entry_search_queries(["junior ml engineer"]) == ["junior ml engineer"]


def test_entry_exclude_terms():
    """Entry-only blocks seniority + student posts unless the user asks for them."""
    from services.title_expander import exclude_terms, title_blocked

    # Off: nothing blocked
    assert exclude_terms(["ml engineer"], entry_only=False) == []

    # On with a junior title: werkstudent posts are blocked
    terms = exclude_terms(["junior ml engineer"], entry_only=True)
    assert title_blocked("Werkstudent Machine Learning (m/w/d)", terms)
    assert title_blocked("Senior ML Engineer", terms)
    assert not title_blocked("Junior ML Engineer", terms)

    # On with an explicit werkstudent title: student posts allowed, seniors not
    terms = exclude_terms(["werkstudent ml"], entry_only=True)
    assert not title_blocked("Werkstudent Machine Learning (m/w/d)", terms)
    assert title_blocked("Senior ML Engineer", terms)


def test_relevance_gate_uses_user_titles_within_it_domain():
    """The gate prompt carries the user's roles plus the IT/CS domain wall."""
    from services.job_relevance import _targets_desc, _BATCH_PROMPT, _IT_DOMAIN_RULE

    desc = _targets_desc(["devops engineer", "sre"])
    assert desc == "devops engineer, sre"
    prompt = _BATCH_PROMPT.format(
        targets=desc, domain_rule=_IT_DOMAIN_RULE, count=1, jobs="1. DevOps Engineer"
    )
    # User titles drive relevance, the domain rule excludes non-IT professions
    assert "devops engineer" in prompt
    assert "non-IT professions" in prompt

    # No titles configured falls back to the whole IT/CS domain
    assert "IT" in _targets_desc([])
    assert "IT" in _targets_desc(None)


def test_seniority_guard_overrides_llm():
    """Titles with explicit seniority markers must never pass the entry gate."""
    from services.job_relevance import title_is_senior

    for title in [
        "Senior Machine Learning Engineer (m/w/d) - PDR.cloud GmbH",
        "Sr. Data Scientist",
        "Lead AI Engineer",
        "Teamleiter Data Science",
    ]:
        assert title_is_senior(title), title

    for title in [
        "Junior Data Analyst",
        "Machine Learning Engineer (m/w/d)",
        "Working Student AI",
    ]:
        assert not title_is_senior(title), title


def test_security_headers_present(client, auth):
    """Every response carries the baseline security headers set by middleware."""
    r = client.get("/api/resumes", headers=auth)
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert r.headers.get("x-frame-options") == "SAMEORIGIN"
    assert r.headers.get("referrer-policy") == "strict-origin-when-cross-origin"

    # CSP locks scripts to our own origin: the JWT lives in localStorage, so an
    # injected script is the realistic path to stealing it.
    csp = r.headers.get("content-security-policy", "")
    assert "script-src 'self'" in csp
    assert "connect-src 'self'" in csp
    assert "object-src 'none'" in csp
    # Fonts are the only third party the page may reach
    assert "https://fonts.gstatic.com" in csp

    # HSTS must NOT be sent in dev - it would pin localhost to https for a year
    assert "strict-transport-security" not in r.headers
