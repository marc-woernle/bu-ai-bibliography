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
    RateLimiter,
    make_paper_record,
    normalize_doi,
    title_fingerprint,
)
from classify_papers import MODEL, SYSTEM_PROMPT, derived_fields, paper_to_prompt_text
from school_mapper import FACULTY_LOOKUP, classify_all, classify_paper
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
    """Save the master dataset with reindexing."""
    for i, p in enumerate(papers):
        p["index"] = i
    with open(MASTER_PATH, "w") as f:
        json.dump(papers, f, ensure_ascii=False)


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
        + [f'"{kw}"[Title/Abstract]' for kw in AI_KEYWORDS_PRIMARY[:10]]
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
        for offset in range(0, min(total, 2000), 500):
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

    law_last_names = [
        last.title()
        for (last, _), (school, _) in FACULTY_LOOKUP.items()
        if school == "School of Law"
    ]

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
# DEDUPLICATION & FILTERING
# ═══════════════════════════════════════════════════════════════════════════

def dedup_against_master(new_papers: list[dict], master_dois: set, master_fps: set) -> list[dict]:
    """Filter new_papers to only those not already in the master dataset."""
    unique = []
    for p in new_papers:
        doi = normalize_doi(p.get("doi", ""))
        if doi and doi in master_dois:
            continue
        fp = title_fingerprint(p.get("title", ""))
        if fp and fp in master_fps:
            continue
        unique.append(p)
    logger.info(f"Dedup: {len(new_papers)} → {len(unique)} new")
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
            logger.error(f"Classification error: {e}")
            continue

    logger.info(f"Classified {len(classified)}/{len(papers)} papers, cost: ${total_cost:.2f}")
    return classified, total_cost


# ═══════════════════════════════════════════════════════════════════════════
# BU AUTHOR VERIFICATION
# ═══════════════════════════════════════════════════════════════════════════

def load_bu_author_names() -> set[str]:
    """Load normalized BU author names from the OpenAlex roster."""
    if not os.path.exists(BU_AUTHORS_PATH):
        return set()
    with open(BU_AUTHORS_PATH) as f:
        authors = json.load(f)
    return {a["name"].lower().strip() for a in authors if a.get("name")}


def _name_matches(name1: str, name2: str) -> bool:
    """Fuzzy name matching: substring or initial matching."""
    n1 = name1.lower().strip()
    n2 = name2.lower().strip()
    if n1 == n2:
        return True
    if n1 in n2 or n2 in n1:
        return True
    # Initial matching: "J. Smith" matches "John Smith"
    parts1 = n1.split()
    parts2 = n2.split()
    if len(parts1) >= 2 and len(parts2) >= 2:
        if parts1[-1] == parts2[-1]:  # Same last name
            # Check if first name matches or is an initial
            f1, f2 = parts1[0].rstrip("."), parts2[0].rstrip(".")
            if f1[0] == f2[0] and (len(f1) == 1 or len(f2) == 1):
                return True
    return False


def verify_bu_authors(papers: list[dict]) -> list[dict]:
    """Three-tier BU verification. Drops papers with zero BU authors.

    Tier 1: Check is_bu flags (set by OpenAlex ROR matching)
    Tier 2: Match against bu_authors_from_openalex.json
    Tier 3: Match against FACULTY_LOOKUP
    """
    bu_names = load_bu_author_names()
    faculty_names = {
        f"{last} {first_initial}".lower(): True
        for (last, first_initial) in FACULTY_LOOKUP
    }

    verified = []
    for paper in papers:
        has_bu = False
        for author in paper.get("authors", []):
            name = author.get("name", "")

            # Tier 1: already flagged
            if author.get("is_bu"):
                has_bu = True
                continue

            # Tier 2: roster match
            if name.lower().strip() in bu_names:
                author["is_bu"] = True
                has_bu = True
                continue

            # Check fuzzy against roster
            if any(_name_matches(name, bn) for bn in list(bu_names)[:1000]):
                author["is_bu"] = True
                has_bu = True
                continue

            # Tier 3: faculty lookup
            parts = name.lower().split()
            if len(parts) >= 2:
                last = parts[-1]
                first_initial = parts[0][0]
                if (last, first_initial) in FACULTY_LOOKUP:
                    author["is_bu"] = True
                    has_bu = True

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
    """Find BU authors with 5+ AI papers who aren't in FACULTY_LOOKUP."""
    author_counts = {}
    for p in master:
        for name in p.get("bu_author_names", []):
            author_counts[name] = author_counts.get(name, 0) + 1

    known = set()
    for (last, fi) in FACULTY_LOOKUP:
        known.add(last.lower())

    candidates = []
    for name, count in sorted(author_counts.items(), key=lambda x: -x[1]):
        last = name.split()[-1].lower() if name.split() else ""
        if count >= 5 and last not in known:
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
    """Run all sanity checks. Returns list of alert messages."""
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

    return alerts
