#!/usr/bin/env python3
"""
Propagate paper counts and source mentions from master to README + GitHub.

Reads the master dataset and the faculty roster, computes the canonical numbers,
and patches every templated count in README.md plus the GitHub repo description
via the `gh` CLI. Called from the monthly pipeline after master regeneration so
the public artifacts never drift.

Templated lines in README.md (matched by regex):
  - "Currently **N papers** across M schools"
  - "DBLP source, which contributes N papers"
  - source coverage table (Mentions column for each source)
  - "exceed the N deduplicated paper count"
  - "Master dataset (N papers)"
  - "Faculty roster (N entries)"

Run:
  python propagate_counts.py             # update README + repo description
  python propagate_counts.py --dry-run   # show diff, don't write
  python propagate_counts.py --no-gh     # skip GitHub repo description update
"""

from __future__ import annotations
import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

from config import DATA_SOURCES, CLASSIFIER_DISPLAY_NAME

MASTER_PATH = Path("data/sonnet_classification_bu_verified.json")
ROSTER_PATH = Path("data/bu_faculty_roster_verified.json")
README_PATH = Path("README.md")

# Source key in master → display label in README/repo description.
# Used to map all_sources tags (lowercase, underscored) to human names.
SOURCE_TAG_TO_LABEL = {
    "openalex": "OpenAlex",
    "dblp": "DBLP",
    "openbu": "OpenBU",
    "pubmed": "PubMed",
    "nih_reporter": "NIH Reporter",
    "semantic_scholar": "Semantic Scholar",
    "ssrn": "SSRN",
    "scholarly_commons": "Scholarly Commons",
    "crossref": "CrossRef",
    "nsf_awards": "NSF Awards",
    "biorxiv_medrxiv": "bioRxiv/medRxiv",
}


def compute_counts(master: list[dict], roster: list[dict]) -> dict:
    """Compute every number that appears in README/repo description."""
    paper_count = len(master)

    # Schools: distinct named bu_schools (excluding *unspecified*)
    schools = set()
    for p in master:
        for s in p.get("bu_schools") or []:
            if s and "unspecified" not in s.lower():
                schools.add(s)

    # Roster
    roster_count = len(roster)
    roster_with_oaid = sum(1 for r in roster if r.get("openalex_id"))

    # Source mention counts (from data tags)
    src_mentions = Counter()
    for p in master:
        for s in p.get("all_sources") or []:
            label = SOURCE_TAG_TO_LABEL.get(s, s)
            src_mentions[label] += 1

    # DBLP-specific count
    dblp_papers = src_mentions.get("DBLP", 0)

    return {
        "paper_count": paper_count,
        "school_count": len(schools),
        "roster_count": roster_count,
        "roster_with_oaid": roster_with_oaid,
        "src_mentions": dict(src_mentions),
        "dblp_papers": dblp_papers,
        "n_data_sources": len(DATA_SOURCES),
        "model": CLASSIFIER_DISPLAY_NAME,
    }


