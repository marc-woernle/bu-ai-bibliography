#!/usr/bin/env python3
"""
Quarterly diagnostic review for the BU AI Bibliography.

Read-only analysis. Does NOT modify data or push to git.
Generates a report for human review.

Usage:
    python quarterly_review.py
"""

import json
import logging
import os
import random
from collections import Counter
from datetime import date, datetime, timedelta

from update_pipeline import (
    compute_cross_school_collaborations,
    compute_domain_snapshot,
    compute_year_over_year,
    create_github_issue,
    detect_new_faculty_candidates,
    load_master,
    load_state,
)
from school_mapper import FACULTY_LOOKUP

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("quarterly_review")


def faculty_gap_check(master: list[dict]) -> list[dict]:
    """Check every faculty in FACULTY_LOOKUP against the master dataset."""
    # Build set of BU author names in master
    author_counts = Counter()
    for p in master:
        for name in p.get("bu_author_names", []):
            author_counts[name.lower()] += 1

    gaps = []
    for (last, first_initial), (school, _) in sorted(FACULTY_LOOKUP.items()):
        # Check if any paper has this faculty member
        found = False
        count = 0
        for name, c in author_counts.items():
            parts = name.split()
            if len(parts) >= 2 and parts[-1].lower() == last and parts[0][0].lower() == first_initial:
                found = True
                count = c
                break
        if not found:
            gaps.append({"name": f"{first_initial.upper()}. {last.title()}", "school": school, "papers": 0})
        elif count < 3:
            gaps.append({"name": f"{first_initial.upper()}. {last.title()}", "school": school, "papers": count})

    return gaps


def stratified_sample(master: list[dict], n: int = 20) -> list[dict]:
    """Sample papers stratified by ai_relevance + lowest confidence."""
    primary = [p for p in master if p.get("ai_relevance") == "primary"]
    methodological = [p for p in master if p.get("ai_relevance") == "methodological"]
    peripheral = [p for p in master if p.get("ai_relevance") == "peripheral"]
    lowest_conf = sorted(master, key=lambda p: p.get("confidence", 1.0))[:100]

    sample = []
    for pool, count in [(primary, 5), (methodological, 5), (peripheral, 5), (lowest_conf, 5)]:
        sample.extend(random.sample(pool, min(count, len(pool))))

    result = []
    for p in sample:
        result.append({
            "title": p.get("title", "")[:100],
            "abstract": (p.get("abstract") or "")[:200],
            "relevance": p.get("ai_relevance"),
            "confidence": p.get("confidence"),
            "summary": p.get("one_line_summary", ""),
            "schools": p.get("bu_schools", []),
        })
    return result


