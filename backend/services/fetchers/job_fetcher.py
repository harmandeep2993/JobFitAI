# fetchers/job_fetcher.py
"""
Adzuna job fetcher.

Fetches job postings from the Adzuna search API and normalizes each
result into a :class:`Job` dataclass. Returns a list of jobs so callers
(the extraction/matching pipeline or the UI) can consume structured data
instead of printed text.

Reuse note:
    A fetched ``Job.description`` is plain text and can be passed straight
    into ``extractors.jd.extract_jd`` (or ``extract_all``) - see
    PROJECT_STATE.md for the full reuse map.
"""

import datetime as dt
import html
import os
import re
import time
from dataclasses import dataclass

import requests
from dotenv import load_dotenv
from langdetect import DetectorFactory, detect
from langdetect.lang_detect_exception import LangDetectException

from core.config import MAX_AGE_DAYS, MAX_PAGES_PER_QUERY
from core.logger import get_logger

load_dotenv()

logger = get_logger(__name__)

# Make language detection deterministic across runs.
DetectorFactory.seed = 0

ADZUNA_APP_ID = os.getenv("ADZUNA_APP_ID")
ADZUNA_APP_KEY = os.getenv("ADZUNA_APP_KEY")

# Country code and page number are path components; Adzuna pages are 1-indexed.
BASE_URL_TEMPLATE = "https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"

# Hard ceiling on results fetched per query to avoid runaway pagination.
MAX_RESULTS_PER_QUERY = 200
# Adzuna API maximum per single page request.
ADZUNA_PAGE_SIZE = 50

_TAG_RE = re.compile(r"<[^>]+>")


@dataclass
class Job:
    """A single normalized job posting."""

    title: str
    company: str
    location: str
    url: str
    description: str
    language: str
    id: str = ""  # stable identifier for dedupe (source-specific)
    source: str = ""  # e.g. "adzuna", "arbeitnow"
    posted_at: str = ""  # publication time (unix epoch as string), if known


def _clean_html(raw: str) -> str:
    """
    Strip HTML tags and unescape entities from a description string.

    Args:
        raw (str): Raw description, may contain tags and HTML entities.

    Returns:
        str: Plain text with collapsed whitespace. Empty string if input is falsy.
    """
    if not raw:
        return ""

    text = _TAG_RE.sub(" ", raw)
    text = html.unescape(text)
    # Collapse all runs of whitespace into single spaces.
    return " ".join(text.split())


def _iso_to_epoch(created: str) -> str:
    """Convert an ISO8601 timestamp (e.g. Adzuna 'created') to epoch seconds as a string."""
    if not created:
        return ""
    try:
        d = dt.datetime.fromisoformat(created.replace("Z", "+00:00"))
        return str(int(d.timestamp()))
    except (ValueError, TypeError):
        return ""


def _detect_language(text: str) -> str:
    """
    Detect the ISO 639-1 language code of a piece of text.

    Args:
        text (str): Text to inspect.

    Returns:
        str: Language code (e.g. "en", "de") or "unknown" if detection fails.
    """
    if not text or not text.strip():
        return "unknown"

    try:
        return detect(text)
    except LangDetectException:
        logger.warning("Language detection failed - defaulting to 'unknown'")
        return "unknown"


def _parse_job(raw: dict) -> Job:
    """
    Convert a raw Adzuna result dict into a :class:`Job`.

    Args:
        raw (dict): A single entry from the Adzuna ``results`` array.

    Returns:
        Job: Normalized job with cleaned description and detected language.
    """
    description = _clean_html(raw.get("description", ""))
    title = (raw.get("title") or "").strip()

    return Job(
        title=title,
        company=(raw.get("company", {}) or {}).get("display_name", "").strip(),
        location=(raw.get("location", {}) or {}).get("display_name", "").strip(),
        url=raw.get("redirect_url", ""),
        description=description,
        # Prefer the (longer) description for detection; fall back to title.
        language=_detect_language(description or title),
        id=str(raw.get("id", "")),
        source="adzuna",
        posted_at=_iso_to_epoch(raw.get("created", "")),
    )


def _fetch_page(query: str, location: str, country: str, page: int) -> tuple[list, int]:
    """
    Fetch a single Adzuna results page.

    Returns (raw_results, total_count). On failure returns ([], 0).
    """
    url = BASE_URL_TEMPLATE.format(country=country, page=page)
    params = {
        "app_id": ADZUNA_APP_ID,
        "app_key": ADZUNA_APP_KEY,
        "what": query,
        "where": location,
        "results_per_page": ADZUNA_PAGE_SIZE,
        # Newest first, capped at our recency window. Without these, Adzuna
        # returns relevance order with no age limit - so a fresh posting ranked
        # below the page window was never seen, while stale jobs we would
        # discard anyway wasted slots in it. With them, repeated runs walk the
        # newest jobs and the seen_jobs dedup makes each run cost only the
        # genuinely new ones.
        "sort_by": "date",
        "max_days_old": MAX_AGE_DAYS,
        "content-type": "application/json",
    }

    response = None
    for attempt in range(3):
        try:
            response = requests.get(url, params=params, timeout=15)
            if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                logger.warning(
                    "Adzuna %s for '%s' page %d - retry %d",
                    response.status_code,
                    query,
                    page,
                    attempt + 1,
                )
                time.sleep(1.5 * (attempt + 1))
                continue
            response.raise_for_status()
            break
        except requests.RequestException as e:
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            logger.error("Adzuna request failed (page %d): %s", page, e)
            return [], 0

    if response is None:
        return [], 0

    try:
        data = response.json()
    except ValueError as e:
        logger.error("Adzuna JSON parse failed (page %d): %s", page, e)
        return [], 0

    return data.get("results", []), data.get("count", 0)


