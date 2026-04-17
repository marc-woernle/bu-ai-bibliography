"""Merge Sonnet Batch-API classification results into the master dataset.

Takes the output of `classify_papers.py collect --input=PATH` and:
1. Records rejected papers (ai_relevance=not_relevant) to the rejection index
2. Verifies BU authorship on the kept papers
3. Maps schools for verified papers
4. Merges into master, saves, and regenerates data.js

Does NOT commit or push. Review with `git diff --stat` then commit manually.

Usage:
    python merge_batch_results.py --input=data/backlog_candidates_results.json
    python merge_batch_results.py --input=... --dry-run
"""

import argparse
import json
import logging
import sys
from pathlib import Path

from update_pipeline import (
    load_master,
    merge_into_master,
    record_rejections,
    regenerate_all_outputs,
    save_master,
    verify_bu_authors,
)
from school_mapper import classify_all

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("bu_bib.merge_batch")


def load_batch_results(path: str) -> list[dict]:
    """classify_papers.py collect saves a dict keyed by index; flatten to a list."""
    with open(path) as f:
        data = json.load(f)
    if isinstance(data, dict):
        return list(data.values())
    return data


def main():
    parser = argparse.ArgumentParser(
        description="Merge batch classification results into master"
    )
    parser.add_argument(
        "--input", required=True,
        help="Path to batch results JSON (from classify_papers.py collect)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would happen without writing"
    )
    args = parser.parse_args()

    if not Path(args.input).exists():
        logger.error(f"Input file not found: {args.input}")
        sys.exit(1)

    papers = load_batch_results(args.input)
    logger.info(f"Loaded {len(papers)} batch results from {args.input}")

    parse_errors = [p for p in papers if p.get("_parse_error")]
    if parse_errors:
        logger.warning(f"{len(parse_errors)} papers had parse errors; skipping them")
        papers = [p for p in papers if not p.get("_parse_error")]

    rejected = [p for p in papers if p.get("ai_relevance") == "not_relevant"]
    kept = [p for p in papers if p.get("ai_relevance") != "not_relevant"]
    logger.info(f"  Rejected (not_relevant): {len(rejected)}")
    logger.info(f"  Kept:                    {len(kept)}")

    verified = verify_bu_authors(kept)
    logger.info(f"  BU-verified:             {len(verified)}")

    if verified:
        classify_all(verified)

    if args.dry_run:
        logger.info("DRY RUN, no changes written")
        return

    if rejected:
        record_rejections(rejected)
        logger.info(f"Recorded {len(rejected)} rejections to rejection index")

    if verified:
        master = load_master()
        old_count = len(master)
        master = merge_into_master(master, verified)
        save_master(master)
        logger.info(f"Master: {old_count} -> {len(master)} papers (+{len(verified)})")

        regenerate_all_outputs()
        logger.info("Regenerated data.js")

    logger.info("Done. Review with `git diff --stat`, then commit.")


if __name__ == "__main__":
    main()
