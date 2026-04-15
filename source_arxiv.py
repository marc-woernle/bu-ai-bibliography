"""
BU AI Bibliography Harvester — arXiv Source
=============================================
Catches CS/ML preprints that may not yet be in journals.
Uses the arXiv API (Atom feed). Rate limit: 1 req per 3 seconds.
"""

import requests
import xml.etree.ElementTree as ET
import logging
import time
import re
from config import ARXIV_AI_CATEGORIES, ARXIV_RATE_LIMIT
from utils import RateLimiter, make_paper_record, save_checkpoint
from utils import HarvestBudgetExceeded, resilient_get

logger = logging.getLogger("bu_bib.arxiv")
BASE_URL = "http://export.arxiv.org/api/query"
rate_limiter = RateLimiter(ARXIV_RATE_LIMIT)

# arXiv Atom namespaces
NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
    "opensearch": "http://a9.com/-/spec/opensearch/1.1/",
}


def _parse_entry(entry) -> dict | None:
    """Parse an arXiv Atom entry."""
    title = entry.findtext("atom:title", "", NS)
    title = re.sub(r'\s+', ' ', title.strip())

    if not title:
        return None

    # Authors
    authors = []
    for author_elem in entry.findall("atom:author", NS):
        name = author_elem.findtext("atom:name", "", NS)
        affil_elems = author_elem.findall("arxiv:affiliation", NS)
        affiliations = [a.text for a in affil_elems if a.text]
        authors.append({
            "name": name,
            "affiliation": "; ".join(affiliations),
            "is_bu": any("boston university" in a.lower() for a in affiliations),
        })

    # Abstract
    abstract = entry.findtext("atom:summary", "", NS)
    abstract = re.sub(r'\s+', ' ', abstract.strip()) if abstract else None

    # Year from published date
    published = entry.findtext("atom:published", "", NS)
    year = int(published[:4]) if published and len(published) >= 4 else None

    # arXiv ID
    entry_id = entry.findtext("atom:id", "", NS)
    arxiv_id = entry_id.split("/abs/")[-1] if entry_id else ""

    # DOI (if available)
    doi = None
    doi_elem = entry.find("arxiv:doi", NS)
    if doi_elem is not None and doi_elem.text:
        doi = doi_elem.text

    # PDF link
    pdf_url = None
    for link in entry.findall("atom:link", NS):
        if link.get("title") == "pdf":
            pdf_url = link.get("href")
            break

    # Categories
    categories = []
    for cat in entry.findall("atom:category", NS):
        term = cat.get("term", "")
        if term:
            categories.append(term)

    # Primary category
    primary_cat = entry.find("arxiv:primary_category", NS)
    primary = primary_cat.get("term", "") if primary_cat is not None else ""

    return make_paper_record(
        title=title,
        authors=authors,
        year=year,
        doi=doi,
        abstract=abstract,
        source="arxiv",
        source_id=arxiv_id,
        url=entry_id,
        pdf_url=pdf_url,
        concepts=categories,
        publication_type="preprint",
        extra={
            "arxiv_id": arxiv_id,
            "primary_category": primary,
            "published": published,
            "updated": entry.findtext("atom:updated", "", NS),
        },
    )


def _search_arxiv(query: str, max_results: int = 2000,
                  deadline: float | None = None,
                  _partial: list[dict] | None = None) -> list[dict]:
    """Search arXiv with query string."""
    papers = []
    batch_size = 100
    start = 0

    while start < max_results:
        if deadline is not None and time.time() >= deadline:
            raise HarvestBudgetExceeded("arXiv harvest exceeded time budget")
        rate_limiter.wait()
        time.sleep(2)  # arXiv is very sensitive about rate limiting

        try:
            resp = resilient_get(BASE_URL, params={
                "search_query": query,
                "start": start,
                "max_results": min(batch_size, max_results - start),
                "sortBy": "submittedDate",
                "sortOrder": "descending",
            }, timeout=60,
               rate_limiter=rate_limiter,
               max_retries=3,
               deadline=deadline)
            resp.raise_for_status()
        except HarvestBudgetExceeded:
            raise
        except Exception as e:
            logger.error(f"arXiv request failed: {e}")
            break  # resilient_get already retried; don't loop on a dead API

        root = ET.fromstring(resp.content)

        # Check total results
        total_elem = root.find("opensearch:totalResults", NS)
        total = int(total_elem.text) if total_elem is not None else 0

        entries = root.findall("atom:entry", NS)
        if not entries:
            break

        for entry in entries:
            paper = _parse_entry(entry)
            if paper:
                papers.append(paper)
                if _partial is not None:
                    _partial.append(paper)

        start += len(entries)
        logger.info(f"  arXiv: {len(papers)} papers (batch from {start - len(entries)})")

        if start >= total:
            break

    return papers


def harvest(since_date: str | None = None,
            deadline: float | None = None,
            _partial: list[dict] | None = None) -> list[dict]:
    """
    Search arXiv for BU-affiliated AI papers.

    Strategy: Search for "Boston University" in author affiliations,
    filtered to AI-related categories.
    """
    logger.info("=== arXiv harvest ===")
    all_papers = []
    seen_ids = set()

    # Build date range clause for arXiv query
    date_clause = ""
    if since_date:
        # since_date is ISO like "2025-01-01", convert to arXiv format YYYYMMDD
        parts = since_date.replace("-", "")
        date_clause = f" AND submittedDate:[{parts}0000 TO *]"
        logger.info(f"  arXiv date filter: submittedDate >= {since_date}")

    # arXiv affiliation search: au: field searches author info
    # But arXiv's affiliation data is inconsistent, so we also do
    # full-text search for "Boston University" in relevant categories

    cat_filter = " OR ".join(f"cat:{cat}" for cat in ARXIV_AI_CATEGORIES)

    # Search 1: Affiliation-based
    query1 = f'all:"Boston University" AND ({cat_filter}){date_clause}'
    logger.info(f"  arXiv query 1: affiliation-based")
    if deadline is not None and time.time() >= deadline:
        raise HarvestBudgetExceeded("arXiv harvest exceeded time budget")
    papers1 = _search_arxiv(query1, max_results=5000, deadline=deadline,
                            _partial=_partial)
    for p in papers1:
        aid = p.get("source_id", "")
        if aid not in seen_ids:
            seen_ids.add(aid)
            all_papers.append(p)
    logger.info(f"    → {len(all_papers)} papers after query 1")

    # Search 2: Also search BU-specific research groups/labs
    bu_groups = [
        '"BU CISE"',
        '"Hariri Institute"',
        '"BU Spark"',
        '"Boston University Computer Science"',
        '"Boston University College of Engineering"',
    ]
    for group in bu_groups:
        if deadline is not None and time.time() >= deadline:
            raise HarvestBudgetExceeded("arXiv harvest exceeded time budget")
        query = f'all:{group} AND ({cat_filter}){date_clause}'
        logger.info(f"  arXiv query: {group}")
        papers = _search_arxiv(query, max_results=500, deadline=deadline,
                               _partial=_partial)
        new = 0
        for p in papers:
            aid = p.get("source_id", "")
            if aid not in seen_ids:
                seen_ids.add(aid)
                all_papers.append(p)
                new += 1
        logger.info(f"    → {new} new papers")

    logger.info(f"arXiv total: {len(all_papers)} papers")
    save_checkpoint(all_papers, "arxiv")
    return all_papers


if __name__ == "__main__":
    from utils import setup_logging
    setup_logging()
    papers = harvest()
    print(f"\nHarvested {len(papers)} papers from arXiv")
