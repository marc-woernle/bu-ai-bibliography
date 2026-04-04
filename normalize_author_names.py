#!/usr/bin/env python3
"""
Normalize BU author names across all papers.

For each BU author, find the canonical identity via:
  1. OpenAlex author ID → roster entry → roster name
  2. Alt-names cache → OAID → roster entry → roster name
  3. Full-name roster match → roster name
  4. No match → keep most common form, apply formatting rules

Updates authors[].name and rebuilds bu_author_names[].

Usage:
    python normalize_author_names.py              # Run on full dataset
    python normalize_author_names.py --dry-run    # Show stats without saving
"""

import json
import re
import argparse
import unicodedata
from pathlib import Path
from collections import defaultdict, Counter

MASTER_PATH = Path("data/sonnet_classification_bu_verified.json")
ROSTER_PATH = Path("data/bu_faculty_roster_verified.json")
ALTNAMES_CACHE_PATH = Path("data/openalex_bu_authors_cache.json")


def _normalize_for_match(name: str) -> str:
    """Normalize name for matching (lowercase, no accents, no punctuation)."""
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    name = name.lower().strip()
    name = re.sub(r"[^a-z\s-]", "", name)
    return re.sub(r"\s+", " ", name)


def _name_key(name: str) -> str:
    """Build 'last first' key for roster matching."""
    parts = _normalize_for_match(name).split()
    if len(parts) < 2:
        return _normalize_for_match(name)
    return f"{parts[-1]} {parts[0]}"


def build_canonical_map(papers, roster, altnames_cache):
    """Build mapping from BU author name -> canonical name.

    Returns:
        canonical: dict mapping (paper_idx, author_idx) -> canonical_name
        stats: dict with counts
    """
    # Build roster indexes
    roster_by_oaid = {}
    roster_by_fullname = defaultdict(list)
    for r in roster:
        if r.get("openalex_id"):
            roster_by_oaid[r["openalex_id"]] = r["name"]
        fkey = _name_key(r["name"])
        roster_by_fullname[fkey].append(r["name"])

    # Build alt_names -> OAID index (only unambiguous, roster-matching)
    altname_to_roster = {}  # normalized_name -> roster_canonical_name
    oaid_name_index = defaultdict(set)  # normalized_name -> set of OAIDs
    for entry in altnames_cache:
        oa_id = entry.get("id", "")
        for alt in entry.get("alt_names", []):
            norm = _normalize_for_match(alt)
            if norm:
                oaid_name_index[norm].add(oa_id)

    # Only keep unambiguous alt_name -> single OAID that's in roster
    for norm_name, oaids in oaid_name_index.items():
        if len(oaids) == 1:
            oa_id = next(iter(oaids))
            if oa_id in roster_by_oaid:
                altname_to_roster[norm_name] = roster_by_oaid[oa_id]

    stats = {
        "total_bu_authors": 0,
        "matched_oaid": 0,
        "matched_altnames": 0,
        "matched_fullname": 0,
        "unmatched": 0,
        "names_changed": 0,
    }

    canonical = {}  # (paper_idx, author_idx) -> canonical_name

    for pi, paper in enumerate(papers):
        for ai, author in enumerate(paper.get("authors", [])):
            if not author.get("is_bu"):
                continue
            stats["total_bu_authors"] += 1

            old_name = author.get("name", "")
            new_name = None

            old_last = _normalize_for_match(old_name).split()[-1] if _normalize_for_match(old_name).split() else ""

            # Tier 1: OAID -> roster (with last-name safety check)
            oa_id = author.get("openalex_id")
            if oa_id and oa_id in roster_by_oaid:
                candidate = roster_by_oaid[oa_id]
                cand_last = _normalize_for_match(candidate).split()[-1] if _normalize_for_match(candidate).split() else ""
                if old_last == cand_last:
                    new_name = candidate
                    stats["matched_oaid"] += 1

            # Tier 2: alt_names cache -> roster (with last-name safety check)
            if new_name is None:
                norm = _normalize_for_match(old_name)
                if norm in altname_to_roster:
                    candidate = altname_to_roster[norm]
                    cand_last = _normalize_for_match(candidate).split()[-1] if _normalize_for_match(candidate).split() else ""
                    if old_last == cand_last:
                        new_name = candidate
                        stats["matched_altnames"] += 1

            # Tier 3: full-name roster match
            if new_name is None:
                fkey = _name_key(old_name)
                matches = roster_by_fullname.get(fkey, [])
                if len(matches) == 1:
                    new_name = matches[0]
                    stats["matched_fullname"] += 1

            # Tier 4: no match — keep as-is
            if new_name is None:
                stats["unmatched"] += 1
                continue

            if new_name != old_name:
                stats["names_changed"] += 1

            canonical[(pi, ai)] = new_name

    return canonical, stats


