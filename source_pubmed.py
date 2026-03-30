"""
BU AI Bibliography Harvester — PubMed/NCBI Source
====================================================
Catches biomedical AI work from BU Medical, SPH, etc.
Uses NCBI E-utilities (free, 3 req/sec without API key, 10 with).
"""

import requests
import xml.etree.ElementTree as ET
import logging
import time
from config import PUBMED_MESH_TERMS, PUBMED_RATE_LIMIT
from utils import RateLimiter, make_paper_record, save_checkpoint

logger = logging.getLogger("bu_bib.pubmed")
ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
rate_limiter = RateLimiter(PUBMED_RATE_LIMIT)


def _build_query() -> str:
    """Build PubMed query for BU-affiliated AI papers."""
    # Affiliation filter
    affil = '("Boston University"[Affiliation])'

    # AI subject filter: MeSH terms OR text words
    mesh_parts = [f'"{term}"[MeSH Terms]' for term in PUBMED_MESH_TERMS]
    text_parts = [
        '"artificial intelligence"[Title/Abstract]',
        '"machine learning"[Title/Abstract]',
        '"deep learning"[Title/Abstract]',
        '"neural network"[Title/Abstract]',
        '"natural language processing"[Title/Abstract]',
        '"computer vision"[Title/Abstract]',
        '"large language model"[Title/Abstract]',
        '"reinforcement learning"[Title/Abstract]',
        '"generative AI"[Title/Abstract]',
        '"predictive model"[Title/Abstract]',
        '"clinical decision support"[Title/Abstract]',
        '"precision medicine"[Title/Abstract]',
        '"medical imaging"[Title/Abstract]',
        '"drug discovery"[Title/Abstract]',
        '"bioinformatics"[Title/Abstract]',
        '"computational biology"[Title/Abstract]',
    ]

    ai_filter = " OR ".join(mesh_parts + text_parts)
    return f'{affil} AND ({ai_filter})'


def _search_pmids(query: str) -> list[str]:
    """Search PubMed and return all matching PMIDs."""
    logger.info(f"PubMed search query length: {len(query)} chars")

    # First, get total count
    while True:
        rate_limiter.wait()
        resp = requests.get(ESEARCH_URL, params={
            "db": "pubmed",
            "term": query,
            "rettype": "count",
            "retmode": "json",
        }, timeout=30)
        if resp.status_code == 429:
            logger.warning("PubMed 429 rate-limited; sleeping 10s")
            time.sleep(10)
            continue
        resp.raise_for_status()
        break
    total = int(resp.json()["esearchresult"]["count"])
    logger.info(f"PubMed found {total} results")

    # Fetch all PMIDs in batches
    pmids = []
    batch_size = 500
    for start in range(0, total, batch_size):
        while True:
            rate_limiter.wait()
            resp = requests.get(ESEARCH_URL, params={
                "db": "pubmed",
                "term": query,
                "retstart": start,
                "retmax": batch_size,
                "retmode": "json",
            }, timeout=30)
            if resp.status_code == 429:
                logger.warning("PubMed 429 rate-limited; sleeping 10s")
                time.sleep(10)
                continue
            resp.raise_for_status()
            break
        batch = resp.json()["esearchresult"].get("idlist", [])
        pmids.extend(batch)
        logger.info(f"  PMIDs: {len(pmids)}/{total}")

    return pmids


def _fetch_details(pmids: list[str]) -> list[dict]:
    """Fetch full article details for a list of PMIDs."""
    papers = []
    batch_size = 200

    for i in range(0, len(pmids), batch_size):
        batch = pmids[i:i + batch_size]
        rate_limiter.wait()

        try:
            resp = requests.post(EFETCH_URL, data={
                "db": "pubmed",
                "id": ",".join(batch),
                "rettype": "xml",
                "retmode": "xml",
            }, timeout=60)
            if resp.status_code == 429:
                logger.warning("PubMed 429 rate-limited; sleeping 10s")
                time.sleep(10)
                continue
            resp.raise_for_status()
        except requests.exceptions.RequestException as e:
            logger.error(f"PubMed fetch failed: {e}")
            time.sleep(5)
            continue

        root = ET.fromstring(resp.content)
        for article_elem in root.findall(".//PubmedArticle"):
            paper = _parse_pubmed_article(article_elem)
            if paper:
                papers.append(paper)

        logger.info(f"  Fetched details: {len(papers)} papers")

    return papers


