#!/usr/bin/env python3
"""
Shared pipeline functions for the BU AI Bibliography auto-update system.

Used by update_monthly.py and quarterly_review.py.
Imports from existing pipeline code, never modifies those files.
"""

import csv
import hashlib
import json
import logging
import os
import re
import signal
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

# ── Imports from existing pipeline ──────────────────────────────────────────
from config import (
    OPENALEX_API_KEY,
    openalex_headers,
    ALL_AI_KEYWORDS,
    AI_KEYWORDS_PRIMARY,
    BU_ROR_ID,
    CONTACT_EMAIL,
    CROSSREF_RATE_LIMIT,
    OPENALEX_RATE_LIMIT,
    PUBMED_MESH_TERMS,
)
from utils import (
    Deduplicator,
    HarvestBudgetExceeded,
    HarvestTimeout,
    RateLimiter,
    make_paper_record,
    normalize_doi,
    resilient_get,
    resilient_post,
    title_fingerprint,
)
from classify_papers import (
    MODEL,
    SYSTEM_PROMPT,
    _system_block,
    derived_fields,
    paper_to_prompt_text,
    validate_classification,
)
from school_mapper import (
    FACULTY_BY_FULLNAME,
    FACULTY_BY_OAID,
    _name_key,
    classify_all,
    classify_author_by_openalex_id,
    classify_paper,
)
from source_openalex import _headers as openalex_headers
from source_openalex import _parse_work, _reconstruct_abstract
from source_ssrn import _parse_crossref_item, _search_crossref_for_ssrn
from generate_data_js import compute_master_hash, generate_all, validate_data_js

logger = logging.getLogger("update_pipeline")

# ── Paths ───────────────────────────────────────────────────────────────────
MASTER_PATH = "data/sonnet_classification_bu_verified.json"
STATE_PATH = "data/update_state.json"
LOG_PATH = "data/update_log.csv"
LOCK_PATH = "data/.update_lock"
BU_AUTHORS_PATH = "data/bu_authors_from_openalex.json"
BU_ROSTER_PATH = "data/bu_faculty_roster.json"
REJECTED_PATH = "data/rejected_papers_index.json"
NON_BU_AI_PATH = "data/non_bu_ai_index.json"
PREFILTER_SEEN_PATH = "data/prefilter_seen_index.json"
BU_AUTHOR_REGISTRY_PATH = "data/bu_author_registry.json"

# ── Cost constants (Sonnet standard API pricing) ────────────────────────────
COST_PER_INPUT_MTOK = 3.0    # $/MTok
COST_PER_OUTPUT_MTOK = 15.0  # $/MTok
# Measured against what the code actually sends and what the model actually
# returns, rather than guessed. The system prompt alone is ~1,230 tokens, so the
# old AVG_INPUT_TOKENS = 800 was below the system prompt by itself and the
# estimate ran ~25% low -- which matters because update_monthly gates on
# est_cost > 15.0 and then classify_via_sonnet silently stops mid-list at the
# real $15.
# Output: mean 181, p95 208, max 271 over the 10,595 master records with usage
# data. Input assumes the cached system block (see _system_block); on a cache
# miss the first request of a run pays full price for it.
AVG_INPUT_TOKENS = 1450
AVG_OUTPUT_TOKENS = 185
AVG_COST_PER_PAPER = (AVG_INPUT_TOKENS * COST_PER_INPUT_MTOK + AVG_OUTPUT_TOKENS * COST_PER_OUTPUT_MTOK) / 1_000_000

# Rate limiters
_openalex_rl = RateLimiter(OPENALEX_RATE_LIMIT)
_crossref_rl = RateLimiter(CROSSREF_RATE_LIMIT)


# ═══════════════════════════════════════════════════════════════════════════
# LOCKING
# ═══════════════════════════════════════════════════════════════════════════

def acquire_lock() -> bool:
    """Acquire a lock file. Returns False if another run is active (<2h old)."""
    if os.path.exists(LOCK_PATH):
        try:
            age = time.time() - os.path.getmtime(LOCK_PATH)
            if age < 7200:  # 2 hours
                logger.warning(f"Lock file exists and is {age/60:.0f}m old. Another run active?")
                return False
            logger.warning("Stale lock file (>2h), overriding")
        except OSError:
            pass
    Path(LOCK_PATH).touch()
    return True


def release_lock():
    """Release the lock file."""
    try:
        os.remove(LOCK_PATH)
    except FileNotFoundError:
        pass


# ═══════════════════════════════════════════════════════════════════════════
# STATE MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════

def load_state() -> dict:
    """Load persistent state. Returns defaults if file doesn't exist."""
    defaults = {
        "last_weekly_run": "2026-03-01T00:00:00",
        "last_monthly_run": "2026-03-01T00:00:00",
        "master_paper_count": 10329,
        "consecutive_zero_weeks": 0,
        "total_api_cost_usd": 72.88,
        "source_health": {},
        "domain_snapshot": {},
        "last_quarterly_review": "2026-03-28",
    }
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            state = json.load(f)
        for k, v in defaults.items():
            state.setdefault(k, v)
        return state
    return defaults


