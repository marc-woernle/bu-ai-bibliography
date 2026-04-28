#!/usr/bin/env python3
"""
Resolve real journal/book venues for SSRN and Scholarly Commons papers.

These platforms are repositories, not journals. CrossRef returns container-title
"SSRN Electronic Journal" for SSRN preprints; Scholarly Commons rarely fills
the journal field at all. When the same work has been (or is later) published
in a real venue, this script looks it up via CrossRef title+author search and
patches the master record's `venue` field. Used as a one-shot cleanup AND as a
post-harvest pass in the monthly pipeline.

Heuristic for accepting a match:
  - Container-title is non-empty and not in the platform-name blocklist
  - Year within ±1 of our paper's year (or one of them is unknown)
  - Title similarity ≥ 0.85 (token-set ratio)
  - At least one author surname in common

Run:
  python resolve_repository_venues.py            # full run, writes master in place
  python resolve_repository_venues.py --dry-run  # report only
  python resolve_repository_venues.py --limit=10 # smoke test
"""
from __future__ import annotations
import argparse
import json
import re
import time
import unicodedata
from pathlib import Path

import requests

from config import CONTACT_EMAIL
from utils import sanitize_inline_text

MASTER_PATH = Path("data/sonnet_classification_bu_verified.json")

PLATFORM_VENUES = {
    "ssrn electronic journal",
    "ssrn",
    "social science research network",
    "bu law scholarly commons",
    "scholarly commons",
    "boston university school of law digital repository",
}

HEADERS = {"User-Agent": f"BU-AI-Bibliography/1.0 (mailto:{CONTACT_EMAIL})"}


