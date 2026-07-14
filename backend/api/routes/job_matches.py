# api/routes/job_matches.py
"""
/api/match/* endpoints - job fetch, score, and state.

All routes are scoped to the authenticated user via get_current_user.
"""

import asyncio
import csv
import io
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, StreamingResponse

from api.routes.auth import get_current_user, get_current_user_llm_limited
from core.config import JD_MAX_CHARS, MAX_AGE_DAYS, SEARCH_PER_TITLE
from core.logger import get_logger
from core import state, uploads
from core.state import sched_last_ref
from services.extractors.jd_extractor import extract_jd
from services.matcher.engine import match
from services.parsers import extract_all_text
from services.extractors.resume_extractor import extract_resume
from repositories import event_repo as event_store
from repositories import match_repo as match_store
from repositories import settings_repo as settings_store
from services.job_matcher import (
    begin_run,
    discover_and_score,
    end_run,
    fetch_combined,
    get_run_status,
    request_stop,
    rescore_all,
)
from services.profile_summary import generate_summary
from schemas.matches import (
    AppliedRequest,
    AppStatusRequest,
    DeleteJobRequest,
    FiltersRequest,
    MatchResumeRequest,
    ScoreJdRequest,
    SchedulerRequest,
)

logger = get_logger(__name__)

router = APIRouter()


def _resume_diff(old: dict, new: dict) -> dict:
    """Compare two extracted resume dicts and return what changed."""

    def flat_skills(r):
        s = r.get("skills", [])
        if isinstance(s, list):
            return {x.lower().strip() for x in s if isinstance(x, str)}
        if isinstance(s, dict):
            out = set()
            for v in s.values():
                if isinstance(v, list):
                    out.update(x.lower().strip() for x in v if isinstance(x, str))
            return out
        return set()

    old_skills = flat_skills(old)
    new_skills = flat_skills(new)

    old_exp = {
        (e.get("title", "") + "|" + e.get("company", "")).lower()
        for e in old.get("experience_entries", [])
    }
    new_exp = {
        (e.get("title", "") + "|" + e.get("company", "")).lower()
        for e in new.get("experience_entries", [])
    }

    old_yrs = (old.get("meta") or {}).get("total_experience_years", 0)
    new_yrs = (new.get("meta") or {}).get("total_experience_years", 0)

    return {
        "skills_added": sorted(new_skills - old_skills),
        "skills_removed": sorted(old_skills - new_skills),
        "exp_added": sorted(new_exp - old_exp),
        "exp_removed": sorted(old_exp - new_exp),
        "years_before": old_yrs,
        "years_after": new_yrs,
        "languages_before": old.get("languages", []),
        "languages_after": new.get("languages", []),
    }


@router.post("/resume")
async def api_match_resume(
    body: MatchResumeRequest,
    current_user: dict = Depends(get_current_user_llm_limited),
) -> JSONResponse:
    """Parse + extract an uploaded resume and store it for this user's matching session."""
    user_id = current_user["id"]
    tmp = uploads.resolve(user_id, (body.tmp or "").strip())
    name = (body.name or "resume").strip()

    if not tmp:
        raise HTTPException(status_code=400, detail="resume file not found")

    def _process() -> dict:
        text = extract_all_text(tmp)
        if not text or len(text) < 50:
            return {}
        return extract_resume(text)

    resume_json = await run_in_threadpool(_process)

    if not resume_json:
        raise HTTPException(status_code=422, detail="could not parse resume")

    prev_resume = state.get_resume(user_id)
    diff = _resume_diff(prev_resume, resume_json) if prev_resume else None

    state.set_resume(user_id, resume_json, name)

    rescored = await run_in_threadpool(rescore_all, user_id)
    if rescored:
        event_store.log_event(
            user_id, "rescore", "", f"{rescored} jobs re-scored vs {name}"
        )

    years = resume_json.get("meta", {}).get("total_experience_years", 0)
    return JSONResponse(
        {
            "ok": True,
            "name": name,
            "experience_years": years,
            "rescored": rescored,
            "diff": diff,
        }
    )


