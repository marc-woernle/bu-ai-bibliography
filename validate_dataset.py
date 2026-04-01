#!/usr/bin/env python3
"""
BU AI Bibliography — Ground Truth Validation
=============================================
Catches problems that would otherwise go unnoticed: missing key faculty,
implausible school coverage, source gaps, name collision artifacts.

Run after any data change. Exit code 0 = pass, 1 = warnings, 2 = failures.

Usage:
    python validate_dataset.py                  # Full validation
    python validate_dataset.py --strict         # Treat warnings as failures
    python validate_dataset.py --json           # Machine-readable output
"""

import argparse
import json
import logging
import sys
from collections import Counter, defaultdict
from pathlib import Path

logger = logging.getLogger("validate_dataset")

MASTER_PATH = Path("data/sonnet_classification_bu_verified.json")
ROSTER_PATH = Path("data/bu_faculty_roster_verified.json")

# ── Ground Truth Anchors ─────────────────────────────────────────────────────
# People whose absence means something is broken. Each entry:
#   name, school, min_papers, why they matter
ANCHOR_FACULTY = [
    # Law — AI program leaders
    ("Woodrow Hartzog", "School of Law", 20, "directs BU Law AI program"),
    ("Christopher Robertson", "School of Law", 10, "health law + AI, tenured"),
    ("James Bessen", "School of Law", 10, "tech economics, prolific"),
    ("Stacey Dogan", "School of Law", 5, "IP/tech law"),
    # CS — core AI faculty
    ("Kate Saenko", "CAS — Computer Science", 50, "ML/vision, 200+ papers in OpenAlex"),
    ("Stan Sclaroff", "CAS — Computer Science", 50, "computer vision, senior"),
    ("Margrit Betke", "CAS — Computer Science", 50, "vision, senior"),
    ("Ran Canetti", "CAS — Computer Science", 10, "crypto/security"),
    ("Mark Crovella", "CAS — Computer Science", 10, "networks/ML"),
    # Engineering
    ("Calin Belta", "College of Engineering", 50, "robotics/controls, 200+ papers"),
    ("Ioannis Ch. Paschalidis", "College of Engineering", 50, "optimization/ML, very prolific"),
    ("Venkatesh Saligrama", "College of Engineering", 50, "ML/stats, very prolific"),
    ("Roberto Tron", "College of Engineering", 30, "robotics/vision"),
    # CDS
    ("Azer Bestavros", "Faculty of Computing & Data Sciences", 10, "founding dean of CDS"),
    ("Eric Kolaczyk", "Faculty of Computing & Data Sciences", 15, "network science, CDS dean"),
    # Medicine
    ("Vijaya Kolachalama", "School of Medicine", 30, "AI in medicine, prolific"),
    # Psychology
    ("Stephen Grossberg", "CAS — Psychology & Brain Sciences", 50, "neural networks pioneer"),
    # Economics
    ("Pascual Restrepo", "CAS — Economics", 5, "AI + labor economics"),
    # SPH
    ("Eleanor Murray", "School of Public Health", 3, "causal inference, AI methods"),
]

# Expected minimum papers per school (based on what we know is reasonable)
MIN_PAPERS_BY_SCHOOL = {
    "School of Medicine": 500,
    "CAS — Computer Science": 500,
    "College of Engineering": 500,
    "School of Public Health": 200,
    "Faculty of Computing & Data Sciences": 100,
    "CAS — Physics": 100,
    "CAS — Psychology & Brain Sciences": 100,
    "Questrom School of Business": 100,
    "School of Dental Medicine": 50,
    "CAS — Mathematics & Statistics": 50,
    "School of Law": 100,
    "CAS — Earth & Environment": 30,
    "CAS — Biology": 30,
    "CAS — Economics": 15,
    "Wheelock College of Education & Human Development": 20,
    "College of Communication": 10,
}

# Expected source diversity: each school should not be >95% from one source
# (Law proved that single-source dependence hides gaps)
MAX_SINGLE_SOURCE_PCT = 0.95


def load_data():
    with open(MASTER_PATH) as f:
        master = json.load(f)
    roster = []
    if ROSTER_PATH.exists():
        with open(ROSTER_PATH) as f:
            roster = json.load(f)
    return master, roster


def _normalize(name: str) -> str:
    """Normalize name for matching: strip accents, lowercase."""
    import unicodedata
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    return name.lower().strip()


