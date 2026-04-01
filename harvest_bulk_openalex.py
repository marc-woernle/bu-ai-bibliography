#!/usr/bin/env python3
"""
Bulk harvest ALL BU+AI papers from OpenAlex in one pass.

Instead of querying 4,000+ faculty one-by-one, this does a single query:
  filter = authorships.institutions.ror:BU_ROR, concepts.id:AI_CONCEPTS

Paginates through all results (~18K papers), deduplicates against master,
runs keyword filter, and saves candidates for classification.

Estimated: ~100 pages of 200 results = ~100 API calls. Takes ~2 minutes.

Usage:
    python harvest_bulk_openalex.py              # Full harvest
    python harvest_bulk_openalex.py --dry-run    # Count only, don't save
"""

import argparse
import hashlib
import json
import re
import time
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import requests

BU_ROR = "https://ror.org/05qwgg493"
EMAIL = "mwoernle@bu.edu"
MASTER_PATH = Path("data/sonnet_classification_bu_verified.json")
OUTPUT_PATH = Path("data/bulk_harvest_candidates.json")

# Broad AI concept filter — OpenAlex concept IDs
AI_CONCEPTS = "|".join([
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
])

AI_KEYWORDS = [
    "artificial intelligence", "machine learning", "deep learning",
    "neural network", "natural language processing", "nlp",
    "computer vision", "reinforcement learning", "transformer",
    "large language model", "llm", "gpt", "bert", "chatgpt",
    "generative ai", "ai model", "ai system",
    "convolutional neural", "recurrent neural", "attention mechanism",
    "image recognition", "object detection", "speech recognition",
    "text mining", "sentiment analysis", "named entity",
    "random forest", "gradient boosting", "support vector",
    "decision tree", "clustering", "classification algorithm",
    "feature extraction", "dimensionality reduction",
    "autonomous", "robotics", "robot learning",
    "recommendation system", "knowledge graph",
    "federated learning", "transfer learning",
    "explainable ai", "interpretable", "adversarial",
    "generative adversarial", "bayesian network",
    "data-driven", "data driven", "predictive model",
    "differential privacy", "formal verification",
    "homomorphic encryption", "online learning", "rlhf",
    "algorithm", "computational", "automated",
]


def title_fingerprint(title: str) -> str:
    t = unicodedata.normalize("NFKD", title.lower())
    t = re.sub(r"[^a-z0-9]", "", t)
    return hashlib.md5(t.encode()).hexdigest()[:16]


def normalize_doi(doi: str) -> str:
    if not doi:
        return ""
    return doi.lower().strip().replace("https://doi.org/", "").replace("http://doi.org/", "")


def keyword_match(title: str, abstract: str) -> bool:
    text = (title + " " + abstract).lower()
    return any(kw in text for kw in AI_KEYWORDS)