@router.get("/run")
async def api_match_run(
    query: str = Query(default=""),
    entry_only: str = Query(default="true"),
    location: str = Query(default=""),
    current_user: dict = Depends(get_current_user),
) -> JSONResponse:
    """Kick off a background job-fetch-and-score run for this user.

    Returns immediately with {started: true} and runs the pipeline in a
    background task. Only one run per user can be active at a time.
    """
    user_id = current_user["id"]

    if not state.has_resume(user_id):
        return JSONResponse(
            {
                "ok": False,
                "error": "no_resume",
                "results": match_store.get_all(user_id),
            },
            status_code=400,
        )

    if not begin_run(user_id):
        return JSONResponse({"ok": True, "started": False, "already_running": True})

    query = query.strip()
    entry_only_flag = entry_only.lower() != "false"
    titles = [query] if query else settings_store.get_titles(user_id)
    location = location.strip() or settings_store.get_location(user_id)
    countries = settings_store.get_countries(user_id)

    async def _bg() -> None:
        def _run() -> None:
            jobs = fetch_combined(
                titles,
                location=location,
                countries=countries,
                per_title=SEARCH_PER_TITLE,
                arbeitnow_limit=settings_store.get_arbeitnow_limit(user_id),
                bundesagentur_limit=settings_store.get_bundesagentur_limit(user_id),
                entry_only=entry_only_flag,
            )
            discover_and_score(
                jobs,
                user_id=user_id,
                entry_only=entry_only_flag,
                manual=True,
                titles=titles,
            )

        try:
            await run_in_threadpool(_run)
        except Exception as e:
            logger.error("match run failed for user %s: %s", user_id, e)
            try:
                event_store.log_event(
                    user_id,
                    "run",
                    "",
                    json.dumps(
                        {
                            "fetched": 0,
                            "new": 0,
                            "recent": 0,
                            "relevant": 0,
                            "scored": 0,
                            "adzuna": 0,
                            "arbeitnow": 0,
                            "bundesagentur": 0,
                            "total_seen": 0,
                            "manual": True,
                            "error": str(e)[:120],
                        }
                    ),
                )
            except Exception as log_err:
                logger.warning("Could not log error run event: %s", log_err)
            end_run(user_id)

    asyncio.create_task(_bg())
    return JSONResponse({"ok": True, "started": True})


@router.post("/stop")
async def api_match_stop(
    current_user: dict = Depends(get_current_user),
) -> JSONResponse:
    """Stop the current fetch-and-score run at the next job boundary.

    Jobs already scored are kept. Returns stopped=false when no run was active.
    """
    stopped = request_stop(current_user["id"])
    return JSONResponse({"ok": True, "stopped": stopped})


@router.get("/state")
async def api_match_state(
    current_user: dict = Depends(get_current_user),
) -> JSONResponse:
    """Full state snapshot polled by the frontend while a run is active."""
    user_id = current_user["id"]
    return JSONResponse(
        {
            "ok": True,
            "has_resume": state.has_resume(user_id),
            "resume_name": state.get_resume_name(user_id),
            "filters": {
                "target_titles": settings_store.get_titles(user_id),
                "countries": settings_store.country_names(user_id),
                "location": settings_store.get_location(user_id),
                "max_age_days": MAX_AGE_DAYS,
                "entry_only": settings_store.get_entry_only(user_id),
                "arbeitnow_limit": settings_store.get_arbeitnow_limit(user_id),
                "bundesagentur_limit": settings_store.get_bundesagentur_limit(user_id),
            },
            "stats": event_store.stats(user_id),
            "last_run": event_store.last_run(user_id),
            "run_status": get_run_status(user_id),
            "resume": state.resume_info(user_id),
            "scheduler": {
                "enabled": settings_store.get_scheduler_enabled(user_id),
                "interval": settings_store.get_scheduler_interval(user_id),
            },
            "results": match_store.get_all(user_id),
        }
    )


@router.post("/applied")
async def api_match_applied(
    body: AppliedRequest,
    current_user: dict = Depends(get_current_user),
) -> JSONResponse:
    """Toggle the applied flag on a scored job and log an 'applied' event."""
    user_id = current_user["id"]
    job_id = (body.id or "").strip()
    applied = bool(body.applied)
    if not job_id:
        raise HTTPException(status_code=400, detail="id required")
    if not match_store.set_applied(user_id, job_id, applied):
        raise HTTPException(status_code=404, detail="job not found")
    if applied:
        event_store.log_event(user_id, "applied", job_id)
    return JSONResponse({"ok": True, "id": job_id, "applied": applied})


@router.post("/app-status")
async def api_match_app_status(
    body: AppStatusRequest,
    current_user: dict = Depends(get_current_user),
) -> JSONResponse:
    """Set the application status for a job (applied / interview / offer / rejected)."""
    user_id = current_user["id"]
    job_id = (body.id or "").strip()
    status = (body.status or "").strip().lower()
    if not job_id:
        raise HTTPException(status_code=400, detail="id required")
    if status not in match_store.VALID_APP_STATUSES:
        raise HTTPException(status_code=400, detail="invalid_status")
    if not match_store.set_app_status(user_id, job_id, status):
        raise HTTPException(status_code=404, detail="job not found")
    if status == "applied":
        event_store.log_event(user_id, "applied", job_id)
    return JSONResponse({"ok": True, "id": job_id, "app_status": status})


