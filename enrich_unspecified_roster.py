#!/usr/bin/env python3
"""
One-shot: resolve 792 'Boston University (unspecified)' roster entries
by querying OpenAlex for department-level affiliation info.

For each entry with an OAID, fetches their BU-affiliated works and
extracts raw_affiliation_strings to determine which BU school they belong to.
"""

import json
import logging
import os
import time
from collections import Counter

import requests

# Force fresh import of school_mapper with extended patterns
import importlib
import school_mapper
importlib.reload(school_mapper)
from school_mapper import classify_affiliation
from config import BU_ROR_ID, CONTACT_EMAIL

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("enrich_roster")

ROSTER_PATH = "data/bu_faculty_roster_verified.json"
OPENALEX_BASE = "https://api.openalex.org"


def _headers():
    return {"User-Agent": f"BU-AI-Bibliography/1.0 (mailto:{CONTACT_EMAIL})"}


def fetch_affiliations(openalex_id: str, max_works: int = 10) -> list[str]:
    """Fetch raw affiliation strings for an author's BU-affiliated works."""
    params = {
        "filter": (
            f"authorships.author.id:{openalex_id},"
            f"authorships.institutions.ror:{BU_ROR_ID}"
        ),
        "per_page": max_works,
        "select": "id,authorships",
    }
    if CONTACT_EMAIL:
        params["mailto"] = CONTACT_EMAIL

    try:
        resp = requests.get(
            f"{OPENALEX_BASE}/works", params=params, headers=_headers(), timeout=15,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning(f"  API error for {openalex_id}: {e}")
        return []

    raw_affs = []
    for work in resp.json().get("results", []):
        for authorship in work.get("authorships", []):
            if authorship.get("author", {}).get("id") == openalex_id:
                for s in authorship.get("raw_affiliation_strings", []):
                    if s:
                        raw_affs.append(s)
    return raw_affs


def resolve_school(raw_affiliations: list[str]) -> str | None:
    """Determine school from raw affiliation strings using majority vote."""
    school_votes = Counter()
    for aff in raw_affiliations:
        result = classify_affiliation(aff)
        if result:
            school, _ = result
            if not school.endswith("(unspecified)") and school != "CAS (unspecified department)":
                school_votes[school] += 1

    if not school_votes:
        return None

    top_school, top_count = school_votes.most_common(1)[0]
    # Require at least 2 votes OR 1 vote if only 1 affiliation found
    if top_count >= 2 or (top_count == 1 and len(raw_affiliations) <= 2):
        return top_school
    return None


def enrich_unspecified(roster: list[dict]) -> tuple[list[dict], int]:
    """Enrich roster entries with school='Boston University (unspecified)' that have OAIDs.
    Returns (updated_roster, count_enriched).
    """
    enriched = 0
    for entry in roster:
        if entry.get("school") != "Boston University (unspecified)":
            continue
        if not entry.get("openalex_id"):
            continue

        time.sleep(0.1)
        raw_affs = fetch_affiliations(entry["openalex_id"])
        if not raw_affs:
            continue

        school = resolve_school(raw_affs)
        if school:
            entry["school"] = school
            enriched += 1

    return roster, enriched


def main():
    with open(ROSTER_PATH) as f:
        roster = json.load(f)

    unspec = [
        (i, r) for i, r in enumerate(roster)
        if r.get("school") == "Boston University (unspecified)" and r.get("openalex_id")
    ]
    logger.info(f"Unspecified entries with OAID: {len(unspec)}")

    resolved = 0
    no_works = 0
    ambiguous = 0
    school_counts = Counter()

    for idx, (roster_idx, entry) in enumerate(unspec):
        time.sleep(0.1)  # Rate limit
        oaid = entry["openalex_id"]
        raw_affs = fetch_affiliations(oaid)

        if not raw_affs:
            no_works += 1
            if idx < 5:
                logger.info(f"  {entry['name']}: no BU affiliations found")
            continue

        school = resolve_school(raw_affs)
        if school:
            roster[roster_idx]["school"] = school
            school_counts[school] += 1
            resolved += 1
            if idx < 20 or idx % 50 == 0:
                logger.info(f"  {entry['name']}: → {school} (from {len(raw_affs)} affiliations)")
        else:
            ambiguous += 1
            if idx < 5:
                logger.info(f"  {entry['name']}: ambiguous ({Counter(classify_affiliation(a)[0] if classify_affiliation(a) else 'none' for a in raw_affs).most_common(3)})")

        if (idx + 1) % 100 == 0:
            logger.info(f"  Progress: {idx + 1}/{len(unspec)} — resolved {resolved}")

    # Save
    tmp = ROSTER_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(roster, f, ensure_ascii=False, indent=2)
    os.rename(tmp, ROSTER_PATH)

    print(f"\n=== Enrichment Summary ===")
    print(f"  Processed:  {len(unspec)}")
    print(f"  Resolved:   {resolved}")
    print(f"  No works:   {no_works}")
    print(f"  Ambiguous:  {ambiguous}")
    print(f"\n  Schools assigned:")
    for school, count in school_counts.most_common():
        print(f"    {school:<55} {count:>4}")


if __name__ == "__main__":
    main()
