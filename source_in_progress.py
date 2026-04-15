"""
BU AI Bibliography Harvester — In-Progress Research Sources
==============================================================
Catches ongoing, not-yet-published, and in-progress research:
  1. NIH Reporter — Active and recent federal grants with AI focus
  2. NSF Awards — NSF-funded AI research at BU
  3. bioRxiv/medRxiv — Biomedical preprints (supplements arXiv for life sciences)

These sources surface work that may not have resulted in publications yet,
or whose publications haven't been indexed.
"""

import requests
import logging
import time
import re
from datetime import date
from utils import RateLimiter, make_paper_record, save_checkpoint

logger = logging.getLogger("bu_bib.in_progress")
rate_limiter = RateLimiter(2)


# ── NIH Reporter ─────────────────────────────────────────────────────────────

def harvest_nih_reporter(since_date: str | None = None) -> list[dict]:
    """
    Search NIH Reporter for active/recent BU grants related to AI.
    NIH Reporter has a proper JSON API.
    https://api.reporter.nih.gov/

    Args:
        since_date: If provided (YYYY-MM-DD), only return projects starting on or after this date.
    """
    logger.info("=== NIH Reporter harvest ===")
    all_records = []

    ai_terms = [
        "artificial intelligence",
        "machine learning",
        "deep learning",
        "natural language processing",
        "computer vision",
        "neural network",
        "reinforcement learning",
        "large language model",
        "clinical decision support",
        "precision medicine",
        "predictive model",
        "computational",
        "bioinformatics",
        "medical imaging",
        "automated diagnosis",
        "algorithmic",
        "data science",
    ]

    for term in ai_terms:
        logger.info(f"  NIH Reporter: '{term}'")
        rate_limiter.wait()

        try:
            criteria = {
                "org_names": ["BOSTON UNIVERSITY"],
                "advanced_text_search": {
                    "operator": "and",
                    "search_field": "projecttitle,terms",
                    "search_text": term,
                },
            }
            if since_date:
                # NIH Reporter expects MM/DD/YYYY
                d = date.fromisoformat(since_date)
                today = date.today()
                criteria["project_start_date"] = {
                    "from_date": d.strftime("%m/%d/%Y"),
                    "to_date": today.strftime("%m/%d/%Y"),
                }
            resp = requests.post(
                "https://api.reporter.nih.gov/v2/projects/search",
                json={
                    "criteria": criteria,
                    "offset": 0,
                    "limit": 500,
                    "sort_field": "project_start_date",
                    "sort_order": "desc",
                },
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"NIH Reporter request failed: {e}")
            continue

        results = data.get("results", [])
        for project in results:
            record = _parse_nih_project(project)
            if record:
                all_records.append(record)

        logger.info(f"    → {len(results)} projects")

    # Deduplicate by project number
    seen = set()
    unique = []
    for r in all_records:
        pid = r.get("source_id", "")
        if pid not in seen:
            seen.add(pid)
            unique.append(r)

    logger.info(f"NIH Reporter total: {len(unique)} unique projects")
    save_checkpoint(unique, "nih_reporter")
    return unique


def _parse_nih_project(project: dict) -> dict | None:
    """Parse an NIH Reporter project into our standard format."""
    title = project.get("project_title", "")
    if not title:
        return None

    # PI info
    pis = project.get("principal_investigators", [])
    authors = []
    for pi in pis:
        name = f"{pi.get('first_name', '')} {pi.get('last_name', '')}".strip()
        authors.append({
            "name": name,
            "affiliation": pi.get("org_name", ""),
            "is_bu": "boston university" in pi.get("org_name", "").lower(),
        })

    # Year from project dates
    year = None
    start_date = project.get("project_start_date")
    if start_date:
        try:
            year = int(start_date[:4])
        except (ValueError, TypeError):
            pass

    # Abstract
    abstract = project.get("abstract_text", "")
    if abstract:
        abstract = re.sub(r'\s+', ' ', abstract.strip())

    # Terms/keywords
    terms = project.get("terms", "")
    concepts = [t.strip() for t in terms.split(";") if t.strip()] if terms else []

    project_num = project.get("project_num", "")
    fiscal_year = project.get("fiscal_year", "")

    return make_paper_record(
        title=title,
        authors=authors,
        year=year,
        doi=None,
        abstract=abstract if abstract else None,
        source="nih_reporter",
        source_id=project_num,
        url=f"https://reporter.nih.gov/project-details/{project.get('application_id', '')}",
        venue=f"NIH {project.get('activity_code', '')} Grant",
        concepts=concepts[:20],
        publication_type="grant",
        extra={
            "project_number": project_num,
            "activity_code": project.get("activity_code", ""),
            "funding_mechanism": project.get("funding_mechanism", ""),
            "fiscal_year": fiscal_year,
            "project_start": start_date,
            "project_end": project.get("project_end_date"),
            "total_cost": project.get("award_amount"),
            "organization": project.get("organization", {}).get("org_name", ""),
            "department": project.get("organization", {}).get("dept_type", ""),
            "status": "active" if project.get("is_active") else "completed",
        },
    )