def save_state(state: dict):
    """Save state to disk."""
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def append_log(entry: dict):
    """Append a row to the update log CSV."""
    fields = [
        "timestamp", "type", "harvested", "deduped", "filtered",
        "classified", "added", "final_count", "cost_usd", "duration_s", "status",
    ]
    write_header = not os.path.exists(LOG_PATH)
    with open(LOG_PATH, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerow(entry)


# ═══════════════════════════════════════════════════════════════════════════
# MASTER DATASET I/O
# ═══════════════════════════════════════════════════════════════════════════

def load_master() -> list[dict]:
    """Load the master dataset."""
    with open(MASTER_PATH) as f:
        return json.load(f)


def save_master(papers: list[dict]):
    """Save the master dataset with reindexing. Uses atomic write to prevent corruption."""
    for i, p in enumerate(papers):
        p["index"] = i
    tmp_path = str(MASTER_PATH) + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(papers, f, ensure_ascii=False)
    os.rename(tmp_path, MASTER_PATH)


def build_dedup_index(master: list[dict]) -> tuple[set, set]:
    """Build DOI and title fingerprint sets for fast dedup lookup."""
    dois = set()
    fps = set()
    for p in master:
        doi = normalize_doi(p.get("doi", ""))
        if doi:
            dois.add(doi)
        fp = title_fingerprint(p.get("title", ""))
        if fp:
            fps.add(fp)
    return dois, fps


# ═══════════════════════════════════════════════════════════════════════════
# FACULTY ROSTER REFRESH
# ═══════════════════════════════════════════════════════════════════════════

def refresh_faculty_roster() -> dict:
    """Rebuild faculty roster: scrape -> merge -> resolve OAIDs -> enrich unspecified.
    Returns report dict with keys: added, warnings, oaids_resolved, enriched, error.
    """
    import shutil
    from school_mapper import reload_roster

    report = {"added": 0, "warnings": [], "oaids_resolved": 0, "enriched": 0, "error": None}
    roster_path = "data/bu_faculty_roster_verified.json"

    # Backup
    backup_path = f"data/bu_faculty_roster_verified.backup_{date.today().strftime('%Y%m%d')}.json"
    if os.path.exists(roster_path):
        shutil.copy2(roster_path, backup_path)
        logger.info(f"Roster backed up to {backup_path}")

    # Load existing roster
    existing_roster = []
    if os.path.exists(roster_path):
        with open(roster_path) as f:
            existing_roster = json.load(f)
    old_count = len(existing_roster)

    # Phase 1: Scrape departments
    try:
        from build_faculty_roster import scrape_all_departments, merge_with_existing
        scraped, school_counts = scrape_all_departments()
        logger.info(f"Scraped {len(scraped)} faculty from department pages")
    except Exception as e:
        report["error"] = f"Scrape failed: {e}"
        report["warnings"].append(f"Faculty scrape failed ({e}), using existing roster")
        logger.error(f"Faculty scrape failed: {e}")
        return report

    # Phase 2: Safe merge with regression protection
    try:
        merged, merge_warnings = merge_with_existing(scraped, existing_roster, school_counts)
        report["warnings"].extend(merge_warnings)
        report["added"] = max(0, len(merged) - old_count)
        logger.info(f"Merged roster: {old_count} -> {len(merged)} entries")
    except Exception as e:
        report["error"] = f"Merge failed: {e}"
        logger.error(f"Roster merge failed: {e}")
        return report

    # Phase 3: Resolve OpenAlex IDs for new entries (those without OAIDs)
    try:
        from resolve_openalex_ids import resolve_batch
        merged, resolved_count = resolve_batch(merged)
        report["oaids_resolved"] = resolved_count
        logger.info(f"Resolved {resolved_count} new OpenAlex IDs")
    except Exception as e:
        report["warnings"].append(f"OAID resolution failed: {e}")
        logger.error(f"OAID resolution failed: {e}")

    # Phase 4: Enrich unspecified entries
    try:
        from enrich_unspecified_roster import enrich_unspecified
        merged, enriched_count = enrich_unspecified(merged)
        report["enriched"] = enriched_count
        logger.info(f"Enriched {enriched_count} unspecified entries")
    except Exception as e:
        report["warnings"].append(f"Enrichment failed: {e}")
        logger.error(f"Enrichment failed: {e}")

    # Save updated roster
    with open(roster_path, "w") as f:
        json.dump(merged, f, indent=2, ensure_ascii=False)
    logger.info(f"Saved updated roster: {len(merged)} entries")

    # Reload school_mapper indexes
    reload_roster()
    logger.info("Reloaded school_mapper indexes")

    return report


# ═══════════════════════════════════════════════════════════════════════════
# INCREMENTAL HARVESTING
# ═══════════════════════════════════════════════════════════════════════════

def harvest_openalex_incremental(date_field: str, since_date: str,
                                 deadline: float | None = None,
                                 _partial: list | None = None,
                                 _registry: dict | None = None) -> list[dict]:
    """Harvest BU papers from OpenAlex with a date filter.

    OpenAlex is the primary source -- roughly two thirds of the corpus -- so a
    truncated OpenAlex harvest is the single largest recall hole in the
    pipeline, and it used to fail silently.

    On the 2026-08-18 full sweep this returned 21,200 papers and stopped,
    because one request came back 504 Gateway Timeout and `except HTTPError:
    break` treated a transient server hiccup as the end of the result set. The
    run then logged "OpenAlex: 21200 papers harvested" and _run_source recorded
    status "ok" -- against a BU work count in the low hundreds of thousands.
    Nothing anywhere said the harvest had been cut off.

    Two changes:
      * 5xx responses are retried against the SAME cursor with backoff. Cursors
        are stable, so re-requesting is correct and loses nothing.
      * meta.count is captured on the first page and compared to what actually
        arrived, so an incomplete harvest is reported as TRUNCATED instead of
        passing as a clean run.

    Args:
        date_field: "from_created_date" or "from_publication_date"
        since_date: ISO date string like "2026-03-01"
        deadline: unix timestamp; raises HarvestBudgetExceeded when passed
        _partial: shared list so _run_source can salvage results on timeout
    """
    logger.info(f"OpenAlex: {date_field}={since_date}")
    papers = []
    cursor = "*"
    base_url = "https://api.openalex.org/works"

    expected = None
    pages = 0
    retries = 0
    max_retries = 8
    truncated_reason = None

    while cursor:
        if deadline is not None and time.time() >= deadline:
            truncated_reason = "time budget"
            # papers are already in _partial, streamed per record above
            raise HarvestBudgetExceeded(
                f"OpenAlex exceeded its time budget after {len(papers)} papers"
            )

        _openalex_rl.wait()
        params = {
            "filter": f"authorships.institutions.ror:{BU_ROR_ID},{date_field}:{since_date}",
            "per_page": 200,
            "cursor": cursor,
        }
        headers = openalex_headers()
        try:
            resp = requests.get(base_url, params=params, headers=headers, timeout=60)

            # A 429 whose body says "Insufficient budget" is NOT transient. The
            # daily allowance resets at midnight UTC and no amount of backing
            # off inside one run recovers it. Retrying it burns the run's clock
            # and then reports a generic rate-limit failure, which is how this
            # cost us a whole source before anyone noticed the actual cause.
            if resp.status_code == 429 and "Insufficient budget" in resp.text:
                truncated_reason = "OpenAlex daily budget exhausted"
                logger.error(
                    "OpenAlex budget exhausted. Filtered queries cost $0.10/1,000 "
                    "against a daily allowance that resets at midnight UTC; keyless "
                    "gets $0.10/day, a FREE key gets $1/day. Set OPENALEX_API_KEY "
                    "(30 seconds at https://openalex.org/settings/api) — "
                    + ("a key IS configured, so today's $1 is genuinely spent."
                       if OPENALEX_API_KEY else
                       "no key is configured, so this run had only $0.10 to spend.")
                )
                break

            # 429 and 5xx are both transient: back off and retry the SAME cursor.
            if resp.status_code == 429 or resp.status_code >= 500:
                retries += 1
                if retries > max_retries:
                    truncated_reason = f"{max_retries} consecutive {resp.status_code}s"
                    logger.error(f"OpenAlex: giving up after {truncated_reason}")
                    break
                wait = min(5 * (2 ** (retries - 1)), 120)
                logger.warning(
                    f"OpenAlex {resp.status_code}, retrying same cursor in {wait}s "
                    f"(attempt {retries}/{max_retries}, {len(papers)} papers so far)"
                )
                time.sleep(wait)
                continue

            resp.raise_for_status()
            retries = 0
            data = resp.json()

        except HarvestBudgetExceeded:
            raise
        except requests.exceptions.HTTPError as e:
            # A genuine 4xx (bad filter, auth) will not fix itself.
            truncated_reason = f"HTTP error: {e}"
            logger.error(f"OpenAlex fatal HTTP error, stopping: {e}")
            break
        except Exception as e:
            # Network blip, JSON decode, read timeout: also worth retrying.
            retries += 1
            if retries > max_retries:
                truncated_reason = f"{max_retries} consecutive errors ({e})"
                logger.error(f"OpenAlex: giving up after {truncated_reason}")
                break
            wait = min(5 * (2 ** (retries - 1)), 120)
            logger.warning(
                f"OpenAlex request failed ({type(e).__name__}: {e}), "
                f"retrying same cursor in {wait}s (attempt {retries}/{max_retries})"
            )
            time.sleep(wait)
            continue

        if expected is None:
            expected = data.get("meta", {}).get("count")
            if expected:
                logger.info(f"OpenAlex reports {expected:,} matching works")

        results = data.get("results", [])
        if not results:
            break

        page_papers = []
        for work in results:
            try:
                paper = _parse_work(work)
                if paper:
                    papers.append(paper)
                    page_papers.append(paper)
                    # Stream into the shared partial list per record, not at the
                    # loop boundary. The SIGALRM hard cutoff can land anywhere,
                    # including mid-page, and anything not already in _partial at
                    # that instant is lost. Run #13 lost the entire OpenAlex
                    # harvest this way.
                    if _partial is not None:
                        _partial.append(paper)
            except Exception as e:
                logger.debug(f"Parse error: {e}")

        # Fold this page's authorships into the BU author registry before the AI
        # filter ever sees them. A BU chemist's 2004 paper is not going into the
        # bibliography, but it is still evidence that they were at BU in 2004,
        # and that evidence is what lets us judge a name-matched DBLP record.
        if _registry is not None:
            try:
                fold_papers_into_bu_registry(page_papers, _registry)
            except Exception as e:
                logger.debug(f"registry fold failed: {e}")

        pages += 1
        if pages % 25 == 0:
            logger.info(f"  OpenAlex page {pages}: {len(papers):,} papers so far")

        cursor = data.get("meta", {}).get("next_cursor")
        if not cursor:
            break

    # Completeness check. Parsing legitimately drops a few records, so allow a
    # small margin before calling it truncated.
    if expected and len(papers) < expected * 0.90:
        logger.error(
            f"OpenAlex TRUNCATED: harvested {len(papers):,} of {expected:,} "
            f"reported works ({100*len(papers)/expected:.0f}%)"
            + (f" - {truncated_reason}" if truncated_reason else "")
        )
    elif truncated_reason:
        logger.warning(f"OpenAlex ended early ({truncated_reason}) with {len(papers):,} papers")
    else:
        logger.info(f"OpenAlex: {len(papers):,} papers harvested"
                    + (f" of {expected:,} reported" if expected else ""))

    return papers


def harvest_pubmed_incremental(since_date: str) -> list[dict]:
    """Harvest BU papers from PubMed with a date filter."""
    from source_pubmed import _fetch_details, _search_pmids

    logger.info(f"PubMed: since {since_date}")
    # Convert ISO date to PubMed format YYYY/MM/DD
    d = datetime.fromisoformat(since_date)
    mindate = d.strftime("%Y/%m/%d")
    maxdate = date.today().strftime("%Y/%m/%d")

    # Build AI query with BU affiliation
    ai_terms = " OR ".join(
        [f'"{t}"[MeSH Terms]' for t in PUBMED_MESH_TERMS]
        + [f'"{kw}"[Title/Abstract]' for kw in AI_KEYWORDS_PRIMARY]
    )
    # See source_pubmed for why: BU's medical campus publishes under Boston
    # Medical Center, Chobanian & Avedisian, and VA Boston. Measured +9% on AI
    # papers since 2024.
    query = f'( "Boston University"[Affiliation] OR "Boston Medical Center"[Affiliation] OR "Chobanian"[Affiliation] OR "VA Boston"[Affiliation] OR "Boston VA"[Affiliation] ) AND ({ai_terms})'

    try:
        # Override _search_pmids to add date filter
        from config import PUBMED_RATE_LIMIT
        esearch_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
        rl = RateLimiter(PUBMED_RATE_LIMIT)
        rl.wait()
        resp = requests.get(esearch_url, params={
            "db": "pubmed", "term": query, "retmax": 0, "retmode": "json",
            "mindate": mindate, "maxdate": maxdate, "datetype": "edat",
        }, timeout=15)
        resp.raise_for_status()
        total = resp.json().get("esearchresult", {}).get("count", "0")
        total = int(total)
        logger.info(f"PubMed found {total} results (date-filtered)")

        if total == 0:
            return []

        # Fetch PMIDs with date filter
        pmids = []
        if total > 5000:
            logger.warning(f"PubMed returned {total} results -- unusually high, verify query")
        for offset in range(0, total, 500):
            rl.wait()
            r = requests.get(esearch_url, params={
                "db": "pubmed", "term": query, "retmax": 500, "retstart": offset,
                "retmode": "json", "mindate": mindate, "maxdate": maxdate, "datetype": "edat",
            }, timeout=15)
            r.raise_for_status()
            ids = r.json().get("esearchresult", {}).get("idlist", [])
            pmids.extend(ids)

        papers = _fetch_details(pmids)
        logger.info(f"PubMed: {len(papers)} papers harvested")
        return papers
    except Exception as e:
        logger.error(f"PubMed error: {e}")
        return []


def harvest_crossref_biorxiv_incremental(since_date: str) -> list[dict]:
    """Harvest bioRxiv/medRxiv papers via CrossRef with date filter."""
    logger.info(f"bioRxiv/medRxiv: since {since_date}")
    papers = []
    queries = [
        f'"Boston University" {kw}' for kw in ["artificial intelligence", "machine learning",
        "deep learning", "neural network", "natural language processing"]
    ]

    for query in queries:
        _crossref_rl.wait()
        try:
            url = "https://api.crossref.org/works"
            params = {
                "query": query,
                "filter": f"prefix:10.1101,from-created-date:{since_date}",
                "rows": 100,
                "mailto": CONTACT_EMAIL,
            }
            resp = requests.get(url, params=params, timeout=30)
            if resp.status_code == 429:
                time.sleep(10)
                continue
            resp.raise_for_status()
            items = resp.json().get("message", {}).get("items", [])
            for item in items:
                parsed = _parse_crossref_item(item)
                if parsed:
                    parsed["source"] = "biorxiv_medrxiv"
                    papers.append(parsed)
        except Exception as e:
            logger.error(f"CrossRef/bioRxiv error: {e}")

    # Dedup by DOI within this batch
    seen = set()
    unique = []
    for p in papers:
        doi = normalize_doi(p.get("doi", ""))
        if doi and doi not in seen:
            seen.add(doi)
            unique.append(p)
    logger.info(f"bioRxiv/medRxiv: {len(unique)} papers harvested")
    return unique


def harvest_ssrn_by_faculty() -> list[dict]:
    """Search SSRN for papers by known BU Law faculty ONLY.

    CRITICAL: Never do broad keyword searches on SSRN — that returned 16K
    worldwide junk in the initial harvest. Search by faculty name only.
    Uses direct CrossRef API calls (no pagination) — max 25 results per name.
    """
    logger.info("SSRN: searching by faculty names")
    papers = []
    seen_dois = set()

    law_last_names = list({
        name_key.split()[0].title()
        for name_key, entries in FACULTY_BY_FULLNAME.items()
        if any(school == "School of Law" for school, _ in entries)
    })

    for name in law_last_names:
        _crossref_rl.wait()
        try:
            resp = requests.get(
                "https://api.crossref.org/works",
                params={
                    "query": f'"{name}" "Boston University"',
                    "filter": "prefix:10.2139",
                    "rows": 100,
                    "select": "DOI,title,author,published-print,published-online,"
                              "abstract,URL,is-referenced-by-count,type,subject",
                },
                headers=openalex_headers(),
                timeout=15,
            )
            if resp.status_code == 429:
                time.sleep(5)
                continue
            resp.raise_for_status()
            items = resp.json().get("message", {}).get("items", [])

            for item in items:
                parsed = _parse_crossref_item(item)
                if not parsed:
                    continue
                doi = normalize_doi(parsed.get("doi", ""))
                if doi and doi not in seen_dois:
                    seen_dois.add(doi)
                    # Verify author name actually appears
                    authors_str = ", ".join(
                        a.get("name", "") for a in parsed.get("authors", [])
                    ).lower()
                    if name.lower() in authors_str:
                        parsed["source"] = "ssrn"
                        papers.append(parsed)
        except Exception as e:
            logger.debug(f"SSRN search error for {name}: {e}")

    logger.info(f"SSRN: {len(papers)} papers harvested")
    return papers


# ═══════════════════════════════════════════════════════════════════════════
# DBLP DUMP DOWNLOAD
# ═══════════════════════════════════════════════════════════════════════════

DBLP_DUMP_PATH = Path("data/dblp-latest.xml.gz")
DBLP_DTD_PATH = Path("data/dblp.dtd")
DBLP_URLS = [
    "https://dblp.org/xml/dblp.xml.gz",
    "https://dblp.dagstuhl.de/xml/dblp.xml.gz",
]
DBLP_DTD_URLS = [
    "https://dblp.org/xml/dblp.dtd",
    "https://dblp.dagstuhl.de/xml/dblp.dtd",
]


def download_dblp_dump(dest: Path = DBLP_DUMP_PATH) -> Path | None:
    """Download DBLP XML dump. Skips if recent file (<35 days) exists.
    Returns path on success, None on failure.
    """
    # Check for recent dump
    if dest.exists():
        age_days = (time.time() - dest.stat().st_mtime) / 86400
        if age_days < 35:
            logger.info(f"DBLP dump exists and is {age_days:.0f} days old, skipping download")
            return dest
        logger.info(f"DBLP dump is {age_days:.0f} days old, re-downloading")

    # Download DTD first (needed for entity resolution)
    dtd_dest = dest.parent / "dblp.dtd"
    if not dtd_dest.exists():
        for dtd_url in DBLP_DTD_URLS:
            try:
                logger.info(f"Downloading DBLP DTD from {dtd_url}...")
                resp = requests.get(dtd_url, timeout=30)
                resp.raise_for_status()
                dtd_dest.write_bytes(resp.content)
                logger.info(f"DTD saved to {dtd_dest}")
                break
            except Exception as e:
                logger.warning(f"DTD download failed from {dtd_url}: {e}")

    # Download dump
    for url in DBLP_URLS:
        try:
            logger.info(f"Downloading DBLP dump from {url} (~1.3 GB)...")
            resp = requests.get(url, stream=True, timeout=600)
            resp.raise_for_status()

            tmp = str(dest) + ".tmp"
            downloaded = 0
            with open(tmp, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192 * 128):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if downloaded % (100 * 1024 * 1024) == 0:
                        logger.info(f"  Downloaded {downloaded / (1024*1024):.0f} MB...")

            os.rename(tmp, str(dest))
            logger.info(f"DBLP dump saved to {dest} ({downloaded / (1024*1024):.0f} MB)")
            return dest

        except Exception as e:
            logger.warning(f"DBLP download failed from {url}: {e}")
            # Clean up partial download
            tmp = str(dest) + ".tmp"
            if os.path.exists(tmp):
                os.remove(tmp)

    logger.error("All DBLP download URLs failed")
    return None


# ═══════════════════════════════════════════════════════════════════════════
# UNIFIED HARVEST
# ═══════════════════════════════════════════════════════════════════════════

# Total wall-clock the harvest phase may consume, in minutes. Per-source budgets
# are clamped to whatever is left of this, so the sum of the individual budgets
# can never overrun the job.
#
# Raised from 75 to 180 alongside the workflow's timeout-minutes going 120 -> 300.
# At 75 minutes the sources were competing for a scarce resource that costs
# nothing: this is a public repository, so GitHub Actions minutes are free, and
# a full sweep genuinely has hundreds of thousands of records to page through.
# Run #13 showed what the scarcity cost -- OpenAlex was cut off at 20 minutes
# and NSF at 5, and because neither streamed partial results at the time, both
# returned ZERO papers. Starving the harvest to protect a deadline we can simply
# move is the wrong trade when acquisition is the hard part.
HARVEST_GLOBAL_BUDGET_MIN = 180


def harvest_all_sources(since_12m: str, since_3m: str,
                        global_budget_min: float = HARVEST_GLOBAL_BUDGET_MIN
                        ) -> tuple[list[dict], dict]:
    """Run all source harvesters with fault isolation, time budgets, and partial result capture.

    Returns (all_papers, source_report) where source_report maps source_name to
    {"count": int, "status": str, "error": str|None, "duration_s": float}
    """
    all_papers = []
    source_report = {}

    # Shared collector: harvesters append here so partial results survive exceptions
    _partial_results: list[dict] = []

    _global_deadline = time.time() + global_budget_min * 60
    logger.info(f"Harvest global budget: {global_budget_min:.0f} min")

    # The BU author registry: who published from BU, and when. Grown every run
    # from the authorship data already flowing through the OpenAlex harvest.
    _bu_registry = load_bu_author_registry()
    _registry_at_start = len(_bu_registry)
    logger.info(f"BU author registry loaded: {_registry_at_start:,} authors")

    def _run_source(name: str, harvester, critical: bool = False, max_minutes: float = 15):
        """Run a harvester with fault isolation, time budget, and partial result capture.

        The budget is enforced two ways. Harvesters that accept `deadline` check
        it cooperatively and raise HarvestBudgetExceeded, which preserves their
        partial results. For the rest -- the majority, which take the deadline
        kwarg only to discard it -- SIGALRM delivers a hard cutoff. Without the
        alarm those budgets were purely decorative: harvest_crossref_per_faculty
        ran 74 minutes against a nominal 15, and OpenBU 74 against 10, which is
        what pushed every monthly run past the CI timeout.
        """
        nonlocal _partial_results
        _partial_results = []
        t0 = time.time()

        remaining = _global_deadline - t0
        if remaining <= 30:
            source_report[name] = {
                "count": 0,
                "status": "SKIPPED_NO_TIME",
                "error": f"global harvest budget ({global_budget_min:.0f}m) exhausted before this source ran",
                "duration_s": 0.0,
            }
            logger.warning(f"  {name}: SKIPPED - global harvest budget exhausted")
            return

        budget_s = min(max_minutes * 60, remaining)
        deadline = t0 + budget_s

        def _on_alarm(signum, frame):
            raise HarvestTimeout(f"{name} hit its {budget_s/60:.1f}m hard cutoff")

        _prev_handler = None
        _alarm_set = False
        try:
            _prev_handler = signal.signal(signal.SIGALRM, _on_alarm)
            signal.alarm(int(budget_s))
            _alarm_set = True
        except (ValueError, AttributeError):
            # Not the main thread, or no SIGALRM on this platform. Fall back to
            # cooperative-only enforcement.
            logger.debug(f"  {name}: SIGALRM unavailable, cooperative budget only")

        try:
            papers = harvester(deadline=deadline)
            all_papers.extend(papers)
            source_report[name] = {
                "count": len(papers),
                # A source that returns zero papers used to be recorded as "ok",
                # which is how arXiv went its entire life returning HTTP 500 and
                # never once raising an alert. Zero is now its own status.
                "status": "ok" if papers else "EMPTY",
                "error": None if papers else "returned 0 papers",
                "duration_s": round(time.time() - t0, 1),
            }
            logger.info(f"  {name}: {len(papers)} papers ({time.time()-t0:.0f}s)")

        except HarvestTimeout as e:
            partial = _partial_results
            if partial:
                all_papers.extend(partial)
            source_report[name] = {
                "count": len(partial),
                "status": "PARTIAL_TIMEOUT",
                "error": f"hard cutoff after {budget_s/60:.1f}m, kept {len(partial)} papers",
                "duration_s": round(time.time() - t0, 1),
            }
            logger.warning(f"  {name}: HARD TIMEOUT after {budget_s/60:.1f}m ({e})")

        except HarvestBudgetExceeded:
            # Time budget hit: use whatever was collected
            partial = _partial_results
            if partial:
                all_papers.extend(partial)
            source_report[name] = {
                "count": len(partial),
                "status": "PARTIAL_TIMEOUT",
                "error": f"Time budget ({max_minutes}m) exceeded, kept {len(partial)} papers",
                "duration_s": round(time.time() - t0, 1),
            }
            logger.warning(f"  {name}: TIMEOUT after {max_minutes}m, kept {len(partial)} partial results")

        except Exception as e:
            # Crash: try to salvage partial results
            partial = _partial_results
            if partial:
                all_papers.extend(partial)
                source_report[name] = {
                    "count": len(partial),
                    "status": "PARTIAL_ERROR",
                    "error": str(e)[:200],
                    "duration_s": round(time.time() - t0, 1),
                }
                logger.warning(f"  {name}: PARTIAL ({len(partial)} papers salvaged) - {e}")
            else:
                source_report[name] = {
                    "count": 0,
                    "status": "FAILED",
                    "error": str(e)[:200],
                    "duration_s": round(time.time() - t0, 1),
                }
                level = "CRITICAL" if critical else "WARNING"
                logger.error(f"  {name}: FAILED ({level}) - {e}")

        finally:
            if _alarm_set:
                signal.alarm(0)
                if _prev_handler is not None:
                    signal.signal(signal.SIGALRM, _prev_handler)

    # Helper: wraps a legacy harvester (no deadline param) to work with _run_source
    def _legacy_wrap(harvest_fn, *args, **kwargs):
        """Wrap a harvest function that doesn't accept deadline."""
        def wrapper(deadline=None):
            return harvest_fn(*args, **kwargs)
        return wrapper

    # ── Core sources (always run, have incremental versions) ──
    _run_source("openalex",
                lambda deadline=None: harvest_openalex_incremental(
                    "from_publication_date", since_12m,
                    deadline=deadline, _partial=_partial_results,
                    _registry=_bu_registry),
                critical=True, max_minutes=30)

    # Ask OpenAlex directly for what it already knows is AI, rather than paging
    # all 200,554 BU works and filtering. 18,475 works in 93 requests, under a
    # cent. Deliberately NOT date-windowed: measured against master, 12,233 of
    # them had never been evaluated and 4,896 of those predate 2010, which is
    # exactly the depth the other harvesters' date windows cut off.
    from source_openalex_concepts import harvest as harvest_oa_concepts
    _run_source("openalex_concepts",
                lambda deadline=None: harvest_oa_concepts(
                    deadline=deadline, _partial=_partial_results,
                    _registry=_bu_registry),
                max_minutes=15)

    _run_source("pubmed",
                lambda deadline=None: harvest_pubmed_incremental(since_3m),
                critical=True, max_minutes=10)

    _run_source("biorxiv",
                lambda deadline=None: harvest_crossref_biorxiv_incremental(since_3m),
                max_minutes=5)

    _run_source("ssrn",
                lambda deadline=None: harvest_ssrn_by_faculty(),
                max_minutes=10)

    # Per-faculty CrossRef back-fill for high-impact venues. Catches faculty
    # whose OpenAlex profile is split, or whose recent JAMA/NEJM/Lancet papers
    # the OpenAlex BU-ROR filter hasn't picked up yet (the missing-Robertson-
    # JAMA-papers scenario).

    # ── Sources with since_date support ──
    from source_semantic_scholar import harvest as harvest_s2
    _run_source("semantic_scholar",
                lambda deadline=None: harvest_s2(since_date=since_12m, deadline=deadline, _partial=_partial_results),
                max_minutes=10)

    from source_crossref import harvest as harvest_crossref
    _run_source("crossref",
                lambda deadline=None: harvest_crossref(since_date=since_12m, deadline=deadline, _partial=_partial_results),
                max_minutes=10)

    from source_arxiv import harvest as harvest_arxiv
    _run_source("arxiv",
                lambda deadline=None: harvest_arxiv(since_date=since_12m, deadline=deadline, _partial=_partial_results),
                max_minutes=10)

    from source_in_progress import harvest_nih_reporter, harvest_nsf_awards
    _run_source("nih_reporter",
                lambda deadline=None: harvest_nih_reporter(
                    since_date=since_12m, _partial=_partial_results),
                max_minutes=10)
    _run_source("nsf_awards",
                lambda deadline=None: harvest_nsf_awards(
                    since_date=since_12m, _partial=_partial_results),
                max_minutes=12)

    from source_openbu import harvest as harvest_openbu
    _run_source("openbu",
                lambda deadline=None: harvest_openbu(
                    since_year=int(since_12m[:4]), _partial=_partial_results),
                max_minutes=20)

    from source_scholarly_commons import harvest as harvest_sc
    _run_source("scholarly_commons",
                lambda deadline=None: harvest_sc(
                    since_year=int(since_12m[:4]), _partial=_partial_results),
                max_minutes=15)

    # NBER via OpenAlex
    try:
        from harvest_nber import harvest_nber_from_openalex
        _run_source("nber",
                    lambda deadline=None: harvest_nber_from_openalex(since_date=since_12m),
                    max_minutes=5)
    except ImportError as e:
        source_report["nber"] = {
            "count": 0, "status": "skipped", "error": str(e)[:200], "duration_s": 0,
        }

    # Everything harvested so far from sources that carry real affiliations also
    # counts as evidence. Fold it in, then derive the year windows DBLP is gated
    # against. This runs immediately before DBLP for a reason: DBLP is the source
    # with no affiliation data, so it should be judged against the most complete
    # picture this run can offer.
    try:
        fold_papers_into_bu_registry(all_papers, _bu_registry)
        fold_papers_into_bu_registry(load_master(), _bu_registry)
        _bu_windows, _ambiguous_names = registry_year_windows(_bu_registry)
        logger.info(
            f"BU author registry: {len(_bu_registry):,} authors "
            f"(+{len(_bu_registry)-_registry_at_start:,} this run), "
            f"{len(_bu_windows):,} names, {len(_ambiguous_names):,} names shared by "
            f"more than one BU identity"
        )
        save_bu_author_registry(_bu_registry)
        global _BU_WINDOWS_CACHE
        _BU_WINDOWS_CACHE = _bu_windows
    except Exception as e:
        logger.warning(f"Could not build BU author registry ({e}); DBLP ungated")
        _bu_windows, _ambiguous_names = {}, set()

    # DBLP dump (download -> parse -> verify)
    # The ~1 GB dump download sits outside _run_source, so it used to run to
    # completion even when there was no time left to parse it -- a gigabyte
    # pulled down and immediately deleted. Check the global budget first.
    _dblp_remaining = _global_deadline - time.time()
    if _dblp_remaining <= 5 * 60:
        logger.warning("  dblp: SKIPPED - not enough global budget left to download and parse the dump")
        source_report["dblp"] = {
            "count": 0,
            "status": "SKIPPED_NO_TIME",
            "error": f"global harvest budget ({global_budget_min:.0f}m) exhausted before the DBLP dump",
            "duration_s": 0.0,
        }
        dblp_dump = None
    else:
        dblp_dump = download_dblp_dump()
    if dblp_dump:
        try:
            from harvest_dblp_dump import harvest_dump
            _run_source("dblp",
                        lambda deadline=None: harvest_dump(
                            dump_path=str(dblp_dump),
                            since_year=int(since_12m[:4])),
                        max_minutes=20)
            try:
                os.remove(str(dblp_dump))
                logger.info("Deleted DBLP dump to free disk space")
            except OSError:
                pass
        except Exception as e:
            source_report["dblp"] = {
                "count": 0, "status": "FAILED", "error": str(e)[:200], "duration_s": 0,
            }
            logger.error(f"  dblp dump parse failed: {e}")
            try:
                from source_dblp import harvest as harvest_dblp_api
                _run_source("dblp",
                            lambda deadline=None: harvest_dblp_api(since_year=int(since_12m[:4])),
                            max_minutes=10)
            except Exception as e2:
                source_report["dblp"] = {
                    "count": 0, "status": "FAILED",
                    "error": f"Dump: {source_report.get('dblp',{}).get('error','')}; API: {e2}",
                    "duration_s": 0,
                }
    elif _dblp_remaining > 5 * 60:
        # Dump download failed (not: skipped for lack of time). Fall back to the
        # per-faculty DBLP API.
        try:
            from source_dblp import harvest as harvest_dblp_api
            _run_source("dblp",
                        lambda deadline=None: harvest_dblp_api(
                            since_year=int(since_12m[:4])),
                        max_minutes=10)
        except Exception as e:
            source_report["dblp"] = {
                "count": 0, "status": "FAILED", "error": f"Download failed; API: {e}", "duration_s": 0,
            }

    total = sum(r["count"] for r in source_report.values())
    ok = sum(1 for r in source_report.values() if r["status"] == "ok")
    failed = sum(1 for r in source_report.values() if r["status"] == "FAILED")
    partial = sum(1 for r in source_report.values() if r["status"].startswith("PARTIAL"))
    logger.info(f"Harvest complete: {total} papers from {ok} sources ({partial} partial, {failed} failed)")

    return all_papers, source_report


# ═══════════════════════════════════════════════════════════════════════════
# DEDUPLICATION & FILTERING
# ═══════════════════════════════════════════════════════════════════════════

# A title fingerprint is only a safe identity when the title is distinctive.
# The non-BU index currently contains the fingerprints of "Machine Learning" and
# "Deep Learning" (verified by membership check), which means any future
# BU-authored book chapter or review with either of those exact titles is
# silently dropped before classification, forever. 188 papers in master already
# have normalized titles under 25 characters, so the collision surface is real.
MIN_FINGERPRINT_TITLE_LEN = 30


def safe_fingerprint(title: str) -> str:
    """Title fingerprint, but only for titles distinctive enough to identify a
    paper. Returns "" for short titles so they are never used as a blocklist
    key. Reading is unaffected; this guards what we WRITE."""
    fp = title_fingerprint(title or "")
    normalized = "".join(c for c in (title or "").lower() if c.isalnum())
    if len(normalized) < MIN_FINGERPRINT_TITLE_LEN:
        return ""
    return fp


def prefilter_version() -> str:
    """Short hash of everything that determines a pre-filter verdict.

    This is what makes it safe to remember pre-filter rejections. A paper the
    filter dropped is skipped on future runs ONLY while the filter is unchanged;
    touch the keyword list, the reference texts or the threshold and the version
    changes, every remembered rejection is invalidated, and the papers get
    re-evaluated automatically.

    Without this, remembering rejections would permanently entomb whatever the
    filter got wrong -- which is exactly how the old stack's 53% loss rate would
    have become unrecoverable.
    """
    basis = json.dumps({
        "keywords": sorted(ALL_AI_KEYWORDS),
        "refs": AI_REFERENCE_TEXTS,
        "threshold": EMBEDDING_THRESHOLD,
    }, sort_keys=True)
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:12]


