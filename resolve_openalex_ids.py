#!/usr/bin/env python3
"""
Resolve OpenAlex author IDs for the BU faculty roster.

Strategy: Fetch ALL BU-affiliated authors from OpenAlex (~98K),
then match against our 5,190 faculty roster locally by name.
This is much faster than 5,190 individual API queries.
"""

import json
import re
import time
import unicodedata
from collections import defaultdict
from pathlib import Path

import requests

BU_ROR = "https://ror.org/05qwgg493"
ROSTER_PATH = Path("data/bu_faculty_roster_verified.json")
CACHE_PATH = Path("data/openalex_bu_authors_cache.json")
EMAIL = "mwoernle@bu.edu"  # For polite pool

def normalize_name(name: str) -> str:
    """Normalize a name for matching: lowercase, strip accents, remove punctuation."""
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    name = name.lower().strip()
    name = re.sub(r"[^a-z\s]", "", name)
    name = re.sub(r"\s+", " ", name)
    return name

def name_parts(name: str) -> tuple:
    """Extract (last, first) from a name string."""
    parts = normalize_name(name).split()
    if len(parts) < 2:
        return (name.lower(), "")
    return (parts[-1], parts[0])

def name_key(name: str) -> str:
    """Create a matching key: 'last first'."""
    last, first = name_parts(name)
    return f"{last} {first}"

def name_key_initial(name: str) -> str:
    """Create a loose key: 'last f' (first initial only)."""
    last, first = name_parts(name)
    return f"{last} {first[0]}" if first else last


def _verify_bu_in_affiliations(oa_id: str) -> bool:
    """Live-query OpenAlex to confirm BU's ROR appears in the author's
    affiliations history. Catches stale-cache cases where the cached author
    set was assembled before OpenAlex de-merged a wrongly-merged profile.
    Returns True on confirmed BU history, False on no-BU or any error.
    """
    short = oa_id.rsplit("/", 1)[-1]
    try:
        r = requests.get(
            f"https://api.openalex.org/authors/{short}?mailto={EMAIL}",
            headers={"User-Agent": f"BU-AI-Bibliography/1.0 (mailto:{EMAIL})"},
            timeout=15,
        )
        if r.status_code != 200:
            return False
        for a in r.json().get("affiliations") or []:
            inst = a.get("institution") or {}
            if (inst.get("ror") or "") == BU_ROR:
                return True
    except requests.RequestException:
        return False
    return False


def fetch_all_bu_authors() -> list[dict]:
    """Fetch all BU-affiliated authors from OpenAlex using cursor pagination."""
    if CACHE_PATH.exists():
        print(f"Loading cached authors from {CACHE_PATH}")
        with open(CACHE_PATH) as f:
            authors = json.load(f)
        print(f"  Loaded {len(authors)} cached authors")
        return authors

    print("Fetching ALL BU-affiliated authors from OpenAlex...")
    authors = []
    cursor = "*"
    page = 0

    while cursor:
        url = (
            f"https://api.openalex.org/authors"
            f"?filter=affiliations.institution.ror:{BU_ROR}"
            f"&select=id,display_name,display_name_alternatives,works_count,last_known_institutions"
            f"&per_page=200&cursor={cursor}&mailto={EMAIL}"
        )
        resp = requests.get(url, timeout=30)
        if resp.status_code == 429:
            print("  Rate limited, waiting 5s...")
            time.sleep(5)
            continue
        if resp.status_code != 200:
            print(f"  Error {resp.status_code}: {resp.text[:200]}")
            break

        data = resp.json()
        results = data.get("results", [])
        if not results:
            break

        for r in results:
            authors.append({
                "id": r["id"],
                "name": r["display_name"],
                "alt_names": r.get("display_name_alternatives", []),
                "works_count": r.get("works_count", 0),
                "last_institution": (
                    r["last_known_institutions"][0].get("display_name", "")
                    if r.get("last_known_institutions") else ""
                ),
            })

        cursor = data.get("meta", {}).get("next_cursor")
        page += 1
        if page % 50 == 0:
            print(f"  Page {page}: {len(authors)} authors so far...")

        # Polite rate: ~10 req/s
        time.sleep(0.1)

    print(f"  Total fetched: {len(authors)}")

    # Cache for reuse
    CACHE_PATH.parent.mkdir(exist_ok=True)
    with open(CACHE_PATH, "w") as f:
        json.dump(authors, f)
    print(f"  Cached to {CACHE_PATH}")

    return authors


def build_openalex_index(authors: list[dict]) -> dict:
    """Build name -> list of OpenAlex authors index."""
    by_full = defaultdict(list)
    by_initial = defaultdict(list)

    for a in authors:
        # Index by display_name
        key = name_key(a["name"])
        by_full[key].append(a)
        ikey = name_key_initial(a["name"])
        by_initial[ikey].append(a)

        # Also index alternative names
        for alt in a.get("alt_names", []):
            akey = name_key(alt)
            if akey != key:
                by_full[akey].append(a)
            aikey = name_key_initial(alt)
            if aikey != ikey:
                by_initial[aikey].append(a)

    return {"full": by_full, "initial": by_initial}


