#!/usr/bin/env python3
"""
Backfill OpenAlex author IDs onto paper author records.

Fetches author-level OpenAlex IDs from the OpenAlex API for papers that have
work-level IDs (source_id) or DOIs, and writes them onto the corresponding
author objects in the master dataset.

Usage:
    python backfill_author_oaids.py              # Run on full dataset
    python backfill_author_oaids.py --dry-run    # Show stats without saving
"""

import json
import time
import argparse
import requests
import unicodedata
import re
from pathlib import Path
from collections import defaultdict

from config import CONTACT_EMAIL, OPENALEX_RATE_LIMIT
from utils import RateLimiter

MASTER_PATH = Path("data/sonnet_classification_bu_verified.json")
BATCH_SIZE = 50  # OpenAlex supports up to 50 pipe-separated IDs per request
HEADERS = {"User-Agent": f"BU-AI-Bibliography/1.0 (mailto:{CONTACT_EMAIL})"}
BASE_URL = "https://api.openalex.org/works"

rate_limiter = RateLimiter(OPENALEX_RATE_LIMIT)


def _normalize(name: str) -> str:
    """Normalize a name for fuzzy matching."""
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    name = name.lower().strip()
    name = re.sub(r"[^a-z\s]", "", name)
    return re.sub(r"\s+", " ", name)


def _match_authors(paper_authors: list, oa_authorships: list):
    """Match paper author records to OpenAlex authorships.

    Returns dict mapping paper author index -> OpenAlex author ID.
    Uses position-first matching (authorships are ordered), with name
    fallback for length mismatches.
    """
    matches = {}

    # Build normalized name -> OA author ID from OpenAlex response
    oa_by_name = {}
    for auth in oa_authorships:
        oa_name = _normalize(auth.get("author", {}).get("display_name", ""))
        oa_id = auth.get("author", {}).get("id", "")
        if oa_name and oa_id:
            oa_by_name[oa_name] = oa_id

    if len(paper_authors) == len(oa_authorships):
        # Same length — match by position
        for i, (pa, oa) in enumerate(zip(paper_authors, oa_authorships)):
            oa_id = oa.get("author", {}).get("id", "")
            if oa_id:
                matches[i] = oa_id
    else:
        # Length mismatch — match by normalized name
        for i, pa in enumerate(paper_authors):
            pa_name = _normalize(pa.get("name", ""))
            if pa_name in oa_by_name:
                matches[i] = oa_by_name[pa_name]

    return matches


