#!/usr/bin/env python3
"""
Weekly incremental update for the BU AI Bibliography.

Harvests new papers from 4 sources (OpenAlex, PubMed, bioRxiv, SSRN),
deduplicates, pre-filters, classifies via Sonnet, verifies BU affiliation,
merges into the master dataset, regenerates data.js, and pushes to GitHub.

Usage:
    python update_weekly.py              # Full run
    python update_weekly.py --dry-run    # Show what would be harvested/classified
    python update_weekly.py --force      # Bypass cost/count gates
    python update_weekly.py --test       # Classify 1 paper to verify API key
"""

import argparse
import logging
import sys
import time
from datetime import date, datetime, timedelta

from update_pipeline import (
    acquire_lock,
    append_log,
    build_dedup_index,
    classify_via_sonnet,
    create_github_issue,
    dedup_against_master,
    embedding_prefilter,
    estimate_cost,
    git_commit_and_push,
    harvest_crossref_biorxiv_incremental,
    harvest_openalex_incremental,
    harvest_pubmed_incremental,
    harvest_ssrn_by_faculty,
    keyword_prefilter,
    load_master,
    load_state,
    merge_into_master,
    notify_macos,
    regenerate_all_outputs,
    release_lock,
    run_sanity_checks,
    save_master,
    save_state,
    validate_before_push,
    verify_bu_authors,
)
from school_mapper import classify_all

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/weekly.log", mode="a"),
    ],
)
logger = logging.getLogger("update_weekly")


def main():
    parser = argparse.ArgumentParser(description="Weekly BU AI Bibliography update")
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen without modifying data")
    parser.add_argument("--force", action="store_true", help="Bypass cost and count gates")
    parser.add_argument("--test", action="store_true", help="Classify 1 paper to verify API key works")
    args = parser.parse_args()

    start_time = time.time()
    logger.info("=" * 60)
    logger.info(f"Weekly update starting {'(DRY RUN)' if args.dry_run else ''}")

    # ── Step 1: Lock ──
    if not args.dry_run and not acquire_lock():
        logger.error("Could not acquire lock. Another run active?")
        sys.exit(1)

    try:
        _run(args, start_time)
    finally:
        if not args.dry_run:
            release_lock()