def check_anchor_faculty(master):
    """Verify that anchor faculty appear with expected paper counts."""
    issues = []

    # Build author -> paper count index keyed by (last, first_initial)
    author_counts = Counter()      # (last, fi) -> count
    author_display = {}            # (last, fi) -> display name
    for p in master:
        for name in p.get("bu_author_names", []):
            norm = _normalize(name)
            parts = norm.split()
            if len(parts) >= 2:
                key = (parts[-1], parts[0][0])
                author_counts[key] += 1
                author_display[key] = name

    for name, school, min_papers, reason in ANCHOR_FACULTY:
        norm = _normalize(name)
        parts = norm.split()
        key = (parts[-1], parts[0][0]) if len(parts) >= 2 else (norm, "")
        count = author_counts.get(key, 0)
        display = author_display.get(key, name)
        if count == 0:
            issues.append({
                "level": "FAIL",
                "check": "anchor_faculty",
                "message": f"{name} ({school}) has 0 papers — {reason}",
            })
        elif count < min_papers:
            issues.append({
                "level": "WARN",
                "check": "anchor_faculty",
                "message": f"{display} has {count} papers, expected >={min_papers} — {reason}",
            })

    return issues


def check_school_coverage(master):
    """Verify minimum paper counts per school."""
    issues = []

    school_counts = Counter()
    for p in master:
        for s in p.get("bu_schools", []):
            school_counts[s] += 1

    for school, min_count in MIN_PAPERS_BY_SCHOOL.items():
        actual = school_counts.get(school, 0)
        if actual == 0:
            issues.append({
                "level": "FAIL",
                "check": "school_coverage",
                "message": f"{school} has 0 papers (expected >={min_count})",
            })
        elif actual < min_count:
            issues.append({
                "level": "WARN",
                "check": "school_coverage",
                "message": f"{school} has {actual} papers (expected >={min_count})",
            })

    return issues


def check_source_diversity(master):
    """Flag schools overly dependent on a single source."""
    issues = []

    school_sources = defaultdict(Counter)
    for p in master:
        src = p.get("source", "unknown")
        for s in p.get("bu_schools", []):
            school_sources[s][src] += 1

    for school, sources in school_sources.items():
        total = sum(sources.values())
        if total < 20:
            continue  # Too few papers to judge
        top_source, top_count = sources.most_common(1)[0]
        pct = top_count / total
        if pct > MAX_SINGLE_SOURCE_PCT:
            issues.append({
                "level": "WARN",
                "check": "source_diversity",
                "message": (
                    f"{school}: {pct:.0%} from {top_source} alone "
                    f"({top_count}/{total}) — missing sources?"
                ),
            })

    return issues


def check_data_consistency(master):
    """Verify internal data consistency."""
    issues = []

    no_bu = sum(1 for p in master if not any(a.get("is_bu") for a in p.get("authors", [])))
    if no_bu > 0:
        issues.append({
            "level": "FAIL",
            "check": "consistency",
            "message": f"{no_bu} papers have 0 BU authors",
        })

    name_mismatch = 0
    for p in master:
        bu_from_flag = set(a["name"] for a in p.get("authors", []) if a.get("is_bu"))
        bu_from_names = set(p.get("bu_author_names", []))
        if bu_from_flag != bu_from_names:
            name_mismatch += 1
    if name_mismatch > 0:
        issues.append({
            "level": "FAIL",
            "check": "consistency",
            "message": f"{name_mismatch} papers have bu_author_names != is_bu flags",
        })

    no_schools = sum(1 for p in master if not p.get("bu_schools"))
    if no_schools > 0:
        issues.append({
            "level": "WARN",
            "check": "consistency",
            "message": f"{no_schools} papers have no bu_schools assigned",
        })

    return issues


