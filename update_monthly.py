#!/usr/bin/env python3
"""
Monthly deep update for the BU AI Bibliography.

Runs on the 1st of each month. Does everything the weekly does PLUS:
- Wider harvest window (12 months back via from_publication_date)
- Citation count refresh for recent papers
- Preprint-to-publication tracking
- Broken URL detection
- BU author roster refresh
- NIH Reporter, NSF Awards, OpenBU harvest
- Domain trend detection
- New faculty candidate detection
- Monthly report generation + GitHub Issue

Usage:
    python update_monthly.py              # Full run
    python update_monthly.py --dry-run    # Show what would happen
    python update_monthly.py --force      # Bypass cost gates
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
    harvest_crossref_biorxiv_incremental,
    harvest_openalex_incremental,
    harvest_pubmed_incremental,
    harvest_ssrn_by_faculty,
    keyword_prefilter,
    load_master,
    load_state,
    merge_into_master,
    notify_macos,
    refresh_bu_authors,
    refresh_citations,
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


def generate_monthly_report(data: dict) -> str:
    """Generate a markdown monthly report."""
    lines = [
        f"# BU AI Bibliography Monthly Report — {date.today().strftime('%B %Y')}",
        "",
        "## Summary",
        f"- New papers added: **{data.get('added', 0)}**",
        f"- Total papers: **{data.get('total', 0)}**",
        f"- API cost: ${data.get('cost', 0):.2f}",
        f"- Citations refreshed: {data.get('citations_updated', 0)} papers",
        "",
    ]

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

    # Citation milestones
    if data.get("milestones_100") or data.get("milestones_1000"):
        lines.append("## Citation Milestones")
        for t in data.get("milestones_1000", []):
            lines.append(f"- **1,000+ citations**: {t}")
        for t in data.get("milestones_100", []):
            lines.append(f"- **100+ citations**: {t}")
        lines.append("")

    # Preprints published
    if data.get("preprints_published"):
        lines.append("## Preprints Now Published")
        for t in data["preprints_published"]:
            lines.append(f"- {t}")
        lines.append("")

    # Broken URLs
    if data.get("broken_urls"):
        lines.append("## Broken URLs Found")
        for b in data["broken_urls"]:
            lines.append(f"- [{b['status']}] {b['title'][:60]} — {b['url']}")
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
        for f in data["new_faculty"][:10]:
            lines.append(f"- **{f['name']}** ({f['paper_count']} papers)")
        lines.append("")

    # New BU authors
    if data.get("new_authors"):
        lines.append(f"## BU Author Roster: +{data['new_authors']} new authors added")
        lines.append("")

    # Source health
    if data.get("source_alerts"):
        lines.append("## Source Health Alerts")
        for a in data["source_alerts"]:
            lines.append(f"- {a}")
        lines.append("")

    lines.append(f"---\n*Generated {datetime.now().isoformat()}*")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Monthly BU AI Bibliography update")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    start_time = time.time()
    logger.info("=" * 60)
    logger.info(f"Monthly update starting {'(DRY RUN)' if args.dry_run else ''}")

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
    old_count = state["master_paper_count"]
    master = load_master()
    master_dois, master_fps = build_dedup_index(master)
    report_data = {}

    # ── Harvest (wider window: 12 months) ──
    since_12m = (date.today() - timedelta(days=365)).isoformat()
    since_3m = (date.today() - timedelta(days=90)).isoformat()

    source_errors = {}
    all_harvested = []

    sources = [
        ("openalex", lambda: harvest_openalex_incremental("from_publication_date", since_12m)),
        ("pubmed", lambda: harvest_pubmed_incremental(since_3m)),
        ("biorxiv", lambda: harvest_crossref_biorxiv_incremental(since_3m)),
        ("ssrn", lambda: harvest_ssrn_by_faculty()),
    ]

    # Monthly also checks NIH, NSF, OpenBU
    try:
        from source_in_progress import harvest_nih_reporter, harvest_nsf_awards
        sources.append(("nih", lambda: harvest_nih_reporter()))
        sources.append(("nsf", lambda: harvest_nsf_awards()))
    except ImportError:
        logger.warning("source_in_progress not available")

    try:
        from source_openbu import harvest as harvest_openbu
        sources.append(("openbu", lambda: harvest_openbu()))
    except ImportError:
        logger.warning("source_openbu not available")

    try:
        from source_scholarly_commons import harvest as harvest_sc
        sources.append(("scholarly_commons", lambda: harvest_sc()))
    except ImportError:
        logger.warning("source_scholarly_commons not available")

    try:
        from source_arxiv import harvest as harvest_arxiv
        sources.append(("arxiv", lambda: harvest_arxiv()))
    except ImportError:
        logger.warning("source_arxiv not available")

    try:
        from source_semantic_scholar import harvest as harvest_s2
        sources.append(("semantic_scholar", lambda: harvest_s2()))
    except ImportError:
        logger.warning("source_semantic_scholar not available")

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

    # ── Dedup + filter ──
    new_papers = dedup_against_master(all_harvested, master_dois, master_fps)
    new_papers = keyword_prefilter(new_papers)
    new_papers = embedding_prefilter(new_papers)
    filtered_count = len(new_papers)

    # ── Cost gate ──
    est_cost = estimate_cost(filtered_count)
    if est_cost > 5.0 and not args.force:
        msg = f"Monthly cost estimate ${est_cost:.2f} exceeds $5. Use --force."
        logger.warning(msg)
        notify_macos("BU Bib Alert", msg)
        return

    if filtered_count > 300 and not args.force:
        msg = f"{filtered_count} papers to classify (>300). Use --force."
        logger.warning(msg)
        return

    if args.dry_run:
        print(f"\n{'='*50}")
        print(f"MONTHLY DRY RUN SUMMARY")
        print(f"{'='*50}")
        print(f"  Harvested:    {total_harvested}")
        print(f"  After filter: {filtered_count}")
        print(f"  Est. cost:    ${est_cost:.2f}")
        print(f"{'='*50}")
        return

    # ── Classify new papers ──
    actual_cost = 0.0
    if filtered_count > 0:
        classified, actual_cost = classify_via_sonnet(new_papers, hard_cap_usd=10.0)
        classified = [p for p in classified if p.get("ai_relevance") != "not_relevant"]
        verified = verify_bu_authors(classified)
        if verified:
            classify_all(verified)
    else:
        verified = []

    added_count = len(verified)
    report_data["added"] = added_count
    report_data["new_papers"] = verified
    report_data["cost"] = actual_cost

    # ── Merge new papers ──
    if added_count > 0:
        master = merge_into_master(master, verified)

    # ── Citation refresh ──
    logger.info("Refreshing citations...")
    cit_result = refresh_citations(master)
    report_data["citations_updated"] = cit_result["updated"]
    report_data["milestones_100"] = cit_result["milestones_100"]
    report_data["milestones_1000"] = cit_result["milestones_1000"]

    # ── Preprint tracking ──
    logger.info("Checking preprint publications...")
    published = track_preprint_publications(master)
    report_data["preprints_published"] = published

    # ── Metadata refresh ──
    logger.info("Refreshing metadata sample...")
    refresh_metadata_sample(master)

    # ── Broken URL check ──
    logger.info("Checking for broken URLs...")
    broken = check_broken_urls(master)
    report_data["broken_urls"] = broken

    # ── BU author roster refresh ──
    logger.info("Refreshing BU author roster...")
    new_authors = refresh_bu_authors()
    report_data["new_authors"] = new_authors

    # ── Domain trends ──
    current_snapshot = compute_domain_snapshot(master)
    previous_snapshot = state.get("domain_snapshot", {})
    trends = detect_domain_trends(current_snapshot, previous_snapshot)
    report_data["domain_trends"] = trends
    state["domain_snapshot"] = current_snapshot

    # ── New faculty candidates ──
    candidates = detect_new_faculty_candidates(master)
    report_data["new_faculty"] = candidates

    # ── Source health alerts ──
    source_alerts = []
    for name, health in state.get("source_health", {}).items():
        if health.get("consecutive_failures", 0) >= 3:
            source_alerts.append(f"{name}: {health['consecutive_failures']} consecutive failures")
    report_data["source_alerts"] = source_alerts

    # ── Save master ──
    report_data["total"] = len(master)
    save_master(master)

    # ── Regenerate outputs ──
    regenerate_all_outputs()

    # ── Validate ──
    errors = validate_before_push(old_count, len(master))
    if errors:
        for e in errors:
            logger.error(f"VALIDATION FAILED: {e}")
        notify_macos("BU Bib FAILED", "Monthly validation failed")
        return

    # ── Generate report ──
    report_md = generate_monthly_report(report_data)
    report_path = f"output/monthly_report_{date.today().strftime('%Y%m')}.md"
    os.makedirs("output", exist_ok=True)
    with open(report_path, "w") as f:
        f.write(report_md)
    logger.info(f"Monthly report: {report_path}")

    # ── Git push ──
    commit_parts = []
    if added_count > 0:
        commit_parts.append(f"+{added_count} papers")
    if cit_result["updated"] > 0:
        commit_parts.append(f"{cit_result['updated']} citation refreshes")
    commit_msg = f"monthly update: {', '.join(commit_parts)}" if commit_parts else "monthly update: metadata refresh"
    git_commit_and_push(commit_msg)

    # ── GitHub Issue with report summary ──
    issue_body = f"Monthly report for {date.today().strftime('%B %Y')}:\n\n"
    issue_body += f"- **+{added_count}** new papers (total: {len(master)})\n"
    issue_body += f"- **{cit_result['updated']}** citation refreshes\n"
    issue_body += f"- **{len(published)}** preprints now published\n"
    issue_body += f"- **{len(broken)}** broken URLs found\n"
    issue_body += f"- **{new_authors}** new BU authors\n"
    if candidates:
        issue_body += f"- **{len(candidates)}** new faculty candidates detected\n"
    issue_body += f"\nFull report: `{report_path}`"
    create_github_issue(
        f"Monthly report: {date.today().strftime('%B %Y')}",
        issue_body,
        ["monthly-report"],
    )

    # ── Update state ──
    state["last_monthly_run"] = datetime.now().isoformat()
    state["master_paper_count"] = len(master)
    state["total_api_cost_usd"] = state.get("total_api_cost_usd", 0) + actual_cost
    if added_count == 0:
        state["consecutive_zero_weeks"] = state.get("consecutive_zero_weeks", 0) + 1
    else:
        state["consecutive_zero_weeks"] = 0
    save_state(state)

    duration = time.time() - start_time
    append_log({
        "timestamp": datetime.now().isoformat(),
        "type": "monthly",
        "harvested": total_harvested,
        "deduped": filtered_count,
        "filtered": filtered_count,
        "classified": len(verified),
        "added": added_count,
        "final_count": len(master),
        "cost_usd": round(actual_cost, 4),
        "duration_s": round(duration),
        "status": "success",
    })

    logger.info(f"Monthly update complete: +{added_count} papers, {cit_result['updated']} citations, ${actual_cost:.2f}, {duration:.0f}s")
    notify_macos("BU Bibliography Monthly", f"+{added_count} papers, {cit_result['updated']} citations refreshed")


if __name__ == "__main__":
    os.makedirs("logs", exist_ok=True)
    main()
