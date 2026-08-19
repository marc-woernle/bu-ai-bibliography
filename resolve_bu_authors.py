#!/usr/bin/env python3
"""Resolve BU authorship from OpenAlex author profiles, into the BU author registry.

Why this exists
---------------
Two things in this pipeline decide "is this person at BU", and until now both
were bad at it.

The faculty roster is scraped from BU department pages, so a name on it means
"this string appeared on a bu.edu page" -- not "this person publishes from BU".
889 of its 5,888 entries were never scraped from a page at all; they carry
source_url "openalex_resolve", and one of them is Li Fei-Fei (Stanford), with no
OpenAlex ID at all. verify_bu_authors then name-matches against that roster, and
DBLP -- which carries no affiliation data whatsoever -- imports the matched
person's entire career. Measured: 858 of 1,586 DBLP papers in master sit outside
their author's documented BU years.

The registry built during harvest fixes the year question for anyone who appears
on a harvested paper with a BU affiliation string, which is free but partial. It
misses people whose OpenAlex records list them under a hospital or a center
rather than the university, and it misses anyone we have not harvested yet.

This script closes the gap with the one authoritative source that is still free:
the OpenAlex author profile. `affiliations[]` gives every institution the author
has published from and the exact years for each. One GET per author, no key
needed. That is the whole of "you get the name, you search it up, boom".

Endpoint economics, measured 2026-08-19: single-entity GETs
(/authors/A5075696701) are free and return in ~0.7s. The filtered list endpoints
(/authors?filter=..., /works?filter=...) are metered and currently return
"Insufficient budget ... $0 remaining", so batching is not available to us.
72 batched requests would have been nicer than 3,586 single ones; free beats
fast.

Output: data/bu_author_registry.json, the same file the harvest writes, so the
pipeline reads one registry and does not care which tier filled a given entry.

Run:
  python resolve_bu_authors.py                # resolve everything unresolved
  python resolve_bu_authors.py --limit 50     # smoke test
  python resolve_bu_authors.py --max-minutes 30
"""
import argparse
import json
import os
import sys
import time

import requests

from config import BU_ROR_ID, CONTACT_EMAIL, openalex_headers

ROSTER_PATH = "data/bu_faculty_roster_verified.json"
REGISTRY_PATH = "data/bu_author_registry.json"
HEADERS = openalex_headers()


def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as e:
        print(f"could not read {path}: {e}", file=sys.stderr)
        return default


def fetch_author(oa_id: str, session: requests.Session) -> tuple[str, dict]:
    """(status, profile). status: ok | missing | error."""
    short = oa_id.rsplit("/", 1)[-1]
    url = f"https://api.openalex.org/authors/{short}"
    for attempt in range(4):
        try:
            r = session.get(url, params={"mailto": CONTACT_EMAIL},
                            headers=HEADERS, timeout=20)
        except requests.RequestException as e:
            time.sleep(2 * (attempt + 1))
            last = str(e)
            continue
        if r.status_code == 200:
            return "ok", r.json()
        if r.status_code == 404:
            return "missing", {}
        if r.status_code == 429:
            # Metered now. Back off, but do not spin: if the budget is gone it
            # is gone until midnight UTC and no amount of waiting helps.
            if "Insufficient budget" in r.text:
                return "error", {"error": "budget exhausted"}
            time.sleep(5 * (attempt + 1))
            continue
        last = f"http {r.status_code}"
        time.sleep(2 * (attempt + 1))
    return "error", {"error": last}


def bu_years(profile: dict) -> list[int]:
    out = []
    for a in profile.get("affiliations") or []:
        inst = a.get("institution") or {}
        if inst.get("ror") == BU_ROR_ID:
            out.extend(y for y in (a.get("years") or []) if isinstance(y, int))
    return sorted(set(out))


