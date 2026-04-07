#!/usr/bin/env python3
"""
Monthly auto-update for the BU AI Bibliography.

Fully automated, zero-human-in-the-loop pipeline that:
  Phase 1: Refreshes faculty roster (scrape + resolve OAIDs + enrich)
  Phase 2: Harvests from all 13 sources
  Phase 3: Filters + classifies via Sonnet
  Phase 4: Merges + runs maintenance tasks
  Phase 5: Validates + pushes to GitHub
  Phase 6: Posts comprehensive report as GitHub Issue

Usage:
    python update_monthly.py --dry-run    # Show what would happen
    python update_monthly.py --ci         # Full run in CI (no interactive gates)
    python update_monthly.py              # Full run locally
"""

import argparse
import logging
import os
import sys
import time
from datetime import date, datetime, timedelta

from update_pipeline import (
    acquire_lock,
    append_log,
    build_dedup_index,
    check_broken_urls,
    classify_via_sonnet,
    compute_domain_snapshot,
    create_github_issue,
    dedup_against_master,
    detect_domain_trends,
    detect_new_faculty_candidates,
    embedding_prefilter,
    estimate_cost,
    git_commit_and_push,
    harvest_all_sources,
    keyword_prefilter,
    load_master,
    load_state,
    merge_into_master,
    notify_macos,
    refresh_bu_authors,
    refresh_citations,
    refresh_faculty_roster,
    refresh_metadata_sample,
    regenerate_all_outputs,
    release_lock,
    run_sanity_checks,
    save_master,
    save_state,
    track_preprint_publications,
    validate_before_push,
    verify_bu_authors,
)
from school_mapper import classify_all

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/monthly.log", mode="a"),
    ],
)
logger = logging.getLogger("update_monthly")


