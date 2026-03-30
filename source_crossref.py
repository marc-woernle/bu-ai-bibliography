"""
BU AI Bibliography Harvester — CrossRef Source
=================================================
Supplementary source that catches journal/conference papers
not found via other sources. CrossRef has excellent DOI coverage.
Filters for non-SSRN DOIs (SSRN is handled separately).
"""

import requests
import logging
import re
import time
from config import CROSSREF_RATE_LIMIT, CONTACT_EMAIL
from utils import RateLimiter, make_paper_record, save_checkpoint

logger = logging.getLogger("bu_bib.crossref")
BASE_URL = "https://api.crossref.org/works"
rate_limiter = RateLimiter(CROSSREF_RATE_LIMIT)


def _headers():
    return {"User-Agent": f"BU-AI-Bibliography/1.0 (mailto:{CONTACT_EMAIL})"}


def _parse_item(item: dict) -> dict | None:
    """Parse CrossRef work item."""
    titles = item.get("title", [])
    title = titles[0] if titles else ""
    if not title:
        return None

    authors = []
    for a in item.get("author", []):
        name = f"{a.get('given', '')} {a.get('family', '')}".strip()
        affs = [aff.get("name", "") for aff in a.get("affiliation", [])]
        authors.append({
            "name": name,
            "affiliation": "; ".join(affs),
            "is_bu": any("boston university" in af.lower() for af in affs),
        })

    year = None
    for date_field in ["published-print", "published-online", "created"]:
        date_parts = item.get(date_field, {}).get("date-parts", [[]])
        if date_parts and date_parts[0] and date_parts[0][0]:
            year = date_parts[0][0]
            break

    abstract = item.get("abstract", "")
    if abstract:
        abstract = re.sub(r'<[^>]+>', '', abstract)

    container = item.get("container-title", [])
    venue = container[0] if container else None

    return make_paper_record(
        title=title,
        authors=authors,
        year=year,
        doi=item.get("DOI"),
        abstract=abstract if abstract else None,
        source="crossref",
        source_id=item.get("DOI", ""),
        url=item.get("URL"),
        venue=venue,
        concepts=item.get("subject", []),
        citation_count=item.get("is-referenced-by-count"),
        publication_type=item.get("type"),
        extra={
            "crossref_type": item.get("type"),
            "issn": item.get("ISSN", []),
        },
    )


def _search(query: str, max_results: int = 500, exclude_ssrn: bool = True) -> list[dict]:
    """Search CrossRef for papers matching query."""
    papers = []
    offset = 0
    rows = 100

    while offset < max_results:
        rate_limiter.wait()
        try:
            params = {
                "query": query,
                "rows": min(rows, max_results - offset),
                "offset": offset,
                "select": "DOI,title,author,published-print,published-online,"
                          "abstract,URL,is-referenced-by-count,type,subject,"
                          "container-title,created,ISSN",
            }
            resp = requests.get(
                BASE_URL,
                params=params,
                headers=_headers(),
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"CrossRef request failed: {e}")
            time.sleep(5)
            continue

        items = data.get("message", {}).get("items", [])
        if not items:
            break

        for item in items:
            # Skip SSRN papers (handled by source_ssrn)
            doi = item.get("DOI", "")
            if exclude_ssrn and doi.startswith("10.2139"):
                continue
            paper = _parse_item(item)
            if paper:
                papers.append(paper)

        offset += len(items)
        total = data.get("message", {}).get("total-results", 0)
        logger.info(f"  CrossRef: {offset}/{total}")

        if offset >= total:
            break

    return papers


def harvest() -> list[dict]:
    """Search CrossRef for BU AI papers not found via SSRN."""
    logger.info("=== CrossRef harvest ===")
    all_papers = []
    seen_dois = set()

    queries = [
        '"Boston University" "artificial intelligence"',
        '"Boston University" "machine learning"',
        '"Boston University" "deep learning"',
        '"Boston University" "natural language processing"',
        '"Boston University" "computer vision"',
        '"Boston University" "neural network"',
        '"Boston University" "reinforcement learning"',
        '"Boston University" "large language model"',
        '"Boston University" "AI regulation"',
        '"Boston University" "AI governance"',
        '"Boston University" "algorithmic fairness"',
        '"Boston University" "robotics"',
        '"Boston University" "computational"',
        '"Boston University" "automated decision"',
    ]

    for query in queries:
        logger.info(f"  CrossRef query: {query}")
        papers = _search(query, max_results=500)
        new = 0
        for p in papers:
            doi = p.get("doi", "")
            if doi and doi not in seen_dois:
                seen_dois.add(doi)
                all_papers.append(p)
                new += 1
        logger.info(f"    → {new} new papers")

    logger.info(f"CrossRef total: {len(all_papers)} papers")
    save_checkpoint(all_papers, "crossref")
    return all_papers


if __name__ == "__main__":
    from utils import setup_logging
    setup_logging()
    papers = harvest()
    print(f"\nHarvested {len(papers)} papers from CrossRef")
