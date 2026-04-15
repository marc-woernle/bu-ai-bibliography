"""
BU AI Bibliography Harvester — OpenBU Source
===============================================
BU's institutional repository (DSpace-based).
Catches theses, dissertations, working papers, tech reports,
and faculty publications not indexed elsewhere.

OpenBU uses DSpace 7 REST API.
"""

import requests
import logging
import time
from utils import RateLimiter, make_paper_record, save_checkpoint

logger = logging.getLogger("bu_bib.openbu")
BASE_URL = "https://open.bu.edu/server/api"
rate_limiter = RateLimiter(2)  # Be polite to BU's servers


def _search_openbu(query: str, page: int = 0, size: int = 100) -> dict:
    """Search OpenBU using DSpace discovery API."""
    while True:
        rate_limiter.wait()
        try:
            resp = requests.get(
                f"{BASE_URL}/discover/search/objects",
                params={
                    "query": query,
                    "dsoType": "item",
                    "page": page,
                    "size": size,
                },
                headers={"Accept": "application/json"},
                timeout=30,
            )
            if resp.status_code == 429:
                logger.warning("OpenBU 429 rate-limited; sleeping 10s")
                time.sleep(10)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"OpenBU request failed: {e}")
            return {}


def _parse_search_result(result: dict) -> dict | None:
    """Parse an OpenBU search result into standard format."""
    # Extract from indexable object (result.type is "discover", not "item")
    indexable = result.get("_embedded", {}).get("indexableObject", {})
    if not indexable:
        return None

    metadata_list = indexable.get("metadata", {})

    # Helper to get metadata value
    def get_meta(key):
        vals = metadata_list.get(key, [])
        if vals and isinstance(vals, list):
            return vals[0].get("value", "")
        return ""

    def get_meta_all(key):
        vals = metadata_list.get(key, [])
        if vals and isinstance(vals, list):
            return [v.get("value", "") for v in vals]
        return []

    title = get_meta("dc.title")
    if not title:
        return None

    # Authors
    author_names = get_meta_all("dc.contributor.author")
    authors = [{"name": name, "affiliation": "Boston University", "is_bu": True}
               for name in author_names]

    # Year
    year = None
    date_str = get_meta("dc.date.issued")
    if date_str and len(date_str) >= 4:
        try:
            year = int(date_str[:4])
        except ValueError:
            pass

    # Abstract
    abstract = get_meta("dc.description.abstract")

    # DOI
    doi = get_meta("dc.identifier.doi")
    if not doi:
        # Check for DOI in dc.identifier.uri
        uri = get_meta("dc.identifier.uri")
        if uri and "doi.org" in uri:
            doi = uri.split("doi.org/")[-1]

    # Subjects/keywords
    subjects = get_meta_all("dc.subject")

    # Type
    pub_type = get_meta("dc.type")

    # UUID for URL construction
    uuid = indexable.get("uuid", "")
    handle = get_meta("dc.identifier.uri")
    url = handle if handle else f"https://open.bu.edu/items/{uuid}"

    return make_paper_record(
        title=title,
        authors=authors,
        year=year,
        doi=doi,
        abstract=abstract if abstract else None,
        source="openbu",
        source_id=uuid,
        url=url,
        venue=get_meta("dc.publisher") or "OpenBU",
        concepts=subjects,
        publication_type=pub_type,
        extra={
            "openbu_uuid": uuid,
            "department": get_meta("dc.contributor.department"),
            "degree": get_meta("dc.description.degree"),
            "advisor": get_meta("dc.contributor.advisor"),
            "handle": handle,
        },
    )


def harvest(since_year: int | None = None) -> list[dict]:
    """Search OpenBU for AI-related items."""
    logger.info("=== OpenBU harvest ===")
    if since_year:
        logger.info(f"  Filtering to papers from {since_year} onward")
    all_papers = []
    seen_uuids = set()
    filtered_by_year = 0

    queries = [
        "artificial intelligence",
        "machine learning",
        "deep learning",
        "neural network",
        "natural language processing",
        "computer vision",
        "reinforcement learning",
        "large language model",
        "algorithmic",
        "computational",
        "AI ethics",
        "AI policy",
        "AI regulation",
        "robotics",
        "data science",
        "automated",
        "predictive model",
        "classification algorithm",
        "intelligent system",
        "chatbot",
        "generative AI",
        "bioinformatics",
    ]

    for query in queries:
        logger.info(f"  OpenBU query: '{query}'")
        page = 0
        query_papers = 0

        while True:
            data = _search_openbu(query, page=page, size=100)
            
            embedded = data.get("_embedded", {})
            results = embedded.get("searchResult", {}).get("_embedded", {}).get("objects", [])

            if not results:
                break

            for result in results:
                paper = _parse_search_result(result)
                if paper and paper.get("source_id") not in seen_uuids:
                    if since_year and paper.get("year") and paper["year"] < since_year:
                        filtered_by_year += 1
                        continue
                    seen_uuids.add(paper["source_id"])
                    all_papers.append(paper)
                    query_papers += 1

            # Check pagination
            page_info = data.get("_embedded", {}).get("searchResult", {}).get("page", {})
            total_pages = page_info.get("totalPages", 1)
            if page + 1 >= total_pages:
                break
            page += 1

        logger.info(f"    → '{query}': {query_papers} new items")

    if filtered_by_year:
        logger.info(f"OpenBU: {filtered_by_year} items filtered (year < {since_year})")
    logger.info(f"OpenBU total: {len(all_papers)} items")
    save_checkpoint(all_papers, "openbu")
    return all_papers


if __name__ == "__main__":
    from utils import setup_logging
    setup_logging()
    papers = harvest()
    print(f"\nHarvested {len(papers)} items from OpenBU")