# ── NSF Awards ────────────────────────────────────────────────────────────────

def _is_ai_relevant(title: str, abstract: str) -> bool:
    """Check if an award's title or abstract matches any AI keyword."""
    from config import ALL_AI_KEYWORDS
    text = f"{title} {abstract}".lower()
    return any(kw.lower() in text for kw in ALL_AI_KEYWORDS)


def harvest_nsf_awards(since_date: str | None = None) -> list[dict]:
    """
    Search NSF Award Search API for BU AI-related awards.
    https://www.research.gov/awardapi-service/v1/awards.json

    Strategy: pull ALL BU awards in one pass (awardeeName does fuzzy matching,
    so we filter strictly client-side), then keep only AI-relevant ones.
    This is much faster than running 12 keyword queries that each return
    thousands of nationwide results.

    Args:
        since_date: If provided (YYYY-MM-DD), only return awards starting on or after this date.
    """
    logger.info("=== NSF Awards harvest ===")
    all_records = []
    seen_ids = set()
    offset = 1
    total_fetched = 0

    while True:
        rate_limiter.wait()
        try:
            nsf_params = {
                "awardeeName": "Boston University",
                "printFields": "id,title,abstractText,piFirstName,piLastName,"
                               "piEmail,startDate,expDate,awardeeName,fundProgramName,"
                               "awardeeCity,awardeeStateCode,fundsObligatedAmt,"
                               "primaryProgram,poName",
                "offset": offset,
                "rpp": 25,
            }
            if since_date:
                # NSF expects MM/dd/yyyy
                d = date.fromisoformat(since_date)
                nsf_params["startDateStart"] = d.strftime("%m/%d/%Y")
            resp = requests.get(
                "https://api.nsf.gov/services/v1/awards.json",
                params=nsf_params,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"NSF request failed: {e}")
            break

        awards = data.get("response", {}).get("award", [])
        if not awards:
            break

        for award in awards:
            # Strict BU filter — the API's awardeeName param does fuzzy matching
            awardee = award.get("awardeeName", "").lower()
            if "boston university" not in awardee:
                continue

            aid = award.get("id", "")
            if aid in seen_ids:
                continue
            seen_ids.add(aid)

            # AI relevance filter
            title = award.get("title", "")
            abstract = award.get("abstractText", "")
            if not _is_ai_relevant(title, abstract):
                continue

            record = _parse_nsf_award(award)
            if record:
                all_records.append(record)

        total_fetched += len(awards)
        if total_fetched % 500 == 0:
            logger.info(f"  NSF: fetched {total_fetched} BU awards, {len(all_records)} AI-relevant so far")

        offset += len(awards)
        if len(awards) < 25:
            break

    logger.info(f"NSF Awards: {total_fetched} total BU awards fetched, {len(all_records)} AI-relevant")
    save_checkpoint(all_records, "nsf_awards")
    return all_records


def _parse_nsf_award(award: dict) -> dict | None:
    """Parse an NSF award."""
    title = award.get("title", "")
    if not title:
        return None

    pi_name = f"{award.get('piFirstName', '')} {award.get('piLastName', '')}".strip()
    authors = [{
        "name": pi_name,
        "affiliation": award.get("awardeeName", ""),
        "is_bu": "boston university" in award.get("awardeeName", "").lower(),
    }]

    # Year from start date (MM/DD/YYYY format)
    year = None
    start_date = award.get("startDate", "")
    if start_date and len(start_date) >= 10:
        try:
            year = int(start_date.split("/")[-1])
        except (ValueError, IndexError):
            pass

    abstract = award.get("abstractText", "")

    return make_paper_record(
        title=title,
        authors=authors,
        year=year,
        doi=None,
        abstract=abstract if abstract else None,
        source="nsf_awards",
        source_id=award.get("id", ""),
        url=f"https://www.nsf.gov/awardsearch/showAward?AWD_ID={award.get('id', '')}",
        venue=f"NSF {award.get('fundProgramName', 'Award')}",
        concepts=[c for c in [award.get("primaryProgram", ""), award.get("fundProgramName", "")] if c],
        publication_type="grant",
        extra={
            "nsf_id": award.get("id"),
            "start_date": start_date,
            "end_date": award.get("expDate", ""),
            "funds_obligated": award.get("fundsObligatedAmt"),
            "program_officer": award.get("poName", ""),
            "program": award.get("primaryProgram", ""),
        },
    )