def match_faculty(roster: list[dict], index: dict) -> list[dict]:
    """Match each roster entry against the OpenAlex index."""
    matched = 0
    ambiguous = 0
    no_match = 0
    updated = []

    for fac in roster:
        fkey = name_key(fac["name"])
        fikey = name_key_initial(fac["name"])

        candidates = index["full"].get(fkey, [])

        if not candidates:
            # Try initial-only match
            candidates = index["initial"].get(fikey, [])

        chosen = None
        chosen_rare = None
        chosen_ambiguous = False

        if len(candidates) == 1:
            chosen = candidates[0]
            chosen_rare = True
        elif len(candidates) > 1:
            # Multiple matches - pick the one with most works at BU.
            # Prefer someone whose last_institution is BU.
            bu_cands = [c for c in candidates if "boston university" in c.get("last_institution", "").lower()]
            if len(bu_cands) == 1:
                chosen = bu_cands[0]
                chosen_rare = False  # Common name, multiple matches
            elif bu_cands:
                chosen = max(bu_cands, key=lambda x: x["works_count"])
                chosen_rare = False
                chosen_ambiguous = True
            else:
                chosen = max(candidates, key=lambda x: x["works_count"])
                chosen_rare = False
                chosen_ambiguous = True

        if chosen is not None:
            # Live-verify BU presence in affiliations. Cached candidate sets can be
            # stale (OpenAlex periodically de-merges wrongly-merged author profiles),
            # so re-check before assigning. Without this, a small fraction of OAIDs
            # end up pointing at non-BU authors who happen to share a name
            # (Apr 2026 audit found 8 such cases out of 897 prior assignments).
            if _verify_bu_in_affiliations(chosen["id"]):
                fac["openalex_id"] = chosen["id"]
                fac["openalex_works"] = chosen["works_count"]
                fac["is_rare_name"] = chosen_rare
                matched += 1
                if chosen_ambiguous:
                    ambiguous += 1
            else:
                fac["openalex_id"] = None
                fac["openalex_works"] = 0
                fac["is_rare_name"] = None
                no_match += 1
            time.sleep(0.1)  # Polite pacing for the verification call.
        else:
            fac["openalex_id"] = None
            fac["openalex_works"] = 0
            fac["is_rare_name"] = None
            no_match += 1

        updated.append(fac)

    return updated, matched, ambiguous, no_match


def resolve_batch(roster: list[dict], cache_path: Path = CACHE_PATH) -> tuple[list[dict], int]:
    """Resolve OpenAlex IDs for roster entries that don't have one.
    Returns (updated_roster, count_resolved).
    Uses cached author list if available, otherwise fetches from OpenAlex.
    """
    needs_resolution = [f for f in roster if not f.get("openalex_id")]
    if not needs_resolution:
        return roster, 0

    # Temporarily override cache path if provided
    global CACHE_PATH
    old_cache = CACHE_PATH
    CACHE_PATH = cache_path

    authors = fetch_all_bu_authors()
    index = build_openalex_index(authors)
    _, matched, _, _ = match_faculty(needs_resolution, index)

    # Merge back: entries that already had OAIDs stay unchanged
    updated = []
    needs_iter = iter(needs_resolution)
    resolved_entry = {f["name"].lower(): f for f in needs_resolution}
    for f in roster:
        if not f.get("openalex_id") and f["name"].lower() in resolved_entry:
            updated.append(resolved_entry[f["name"].lower()])
        else:
            updated.append(f)

    CACHE_PATH = old_cache
    return updated, matched


def main():
    # Load roster
    with open(ROSTER_PATH) as f:
        roster = json.load(f)
    print(f"Roster: {len(roster)} faculty")

    # Fetch all BU authors
    authors = fetch_all_bu_authors()

    # Sanity check: how many have >0 works?
    active = sum(1 for a in authors if a["works_count"] > 0)
    print(f"\nOpenAlex BU authors: {len(authors)} total, {active} with works > 0")

    # Build index
    print("Building name index...")
    index = build_openalex_index(authors)
    print(f"  Full-name keys: {len(index['full'])}")
    print(f"  Initial keys: {len(index['initial'])}")

    # Match
    print("\nMatching roster against OpenAlex...")
    updated, matched, ambiguous, no_match = match_faculty(roster, index)

    print(f"\n{'='*50}")
    print(f"RESULTS")
    print(f"{'='*50}")
    print(f"  Matched:   {matched} ({matched/len(roster)*100:.1f}%)")
    print(f"  Ambiguous: {ambiguous} (matched but multiple candidates)")
    print(f"  No match:  {no_match} ({no_match/len(roster)*100:.1f}%)")

    # Breakdown by school
    from collections import Counter
    school_stats = defaultdict(lambda: {"matched": 0, "total": 0})
    for fac in updated:
        s = fac["school"]
        school_stats[s]["total"] += 1
        if fac.get("openalex_id"):
            school_stats[s]["matched"] += 1

    print(f"\nBy school:")
    for school in sorted(school_stats, key=lambda s: -school_stats[s]["total"]):
        st = school_stats[school]
        pct = st["matched"] / st["total"] * 100 if st["total"] else 0
        print(f"  {school}: {st['matched']}/{st['total']} ({pct:.0f}%)")

    # Sanity check: spot-check some known faculty
    print(f"\nSpot checks:")
    spot_checks = ["Ran Canetti", "Kate Saenko", "Mark Crovella", "Margrit Betke", "Azer Bestavros"]
    for name in spot_checks:
        matches = [f for f in updated if f["name"] == name]
        if matches:
            m = matches[0]
            print(f"  {name}: {m.get('openalex_id', 'NO ID')} (works: {m.get('openalex_works', 0)})")
        else:
            print(f"  {name}: NOT IN ROSTER")

    # Rare name analysis
    rare = sum(1 for f in updated if f.get("is_rare_name") is True)
    common = sum(1 for f in updated if f.get("is_rare_name") is False)
    print(f"\nName rarity: {rare} rare, {common} common (multiple OA matches)")

    # Save
    with open(ROSTER_PATH, "w") as f:
        json.dump(updated, f, indent=2)
    print(f"\nSaved updated roster to {ROSTER_PATH}")


if __name__ == "__main__":
    main()