def load_prefilter_seen() -> dict:
    if os.path.exists(PREFILTER_SEEN_PATH):
        try:
            with open(PREFILTER_SEEN_PATH) as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Could not read pre-filter seen index: {e}")
    return {"entries": {}}


def prefilter_seen_keys() -> set:
    """Keys rejected by the CURRENT filter configuration. Entries recorded under
    a different filter version are ignored, so they get another chance."""
    version = prefilter_version()
    data = load_prefilter_seen()
    keys = {k for k, v in data.get("entries", {}).items() if v.get("v") == version}
    stale = len(data.get("entries", {})) - len(keys)
    if keys or stale:
        logger.info(
            f"Pre-filter memory: {len(keys):,} papers rejected under the current "
            f"filter ({version})"
            + (f"; {stale:,} entries from older filter versions will be re-evaluated"
               if stale else "")
        )
    return keys


def record_prefilter_drops(dropped: list[dict]):
    """Remember papers the pre-filter rejected, so later runs skip the embedding
    pass and the Sonnet call instead of re-deciding them from scratch.

    Run #12 dropped 16,020 candidates. Without this every subsequent run
    re-harvests them, re-fetches their abstracts, re-embeds them and re-reaches
    the same verdict. Entries are stamped with the filter version so the memory
    is invalidated the moment the filter changes.
    """
    if not dropped:
        return {"added": 0, "total": 0}

    version = prefilter_version()
    data = load_prefilter_seen()
    entries = data.setdefault("entries", {})
    stamp = date.today().strftime("%Y-%m")
    added = 0

    for d in dropped:
        doi = normalize_doi(d.get("doi") or "")
        key = doi or safe_fingerprint(d.get("title") or "")
        if not key:
            continue
        if entries.get(key, {}).get("v") == version:
            continue
        entries[key] = {
            "v": version,
            "s": d.get("embedding_score"),
            "t": (d.get("title") or "")[:120],
            "d": stamp,
        }
        added += 1

    data["filter_version"] = version
    try:
        with open(PREFILTER_SEEN_PATH, "w") as f:
            json.dump(data, f, separators=(",", ":"))
        logger.info(
            f"Pre-filter memory: +{added:,} rejections recorded "
            f"({len(entries):,} total, filter {version})"
        )
    except Exception as e:
        logger.warning(f"Could not write pre-filter seen index: {e}")
    return {"added": added, "total": len(entries)}


