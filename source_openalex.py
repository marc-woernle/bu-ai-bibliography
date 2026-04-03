"""
BU AI Bibliography Harvester — OpenAlex Source
================================================
Primary harvester. OpenAlex covers 250M+ works with good affiliation data.
Strategy:
  1. Concept-based: Pull BU papers tagged with any AI-related concept
  2. Keyword-based: Search BU papers for AI terms in title/abstract
  3. Merge both sweeps to maximize recall
"""

import requests
import logging
import time
from config import (
    BU_ROR_ID, BU_OPENALEX_INSTITUTION_ID,
    OPENALEX_AI_CONCEPT_IDS, ALL_AI_KEYWORDS, AI_KEYWORDS_PRIMARY,
    OPENALEX_RATE_LIMIT, CONTACT_EMAIL,
)
from utils import RateLimiter, make_paper_record, save_checkpoint

logger = logging.getLogger("bu_bib.openalex")
BASE_URL = "https://api.openalex.org"


rate_limiter = RateLimiter(OPENALEX_RATE_LIMIT)

def _safe_venue(work):
    loc = work.get("primary_location")
    if loc:
        source = loc.get("source")
        if source:
            return source.get("display_name")
    return None


def _headers():
    """OpenAlex polite pool: include mailto for faster access."""
    return {"User-Agent": f"BU-AI-Bibliography/1.0 (mailto:{CONTACT_EMAIL})"}


def _parse_work(work: dict) -> dict:
    """Parse an OpenAlex work object into our standard format."""
    # Extract authors with affiliations
    authors = []
    bu_affiliated = False
    for authorship in work.get("authorships", []):
        author_info = authorship.get("author", {})
        name = author_info.get("display_name", "")
        orcid = author_info.get("orcid", "")

        # Check institutions for BU affiliation
        affiliations = []
        for inst in authorship.get("institutions", []):
            affiliations.append(inst.get("display_name", ""))
            if inst.get("ror") == BU_ROR_ID:
                bu_affiliated = True

        # Extract OpenAlex author ID (e.g. "https://openalex.org/A5003349673")
        oa_author_id = author_info.get("id", "")

        authors.append({
            "name": name,
            "orcid": orcid,
            "openalex_id": oa_author_id if oa_author_id else None,
            "affiliation": "; ".join(a for a in affiliations if a),
            "is_bu": any(
                inst.get("ror") == BU_ROR_ID
                for inst in authorship.get("institutions", [])
            ),
        })

    # Extract concepts with scores
    concepts = []
    for concept in work.get("concepts", []):
        cn = concept.get("display_name"); concepts.append(cn) if cn else None

    # Also get topics (newer OpenAlex taxonomy)
    for topic in work.get("topics", []):
        tn = topic.get("display_name"); concepts.append(tn) if tn else None

    # Get DOI
    doi = work.get("doi", "")
    if doi and doi.startswith("https://doi.org/"):
        doi = doi[len("https://doi.org/"):]

    # Get best open access URL
    oa = work.get("open_access", {})
    pdf_url = oa.get("oa_url")

    # Get abstract from inverted index
    abstract = None
    abstract_inv = work.get("abstract_inverted_index")
    if abstract_inv:
        abstract = _reconstruct_abstract(abstract_inv)

    return make_paper_record(
        title=work.get("title", ""),
        authors=authors,
        year=work.get("publication_year"),
        doi=doi,
        abstract=abstract,
        source="openalex",
        source_id=work.get("id", ""),
        url=work.get("id", ""),  # OpenAlex URL
        pdf_url=pdf_url,
        venue=_safe_venue(work),
        concepts=concepts,
        citation_count=work.get("cited_by_count"),
        publication_type=work.get("type"),
        extra={
            "openalex_id": work.get("id"),
            "is_oa": oa.get("is_oa", False),
            "sustainable_development_goals": [
                sdg.get("display_name") for sdg in work.get("sustainable_development_goals", [])
            ],
        },
    )


def _reconstruct_abstract(inverted_index: dict) -> str:
    """Reconstruct abstract text from OpenAlex inverted index format."""
    if not inverted_index:
        return ""
    # Build word→positions map, then sort by position
    word_positions = []
    for word, positions in inverted_index.items():
        for pos in positions:
            word_positions.append((pos, word))
    word_positions.sort(key=lambda x: x[0])
    return " ".join(word for _, word in word_positions)


