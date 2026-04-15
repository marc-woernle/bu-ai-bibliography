"""
BU AI Bibliography Harvester — Scholarly Commons Source
========================================================
Harvests BU Law faculty publications from scholarship.law.bu.edu.
This is the primary source for legal scholarship, which is poorly
indexed in OpenAlex/CrossRef/PubMed.
"""

import logging
import re
import time

import requests
from bs4 import BeautifulSoup

from utils import make_paper_record

logger = logging.getLogger(__name__)

BASE_URL = "https://scholarship.law.bu.edu/faculty_scholarship/"


def _fetch_page(url: str) -> BeautifulSoup | None:
    """Fetch and parse a Scholarly Commons page."""
    try:
        resp = requests.get(url, timeout=20)
        if resp.status_code != 200:
            return None
        return BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        logger.error(f"Fetch error: {e}")
        return None


def _parse_paper_page(url: str) -> dict | None:
    """Fetch a single paper's metadata from its Scholarly Commons page."""
    soup = _fetch_page(url)
    if not soup:
        return None

    title_el = soup.select_one('meta[name="bepress_citation_title"]')
    if not title_el:
        return None

    title = title_el.get("content", "")
    author_metas = soup.select('meta[name="bepress_citation_author"]')
    inst_metas = soup.select('meta[name="bepress_citation_author_institution"]')
    date_el = soup.select_one('meta[name="bepress_citation_date"]')
    abstract_el = soup.select_one('#abstract, meta[name="description"]')
    venue_el = soup.select_one('meta[name="bepress_citation_journal_title"]')
    doi_el = soup.select_one('meta[name="bepress_citation_doi"]')

    # Build authors
    authors = []
    for j, am in enumerate(author_metas):
        name_raw = am.get("content", "")
        parts = name_raw.split(",", 1)
        name = f"{parts[1].strip()} {parts[0].strip()}" if len(parts) == 2 else name_raw
        inst = inst_metas[j].get("content", "") if j < len(inst_metas) else ""
        is_bu = "boston university" in inst.lower()
        authors.append({"name": name, "affiliation": inst, "is_bu": is_bu})

    # Abstract
    abstract = ""
    if abstract_el:
        if abstract_el.name == "meta":
            abstract = abstract_el.get("content", "")
        else:
            abstract = abstract_el.get_text(strip=True)
            if abstract.startswith("Abstract"):
                abstract = abstract[8:].strip()

    # Year
    year = None
    if date_el:
        ym = re.search(r"(\d{4})", date_el.get("content", ""))
        if ym:
            year = int(ym.group(1))

    doi = doi_el.get("content", "") if doi_el else ""
    venue = venue_el.get("content", "") if venue_el else ""

    if not any(a.get("is_bu") for a in authors):
        return None

    return make_paper_record(
        title=title,
        authors=authors,
        year=year,
        doi=doi if doi else None,
        abstract=abstract,
        source="scholarly_commons",
        source_id=url,
        url=url,
        venue=venue if venue else None,
        publication_type="article",
    )


def harvest(max_pages: int = 200, since_year: int | None = None) -> list[dict]:
    """Harvest all faculty scholarship from BU Law Scholarly Commons."""
    logger.info("=== Scholarly Commons harvest ===")
    if since_year:
        logger.info(f"  Filtering to papers from {since_year} onward")
    papers = []
    seen_titles = set()
    filtered_by_year = 0
    consecutive_old_pages = 0

    for page in range(1, max_pages + 1):
        url = BASE_URL if page == 1 else f"{BASE_URL}index.{page}.html"
        time.sleep(0.3)

        soup = _fetch_page(url)
        if not soup:
            break

        articles = soup.select('p[class*="article"]')
        if not articles:
            break

        new_count = 0
        page_had_recent = False
        for art in articles:
            link = art.select_one("a")
            if not link:
                continue

            title = link.get_text(strip=True)
            key = title.lower().strip()[:50]
            if key in seen_titles:
                continue
            seen_titles.add(key)
            new_count += 1

            href = link.get("href", "")
            if not href.startswith("http"):
                href = "https://scholarship.law.bu.edu" + href

            # Fetch full metadata
            time.sleep(0.3)
            paper = _parse_paper_page(href)
            if paper:
                if since_year and paper.get("year") and paper["year"] < since_year:
                    filtered_by_year += 1
                    continue
                page_had_recent = True
                papers.append(paper)

        if new_count == 0:
            break

        # Early termination: if 3 consecutive pages had no recent papers, stop
        if since_year:
            if not page_had_recent:
                consecutive_old_pages += 1
                if consecutive_old_pages >= 3:
                    logger.info(f"  3 consecutive pages with no papers >= {since_year}, stopping early at page {page}")
                    break
            else:
                consecutive_old_pages = 0

        if page % 20 == 0:
            logger.info(f"  Scholarly Commons page {page}: {len(papers)} papers")

    if filtered_by_year:
        logger.info(f"Scholarly Commons: {filtered_by_year} papers filtered (year < {since_year})")
    logger.info(f"Scholarly Commons: {len(papers)} papers harvested")
    return papers
