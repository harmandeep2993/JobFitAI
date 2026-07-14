# api/routes/resume_analyzer.py
"""
Analyzer endpoints: /api/upload, /api/resume-preview, /api/resume-file, /api/analyze
"""

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Set

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse

from core import uploads
from core.config import JD_MAX_CHARS, MAX_FILE_SIZE_MB, SUPPORTED_EXTENSIONS, WEIGHTS
from services.matcher.scores.skills import PARTIAL_CREDIT
from core.logger import get_logger
from services.extractors import extract_all
from services.matcher.engine import match
from services.parsers import extract_all_text
from repositories import analysis_repo as analysis_store
from repositories import cache_repo as cache_store
from repositories import resume_repo as resume_store
from services.profile_summary import generate_summary
from services.llm.caller import check_llm
from schemas.analyzer import AnalyzeRequest, ResumePreviewRequest
from api.routes.auth import get_current_user, get_current_user_llm_limited

logger = get_logger(__name__)

router = APIRouter()
ALLOWED_EXTENSIONS: Set[str] = SUPPORTED_EXTENSIONS
MAX_FILE_MB: int = MAX_FILE_SIZE_MB

# Bump when match() scoring semantics change - keys the analysis cache.
_SCORING_VERSION = "v3"

_PREVIEW_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


@router.post("/upload")
async def api_upload(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
) -> JSONResponse:
    """
    Handle resume file uploads.

    Returns {ok, name, tmp, kb, ext} on success. `tmp` is an opaque token
    resolved server-side - never a filesystem path.
    """
    if file is None:
        raise HTTPException(status_code=400, detail="no file uploaded")

    data = await file.read()
    filename = file.filename or "resume"
    suffix = Path(filename).suffix.lower()

    if len(data) == 0:
        raise HTTPException(status_code=400, detail="empty file")
    if len(data) > MAX_FILE_MB * 1024 * 1024:
        raise HTTPException(
            status_code=400, detail=f"file too large (max {MAX_FILE_MB} MB)"
        )
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported file type (allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))})",
        )

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_file:
        tmp_file.write(data)
        temp_path = tmp_file.name

    token = uploads.register(current_user["id"], temp_path)

    return JSONResponse(
        {
            "ok": True,
            "name": filename,
            "tmp": token,
            "kb": round(len(data) / 1024, 1),
            "ext": suffix.upper()[1:],
        }
    )


@router.get("/resume-file")
async def api_resume_file(
    tmp: str,
    current_user: dict = Depends(get_current_user),
) -> FileResponse:
    """Serve an uploaded temp resume for inline preview, resolved by token."""
    path = uploads.resolve(current_user["id"], tmp)
    if not path:
        return JSONResponse({"ok": False, "error": "file not found"}, status_code=404)
    suffix = Path(path).suffix.lower()
    if suffix not in _PREVIEW_TYPES:
        return JSONResponse({"ok": False, "error": "unsupported type"}, status_code=400)
    return FileResponse(path, media_type=_PREVIEW_TYPES[suffix])


@router.post("/resume-preview")
async def api_resume_preview(
    body: ResumePreviewRequest,
    current_user: dict = Depends(get_current_user),
) -> JSONResponse:
    """Extract raw text from a stored or temp resume file - no LLM, no scoring."""
    tmp = (body.tmp or "").strip()
    resume_id = (body.resume_id or "").strip()

    if resume_id:
        row = resume_store.get(current_user["id"], resume_id)
        if not row or not os.path.exists(row["file_path"]):
            return JSONResponse(
                {"ok": False, "error": "file not found"}, status_code=404
            )
        tmp = row["file_path"]
    else:
        tmp = uploads.resolve(current_user["id"], tmp)
        if not tmp:
            return JSONResponse(
                {"ok": False, "error": "file not found"}, status_code=400
            )

    text = await run_in_threadpool(lambda: extract_all_text(tmp))
    if not text:
        return JSONResponse(
            {"ok": False, "error": "could not extract text"}, status_code=422
        )
    return JSONResponse({"ok": True, "text": text[:3000], "total_chars": len(text)})


