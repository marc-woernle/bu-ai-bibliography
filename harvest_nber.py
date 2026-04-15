#!/usr/bin/env python3
"""
One-shot NBER harvest via OpenAlex.

Query: BU-affiliated papers whose primary location is NBER.
Dedup against master, keyword filter, save candidates for review.
"""

import json
import logging
import sys

from source_openalex import _parse_work, _headers, rate_limiter, BASE_URL
from config import BU_ROR_ID, CONTACT_EMAIL, ALL_AI_KEYWORDS
from utils import normalize_doi, title_fingerprint, save_checkpoint
import requests

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("harvest_nber")

MASTER_PATH = "data/sonnet_classification_bu_verified.json"
NBER_SOURCE_ID = "https://openalex.org/S2809516038"


def harvest_nber_from_openalex(per_page: int = 200, since_date: str | None = None) -> list[dict]:
    """Fetch all BU-affiliated NBER papers from OpenAlex.

    Args:
        per_page: Number of results per API page.
        since_date: If provided (YYYY-MM-DD), only return papers published on or after this date.
    """
    logger.info("=== Harvesting NBER papers via OpenAlex ===")

    filter_str = (
        f"authorships.institutions.ror:{BU_ROR_ID},"
        f"primary_location.source.id:{NBER_SOURCE_ID}"
    )
    if since_date:
        filter_str += f",from_publication_date:{since_date}"

    params = {
        "filter": filter_str,
        "per_page": per_page,
        "cursor": "*",
        "select": (
            "id,title,doi,publication_year,authorships,concepts,topics,"
            "abstract_inverted_index,open_access,cited_by_count,type,"
            "primary_location,sustainable_development_goals"
        ),
    }
    if CONTACT_EMAIL and CONTACT_EMAIL != "CHANGEME@bu.edu":
        params["mailto"] = CONTACT_EMAIL

    all_papers = []
    page_count = 0

    while True:
        rate_limiter.wait()
        resp = requests.get(
            f"{BASE_URL}/works", params=params, headers=_headers(), timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        results = data.get("results", [])
        if not results:
            break

        for work in results:
            paper = _parse_work(work)
            if paper["title"]:
                all_papers.append(paper)

        page_count += 1
        total = data.get("meta", {}).get("count", "?")
        logger.info(f"  Page {page_count}: +{len(results)} (total so far: {len(all_papers)} / {total})")

        next_cursor = data.get("meta", {}).get("next_cursor")
        if not next_cursor:
            break
        params["cursor"] = next_cursor

    logger.info(f"NBER harvest complete: {len(all_papers)} papers")
    return all_papers


def dedup_against_master(new_papers, master):
    """Filter new_papers to only those not already in master."""
    master_dois = set()
    master_fps = set()
    for p in master:
        doi = normalize_doi(p.get("doi", ""))
        if doi:
            master_dois.add(doi)
        fp = title_fingerprint(p.get("title", ""))
        if fp:
            master_fps.add(fp)

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


def keyword_prefilter(papers):
    """Keep papers that mention any AI keyword in title or abstract."""
    kept = []
    for p in papers:
        text = ((p.get("title") or "") + " " + (p.get("abstract") or "")).lower()
        if any(kw.lower() in text for kw in ALL_AI_KEYWORDS):
            kept.append(p)
    logger.info(f"Keyword filter: {len(papers)} → {len(kept)}")
    return kept


def main():
    # Step 1: Harvest
    nber_papers = harvest_nber_from_openalex()
    save_checkpoint(nber_papers, "nber_raw")

    # Step 2: Dedup against master
    logger.info("Loading master dataset...")
    with open(MASTER_PATH) as f:
        master = json.load(f)
    new_papers = dedup_against_master(nber_papers, master)

    if not new_papers:
        logger.info("No new NBER papers after dedup. Done.")
        return

    # Step 3: Keyword pre-filter
    ai_candidates = keyword_prefilter(new_papers)

    if not ai_candidates:
        logger.info("No NBER papers passed keyword filter. Done.")
        return

    # Step 4: Save candidates for review
    out_path = "data/nber_ai_candidates.json"
    with open(out_path, "w") as f:
        json.dump(ai_candidates, f, ensure_ascii=False, indent=2)

    # Summary
    print(f"\n=== NBER Harvest Summary ===")
    print(f"  Raw from OpenAlex:   {len(nber_papers)}")
    print(f"  New (after dedup):   {len(new_papers)}")
    print(f"  AI candidates:       {len(ai_candidates)}")
    print(f"  Saved to: {out_path}")

    # Show sample titles
    print(f"\nAI candidate titles:")
    for i, p in enumerate(ai_candidates):
        yr = p.get("year", "?")
        bu_authors = [a["name"] for a in p.get("authors", []) if a.get("is_bu")]
        print(f"  {i+1}. [{yr}] {p['title'][:100]}")
        if bu_authors:
            print(f"         BU: {', '.join(bu_authors[:3])}")


if __name__ == "__main__":
    main()
