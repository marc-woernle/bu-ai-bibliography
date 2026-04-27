#!/usr/bin/env python3
"""
Audit roster entries from `openalex_resolve` source. For each one, query
OpenAlex for the full institutional history and check whether BU's ROR
appears anywhere. Entries with NO BU connection are wrong OAID assignments
(the resolver picked the highest-works candidate without verifying BU).

Output: data/openalex_resolve_audit.json with three lists:
  - confirmed_bu: OAID has BU in affiliations history
  - wrong_oaid:  OAID has zero BU connection — drop from roster
  - unreachable: API errors / no profile

Run:
  python audit_openalex_resolve.py             # full audit (~2 min)
  python audit_openalex_resolve.py --limit=20  # smoke test
"""
import argparse
import json
import time
from pathlib import Path

import requests

from config import BU_ROR_ID, CONTACT_EMAIL

ROSTER_PATH = Path("data/bu_faculty_roster_verified.json")
OUT_PATH = Path("data/openalex_resolve_audit.json")
HEADERS = {"User-Agent": f"BU-AI-Bibliography/1.0 (mailto:{CONTACT_EMAIL})"}


def check_oaid_has_bu(oa_id: str, session: requests.Session) -> tuple[str, dict]:
    """Returns (status, info). status is one of: bu, non_bu, unreachable."""
    short = oa_id.rsplit("/", 1)[-1]
    url = f"https://api.openalex.org/authors/{short}?mailto={CONTACT_EMAIL}"
    try:
        r = session.get(url, headers=HEADERS, timeout=15)
    except requests.RequestException as e:
        return ("unreachable", {"error": str(e)})
    if r.status_code == 404:
        return ("unreachable", {"error": "404 not found"})
    if r.status_code == 429:
        time.sleep(5)
        return check_oaid_has_bu(oa_id, session)
    if r.status_code != 200:
        return ("unreachable", {"error": f"http {r.status_code}"})

    data = r.json()
    affiliations = data.get("affiliations") or []
    last_known = data.get("last_known_institutions") or []
    bu_years = []
    other_insts = set()
    for a in affiliations:
        inst = a.get("institution") or {}
        ror = inst.get("ror") or ""
        name = inst.get("display_name") or ""
        if ror == BU_ROR_ID:
            bu_years.extend(a.get("years") or [])
        elif name:
            other_insts.add(name)
    info = {
        "name": data.get("display_name"),
        "works_count": data.get("works_count", 0),
        "bu_years": sorted(set(bu_years)) if bu_years else [],
        "last_known": [i.get("display_name") for i in last_known],
        "other_institutions": sorted(other_insts)[:10],
    }
    return ("bu" if bu_years else "non_bu", info)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--rate", type=float, default=0.12, help="seconds between calls")
    args = p.parse_args()

    roster = json.loads(ROSTER_PATH.read_text())
    targets = [e for e in roster if e.get("source_url") == "openalex_resolve"]
    if args.limit:
        targets = targets[: args.limit]
    print(f"Auditing {len(targets)} openalex_resolve roster entries...")

    confirmed = []  # BU in affiliations history
    wrong = []      # no BU at all
    unreachable = []

    s = requests.Session()
    for i, e in enumerate(targets):
        oa_id = e.get("openalex_id")
        if not oa_id:
            continue
        status, info = check_oaid_has_bu(oa_id, s)
        rec = {
            "roster_name": e.get("name"),
            "roster_school": e.get("school"),
            "openalex_id": oa_id,
            "openalex_works": e.get("openalex_works", 0),
            "is_rare_name": e.get("is_rare_name"),
            **info,
        }
        if status == "bu":
            confirmed.append(rec)
        elif status == "non_bu":
            wrong.append(rec)
        else:
            unreachable.append(rec)

        if (i + 1) % 50 == 0:
            print(f"  {i+1}/{len(targets)}  confirmed={len(confirmed)} wrong={len(wrong)} unreachable={len(unreachable)}")
        time.sleep(args.rate)

    out = {
        "audited": len(targets),
        "confirmed_bu": confirmed,
        "wrong_oaid": wrong,
        "unreachable": unreachable,
    }
    OUT_PATH.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(
        f"\nDone. Audited: {len(targets)}, "
        f"confirmed BU: {len(confirmed)}, "
        f"wrong OAID: {len(wrong)}, "
        f"unreachable: {len(unreachable)}"
    )
    print(f"Output: {OUT_PATH}")
    if wrong:
        print("\nFirst 10 wrong OAIDs:")
        for w in wrong[:10]:
            print(
                f"  {w['roster_name']:30} -> {w['name']!r:30} "
                f"works={w['works_count']:5} other={w['other_institutions'][:3]}"
            )


if __name__ == "__main__":
    main()
