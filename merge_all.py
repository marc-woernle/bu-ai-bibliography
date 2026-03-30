#!/usr/bin/env python3
"""
Merge all checkpoint JSON files into a single deduplicated bibliography.

Loads every source checkpoint from data/, deduplicates across all sources,
classifies by BU school/department, and saves the final merged output.
"""

import glob
import time
import logging
from utils import Deduplicator, load_checkpoint, save_final, setup_logging
from school_mapper import classify_all

logger = logging.getLogger("bu_bib")

# Map source prefixes to the latest checkpoint file (glob pattern).
# Order doesn't matter for correctness, but mirrors harvest.py for readability.
SOURCE_PREFIXES = [
    "openalex_concepts",
    "openalex_keywords",
    "semantic_scholar",
    "pubmed",
    "arxiv",
    "ssrn",
    "crossref",
    "openbu",
    "nih_reporter",
    "nsf_awards",
    "biorxiv_medrxiv",
]


def find_latest_checkpoint(prefix: str, data_dir: str = "data") -> str | None:
    """Return the most recent checkpoint file for a source prefix."""
    # Match e.g. data/pubmed_20260327_*.json but exclude partials
    pattern = f"{data_dir}/{prefix}_2026*.json"
    matches = sorted(glob.glob(pattern))
    return matches[-1] if matches else None


def main():
    setup_logging()
    start = time.time()
    dedup = Deduplicator()
    stats = {}

    for prefix in SOURCE_PREFIXES:
        path = find_latest_checkpoint(prefix)
        if not path:
            logger.warning(f"No checkpoint found for '{prefix}', skipping")
            stats[prefix] = {"raw": 0, "new": 0, "status": "missing"}
            continue

        logger.info(f"Loading {path}")
        records = load_checkpoint(path)
        new = sum(1 for r in records if dedup.add(r))
        dupes = len(records) - new
        stats[prefix] = {"raw": len(records), "new": new, "dupes": dupes, "status": "ok"}
        logger.info(f"  {prefix}: {len(records)} raw → {new} new, {dupes} duplicates")

    all_records = dedup.get_all()
    logger.info(f"\nTotal unique records before classification: {len(all_records)}")

    # Classify by BU school/department
    logger.info("Classifying papers by BU school/department...")
    classify_all(all_records)

    # Save final output
    json_path, csv_path = save_final(all_records)

    elapsed = time.time() - start
    print(f"\n{'='*72}")
    print(f"  MERGE SUMMARY")
    print(f"{'='*72}")
    print(f"  {'Source':<25} {'Raw':>8} {'New':>8} {'Dupes':>8} {'Status'}")
    print(f"  {'-'*25} {'-'*8} {'-'*8} {'-'*8} {'-'*10}")
    for name, s in stats.items():
        print(f"  {name:<25} {s['raw']:>8} {s.get('new',0):>8} {s.get('dupes',0):>8} {s['status']}")
    print(f"  {'-'*25} {'-'*8} {'-'*8} {'-'*8}")
    print(f"  {'TOTAL UNIQUE':<25} {len(all_records):>8}")
    print(f"  Elapsed: {elapsed:.1f}s")
    print(f"  Output: {json_path}")
    print(f"{'='*72}\n")


if __name__ == "__main__":
    main()
