# services/job_matcher.py
"""
Job matching service.

Scores fetched jobs against the stored resume and persists the results.
Each job costs one JD-extraction LLM call; the resume is extracted once
(held in session per user) and reused across all jobs for that user.
"""

import json as _json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from services.extractors.jd_extractor import extract_jd
from services.fetchers import (
    Job,
    fetch_adzuna_multi,
    fetch_arbeitnow_jobs,
    fetch_bundesagentur_jobs,
)
from services.fetchers.job_enricher import fetch_full_description
from services.matcher import gates, match
from repositories import event_repo, match_repo
from services import (
    job_relevance as relevance,
    role_filter,
    title_expander,
    vector_store,
)
from core import state as session
from core.config import MAX_AGE_DAYS, MAX_EXPERIENCE_YEARS
from core.logger import get_logger

logger = get_logger(__name__)

# Below this length an Adzuna description is just a snippet - fetch the full JD.
_SNIPPET_LIMIT = 1200


def _enrich(job: Job) -> None:
    """Replace a thin or empty description with the full JD scraped from the posting URL.

    Applies to Adzuna snippets and Bundesagentur jobs where the real JD lives
    on an external site (StepStone, LinkedIn, etc.).
    """
    needs_enrich = (
        job.source == "adzuna" and len(job.description) < _SNIPPET_LIMIT
    ) or (job.source == "bundesagentur" and len(job.description) < _SNIPPET_LIMIT)
    if not needs_enrich:
        return
    full = fetch_full_description(job.url)
    if full and len(full) > len(job.description):
        logger.debug(
            "Enriched '%s': %d -> %d chars",
            job.title[:40],
            len(job.description),
            len(full),
        )
        job.description = full


def _score_one(job: Job, resume_json: dict) -> dict | None:
    """Extract a job's JD and score the resume against it."""
    try:
        jd_json = extract_jd(job.description)
        if not jd_json:
            logger.warning("Empty JD extraction for '%s'", job.title)
            return None

        result = match(resume_json, jd_json)

        return {
            "id": job.id,
            "source": job.source,
            "title": job.title,
            "company": job.company,
            "location": job.location,
            "url": job.url,
            "language": job.language,
            "posted_at": job.posted_at,
            "score": result.get("overall_score", 0),
            "label": result.get("label", ""),
            "matched_required": result.get("matched_required", []),
            "missing_required": result.get("missing_required", []),
            "section_scores": result.get("section_scores", {}),
            "scored_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "jd_json": jd_json,
        }
    except Exception as e:
        logger.error("Scoring failed for '%s': %s", job.title, e)
        return None


# === Per-user run status ===
# Progress of the current/last run per user, polled by the dashboard for live updates.
_run_statuses: dict[str, dict] = {}

# Guards the check-and-set on a user's "running" flag. begin_run() (manual
# route) and discover_and_score() (scheduler + any direct caller) both claim
# it, so two runs can never pass the gate at once. Without this the scheduler
# could start a scoring loop while a manual run's loop is still live - both
# then increment the same status["checked"], which runs past status["total"]
# (the "688 of 446 checked" symptom).
_status_lock = threading.Lock()

# Phases during which a scoring loop is actively stepping through jobs. A
# second run entering while the status sits in one of these is a duplicate.
_ACTIVE_SCORING_PHASES = ("classifying", "scoring", "stopping")


def _default_status() -> dict:
    """Return a blank run-status dict."""
    return {
        "running": False,
        "phase": "idle",
        "checked": 0,
        "scored": 0,
        "total": 0,
        "cancel": False,
    }


def get_run_status(user_id: str) -> dict:
    """Return a snapshot of the current run's progress for this user."""
    return dict(_run_statuses.get(user_id, _default_status()))


def begin_run(user_id: str) -> bool:
    """Claim the run slot for this user. Returns False if one is already running."""
    # Atomic check-and-set: two callers racing here (manual fetch + scheduler)
    # must not both see "not running" and proceed.
    with _status_lock:
        if _run_statuses.get(user_id, {}).get("running"):
            return False
        _run_statuses[user_id] = {
            "running": True,
            "phase": "fetching",
            "checked": 0,
            "scored": 0,
            "total": 0,
            "cancel": False,
        }
        return True