def update_readme(text: str, counts: dict) -> str:
    """Patch every templated count in README.md."""
    pc = f"{counts['paper_count']:,}"
    sc = counts["school_count"]
    dblp = f"{counts['dblp_papers']:,}"

    # Headline: "Currently **N papers** across M schools and departments."
    text = re.sub(
        r"Currently\s+\*\*[\d,]+\s+papers\*\*\s+across\s+\d+\s+schools\s+and\s+departments\.",
        f"Currently **{pc} papers** across {sc} schools and departments.",
        text,
    )

    # DBLP coverage line: "the DBLP source, which contributes N papers"
    text = re.sub(
        r"DBLP source, which contributes\s+[\d,]+\s+papers",
        f"DBLP source, which contributes {dblp} papers",
        text,
    )

    # Source coverage table mentions: "| **<Source>** | <count> |"
    def replace_mention_row(match: re.Match) -> str:
        label = match.group(1)
        # Look up canonical label (strip whitespace)
        canon = label.strip()
        n = counts["src_mentions"].get(canon)
        if n is None:
            return match.group(0)  # leave unchanged
        return f"| **{label}** | {n:,} |"

    text = re.sub(
        r"\|\s+\*\*([^*]+)\*\*\s+\|\s+(?:[\d,]+|--)\s+\|",
        replace_mention_row,
        text,
    )

    # "exceed the N deduplicated paper count"
    text = re.sub(
        r"exceed\s+the\s+[\d,]+\s+deduplicated\s+paper\s+count",
        f"exceed the {pc} deduplicated paper count",
        text,
    )

    # "Master dataset (N papers)"
    text = re.sub(
        r"Master dataset \([\d,]+ papers\)",
        f"Master dataset ({pc} papers)",
        text,
    )

    # "Faculty roster (N entries)"
    text = re.sub(
        r"Faculty roster \([\d,]+ entries\)",
        f"Faculty roster ({counts['roster_count']:,} entries)",
        text,
    )

    # "faculty roster of N entries" (in methodology section)
    text = re.sub(
        r"faculty roster of [\d,]+ entries",
        f"faculty roster of {counts['roster_count']:,} entries",
        text,
    )

    # "5,896-entry faculty roster" form
    text = re.sub(
        r"[\d,]+-entry faculty roster",
        f"{counts['roster_count']:,}-entry faculty roster",
        text,
    )

    return text


def make_repo_description(counts: dict) -> str:
    """Standard one-line GitHub repo description."""
    pc = f"{counts['paper_count']:,}"
    return (
        f"{pc} AI-related papers by Boston University faculty, searchable web "
        f"app, auto-updated monthly via {counts['n_data_sources']} sources, "
        f"classified by Claude {counts['model']}"
    )


def update_gh_description(desc: str, dry_run: bool = False) -> bool:
    """Update the GitHub repo description via `gh repo edit`. Returns True on success."""
    if dry_run:
        print(f"[dry-run] gh repo edit --description {desc!r}")
        return True
    try:
        subprocess.run(
            ["gh", "repo", "edit", "--description", desc],
            check=True,
            capture_output=True,
            text=True,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        msg = getattr(e, "stderr", str(e))
        print(f"WARN: could not update GitHub repo description: {msg}", file=sys.stderr)
        return False


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true", help="show changes without writing")
    p.add_argument("--no-gh", action="store_true", help="skip GitHub repo description update")
    args = p.parse_args()

    master = json.loads(MASTER_PATH.read_text())
    roster = json.loads(ROSTER_PATH.read_text())
    counts = compute_counts(master, roster)

    print(f"Paper count:    {counts['paper_count']:,}")
    print(f"Schools (named): {counts['school_count']}")
    print(f"Roster:         {counts['roster_count']:,} ({counts['roster_with_oaid']:,} with OAID)")
    print(f"DBLP papers:    {counts['dblp_papers']:,}")
    print(f"Sources (data): {sum(counts['src_mentions'].values()):,} mentions across "
          f"{len(counts['src_mentions'])} tags; {counts['n_data_sources']} canonical sources")
    print(f"Model:          {counts['model']}")

    # README
    old = README_PATH.read_text()
    new = update_readme(old, counts)
    if new == old:
        print("README.md: no changes needed")
    else:
        if args.dry_run:
            print("\n[dry-run] README.md would change:")
            import difflib
            for line in difflib.unified_diff(
                old.splitlines(keepends=True),
                new.splitlines(keepends=True),
                fromfile="README.md (current)",
                tofile="README.md (proposed)",
                n=1,
            ):
                print(line, end="")
        else:
            README_PATH.write_text(new)
            print("README.md: updated")

    # GitHub repo description
    if not args.no_gh:
        desc = make_repo_description(counts)
        print(f"\nGitHub repo description:\n  {desc}")
        update_gh_description(desc, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
