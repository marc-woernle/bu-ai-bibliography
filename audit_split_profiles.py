#!/usr/bin/env python3
"""
Audit OpenAlex for split-author profiles in the faculty roster.

OpenAlex sometimes assigns the same person multiple author IDs — e.g. when
their affiliation history fragments, a name disambiguation pass mis-merges,
or a venue uses a slightly different display form. The roster only carries
ONE openalex_id per entry, so when a faculty member has a split, papers
authored under the alternate OAID are missed by ROR-based harvest and
silently absent from master.

This audit, for every roster entry with an OAID, queries OpenAlex
`/authors?search=<name>&filter=affiliations.institution.ror:<BU_ROR>` to
find every BU-affiliated profile bearing that name. Profiles with non-trivial
overlap (works_count >= 5) get proposed as alternates.

Output: data/split_profiles_audit.json — a JSON report listing each
candidate alternate OAID, its name, last_known_institutions, and works_count.
With --apply, alternates are written back into roster entries as
`alternate_openalex_ids: [...]`.

Usage:
  python audit_split_profiles.py             # dry-run, write report only
  python audit_split_profiles.py --apply     # also patch the roster
  python audit_split_profiles.py --limit=50  # smoke test
"""
from __future__ import annotations
import argparse
import json
import re
import time
import unicodedata
from pathlib import Path

import requests

from config import BU_ROR_ID, CONTACT_EMAIL

ROSTER_PATH = Path("data/bu_faculty_roster_verified.json")
OUT_PATH = Path("data/split_profiles_audit.json")
HEADERS = {"User-Agent": f"BU-AI-Bibliography/1.0 (mailto:{CONTACT_EMAIL})"}

# Conservative threshold: only propose alternates that have at least this many
# works. Below this, a duplicate profile is more likely to be a stub OpenAlex
# created by mistake than a real split career.
MIN_WORKS = 10

# Boston-area institutions that flag a candidate as plausibly the same person.
# Common names (Adam Smith, John Smith) match many candidates BU's ROR filter
# returns; require Boston-leaning last_known to filter out look-alikes.
BU_PROXIMITY = (
    "boston univ", "bumc", "boston med", "boston public",
    "boston children", "bidmc", "beth israel", "harvard",
    "broad institute", "mass general", "brigham", "tufts",
)


def _normalize_name(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _name_match(roster_name: str, candidate_name: str) -> bool:
    """Conservative name match: same surname AND first-name overlap."""
    rn = _normalize_name(roster_name).split()
    cn = _normalize_name(candidate_name).split()
    if not rn or not cn:
        return False
    if rn[-1] != cn[-1]:
        return False
    # First-name overlap: either matches in full, or one is a prefix of the
    # other. Handles "Chris" vs "Christopher", "C." vs "Christopher".
    rf, cf = rn[0], cn[0]
    if rf == cf:
        return True
    if rf.startswith(cf) or cf.startswith(rf):
        return True
    return False


def find_candidate_alternates(name: str, primary_oaid: str,
                              session: requests.Session) -> list[dict]:
    """Query OpenAlex for BU-affiliated authors matching this name."""
    params = {
        "search": name,
        "filter": f"affiliations.institution.ror:{BU_ROR_ID}",
        "per-page": 25,
        "select": "id,display_name,display_name_alternatives,works_count,last_known_institutions",
        "mailto": CONTACT_EMAIL,
    }
    try:
        r = session.get("https://api.openalex.org/authors", params=params,
                        headers=HEADERS, timeout=20)
    except requests.RequestException:
        return []
    if r.status_code != 200:
        return []
    out = []
    for it in r.json().get("results", []) or []:
        oa_id = it.get("id")
        if not oa_id or oa_id == primary_oaid:
            continue
        if (it.get("works_count") or 0) < MIN_WORKS:
            continue
        cand_names = [it.get("display_name") or ""] + list(it.get("display_name_alternatives") or [])
        if not any(_name_match(name, cn) for cn in cand_names if cn):
            continue
        last_inst = [li.get("display_name") or "" for li in (it.get("last_known_institutions") or [])]
        # Require Boston-area last_known. OpenAlex's BU-ROR filter alone is too
        # loose for common names like "Adam Smith" — many candidates have BU in
        # one historic affiliation but are clearly other people. A Boston-area
        # last-known confirms ongoing local presence.
        last_blob = " ".join(li.lower() for li in last_inst)
        if last_inst and not any(p in last_blob for p in BU_PROXIMITY):
            continue
        out.append({
            "openalex_id": oa_id,
            "display_name": it.get("display_name"),
            "works_count": it.get("works_count"),
            "last_known_institutions": last_inst,
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="patch alternate_openalex_ids back into the roster")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--rate", type=float, default=0.12,
                    help="seconds between calls (polite pool ~10 rps)")
    args = ap.parse_args()

    roster = json.loads(ROSTER_PATH.read_text())
    targets = [r for r in roster if r.get("openalex_id") and r.get("name")]
    if args.limit:
        targets = targets[: args.limit]
    print(f"Auditing {len(targets)} roster entries with OAIDs...")

    session = requests.Session()
    splits = []
    n_with_alts = 0
    for i, r in enumerate(targets):
        cands = find_candidate_alternates(r["name"], r["openalex_id"], session)
        if cands:
            n_with_alts += 1
            splits.append({
                "name": r["name"],
                "school": r.get("school"),
                "primary_openalex_id": r["openalex_id"],
                "alternates": cands,
            })
        time.sleep(args.rate)
        if (i + 1) % 100 == 0:
            print(f"  {i+1}/{len(targets)}  with-alternates={n_with_alts}")

    OUT_PATH.write_text(json.dumps({
        "audited": len(targets), "with_alternates": n_with_alts, "splits": splits,
    }, indent=2, ensure_ascii=False))
    print(f"\nDone. Faculty with split profiles: {n_with_alts} of {len(targets)}")
    print(f"Output: {OUT_PATH}")

    if splits:
        print("\nFirst 10:")
        for s in splits[:10]:
            print(f"  {s['name']!r:32} school={s['school']!r}")
            for alt in s["alternates"][:3]:
                li = ", ".join(alt["last_known_institutions"][:2]) or "?"
                print(f"      alt {alt['openalex_id'].split('/')[-1]:14} works={alt['works_count']:5} ({li})")

    if args.apply:
        # Write alternates back into roster
        by_pri = {s["primary_openalex_id"]: [a["openalex_id"] for a in s["alternates"]]
                  for s in splits}
        n_patched = 0
        for entry in roster:
            pri = entry.get("openalex_id")
            if pri in by_pri:
                existing = entry.get("alternate_openalex_ids") or []
                merged = list(dict.fromkeys(existing + by_pri[pri]))
                if merged != existing:
                    entry["alternate_openalex_ids"] = merged
                    n_patched += 1
        ROSTER_PATH.write_text(json.dumps(roster, ensure_ascii=False, indent=2))
        print(f"\nPatched {n_patched} roster entries with alternate_openalex_ids")


if __name__ == "__main__":
    main()