@router.get("/detail")
async def api_match_detail(
    id: str = Query(default=""),
    current_user: dict = Depends(get_current_user),
) -> JSONResponse:
    """Return full detail for a scored job, lazily generating an LLM summary if absent."""
    user_id = current_user["id"]
    job_id = id.strip()
    row = match_store.get_one(user_id, job_id)
    if not row:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)

    summary = row.get("summary")
    if not summary and state.has_resume(user_id):
        resume_json = state.get_resume(user_id)
        results = {
            "overall_score": row.get("score", 0),
            "label": row.get("label", ""),
            "section_scores": row.get("section_scores", {}),
            "matched_required": row.get("matched_required", []),
            "missing_required": row.get("missing_required", []),
            "matched_preferred": [],
            "missing_preferred": [],
        }
        try:
            summary = await run_in_threadpool(
                generate_summary, resume_json, row.get("jd_json", {}), results
            )
            match_store.set_summary(user_id, job_id, summary)
        except Exception as e:
            logger.error("summary generation failed: %s", e)
            summary = ""

    return JSONResponse(
        {
            "ok": True,
            "job": {
                "id": row["id"],
                "title": row["title"],
                "company": row["company"],
                "location": row["location"],
                "url": row["url"],
                "language": row["language"],
                "posted_at": row["posted_at"],
                "score": row["score"],
                "label": row["label"],
                "applied": row.get("applied", 0),
                "app_status": row.get("app_status") or "",
                "source": row.get("source") or "",
                "status": row.get("status") or "",
            },
            "section_scores": row.get("section_scores", {}),
            "matched_required": row.get("matched_required", []),
            "missing_required": row.get("missing_required", []),
            "jd": row.get("jd_json", {}),
            "resume": state.resume_info(user_id),
            "summary": summary or "",
        }
    )


@router.post("/filters")
async def api_match_filters(
    body: FiltersRequest,
    current_user: dict = Depends(get_current_user),
) -> JSONResponse:
    """Update target titles, countries, and/or location filter for this user."""
    user_id = current_user["id"]

    if isinstance(body.target_titles, list):
        settings_store.set_titles(user_id, body.target_titles)

    if body.countries is not None:
        c = body.countries
        names = c if isinstance(c, list) else str(c).replace(",", " ").split()
        settings_store.set_countries(user_id, names)

    if body.location is not None:
        settings_store.set_location(user_id, body.location or "")

    if body.entry_only is not None:
        settings_store.set_entry_only(user_id, bool(body.entry_only))

    if body.arbeitnow_limit is not None:
        try:
            settings_store.set_arbeitnow_limit(user_id, int(body.arbeitnow_limit))
        except (ValueError, TypeError):
            pass

    if body.bundesagentur_limit is not None:
        try:
            settings_store.set_bundesagentur_limit(
                user_id, int(body.bundesagentur_limit)
            )
        except (ValueError, TypeError):
            pass

    return JSONResponse(
        {
            "ok": True,
            "target_titles": settings_store.get_titles(user_id),
            "countries": settings_store.country_names(user_id),
            "location": settings_store.get_location(user_id),
            "entry_only": settings_store.get_entry_only(user_id),
            "arbeitnow_limit": settings_store.get_arbeitnow_limit(user_id),
            "bundesagentur_limit": settings_store.get_bundesagentur_limit(user_id),
        }
    )