def fetch_works_batch(work_ids: list[str]) -> dict:
    """Fetch a batch of works from OpenAlex by ID. Returns {work_id: authorships}."""
    short_ids = [wid.split("/")[-1] for wid in work_ids]
    filter_str = "|".join(short_ids)

    rate_limiter.wait()
    resp = requests.get(
        BASE_URL,
        params={
            "filter": f"openalex_id:{filter_str}",
            "select": "id,authorships",
            "per_page": BATCH_SIZE,
            "mailto": CONTACT_EMAIL,
        },
        headers=HEADERS,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    results = {}
    for work in data.get("results", []):
        wid = work.get("id", "")
        results[wid] = work.get("authorships", [])

    return results


def fetch_works_by_doi(dois: list[str]) -> dict:
    """Fetch works by DOI. Returns {doi_lower: (work_id, authorships)}."""
    # OpenAlex wants full DOI URLs
    doi_filter = "|".join(dois)

    rate_limiter.wait()
    resp = requests.get(
        BASE_URL,
        params={
            "filter": f"doi:{doi_filter}",
            "select": "id,doi,authorships",
            "per_page": BATCH_SIZE,
            "mailto": CONTACT_EMAIL,
        },
        headers=HEADERS,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    results = {}
    for work in data.get("results", []):
        doi = (work.get("doi") or "").lower().replace("https://doi.org/", "")
        wid = work.get("id", "")
        if doi:
            results[doi] = (wid, work.get("authorships", []))

    return results


def backfill(papers: list[dict], dry_run: bool = False) -> dict:
    """Backfill OpenAlex author IDs onto paper author records.

    Returns stats dict.
    """
    stats = {
        "papers_total": len(papers),
        "papers_with_oa_source_id": 0,
        "papers_with_doi_only": 0,
        "papers_no_id": 0,
        "api_calls": 0,
        "authors_backfilled": 0,
        "papers_backfilled": 0,
    }

    # Group papers by available identifiers
    by_source_id = {}  # source_id -> paper index
    doi_only = {}      # doi_lower -> paper index

    for i, p in enumerate(papers):
        sid = p.get("source_id", "")
        doi = (p.get("doi") or "").lower()

        if sid.startswith("https://openalex.org/W"):
            by_source_id[sid] = i
            stats["papers_with_oa_source_id"] += 1
        elif doi:
            doi_only[doi] = i
            stats["papers_with_doi_only"] += 1
        else:
            stats["papers_no_id"] += 1

    print(f"Papers with OpenAlex work ID: {stats['papers_with_oa_source_id']}")
    print(f"Papers with DOI only: {stats['papers_with_doi_only']}")
    print(f"Papers with no ID: {stats['papers_no_id']}")

    # Batch fetch by OpenAlex work ID
    source_ids = list(by_source_id.keys())
    batches = [source_ids[i:i + BATCH_SIZE] for i in range(0, len(source_ids), BATCH_SIZE)]
    print(f"\nFetching {len(batches)} batches of OpenAlex works by ID...")

    for batch_num, batch in enumerate(batches):
        if dry_run:
            stats["api_calls"] += 1
            continue

        try:
            results = fetch_works_batch(batch)
            stats["api_calls"] += 1
        except Exception as e:
            print(f"  Batch {batch_num} failed: {e}")
            continue

        for wid, authorships in results.items():
            paper_idx = by_source_id.get(wid)
            if paper_idx is None:
                continue

            paper = papers[paper_idx]
            author_matches = _match_authors(paper.get("authors", []), authorships)

            if author_matches:
                stats["papers_backfilled"] += 1
                for ai, oa_id in author_matches.items():
                    paper["authors"][ai]["openalex_id"] = oa_id
                    stats["authors_backfilled"] += 1

        if (batch_num + 1) % 50 == 0:
            print(f"  {batch_num + 1}/{len(batches)} batches done...")

    # Batch fetch by DOI
    dois = list(doi_only.keys())
    doi_batches = [dois[i:i + BATCH_SIZE] for i in range(0, len(dois), BATCH_SIZE)]
    print(f"\nFetching {len(doi_batches)} batches by DOI...")

    for batch_num, batch in enumerate(doi_batches):
        if dry_run:
            stats["api_calls"] += 1
            continue

        try:
            results = fetch_works_by_doi(batch)
            stats["api_calls"] += 1
        except Exception as e:
            print(f"  DOI batch {batch_num} failed: {e}")
            continue

        for doi_lower, (wid, authorships) in results.items():
            paper_idx = doi_only.get(doi_lower)
            if paper_idx is None:
                continue

            paper = papers[paper_idx]
            author_matches = _match_authors(paper.get("authors", []), authorships)

            if author_matches:
                stats["papers_backfilled"] += 1
                for ai, oa_id in author_matches.items():
                    paper["authors"][ai]["openalex_id"] = oa_id
                    stats["authors_backfilled"] += 1

    return stats


def main():
    parser = argparse.ArgumentParser(description="Backfill OpenAlex author IDs")
    parser.add_argument("--dry-run", action="store_true", help="Show stats without saving")
    args = parser.parse_args()

    print(f"Loading {MASTER_PATH}...")
    with open(MASTER_PATH) as f:
        papers = json.load(f)

    # Count existing OAIDs
    existing = sum(
        1 for p in papers for a in p.get("authors", []) if a.get("openalex_id")
    )
    print(f"Existing author OAIDs: {existing}")

    stats = backfill(papers, dry_run=args.dry_run)

    print(f"\n=== Results ===")
    print(f"API calls: {stats['api_calls']}")
    print(f"Papers backfilled: {stats['papers_backfilled']}")
    print(f"Authors backfilled: {stats['authors_backfilled']}")

    if not args.dry_run:
        # Verify
        total_oaids = sum(
            1 for p in papers for a in p.get("authors", []) if a.get("openalex_id")
        )
        bu_oaids = sum(
            1 for p in papers
            for a in p.get("authors", [])
            if a.get("openalex_id") and a.get("is_bu")
        )
        print(f"\nTotal author OAIDs after backfill: {total_oaids}")
        print(f"BU author OAIDs after backfill: {bu_oaids}")

        print(f"\nSaving {MASTER_PATH}...")
        with open(MASTER_PATH, "w") as f:
            json.dump(papers, f, indent=2, ensure_ascii=False)
        print("Done.")
    else:
        print("\n(dry run — no changes saved)")


if __name__ == "__main__":
    main()
