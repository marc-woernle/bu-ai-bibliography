#!/usr/bin/env python3
"""
One-time historical backfill: fetch ALL works for faculty with OpenAlex IDs,
filter for AI relevance, dedup against master dataset.

This closes the "old papers we missed" gap — the incremental pipeline only
looks back 4 weeks / 12 months, so papers from 2016 etc. were never caught.

Strategy:
  1. Load faculty roster (3,572 with OpenAlex IDs)
  2. Batch-query OpenAlex: for each author ID, get works tagged with AI concepts
  3. Dedup against master by DOI and title fingerprint
  4. Run keyword pre-filter
  5. Save candidates for Sonnet classification (separate step to control cost)
"""

import hashlib
import json
import re
import time
import unicodedata
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import requests

BU_ROR = "https://ror.org/05qwgg493"
EMAIL = "mwoernle@bu.edu"
ROSTER_PATH = Path("data/bu_faculty_roster_verified.json")
MASTER_PATH = Path("data/sonnet_classification_bu_verified.json")
CANDIDATES_PATH = Path("data/faculty_harvest_candidates.json")
CACHE_DIR = Path("data/faculty_harvest_cache")

# OpenAlex concept IDs for AI-related topics
AI_CONCEPTS = [
    "C154945302",  # Artificial intelligence
    "C119857082",  # Machine learning
    "C108827166",  # Deep learning
    "C204321447",  # Natural language processing
    "C31972630",   # Computer vision
    "C2522767166", # Data mining
    "C50644808",   # Artificial neural network
    "C124101348",  # Data science
    "C80444323",   # Sentiment analysis
    "C23123220",   # Reinforcement learning
]

# AI keywords for title/abstract pre-filter (same as pipeline)
AI_KEYWORDS = [
    "artificial intelligence", "machine learning", "deep learning",
    "neural network", "natural language processing", "nlp",
    "computer vision", "reinforcement learning", "transformer",
    "large language model", "llm", "gpt", "bert", "chatgpt",
    "generative ai", "gen ai", "ai model", "ai system",
    "convolutional neural", "recurrent neural", "attention mechanism",
    "image recognition", "object detection", "speech recognition",
    "text mining", "sentiment analysis", "named entity",
    "random forest", "gradient boosting", "xgboost",
    "support vector", "decision tree", "clustering",
    "classification algorithm", "regression model",
    "feature extraction", "dimensionality reduction",
    "autonomous", "robotics", "robot learning",
    "recommendation system", "collaborative filtering",
    "knowledge graph", "ontology learning",
    "federated learning", "transfer learning",
    "explainable ai", "interpretable",
    "adversarial", "gan", "generative adversarial",
    "bayesian network", "probabilistic model",
    "data-driven", "data driven", "predictive model",
    "algorithm", "computational", "automated",
    "differential privacy", "secure computation",
    "zero-knowledge", "formal verification",
    "mechanism design", "homomorphic encryption",
    "bandit", "regret bound", "online learning", "rlhf",
]


def title_fingerprint(title: str) -> str:
    """Normalized title fingerprint for dedup."""
    t = unicodedata.normalize("NFKD", title.lower())
    t = re.sub(r"[^a-z0-9]", "", t)
    return hashlib.md5(t.encode()).hexdigest()[:16]


def normalize_doi(doi: str) -> str:
    if not doi:
        return ""
    doi = doi.lower().strip()
    doi = doi.replace("https://doi.org/", "").replace("http://doi.org/", "")
    return doi


def keyword_match(title: str, abstract: str) -> bool:
    """Check if title or abstract contains any AI keyword."""
    text = (title + " " + abstract).lower()
    return any(kw in text for kw in AI_KEYWORDS)


def parse_work(work: dict) -> dict | None:
    """Parse an OpenAlex work into our paper format."""
    title = work.get("title", "")
    if not title:
        return None

    doi = normalize_doi(work.get("doi", ""))
    year = work.get("publication_year")

    # Get abstract
    abstract = ""
    inv_idx = work.get("abstract_inverted_index")
    if inv_idx:
        words = {}
        for word, positions in inv_idx.items():
            for pos in positions:
                words[pos] = word
        abstract = " ".join(words[k] for k in sorted(words.keys()))

    # Authors
    authors = []
    bu_authors = []
    for authorship in work.get("authorships", []):
        author_name = authorship.get("author", {}).get("display_name", "")
        if not author_name:
            continue

        institutions = authorship.get("institutions", [])
        is_bu = any(inst.get("ror") == BU_ROR for inst in institutions)
        aff = ""
        if institutions:
            aff = institutions[0].get("display_name", "")

        authors.append({
            "name": author_name,
            "affiliation": aff,
            "is_bu": is_bu,
        })
        if is_bu:
            bu_authors.append(author_name)

    if not bu_authors:
        return None

    # Venue
    venue = ""
    primary = work.get("primary_location", {})
    if primary and primary.get("source"):
        venue = primary["source"].get("display_name", "")

    # Open access
    oa = work.get("open_access", {})
    is_oa = oa.get("is_oa", False)

    # Best URL
    url = ""
    if doi:
        url = f"https://doi.org/{doi}"
    elif primary and primary.get("landing_page_url"):
        url = primary["landing_page_url"]

    return {
        "title": title,
        "authors": authors,
        "year": year,
        "doi": doi,
        "venue": venue,
        "citation_count": work.get("cited_by_count", 0),
        "abstract": abstract,
        "source": "openalex",
        "source_id": work.get("id", ""),
        "all_sources": ["openalex"],
        "best_url": url,
        "is_open_access": is_oa,
        "bu_author_names": bu_authors,
        "bu_schools": [],
        "publication_type": work.get("type", "article"),
    }