def check_suspicious_patterns(master):
    """Detect patterns that suggest data errors."""
    issues = []

    # Authors with implausibly many papers (>300 = suspicious)
    author_counts = Counter()
    for p in master:
        for name in p.get("bu_author_names", []):
            author_counts[name] += 1
    for name, count in author_counts.most_common():
        if count > 300:
            issues.append({
                "level": "WARN",
                "check": "suspicious",
                "message": f"{name} has {count} papers — verify this is one person",
            })
        else:
            break

    # Papers with >30 BU authors on a <100-author paper (collision artifact)
    for p in master:
        n_authors = len(p.get("authors", []))
        n_bu = len(p.get("bu_author_names", []))
        if n_authors < 100 and n_bu > 30:
            issues.append({
                "level": "WARN",
                "check": "suspicious",
                "message": (
                    f"'{p['title'][:50]}' has {n_bu}/{n_authors} BU authors "
                    f"— likely name collision"
                ),
            })

    # Year distribution: any year with <10 papers when adjacent years have >100
    year_counts = Counter(p.get("year") for p in master if p.get("year"))
    years = sorted(year_counts.keys())
    for i, y in enumerate(years):
        if 1 < i < len(years) - 1:
            prev_c = year_counts[years[i - 1]]
            curr_c = year_counts[y]
            next_c = year_counts[years[i + 1]]
            if curr_c < 10 and prev_c > 100 and next_c > 100:
                issues.append({
                    "level": "WARN",
                    "check": "suspicious",
                    "message": f"Year {y} has only {curr_c} papers (neighbors: {prev_c}, {next_c})",
                })

    return issues


# Schools where most faculty do AI-adjacent research — flag at lower threshold
CORE_AI_SCHOOLS = {
    "CAS — Computer Science", "College of Engineering",
    "Faculty of Computing & Data Sciences",
}
# Schools where some faculty do AI — flag at higher threshold
ADJACENT_AI_SCHOOLS = {
    "CAS — Mathematics & Statistics", "CAS — Economics",
    "CAS — Psychology & Brain Sciences", "CAS — Linguistics",
    "Questrom School of Business", "School of Law",
    "School of Public Health", "CAS — Physics",
}


def check_roster_coverage(master, roster):
    """Check roster faculty in AI-relevant schools with many works but 0 papers."""
    if not roster:
        return []

    issues = []

    # Build author presence index (accent-normalized)
    author_present = set()
    for p in master:
        for name in p.get("bu_author_names", []):
            author_present.add(_normalize(name))

    for r in roster:
        school = r.get("school", "")
        works = r.get("openalex_works", 0)

        # Different thresholds: core AI schools (50+), adjacent (200+), others skip
        if school in CORE_AI_SCHOOLS:
            min_works = 50
        elif school in ADJACENT_AI_SCHOOLS:
            min_works = 200
        else:
            continue

        norm = _normalize(r["name"])
        if works >= min_works and norm not in author_present:
            # Check if any variant is present (last name match)
            last = norm.split()[-1] if norm.split() else ""
            has_variant = any(last in n and len(last) > 3 for n in author_present)
            if not has_variant:
                issues.append({
                    "level": "WARN",
                    "check": "roster_coverage",
                    "message": (
                        f"{r['name']} ({school}) has {works} OpenAlex works "
                        f"but 0 papers in dataset"
                    ),
                })

    return issues


def main():
    parser = argparse.ArgumentParser(description="Validate BU AI Bibliography dataset")
    parser.add_argument("--strict", action="store_true", help="Treat warnings as failures")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    master, roster = load_data()

    all_issues = []
    all_issues.extend(check_anchor_faculty(master))
    all_issues.extend(check_school_coverage(master))
    all_issues.extend(check_source_diversity(master))
    all_issues.extend(check_data_consistency(master))
    all_issues.extend(check_suspicious_patterns(master))
    all_issues.extend(check_roster_coverage(master, roster))

    fails = [i for i in all_issues if i["level"] == "FAIL"]
    warns = [i for i in all_issues if i["level"] == "WARN"]

    if args.json:
        print(json.dumps({"fails": fails, "warnings": warns, "total_papers": len(master)}))
    else:
        print(f"{'='*60}")
        print(f"BU AI Bibliography Validation — {len(master)} papers")
        print(f"{'='*60}")

        if fails:
            print(f"\n FAILURES ({len(fails)}):")
            for i in fails:
                print(f"  [{i['check']}] {i['message']}")

        if warns:
            print(f"\n WARNINGS ({len(warns)}):")
            for i in warns:
                print(f"  [{i['check']}] {i['message']}")

        if not fails and not warns:
            print("\n  All checks passed.")

        print(f"\n{'='*60}")
        print(f"Result: {len(fails)} failures, {len(warns)} warnings")

    if fails:
        sys.exit(2)
    elif warns and args.strict:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