def load_rejected_index() -> tuple[set, set]:
    """Load DOIs and title fingerprints of previously rejected papers."""
    if os.path.exists(REJECTED_PATH):
        with open(REJECTED_PATH) as f:
            data = json.load(f)
        return set(data.get("dois", [])), set(data.get("fingerprints", []))
    return set(), set()


def save_rejected_index(dois: set, fingerprints: set):
    """Save rejected papers index to disk."""
    with open(REJECTED_PATH, "w") as f:
        json.dump({"dois": sorted(dois), "fingerprints": sorted(fingerprints)}, f)
    logger.info(f"Rejected index saved: {len(dois)} DOIs, {len(fingerprints)} fingerprints")


def record_rejections(papers: list[dict]):
    """Add papers classified as not_relevant to the rejection index."""
    dois, fps = load_rejected_index()
    added = 0
    for p in papers:
        doi = normalize_doi(p.get("doi", ""))
        fp = safe_fingerprint(p.get("title", ""))
        if doi and doi not in dois:
            dois.add(doi)
            added += 1
        if fp and fp not in fps:
            fps.add(fp)
            added += 1
    if added:
        save_rejected_index(dois, fps)
    logger.info(f"Recorded {len(papers)} rejections ({added} new index entries)")


def load_non_bu_ai_index() -> tuple[set, set]:
    """Load DOIs and title fingerprints of papers that were AI-relevant but failed BU verification."""
    if os.path.exists(NON_BU_AI_PATH):
        with open(NON_BU_AI_PATH) as f:
            data = json.load(f)
        return set(data.get("dois", [])), set(data.get("fingerprints", []))
    return set(), set()


def save_non_bu_ai_index(dois: set, fingerprints: set):
    with open(NON_BU_AI_PATH, "w") as f:
        json.dump({"dois": sorted(dois), "fingerprints": sorted(fingerprints)}, f)
    logger.info(f"Non-BU AI index saved: {len(dois)} DOIs, {len(fingerprints)} fingerprints")


def record_non_bu_ai(papers: list[dict]):
    """Add papers that classified as AI-relevant but didn't pass BU verification.
    Saves ~$10/month by skipping them on future harvests (they'd just fail BU verification again)."""
    dois, fps = load_non_bu_ai_index()
    added = 0
    for p in papers:
        doi = normalize_doi(p.get("doi", ""))
        fp = safe_fingerprint(p.get("title", ""))
        if doi and doi not in dois:
            dois.add(doi)
            added += 1
        if fp and fp not in fps:
            fps.add(fp)
            added += 1
    if added:
        save_non_bu_ai_index(dois, fps)
    logger.info(f"Recorded {len(papers)} non-BU AI papers ({added} new index entries)")


# ── BU author registry ──────────────────────────────────────────────────────
# Who is a BU author, and in which years, established from evidence rather than
# from a name appearing on a department web page.
#
# The evidence is already flowing past us. Every OpenAlex work we page through
# during harvest is filtered by BU's ROR, and each authorship on it says whether
# THAT author was at BU on THAT paper, with the publication year attached. We
# parse it, use it once for the AI filter, and throw it away.
#
# Folding it into a registry instead gives, for free and with no extra API call,
# the thing the roster was trying and failing to be: every person with evidence
# of a BU affiliation, their OpenAlex ID and ORCID, and the year span over which
# that evidence exists. A department scrape says "this name is on a web page".
# This says "this identity published from BU in these years".


def load_bu_author_registry() -> dict:
    if os.path.exists(BU_AUTHOR_REGISTRY_PATH):
        try:
            with open(BU_AUTHOR_REGISTRY_PATH) as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Could not read BU author registry: {e}")
    return {}


def save_bu_author_registry(registry: dict):
    try:
        with open(BU_AUTHOR_REGISTRY_PATH, "w") as f:
            json.dump(registry, f, separators=(",", ":"), sort_keys=True)
        logger.info(f"BU author registry saved: {len(registry):,} authors")
    except Exception as e:
        logger.warning(f"Could not write BU author registry: {e}")


BU_AFFILIATION_MARKERS = (
    "boston university", "boston univ", "boston medical center",
    "chobanian", "bumc", "va boston", "boston va",
)


def _attests_bu(authorship: dict) -> bool:
    """Does this authorship's OWN affiliation string say Boston University?

    is_bu cannot be trusted as evidence, because several things set it that are
    not evidence of anything. Measured on the current master, of 25,288 is_bu
    authorships only 18,917 carry a BU affiliation string: OpenBU stamps
    "Boston University" onto every author of every record it holds including
    external co-authors (1,470 authorships), NIH Reporter and Semantic Scholar
    carry no affiliation at all yet come through at 4.5% and 7.0% attested, and
    verify_bu_authors flips the flag on a roster name match with no affiliation
    to back it. Building the registry off is_bu would be circular: it would
    launder name matches into "evidence" and then use that evidence to approve
    name matches.
    """
    aff = (authorship.get("affiliation") or "").lower()
    return bool(aff) and any(k in aff for k in BU_AFFILIATION_MARKERS)


def fold_papers_into_bu_registry(papers: list[dict], registry: dict) -> int:
    """Record every author with an attested BU affiliation, and the year.

    Keyed by OpenAlex author ID where we have one, because an ID is an identity
    and a name is not -- that distinction is exactly what the DBLP namesake
    problem is made of. Names are kept as an index onto those identities.

    Two independent conditions, both required: the paper comes from a source
    that carries real per-paper affiliation data, and the authorship's own
    affiliation string names BU. OpenBU passes the first and fails the second;
    a name-matched DBLP record fails both.
    """
    added = 0
    for p in papers:
        src = p.get("source")
        if src is not None and src not in AFFILIATION_TRUSTED_SOURCES:
            continue
        y = p.get("year")
        if not isinstance(y, int) or not (1900 < y < 2100):
            continue
        for a in (p.get("authors") or []):
            if not _attests_bu(a):
                continue
            nm = (a.get("name") or "").strip()
            key = a.get("openalex_id") or (("name:" + nm.lower()) if nm else None)
            if not key:
                continue
            e = registry.get(key)
            if e is None:
                registry[key] = {
                    "name": nm or None,
                    "orcid": a.get("orcid") or None,
                    "first": y, "last": y, "n": 1,
                    "names": [nm] if nm else [],
                }
                added += 1
            else:
                e["first"] = min(e["first"], y)
                e["last"] = max(e["last"], y)
                e["n"] = e.get("n", 0) + 1
                if nm and nm not in e.get("names", []):
                    e.setdefault("names", []).append(nm)
                if a.get("orcid") and not e.get("orcid"):
                    e["orcid"] = a["orcid"]
    return added


def _reg_name_key(name: str) -> str:
    """Fold a display name to a comparison key.

    Unicode dashes and casing split identities that are the same person:
    the roster carries "Ching\u2010Ray Chang" while OpenAlex returns
    "Ching-Ray Chang", and without folding, a bu=False recorded against one
    spelling fails to be overridden by BU evidence carried on the other.
    """
    import unicodedata
    n = unicodedata.normalize("NFKC", name or "").casefold()
    for dash in ("\u2010", "\u2011", "\u2012", "\u2013", "\u2014", "\u2212"):
        n = n.replace(dash, "-")
    return " ".join(n.split())


