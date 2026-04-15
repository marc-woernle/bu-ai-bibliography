"""
BU AI Bibliography Harvester -- DBLP Source
=============================================
Catches conference papers (NeurIPS, ICML, AAAI, etc.) that OpenAlex undercounts.
Uses the DBLP API (free, no auth). Rate limited to 1 req/sec (conservative).

Strategy: author-centric harvest. For each CS/Engineering/CDS faculty member,
search DBLP for their publications and collect conference papers.
"""

import argparse
import json
import logging
import requests
import time
from pathlib import Path
from config import CONTACT_EMAIL
from utils import RateLimiter, make_paper_record, save_checkpoint

logger = logging.getLogger("bu_bib.dblp")
SEARCH_URL = "https://dblp.org/search/publ/api"
DBLP_RATE_LIMIT = 1  # requests/second
rate_limiter = RateLimiter(DBLP_RATE_LIMIT)

# Schools likely to have DBLP presence
DBLP_SCHOOLS = {
    "CAS -- Computer Science",
    "CAS — Computer Science",
    "College of Engineering",
    "Faculty of Computing & Data Sciences",
    "CAS -- Mathematics & Statistics",
    "CAS — Mathematics & Statistics",
    "CAS -- Physics",
    "CAS — Physics",
}


def _headers():
    return {"User-Agent": f"BU-AI-Bibliography/1.0 (mailto:{CONTACT_EMAIL})"}


def _parse_hit(hit: dict) -> dict | None:
    """Parse a single DBLP search hit into a paper record."""
    info = hit.get("info", {})
    title = info.get("title", "")
    if not title:
        return None

    # Clean trailing period DBLP adds
    if title.endswith("."):
        title = title[:-1]

    # Authors
    raw_authors = info.get("authors", {}).get("author", [])
    if isinstance(raw_authors, dict):
        raw_authors = [raw_authors]

    authors = []
    for a in raw_authors:
        name = a.get("text", "") if isinstance(a, dict) else str(a)
        # DBLP has no affiliation data
        authors.append({
            "name": name,
            "affiliation": "",
            "is_bu": False,  # Will be set by verify_bu_authors downstream
        })

    # Year
    year = None
    year_str = info.get("year", "")
    if year_str:
        try:
            year = int(year_str)
        except ValueError:
            pass

    # DOI
    doi = info.get("doi", None)
    # DBLP sometimes has doi as URL path, normalize
    if doi and doi.startswith("https://doi.org/"):
        doi = doi[len("https://doi.org/"):]

    # Venue
    venue = info.get("venue", "")

    # Publication type
    pub_type = info.get("type", "")

    # URL
    url = info.get("url", "")
    if url and not url.startswith("http"):
        url = f"https://dblp.org/{url}"

    # DBLP key as source_id
    dblp_key = info.get("key", "")

    return make_paper_record(
        title=title,
        authors=authors,
        year=year,
        doi=doi if doi else None,
        abstract=None,  # DBLP has no abstracts
        source="dblp",
        source_id=dblp_key,
        url=url if url else None,
        venue=venue if venue else None,
        concepts=[],
        publication_type=pub_type,
        extra={
            "dblp_key": dblp_key,
            "dblp_type": pub_type,
        },
    )


def _search_author(name: str, max_results: int = 1000) -> list[dict]:
    """Search DBLP for publications by author name."""
    papers = []
    offset = 0
    batch = 100  # DBLP max per request is 1000, but smaller batches are safer

    while offset < max_results:
        rate_limiter.wait()
        try:
            resp = requests.get(SEARCH_URL, params={
                "q": f"author:{name}",
                "format": "json",
                "h": min(batch, max_results - offset),
                "f": offset,
            }, headers=_headers(), timeout=30)

            if resp.status_code == 429:
                logger.warning("DBLP rate limited, sleeping 10s")
                time.sleep(10)
                continue
            if resp.status_code == 503:
                logger.warning("DBLP 503, sleeping 30s and retrying")
                time.sleep(30)
                continue
            resp.raise_for_status()
        except requests.exceptions.RequestException as e:
            logger.error(f"DBLP request failed for {name}: {e}")
            time.sleep(5)
            break

        data = resp.json()
        hits_data = data.get("result", {}).get("hits", {})
        total = int(hits_data.get("@total", 0))
        hits = hits_data.get("hit", [])

        if not hits:
            break

        for h in hits:
            paper = _parse_hit(h)
            if paper:
                papers.append(paper)

        offset += len(hits)
        if offset >= total:
            break

    return papers


def _load_target_faculty(schools: set | None = None) -> list[dict]:
    """Load roster entries for faculty likely to have DBLP presence."""
    roster_path = Path(__file__).parent / "data" / "bu_faculty_roster_verified.json"
    with open(roster_path) as f:
        roster = json.load(f)

    target_schools = schools or DBLP_SCHOOLS
    return [
        f for f in roster
        if f.get("school", "") in target_schools
        and f.get("openalex_id")  # Only faculty we can verify
    ]


def harvest(test_limit: int = 0, since_year: int | None = None) -> list[dict]:
    """
    Main entry point: search DBLP for publications by BU CS/Engineering faculty.

    Args:
        test_limit: If >0, only search this many faculty (for testing).
        since_year: If provided, skip papers with year < since_year (client-side filter).
    """
    logger.info("=== DBLP harvest ===")
    faculty = _load_target_faculty()
    if test_limit > 0:
        faculty = faculty[:test_limit]

    logger.info(f"Searching DBLP for {len(faculty)} faculty members")

    all_papers = []
    seen_keys = set()  # Dedup by DBLP key
    faculty_with_results = 0

    for i, f in enumerate(faculty):
        name = f["name"]
        papers = _search_author(name)

        new_count = 0
        for p in papers:
            # Client-side date filter (DBLP API has no date param)
            if since_year and p.get("year") and p["year"] < since_year:
                continue
            key = p.get("extra", {}).get("dblp_key", "")
            if key and key not in seen_keys:
                seen_keys.add(key)
                all_papers.append(p)
                new_count += 1

        if new_count > 0:
            faculty_with_results += 1

        if (i + 1) % 50 == 0 or i == len(faculty) - 1:
            logger.info(f"  Progress: {i+1}/{len(faculty)} faculty, {len(all_papers)} papers")

    logger.info(f"DBLP total: {len(all_papers)} papers from {faculty_with_results} faculty")
    save_checkpoint(all_papers, "dblp")
    return all_papers


if __name__ == "__main__":
    from utils import setup_logging
    setup_logging()

    parser = argparse.ArgumentParser(description="DBLP harvest")
    parser.add_argument("--test", type=int, default=0,
                        help="Only search N faculty (for testing)")
    args = parser.parse_args()

    papers = harvest(test_limit=args.test)
    print(f"\nHarvested {len(papers)} papers from DBLP")