def apply_canonical_names(papers, canonical):
    """Apply canonical names to paper author records and rebuild bu_author_names."""
    for (pi, ai), name in canonical.items():
        papers[pi]["authors"][ai]["name"] = name

    # Rebuild bu_author_names from is_bu authors
    for paper in papers:
        bu_names = []
        for author in paper.get("authors", []):
            if author.get("is_bu"):
                bu_names.append(author["name"])
        paper["bu_author_names"] = bu_names


def main():
    parser = argparse.ArgumentParser(description="Normalize BU author names")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print(f"Loading data...")
    with open(MASTER_PATH) as f:
        papers = json.load(f)
    with open(ROSTER_PATH) as f:
        roster = json.load(f)
    with open(ALTNAMES_CACHE_PATH) as f:
        altnames_cache = json.load(f)

    print(f"Papers: {len(papers)}, Roster: {len(roster)}, Alt-names cache: {len(altnames_cache)}")

    canonical, stats = build_canonical_map(papers, roster, altnames_cache)

    print(f"\n=== Name Matching Stats ===")
    print(f"Total BU author records: {stats['total_bu_authors']}")
    print(f"  Matched by OAID:       {stats['matched_oaid']} ({100*stats['matched_oaid']/stats['total_bu_authors']:.1f}%)")
    print(f"  Matched by alt-names:  {stats['matched_altnames']} ({100*stats['matched_altnames']/stats['total_bu_authors']:.1f}%)")
    print(f"  Matched by full-name:  {stats['matched_fullname']} ({100*stats['matched_fullname']/stats['total_bu_authors']:.1f}%)")
    print(f"  Unmatched:             {stats['unmatched']} ({100*stats['unmatched']/stats['total_bu_authors']:.1f}%)")
    print(f"  Names that will change: {stats['names_changed']}")

    # Show some examples of name changes
    changes = []
    for (pi, ai), new_name in canonical.items():
        old_name = papers[pi]["authors"][ai]["name"]
        if old_name != new_name:
            changes.append((old_name, new_name))

    if changes:
        print(f"\nSample name changes (first 20):")
        for old, new in changes[:20]:
            print(f"  '{old}' -> '{new}'")

    if not args.dry_run:
        apply_canonical_names(papers, canonical)

        # Verify consistency
        inconsistent = 0
        for paper in papers:
            bu_from_authors = [a["name"] for a in paper.get("authors", []) if a.get("is_bu")]
            bu_field = paper.get("bu_author_names", [])
            if bu_from_authors != bu_field:
                inconsistent += 1
        print(f"\nConsistency check: {inconsistent} papers with mismatched bu_author_names (should be 0)")

        # Count unique BU author names after normalization
        all_bu_names = set()
        for p in papers:
            for n in p.get("bu_author_names", []):
                all_bu_names.add(n)
        print(f"Unique BU author names: {len(all_bu_names)} (was 9,364)")

        print(f"\nSaving {MASTER_PATH}...")
        with open(MASTER_PATH, "w") as f:
            json.dump(papers, f, indent=2, ensure_ascii=False)
        print("Done.")
    else:
        print("\n(dry run — no changes saved)")


if __name__ == "__main__":
    main()