def reconstruct_abstract(inv_idx: dict) -> str:
    if not inv_idx:
        return ""
    words = {}
    for word, positions in inv_idx.items():
        for pos in positions:
            words[pos] = word
    return " ".join(words[k] for k in sorted(words.keys()))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    # Load master for dedup
    print("Loading master dataset...")
    with open(MASTER_PATH) as f:
        master = json.load(f)

    master_dois = set()
    master_fps = set()
    for p in master:
        doi = normalize_doi(p.get("doi", ""))
        if doi:
            master_dois.add(doi)
        fp = title_fingerprint(p.get("title", ""))
        if fp:
            master_fps.add(fp)

    print(f"Master: {len(master)} papers, {len(master_dois)} DOIs")

    # Single paginated query: all BU + AI concept works
    print("\nFetching ALL BU+AI papers from OpenAlex...")
    cursor = "*"
    total_fetched = 0
    candidates = []
    page = 0

    while cursor:
        url = (
            f"https://api.openalex.org/works"
            f"?filter=authorships.institutions.ror:{BU_ROR},"
            f"concepts.id:{AI_CONCEPTS}"
            f"&select=id,doi,title,publication_year,authorships,"
            f"abstract_inverted_index,cited_by_count,primary_location,"
            f"open_access,type"
            f"&per_page=200&cursor={cursor}&mailto={EMAIL}"
        )

        try:
            resp = requests.get(url, timeout=30)
            if resp.status_code == 429:
                print("  Rate limited, waiting 10s...")
                time.sleep(10)
                continue
            if resp.status_code != 200:
                print(f"  Error {resp.status_code}: {resp.text[:100]}")
                break
            data = resp.json()
        except Exception as e:
            print(f"  Request error: {e}")
            break

        results = data.get("results", [])
        if not results:
            break

        total_fetched += len(results)

        if not args.dry_run:
            for work in results:
                title = work.get("title", "")
                if not title:
                    continue

                doi = normalize_doi(work.get("doi", ""))
                fp = title_fingerprint(title)

                # Dedup against master
                if doi and doi in master_dois:
                    continue
                if fp and fp in master_fps:
                    continue

                # Reconstruct abstract + keyword filter
                abstract = reconstruct_abstract(work.get("abstract_inverted_index"))
                if not keyword_match(title, abstract):
                    continue

                # Parse authors
                authors = []
                bu_authors = []
                for authorship in work.get("authorships", []):
                    author_name = authorship.get("author", {}).get("display_name", "")
                    if not author_name:
                        continue
                    institutions = authorship.get("institutions", [])
                    is_bu = any(inst.get("ror") == BU_ROR for inst in institutions)
                    aff = institutions[0].get("display_name", "") if institutions else ""
                    authors.append({"name": author_name, "affiliation": aff, "is_bu": is_bu})
                    if is_bu:
                        bu_authors.append(author_name)

                if not bu_authors:
                    continue

                # Venue
                venue = ""
                primary = work.get("primary_location", {})
                if primary and primary.get("source"):
                    venue = primary["source"].get("display_name", "")

                url_paper = f"https://doi.org/{doi}" if doi else ""
                if not url_paper and primary and primary.get("landing_page_url"):
                    url_paper = primary["landing_page_url"]

                oa = work.get("open_access", {})

                candidates.append({
                    "title": title,
                    "authors": authors,
                    "year": work.get("publication_year"),
                    "doi": doi,
                    "venue": venue,
                    "citation_count": work.get("cited_by_count", 0),
                    "abstract": abstract,
                    "source": "openalex",
                    "source_id": work.get("id", ""),
                    "all_sources": ["openalex"],
                    "best_url": url_paper,
                    "is_open_access": oa.get("is_oa", False),
                    "bu_author_names": bu_authors,
                    "bu_schools": [],
                    "publication_type": work.get("type", "article"),
                })

                # Track for dedup within candidates
                if doi:
                    master_dois.add(doi)
                if fp:
                    master_fps.add(fp)

        cursor = data.get("meta", {}).get("next_cursor")
        page += 1

        if page % 20 == 0:
            print(f"  Page {page}: {total_fetched} fetched, {len(candidates)} new candidates")

        time.sleep(0.12)

    print(f"\n{'='*60}")
    print(f"BULK HARVEST RESULTS")
    print(f"{'='*60}")
    print(f"Total fetched from OpenAlex: {total_fetched}")
    print(f"New candidates (after dedup + keyword filter + BU verify): {len(candidates)}")

    if args.dry_run:
        print("\n[DRY RUN — not saving]")
        return

    if candidates:
        # Stats
        years = Counter(p.get("year") for p in candidates if p.get("year"))
        decades = defaultdict(int)
        for y, c in years.items():
            decades[(y // 10) * 10] += c
        print(f"\nBy decade:")
        for d in sorted(decades):
            print(f"  {d}s: {decades[d]}")

        no_abs = sum(1 for p in candidates if not p.get("abstract"))
        print(f"Without abstract: {no_abs}/{len(candidates)}")

        with open(OUTPUT_PATH, "w") as f:
            json.dump(candidates, f, indent=2)
        print(f"\nSaved {len(candidates)} candidates to {OUTPUT_PATH}")
    else:
        print("\nNo new candidates found.")


if __name__ == "__main__":
    main()