def fetch_faculty_works(author_id: str, name: str) -> list[dict]:
    """Fetch all AI-concept-tagged works for a faculty member."""
    # Use cache if available
    cache_file = CACHE_DIR / f"{author_id.split('/')[-1]}.json"
    if cache_file.exists():
        with open(cache_file) as f:
            return json.load(f)

    concepts_filter = "|".join(AI_CONCEPTS)
    works = []
    cursor = "*"
    pages = 0

    while cursor:
        url = (
            f"https://api.openalex.org/works"
            f"?filter=author.id:{author_id},concepts.id:{concepts_filter}"
            f"&select=id,doi,title,publication_year,authorships,abstract_inverted_index,"
            f"cited_by_count,primary_location,open_access,type"
            f"&per_page=200&cursor={cursor}&mailto={EMAIL}"
        )

        try:
            resp = requests.get(url, timeout=30)
            if resp.status_code == 429:
                time.sleep(5)
                continue
            if resp.status_code != 200:
                break
            data = resp.json()
        except Exception:
            break

        results = data.get("results", [])
        if not results:
            break

        works.extend(results)
        cursor = data.get("meta", {}).get("next_cursor")
        pages += 1
        time.sleep(0.12)  # Polite rate

    # Cache results
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(cache_file, "w") as f:
        json.dump(works, f)

    return works


def main():
    # Load data
    with open(ROSTER_PATH) as f:
        roster = json.load(f)
    with open(MASTER_PATH) as f:
        master = json.load(f)

    # Build dedup index from master
    master_dois = set()
    master_fps = set()
    for p in master:
        doi = normalize_doi(p.get("doi", ""))
        if doi:
            master_dois.add(doi)
        fp = title_fingerprint(p.get("title", ""))
        if fp:
            master_fps.add(fp)

    print(f"Master: {len(master)} papers, {len(master_dois)} DOIs, {len(master_fps)} title fingerprints")

    # Get faculty with OpenAlex IDs
    faculty_with_ids = [r for r in roster if r.get("openalex_id")]
    print(f"Faculty with OpenAlex IDs: {len(faculty_with_ids)}")

    # Fetch works for each faculty member
    all_candidates = []
    faculty_stats = []
    total_fetched = 0
    total_new = 0
    total_ai_match = 0

    for i, fac in enumerate(faculty_with_ids):
        oa_id = fac["openalex_id"]
        name = fac["name"]

        works = fetch_faculty_works(oa_id, name)
        total_fetched += len(works)

        # Parse, dedup, filter
        new_for_faculty = 0
        for work in works:
            paper = parse_work(work)
            if not paper:
                continue

            # Dedup
            doi = normalize_doi(paper.get("doi", ""))
            fp = title_fingerprint(paper.get("title", ""))
            if doi and doi in master_dois:
                continue
            if fp and fp in master_fps:
                continue

            # Keyword filter
            if not keyword_match(paper.get("title", ""), paper.get("abstract", "")):
                continue

            total_ai_match += 1

            # Add to candidates (avoid duplicates within candidates)
            if doi:
                master_dois.add(doi)
            if fp:
                master_fps.add(fp)

            # Tag with faculty info
            paper["_harvested_via"] = name
            paper["_faculty_school"] = fac.get("school", "")
            all_candidates.append(paper)
            new_for_faculty += 1

        total_new += new_for_faculty

        if (i + 1) % 100 == 0:
            print(f"  [{i+1}/{len(faculty_with_ids)}] Fetched: {total_fetched}, New candidates: {total_new}")

    print(f"\n{'='*60}")
    print(f"HARVEST COMPLETE")
    print(f"{'='*60}")
    print(f"Faculty queried: {len(faculty_with_ids)}")
    print(f"Total works fetched: {total_fetched}")
    print(f"New AI-keyword candidates: {len(all_candidates)}")

    # Stats by school
    school_counts = Counter(p.get("_faculty_school", "") for p in all_candidates)
    print(f"\nCandidates by school:")
    for school, count in school_counts.most_common(20):
        print(f"  {school}: {count}")

    # Year distribution of candidates
    year_counts = Counter(p.get("year") for p in all_candidates if p.get("year"))
    print(f"\nCandidates by decade:")
    decades = defaultdict(int)
    for y, c in year_counts.items():
        decades[(y // 10) * 10] += c
    for d in sorted(decades.keys()):
        print(f"  {d}s: {decades[d]}")

    # Save candidates
    with open(CANDIDATES_PATH, "w") as f:
        json.dump(all_candidates, f, indent=2)
    print(f"\nSaved {len(all_candidates)} candidates to {CANDIDATES_PATH}")

    # Sanity checks
    print(f"\n{'='*60}")
    print(f"SANITY CHECKS")
    print(f"{'='*60}")

    # Check: any candidate with >50 authors? (potential CERN-style)
    big = [p for p in all_candidates if len(p.get("authors", [])) > 50]
    print(f"Candidates with >50 authors: {len(big)}")

    # Check: candidates with no abstract
    no_abs = sum(1 for p in all_candidates if not p.get("abstract"))
    print(f"Candidates without abstract: {no_abs} ({no_abs/max(len(all_candidates),1)*100:.0f}%)")

    # Sample 10 random candidates
    import random
    random.seed(42)
    print(f"\nRandom sample (10):")
    for p in random.sample(all_candidates, min(10, len(all_candidates))):
        bu = ", ".join(p.get("bu_author_names", [])[:2])
        print(f"  [{p.get('year', '?')}] {p['title'][:55]} — {bu}")


if __name__ == "__main__":
    main()