def _norm(s: str) -> str:
    """Lowercase, strip accents, collapse non-alphanumeric to single spaces."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return s.strip()


def _title_similarity(a: str, b: str) -> float:
    """Token-set similarity in [0,1]."""
    sa, sb = set(_norm(a).split()), set(_norm(b).split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / max(len(sa), len(sb))


def _surname(name: str) -> str:
    parts = _norm(name).split()
    return parts[-1] if parts else ""


def _is_platform_venue(v: str) -> bool:
    return _norm(v) in PLATFORM_VENUES


def _author_surnames(paper: dict) -> set[str]:
    out = set()
    for a in paper.get("authors") or []:
        if isinstance(a, dict):
            sn = _surname(a.get("name") or "")
            if sn:
                out.add(sn)
    return out


def needs_lookup(paper: dict) -> bool:
    srcs = set(paper.get("all_sources") or [])
    if not (srcs & {"ssrn", "scholarly_commons"}):
        return False
    v = paper.get("venue") or ""
    return not v.strip() or _is_platform_venue(v)


def _accept_match(our_title: str, our_year, our_surnames,
                  cand_title: str, cand_year, cand_surnames) -> float | None:
    """Shared scoring used by all three lookups. Returns score >= 0.85 on
    confident match, or None to reject."""
    sim = _title_similarity(our_title, cand_title)
    if sim < 0.85:
        return None
    if our_year and cand_year and abs(our_year - cand_year) > 1:
        return None
    if our_surnames and cand_surnames and not (our_surnames & cand_surnames):
        return None
    score = sim
    if our_year and cand_year and our_year == cand_year:
        score += 0.1
    if our_surnames and cand_surnames and (our_surnames & cand_surnames):
        score += 0.05
    return score


def _query_crossref(title: str, year, our_surnames,
                    session: requests.Session) -> tuple[str, dict] | None:
    params = {
        "query.bibliographic": title,
        "rows": 8,
        "select": "DOI,title,container-title,issued,author,type",
        "mailto": CONTACT_EMAIL,
    }
    try:
        r = session.get("https://api.crossref.org/works", params=params,
                        headers=HEADERS, timeout=20)
    except requests.RequestException:
        return None
    if r.status_code != 200:
        return None
    items = (r.json().get("message") or {}).get("items") or []
    best: tuple[float, str, dict] | None = None
    for it in items:
        ctitle_arr = it.get("container-title") or []
        ctitle = ctitle_arr[0] if ctitle_arr else ""
        if not ctitle or _is_platform_venue(ctitle):
            continue
        cand_titles = it.get("title") or []
        cand_title = cand_titles[0] if cand_titles else ""
        cand_year = None
        issued = (it.get("issued") or {}).get("date-parts") or []
        if issued and issued[0]:
            cand_year = issued[0][0]
        cand_surnames = set()
        for a in it.get("author") or []:
            sn = _surname(a.get("family") or a.get("name") or "")
            if sn:
                cand_surnames.add(sn)
        score = _accept_match(title, year, our_surnames,
                              cand_title, cand_year, cand_surnames)
        if score is None:
            continue
        if best is None or score > best[0]:
            best = (score, ctitle, it)
    if best:
        return sanitize_inline_text(best[1]), {
            "source": "crossref", "score": round(best[0], 3),
            "doi": best[2].get("DOI"), "type": best[2].get("type"),
        }
    return None


def _query_openalex(title: str, year, our_surnames,
                    session: requests.Session) -> tuple[str, dict] | None:
    params = {
        "search": title,
        "per-page": 8,
        "select": "id,doi,title,publication_year,primary_location,host_venue,authorships,type",
        "mailto": CONTACT_EMAIL,
    }
    try:
        r = session.get("https://api.openalex.org/works", params=params,
                        headers=HEADERS, timeout=20)
    except requests.RequestException:
        return None
    if r.status_code != 200:
        return None
    items = r.json().get("results") or []
    best: tuple[float, str, dict] | None = None
    for it in items:
        loc = it.get("primary_location") or {}
        src = loc.get("source") or {}
        venue = src.get("display_name") or (it.get("host_venue") or {}).get("display_name") or ""
        if not venue or _is_platform_venue(venue):
            continue
        cand_title = it.get("title") or ""
        cand_year = it.get("publication_year")
        cand_surnames = set()
        for a in it.get("authorships") or []:
            au = a.get("author") or {}
            sn = _surname(au.get("display_name") or "")
            if sn:
                cand_surnames.add(sn)
        score = _accept_match(title, year, our_surnames,
                              cand_title, cand_year, cand_surnames)
        if score is None:
            continue
        if best is None or score > best[0]:
            best = (score, venue, it)
    if best:
        return sanitize_inline_text(best[1]), {
            "source": "openalex", "score": round(best[0], 3),
            "doi": best[2].get("doi"), "type": best[2].get("type"),
        }
    return None


def _query_semantic_scholar(title: str, year, our_surnames,
                            session: requests.Session) -> tuple[str, dict] | None:
    """Semantic Scholar Graph API. No auth needed; rate-limited to ~1 req/s."""
    try:
        r = session.get(
            "https://api.semanticscholar.org/graph/v1/paper/search",
            params={
                "query": title,
                "limit": 8,
                "fields": "title,venue,year,authors,externalIds,publicationVenue",
            },
            headers=HEADERS,
            timeout=20,
        )
    except requests.RequestException:
        return None
    if r.status_code != 200:
        return None
    items = (r.json() or {}).get("data") or []
    best: tuple[float, str, dict] | None = None
    for it in items:
        # Prefer publicationVenue.name; fall back to flat venue field
        pv = it.get("publicationVenue") or {}
        venue = pv.get("name") or it.get("venue") or ""
        if not venue or _is_platform_venue(venue):
            continue
        cand_title = it.get("title") or ""
        cand_year = it.get("year")
        cand_surnames = set()
        for a in it.get("authors") or []:
            sn = _surname(a.get("name") or "")
            if sn:
                cand_surnames.add(sn)
        score = _accept_match(title, year, our_surnames,
                              cand_title, cand_year, cand_surnames)
        if score is None:
            continue
        if best is None or score > best[0]:
            best = (score, venue, it)
    if best:
        return sanitize_inline_text(best[1]), {
            "source": "semantic_scholar", "score": round(best[0], 3),
            "doi": (best[2].get("externalIds") or {}).get("DOI"),
        }
    return None


def find_real_venue(paper: dict, session: requests.Session) -> tuple[str, dict] | None:
    """Search CrossRef -> OpenAlex -> Semantic Scholar; first confident match wins."""
    title = (paper.get("title") or "").strip()
    if not title:
        return None
    year = paper.get("year")
    our_surnames = _author_surnames(paper)

    for fn in (_query_crossref, _query_openalex, _query_semantic_scholar):
        result = fn(title, year, our_surnames, session)
        if result:
            return result
        # Polite pacing between sources, especially for SS
        time.sleep(0.05)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--rate", type=float, default=0.05, help="seconds between calls")
    args = ap.parse_args()

    data = json.loads(MASTER_PATH.read_text())
    targets = [p for p in data if needs_lookup(p)]
    if args.limit:
        targets = targets[: args.limit]
    print(f"Repository papers needing real venue: {len(targets)}")

    session = requests.Session()
    fixed = 0
    no_match = 0
    log = []
    for i, p in enumerate(targets):
        result = find_real_venue(p, session)
        time.sleep(args.rate)
        if not result:
            no_match += 1
            continue
        venue, ev = result
        old = p.get("venue") or ""
        log.append({"idx": p.get("index"), "old": old, "new": venue, "evidence": ev,
                    "title": (p.get("title") or "")[:80]})
        if not args.dry_run:
            p["venue"] = venue
        fixed += 1
        if (i + 1) % 25 == 0:
            print(f"  {i+1}/{len(targets)}  fixed={fixed} no_match={no_match}")

    print(f"\nFixed: {fixed}  no_match: {no_match}")

    # Show fixes
    if log:
        print("\nFirst 10 changes:")
        for entry in log[:10]:
            print(f"  idx={entry['idx']:5} score={entry['evidence']['score']}")
            print(f"    title: {entry['title']}")
            print(f"    venue: {entry['old']!r} -> {entry['new']!r}")

    if not args.dry_run and fixed:
        MASTER_PATH.write_text(json.dumps(data, ensure_ascii=False,
                                          separators=(",", ":")))
        print(f"\nWrote {MASTER_PATH}")


if __name__ == "__main__":
    main()
