#!/usr/bin/env python3
"""
BU AI Bibliography Harvester — Main Orchestrator
====================================================
Runs all source harvesters, deduplicates, and produces
a unified bibliography of BU faculty AI-related publications.

Usage:
    python harvest.py                    # Run all sources (default)
    python harvest.py --sources openalex pubmed  # Run specific sources
    python harvest.py --comprehensive    # Include ALL BU works (very large)
    python harvest.py --dry-run          # Test connections only
    python harvest.py --stats-only       # Just show stats from existing data

Sources:
    openalex         Primary academic database (broadest coverage)
    semantic_scholar Semantic Scholar (strong CS/ML coverage)
    pubmed           PubMed/NCBI (biomedical AI)
    arxiv            arXiv (CS/ML preprints)
    ssrn             SSRN via CrossRef (law/policy working papers)
    crossref         CrossRef (journal articles catch-all)
    openbu           OpenBU institutional repository (theses, working papers)
"""

import argparse
import json
import sys
import time
import logging
from datetime import datetime
from pathlib import Path

import requests
from utils import setup_logging, Deduplicator, save_final, save_checkpoint
from config import CONTACT_EMAIL

logger = logging.getLogger("bu_bib")

# Source registry — maps names to harvest functions
SOURCE_REGISTRY = {
    "openalex": ("source_openalex", "harvest"),
    "semantic_scholar": ("source_semantic_scholar", "harvest"),
    "pubmed": ("source_pubmed", "harvest"),
    "arxiv": ("source_arxiv", "harvest"),
    "ssrn": ("source_ssrn", "harvest"),
    "crossref": ("source_crossref", "harvest"),
    "openbu": ("source_openbu", "harvest"),
    "in_progress": ("source_in_progress", "harvest"),
}

# Run order: most comprehensive first, gap-fillers after
DEFAULT_SOURCE_ORDER = [
    "openalex",
    "semantic_scholar",
    "pubmed",
    "arxiv",
    "ssrn",
    "openbu",
    "crossref",
    "in_progress",
]


def import_source(module_name: str, func_name: str):
    """Dynamically import a source's harvest function."""
    import importlib
    mod = importlib.import_module(module_name)
    return getattr(mod, func_name)


def run_harvest(sources: list[str], comprehensive: bool = False) -> list[dict]:
    """Run harvesters and deduplicate results."""
    dedup = Deduplicator()
    source_stats = {}
    start_time = time.time()

    # Warn about email
    if CONTACT_EMAIL == "CHANGEME@bu.edu":
        logger.warning(
            "⚠️  CONTACT_EMAIL not set in config.py! "
            "Set it to your BU email for better API access rates."
        )

    for source_name in sources:
        if source_name not in SOURCE_REGISTRY:
            logger.warning(f"Unknown source: {source_name}, skipping")
            continue

        module_name, func_name = SOURCE_REGISTRY[source_name]
        harvest_func = import_source(module_name, func_name)

        logger.info(f"\n{'='*60}")
        logger.info(f"HARVESTING: {source_name}")
        logger.info(f"{'='*60}")

        source_start = time.time()
        try:
            papers = harvest_func()
            elapsed = time.time() - source_start

            # Deduplicate
            new_count = 0
            dupe_count = 0
            for paper in papers:
                if dedup.add(paper):
                    new_count += 1
                else:
                    dupe_count += 1

            source_stats[source_name] = {
                "raw_count": len(papers),
                "new_count": new_count,
                "dupe_count": dupe_count,
                "elapsed_seconds": round(elapsed, 1),
                "status": "success",
            }
            logger.info(
                f"✓ {source_name}: {len(papers)} raw → "
                f"{new_count} new, {dupe_count} duplicates "
                f"({elapsed:.1f}s)"
            )
        except Exception as e:
            elapsed = time.time() - source_start
            source_stats[source_name] = {
                "raw_count": 0,
                "new_count": 0,
                "dupe_count": 0,
                "elapsed_seconds": round(elapsed, 1),
                "status": f"FAILED: {str(e)}",
            }
            logger.error(f"✗ {source_name} FAILED: {e}", exc_info=True)

    # Optionally run comprehensive sweep
    if comprehensive:
        logger.info(f"\n{'='*60}")
        logger.info("COMPREHENSIVE SWEEP: All BU works from OpenAlex")
        logger.info("(This will be very large — pulling everything)")
        logger.info(f"{'='*60}")
        try:
            from source_openalex import harvest_all_bu_works
            all_papers = harvest_all_bu_works()
            new_count = sum(1 for p in all_papers if dedup.add(p))
            source_stats["openalex_comprehensive"] = {
                "raw_count": len(all_papers),
                "new_count": new_count,
                "status": "success",
            }
            logger.info(f"Comprehensive: {new_count} additional papers found")
        except Exception as e:
            logger.error(f"Comprehensive sweep failed: {e}", exc_info=True)

    total_elapsed = time.time() - start_time
    all_records = dedup.get_all()

    # Classify by BU school/department (LAW vs NON-LAW)
    logger.info("\nClassifying papers by BU school/department...")
    from school_mapper import classify_all
    classify_all(all_records)

    # Print summary
    print_summary(source_stats, len(all_records), total_elapsed)

    # Save final output
    json_path, csv_path = save_final(all_records)

    # Save stats
    stats_path = Path("data") / f"harvest_stats_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(stats_path, 'w') as f:
        json.dump({
            "total_unique_papers": len(all_records),
            "total_elapsed_seconds": round(total_elapsed, 1),
            "sources": source_stats,
            "timestamp": datetime.utcnow().isoformat(),
        }, f, indent=2)

    return all_records