# ── bioRxiv / medRxiv ────────────────────────────────────────────────────────

def harvest_biorxiv_medrxiv() -> list[dict]:
    """
    Search bioRxiv and medRxiv for BU preprints.
    Uses the bioRxiv/medRxiv content detail API.
    
    Note: bioRxiv API is date-range based, not search-based.
    We use the search endpoint on the website via CrossRef instead,
    since bioRxiv DOIs are registered there.
    """
    logger.info("=== bioRxiv/medRxiv harvest (via CrossRef) ===")
    all_papers = []
    seen_dois = set()

    # bioRxiv DOI prefix: 10.1101
    ai_queries = [
        '"Boston University" artificial intelligence',
        '"Boston University" machine learning',
        '"Boston University" deep learning',
        '"Boston University" neural network',
        '"Boston University" computational biology',
        '"Boston University" bioinformatics',
        '"Boston University" medical imaging',
        '"Boston University" clinical prediction',
        '"Boston University" genomics',
        '"Boston University" proteomics',
        '"Boston University" drug discovery',
    ]

    for query in ai_queries:
        logger.info(f"  bioRxiv/medRxiv: '{query}'")
        rate_limiter.wait()

        try:
            resp = requests.get(
                "https://api.crossref.org/works",
                params={
                    "query": query,
                    "filter": "prefix:10.1101",  # bioRxiv/medRxiv DOI prefix
                    "rows": 200,
                    "select": "DOI,title,author,posted,abstract,URL,"
                              "is-referenced-by-count,type,subject,group-title",
                },
                headers={"User-Agent": "BU-AI-Bibliography/1.0"},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"bioRxiv CrossRef request failed: {e}")
            continue

        items = data.get("message", {}).get("items", [])
        new = 0
        for item in items:
            doi = item.get("DOI", "")
            if doi and doi not in seen_dois:
                seen_dois.add(doi)
                paper = _parse_biorxiv_item(item)
                if paper:
                    all_papers.append(paper)
                    new += 1

        logger.info(f"    → {new} new preprints")

    logger.info(f"bioRxiv/medRxiv total: {len(all_papers)} preprints")
    save_checkpoint(all_papers, "biorxiv_medrxiv")
    return all_papers


def _parse_biorxiv_item(item: dict) -> dict | None:
    """Parse a bioRxiv/medRxiv item from CrossRef."""
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
    posted = item.get("posted", {}).get("date-parts", [[]])
    if posted and posted[0]:
        year = posted[0][0]

    abstract = item.get("abstract", "")
    if abstract:
        abstract = re.sub(r'<[^>]+>', '', abstract)

    # Determine if bioRxiv or medRxiv
    group = item.get("group-title", "").lower()
    server = "medRxiv" if "medrxiv" in group else "bioRxiv"

    return make_paper_record(
        title=title,
        authors=authors,
        year=year,
        doi=item.get("DOI"),
        abstract=abstract if abstract else None,
        source="biorxiv_medrxiv",
        source_id=item.get("DOI", ""),
        url=item.get("URL"),
        venue=server,
        concepts=item.get("subject", []),
        citation_count=item.get("is-referenced-by-count"),
        publication_type="preprint",
        extra={
            "server": server,
            "group_title": item.get("group-title", ""),
        },
    )


# ── Main Entry Point ─────────────────────────────────────────────────────────

def harvest() -> list[dict]:
    """Harvest all in-progress/ongoing research sources."""
    logger.info("=== In-Progress Research Sources ===")
    all_papers = []

    # NIH Reporter
    try:
        nih = harvest_nih_reporter()
        all_papers.extend(nih)
    except Exception as e:
        logger.error(f"NIH Reporter failed: {e}", exc_info=True)

    # NSF Awards
    try:
        nsf = harvest_nsf_awards()
        all_papers.extend(nsf)
    except Exception as e:
        logger.error(f"NSF Awards failed: {e}", exc_info=True)

    # bioRxiv/medRxiv
    try:
        bio = harvest_biorxiv_medrxiv()
        all_papers.extend(bio)
    except Exception as e:
        logger.error(f"bioRxiv/medRxiv failed: {e}", exc_info=True)

    logger.info(f"In-progress sources total: {len(all_papers)} items")
    return all_papers


if __name__ == "__main__":
    from utils import setup_logging
    setup_logging()
    papers = harvest()
    print(f"\nHarvested {len(papers)} in-progress research items")