def registry_year_windows(registry: dict) -> tuple[dict, set]:
    """Name -> {"first", "last", "bu"} across every BU identity with that name.

    "bu": False only when EVERY identity carrying that name has been checked
    against its OpenAlex profile and none of them has ever published from BU.
    That is the Li Fei-Fei case -- on our roster, never at BU -- and it is the
    only situation in which a name match should be refused outright rather than
    merely bounded by years.

    Also returns the set of names shared by more than one BU identity, where a
    bare string match cannot tell you which person you have.
    """
    by_name, ids_per_name = {}, {}
    for key, e in registry.items():
        names = e.get("names") or ([e["name"]] if e.get("name") else [])
        for nm in names:
            if not nm:
                continue
            nk = _reg_name_key(nm)
            if not nk:
                continue
            ids_per_name.setdefault(nk, set()).add(key)
            w = by_name.get(nk)
            f, l, bu = e.get("first"), e.get("last"), e.get("bu", True)
            if w is None:
                by_name[nk] = {"first": f, "last": l, "bu": bool(bu)}
                continue
            if bu:
                w["bu"] = True
            if f is not None:
                w["first"] = f if w["first"] is None else min(w["first"], f)
            if l is not None:
                w["last"] = l if w["last"] is None else max(w["last"], l)
    ambiguous = {nm for nm, ids in ids_per_name.items() if len(ids) > 1}
    for nm in ambiguous:
        # More than one identity answers to this name, so no single identity's
        # history can speak for it. Fall back to the year window, which is a
        # bound rather than a resolution and is safe to union.
        w = by_name.get(nm)
        if w is not None and not w.get("bu"):
            w["bu"] = True
    return by_name, ambiguous


def bu_name_verdict(name: str, year, windows: dict) -> bool | None:
    """Is a bare name match to `name` on a paper from `year` believable?

    True  -- yes, the person was at BU then
    False -- no; refuse the match
    None  -- we hold no evidence about this person either way
    """
    w = windows.get(_reg_name_key(name))
    if w is None:
        return None
    if not w.get("bu"):
        return False
    f, l = w.get("first"), w.get("last")
    if not year or f is None or l is None:
        return True
    return (f - BU_WINDOW_GRACE_YEARS) <= year <= (l + BU_WINDOW_GRACE_YEARS)


# Sources that carry real, per-paper affiliation data. A BU affiliation string
# on a paper from one of these is independent evidence that the author was at BU
# when the paper was written. DBLP carries no affiliations at all, and the
# per-faculty harvesters fabricate them from a name match, so neither counts.
AFFILIATION_TRUSTED_SOURCES = {"openalex", "pubmed", "biorxiv", "nber"}

# Grace either side of an author's documented BU years. Indexing lags, a paper
# submitted at BU can appear after a move, and affiliation data is patchy.
BU_WINDOW_GRACE_YEARS = 2


def dedup_against_master(new_papers: list[dict], master_dois: set, master_fps: set) -> list[dict]:
    """Filter new_papers against master dataset, rejection index, and non-BU AI index."""
    rejected_dois, rejected_fps = load_rejected_index()
    non_bu_dois, non_bu_fps = load_non_bu_ai_index()
    prefilter_rejected = prefilter_seen_keys()

    unique = []
    skipped_master = 0
    skipped_rejected = 0
    skipped_non_bu = 0
    skipped_prefilter = 0
    seen_this_run = set()
    dupes_within_run = 0
    for p in new_papers:
        doi = normalize_doi(p.get("doi", ""))
        fp = title_fingerprint(p.get("title", ""))

        # Within-run dedup. Every harvester extends one shared list, so a paper
        # found by OpenAlex AND PubMed AND DBLP in the same sweep was embedded
        # and classified once per source. Master already carries 6 duplicate
        # DOIs and 42 duplicate title fingerprints from exactly this.
        run_key = doi or fp
        if run_key:
            if run_key in seen_this_run:
                dupes_within_run += 1
                continue
            seen_this_run.add(run_key)

        # Already judged not-AI by this exact filter configuration.
        if (doi and doi in prefilter_rejected) or (fp and fp in prefilter_rejected):
            skipped_prefilter += 1
            continue
        if doi and doi in master_dois:
            skipped_master += 1
            continue
        if fp and fp in master_fps:
            skipped_master += 1
            continue
        if doi and doi in rejected_dois:
            skipped_rejected += 1
            continue
        if fp and fp in rejected_fps:
            skipped_rejected += 1
            continue
        if doi and doi in non_bu_dois:
            skipped_non_bu += 1
            continue
        if fp and fp in non_bu_fps:
            skipped_non_bu += 1
            continue
        unique.append(p)
    logger.info(
        f"Dedup: {len(new_papers):,} → {len(unique):,} new "
        f"({skipped_master:,} in master, {dupes_within_run:,} duplicates within this run, "
        f"{skipped_prefilter:,} already rejected by this filter version, "
        f"{skipped_rejected:,} previously rejected by Sonnet, "
        f"{skipped_non_bu:,} previously non-BU)"
    )
    return unique


# Word-boundary matcher, built once. Plain substring matching meant "RAG" fired
# on average / leverage / storage / coverage (1,872 papers in master, and the
# ONLY matching keyword for 448 of them), "BERT" on hilbert / robert /
# liberties -- so BU Law papers were passing the AI filter on the word
# "liberties" -- and "LLM" on hallmark / enrollment / fulfillment.
#
# Left boundary only, deliberately. A full \b...\b matcher looks more correct
# and drops 1,234 master papers, because "neural network" must keep matching
# "neural networks" and "robot" must keep matching "robots" and "robotic".
_AI_KEYWORD_RE = re.compile(
    "|".join(r"(?<![a-z0-9])" + re.escape(kw.lower()) for kw in ALL_AI_KEYWORDS)
)

# Reference texts for the embedding filter. The original five were 2015-era
# vocabulary ("data mining", "neural network training optimization") with no
# LLM, agent, governance or applied-clinical language at all. Since MiniLM
# scores whole-document topical similarity, a clinical abstract dominated by
# trial vocabulary scored far from every reference even when the method was a
# CNN -- which is why Applied AI survived at 21.8% and School of Law at 14.7%.
AI_REFERENCE_TEXTS = [
    "artificial intelligence machine learning deep learning",
    "neural network training optimization backpropagation",
    "natural language processing computer vision speech recognition",
    "data mining classification prediction algorithm",
    "reinforcement learning generative model",
    "large language model foundation model pretrained transformer",
    "generative AI multimodal vision-language model diffusion",
    "AI governance regulation policy law ethics accountability",
    "algorithmic fairness bias transparency explainability",
    "applying machine learning to clinical data to predict patient outcomes",
    "statistical prediction model risk stratification electronic health records",
    "medical imaging segmentation computer-aided diagnosis radiology",
    "autonomous agents robotics planning control",
    "self-supervised representation learning embeddings retrieval",
    "model alignment interpretability evaluation benchmarks safety",
    # A second wave, added after measuring which confirmed-AI papers the filter
    # still dropped. MiniLM scores whole-document topical similarity, so a paper
    # is only close to a reference if it talks the same way -- and none of the
    # fifteen above talk like a 1998 computer vision paper, a cortical circuit
    # model, a formal-methods control paper, or a bioinformatics method. At the
    # same 0.30 threshold these eighteen take the embedding arm from recovering
    # 5.9% of those missed papers to 57.3%.
    "computer vision image processing object detection tracking segmentation recognition",
    "feature extraction pattern recognition classification of images and video",
    "signal processing estimation filtering time series prediction from noisy measurements",
    "speech and audio processing acoustic modelling recognition synthesis",
    "statistical learning regression classification inference from data with a fitted model",
    "probabilistic graphical models Bayesian inference latent variables expectation maximization",
    "dimensionality reduction manifold learning clustering unsupervised structure discovery",
    "computational neuroscience neural network models of brain circuits learning and memory",
    "adaptive resonance theory cortical models attention consciousness perceptual learning",
    "brain computer interface neural decoding of EEG and spiking activity",
    "robotics motion planning control of autonomous vehicles and multi-robot teams",
    "optimal control model predictive control formal methods temporal logic specifications",
    "network science graph algorithms link prediction community structure in complex networks",
    "computational biology bioinformatics predicting protein structure function and interactions",
    "gene regulatory network inference from expression data systems biology modelling",
    "optimization linear and integer programming combinatorial algorithms operations research",
    "computational social science analysis of social media text and online behaviour",
    "recommender systems collaborative filtering information retrieval ranking",
]

# Papers kept by the embedding filter alone need to clear this. Higher than the
# old 0.25 because the filter is now an OR arm rather than a second gate.
EMBEDDING_THRESHOLD = 0.30

PREFILTER_DROP_LOG = "logs/prefilter_drops_{ym}.jsonl"


def _paper_text(p: dict) -> str:
    """Title + abstract, defensively. Note `or ""` rather than a default: master
    contains records where the key exists with a null value, and None + " " is a
    TypeError that would abort the whole run."""
    return ((p.get("title") or "") + " " + (p.get("abstract") or "")).strip()


def keyword_prefilter(papers: list[dict]) -> list[dict]:
    """Keep papers that mention any AI keyword in title or abstract.

    Kept as a standalone function for the hand-run scripts that call it. The
    monthly pipeline uses ai_prefilter, which combines this with the embedding
    signal rather than gating on it.
    """
    kept = [p for p in papers if _AI_KEYWORD_RE.search(_paper_text(p).lower())]
    logger.info(f"Keyword filter: {len(papers)} → {len(kept)}")
    return kept


def _embedding_scores(papers: list[dict]) -> list[float] | None:
    """Max cosine similarity of each paper against AI_REFERENCE_TEXTS.

    Returns None if the model can't be loaded, so callers can fall back to
    keyword-only rather than dropping everything.
    """
    if not papers:
        return []
    try:
        from sentence_transformers import SentenceTransformer, util
        logger.info("Loading sentence-transformer model...")
        model = SentenceTransformer("all-MiniLM-L6-v2")
        ref_embeddings = model.encode(AI_REFERENCE_TEXTS, convert_to_tensor=True)
        # 1,000 chars, not 500: 92.5% of abstracts exceeded the old cap, so the
        # filter was judging most papers on their first few sentences, which for
        # a clinical paper is the framing rather than the method.
        texts = [_paper_text(p)[:1000] for p in papers]
        embeddings = model.encode(texts, convert_to_tensor=True, show_progress_bar=False)
        return [util.cos_sim(embeddings[i], ref_embeddings).max().item()
                for i in range(len(papers))]
    except Exception as e:
        # Was `except ImportError`, which did not cover the realistic failure:
        # SentenceTransformer() raises OSError/HfHubHTTPError when the Hugging
        # Face download fails, and that propagated up and killed the run.
        logger.warning(f"Embedding filter unavailable ({type(e).__name__}: {e}); keyword-only")
        return None


def embedding_prefilter(papers: list[dict], threshold: float = EMBEDDING_THRESHOLD) -> list[dict]:
    """Standalone embedding filter, for hand-run scripts. See ai_prefilter."""
    scores = _embedding_scores(papers)
    if scores is None:
        return papers
    kept = [p for i, p in enumerate(papers) if scores[i] >= threshold]
    logger.info(f"Embedding filter: {len(papers)} → {len(kept)} (threshold={threshold})")
    return kept