def print_summary(source_stats: dict, total: int, elapsed: float):
    """Print a nice summary table."""
    print(f"\n{'='*72}")
    print(f"  BU AI BIBLIOGRAPHY HARVEST SUMMARY")
    print(f"{'='*72}")
    print(f"  {'Source':<25} {'Raw':>8} {'New':>8} {'Dupes':>8} {'Time':>8} {'Status'}")
    print(f"  {'-'*25} {'-'*8} {'-'*8} {'-'*8} {'-'*8} {'-'*10}")

    for name, stats in source_stats.items():
        status = "✓" if stats["status"] == "success" else "✗"
        print(
            f"  {name:<25} "
            f"{stats.get('raw_count', 0):>8} "
            f"{stats.get('new_count', 0):>8} "
            f"{stats.get('dupe_count', 0):>8} "
            f"{stats.get('elapsed_seconds', 0):>7.1f}s "
            f"{status}"
        )

    print(f"  {'-'*25} {'-'*8} {'-'*8} {'-'*8} {'-'*8}")
    print(f"  {'TOTAL UNIQUE':<25} {total:>8}")
    print(f"  Total time: {elapsed:.1f}s ({elapsed/60:.1f} min)")
    print(f"{'='*72}\n")


def dry_run():
    """Test API connections without harvesting."""
    print("Testing API connections...\n")

    tests = [
        ("OpenAlex", "https://api.openalex.org/works?per_page=1"),
        ("Semantic Scholar", "https://api.semanticscholar.org/graph/v1/paper/search?query=test&limit=1"),
        ("PubMed", "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&term=test&retmode=json&retmax=1"),
        ("CrossRef", "https://api.crossref.org/works?query=test&rows=1"),
        ("OpenBU", "https://open.bu.edu/server/api/discover/search/objects?query=test&size=1"),
    ]

    for name, url in tests:
        try:
            resp = requests.get(url, timeout=15)
            status = "✓" if resp.status_code == 200 else f"✗ ({resp.status_code})"
            print(f"  {name:<25} {status}")
        except Exception as e:
            print(f"  {name:<25} ✗ ({e})")

    # arXiv uses a different format
    try:
        resp = requests.get(
            "http://export.arxiv.org/api/query?search_query=ti:test&max_results=1",
            timeout=15,
        )
        status = "✓" if resp.status_code == 200 else f"✗ ({resp.status_code})"
        print(f"  {'arXiv':<25} {status}")
    except Exception as e:
        print(f"  {'arXiv':<25} ✗ ({e})")

    print()


def show_stats():
    """Show stats from the most recent harvest."""
    data_dir = Path("data")
    stat_files = sorted(data_dir.glob("harvest_stats_*.json"), reverse=True)
    if not stat_files:
        print("No harvest stats found. Run a harvest first.")
        return

    with open(stat_files[0]) as f:
        stats = json.load(f)

    print(f"\nMost recent harvest: {stat_files[0].name}")
    print(f"Total unique papers: {stats['total_unique_papers']}")
    print(f"Timestamp: {stats['timestamp']}")
    print()
    for name, s in stats.get("sources", {}).items():
        print(f"  {name}: {s.get('new_count', 0)} new ({s.get('status')})")


def main():
    parser = argparse.ArgumentParser(
        description="BU AI Bibliography Harvester",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--sources",
        nargs="+",
        choices=list(SOURCE_REGISTRY.keys()),
        default=None,
        help="Specific sources to harvest (default: all)",
    )
    parser.add_argument(
        "--comprehensive",
        action="store_true",
        help="Also pull ALL BU works from OpenAlex (very large dataset)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Test API connections only",
    )
    parser.add_argument(
        "--stats-only",
        action="store_true",
        help="Show stats from last harvest",
    )
    args = parser.parse_args()

    if args.dry_run:
        import requests
        dry_run()
        return

    if args.stats_only:
        show_stats()
        return

    setup_logging()
    sources = args.sources or DEFAULT_SOURCE_ORDER
    records = run_harvest(sources, comprehensive=args.comprehensive)
    print(f"Done! {len(records)} unique papers harvested.")


if __name__ == "__main__":
    main()