def harvest_by_concepts(per_page: int = 200) -> list[dict]:
    """
    Sweep 1: Pull all BU papers tagged with AI-related concepts.
    Uses concept.id filter combined with institution filter.
    """
    logger.info("=== OpenAlex Sweep 1: Concept-based harvest ===")
    all_papers = []

    # Build concept filter: OR across all AI concept IDs
    concept_filter = "|".join(OPENALEX_AI_CONCEPT_IDS)
    
    # We'll do this in a single query with concept OR filter
    params = {
        "filter": f"authorships.institutions.ror:{BU_ROR_ID},concepts.id:{concept_filter}",
        "per_page": per_page,
        "cursor": "*",
        "select": "id,title,doi,publication_year,authorships,concepts,topics,"
                  "abstract_inverted_index,open_access,cited_by_count,type,"
                  "primary_location,sustainable_development_goals",
    }
    if CONTACT_EMAIL and CONTACT_EMAIL != "CHANGEME@bu.edu":
        params["mailto"] = CONTACT_EMAIL

    page_count = 0
    total_fetched = 0

    while True:
        rate_limiter.wait()
        try:
            resp = requests.get(
                f"{BASE_URL}/works",
                params=params,
                headers=_headers(),
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Request failed: {e}")
            time.sleep(5)
            continue

        results = data.get("results", [])
        if not results:
            break

        for work in results:
            paper = _parse_work(work)
            if paper["title"]:  # skip empty titles
                all_papers.append(paper)

        total_fetched += len(results)
        page_count += 1
        total_count = data.get("meta", {}).get("count", "?")
        logger.info(f"  Page {page_count}: fetched {len(results)} "
                     f"(total so far: {total_fetched} / {total_count})")

        # Get next cursor
        next_cursor = data.get("meta", {}).get("next_cursor")
        if not next_cursor:
            break
        params["cursor"] = next_cursor

        # Checkpoint every 50 pages
        if page_count % 50 == 0:
            save_checkpoint(all_papers, "openalex_concepts_partial")

    logger.info(f"Concept sweep complete: {len(all_papers)} papers")
    save_checkpoint(all_papers, "openalex_concepts")
    return all_papers


def harvest_by_keywords(per_page: int = 200) -> list[dict]:
    """
    Sweep 2: Full-text search for AI keywords in BU papers.
    Catches papers that use AI methods but aren't concept-tagged as AI.
    """
    logger.info("=== OpenAlex Sweep 2: Keyword-based harvest ===")
    all_papers = []
    seen_ids = set()

    for keyword in AI_KEYWORDS_PRIMARY:
        logger.info(f"  Searching: '{keyword}'")
        params = {
            "filter": f"authorships.institutions.ror:{BU_ROR_ID}",
            "search": keyword,
            "per_page": per_page,
            "cursor": "*",
            "select": "id,title,doi,publication_year,authorships,concepts,topics,"
                      "abstract_inverted_index,open_access,cited_by_count,type,"
                      "primary_location,sustainable_development_goals",
        }
        if CONTACT_EMAIL and CONTACT_EMAIL != "CHANGEME@bu.edu":
            params["mailto"] = CONTACT_EMAIL

        keyword_count = 0
        while True:
            rate_limiter.wait()
            try:
                resp = requests.get(
                    f"{BASE_URL}/works",
                    params=params,
                    headers=_headers(),
                    timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()
            except requests.exceptions.RequestException as e:
                logger.error(f"Request failed for '{keyword}': {e}")
                time.sleep(5)
                continue

            results = data.get("results", [])
            if not results:
                break

            for work in results:
                oa_id = work.get("id", "")
                if oa_id not in seen_ids:
                    seen_ids.add(oa_id)
                    paper = _parse_work(work)
                    if paper["title"]:
                        paper["extra"]["matched_keyword"] = keyword
                        all_papers.append(paper)
                        keyword_count += 1

            next_cursor = data.get("meta", {}).get("next_cursor")
            if not next_cursor:
                break
            params["cursor"] = next_cursor

        logger.info(f"    → '{keyword}': {keyword_count} new papers")

    logger.info(f"Keyword sweep complete: {len(all_papers)} papers")
    save_checkpoint(all_papers, "openalex_keywords")
    return all_papers


def harvest_all_bu_works(per_page: int = 200) -> list[dict]:
    """
    Sweep 3 (OPTIONAL — use for maximum recall): Pull ALL BU works,
    then filter downstream with Claude. This is comprehensive but large.
    Only run this if you want to catch absolutely everything.
    """
    logger.info("=== OpenAlex Sweep 3: All BU works (comprehensive) ===")
    all_papers = []

    params = {
        "filter": f"authorships.institutions.ror:{BU_ROR_ID}",
        "per_page": per_page,
        "cursor": "*",
        "select": "id,title,doi,publication_year,authorships,concepts,topics,"
                  "abstract_inverted_index,open_access,cited_by_count,type,"
                  "primary_location,sustainable_development_goals",
    }
    if CONTACT_EMAIL and CONTACT_EMAIL != "CHANGEME@bu.edu":
        params["mailto"] = CONTACT_EMAIL

    page_count = 0
    total_fetched = 0

    while True:
        rate_limiter.wait()
        try:
            resp = requests.get(
                f"{BASE_URL}/works",
                params=params,
                headers=_headers(),
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Request failed: {e}")
            time.sleep(5)
            continue

        results = data.get("results", [])
        if not results:
            break

        for work in results:
            paper = _parse_work(work)
            if paper["title"]:
                all_papers.append(paper)

        total_fetched += len(results)
        page_count += 1
        total_count = data.get("meta", {}).get("count", "?")
        logger.info(f"  Page {page_count}: {total_fetched} / {total_count}")

        next_cursor = data.get("meta", {}).get("next_cursor")
        if not next_cursor:
            break
        params["cursor"] = next_cursor

        # Checkpoint every 100 pages
        if page_count % 100 == 0:
            save_checkpoint(all_papers, "openalex_all_partial")

    logger.info(f"Full BU sweep complete: {len(all_papers)} papers")
    save_checkpoint(all_papers, "openalex_all")
    return all_papers


def get_bu_institution_info() -> dict:
    """Fetch BU's OpenAlex institution profile (useful for verification)."""
    rate_limiter.wait()
    resp = requests.get(
        f"{BASE_URL}/institutions/{BU_OPENALEX_INSTITUTION_ID}",
        headers=_headers(),
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def harvest() -> list[dict]:
    """Main entry point: run concept + keyword sweeps and merge."""
    concept_papers = harvest_by_concepts()
    keyword_papers = harvest_by_keywords()

    # Merge (keyword sweep may have papers already in concept sweep)
    all_papers = concept_papers + keyword_papers
    logger.info(f"OpenAlex total (before dedup): {len(all_papers)} papers")
    return all_papers


if __name__ == "__main__":
    from utils import setup_logging
    setup_logging()
    papers = harvest()
    print(f"\nHarvested {len(papers)} papers from OpenAlex")