def generate_report(data: dict) -> str:
    """Generate comprehensive markdown report for GitHub Issue."""
    lines = [
        f"# BU AI Bibliography Monthly Report - {date.today().strftime('%B %Y')}",
        "",
    ]

    # Summary
    lines.append("## Summary")
    lines.append(f"- **New papers added:** {data.get('added', 0)}")
    lines.append(f"- **Total papers:** {data.get('total', 0)}")
    lines.append(f"- **API cost:** ${data.get('cost', 0):.2f}")
    lines.append(f"- **Duration:** {data.get('duration_m', 0):.0f} minutes")
    lines.append("")

    # Roster refresh
    roster = data.get("roster", {})
    if roster:
        lines.append("## Faculty Roster Refresh")
        if roster.get("error"):
            lines.append(f"- **ERROR:** {roster['error']}")
        else:
            lines.append(f"- New faculty added: {roster.get('added', 0)}")
            lines.append(f"- OpenAlex IDs resolved: {roster.get('oaids_resolved', 0)}")
            lines.append(f"- Unspecified entries enriched: {roster.get('enriched', 0)}")
        for w in roster.get("warnings", []):
            lines.append(f"- WARNING: {w}")
        lines.append("")

    # Source-by-source harvest
    source_report = data.get("source_report", {})
    if source_report:
        lines.append("## Source Harvest")
        lines.append("| Source | Papers | Status | Time |")
        lines.append("|--------|--------|--------|------|")
        for name, info in sorted(source_report.items(), key=lambda x: -x[1].get("count", 0)):
            status = info.get("status", "?")
            if status == "FAILED":
                status = f"FAILED: {info.get('error', '')[:60]}"
            lines.append(f"| {name} | {info.get('count', 0)} | {status} | {info.get('duration_s', 0):.0f}s |")
        total_harvested = sum(r.get("count", 0) for r in source_report.values())
        lines.append(f"\n**Total harvested:** {total_harvested}")
        lines.append("")

    # Pipeline
    lines.append("## Pipeline")
    lines.append(f"- Harvested: {data.get('harvested', 0)}")
    lines.append(f"- After dedup: {data.get('deduped', 0)}")
    lines.append(f"- After keyword filter: {data.get('keyword_filtered', 0)}")
    lines.append(f"- After embedding filter: {data.get('embedding_filtered', 0)}")
    lines.append(f"- Classified: {data.get('classified', 0)}")
    lines.append(f"- After BU verification: {data.get('verified', 0)}")
    lines.append(f"- **Merged into master:** {data.get('added', 0)}")
    lines.append("")

    # New papers by school
    if data.get("new_papers"):
        lines.append("## New Papers by School")
        school_counts = {}
        for p in data["new_papers"]:
            for s in p.get("bu_schools", ["Unknown"]):
                school_counts[s] = school_counts.get(s, 0) + 1
        for school, count in sorted(school_counts.items(), key=lambda x: -x[1]):
            lines.append(f"- {school}: {count}")
        lines.append("")

    # Citations
    if data.get("citations_updated"):
        lines.append("## Citation Refresh")
        lines.append(f"- Updated: {data['citations_updated']} papers")
        for t in data.get("milestones_1000", []):
            lines.append(f"- **1,000+ citations:** {t}")
        for t in data.get("milestones_100", []):
            lines.append(f"- **100+ citations:** {t}")
        lines.append("")

    # Preprints
    if data.get("preprints_published"):
        lines.append("## Preprints Now Published")
        for t in data["preprints_published"]:
            lines.append(f"- {t}")
        lines.append("")

    # Broken URLs
    if data.get("broken_urls"):
        lines.append("## Broken URLs")
        for b in data["broken_urls"][:20]:
            lines.append(f"- [{b['status']}] {b['title'][:60]}")
        lines.append("")

    # Domain trends
    if data.get("domain_trends"):
        lines.append("## Domain Trends")
        for t in data["domain_trends"]:
            lines.append(f"- {t}")
        lines.append("")

    # New faculty candidates
    if data.get("new_faculty"):
        lines.append("## New Faculty Candidates")
        lines.append("Authors with 5+ AI papers not in roster:")
        for f in data["new_faculty"][:15]:
            lines.append(f"- **{f['name']}** ({f['paper_count']} papers)")
        lines.append("")

    # Source health alerts
    failed_sources = [n for n, r in source_report.items() if r.get("status") == "FAILED"]
    if failed_sources:
        lines.append("## Alerts")
        for n in failed_sources:
            lines.append(f"- SOURCE FAILED: {n} - {source_report[n].get('error', '')[:100]}")
        lines.append("")

    if data.get("validation_errors"):
        lines.append("## VALIDATION FAILURES")
        for e in data["validation_errors"]:
            lines.append(f"- {e}")
        lines.append("")

    lines.append(f"---\n*Generated {datetime.now().isoformat()}*")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Monthly BU AI Bibliography update")
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen")
    parser.add_argument("--ci", action="store_true", help="CI mode: no interactive gates")
    args = parser.parse_args()

    start_time = time.time()
    logger.info("=" * 60)
    logger.info(f"Monthly update starting {'(DRY RUN)' if args.dry_run else '(CI)' if args.ci else ''}")

    if not args.dry_run and not acquire_lock():
        logger.error("Could not acquire lock")
        sys.exit(1)

    try:
        _run(args, start_time)
    finally:
        if not args.dry_run:
            release_lock()