def autocomplete(name: str, session: requests.Session) -> list[str]:
    """Candidate OpenAlex author IDs for a name. Free.

    /authors?search= is metered and currently refused ("this request costs
    $0.001 but you only have $0"). /autocomplete/authors reports cost_usd 0.0
    and answers the same question well enough: it returns candidate ids with an
    institution hint, and the profile GET that follows is the real check.
    """
    try:
        r = session.get("https://api.openalex.org/autocomplete/authors",
                        params={"q": name, "mailto": CONTACT_EMAIL},
                        headers=HEADERS, timeout=20)
    except requests.RequestException:
        return []
    if r.status_code != 200:
        return []
    out = []
    for x in (r.json().get("results") or [])[:MAX_CANDIDATES]:
        if x.get("id"):
            out.append(x["id"])
    return out


MAX_CANDIDATES = 5


def resolve_by_name(roster, registry, session, deadline):
    """Second pass: roster entries with no OpenAlex ID at all.

    1,422 of 5,888 roster entries have no ID, so the ID pass cannot see them and
    a bare name match cannot be checked against anything. They are the entries
    that let Li Fei-Fei (Stanford) sit on a BU faculty roster and import her
    career through DBLP. Name -> candidates -> profile -> BU years closes it,
    and only a candidate whose OpenAlex profile actually carries BU's ROR is
    accepted, so a namesake at another institution resolves to nothing.
    """
    todo, seen = [], set()
    for p in roster:
        if p.get("openalex_id"):
            continue
        nm = (p.get("name") or "").strip()
        key = "name:" + nm.lower()
        if not nm or nm in seen:
            continue
        if registry.get(key, {}).get("src") == "profile":
            continue
        seen.add(nm)
        todo.append(nm)

    print(f"\nname pass: {len(todo):,} roster entries with no OpenAlex ID")
    found = nothing = 0
    for i, nm in enumerate(todo, 1):
        if time.time() > deadline:
            print(f"time budget reached after {i-1:,}; rerun to continue")
            break
        hit = False
        cands = autocomplete(nm, session)
        for cand in cands:
            status, prof = fetch_author(cand, session)
            if status != "ok":
                if prof.get("error") == "budget exhausted":
                    print("OpenAlex budget exhausted; stopping.")
                    return found, nothing
                continue
            yrs = bu_years(prof)
            if not yrs:
                continue
            e = registry.get(cand) or {}
            names = e.get("names") or []
            for c in (prof.get("display_name"), nm):
                if c and c not in names:
                    names.append(c)
            e.update({
                "name": prof.get("display_name") or nm,
                "orcid": prof.get("orcid") or e.get("orcid"),
                "names": names, "src": "profile", "bu": True,
                "first": min(yrs[0], e["first"]) if "first" in e else yrs[0],
                "last": max(yrs[-1], e["last"]) if "last" in e else yrs[-1],
                "n": max(e.get("n", 0), len(yrs)),
            })
            registry[cand] = e
            hit = True
        if hit:
            found += 1
        else:
            # No BU identity found for this name. Deliberately record NOTHING.
            #
            # The tempting move is to write bu=False and refuse the name
            # outright. It does not survive contact with the data. Autocomplete
            # returns one page, so for a common name -- "Jian Wu", "H. Chen" --
            # "none of the candidates I saw is BU" says nothing about the ones I
            # did not see; measured, 17 names refused this way carry a real BU
            # affiliation string elsewhere in master. And most no-ID roster
            # entries are clinical and administrative staff who simply do not
            # publish, where a failed lookup is silence, not disconfirmation.
            # Net effect of refusing was 19 more DBLP papers dropped in exchange
            # for a new class of false negatives. Bad trade for a bibliography
            # whose first job is finding everything.
            #
            # bu=False is written only by the ID pass, where OpenAlex hands back
            # that specific person's complete institutional history.
            nothing += 1
        if i % 100 == 0:
            print(f"  {i:,}/{len(todo):,}  resolved to a BU identity {found:,}  no BU identity {nothing:,}")
            with open(REGISTRY_PATH, "w") as f:
                json.dump(registry, f, separators=(",", ":"), sort_keys=True)
    return found, nothing


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--max-minutes", type=float, default=90.0)
    ap.add_argument("--names-only", action="store_true",
                    help="skip the ID pass, only resolve roster entries with no ID")
    ap.add_argument("--no-names", action="store_true",
                    help="skip the name pass")
    ap.add_argument("--refresh", action="store_true",
                    help="re-check profiles already resolved by a previous run")
    args = ap.parse_args()

    roster = load_json(ROSTER_PATH, [])
    registry = load_json(REGISTRY_PATH, {})

    todo = []
    for p in roster:
        oa = p.get("openalex_id")
        if not oa:
            continue
        e = registry.get(oa)
        if e and e.get("src") == "profile" and not args.refresh:
            continue
        todo.append((oa, p.get("name")))
    # dedupe, roster has the same person under several schools
    seen, uniq = set(), []
    for oa, nm in todo:
        if oa in seen:
            continue
        seen.add(oa)
        uniq.append((oa, nm))
    todo = uniq
    if args.limit:
        todo = todo[:args.limit]

    if args.names_only:
        todo = []

    print(f"roster {len(roster):,} | registry {len(registry):,} | to resolve by id {len(todo):,}")

    deadline = time.time() + args.max_minutes * 60
    session = requests.Session()
    confirmed = rejected = missing = errors = 0

    for i, (oa, nm) in enumerate(todo, 1):
        if time.time() > deadline:
            print(f"time budget reached after {i-1:,}; rerun to continue")
            break
        status, prof = fetch_author(oa, session)
        if status == "error":
            errors += 1
            if prof.get("error") == "budget exhausted":
                print("OpenAlex budget exhausted; stopping. Resets at midnight UTC.")
                break
            continue
        if status == "missing":
            missing += 1
            continue

        yrs = bu_years(prof)
        e = registry.get(oa) or {}
        names = e.get("names") or []
        for cand in (prof.get("display_name"), nm):
            if cand and cand not in names:
                names.append(cand)
        e.update({
            "name": prof.get("display_name") or nm,
            "orcid": prof.get("orcid") or e.get("orcid"),
            "names": names,
            "src": "profile",
        })
        if yrs:
            # Union with anything the harvest already observed. Both are real
            # evidence; neither is complete on its own.
            e["first"] = min(yrs[0], e["first"]) if "first" in e else yrs[0]
            e["last"] = max(yrs[-1], e["last"]) if "last" in e else yrs[-1]
            e["n"] = max(e.get("n", 0), len(yrs))
            e["bu"] = True
            confirmed += 1
        elif e.get("first") is not None:
            # OpenAlex's profile says no BU, but we have already seen this exact
            # author ID on a paper carrying a Boston University affiliation
            # string. affiliations[] is a derived summary and is demonstrably
            # incomplete -- OpenAlex lists no BU for the Jian Wu whose four BU
            # papers we hold from 1988-2003. A direct observation outranks a
            # derived one, so keep the evidence and do not refuse the identity.
            e["bu"] = True
            confirmed += 1
        else:
            # No BU anywhere in this person's institutional history, and we have
            # never seen them on a BU-affiliated paper either. This is the
            # Li Fei-Fei case: on our roster, never at BU.
            e["bu"] = False
            rejected += 1
        registry[oa] = e

        if i % 100 == 0:
            print(f"  {i:,}/{len(todo):,}  confirmed {confirmed:,}  not-BU {rejected:,}"
                  f"  missing {missing:,}  errors {errors:,}")
            with open(REGISTRY_PATH, "w") as f:
                json.dump(registry, f, separators=(",", ":"), sort_keys=True)

    if not args.no_names:
        resolve_by_name(roster, registry, session, deadline)

    with open(REGISTRY_PATH, "w") as f:
        json.dump(registry, f, separators=(",", ":"), sort_keys=True)
    print(f"\nregistry {len(registry):,} identities")
    print(f"confirmed BU {confirmed:,} | on roster but never at BU {rejected:,} "
          f"| no profile {missing:,} | errors {errors:,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