def weekly_histogram(state: dict) -> str:
    """Text bar chart of papers added per week from update_log.csv."""
    log_path = "data/update_log.csv"
    if not os.path.exists(log_path):
        return "No update log found."

    import csv
    weeks = {}
    with open(log_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts = row.get("timestamp", "")[:10]
            added = int(row.get("added", 0))
            week = ts[:7] + "-W" + str((int(ts[8:10]) - 1) // 7 + 1)
            weeks[week] = weeks.get(week, 0) + added

    if not weeks:
        return "No weekly data yet."

    max_val = max(weeks.values()) or 1
    lines = ["```"]
    for week, count in sorted(weeks.items()):
        bar = "█" * int(count / max_val * 30) if count > 0 else "·"
        lines.append(f"{week}  {bar} {count}")
    lines.append("```")
    return "\n".join(lines)


def generate_report() -> str:
    """Generate the full quarterly review report."""
    master = load_master()
    state = load_state()
    today = date.today()

    lines = [
        f"# BU AI Bibliography Quarterly Review — {today.strftime('%B %d, %Y')}",
        "",
        f"Total papers: **{len(master)}**",
        "",
    ]

    # 1. Faculty gap check
    lines.append("## 1. Faculty Gap Check")
    gaps = faculty_gap_check(master)
    if gaps:
        lines.append(f"{len(gaps)} faculty with <3 papers:")
        for g in gaps:
            lines.append(f"- {g['name']} ({g['school']}): {g['papers']} papers")
    else:
        lines.append("All faculty in FACULTY_LOOKUP have 3+ papers.")
    lines.append("")

    # 2. Stratified sample for human review
    lines.append("## 2. Random Sample for Review")
    sample = stratified_sample(master)
    for s in sample:
        lines.append(f"### [{s['relevance']}] conf={s['confidence']:.2f}")
        lines.append(f"**{s['title']}**")
        lines.append(f"*{s['summary']}*")
        if s["abstract"]:
            lines.append(f"> {s['abstract']}...")
        lines.append(f"Schools: {', '.join(s['schools'])}")
        lines.append("")

    # 3. Weekly addition histogram
    lines.append("## 3. Weekly Addition Histogram")
    lines.append(weekly_histogram(state))
    lines.append("")

    # 4. New faculty candidates
    lines.append("## 4. New Faculty Candidates")
    candidates = detect_new_faculty_candidates(master)
    if candidates:
        lines.append("BU authors with 5+ AI papers not in FACULTY_LOOKUP:")
        for c in candidates[:15]:
            lines.append(f"- **{c['name']}** — {c['paper_count']} papers")
    else:
        lines.append("No new candidates found.")
    lines.append("")

    # 5. Top cited papers (last 3 months)
    three_months_ago = (today - timedelta(days=90)).isoformat()
    lines.append("## 5. Top Cited Papers (Recent)")
    # Find papers added recently (approximation: year >= current year)
    recent = sorted(
        [p for p in master if (p.get("year") or 0) >= today.year - 1],
        key=lambda p: p.get("citation_count", 0) or 0,
        reverse=True,
    )[:10]
    for p in recent:
        lines.append(f"- **{p.get('citation_count', 0)}** citations: {p.get('title', '')[:80]}")
    lines.append("")

    # 6. Year-over-year output trends
    lines.append("## 6. Year-over-Year Output")
    yoy = compute_year_over_year(master)
    recent_years = {y: c for y, c in yoy.items() if y >= today.year - 5}
    lines.append("```")
    max_count = max(recent_years.values()) if recent_years else 1
    for year, count in sorted(recent_years.items()):
        bar = "█" * int(count / max_count * 30)
        lines.append(f"{year}  {bar} {count}")
    lines.append("```")
    lines.append("")

    # 7. Domain distribution
    lines.append("## 7. Domain Distribution")
    snapshot = compute_domain_snapshot(master)
    for domain, count in sorted(snapshot.items(), key=lambda x: -x[1])[:15]:
        lines.append(f"- {domain}: {count}")
    lines.append("")

    # 8. Cross-school collaborations
    lines.append("## 8. Cross-School Collaborations")
    collabs = compute_cross_school_collaborations(master)
    if collabs:
        for (s1, s2), count in collabs[:10]:
            lines.append(f"- {s1} + {s2}: {count} papers")
    else:
        lines.append("No cross-school collaborations detected.")
    lines.append("")

    # 9. Cost summary
    lines.append("## 9. Cost Summary")
    lines.append(f"- Total API cost to date: ${state.get('total_api_cost_usd', 0):.2f}")
    lines.append(f"- Last weekly run: {state.get('last_weekly_run', 'never')}")
    lines.append(f"- Last monthly run: {state.get('last_monthly_run', 'never')}")
    lines.append(f"- Consecutive zero weeks: {state.get('consecutive_zero_weeks', 0)}")
    lines.append("")

    lines.append(f"---\n*Generated {datetime.now().isoformat()}*")
    return "\n".join(lines)


def main():
    logger.info("Generating quarterly review...")
    report = generate_report()

    report_path = f"output/quarterly_review_{date.today().strftime('%Y%m%d')}.md"
    os.makedirs("output", exist_ok=True)
    with open(report_path, "w") as f:
        f.write(report)

    logger.info(f"Report saved to {report_path}")

    # Create GitHub Issue
    create_github_issue(
        f"Quarterly review: {date.today().strftime('%B %Y')}",
        f"Quarterly diagnostic report is ready for review.\n\nSee `{report_path}` in the repo.",
        ["quarterly-review"],
    )

    print(f"\nReport saved to {report_path}")
    print("Open it to review faculty gaps, random samples, and trends.")


if __name__ == "__main__":
    main()