def _run(args, start_time):
    state = load_state()
    report_data = {}

    # ── Phase 1: Faculty Roster Refresh ──
    logger.info("=" * 40 + " PHASE 1: ROSTER REFRESH " + "=" * 40)
    if not args.dry_run:
        roster_report = refresh_faculty_roster()
        report_data["roster"] = roster_report
        logger.info(f"Roster refresh: +{roster_report.get('added', 0)} faculty, "
                     f"{roster_report.get('oaids_resolved', 0)} OAIDs, "
                     f"{roster_report.get('enriched', 0)} enriched")
    else:
        report_data["roster"] = {"added": 0, "warnings": ["skipped (dry run)"], "oaids_resolved": 0, "enriched": 0}

    # ── Phase 2: Harvest ──
    logger.info("=" * 40 + " PHASE 2: HARVEST " + "=" * 40)
    old_count = state.get("master_paper_count", 0)
    master = load_master()
    old_count = len(master)
    master_dois, master_fps = build_dedup_index(master)

    since_12m = (date.today() - timedelta(days=365)).isoformat()
    since_3m = (date.today() - timedelta(days=90)).isoformat()

    all_harvested, source_report = harvest_all_sources(since_12m, since_3m)
    report_data["source_report"] = source_report
    report_data["harvested"] = len(all_harvested)

    # ── Phase 3: Filter + Classify ──
    logger.info("=" * 40 + " PHASE 3: FILTER + CLASSIFY " + "=" * 40)

    new_papers = dedup_against_master(all_harvested, master_dois, master_fps)
    report_data["deduped"] = len(new_papers)

    new_papers = keyword_prefilter(new_papers)
    report_data["keyword_filtered"] = len(new_papers)

    new_papers = embedding_prefilter(new_papers)
    report_data["embedding_filtered"] = len(new_papers)

    est_cost = estimate_cost(len(new_papers))
    logger.info(f"Estimated cost: ${est_cost:.2f} ({len(new_papers)} papers)")

    if args.dry_run:
        duration = (time.time() - start_time) / 60
        report_data["duration_m"] = duration
        print(f"\n{'='*50}")
        print(f"MONTHLY DRY RUN SUMMARY")
        print(f"{'='*50}")
        print(f"  Harvested:       {report_data['harvested']}")
        print(f"  After dedup:     {report_data['deduped']}")
        print(f"  After kw filter: {report_data['keyword_filtered']}")
        print(f"  After emb filter:{report_data['embedding_filtered']}")
        print(f"  Est. cost:       ${est_cost:.2f}")
        print(f"  Duration:        {duration:.1f} min")
        print(f"\n  Sources:")
        for name, info in sorted(source_report.items(), key=lambda x: -x[1].get("count", 0)):
            print(f"    {name:25s} {info['count']:>6} papers  [{info['status']}]")
        print(f"{'='*50}")
        return

    # Cost gate (CI mode: no interactive block, just hard cap)
    hard_cap = 15.0
    if est_cost > hard_cap and not args.ci:
        msg = f"Cost estimate ${est_cost:.2f} exceeds ${hard_cap} cap. Use --ci to proceed."
        logger.warning(msg)
        notify_macos("BU Bib Alert", msg)
        return

    # Classify
    actual_cost = 0.0
    if len(new_papers) > 0:
        classified, actual_cost = classify_via_sonnet(new_papers, hard_cap_usd=hard_cap)
        classified = [p for p in classified if p.get("ai_relevance") != "not_relevant"]
        report_data["classified"] = len(classified)

        verified = verify_bu_authors(classified)
        report_data["verified"] = len(verified)

        if verified:
            classify_all(verified)
    else:
        verified = []
        report_data["classified"] = 0
        report_data["verified"] = 0

    report_data["cost"] = actual_cost
    report_data["added"] = len(verified)
    report_data["new_papers"] = verified

    # ── Phase 4: Merge + Maintenance ──
    logger.info("=" * 40 + " PHASE 4: MERGE + MAINTENANCE " + "=" * 40)

    if verified:
        master = merge_into_master(master, verified)

    # Citation refresh
    logger.info("Refreshing citations...")
    cit_result = refresh_citations(master)
    report_data["citations_updated"] = cit_result["updated"]
    report_data["milestones_100"] = cit_result.get("milestones_100", [])
    report_data["milestones_1000"] = cit_result.get("milestones_1000", [])

    # Preprint tracking
    logger.info("Tracking preprints...")
    published = track_preprint_publications(master)
    report_data["preprints_published"] = published

    # Metadata refresh
    logger.info("Refreshing metadata sample...")
    refresh_metadata_sample(master)

    # Broken URL check
    logger.info("Checking broken URLs...")
    broken = check_broken_urls(master)
    report_data["broken_urls"] = broken

    # BU author refresh
    logger.info("Refreshing BU author list...")
    new_authors = refresh_bu_authors()
    report_data["new_bu_authors"] = new_authors

    # Domain trends
    current_snapshot = compute_domain_snapshot(master)
    previous_snapshot = state.get("domain_snapshot", {})
    trends = detect_domain_trends(current_snapshot, previous_snapshot)
    report_data["domain_trends"] = trends
    state["domain_snapshot"] = current_snapshot

    # New faculty candidates
    candidates = detect_new_faculty_candidates(master)
    report_data["new_faculty"] = candidates

    # ── Phase 5: Save + Validate + Push ──
    logger.info("=" * 40 + " PHASE 5: SAVE + VALIDATE + PUSH " + "=" * 40)

    report_data["total"] = len(master)
    save_master(master)
    regenerate_all_outputs()

    errors = validate_before_push(old_count, len(master))
    report_data["validation_errors"] = errors

    if errors:
        for e in errors:
            logger.error(f"VALIDATION FAILED: {e}")
        notify_macos("BU Bib FAILED", "Monthly validation failed, not pushing")
    else:
        commit_parts = []
        if len(verified) > 0:
            commit_parts.append(f"+{len(verified)} papers")
        if cit_result["updated"] > 0:
            commit_parts.append(f"{cit_result['updated']} citation refreshes")
        commit_msg = f"monthly update: {', '.join(commit_parts)}" if commit_parts else "monthly update: maintenance only"
        git_commit_and_push(commit_msg)

    # ── Phase 6: Report ──
    logger.info("=" * 40 + " PHASE 6: REPORT " + "=" * 40)

    duration = (time.time() - start_time) / 60
    report_data["duration_m"] = duration

    report_md = generate_report(report_data)

    # Save report locally
    report_path = f"output/monthly_report_{date.today().strftime('%Y%m')}.md"
    os.makedirs("output", exist_ok=True)
    with open(report_path, "w") as f:
        f.write(report_md)
    logger.info(f"Report saved: {report_path}")

    # Post as GitHub Issue
    label = "monthly-report" if not errors else "alert"
    title = f"Monthly report: {date.today().strftime('%B %Y')}"
    if errors:
        title = f"ALERT: Monthly update validation failed ({date.today().isoformat()})"
    create_github_issue(title, report_md, [label])

    # ── Update state ──
    state["last_monthly_run"] = datetime.now().isoformat()
    state["master_paper_count"] = len(master)
    state["total_api_cost_usd"] = state.get("total_api_cost_usd", 0) + actual_cost

    # Track source health
    for name, info in source_report.items():
        health = state.setdefault("source_health", {}).get(name, {})
        if info["status"] == "ok":
            state.setdefault("source_health", {})[name] = {
                "last_success": date.today().isoformat(),
                "consecutive_failures": 0,
            }
        elif info["status"] == "FAILED":
            failures = health.get("consecutive_failures", 0) + 1
            state.setdefault("source_health", {})[name] = {
                "last_success": health.get("last_success", "unknown"),
                "consecutive_failures": failures,
            }

    save_state(state)

    # Log
    append_log({
        "timestamp": datetime.now().isoformat(),
        "type": "monthly",
        "harvested": report_data.get("harvested", 0),
        "deduped": report_data.get("deduped", 0),
        "filtered": report_data.get("embedding_filtered", 0),
        "classified": report_data.get("classified", 0),
        "added": len(verified),
        "final_count": len(master),
        "cost_usd": round(actual_cost, 4),
        "duration_s": round(time.time() - start_time),
        "status": "success" if not errors else "validation_failed",
    })

    logger.info(f"Monthly update complete: +{len(verified)} papers, "
                f"{cit_result['updated']} citations, ${actual_cost:.2f}, {duration:.0f}m")
    if not errors:
        notify_macos("BU Bibliography Monthly", f"+{len(verified)} papers")


if __name__ == "__main__":
    os.makedirs("logs", exist_ok=True)
    main()