def _run(args, start_time):
    # ── Step 2: State ──
    state = load_state()
    old_count = state["master_paper_count"]
    logger.info(f"Master dataset: {old_count} papers")

    # ── Step 3: Date range ──
    since = (date.today() - timedelta(weeks=4)).isoformat()
    logger.info(f"Lookback: {since} → {date.today().isoformat()}")

    # ── Step 4: Load master + dedup index ──
    master = load_master()
    master_dois, master_fps = build_dedup_index(master)

    # ── Step 5: Harvest ──
    source_errors = {}
    all_harvested = []

    sources = [
        ("openalex", lambda: harvest_openalex_incremental("from_publication_date", since)),
        ("pubmed", lambda: harvest_pubmed_incremental(since)),
        ("biorxiv", lambda: harvest_crossref_biorxiv_incremental(since)),
        ("ssrn", lambda: harvest_ssrn_by_faculty()),
    ]

    for name, harvester in sources:
        try:
            papers = harvester()
            all_harvested.extend(papers)
            state.setdefault("source_health", {})[name] = {
                "last_success": date.today().isoformat(),
                "consecutive_failures": 0,
            }
            logger.info(f"  {name}: {len(papers)} papers")
        except Exception as e:
            logger.error(f"  {name}: FAILED — {e}")
            health = state.setdefault("source_health", {}).get(name, {})
            failures = health.get("consecutive_failures", 0) + 1
            state.setdefault("source_health", {})[name] = {
                "last_success": health.get("last_success", "unknown"),
                "consecutive_failures": failures,
            }
            source_errors[name] = {"consecutive_failures": failures}

    total_harvested = len(all_harvested)
    logger.info(f"Total harvested: {total_harvested}")

    # ── Step 6: Dedup ──
    new_papers = dedup_against_master(all_harvested, master_dois, master_fps)
    deduped_count = len(new_papers)

    # ── Step 7: Pre-filter ──
    new_papers = keyword_prefilter(new_papers)
    kw_count = len(new_papers)
    new_papers = embedding_prefilter(new_papers)
    filtered_count = len(new_papers)

    # ── Summary so far ──
    logger.info(f"Pipeline: {total_harvested} harvested → {deduped_count} new → {kw_count} keyword → {filtered_count} after embedding")

    # ── Step 8: Cost gate ──
    est_cost = estimate_cost(filtered_count)
    logger.info(f"Estimated classification cost: ${est_cost:.2f} ({filtered_count} papers)")

    if est_cost > 2.0 and not args.force:
        msg = f"Cost estimate ${est_cost:.2f} exceeds $2 threshold. Use --force to proceed."
        logger.warning(msg)
        notify_macos("BU Bib Alert", msg)
        if not args.dry_run:
            create_github_issue("Weekly update: cost threshold exceeded", msg, ["alert"])
        print(f"\n⚠️  {msg}")
        return

    if filtered_count > 100 and not args.force:
        msg = f"{filtered_count} papers to classify (>100). Likely pulling non-BU papers. Use --force."
        logger.warning(msg)
        notify_macos("BU Bib Alert", msg)
        print(f"\n⚠️  {msg}")
        return

    # ── Step 9: Dry run exit ──
    if args.dry_run:
        print(f"\n{'='*50}")
        print(f"DRY RUN SUMMARY")
        print(f"{'='*50}")
        print(f"  Harvested:    {total_harvested}")
        print(f"  After dedup:  {deduped_count}")
        print(f"  After filter: {filtered_count}")
        print(f"  Est. cost:    ${est_cost:.2f}")
        print(f"  Would classify {filtered_count} papers via Sonnet")
        print(f"{'='*50}")
        return

    # ── Step 10: Test mode ──
    if args.test:
        if filtered_count == 0:
            print("No papers to test with. API key cannot be verified without papers.")
            return
        test_papers, test_cost = classify_via_sonnet(new_papers[:1], hard_cap_usd=0.10)
        if test_papers and test_papers[0].get("ai_relevance"):
            print(f"✓ API key works. Test paper classified as: {test_papers[0]['ai_relevance']}")
            print(f"  Cost: ${test_cost:.4f}")
        else:
            print("✗ Classification failed. Check API key and model access.")
        return

    # ── Step 11: Classify ──
    if filtered_count == 0:
        logger.info("No new papers to classify")
        classified = []
        actual_cost = 0.0
    else:
        classified, actual_cost = classify_via_sonnet(new_papers, hard_cap_usd=5.0)
        # Filter out not_relevant
        classified = [p for p in classified if p.get("ai_relevance") != "not_relevant"]
        logger.info(f"After filtering not_relevant: {len(classified)} papers")

    # ── Step 12: BU verification ──
    verified = verify_bu_authors(classified) if classified else []

    # ── Step 13: School classification ──
    if verified:
        classify_all(verified)

    added_count = len(verified)
    logger.info(f"Papers to add: {added_count}")

    # ── Step 14: Sanity checks ──
    alerts = run_sanity_checks(added_count, state, actual_cost, source_errors, "weekly")
    for alert in alerts:
        logger.warning(f"ALERT: {alert}")
        notify_macos("BU Bib Alert", alert)

    if alerts:
        create_github_issue(
            f"Weekly update alerts ({date.today().isoformat()})",
            "\n".join(f"- {a}" for a in alerts),
            ["alert"],
        )

    # ── Step 15: Merge ──
    if added_count > 0:
        master = merge_into_master(master, verified)
        save_master(master)
    new_count = len(master)

    # ── Step 16: Regenerate outputs ──
    regenerate_all_outputs()

    # ── Step 17: Validate ──
    errors = validate_before_push(old_count, new_count)
    if errors:
        for e in errors:
            logger.error(f"VALIDATION FAILED: {e}")
        notify_macos("BU Bib FAILED", "Validation failed, not pushing")
        create_github_issue(
            "Weekly update: validation failed",
            "\n".join(f"- {e}" for e in errors),
            ["alert"],
        )
        return

    # ── Step 18: Git push ──
    if added_count > 0:
        commit_msg = f"weekly update: +{added_count} papers"
        git_commit_and_push(commit_msg)
    else:
        logger.info("No new papers, skipping git push")

    # ── Step 19: Update state ──
    state["last_weekly_run"] = datetime.now().isoformat()
    state["master_paper_count"] = new_count
    state["total_api_cost_usd"] = state.get("total_api_cost_usd", 0) + actual_cost
    if added_count == 0:
        state["consecutive_zero_weeks"] = state.get("consecutive_zero_weeks", 0) + 1
    else:
        state["consecutive_zero_weeks"] = 0
    save_state(state)

    # ── Step 20: Log ──
    duration = time.time() - start_time
    append_log({
        "timestamp": datetime.now().isoformat(),
        "type": "weekly",
        "harvested": total_harvested,
        "deduped": deduped_count,
        "filtered": filtered_count,
        "classified": len(classified) if classified else 0,
        "added": added_count,
        "final_count": new_count,
        "cost_usd": round(actual_cost, 4),
        "duration_s": round(duration),
        "status": "success",
    })

    # ── Done ──
    logger.info(f"Weekly update complete: +{added_count} papers, ${actual_cost:.2f}, {duration:.0f}s")
    if added_count > 0:
        notify_macos("BU Bibliography Updated", f"+{added_count} papers added")


if __name__ == "__main__":
    import os
    os.makedirs("logs", exist_ok=True)
    main()
