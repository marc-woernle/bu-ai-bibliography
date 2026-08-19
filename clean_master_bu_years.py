#!/usr/bin/env python3
"""One-shot: remove papers already in master that were never BU's to begin with.

The problem this cleans up
--------------------------
DBLP carries no affiliation data at all. The harvester matched author names
against the faculty roster, and a match imported that person's ENTIRE career --
the decades before they arrived at BU and, far more often, everything they
published after they left. The people are real BU alumni; the papers are not
BU research.

Measured against the BU author registry, 858 of the 1,586 DBLP papers in master
sit outside their author's documented BU years. The largest contributors are
exactly who you would expect:

    67  Mari Ostendorf          BU 1988-2002, then Washington
    61  Vladimir Pavlovic       BU 2002-2003, then Rutgers
    55  Mac Schwager            BU 2011-2016, then Stanford
    42  Panagiotis Papapetrou   BU 2006-2009, then Stockholm
    41  Morteza Lahijanian      BU 2008-2015, then Colorado
    30  Christopher Amato       BU 2011-2018, then Northeastern
    14  Li Fei-Fei              never at BU under this identity

verify_bu_authors now applies this check to every name-matched source at
harvest time, so nothing new can arrive this way. This script is for what is
already merged.

Where the removed papers go
---------------------------
Two places, because throwing them away would mean re-harvesting and re-deciding
them every month:

  * data/removed_outside_bu_years.json -- the full records, so the removal is
    reviewable and reversible.
  * data/non_bu_ai_index.json -- DOI and title fingerprint, so the harvester
    skips them on sight. They are AI papers; they are just not BU's, which is
    exactly what that index means.

Run:
  python clean_master_bu_years.py              # dry run, writes nothing
  python clean_master_bu_years.py --apply      # writes master and both indexes
"""
import argparse
import collections
import json
import shutil
import sys

from update_pipeline import (
    MASTER_PATH,
    bu_name_verdict,
    load_bu_author_registry,
    load_master,
    load_non_bu_ai_index,
    registry_year_windows,
    safe_fingerprint,
    save_master,
    _reg_name_key,
)
from utils import normalize_doi

ARCHIVE_PATH = "data/removed_outside_bu_years.json"
NON_BU_PATH = "data/non_bu_ai_index.json"
# Only sources that carry no affiliation data of their own. A paper from a
# source that DID assert a BU affiliation is not this bug, whatever the years
# say, and is left alone.
NAME_MATCHED_SOURCES = {"dblp"}


def paper_verdict(paper: dict, windows: dict):
    """False only when a BU author is named and every named author's own
    documented BU years exclude this paper's year. Any single author who checks
    out keeps the paper: a real collaboration should survive."""
    year = paper.get("year")
    names = [a.get("name") for a in (paper.get("authors") or []) if a.get("is_bu")]
    if not year or not names:
        return None
    verdict = None
    for n in names:
        r = bu_name_verdict(n, year, windows)
        if r is True:
            return True
        if r is False:
            verdict = False
    return verdict


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="actually write the files")
    args = ap.parse_args()

    windows, _ = registry_year_windows(load_bu_author_registry())
    if not windows:
        print("BU author registry is empty; run resolve_bu_authors.py first.", file=sys.stderr)
        return 1

    master = load_master()
    keep, drop = [], []
    for p in master:
        if p.get("source") in NAME_MATCHED_SOURCES and paper_verdict(p, windows) is False:
            drop.append(p)
        else:
            keep.append(p)

    print(f"master {len(master):,} -> {len(keep):,}  ({len(drop):,} removed)")
    by_author = collections.Counter()
    for p in drop:
        for a in p.get("authors") or []:
            if a.get("is_bu"):
                by_author[a.get("name")] += 1
    print("\nremoved, by author (BU years in brackets):")
    for n, k in by_author.most_common(20):
        w = windows.get(_reg_name_key(n)) or {}
        print(f"  {k:4}  {n:30} BU {w.get('first')}-{w.get('last')}")
    years = collections.Counter(p.get("year") for p in drop)
    print("\nremoved, by year:", " ".join(f"{y}:{n}" for y, n in sorted(years.items()) if y and y >= 2015))

    if not args.apply:
        print("\nDry run. Nothing written. Re-run with --apply.")
        return 0

    shutil.copy(MASTER_PATH, MASTER_PATH + ".pre_bu_year_cleanup")
    with open(ARCHIVE_PATH, "w") as f:
        json.dump(drop, f, ensure_ascii=False)
    print(f"\narchived {len(drop):,} removed papers to {ARCHIVE_PATH}")

    dois, fps = load_non_bu_ai_index()
    before = len(dois) + len(fps)
    for p in drop:
        d = normalize_doi(p.get("doi", "") or "")
        fp = safe_fingerprint(p.get("title", "") or "")
        if d:
            dois.add(d)
        if fp:
            fps.add(fp)
    with open(NON_BU_PATH, "w") as f:
        json.dump({"dois": sorted(dois), "fingerprints": sorted(fps)}, f)
    print(f"non-BU AI index: {before:,} -> {len(dois)+len(fps):,} entries; they will not be re-harvested")

    save_master(keep)
    print(f"master written: {len(keep):,} papers (previous copy at {MASTER_PATH}.pre_bu_year_cleanup)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