def fetch_adzuna_jobs(
    query: str = "machine learning engineer",
    location: str = "berlin",
    results: int = 50,
    country: str = "de",
    seen_ids: set[str] | None = None,
) -> list[Job]:
    """
    Fetch job postings from the Adzuna API, paginating automatically.

    Two modes, decided by ``seen_ids``:

    Budget mode (seen_ids=None): fetch up to ``results`` jobs and stop - the
    original behaviour.

    Seen-stop mode (seen_ids given): walk the date-sorted pool page by page
    until a page contains ONLY already-seen ids. Because results are sorted
    newest-first, everything past a fully-seen page was covered by an earlier
    run, so no job in the window is ever skipped: the first run walks the whole
    pool (bounded by MAX_PAGES_PER_QUERY) and each later run pays only for
    pages containing something new.

    Args:
        query (str):    Search terms (Adzuna ``what`` parameter).
        location (str): Location filter (Adzuna ``where`` parameter).
        results (int):  Budget-mode cap (ignored in seen-stop mode).
        country (str):  Adzuna country code used in the request path.
        seen_ids (set | None): Job ids this user has already processed.

    Returns:
        list[Job]: Normalized jobs. Empty list if credentials are missing
                   or the request fails.
    """
    if not ADZUNA_APP_ID or not ADZUNA_APP_KEY:
        logger.error("Missing ADZUNA_APP_ID / ADZUNA_APP_KEY - cannot fetch jobs")
        return []

    exhaustive = seen_ids is not None
    want = (
        MAX_PAGES_PER_QUERY * ADZUNA_PAGE_SIZE
        if exhaustive
        else min(results, MAX_RESULTS_PER_QUERY)
    )
    all_raw: list[dict] = []
    page = 1
    total = 0

    while len(all_raw) < want and page <= MAX_PAGES_PER_QUERY:
        raw_jobs, total = _fetch_page(query, location, country, page)

        if not raw_jobs:
            break

        all_raw.extend(raw_jobs)
        logger.info(
            "Adzuna page %d: got %d jobs (total available: %d, fetched so far: %d)",
            page,
            len(raw_jobs),
            total,
            len(all_raw),
        )

        if exhaustive:
            # Newest-first ordering: a page with nothing new means every older
            # page was already covered by a previous run.
            unseen = sum(1 for j in raw_jobs if str(j.get("id", "")) not in seen_ids)
            if unseen == 0:
                logger.info(
                    "Adzuna '%s': page %d fully seen - stopping (no gaps below)",
                    query,
                    page,
                )
                break

        # Stop when the pool is exhausted (or budget-mode has enough).
        if len(all_raw) >= total or (not exhaustive and len(all_raw) >= want):
            break

        page += 1
        time.sleep(0.4)  # gentle inter-page throttle

    jobs = [_parse_job(j) for j in all_raw[:want]]
    logger.info(
        "Adzuna '%s': returning %d jobs (total available: %d)",
        query,
        len(jobs),
        total if all_raw else 0,
    )
    return jobs


def fetch_adzuna_multi(
    titles: list[str],
    location: str = "",
    country: str = "de",
    per_title: int = MAX_RESULTS_PER_QUERY,
    seen_ids: set[str] | None = None,
) -> list[Job]:
    """
    Run one Adzuna search per target title and merge the results.

    Adzuna supports server-side search, so this finds far more AI/ML roles
    than the Arbeitnow feed. Results are deduplicated by id (falling back
    to url).

    Args:
        titles (list[str]): Role phrases to search for, one query each.
        location (str):     Location filter.
        country (str):      Adzuna country code.
        per_title (int):    Results requested per title (budget mode only).
        seen_ids (set | None): Already-processed ids; enables seen-stop mode,
            which walks each query's date-sorted pool until nothing is new.

    Returns:
        list[Job]: Unique jobs across all title searches.
    """
    seen: set[str] = set()
    out: list[Job] = []

    for i, title in enumerate(titles):
        if i:
            time.sleep(0.3)  # gentle throttle to avoid Adzuna rate-limit (503)
        for job in fetch_adzuna_jobs(
            query=title,
            location=location,
            results=per_title,
            country=country,
            seen_ids=seen_ids,
        ):
            key = job.id or job.url
            if key and key not in seen:
                seen.add(key)
                out.append(job)

    logger.info(
        "Adzuna multi-title: %d unique jobs across %d titles", len(out), len(titles)
    )
    return out


if __name__ == "__main__":
    # Manual smoke test - prints a compact view of fetched jobs.
    for job in fetch_adzuna_jobs(
        query="python developer", location="berlin", results=3
    ):
        print(f"[{job.language}] {job.title} - {job.company} ({job.location})")
        print(f"  {job.url}")
        print("-" * 60)