def _build_summary(raw: dict | str | None) -> dict:
    """Normalise generate_summary() output to a plain dict with guaranteed keys.

    Args:
        raw: JSON string, dict, or None from generate_summary().

    Returns:
        Dict with keys profile, strengths, gaps, focus - each a list.
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            raw = {}
    if not isinstance(raw, dict):
        raw = {}
    return {
        "profile": raw.get("profile") or [],
        "strengths": raw.get("strengths") or [],
        "gaps": raw.get("gaps") or [],
        "focus": raw.get("focus") or [],
    }


def _build_job_context(jd_json: dict) -> dict:
    """What the user is being scored against - extracted but never surfaced."""
    job = jd_json.get("job") or {}
    return {
        "title": job.get("title", ""),
        "company": job.get("company", ""),
        "location": job.get("location", ""),
        "work_mode": job.get("work_mode", ""),
        "employment_type": job.get("employment_type", ""),
        "job_level": job.get("job_level", ""),
        "summary": jd_json.get("job_summary", ""),
    }


def _build_score_math(results: dict) -> list[dict]:
    """Show how the overall score was actually built.

    Each active section contributes score x (weight / active weight). Sections
    the JD said nothing about carry no weight and are reported as excluded, so
    the number is defensible instead of magic.
    """
    ss = results.get("section_scores") or {}
    active = {s: v for s, v in ss.items() if v is not None}
    total_weight = sum(WEIGHTS.get(s, 0) for s in active) or 1.0

    rows = []
    for section, score in ss.items():
        weight = WEIGHTS.get(section, 0)
        if score is None:
            rows.append(
                {
                    "section": section,
                    "score": None,
                    "weight": weight,
                    "points": 0.0,
                    "excluded": True,
                }
            )
        else:
            rows.append(
                {
                    "section": section,
                    "score": score,
                    "weight": round(weight / total_weight, 3),
                    "points": round(score * weight / total_weight, 1),
                    "excluded": False,
                }
            )
    return rows


def _build_skill_impact(results: dict) -> list[dict]:
    """Rank missing required skills by how much each would raise the score.

    Pure arithmetic on the same weights the engine used: re-score the required
    section as if one missing skill were matched, then recompute the weighted
    overall. Turns a list of gaps into a prioritised action plan at no cost.
    """
    ss = results.get("section_scores") or {}
    req_score = ss.get("required_skills")
    missing = results.get("missing_required") or []
    if req_score is None or not missing:
        return []

    matched = len(results.get("matched_required") or [])
    partial = len(results.get("partial_required") or [])
    total = matched + partial + len(missing)
    if total == 0:
        return []

    active = {s: v for s, v in ss.items() if v is not None}
    total_weight = sum(WEIGHTS.get(s, 0) for s in active) or 1.0
    current = results.get("overall_score", 0)

    # Adding one missing skill moves it from missing to matched.
    improved_req = (matched + 1 + PARTIAL_CREDIT * partial) / total * 100
    delta = round(
        (improved_req - req_score) * WEIGHTS.get("required_skills", 0) / total_weight, 1
    )

    # Every missing required skill is worth the same gain, so order by the
    # user's real constraint: how hard it is to add. We cannot know that, so
    # keep JD order and let the UI show the shared gain.
    return [
        {
            "skill": skill,
            "gain": delta,
            "new_score": round(min(100.0, current + delta), 1),
        }
        for skill in missing
    ]


def _build_breakdown(results: dict) -> dict:
    """Map match() output into a per-section breakdown for the API response.

    A section score of None means the JD had no data for it - the frontend
    renders those as "not in JD" instead of a numeric bar.

    Args:
        results: Dict returned by match() containing section_scores and matched/missing lists.

    Returns:
        Dict keyed by section name, each with score, matched, partial, and missing lists.
    """
    ss = results.get("section_scores") or {}
    return {
        "required_skills": {
            "score": ss.get("required_skills"),
            "matched": results.get("matched_required") or [],
            "partial": results.get("partial_required") or [],
            "missing": results.get("missing_required") or [],
        },
        "preferred_skills": {
            "score": ss.get("preferred_skills"),
            "matched": results.get("matched_preferred") or [],
            "partial": results.get("partial_preferred") or [],
            "missing": results.get("missing_preferred") or [],
        },
        "responsibilities": {
            "score": ss.get("responsibilities"),
            "matched": [],
            "missing": [],
        },
        "education": {"score": ss.get("education"), "matched": [], "missing": []},
        "certifications": {
            "score": ss.get("certifications"),
            "matched": [],
            "missing": [],
        },
    }


@router.post("/analyze")
async def api_analyze(
    body: AnalyzeRequest,
    current_user: dict = Depends(get_current_user_llm_limited),
) -> JSONResponse:
    """
    Full analysis pipeline: parse resume -> extract -> score -> summarize.

    Returns structured JSON - no HTML blobs.
    """
    tmp = (body.tmp or "").strip()
    resume_id = (body.resume_id or "").strip()
    jd_text = (body.jd or "").strip()

    if resume_id:
        row = resume_store.get(current_user["id"], resume_id)
        if not row or not os.path.exists(row["file_path"]):
            return JSONResponse(
                {"ok": False, "error": "resume file not found"}, status_code=400
            )
        tmp = row["file_path"]
    else:
        tmp = uploads.resolve(current_user["id"], tmp)
        if not tmp:
            return JSONResponse(
                {"ok": False, "error": "resume file not found"}, status_code=400
            )

    if len(jd_text) < 50:
        return JSONResponse(
            {
                "ok": False,
                "error": "jd_too_short",
                "message": "Job description must be at least 50 characters.",
            },
            status_code=400,
        )

    if len(jd_text) > JD_MAX_CHARS:
        logger.warning("JD truncated from %d to %d chars", len(jd_text), JD_MAX_CHARS)
        jd_text = jd_text[:JD_MAX_CHARS]

    # _SCORING_VERSION busts the cache when the scoring engine changes, so a
    # cache hit never serves scores from an older engine. Old entries stay in
    # the table so history rows keep reopening.
    cache_key = hashlib.sha256(
        f"{_SCORING_VERSION}::{resume_id or tmp}::{jd_text}".encode()
    ).hexdigest()
    cached = cache_store.get(cache_key)
    if cached:
        logger.info("Cache hit for analysis %s", cache_key[:12])
        return JSONResponse({"ok": True, "cached": True, **cached})

    if not check_llm():
        return JSONResponse({"ok": False, "error": "llm_unavailable"}, status_code=503)

    def _run():
        resume_text = extract_all_text(tmp)
        if not resume_text or len(resume_text) < 50:
            return None, None, None, None

        resume_json, jd_json = extract_all(resume_text, jd_text)
        if not resume_json or not jd_json:
            return resume_json, jd_json, None, None

        # llm_judge: the Analyser scores one job for one user, so it can afford
        # the LLM responsibility-coverage call that bulk job scoring cannot.
        results = match(resume_json, jd_json, llm_judge=True)
        summary = generate_summary(resume_json, jd_json, results)
        return resume_json, jd_json, results, summary

    try:
        resume_json, jd_json, results, summary = await run_in_threadpool(_run)
    except Exception as e:
        # Multi-step pipeline - any layer (parser, LLM, matcher) can raise
        logger.error("analyze failed: %s", e)
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    if not resume_json and not jd_json:
        return JSONResponse(
            {"ok": False, "error": "could_not_parse_resume"}, status_code=422
        )
    if not resume_json:
        return JSONResponse(
            {"ok": False, "error": "resume_extraction_failed"}, status_code=422
        )
    if not jd_json:
        return JSONResponse(
            {"ok": False, "error": "jd_extraction_failed"}, status_code=422
        )
    if not results:
        return JSONResponse({"ok": False, "error": "scoring_failed"}, status_code=500)

    payload = {
        "score": results.get("overall_score", 0),
        "label": results.get("label", ""),
        "summary": _build_summary(summary),
        "breakdown": _build_breakdown(results),
        # What the user is actually being scored against
        "job": _build_job_context(jd_json),
        # How the number was built, section by section
        "score_math": _build_score_math(results),
        # Prioritised action plan: what each missing skill is worth
        "skill_impact": _build_skill_impact(results),
        # Where each skill was found (listed / proven in a bullet / related)
        "evidence": {
            "required": results.get("required_evidence") or {},
            "preferred": results.get("preferred_evidence") or {},
        },
        # Hard requirements (years, language level): warnings, never points
        "gates": results.get("gates") or {},
        # Per-duty verdicts from the LLM coverage judge
        "duties": {
            "demonstrated": results.get("demonstrated_duties") or [],
            "partial": results.get("partial_duties") or [],
            "missing": results.get("missing_duties") or [],
        },
        "keywords": {
            "matched": results.get("matched_required") or [],
            "partial": results.get("partial_required") or [],
            "missing": results.get("missing_required") or [],
        },
    }
    cache_store.set(cache_key, payload)

    if resume_id:
        try:
            analysis_store.save(
                current_user["id"],
                resume_id,
                jd_text,
                results.get("overall_score", 0),
                results.get("label", ""),
                cache_hash=cache_key,
            )
        except Exception as e:
            logger.error("analysis_store.save failed: %s", e)

    return JSONResponse({"ok": True, "cached": False, **payload})
