"""
One-time PubMed backfill: run checkpoint through the full pipeline.
Delete this script after use.

Usage:
    python backfill_pubmed.py data/pubmed_TIMESTAMP.json           # dry run (default)
    python backfill_pubmed.py data/pubmed_TIMESTAMP.json --test    # classify 5 papers
    python backfill_pubmed.py data/pubmed_TIMESTAMP.json --run     # full classification
"""

import argparse
import json
import logging
import sys

from update_pipeline import (
    load_master, build_dedup_index, dedup_against_master,
    keyword_prefilter, embedding_prefilter, estimate_cost,
    classify_via_sonnet, verify_bu_authors, merge_into_master,
    save_master, regenerate_all_outputs,
)
from school_mapper import classify_all
from validate_dataset import main as validate
from utils import setup_logging

logger = logging.getLogger("bu_bib.backfill")


def main():
    parser = argparse.ArgumentParser(description="One-time PubMed backfill")
    parser.add_argument("checkpoint", help="Path to PubMed checkpoint JSON")
    parser.add_argument("--test", action="store_true", help="Classify 5 papers only")
    parser.add_argument("--run", action="store_true", help="Full classification + merge")
    args = parser.parse_args()

    setup_logging()

    # Load checkpoint
    with open(args.checkpoint) as f:
        raw = json.load(f)
    logger.info(f"Loaded checkpoint: {len(raw)} papers")

    # Load master + dedup
    master = load_master()
    master_dois, master_fps = build_dedup_index(master)
    logger.info(f"Master: {len(master)} papers")

    # Dedup
    new = dedup_against_master(raw, master_dois, master_fps)
    logger.info(f"After dedup: {len(new)} new (removed {len(raw) - len(new)})")

    # Keyword filter
    after_kw = keyword_prefilter(new)
    logger.info(f"After keyword filter: {len(after_kw)} (removed {len(new) - len(after_kw)})")

    # Embedding filter
    after_emb = embedding_prefilter(after_kw)
    logger.info(f"After embedding filter: {len(after_emb)} (removed {len(after_kw) - len(after_emb)})")

    # Cost estimate
    cost = estimate_cost(len(after_emb))
    print(f"\n{'='*50}")
    print(f"BACKFILL PIPELINE SUMMARY")
    print(f"{'='*50}")
    print(f"  Raw checkpoint:     {len(raw)}")
    print(f"  After dedup:        {len(new)}")
    print(f"  After keyword:      {len(after_kw)}")
    print(f"  After embedding:    {len(after_emb)}")
    print(f"  Est. cost:          ${cost:.2f}")
    print(f"{'='*50}")

    if not args.test and not args.run:
        print("\nDry run. Use --test for 5-paper sample or --run for full classification.")
        return

    if args.test:
        sample = after_emb[:5]
        classified, actual_cost = classify_via_sonnet(sample, hard_cap_usd=0.50)
        print(f"\nTest results ({len(classified)} papers, ${actual_cost:.4f}):")
        for p in classified:
            rel = p.get("ai_relevance", "unknown")
            print(f"  [{rel}] {p.get('title', 'no title')[:80]}")
        return

    if args.run:
        if cost > 5.0:
            print(f"\nCost ${cost:.2f} exceeds $5 cap. Aborting.")
            sys.exit(1)

        # Classify
        classified, actual_cost = classify_via_sonnet(after_emb, hard_cap_usd=5.0)
        logger.info(f"Classified {len(classified)} papers, cost ${actual_cost:.2f}")

        # Filter not_relevant
        relevant = [p for p in classified if p.get("ai_relevance") != "not_relevant"]
        logger.info(f"After filtering not_relevant: {len(relevant)} papers")

        # BU verification
        verified = verify_bu_authors(relevant) if relevant else []
        logger.info(f"After BU verification: {len(verified)} papers")

        # School classification
        if verified:
            classify_all(verified)

        print(f"\n{'='*50}")
        print(f"CLASSIFICATION RESULTS")
        print(f"{'='*50}")
        print(f"  Classified:         {len(classified)}")
        print(f"  AI-relevant:        {len(relevant)}")
        print(f"  BU-verified:        {len(verified)}")
        print(f"  Actual cost:        ${actual_cost:.2f}")
        print(f"{'='*50}")

        if len(verified) == 0:
            print("\nNo papers to add.")
            return

        # Merge
        master = merge_into_master(master, verified)
        save_master(master)
        logger.info(f"Master now has {len(master)} papers")

        # Regenerate outputs
        regenerate_all_outputs()

        # Validate
        print("\nRunning validation...")
        validate()

        print(f"\nDone. Added {len(verified)} papers. Master: {len(master)} total.")


if __name__ == "__main__":
    main()