def _parse_pubmed_article(elem) -> dict | None:
    """Parse a PubmedArticle XML element."""
    medline = elem.find(".//MedlineCitation")
    if medline is None:
        return None

    article = medline.find("Article")
    if article is None:
        return None

    # Title
    title_elem = article.find("ArticleTitle")
    title = "".join(title_elem.itertext()) if title_elem is not None else ""

    # PMID
    pmid_elem = medline.find("PMID")
    pmid = pmid_elem.text if pmid_elem is not None else ""

    # Abstract
    abstract_parts = []
    abstract_elem = article.find("Abstract")
    if abstract_elem is not None:
        for text_elem in abstract_elem.findall("AbstractText"):
            label = text_elem.get("Label", "")
            text = "".join(text_elem.itertext())
            if label:
                abstract_parts.append(f"{label}: {text}")
            else:
                abstract_parts.append(text)
    abstract = " ".join(abstract_parts) if abstract_parts else None

    # Authors
    authors = []
    author_list = article.find("AuthorList")
    if author_list is not None:
        for author_elem in author_list.findall("Author"):
            last = author_elem.findtext("LastName", "")
            first = author_elem.findtext("ForeName", "")
            name = f"{first} {last}".strip()

            # Affiliations
            affs = []
            for aff_elem in author_elem.findall(".//Affiliation"):
                if aff_elem.text:
                    affs.append(aff_elem.text)

            authors.append({
                "name": name,
                "affiliation": "; ".join(affs),
                "is_bu": any("boston university" in a.lower() for a in affs),
            })

    # Year
    year = None
    pub_date = article.find(".//PubDate")
    if pub_date is not None:
        year_elem = pub_date.find("Year")
        if year_elem is not None and year_elem.text:
            try:
                year = int(year_elem.text)
            except ValueError:
                pass

    # DOI
    doi = None
    for id_elem in elem.findall(".//ArticleId"):
        if id_elem.get("IdType") == "doi":
            doi = id_elem.text
            break

    # Journal
    journal = article.find("Journal")
    venue = None
    if journal is not None:
        venue = journal.findtext("Title", "")

    # MeSH terms
    concepts = []
    for mesh in medline.findall(".//MeshHeading/DescriptorName"):
        if mesh.text:
            concepts.append(mesh.text)

    # Keywords
    for kw in medline.findall(".//Keyword"):
        if kw.text:
            concepts.append(kw.text)

    return make_paper_record(
        title=title,
        authors=authors,
        year=year,
        doi=doi,
        abstract=abstract,
        source="pubmed",
        source_id=pmid,
        url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else None,
        venue=venue,
        concepts=concepts,
        publication_type=article.findtext(".//PublicationType", ""),
        extra={
            "pmid": pmid,
            "pmc_id": next(
                (e.text for e in elem.findall(".//ArticleId") if e.get("IdType") == "pmc"),
                None
            ),
        },
    )


def harvest() -> list[dict]:
    """Main entry point: search PubMed for BU AI papers."""
    logger.info("=== PubMed harvest ===")
    query = _build_query()
    pmids = _search_pmids(query)
    papers = _fetch_details(pmids)
    logger.info(f"PubMed total: {len(papers)} papers")
    save_checkpoint(papers, "pubmed")
    return papers


if __name__ == "__main__":
    from utils import setup_logging
    setup_logging()
    papers = harvest()
    print(f"\nHarvested {len(papers)} papers from PubMed")
