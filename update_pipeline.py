#!/usr/bin/env python3
"""
Shared pipeline functions for the BU AI Bibliography auto-update system.

Used by update_weekly.py, update_monthly.py, and quarterly_review.py.
Imports from existing pipeline code — never modifies those files.
"""

import csv
import hashlib
import json
import logging
import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

# ── Imports from existing pipeline ──────────────────────────────────────────
from config import (
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
    RateLimiter,
    make_paper_record,
    normalize_doi,
    resilient_get,
    resilient_post,
    title_fingerprint,
)
from classify_papers import MODEL, SYSTEM_PROMPT, derived_fields, paper_to_prompt_text
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

# ── Cost constants (Sonnet standard API pricing) ────────────────────────────
COST_PER_INPUT_MTOK = 3.0    # $/MTok
COST_PER_OUTPUT_MTOK = 15.0  # $/MTok
AVG_INPUT_TOKENS = 800
AVG_OUTPUT_TOKENS = 200
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

def harvest_openalex_incremental(date_field: str, since_date: str) -> list[dict]:
    """Harvest BU papers from OpenAlex with a date filter.

    Args:
        date_field: "from_created_date" (weekly) or "from_publication_date" (monthly)
        since_date: ISO date string like "2026-03-01"
    """
    logger.info(f"OpenAlex: {date_field}={since_date}")
    papers = []
    cursor = "*"
    base_url = "https://api.openalex.org/works"

    retries = 0
    max_retries = 5
    while cursor:
        _openalex_rl.wait()
        params = {
            "filter": f"authorships.institutions.ror:{BU_ROR_ID},{date_field}:{since_date}",
            "per_page": 200,
            "cursor": cursor,
        }
        headers = openalex_headers()
        try:
            resp = requests.get(base_url, params=params, headers=headers, timeout=30)
            if resp.status_code == 429:
                retries += 1
                if retries > max_retries:
                    logger.error(f"OpenAlex: {max_retries} consecutive 429s, giving up")
                    break
                wait = min(10 * (2 ** (retries - 1)), 120)
                logger.warning(f"OpenAlex 429, backoff {wait}s (attempt {retries}/{max_retries})")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            retries = 0  # Reset on success
            data = resp.json()
        except requests.exceptions.HTTPError as e:
            logger.error(f"OpenAlex HTTP error: {e}")
            break
        except Exception as e:
            logger.error(f"OpenAlex error: {e}")
            break

        results = data.get("results", [])
        if not results:
            break

        for work in results:
            try:
                paper = _parse_work(work)
                if paper:
                    papers.append(paper)
            except Exception as e:
                logger.debug(f"Parse error: {e}")

        cursor = data.get("meta", {}).get("next_cursor")
        if not cursor:
            break

    logger.info(f"OpenAlex: {len(papers)} papers harvested")
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
    query = f'("Boston University"[Affiliation]) AND ({ai_terms})'

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
                    "rows": 25,
                    "select": "DOI,title,author,published-print,published-online,"
                              "abstract,URL,is-referenced-by-count,type,subject",
                },
                headers={"User-Agent": f"BU-AI-Bibliography/1.0 (mailto:{CONTACT_EMAIL})"},
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