@router.post("/score-jd")
async def api_score_jd(
    body: ScoreJdRequest,
    current_user: dict = Depends(get_current_user_llm_limited),
) -> JSONResponse:
    """Score a jd_unavailable job using a manually pasted JD."""
    user_id = current_user["id"]
    job_id = (body.id or "").strip()
    jd_text = (body.jd_text or "").strip()[:JD_MAX_CHARS]

    if not job_id:
        raise HTTPException(status_code=400, detail="id required")
    if len(jd_text) < 50:
        raise HTTPException(status_code=400, detail="jd_text too short")
    if not state.has_resume(user_id):
        raise HTTPException(status_code=400, detail="no_resume")

    row = match_store.get_one(user_id, job_id)
    if not row:
        raise HTTPException(status_code=404, detail="job not found")

    def _score():
        try:
            jd_json = extract_jd(jd_text)
        except Exception as exc:
            logger.warning("extract_jd failed: %s", exc)
            return None, None
        if not jd_json:
            return None, None
        try:
            # Single user-requested job - worth the LLM coverage judgement.
            return jd_json, match(state.get_resume(user_id), jd_json, llm_judge=True)
        except Exception as exc:
            logger.exception("match() failed in score-jd: %s", exc)
            return None, None

    jd_json, result = await run_in_threadpool(_score)
    if not result:
        raise HTTPException(status_code=422, detail="could not parse JD")

    match_store.upsert(
        user_id,
        [
            {
                "id": job_id,
                "source": row["source"],
                "title": row["title"],
                "company": row["company"],
                "location": row["location"],
                "url": row["url"],
                "language": row["language"],
                "posted_at": row["posted_at"],
                "score": result.get("overall_score", 0),
                "label": result.get("label", ""),
                "matched_required": result.get("matched_required", []),
                "missing_required": result.get("missing_required", []),
                "section_scores": result.get("section_scores", {}),
                "scored_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "jd_json": jd_json,
                "status": "scored",
            }
        ],
    )

    return JSONResponse(
        {
            "ok": True,
            "score": result.get("overall_score", 0),
            "label": result.get("label", ""),
        }
    )


@router.post("/scheduler")
async def api_match_scheduler(
    body: SchedulerRequest,
    current_user: dict = Depends(get_current_user),
) -> JSONResponse:
    """Enable/disable the auto-fetch scheduler or change its interval (minutes)."""
    user_id = current_user["id"]

    if body.enabled is not None:
        settings_store.set_scheduler_enabled(user_id, bool(body.enabled))
        if body.enabled:
            # Reset the last-run timestamp so the scheduler fires promptly.
            sched_last_ref.pop(user_id, None)
    if body.interval is not None:
        settings_store.set_scheduler_interval(user_id, int(body.interval))
    return JSONResponse(
        {
            "ok": True,
            "enabled": settings_store.get_scheduler_enabled(user_id),
            "interval": settings_store.get_scheduler_interval(user_id),
        }
    )


@router.post("/delete")
async def api_match_delete(
    body: DeleteJobRequest,
    current_user: dict = Depends(get_current_user),
) -> JSONResponse:
    """Soft-delete a job match and block its id from re-appearing in fetches.

    Restorable via POST /api/match/restore until the user clears all matches.
    """
    user_id = current_user["id"]
    job_id = (body.id or "").strip()
    if not job_id:
        raise HTTPException(status_code=400, detail="id required")
    if not match_store.delete(user_id, job_id):
        raise HTTPException(status_code=404, detail="job not found")
    event_store.block(job_id, user_id)
    return JSONResponse({"ok": True, "id": job_id})


@router.post("/restore")
async def api_match_restore(
    body: DeleteJobRequest,
    current_user: dict = Depends(get_current_user),
) -> JSONResponse:
    """Undo a job deletion: bring the match back and unblock its id."""
    user_id = current_user["id"]
    job_id = (body.id or "").strip()
    if not job_id:
        raise HTTPException(status_code=400, detail="id required")
    if not match_store.restore(user_id, job_id):
        raise HTTPException(status_code=404, detail="job not found")
    event_store.unblock(job_id, user_id)
    return JSONResponse({"ok": True, "id": job_id})


@router.post("/clear")
async def api_match_clear(
    current_user: dict = Depends(get_current_user),
) -> JSONResponse:
    """Wipe all matches and events for this user.

    The vector dedup store is shared across users and is not cleared here -
    clearing it for one user would corrupt dedup state for all other users.
    """
    user_id = current_user["id"]
    match_store.clear(user_id)
    event_store.clear(user_id)
    return JSONResponse({"ok": True})


@router.get("/export")
async def api_match_export(
    current_user: dict = Depends(get_current_user),
) -> StreamingResponse:
    """Download all scored matches for this user as a CSV file."""
    user_id = current_user["id"]
    rows = match_store.get_all(user_id)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "title",
            "company",
            "location",
            "score",
            "label",
            "language",
            "posted_at",
            "applied",
            "url",
        ]
    )
    for r in rows:
        posted = r.get("posted_at") or ""
        if posted and posted.isdigit():
            try:
                posted = datetime.fromtimestamp(int(posted), tz=timezone.utc).strftime(
                    "%Y-%m-%d"
                )
            except (OSError, OverflowError):
                pass
        writer.writerow(
            [
                r.get("title", ""),
                r.get("company", ""),
                r.get("location", ""),
                r.get("score", ""),
                r.get("label", ""),
                r.get("language", ""),
                posted,
                "yes" if r.get("applied") else "no",
                r.get("url", ""),
            ]
        )

    buf.seek(0)
    filename = "jobsfitai_matches.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
