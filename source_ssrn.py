"""
BU AI Bibliography Harvester — SSRN Source
============================================
Critical for BU Law faculty papers on AI law, policy, regulation.
SSRN doesn't have a public API, so we use their search pages
and CrossRef for SSRN-indexed papers.
"""

import requests
import logging
import time
import re
from utils import RateLimiter, make_paper_record, save_checkpoint

logger = logging.getLogger("bu_bib.ssrn")
rate_limiter = RateLimiter(0.5)  # Very conservative for SSRN


def _search_crossref_for_ssrn(query: str, rows: int = 100) -> list[dict]:
    """
    Search CrossRef for SSRN working papers matching a query.
    CrossRef indexes many SSRN papers and has a proper API.
    """
    papers = []
    offset = 0

    while offset < 1000:  # safety cap
        rate_limiter.wait()
        try:
            resp = requests.get(
                "https://api.crossref.org/works",
                params={
                    "query": query,
                    "filter": "prefix:10.2139",  # SSRN DOI prefix
                    "rows": rows,
                    "offset": offset,
                    "select": "DOI,title,author,published-print,published-online,"
                              "abstract,URL,is-referenced-by-count,type,subject",
                },
                headers={
                    "User-Agent": "BU-AI-Bibliography/1.0 (mailto:research@bu.edu)"
                },
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"CrossRef/SSRN request failed: {e}")
            time.sleep(10)
            continue

        items = data.get("message", {}).get("items", [])
        if not items:
            break

        for item in items:
            paper = _parse_crossref_item(item)
            if paper:
                papers.append(paper)

        offset += len(items)
        total = data.get("message", {}).get("total-results", 0)
        logger.info(f"  SSRN/CrossRef: {offset}/{total}")

        if offset >= total:
            break

    return papers


def _parse_crossref_item(item: dict) -> dict | None:
    """Parse CrossRef work item."""
    titles = item.get("title", [])
    title = titles[0] if titles else ""
    if not title:
        return None

    # Authors
    authors = []
    for a in item.get("author", []):
        name = f"{a.get('given', '')} {a.get('family', '')}".strip()
        affs = [aff.get("name", "") for aff in a.get("affiliation", [])]
        authors.append({
            "name": name,
            "affiliation": "; ".join(affs),
            "is_bu": any("boston university" in af.lower() for af in affs),
        })

    # Year
    year = None
    for date_field in ["published-print", "published-online", "created"]:
        date_parts = item.get(date_field, {}).get("date-parts", [[]])
        if date_parts and date_parts[0] and date_parts[0][0]:
            year = date_parts[0][0]
            break

    # Abstract (CrossRef sometimes has it, sometimes not)
    abstract = item.get("abstract", "")
    if abstract:
        # Clean JATS XML tags from abstract
        abstract = re.sub(r'<[^>]+>', '', abstract)

    return make_paper_record(
        title=title,
        authors=authors,
        year=year,
        doi=item.get("DOI"),
        abstract=abstract if abstract else None,
        source="ssrn",
        source_id=item.get("DOI", ""),
        url=item.get("URL"),
        venue="SSRN Electronic Journal",
        concepts=item.get("subject", []),
        citation_count=item.get("is-referenced-by-count"),
        publication_type=item.get("type"),
        extra={"crossref_type": item.get("type")},
    )


def harvest() -> list[dict]:
    """
    Harvest BU SSRN papers on AI topics.
    Uses CrossRef's index of SSRN papers (DOI prefix 10.2139).
    """
    logger.info("=== SSRN harvest (via CrossRef) ===")
    all_papers = []
    seen_dois = set()

    queries = [
        '"Boston University" artificial intelligence',
        '"Boston University" machine learning',
        '"Boston University" AI regulation',
        '"Boston University" AI policy',
        '"Boston University" AI governance',
        '"Boston University" algorithmic',
        '"Boston University" automated decision',
        '"Boston University" legal technology',
        '"Boston University" computational law',
        '"Boston University" AI ethics',
        '"Boston University" AI bias',
        '"Boston University" large language model',
        '"Boston University" generative AI',
        '"Boston University" robotics',
        '"Boston University" autonomous',
        '"Boston University" data privacy',
        '"Boston University" surveillance',
        '"Boston University" facial recognition',
        '"Boston University" platform regulation',
        '"Boston University" content moderation',
        '"Boston University" intellectual property AI',
    ]

    for query in queries:
        logger.info(f"  SSRN query: {query}")
        papers = _search_crossref_for_ssrn(query)
        new = 0
        for p in papers:
            doi = p.get("doi", "")
            if doi and doi not in seen_dois:
                seen_dois.add(doi)
                all_papers.append(p)
                new += 1
        logger.info(f"    → {new} new papers")

    logger.info(f"SSRN total: {len(all_papers)} papers")
    save_checkpoint(all_papers, "ssrn")
    return all_papers


if __name__ == "__main__":
    from utils import setup_logging
    setup_logging()
    papers = harvest()
    print(f"\nHarvested {len(papers)} papers from SSRN")
