"""
BU AI Bibliography Harvester -- Semantic Scholar Source
=======================================================
Supplements OpenAlex with S2's strong CS/ML paper coverage.
Uses the public API with optional API key.
"""

import logging
import os
import time
from config import SEMANTIC_SCHOLAR_RATE_LIMIT
from utils import (
    HarvestBudgetExceeded,
    RateLimiter,
    make_paper_record,
    resilient_get,
    save_checkpoint,
)

logger = logging.getLogger("bu_bib.semantic_scholar")
BASE_URL = "https://api.semanticscholar.org/graph/v1"
rate_limiter = RateLimiter(SEMANTIC_SCHOLAR_RATE_LIMIT)

FIELDS = (
    "title,authors,year,externalIds,abstract,venue,citationCount,"
    "publicationTypes,openAccessPdf,s2FieldsOfStudy,url"
)

# Core AI keywords only. The old list of 13 had massive overlap
# (e.g. "deep learning" and "neural network" return 80%+ the same papers).
SEARCH_KEYWORDS = [
    "artificial intelligence",
    "machine learning",
    "deep learning",
    "natural language processing",
    "computer vision",
    "large language model",
]


def _parse_paper(paper: dict) -> dict | None:
    """Parse S2 paper into standard format."""
    if not paper.get("title"):
        return None

    authors = []
    for a in paper.get("authors", []):
        authors.append({
            "name": a.get("name", ""),
            "s2_author_id": a.get("authorId", ""),
        })

    ext_ids = paper.get("externalIds", {}) or {}
    doi = ext_ids.get("DOI")
    arxiv_id = ext_ids.get("ArXiv")
    pubmed_id = ext_ids.get("PubMed")

    concepts = [
        f.get("category", "")
        for f in (paper.get("s2FieldsOfStudy") or [])
    ]

    pdf_info = paper.get("openAccessPdf") or {}

    return make_paper_record(
        title=paper.get("title", ""),
        authors=authors,
        year=paper.get("year"),
        doi=doi,
        abstract=paper.get("abstract"),
        source="semantic_scholar",
        source_id=paper.get("paperId", ""),
        url=paper.get("url", ""),
        pdf_url=pdf_info.get("url"),
        venue=paper.get("venue"),
        concepts=concepts,
        citation_count=paper.get("citationCount"),
        publication_type=", ".join(paper.get("publicationTypes") or []),
        extra={
            "s2_id": paper.get("paperId"),
            "arxiv_id": arxiv_id,
            "pubmed_id": pubmed_id,
        },
    )


def search_papers(
    query: str,
    limit: int = 200,
    year_range: str | None = None,
    deadline: float | None = None,
    _partial: list[dict] | None = None,
) -> list[dict]:
    """Search S2 for papers matching a query.

    Args:
        year_range: e.g. "2025-" for 2025 onwards, "2024-2026" for range
        deadline: absolute time.time() cutoff
        _partial: shared list to append results for partial recovery
    """
    papers = []
    offset = 0
    batch_size = 100

    headers = {}
    api_key = os.environ.get("S2_API_KEY", "")
    if api_key:
        headers["x-api-key"] = api_key

    while offset < limit:
        if deadline and time.time() > deadline:
            raise HarvestBudgetExceeded(f"S2 deadline exceeded at offset {offset}")

        params = {
            "query": query,
            "offset": offset,
            "limit": min(batch_size, limit - offset),
            "fields": FIELDS,
        }
        if year_range:
            params["year"] = year_range

        try:
            resp = resilient_get(
                f"{BASE_URL}/paper/search",
                params=params,
                headers=headers,
                rate_limiter=rate_limiter,
                max_retries=3,
                base_delay=10.0,
                max_delay=120.0,
                timeout=30,
                deadline=deadline,
            )
            data = resp.json()
        except HarvestBudgetExceeded:
            raise
        except Exception as e:
            logger.error(f"S2 search failed for '{query}' at offset {offset}: {e}")
            break

        results = data.get("data", [])
        if not results:
            break

        for p in results:
            parsed = _parse_paper(p)
            if parsed:
                papers.append(parsed)
                if _partial is not None:
                    _partial.append(parsed)

        offset += len(results)
        total = data.get("total", "?")
        logger.info(f"  S2 '{query}': {offset}/{total}")

        if offset >= data.get("total", 0):
            break

    return papers


def harvest(
    since_date: str | None = None,
    deadline: float | None = None,
    _partial: list[dict] | None = None,
) -> list[dict]:
    """Search S2 for AI papers by BU authors.

    Args:
        since_date: ISO date string like "2025-01-01". Filters to papers from that year onwards.
        deadline: absolute time.time() cutoff for time budgets.
        _partial: shared list for partial result recovery.
    """
    logger.info("=== Semantic Scholar harvest ===")

    # Build year range from since_date
    year_range = None
    if since_date:
        year = since_date[:4]
        year_range = f"{year}-"
        logger.info(f"  Date filter: year >= {year}")

    all_papers = []
    seen_ids = set()

    search_queries = [f'"Boston University" {kw}' for kw in SEARCH_KEYWORDS]

    for query in search_queries:
        if deadline and time.time() > deadline:
            raise HarvestBudgetExceeded(f"S2 deadline exceeded before query: {query}")

        logger.info(f"  Querying: {query}")
        papers = search_papers(
            query, limit=200, year_range=year_range,
            deadline=deadline, _partial=_partial,
        )
        new_count = 0
        for p in papers:
            s2_id = p.get("source_id", "")
            if s2_id not in seen_ids:
                seen_ids.add(s2_id)
                all_papers.append(p)
                new_count += 1
        logger.info(f"    -> {new_count} new papers")

    logger.info(f"Semantic Scholar total: {len(all_papers)} papers")
    save_checkpoint(all_papers, "semantic_scholar")
    return all_papers


if __name__ == "__main__":
    from utils import setup_logging
    setup_logging()
    papers = harvest()
    print(f"\nHarvested {len(papers)} papers from Semantic Scholar")