def harvest_all_sources(since_12m: str, since_3m: str) -> tuple[list[dict], dict]:
    """Run all source harvesters with fault isolation, time budgets, and partial result capture.

    Returns (all_papers, source_report) where source_report maps source_name to
    {"count": int, "status": str, "error": str|None, "duration_s": float}
    """
    all_papers = []
    source_report = {}

    # Shared collector: harvesters append here so partial results survive exceptions
    _partial_results: list[dict] = []

    def _run_source(name: str, harvester, critical: bool = False, max_minutes: float = 15):
        """Run a harvester with fault isolation, time budget, and partial result capture."""
        nonlocal _partial_results
        _partial_results = []
        t0 = time.time()
        deadline = t0 + max_minutes * 60

        try:
            papers = harvester(deadline=deadline)
            all_papers.extend(papers)
            source_report[name] = {
                "count": len(papers),
                "status": "ok",
                "error": None,
                "duration_s": round(time.time() - t0, 1),
            }
            logger.info(f"  {name}: {len(papers)} papers ({time.time()-t0:.0f}s)")

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

    # Helper: wraps a legacy harvester (no deadline param) to work with _run_source
    def _legacy_wrap(harvest_fn, *args, **kwargs):
        """Wrap a harvest function that doesn't accept deadline."""
        def wrapper(deadline=None):
            return harvest_fn(*args, **kwargs)
        return wrapper

    # ── Core sources (always run, have incremental versions) ──
    _run_source("openalex",
                lambda deadline=None: harvest_openalex_incremental("from_publication_date", since_12m),
                critical=True, max_minutes=20)

    _run_source("pubmed",
                lambda deadline=None: harvest_pubmed_incremental(since_3m),
                critical=True, max_minutes=10)

    _run_source("biorxiv",
                lambda deadline=None: harvest_crossref_biorxiv_incremental(since_3m),
                max_minutes=5)

    _run_source("ssrn",
                lambda deadline=None: harvest_ssrn_by_faculty(),
                max_minutes=10)

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
                lambda deadline=None: harvest_nih_reporter(since_date=since_12m),
                max_minutes=5)
    _run_source("nsf_awards",
                lambda deadline=None: harvest_nsf_awards(since_date=since_12m),
                max_minutes=5)

    from source_openbu import harvest as harvest_openbu
    _run_source("openbu",
                lambda deadline=None: harvest_openbu(since_year=int(since_12m[:4])),
                max_minutes=10)

    from source_scholarly_commons import harvest as harvest_sc
    _run_source("scholarly_commons",
                lambda deadline=None: harvest_sc(since_year=int(since_12m[:4])),
                max_minutes=10)

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

    # DBLP dump (download -> parse -> verify)
    dblp_dump = download_dblp_dump()
    if dblp_dump:
        try:
            from harvest_dblp_dump import harvest_dump
            _run_source("dblp",
                        lambda deadline=None: harvest_dump(dump_path=str(dblp_dump), since_year=int(since_12m[:4])),
                        max_minutes=15)
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
    else:
        try:
            from source_dblp import harvest as harvest_dblp_api
            _run_source("dblp",
                        lambda deadline=None: harvest_dblp_api(since_year=int(since_12m[:4])),
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
        fp = title_fingerprint(p.get("title", ""))
        if doi and doi not in dois:
            dois.add(doi)
            added += 1
        if fp and fp not in fps:
            fps.add(fp)
            added += 1
    if added:
        save_rejected_index(dois, fps)
    logger.info(f"Recorded {len(papers)} rejections ({added} new index entries)")


def dedup_against_master(new_papers: list[dict], master_dois: set, master_fps: set) -> list[dict]:
    """Filter new_papers against master dataset AND rejected papers index."""
    rejected_dois, rejected_fps = load_rejected_index()
    all_dois = master_dois | rejected_dois
    all_fps = master_fps | rejected_fps

    unique = []
    skipped_master = 0
    skipped_rejected = 0
    for p in new_papers:
        doi = normalize_doi(p.get("doi", ""))
        fp = title_fingerprint(p.get("title", ""))
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
        unique.append(p)
    logger.info(f"Dedup: {len(new_papers)} → {len(unique)} new "
                f"({skipped_master} in master, {skipped_rejected} previously rejected)")
    return unique


def keyword_prefilter(papers: list[dict]) -> list[dict]:
    """Keep papers that mention any AI keyword in title or abstract."""
    kept = []
    for p in papers:
        text = ((p.get("title") or "") + " " + (p.get("abstract") or "")).lower()
        if any(kw.lower() in text for kw in ALL_AI_KEYWORDS):
            kept.append(p)
    logger.info(f"Keyword filter: {len(papers)} → {len(kept)}")
    return kept


def embedding_prefilter(papers: list[dict], threshold: float = 0.25) -> list[dict]:
    """Filter papers using sentence-transformer embeddings.

    Gracefully falls back to returning all papers if torch is unavailable.
    """
    if not papers:
        return papers

    try:
        from sentence_transformers import SentenceTransformer, util
        logger.info("Loading sentence-transformer model...")
        model = SentenceTransformer("all-MiniLM-L6-v2")

        # Reference AI sentences for similarity comparison
        ai_refs = [
            "artificial intelligence machine learning deep learning",
            "neural network training optimization",
            "natural language processing computer vision robotics",
            "data mining classification prediction algorithm",
            "reinforcement learning generative model",
        ]
        ref_embeddings = model.encode(ai_refs, convert_to_tensor=True)

        kept = []
        texts = [
            (p.get("title", "") + " " + (p.get("abstract", "") or ""))[:500]
            for p in papers
        ]
        paper_embeddings = model.encode(texts, convert_to_tensor=True, show_progress_bar=False)

        for i, p in enumerate(papers):
            scores = util.cos_sim(paper_embeddings[i], ref_embeddings)
            max_score = scores.max().item()
            if max_score >= threshold:
                kept.append(p)

        logger.info(f"Embedding filter: {len(papers)} → {len(kept)} (threshold={threshold})")
        return kept

    except ImportError:
        logger.warning("sentence-transformers not available; skipping embedding filter")
        return papers


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
    rl = RateLimiter(1)  # 1 call/sec

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
                system=SYSTEM_PROMPT,
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
                result = {
                    "ai_relevance": "peripheral",
                    "confidence": 0.3,
                    "domains": [],
                    "subfields": [],
                    "annotation": text[:300],
                    "_parse_error": True,
                }

            # Merge classification into paper
            paper["ai_relevance"] = result.get("ai_relevance", "peripheral")
            paper["confidence"] = result.get("confidence", 0.5)
            paper["publication_status"] = result.get("publication_status", "other")
            paper["one_line_summary"] = result.get("one_line_summary", "")
            paper["domains"] = result.get("domains", [])
            paper["subfields"] = result.get("subfields", [])
            paper["annotation"] = result.get("annotation", "")

            # Track tokens and cost
            input_tok = msg.usage.input_tokens
            output_tok = msg.usage.output_tokens
            paper["input_tokens"] = input_tok
            paper["output_tokens"] = output_tok
            cost = (input_tok * COST_PER_INPUT_MTOK + output_tok * COST_PER_OUTPUT_MTOK) / 1_000_000
            total_cost += cost

            classified.append(paper)
            logger.debug(f"  [{i+1}/{len(papers)}] {paper.get('title', '')[:60]}... → {paper['ai_relevance']}")

        except Exception as e:
            error_str = str(e)
            # Detect fatal errors that won't resolve by retrying the next paper
            if "credit balance" in error_str or "billing" in error_str.lower():
                logger.error(f"BILLING ERROR - aborting classification: {e}")
                break
            if "authentication" in error_str.lower() or "api key" in error_str.lower():
                logger.error(f"AUTH ERROR - aborting classification: {e}")
                break
            if "not_found_error" in error_str or ("404" in error_str and "model" in error_str.lower()):
                logger.error(f"MODEL NOT FOUND - aborting classification (bad MODEL constant?): {e}")
                break
            if "invalid_request_error" in error_str and "400" in error_str:
                logger.error(f"API request error: {e}")
                # Count consecutive failures to detect systemic issues
                if not hasattr(classify_via_sonnet, '_consecutive_errors'):
                    classify_via_sonnet._consecutive_errors = 0
                classify_via_sonnet._consecutive_errors += 1
                if classify_via_sonnet._consecutive_errors >= 5:
                    logger.error(f"5 consecutive API errors - aborting classification")
                    break
                continue
            # Transient error (rate limit, network, etc.) - skip this paper
            classify_via_sonnet._consecutive_errors = 0
            logger.error(f"Classification error: {e}")
            continue

    logger.info(f"Classified {len(classified)}/{len(papers)} papers, cost: ${total_cost:.2f}")
    return classified, total_cost


# ═══════════════════════════════════════════════════════════════════════════
# BU AUTHOR VERIFICATION
# ═══════════════════════════════════════════════════════════════════════════

def verify_bu_authors(papers: list[dict]) -> list[dict]:
    """Verify BU authorship using the faculty roster. Drops papers with zero BU authors.

    Tier 1: Check existing is_bu flags (set by OpenAlex ROR matching during harvest)
    Tier 2: OpenAlex author ID match against FACULTY_BY_OAID (zero false positives)
    Tier 3: Full-name unique match against roster (no initial-only fallback)
    """
    verified = []
    for paper in papers:
        has_bu = False

        for author in paper.get("authors", []):
            # Tier 1: already flagged by source (OpenAlex ROR match)
            if author.get("is_bu"):
                has_bu = True
                continue

            # Tier 2: OpenAlex author ID
            oa_id = author.get("openalex_id")
            if oa_id and oa_id in FACULTY_BY_OAID:
                author["is_bu"] = True
                has_bu = True
                continue

            # Tier 3: full-name match only (no initial fallback)
            name = author.get("name", "")
            fkey = _name_key(name)
            matches = FACULTY_BY_FULLNAME.get(fkey, [])
            if len(matches) == 1:
                author["is_bu"] = True
                has_bu = True
                continue

        if has_bu:
            verified.append(paper)

    logger.info(f"BU verification: {len(papers)} → {len(verified)} with confirmed BU authors")
    return verified


# ═══════════════════════════════════════════════════════════════════════════
# MERGE & OUTPUT
# ═══════════════════════════════════════════════════════════════════════════

def merge_into_master(master: list[dict], new_papers: list[dict]) -> list[dict]:
    """Append new papers to master, derive fields, reindex."""
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

    logger.info(f"Merged: +{len(new_papers)} → {len(master)} total")
    return master


def regenerate_all_outputs(master_path: str = MASTER_PATH):
    """Regenerate all data.js files from master dataset."""
    result = generate_all(master_path)
    logger.info(
        f"Regenerated data.js: {result['paper_count']} papers, "
        f"public={result['public_size_mb']}MB, private={result['private_size_mb']}MB"
    )
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

        # Stage data files only
        files_to_stage = [
            MASTER_PATH, STATE_PATH, LOG_PATH,
            "output/bibliography_app/data.js",
            "output/bibliography_app/data_private.js",
            "docs/data.js",
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