def backfill_abstracts(papers: list[dict], deadline: float | None = None) -> dict:
    """Fill in missing abstracts before the AI pre-filter runs. Mutates in place.

    Why this exists: on the 2026-08-18 full sweep, 21,954 of 43,469 candidates
    (50.5%) had no abstract at all. ai_prefilter passes those straight to Sonnet,
    because a bare title is too thin for either the keyword or the embedding
    signal to mean anything -- which is the right call for the handful of SSRN
    and law papers a normal monthly run sees, and the wrong economics when a
    full sweep drags in all of DBLP. It also hurts quality: master contains
    duplicate-title pairs where the copy WITH an abstract and the copy WITHOUT
    got different relevance tiers from the same classifier.

    Semantic Scholar's batch endpoint takes 500 IDs per request, so the whole
    backlog is ~44 requests. Measured on 500 real DOIs from master: 2.2 seconds
    per request, 447/500 resolved, 290 (58%) carried an abstract.

    Papers with no DOI can't be looked up this way and are left alone.
    """
    missing = [p for p in papers
               if not (p.get("abstract") or "").strip() and p.get("doi")]
    no_doi = sum(1 for p in papers
                 if not (p.get("abstract") or "").strip() and not p.get("doi"))

    if not missing:
        logger.info(f"Abstract backfill: nothing to do ({no_doi} abstract-less papers have no DOI)")
        return {"attempted": 0, "filled": 0, "no_doi": no_doi}

    logger.info(
        f"Abstract backfill: {len(missing)} papers missing an abstract have a DOI "
        f"({no_doi} more have neither)"
    )

    by_doi = {}
    for p in missing:
        by_doi.setdefault(normalize_doi(p["doi"]), []).append(p)

    headers = openalex_headers()
    api_key = os.environ.get("S2_API_KEY", "")
    if api_key:
        headers["x-api-key"] = api_key

    dois = list(by_doi)
    filled = 0
    BATCH = 500

    for i in range(0, len(dois), BATCH):
        if deadline is not None and time.time() >= deadline:
            logger.warning(f"  abstract backfill: time budget hit, stopping at {i}/{len(dois)}")
            break
        chunk = dois[i:i + BATCH]
        try:
            resp = requests.post(
                "https://api.semanticscholar.org/graph/v1/paper/batch",
                params={"fields": "abstract"},
                json={"ids": [f"DOI:{d}" for d in chunk]},
                headers=headers,
                timeout=120,
            )
            if resp.status_code == 429:
                time.sleep(5)
                continue
            if resp.status_code != 200:
                logger.warning(f"  abstract backfill: HTTP {resp.status_code}, skipping chunk")
                continue
            results = resp.json()
        except Exception as e:
            logger.warning(f"  abstract backfill: chunk failed ({e})")
            continue

        # The batch endpoint returns results positionally, null for unresolved.
        for doi, record in zip(chunk, results):
            if not record:
                continue
            abstract = (record.get("abstract") or "").strip()
            if not abstract:
                continue
            for p in by_doi[doi]:
                p["abstract"] = abstract
                p["abstract_source"] = "semantic_scholar_backfill"
                filled += 1

        if (i // BATCH) % 10 == 0:
            logger.info(f"  abstract backfill: {i + len(chunk)}/{len(dois)} looked up, {filled} filled")

    logger.info(
        f"Abstract backfill: filled {filled} of {len(missing)} "
        f"({100 * filled / max(len(missing), 1):.0f}%)"
    )
    return {"attempted": len(missing), "filled": filled, "no_doi": no_doi}


def ai_prefilter(papers: list[dict], threshold: float = EMBEDDING_THRESHOLD) -> tuple[list[dict], dict]:
    """Decide which harvested papers are worth sending to Sonnet.

    A paper is kept if ANY of these holds:
      * it has no abstract -- title-only text is too thin for either signal to
        mean anything, and these are 16% of the corpus, concentrated in exactly
        the law and working-paper sources the filters were worst at;
      * it matches an AI keyword;
      * it clears the embedding threshold.

    This used to be a strict AND chain, evaluated keyword-first, so the semantic
    filter -- the only one that can catch vocabulary the keyword list doesn't
    know -- only ever saw papers the keyword list had already approved. Measured
    against the 11,903 confirmed-AI papers in master, the old chain kept 46.4%.

    The precision this bought was never needed. A real logged run harvested 975
    papers, kept 17, and cost $0.09 against a $15 cap. Sonnet is the actual gate
    and it is cheap; the pre-filter's job is to keep the bill sane, not to make
    the AI/not-AI decision on its own.

    Returns (kept, stats).
    """
    if not papers:
        return papers, {"input": 0, "kept": 0}

    scores = _embedding_scores(papers)
    kept, dropped = [], []

    for i, p in enumerate(papers):
        has_abstract = bool((p.get("abstract") or "").strip())
        kw = bool(_AI_KEYWORD_RE.search(_paper_text(p).lower()))
        emb = scores[i] if scores is not None else None
        emb_hit = emb is not None and emb >= threshold

        if not has_abstract or kw or emb_hit:
            kept.append(p)
        else:
            dropped.append({
                "title": (p.get("title") or "")[:200],
                "doi": p.get("doi"),
                "source": p.get("source"),
                "year": p.get("year"),
                "embedding_score": round(emb, 4) if emb is not None else None,
            })

    # Diagnostic only, changes no behaviour. The abstract-less passthrough is
    # the single biggest cost driver on a full sweep, so record how many of
    # those papers would still survive if the rule required a keyword hit on
    # the title. That turns the next rule decision into a measurement instead
    # of a guess.
    title_only = [p for p in papers if not (p.get("abstract") or "").strip()]
    title_only_kw = sum(
        1 for p in title_only if _AI_KEYWORD_RE.search((p.get("title") or "").lower())
    )
    stats = {
        "input": len(papers),
        "kept": len(kept),
        "dropped": len(dropped),
        "no_abstract": len(title_only),
        "no_abstract_with_keyword_in_title": title_only_kw,
        "embedding_available": scores is not None,
        "threshold": threshold,
    }
    if title_only:
        logger.info(
            f"  of {len(title_only)} abstract-less papers passed through, "
            f"{title_only_kw} ({100*title_only_kw/len(title_only):.0f}%) match an AI keyword on the title alone"
        )

    # Remember the rejections so later runs skip them entirely, and log them in
    # full so "what did we lose last month?" is answerable. Both filters used to
    # log counts only -- no title, no DOI, no score -- which is how a >50% loss
    # rate stayed invisible for months.
    if dropped:
        stats["memory"] = record_prefilter_drops(dropped)
    if dropped:
        try:
            os.makedirs("logs", exist_ok=True)
            path = PREFILTER_DROP_LOG.format(ym=date.today().strftime("%Y%m"))
            with open(path, "a") as f:
                for d in dropped:
                    f.write(json.dumps(d, ensure_ascii=False) + "\n")
            logger.info(f"Logged {len(dropped)} pre-filter drops to {path}")
        except Exception as e:
            logger.warning(f"Could not write pre-filter drop log: {e}")

    logger.info(
        f"AI pre-filter: {len(papers)} → {len(kept)} "
        f"(dropped {len(dropped)}; {stats['no_abstract']} had no abstract and were passed through)"
    )
    return kept, stats


# ═══════════════════════════════════════════════════════════════════════════
# COST ESTIMATION
# ═══════════════════════════════════════════════════════════════════════════

def estimate_cost(paper_count: int) -> float:
    """Estimate Sonnet API cost in USD before classification."""
    return paper_count * AVG_COST_PER_PAPER


# ═══════════════════════════════════════════════════════════════════════════
# CLASSIFICATION
# ═══════════════════════════════════════════════════════════════════════════

def classify_via_sonnet(papers: list[dict], hard_cap_usd: float = 5.0) -> tuple[list[dict], float]:
    """Classify papers using Sonnet standard API (not batch).

    Returns (classified_papers, actual_cost_usd).
    Uses the same system prompt and model as classify_papers.py.
    """
    import anthropic

    client = anthropic.Anthropic()
    classified = []
    total_cost = 0.0
    parse_failures = 0
    vocab_warnings = 0
    consecutive_errors = 0
    rl = RateLimiter(5)  # 5 calls/sec; tier 2 Anthropic accounts can safely run at this rate

    for i, paper in enumerate(papers):
        if total_cost >= hard_cap_usd:
            logger.warning(f"Hard cost cap ${hard_cap_usd} reached after {i} papers")
            break

        rl.wait()
        prompt_text = paper_to_prompt_text(paper)

        try:
            msg = client.messages.create(
                model=MODEL,
                max_tokens=512,
                temperature=0.0,
                system=_system_block(),
                messages=[{"role": "user", "content": prompt_text}],
            )

            # Parse response (same logic as classify_papers.py)
            text = msg.content[0].text if msg.content else "{}"
            clean = text.strip()
            if clean.startswith("```"):
                clean = clean.split("\n", 1)[1].rsplit("```", 1)[0].strip()

            try:
                result = json.loads(clean)
            except json.JSONDecodeError:
                # A parse failure used to be labelled "peripheral" with 300 chars
                # of raw model output as the annotation, which shipped to the
                # public site as an AI Studies paper. Skip it instead: the paper
                # is not merged, not indexed as rejected, and is re-harvested
                # next month within the 18-month window.
                parse_failures += 1
                logger.warning(f"  parse failure on {(paper.get('title') or '')[:70]!r}; skipping")
                continue

            result, warns = validate_classification(result)
            if result["ai_relevance"] == "parse_error":
                parse_failures += 1
                continue
            vocab_warnings += len(warns)

            # Merge classification into paper
            paper["ai_relevance"] = result["ai_relevance"]
            paper["confidence"] = result["confidence"]
            paper["publication_status"] = result.get("publication_status", "other")
            paper["one_line_summary"] = result.get("one_line_summary", "")
            paper["domains"] = result["domains"]
            paper["subfields"] = result["subfields"]
            paper["annotation"] = result.get("annotation", "")

            # Track tokens and cost
            input_tok = msg.usage.input_tokens
            output_tok = msg.usage.output_tokens
            paper["input_tokens"] = input_tok
            paper["output_tokens"] = output_tok
            cost = (input_tok * COST_PER_INPUT_MTOK + output_tok * COST_PER_OUTPUT_MTOK) / 1_000_000
            total_cost += cost

            classified.append(paper)
            consecutive_errors = 0
            logger.debug(f"  [{i+1}/{len(papers)}] {paper.get('title', '')[:60]}... → {paper['ai_relevance']}")

        except Exception as e:
            error_str = str(e)
            low = error_str.lower()
            # Detect fatal errors that won't resolve by retrying the next paper
            if "credit balance" in error_str or "billing" in low:
                logger.error(f"BILLING ERROR - aborting classification: {e}")
                break
            if "authentication" in low or "api key" in low:
                logger.error(f"AUTH ERROR - aborting classification: {e}")
                break
            if "not_found_error" in error_str or ("404" in error_str and "model" in low):
                logger.error(f"MODEL NOT FOUND - aborting classification (bad MODEL constant?): {e}")
                break

            # Every error class now feeds one counter, reset only on success.
            #
            # Previously the counter incremented only on invalid_request_error +
            # 400, and the transient branch reset it to zero on its way past --
            # so overloaded_error (529), rate_limit_error (429), api_error (500)
            # and every network failure could repeat for all N papers without
            # ever tripping the abort. That is the same shape as the incident
            # where a bad model ID burned two hours of CI looping over ~3,800
            # 404s, just with a different error class, and each affected paper
            # is silently dropped: not classified, not merged, not recorded in
            # any index.
            consecutive_errors += 1
            if any(k in error_str for k in ("overloaded_error", "rate_limit_error")) or "529" in error_str or "429" in error_str:
                backoff = min(2 ** min(consecutive_errors, 6), 60)
                logger.warning(f"API overloaded/rate-limited, backing off {backoff}s: {error_str[:120]}")
                time.sleep(backoff)
            else:
                logger.error(f"Classification error ({consecutive_errors} consecutive): {e}")

            if consecutive_errors >= 10:
                logger.error(
                    f"{consecutive_errors} consecutive API errors - aborting classification. "
                    f"{len(papers) - i - 1} papers not classified this run."
                )
                break
            continue

    skipped = len(papers) - len(classified)
    logger.info(
        f"Classified {len(classified)}/{len(papers)} papers, cost: ${total_cost:.2f}"
        + (f" | {parse_failures} parse failures" if parse_failures else "")
        + (f" | {vocab_warnings} off-vocabulary values normalized" if vocab_warnings else "")
        + (f" | {skipped} not classified" if skipped else "")
    )
    return classified, total_cost


# ═══════════════════════════════════════════════════════════════════════════
# BU AUTHOR VERIFICATION
# ═══════════════════════════════════════════════════════════════════════════

_BU_WINDOWS_CACHE = None


def _cached_bu_windows() -> dict:
    """Windows from the registry on disk, read once per process.

    Callers that do not pass windows explicitly still get the check, which is
    the point: merge_batch_results and the batch merge path both call
    verify_bu_authors and neither should be able to opt out of it by omission.
    """
    global _BU_WINDOWS_CACHE
    if _BU_WINDOWS_CACHE is None:
        try:
            _BU_WINDOWS_CACHE = registry_year_windows(load_bu_author_registry())[0]
        except Exception as e:
            logger.warning(f"Could not load BU author registry ({e}); name matches unchecked")
            _BU_WINDOWS_CACHE = {}
    return _BU_WINDOWS_CACHE


def verify_bu_authors(papers: list[dict], windows: dict | None = None) -> list[dict]:
    """Verify BU authorship. Drops papers left with zero BU authors.

    Tier 1: is_bu already set by the source from a real affiliation (OpenAlex ROR)
    Tier 2: OpenAlex author ID matches the roster
    Tier 3: unique full-name match against the roster

    Tiers 2 and 3 are name-or-id matches against a roster that says only "this
    person is associated with BU", with no sense of when. That is how a
    bibliography of BU research ends up holding papers written at Stanford:
    DBLP carries no affiliations at all, so one name match imports the matched
    person's entire career. Measured on the current master, 858 of 1,586 DBLP
    papers sit outside their author's documented BU years, and another 82 belong
    to people with no BU evidence anywhere -- Li Fei-Fei is on our roster.

    So both tiers are checked against the BU author registry, which knows the
    years each identity actually published from BU. This used to be a gate
    bolted onto the DBLP call sites only; it belongs here, where it covers every
    source that matches by name.
    """
    if windows is None:
        windows = _cached_bu_windows()
    verified = []
    refused_never_bu = refused_window = 0

    for paper in papers:
        has_bu = False
        year = paper.get("year")

        for author in paper.get("authors", []):
            # Tier 1: already flagged by the source from a real affiliation.
            if author.get("is_bu"):
                has_bu = True
                continue

            name = author.get("name", "")
            verdict = bu_name_verdict(name, year, windows)
            if verdict is False:
                w = windows.get(_reg_name_key(name)) or {}
                if w.get("bu"):
                    refused_window += 1
                else:
                    refused_never_bu += 1
                continue

            # Tier 2: OpenAlex author ID
            oa_id = author.get("openalex_id")
            if oa_id and oa_id in FACULTY_BY_OAID:
                author["is_bu"] = True
                has_bu = True
                continue

            # Tier 3: full-name match only (no initial fallback).
            # Skip empty/whitespace keys: non-Latin names normalize to ' ' or '' under
            # _normalize_name's [^a-z\s-] strip, and would otherwise collide with any
            # similarly-stripped roster entry (the "Lei Guo trojan" pattern).
            fkey = _name_key(name)
            if not fkey.strip():
                continue
            matches = FACULTY_BY_FULLNAME.get(fkey, [])
            if len(matches) == 1:
                author["is_bu"] = True
                has_bu = True
                continue

        if has_bu:
            verified.append(paper)

    logger.info(
        f"BU verification: {len(papers):,} → {len(verified):,} with confirmed BU authors "
        f"({refused_window:,} name matches refused as outside the author's BU years, "
        f"{refused_never_bu:,} as people who have never published from BU)"
    )
    return verified


# ═══════════════════════════════════════════════════════════════════════════
# MERGE & OUTPUT
# ═══════════════════════════════════════════════════════════════════════════

def merge_into_master(master: list[dict], new_papers: list[dict]) -> list[dict]:
    """Append new papers to master, derive fields, reindex."""
    # Resolve real journal/book venues for SSRN and Scholarly Commons entries
    # before they hit master. Repository platforms return "SSRN Electronic
    # Journal" or empty venue; this looks up CrossRef by title+author and
    # patches the venue when the work has actually been published in a real
    # journal. Best-effort: failures don't block the merge.
    try:
        import resolve_repository_venues as _rrv
        import requests as _requests
        sess = _requests.Session()
        n_fixed = 0
        for paper in new_papers:
            if _rrv.needs_lookup(paper):
                result = _rrv.find_real_venue(paper, sess)
                if result:
                    paper["venue"] = result[0]
                    n_fixed += 1
        if n_fixed:
            logger.info(f"Repository venues resolved: {n_fixed}")
    except Exception as e:
        logger.warning(f"Repository venue resolution failed (non-fatal): {e}")

    for paper in new_papers:
        # Derive fields (same as classify_papers.py)
        d = derived_fields(paper)
        paper["bu_author_names"] = d.get("bu_author_names", [])
        paper["best_url"] = d.get("best_url", "")
        paper["is_open_access"] = d.get("is_open_access", False)

        # Ensure all_sources is a list
        if "all_sources" not in paper:
            paper["all_sources"] = [paper.get("source", "unknown")]

        master.append(paper)

    # Reindex
    for i, p in enumerate(master):
        p["index"] = i

    # Canonical-name pass: BU author names from upstream sources are inconsistent
    # ("Christopher Robertson" vs "Christopher T. Robertson", missing periods,
    # truncated middle initials). Run normalize_author_names so every BU author
    # appears with their roster-canonical form site-wide. Best-effort.
    try:
        import normalize_author_names as _nan
        try:
            with open("data/bu_faculty_roster_verified.json") as f:
                roster = json.load(f)
            with open("data/openalex_bu_authors_cache.json") as f:
                altnames_cache = json.load(f)
        except FileNotFoundError as e:
            logger.warning(f"Skipping name normalization: {e}")
        else:
            canonical, stats = _nan.build_canonical_map(master, roster, altnames_cache)
            if stats.get("names_changed", 0):
                _nan.apply_canonical_names(master, canonical)
                logger.info(
                    f"Author names normalized: {stats['names_changed']} changes "
                    f"(OAID={stats['matched_oaid']}, alt={stats['matched_altnames']}, "
                    f"name={stats['matched_fullname']})"
                )
    except Exception as e:
        logger.warning(f"Name normalization failed (non-fatal): {e}")

    logger.info(f"Merged: +{len(new_papers)} → {len(master)} total")
    return master


def regenerate_all_outputs(master_path: str = MASTER_PATH):
    """Regenerate all data.js files from master dataset, then propagate counts
    to README.md and the GitHub repo description so the public artifacts stay
    in sync. Site reads counts dynamically from data.js; README and the repo
    description are static and need this propagation step.
    """
    result = generate_all(master_path)
    logger.info(
        f"Regenerated data.js: {result['paper_count']} papers, "
        f"public={result['public_size_mb']}MB, private={result['private_size_mb']}MB"
    )

    # Propagate paper count, source mentions, and roster size to README +
    # GitHub repo description. Best-effort: log and continue if `gh` isn't
    # available (e.g., in a CI runner without the CLI installed).
    try:
        import propagate_counts
        master = json.loads(open(master_path).read())
        roster = json.loads(open("data/bu_faculty_roster_verified.json").read())
        counts = propagate_counts.compute_counts(master, roster)
        old = open("README.md").read()
        new = propagate_counts.update_readme(old, counts)
        if new != old:
            with open("README.md", "w") as f:
                f.write(new)
            logger.info("README.md: counts updated")
        desc = propagate_counts.make_repo_description(counts)
        if propagate_counts.update_gh_description(desc):
            logger.info("GitHub repo description updated")
    except Exception as e:
        logger.warning(f"propagate_counts failed (non-fatal): {e}")

    return result


# ═══════════════════════════════════════════════════════════════════════════
# VALIDATION
# ═══════════════════════════════════════════════════════════════════════════

HTML_FILES = [
    "docs/index.html",
    "output/bibliography_app/index.html",
]

DATA_JS_FILES = [
    "output/bibliography_app/data.js",
    "docs/data.js",
]


def validate_before_push(old_count: int, new_count: int) -> list[str]:
    """Run all pre-push validations. Returns list of error messages (empty = ok)."""
    errors = []

    # Paper count check
    if new_count < old_count - 5:
        errors.append(f"Paper count dropped: {old_count} → {new_count}")

    # HTML files exist
    for path in HTML_FILES:
        if not os.path.exists(path):
            errors.append(f"HTML file missing: {path}")

    # data.js files valid
    for path in DATA_JS_FILES:
        if not validate_data_js(path):
            errors.append(f"data.js invalid: {path}")

    return errors


# ═══════════════════════════════════════════════════════════════════════════
# NOTIFICATIONS
# ═══════════════════════════════════════════════════════════════════════════

def notify_macos(title: str, message: str):
    """Send macOS notification. No-op on other platforms."""
    if sys.platform != "darwin":
        return
    try:
        subprocess.run([
            "osascript", "-e",
            f'display notification "{message}" with title "{title}"'
        ], capture_output=True, timeout=5)
    except Exception:
        pass


def create_github_issue(title: str, body: str, labels: list[str] = None) -> str | None:
    """Create a GitHub Issue via gh CLI. Returns issue URL or None."""
    cmd = ["gh", "issue", "create", "--title", title, "--body", body]
    if labels:
        cmd.extend(["--label", ",".join(labels)])
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            url = result.stdout.strip()
            logger.info(f"Created issue: {url}")
            return url
        else:
            logger.error(f"gh issue create failed: {result.stderr}")
    except FileNotFoundError:
        logger.warning("gh CLI not found, skipping issue creation")
    except Exception as e:
        logger.error(f"Issue creation error: {e}")
    return None


# ═══════════════════════════════════════════════════════════════════════════
# GIT OPERATIONS
# ═══════════════════════════════════════════════════════════════════════════

def git_commit_and_push(message: str) -> bool:
    """Stage data files, commit as Marc Woernle, and push."""
    try:
        # Ensure git config
        subprocess.run(["git", "config", "user.name", "Marc Woernle"], capture_output=True)
        subprocess.run(["git", "config", "user.email", "marcwho13@gmail.com"], capture_output=True)

        # Stage data files only. The two indexes are critical: if they aren't committed,
        # each CI run starts from a stale copy and rediscovers the same rejections/non-BU
        # papers every month.
        files_to_stage = [
            MASTER_PATH, STATE_PATH, LOG_PATH,
            REJECTED_PATH, NON_BU_AI_PATH,
            # The pre-filter memory is only worth keeping if it is committed;
            # otherwise every CI run starts blank and re-decides the same
            # tens of thousands of papers.
            PREFILTER_SEEN_PATH,
            # The BU author registry is the accumulated evidence of who published
            # from BU and when; it is only useful if it persists between runs.
            BU_AUTHOR_REGISTRY_PATH,
            "output/bibliography_app/data.js",
            "output/bibliography_app/data_private.js",
            "docs/data.js",
            # The roster is rebuilt in phase 1 at real cost (24 department scrapes
            # + ~1,400 live OpenAlex ID resolutions, ~16 min). Leaving it unstaged
            # meant every CI run threw that work away and started from the stale
            # committed copy, so roster-driven school tagging and BU verification
            # could never improve month over month.
            "data/bu_faculty_roster_verified.json",
            # propagate_counts patches the paper counts into README during
            # regenerate_all_outputs; unstaged, that edit died with the runner.
            "README.md",
        ]
        for f in files_to_stage:
            if os.path.exists(f):
                subprocess.run(["git", "add", f], capture_output=True)

        # Check if there are changes
        result = subprocess.run(["git", "diff", "--staged", "--quiet"], capture_output=True)
        if result.returncode == 0:
            logger.info("No changes to commit")
            return True

        # Commit and push
        subprocess.run(["git", "commit", "-m", message], capture_output=True, check=True)
        subprocess.run(["git", "push", "origin", "main"], capture_output=True, check=True, timeout=120)
        logger.info(f"Pushed: {message}")

        # Update repo description with current paper count
        try:
            with open(MASTER_PATH) as f:
                count = len(json.load(f))
            subprocess.run([
                "gh", "repo", "edit", "marc-woernle/bu-ai-bibliography",
                "--description", f"Comprehensive annotated bibliography of AI research at Boston University \u2014 {count:,} papers, auto-updating",
            ], capture_output=True, timeout=15)
        except Exception:
            pass  # Non-critical

        return True

    except subprocess.CalledProcessError as e:
        logger.error(f"Git error: {e}")
        return False


# ═══════════════════════════════════════════════════════════════════════════
# MONTHLY: CITATION REFRESH
# ═══════════════════════════════════════════════════════════════════════════

def refresh_citations(master: list[dict], max_age_months: int = 24) -> dict:
    """Batch DOI lookups to OpenAlex to refresh citation counts.

    Returns dict with counts: {updated, milestones_100, milestones_1000, errors}.
    """
    cutoff_year = date.today().year - (max_age_months // 12) - 1
    recent = [(i, p) for i, p in enumerate(master) if (p.get("year") or 0) >= cutoff_year and p.get("doi")]

    logger.info(f"Citation refresh: {len(recent)} papers with DOIs from {cutoff_year}+")

    updated = 0
    milestones_100 = []
    milestones_1000 = []
    errors = 0

    # Batch lookups: 50 DOIs per request
    for batch_start in range(0, len(recent), 50):
        batch = recent[batch_start:batch_start + 50]
        dois = [p.get("doi") for _, p in batch]
        doi_filter = "|".join(f"https://doi.org/{d}" for d in dois if d)

        _openalex_rl.wait()
        try:
            resp = requests.get(
                "https://api.openalex.org/works",
                params={"filter": f"doi:{doi_filter}", "per_page": 50, "mailto": CONTACT_EMAIL},
                headers=openalex_headers(),
                timeout=30,
            )
            if resp.status_code == 429:
                time.sleep(10)
                continue
            resp.raise_for_status()
            results = {
                normalize_doi(w.get("doi", "")): w
                for w in resp.json().get("results", [])
                if w.get("doi")
            }
        except Exception as e:
            logger.error(f"Citation refresh batch error: {e}")
            errors += 1
            continue

        for idx, paper in batch:
            doi = normalize_doi(paper.get("doi", ""))
            if doi in results:
                work = results[doi]
                old_count = paper.get("citation_count", 0) or 0
                new_count = work.get("cited_by_count", 0) or 0
                if new_count != old_count:
                    master[idx]["citation_count"] = new_count
                    updated += 1
                    # Check milestones
                    if old_count < 100 <= new_count:
                        milestones_100.append(paper.get("title", ""))
                    if old_count < 1000 <= new_count:
                        milestones_1000.append(paper.get("title", ""))

                # Also refresh open access status
                is_oa = work.get("open_access", {}).get("is_oa", False)
                master[idx]["is_open_access"] = is_oa

    logger.info(f"Citations updated: {updated}, milestones: {len(milestones_100)} @100, {len(milestones_1000)} @1000")
    return {
        "updated": updated,
        "milestones_100": milestones_100,
        "milestones_1000": milestones_1000,
        "errors": errors,
    }


# ═══════════════════════════════════════════════════════════════════════════
# MONTHLY: PREPRINT-TO-PUBLICATION TRACKING
# ═══════════════════════════════════════════════════════════════════════════

def track_preprint_publications(master: list[dict]) -> list[dict]:
    """Check if preprints have been published as journal articles."""
    preprints = [
        (i, p) for i, p in enumerate(master)
        if p.get("publication_status") in ("preprint",)
        or p.get("publication_type") in ("preprint", "posted-content")
        and p.get("doi")
    ]

    logger.info(f"Checking {len(preprints)} preprints for publication status")
    updated = []

    for batch_start in range(0, len(preprints), 50):
        batch = preprints[batch_start:batch_start + 50]
        dois = [p.get("doi") for _, p in batch]
        doi_filter = "|".join(f"https://doi.org/{d}" for d in dois if d)

        _openalex_rl.wait()
        try:
            resp = requests.get(
                "https://api.openalex.org/works",
                params={"filter": f"doi:{doi_filter}", "per_page": 50, "mailto": CONTACT_EMAIL},
                headers=openalex_headers(),
                timeout=30,
            )
            resp.raise_for_status()
            results = {
                normalize_doi(w.get("doi", "")): w
                for w in resp.json().get("results", [])
                if w.get("doi")
            }
        except Exception:
            continue

        for idx, paper in batch:
            doi = normalize_doi(paper.get("doi", ""))
            if doi not in results:
                continue
            work = results[doi]
            primary = work.get("primary_location", {})
            source = primary.get("source", {})
            if source and source.get("type") == "journal":
                new_venue = source.get("display_name", "")
                if new_venue and new_venue != paper.get("venue", ""):
                    master[idx]["venue"] = new_venue
                    master[idx]["publication_status"] = "peer-reviewed article"
                    master[idx]["publication_type"] = "article"
                    updated.append(paper.get("title", ""))

    logger.info(f"Preprints now published: {len(updated)}")
    return updated


# ═══════════════════════════════════════════════════════════════════════════
# MONTHLY: BROKEN URL DETECTION
# ═══════════════════════════════════════════════════════════════════════════

def check_broken_urls(master: list[dict], sample_size: int = 100) -> list[dict]:
    """HEAD-request a random sample of non-DOI URLs. Returns broken entries."""
    import random

    # Only check non-DOI URLs (DOIs are stable)
    candidates = [
        p for p in master
        if p.get("best_url") and "doi.org" not in p.get("best_url", "")
    ]
    sample = random.sample(candidates, min(sample_size, len(candidates)))

    broken = []
    for p in sample:
        url = p["best_url"]
        try:
            resp = requests.head(url, timeout=10, allow_redirects=True)
            if resp.status_code >= 400:
                broken.append({"title": p.get("title", ""), "url": url, "status": resp.status_code})
        except Exception:
            broken.append({"title": p.get("title", ""), "url": url, "status": "timeout"})

    logger.info(f"Broken URLs: {len(broken)}/{len(sample)} checked")
    return broken


# ═══════════════════════════════════════════════════════════════════════════
# MONTHLY: BU AUTHORS REFRESH
# ═══════════════════════════════════════════════════════════════════════════

def refresh_bu_authors() -> int:
    """Refresh the BU author roster from OpenAlex. Returns new author count."""
    logger.info("Refreshing BU author roster from OpenAlex...")
    existing = set()
    if os.path.exists(BU_AUTHORS_PATH):
        with open(BU_AUTHORS_PATH) as f:
            for a in json.load(f):
                existing.add(a.get("name", "").lower().strip())

    authors = []
    cursor = "*"
    while cursor:
        _openalex_rl.wait()
        try:
            resp = requests.get(
                "https://api.openalex.org/authors",
                params={
                    "filter": f"affiliations.institution.ror:{BU_ROR_ID}",
                    "per_page": 200,
                    "cursor": cursor,
                    "mailto": CONTACT_EMAIL,
                },
                headers=openalex_headers(),
                timeout=30,
            )
            if resp.status_code == 429:
                time.sleep(10)
                continue
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error(f"Author refresh error: {e}")
            break

        for a in data.get("results", []):
            authors.append({
                "name": a.get("display_name", ""),
                "count": a.get("works_count", 0),
                "affiliation": "Boston University",
            })

        cursor = data.get("meta", {}).get("next_cursor")
        if not cursor or not data.get("results"):
            break

    new_count = sum(1 for a in authors if a["name"].lower().strip() not in existing)

    with open(BU_AUTHORS_PATH, "w") as f:
        json.dump(authors, f, ensure_ascii=False)

    logger.info(f"BU authors: {len(authors)} total, {new_count} new")
    return new_count


# ═══════════════════════════════════════════════════════════════════════════
# MONTHLY: DOMAIN TRENDS & NEW FACULTY
# ═══════════════════════════════════════════════════════════════════════════

def compute_domain_snapshot(master: list[dict]) -> dict:
    """Count papers per domain."""
    counts = {}
    for p in master:
        for d in p.get("domains", []):
            counts[d] = counts.get(d, 0) + 1
    return counts


def detect_domain_trends(current: dict, previous: dict) -> list[str]:
    """Compare domain distributions. Returns list of notable changes."""
    trends = []
    for domain, count in current.items():
        prev = previous.get(domain, 0)
        if prev > 0 and count > prev * 1.5:
            trends.append(f"{domain}: {prev} → {count} (+{count - prev}, {(count/prev - 1)*100:.0f}% growth)")
        elif prev == 0 and count >= 10:
            trends.append(f"{domain}: NEW with {count} papers")
    return trends


def detect_new_faculty_candidates(master: list[dict]) -> list[dict]:
    """Find BU authors with 5+ AI papers who aren't in the faculty roster."""
    author_counts = {}
    for p in master:
        for name in p.get("bu_author_names", []):
            author_counts[name] = author_counts.get(name, 0) + 1

    # Check against full roster (not just the old FACULTY_LOOKUP)
    known_keys = set(FACULTY_BY_FULLNAME.keys())

    candidates = []
    for name, count in sorted(author_counts.items(), key=lambda x: -x[1]):
        fkey = _name_key(name)
        if count >= 5 and fkey not in known_keys:
            candidates.append({"name": name, "paper_count": count})

    return candidates[:20]  # Top 20


# ═══════════════════════════════════════════════════════════════════════════
# MONTHLY: METADATA REFRESH
# ═══════════════════════════════════════════════════════════════════════════

def refresh_metadata_sample(master: list[dict], sample_size: int = 200) -> int:
    """Spot-check a sample of existing records for metadata changes in OpenAlex.

    Checks: title corrections, new abstracts, author changes.
    Returns count of records updated.
    """
    import random

    candidates = [
        (i, p) for i, p in enumerate(master)
        if p.get("doi") and (p.get("year") or 0) >= date.today().year - 3
    ]
    sample = random.sample(candidates, min(sample_size, len(candidates)))

    updated = 0
    for batch_start in range(0, len(sample), 50):
        batch = sample[batch_start:batch_start + 50]
        dois = [p.get("doi") for _, p in batch]
        doi_filter = "|".join(f"https://doi.org/{d}" for d in dois if d)

        _openalex_rl.wait()
        try:
            resp = requests.get(
                "https://api.openalex.org/works",
                params={"filter": f"doi:{doi_filter}", "per_page": 50, "mailto": CONTACT_EMAIL},
                headers=openalex_headers(),
                timeout=30,
            )
            resp.raise_for_status()
            results = {
                normalize_doi(w.get("doi", "")): w
                for w in resp.json().get("results", [])
                if w.get("doi")
            }
        except Exception:
            continue

        for idx, paper in batch:
            doi = normalize_doi(paper.get("doi", ""))
            if doi not in results:
                continue
            work = results[doi]

            # Check for new abstract
            if not paper.get("abstract") and work.get("abstract_inverted_index"):
                new_abs = _reconstruct_abstract(work["abstract_inverted_index"])
                if new_abs:
                    master[idx]["abstract"] = new_abs
                    updated += 1

    logger.info(f"Metadata refresh: {updated} records updated from sample of {len(sample)}")
    return updated


# ═══════════════════════════════════════════════════════════════════════════
# QUARTERLY: COLLABORATION & TRENDS
# ═══════════════════════════════════════════════════════════════════════════

def compute_cross_school_collaborations(master: list[dict]) -> list[tuple]:
    """Find which school pairs co-author the most papers."""
    from collections import Counter

    pair_counts = Counter()
    for p in master:
        schools = [s for s in p.get("bu_schools", []) if s != "Boston University (unspecified)"]
        if len(schools) >= 2:
            for i in range(len(schools)):
                for j in range(i + 1, len(schools)):
                    pair = tuple(sorted([schools[i], schools[j]]))
                    pair_counts[pair] += 1

    return pair_counts.most_common(15)


def compute_year_over_year(master: list[dict]) -> dict:
    """Papers per year for trend analysis."""
    counts = {}
    for p in master:
        y = p.get("year")
        if y:
            counts[y] = counts.get(y, 0) + 1
    return dict(sorted(counts.items()))


# ═══════════════════════════════════════════════════════════════════════════
# SANITY CHECKS
# ═══════════════════════════════════════════════════════════════════════════

def run_sanity_checks(
    new_count: int,
    state: dict,
    cost: float,
    source_errors: dict,
    run_type: str = "weekly",
) -> list[str]:
    """Run all sanity checks including ground truth validation. Returns alert messages."""
    alerts = []

    max_new = 200 if run_type == "weekly" else 500
    if new_count > max_new:
        alerts.append(f"Suspiciously high paper count: {new_count} (expected <{max_new})")

    if new_count == 0:
        weeks = state.get("consecutive_zero_weeks", 0) + 1
        if weeks >= 3:
            alerts.append(f"Zero new papers for {weeks} consecutive weeks")

    max_cost = 5.0 if run_type == "weekly" else 10.0
    if cost > max_cost:
        alerts.append(f"Cost ${cost:.2f} exceeds ${max_cost} cap")

    for source, health in source_errors.items():
        failures = health.get("consecutive_failures", 0)
        if failures >= 3:
            alerts.append(f"Source '{source}' has failed {failures} consecutive runs")

    # Ground truth validation — catch missing anchor faculty, data consistency
    try:
        from validate_dataset import (
            check_anchor_faculty,
            check_data_consistency,
            check_suspicious_patterns,
            load_data,
        )
        master, roster = load_data()
        for issue in check_anchor_faculty(master):
            if issue["level"] == "FAIL":
                alerts.append(f"GROUND TRUTH: {issue['message']}")
        for issue in check_data_consistency(master):
            if issue["level"] == "FAIL":
                alerts.append(f"DATA INTEGRITY: {issue['message']}")
        for issue in check_suspicious_patterns(master):
            if issue["level"] == "FAIL":
                alerts.append(f"SUSPICIOUS: {issue['message']}")
    except Exception as e:
        logger.warning(f"Ground truth validation skipped: {e}")

    return alerts
