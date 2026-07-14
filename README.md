<div align="center">

![Status](https://img.shields.io/badge/Status-Active_Development-orange?style=for-the-badge)
![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white)
![React](https://img.shields.io/badge/React-18-61DAFB?style=for-the-badge&logo=react&logoColor=black)
![FastAPI](https://img.shields.io/badge/FastAPI-latest-009688?style=for-the-badge&logo=fastapi&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)

![OpenAI](https://img.shields.io/badge/OpenAI-GPT--4o-412991?style=flat-square&logo=openai&logoColor=white)
![Groq](https://img.shields.io/badge/Groq-llama--3.1-F55036?style=flat-square)
![Ollama](https://img.shields.io/badge/Ollama-Local-black?style=flat-square)
![Adzuna](https://img.shields.io/badge/Adzuna-Germany-0055A4?style=flat-square)
![Arbeitnow](https://img.shields.io/badge/Arbeitnow-Germany-E63946?style=flat-square)
![Bundesagentur](https://img.shields.io/badge/Bundesagentur-Germany-003082?style=flat-square)

# JobsFitAI

### Stop applying blind. Know your fit before you apply.

*Built for job seekers in Germany.*
*Upload your resume, paste a job description, and get a precise AI-powered match score*
*with keyword gaps, section breakdown, and live job listings from three German job boards.*

[![LinkedIn](https://img.shields.io/badge/LinkedIn-Connect-0A66C2?style=flat&logo=linkedin)](https://linkedin.com/in/harmandeep/)
[![GitHub](https://img.shields.io/badge/GitHub-Follow-181717?style=flat&logo=github)](https://github.com/harmandeep2993)

</div>

---

## Why I Built This

The job search process is opaque for candidates. Companies run resumes through automated filters
and candidates have no visibility into what those filters are looking for. You apply, you wait,
you hear nothing, and you have no idea why.

I needed a tool that would look at any job description and tell me exactly how well my profile
matches it, which keywords I am missing, and whether it is even worth tailoring my resume for
that role. Not a generic score. A specific analysis against a specific job every time.

So I built it. The core works and I use it for every role I consider applying to.

---

## What It Does

**Resume Analyser** - Upload a PDF or DOCX resume and paste any job description. The AI
pipeline extracts structured data from both, runs a weighted scoring engine across seven
dimensions, and returns a 0-100 match score with a full breakdown, keyword gap list, and
actionable recommendations. Re-running the same pair shows the score delta.

The matcher goes beyond naive keyword comparison: skill aliases resolve spelling variants
(k8s = kubernetes, js = javascript), an evidence search finds skills used in experience
bullets but missing from the skills section, related skills earn partial credit and show
as a separate chip group, and similarity scores are calibrated against the embedding
model so honest matches read as honest scores. Sections the JD says nothing about are
excluded from the weighted overall instead of diluting it. Extraction handles academic
profiles (publications, thesis, research roles) and German vocational qualifications
(Ausbildung, Meister, Fachinformatiker) alongside standard industry CVs.

**AI Resume Improvement** - After an analysis, one click feeds the identified gaps into a
rewrite engine that generates improved, JD-aligned bullet points grounded in your real
experience - shown as before/after pairs with copy buttons.

**Live Job Fetcher** - Pulls fresh listings from Adzuna Germany, Arbeitnow, and the
Bundesagentur fur Arbeit (up to 200 per search title with pagination), scores each against
your loaded resume, and presents them ranked by match score. Job roles are managed as
editable keyword chips; with the entry-level filter on, each role is combined with the
configured entry keywords (junior ml engineer, intern ml engineer, ...) at search time,
a free keyword gate blocks seniority and working-student markers before any LLM tokens
are spent, and an LLM gate scoped to IT & Computer Science roles makes the final call
with a deterministic seniority override. Runs stream results in live, can be stopped
mid-flight without losing scored jobs, and auto-fetch runs on a per-user schedule.
Deleting a job offers undo; application status (applied / interview / offer / rejected)
is tracked per job and summarised in a day-grouped History timeline. CSV export included.

**ATS Check and Optimise** - Scans your resume for structural issues that confuse Applicant
Tracking Systems (missing sections, formatting problems, keyword coverage), then optionally
rewrites it for the job description and exports the result as a ready-to-send DOCX.

**Resume Vault** - Store up to 3 resume versions (base + tailored). Switch between them
instantly when scoring a new job. Past analyses can be reopened in full from the History tab.

**LLM Routing** - Analyses run on OpenAI GPT-4o-mini with automatic retry and Groq fallback.
Providers are forced into native JSON mode so structured extraction can never return
malformed output. The active provider is an app-wide setting controlled by the admin account.

**Security & Beta Hardening** - Per-IP rate limits on credential endpoints and a per-user
budget on every LLM-backed endpoint, request body size caps, security headers, constant-time
invite code checks, clamped user-supplied fetch limits, opaque upload tokens (clients never
see file paths), and JD length caps on all analysis inputs. All state is scoped per user;
analysis results are cached (versioned by scoring engine) so repeat runs cost zero tokens.

---

## Score Labels

| Score | Label |
|-------|-------|
| 80 and above | Excellent Match |
| 60 to 79 | Good Match |
| 40 to 59 | Partial Match |
| Below 40 | Poor Match |

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend API | FastAPI + Uvicorn |
| Database | SQLite (local) or Turso (cloud) |
| Frontend | React 18 + Vite + Tailwind CSS |
| Animations | Framer Motion |
| PDF / DOCX parsing | pdfplumber, PyMuPDF, python-docx |
| LLM extraction | OpenAI GPT-4o / Groq / Ollama |
| Semantic dedup | sentence-transformers + ChromaDB |
| Job sources | Adzuna Germany, Arbeitnow, Bundesagentur fur Arbeit |
| Auth | JWT (HS256) via python-jose + bcrypt |

---

## Project Structure

```
JobsFitAI/
  backend/
    main.py                    FastAPI entry point, routers, scheduler
    config.yaml                LLM providers, matcher weights, job search settings
    api/routes/                One file per feature group (auth, resumes, analyzer, matches, ats)
    core/                      Config, database, security, logger, state, upload tokens
    models/                    User model (DB queries for the users table)
    repositories/              Data access layer for all other tables
    schemas/                   Pydantic request/response shapes
    tests/                     Frontend-backend contract test suite (pytest)
    services/
      ats.py                   ATS check and optimise logic
      job_matcher.py           Fetch + score pipeline orchestration (stoppable runs)
      job_relevance.py         LLM relevance gate (IT/CS scoped, seniority override)
      title_expander.py        Entry-keyword search expansion + exclude gate
      fetchers/                Adzuna, Arbeitnow, Bundesagentur fetchers
      extractors/              LLM-based resume and JD extraction
      llm/                     call_llm() router + providers (native JSON mode)
      matcher/                 Scoring engine + per-section scorers + skill aliases
      parsers/                 PDF / DOCX text extraction
      prompts/                 LLM prompt builders + JSON schemas

  frontend/
    src/
      pages/                   Landing, Login, About, Pricing, Privacy
      components/tabs/         Analyzer, ATS, JobMatches, Resumes, History, Settings
      components/              ResumePicker, AnalysisResults, TopBar, Sidebar, Toast, ui
      layouts/AppShell.jsx     Main app shell (sidebar + content)
      lib/auth.js              apiFetch() - attaches Bearer token, redirects to login on 401
      lib/errors.js            errMsg() - maps API error codes to human-readable messages
      App.jsx                  React Router config
      index.css                Design tokens + global styles
```

---

## Running Locally

**Backend**

```bash
cd backend
uv run uvicorn main:app --reload --port 8080
```

**Frontend**

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173` for the React app, or `http://localhost:8080` to hit the API directly.

**Tests** - the contract test suite runs offline against a throwaway database:

```bash
cd backend
uv run pytest tests
```

**Environment variables** - copy `.env.example` to `backend/.env` and fill in your keys:

```
JWT_SECRET=your-long-random-secret
OPENAI_API_KEY=sk-...
GROQ_API_KEY=gsk-...
ADZUNA_APP_ID=your-app-id
ADZUNA_APP_KEY=your-app-key
ADMIN_EMAILS=you@example.com        # admin role (app-wide LLM settings)
INVITE_CODE=your-beta-code          # optional: makes registration invite-only
```

Optional: `APP_ENV=production` enforces production requirements (JWT_SECRET must be set),
and `ALLOWED_ORIGINS` restricts CORS to your real domain.

---

## Secrets and Deployment Safety

Every credential is server-side only and read from the environment. **The frontend reads no
environment variables at all** - no key, public or otherwise, is bundled into the browser.
There are no Supabase, Stripe, MongoDB, or Firebase credentials in this project.

| Rule | Status |
|------|--------|
| No secret as a string literal anywhere in source | Verified - full-tree scan, none found |
| `.env` gitignored and never committed | Verified - only `.env.example` is tracked |
| Secrets absent from git history | Verified - no key pattern in any commit |
| No secret in logs (values, URLs, connection strings) | Enforced - log lines name variables, never values |
| No secret in any API response | Enforced - `/api/llm-settings` returns `has_key` as a boolean only |
| API keys sent as request params, never interpolated into logged URLs | Verified |

**Before deploying:**

1. Generate a fresh `JWT_SECRET` (`python -c "import secrets; print(secrets.token_hex(32))"`).
   Without a fixed value, every restart invalidates all sessions; with `APP_ENV=production`
   the server refuses to boot without it.
2. Set `ALLOWED_ORIGINS` to your real `https://` domain. The default allows localhost only,
   and in production the server refuses to boot on `*` or any `http://` origin.
3. Set `INVITE_CODE` to keep registration closed during beta.

---

## Data in Transit

Resumes and passwords must never cross the network in clear text. Protection is split
between the platform and the app:

**The platform terminates TLS.** The app cannot encrypt the wire itself - your host
(Fly, Railway, Render, Cloudflare, or an nginx/Caddy reverse proxy) must serve HTTPS with
a valid certificate. Run uvicorn behind it with `--proxy-headers --forwarded-allow-ips=*`
so the app can see the original scheme.

**The app refuses to be used insecurely.** With `APP_ENV=production`:

| Protection | Effect |
|------------|--------|
| `HTTPSRedirectMiddleware` | Any plain-HTTP request is redirected before a body is read |
| `Strict-Transport-Security` (1 year, includeSubDomains) | Browsers refuse `http://` for this domain entirely, even if a user types it |
| `Content-Security-Policy` | `script-src 'self'` - the JWT lives in localStorage, so blocking injected scripts is the main defence against token theft; `connect-src 'self'` stops any exfiltration endpoint |
| `secure` + `httpOnly` + `sameSite` on session cookies | Cookie is never sent over plain HTTP |
| CORS allowlist | Boot fails on `*` or `http://` origins - a wildcard with credentials would let any site read authenticated responses |
| Body size cap, per-IP and per-user rate limits | Limits abuse of the exposed surface |

All outbound calls (OpenAI, Groq, Adzuna, Arbeitnow, Bundesagentur) already use HTTPS.

**Rotate any key that was ever pasted into a file, a commit, a screenshot, or a chat.**
Git history is permanent: deleting a secret from the current code does not remove it from
earlier commits, and a leaked key stays valid until you revoke it at the provider
(OpenAI, Groq, Adzuna, Turso). Rotation is the only fix - re-writing history is not enough
if the repository was ever pushed or cloned.

---

## API Endpoints (summary)

| Group | Endpoints |
|-------|-----------|
| Auth | POST /api/auth/register, /login, /change-password; GET /me |
| Resumes | GET/POST/DELETE /api/resumes, /{id}/file, /label, /use-for-matching, /re-extract, /recommend |
| Analyzer | POST /api/upload, /resume-preview, /analyze; POST /api/improve-resume |
| Job Matches | GET /api/match/run, /state, /detail, /export; POST /applied, /app-status, /filters, /score-jd, /scheduler, /stop, /delete, /restore, /clear |
| ATS | POST /api/ats/check, /optimise, /docx |
| History | GET /api/history, /api/history/analysis |
| LLM Settings | GET/POST /api/llm-settings (POST is admin-only), GET /api/llm-ping |

---

## Current Status

Core pipeline works end to end: resume parsing, LLM extraction with canonical skill
normalization, alias- and evidence-aware semantic scoring with calibrated similarities,
keyword gap analysis with partial-credit related skills, ATS check and optimise with DOCX
export, AI resume improvement, and live job fetching from three German job boards through
a two-part entry-level gate (keyword expansion + IT/CS-scoped LLM relevance).

The React frontend is live with full auth (invite-only beta), resume vault, analyzer with
improve flow, ATS tab, job matches with editable role chips, live run progress, stop
control, undo delete and application tracking, a day-grouped history timeline, and settings
with account management. The API is hardened for public beta (rate limits, body caps,
security headers, per-user isolation). A contract + matcher test suite (42 tests) pins
every frontend-backend interaction and every scoring behaviour.

**In progress:** production deployment prep (usage quotas, GDPR account deletion, Docker),
then Pro plan billing.

---

## Connect

Built by Harman. Open to feedback, ideas, and conversations about making the job search
less of a black box.

[![LinkedIn](https://img.shields.io/badge/LinkedIn-Connect-0A66C2?style=flat&logo=linkedin)](https://linkedin.com/in/harmandeep)
[![GitHub](https://img.shields.io/badge/GitHub-Follow-181717?style=flat&logo=github)](https://github.com/harmandeep2993)

---

## License

MIT License. See LICENSE file.