def request_stop(user_id: str) -> bool:
    """Ask the current run to stop at the next job boundary.

    Returns True when a run was active and the stop was requested. The
    pipeline checks the flag between phases and between jobs, so already
    fetched-and-scored results are kept.
    """
    status = _run_statuses.get(user_id)
    if not status or not status.get("running"):
        return False
    status["cancel"] = True
    status["phase"] = "stopping"
    return True


def end_run(user_id: str) -> None:
    """Force-clear the running flag for this user (e.g. after a failure)."""
    if user_id in _run_statuses:
        _run_statuses[user_id]["running"] = False
        _run_statuses[user_id]["phase"] = "idle"


# Floor for the per-query Adzuna budget after entry-keyword expansion, so
# each expanded query still fills at least a good part of one result page.
_MIN_PER_QUERY = 20


def fetch_combined(
    titles: list[str],
    location: str = "",
    countries: list[str] | None = None,
    per_title: int = 5,
    arbeitnow_limit: int = 100,
    bundesagentur_limit: int = 500,
    entry_only: bool = False,
    seen_ids: set[str] | None = None,
) -> list[Job]:
    """Pull jobs from all sources across one or more countries and merge.

    - Adzuna: server-side search per target title, per country code.
    - Arbeitnow: Germany feed filtered to target-title roles (DE only).
    - Bundesagentur: Germany official job board via public API (DE only).

    When entry_only is set, plain titles are combined with the configured
    entry keywords ("ml engineer" -> "junior ml engineer", "intern ml
    engineer", ...) so sources surface entry postings that generic queries
    push past the pagination cap. The per-title budget is split across the
    expanded queries to keep total API usage roughly constant.

    Deduped by id; the LLM funnel and vector dedup refine further.
    """
    countries = countries or ["de"]

    queries = title_expander.entry_search_queries(titles) if entry_only else titles
    expansion = max(1, len(queries) // max(1, len(titles)))
    per_query = max(_MIN_PER_QUERY, per_title // expansion) if entry_only else per_title

    adzuna: list[Job] = []
    for code in countries:
        # With seen_ids, each query walks the date-sorted pool until it reaches
        # already-processed jobs - no job in the age window is skipped, and
        # repeat runs only pay for pages containing something new.
        adzuna += fetch_adzuna_multi(
            queries,
            location=location,
            country=code,
            per_title=per_query,
            seen_ids=seen_ids,
        )

    arbeitnow: list[Job] = []
    bundesagentur: list[Job] = []
    if "de" in countries:
        # Arbeitnow: word-union query is fine since it filters in Python afterwards
        terms = sorted({w for t in titles for w in t.split()})
        arb_raw = fetch_arbeitnow_jobs(
            query=" ".join(terms), location=location, limit=arbeitnow_limit
        )
        arbeitnow = [j for j in arb_raw if role_filter.is_target_role(j.title, titles)]

        # Bundesagentur: fetch all queries in parallel - each query is an
        # independent API search so there is no shared state to protect.
        seen_ba: set[str] = set()

        def _ba_fetch(title: str) -> list[Job]:
            return fetch_bundesagentur_jobs(
                query=title, location=location, max_age_days=MAX_AGE_DAYS
            )

        with ThreadPoolExecutor(max_workers=min(6, len(queries))) as pool:
            futures = {pool.submit(_ba_fetch, t): t for t in queries}
            for fut in as_completed(futures):
                try:
                    for job in fut.result():
                        if job.id not in seen_ba:
                            seen_ba.add(job.id)
                            bundesagentur.append(job)
                except Exception as e:
                    logger.warning(
                        "BA fetch failed for title '%s': %s", futures[fut], e
                    )

    merged, seen_ids, seen_content = [], set(), set()
    for job in adzuna + arbeitnow + bundesagentur:
        ckey = (
            (job.title or "").strip().lower()
            + "|"
            + (job.company or "").strip().lower()
        )
        if job.id and job.id not in seen_ids and ckey not in seen_content:
            seen_ids.add(job.id)
            seen_content.add(ckey)
            merged.append(job)

    logger.info(
        "Combined sources (%s): %d adzuna + %d arbeitnow + %d bundesagentur -> %d unique",
        ",".join(countries),
        len(adzuna),
        len(arbeitnow),
        len(bundesagentur),
        len(merged),
    )
    return merged


def rescore_all(user_id: str) -> int:
    """Re-score every stored job for this user against the current resume.

    Uses the cached JD - no LLM tokens and no re-fetch. Used when a new
    resume is loaded. Returns the number of jobs re-scored.
    """
    resume_json = session.get_resume(user_id)
    if not resume_json:
        return 0

    rows = match_repo.rows_with_jd(user_id)
    if not rows:
        return 0

    resume_name = session.get_resume_name(user_id) or "resume"
    logger.info(
        "Rescoring %d stored jobs for user %s against '%s' (no LLM - cached JD only)",
        len(rows),
        user_id,
        resume_name,
    )

    count = 0
    for row in rows:
        jd = row.get("jd")
        if not jd:
            continue
        try:
            result = match(resume_json, jd)
            match_repo.update_score(user_id, row["id"], result)
            count += 1
        except Exception as e:
            logger.error("Re-score failed for %s: %s", row["id"], e)

    logger.info(
        "Rescore complete for user %s: %d/%d jobs updated", user_id, count, len(rows)
    )
    return count


def discover_and_score(
    jobs: list[Job],
    user_id: str,
    entry_only: bool = True,
    manual: bool = False,
    titles: list[str] | None = None,
) -> dict:
    """Token-efficient funnel for one user's job run.

    skip already-seen -> recency -> keyword exclude gate -> cheap LLM
    relevance gate -> full-JD enrich + extract + score.

    Each scored job is persisted immediately so the dashboard can show
    results streaming in. Progress is exposed via get_run_status(user_id).

    Args:
        jobs: Raw job list from fetch_combined.
        user_id: The user this run belongs to.
        entry_only: When True, filter to entry-level roles only.
        manual: Whether this was a user-triggered run (vs scheduler).
        titles: The user's target titles - used to decide whether student
            posts (werkstudent/praktikum) were explicitly asked for.
    """
    # Claim the run slot. begin_run() already set this for the manual route and
    # the scheduler calls begin_run() too; this guard is the backstop. If a
    # scoring loop is already stepping through jobs for this user, a second
    # entry here would share status["checked"] with it - refuse instead.
    with _status_lock:
        existing = _run_statuses.get(user_id)
        if existing and existing.get("phase") in _ACTIVE_SCORING_PHASES:
            logger.warning(
                "discover_and_score already active for user %s (phase=%s) - dropping duplicate run",
                user_id,
                existing["phase"],
            )
            return {
                "checked": 0,
                "scored": 0,
                "skipped": True,
                "results": match_repo.get_all(user_id),
            }
        status = _run_statuses.setdefault(user_id, _default_status())
        status["running"] = True  # idempotent - begin_run set this for the manual route

    resume_json = session.get_resume(user_id)
    if not resume_json:
        status.update({"running": False, "phase": "idle"})
        return {
            "error": "no_resume",
            "checked": 0,
            "scored": 0,
            "results": match_repo.get_all(user_id),
        }

    seen = event_repo.seen_ids(user_id)
    new_jobs = [j for j in jobs if j.id and j.id not in seen]

    # --- recency filter (free) ---
    candidates = []
    for job in new_jobs:
        if role_filter.is_recent(job):
            candidates.append(job)
        else:
            event_repo.mark_seen(job, user_id, "stale")

    logger.info(
        ">> Run user=%s: %d fetched, %d new, %d recent",
        user_id,
        len(jobs),
        len(new_jobs),
        len(candidates),
    )

    # --- deterministic keyword gate (free) ---
    # Blocks seniority markers and, unless the user asked for them, student
    # posts (werkstudent/praktikum) before any LLM tokens are spent.
    blocked_terms = title_expander.exclude_terms(titles or [], entry_only)
    if blocked_terms:
        kept = []
        for job in candidates:
            if title_expander.title_blocked(job.title, blocked_terms):
                event_repo.mark_seen(job, user_id, "not_entry")
            else:
                kept.append(job)
        if len(kept) < len(candidates):
            logger.info(
                "   keyword gate blocked %d of %d for user %s",
                len(candidates) - len(kept),
                len(candidates),
                user_id,
            )
        candidates = kept

    # Stop requested while sources were being fetched - keep nothing pending.
    if status.get("cancel"):
        logger.info("Run stopped by user %s before classification", user_id)
        status.update({"running": False, "phase": "idle", "cancel": False})
        return {"checked": 0, "scored": 0, "results": match_repo.get_all(user_id)}

    # --- bulk LLM relevance gate (few calls, not one-per-job) ---
    status.update(
        {"phase": "classifying", "total": len(candidates), "checked": 0, "scored": 0}
    )
    verdicts = relevance.classify_batch(candidates, titles=titles)

    survivors = []
    for job in candidates:
        verdict = verdicts.get(job.id, {"relevant": True, "entry_level": True})
        if not verdict["relevant"]:
            event_repo.mark_seen(job, user_id, "irrelevant")
        elif entry_only and not verdict["entry_level"]:
            event_repo.mark_seen(job, user_id, "not_entry")
        else:
            survivors.append(job)

    logger.info(
        "   %d relevant entry-level survivors for user %s", len(survivors), user_id
    )
    status.update({"phase": "scoring", "total": len(survivors), "checked": 0})

    scored = 0
    stopped = False
    try:
        for job in survivors:
            # User-requested stop: finish cleanly, keeping what was scored.
            if status.get("cancel"):
                stopped = True
                logger.info(
                    "Run stopped by user %s after %d of %d jobs",
                    user_id,
                    status["checked"],
                    len(survivors),
                )
                break
            status["checked"] += 1
            tag = f"{job.title[:42]} @ {job.company[:20]}"

            # cross-source near-duplicate dedup (free, local embeddings)
            if vector_store.is_duplicate(job):
                event_repo.mark_seen(job, user_id, "duplicate")
                logger.debug("   skip duplicate | %s", tag)
                continue

            # store metadata now (appears on dashboard), then enrich + score
            match_repo.upsert_pending(user_id, job)
            vector_store.add(job)

            _enrich(job)
            item = _score_one(job, resume_json)
            jd = item.get("jd_json") if item else None
            has_jd = bool(
                jd and (jd.get("required_skills") or jd.get("responsibilities"))
            )

            # Post-score entry gate: the title got the job this far, but the
            # extracted JD is the real evidence. If it says senior or asks for
            # more years than an entry candidate has, drop it now - even though
            # we already paid to score it. Better a wasted score than a senior
            # role cluttering an entry-level list.
            if entry_only and has_jd:
                reason = gates.jd_requires_seniority(jd, MAX_EXPERIENCE_YEARS)
                if reason:
                    match_repo.delete(user_id, job.id)  # remove the pending row
                    event_repo.mark_seen(job, user_id, "not_entry")
                    logger.debug("   dropped (not entry: %s) | %s", reason, tag)
                    continue

            if item and has_jd:
                item["status"] = "scored"
                match_repo.upsert(user_id, [item])
                scored += 1
                status["scored"] = scored
                event_repo.mark_seen(job, user_id, "scored")
                event_repo.log_event(
                    user_id,
                    "scored",
                    job.id,
                    f"{round(item['score'])}% - {item['title'][:40]}",
                )
                logger.debug("   stored %3d%% | %s", round(item["score"]), tag)
            else:
                # JD not on Adzuna (real posting lives elsewhere) - keep for
                # manual review instead of a fake score.
                match_repo.set_status(user_id, job.id, "jd_unavailable")
                event_repo.mark_seen(job, user_id, "jd_unavailable")
                logger.debug("   jd-unavailable | %s", tag)

        logger.info(
            "== Run complete user=%s: %d candidates, %d survivors, %d scored",
            user_id,
            len(candidates),
            len(survivors),
            scored,
        )
    finally:
        # Always log the run event - even if scoring was interrupted by an error.
        by_source: dict[str, int] = {}
        for j in jobs:
            by_source[j.source] = by_source.get(j.source, 0) + 1
        run_detail = _json.dumps(
            {
                "fetched": len(jobs),
                "new": len(new_jobs),
                "recent": len(candidates),
                "relevant": len(survivors),
                "scored": scored,
                "adzuna": by_source.get("adzuna", 0),
                "arbeitnow": by_source.get("arbeitnow", 0),
                "bundesagentur": by_source.get("bundesagentur", 0),
                "total_seen": len(seen) + len(new_jobs),
                "manual": manual,
                "stopped": stopped,
            }
        )
        try:
            event_repo.log_event(user_id, "run", "", run_detail)
        except Exception as _log_err:
            logger.warning("Could not log run event: %s", _log_err)
        status.update({"running": False, "phase": "idle", "cancel": False})

    return {
        "checked": len(candidates),
        "scored": scored,
        "results": match_repo.get_all(user_id),
    }
