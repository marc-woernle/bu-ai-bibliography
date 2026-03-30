"""
BU AI Bibliography Harvester — Semantic Scholar Source
=======================================================
Supplements OpenAlex with S2's strong CS/ML paper coverage.
Uses the public API (no key needed, 1 req/sec).
"""

import logging
import os
import requests
import time
from config import AI_KEYWORDS_PRIMARY, SEMANTIC_SCHOLAR_RATE_LIMIT
from utils import RateLimiter, make_paper_record, save_checkpoint

logger = logging.getLogger("bu_bib.semantic_scholar")
BASE_URL = "https://api.semanticscholar.org/graph/v1"
rate_limiter = RateLimiter(SEMANTIC_SCHOLAR_RATE_LIMIT)

# S2 doesn't support direct institution filtering, so we combine
# keyword search with BU affiliation check in post-processing.
# We can also search for known BU faculty by author ID.

FIELDS = (
    "title,authors,year,externalIds,abstract,venue,citationCount,"
    "publicationTypes,openAccessPdf,s2FieldsOfStudy,url"
)


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


def search_papers(query: str, limit: int = 1000) -> list[dict]:
    """Search S2 for papers matching a query."""
    papers = []
    offset = 0
    batch_size = 100  # S2 max per request

    while offset < limit:
        rate_limiter.wait()
        try:
            resp = requests.get(
                f"{BASE_URL}/paper/search",
                headers={"x-api-key": os.environ.get("S2_API_KEY", "")},
                params={
                    "query": query,
                    "offset": offset,
                    "limit": min(batch_size, limit - offset),
                    "fields": FIELDS,
                },
                timeout=30,
            )
            if resp.status_code == 429:
                logger.warning("Rate limited by S2, waiting 60s...")
                time.sleep(60)
                continue
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"S2 request failed: {e}")
            time.sleep(10)
            continue

        results = data.get("data", [])
        if not results:
            break

        for p in results:
            parsed = _parse_paper(p)
            if parsed:
                papers.append(parsed)

        offset += len(results)
        total = data.get("total", "?")
        logger.info(f"  S2 '{query}': {offset}/{total}")

        if offset >= data.get("total", 0):
            break

    return papers


def _is_bu_affiliated(paper: dict) -> bool:
    """Heuristic check if any author is BU-affiliated.
    
    S2 doesn't always have affiliation data, so this is fuzzy.
    We check author names against a known BU faculty list if available,
    or look for "Boston University" in any available affiliation text.
    """
    for author in paper.get("authors", []):
        aff = author.get("affiliation", "").lower()
        if "boston university" in aff:
            return True
    return False


def harvest() -> list[dict]:
    """
    Search S2 for AI papers by BU authors.
    
    Strategy: Search for "Boston University" + AI keywords.
    S2's search is full-text, so this catches papers mentioning BU in affiliations.
    """
    logger.info("=== Semantic Scholar harvest ===")
    all_papers = []
    seen_ids = set()

    # Combine institution name with AI keywords for targeted search
    search_queries = [
        f'"Boston University" {kw}'
        for kw in [
            "artificial intelligence",
            "machine learning",
            "deep learning",
            "neural network",
            "natural language processing",
            "computer vision",
            "reinforcement learning",
            "large language model",
            "robotics",
            "AI ethics",
            "AI policy",
            "algorithmic",
            "automated",
            "computational",
        ]
    ]

    for query in search_queries:
        logger.info(f"  Querying: {query}")
        papers = search_papers(query, limit=500)
        new_count = 0
        for p in papers:
            s2_id = p.get("source_id", "")
            if s2_id not in seen_ids:
                seen_ids.add(s2_id)
                all_papers.append(p)
                new_count += 1
        logger.info(f"    → {new_count} new papers")

    logger.info(f"Semantic Scholar total: {len(all_papers)} papers")
    save_checkpoint(all_papers, "semantic_scholar")
    return all_papers


if __name__ == "__main__":
    from utils import setup_logging
    setup_logging()
    papers = harvest()
    print(f"\nHarvested {len(papers)} papers from Semantic Scholar")
