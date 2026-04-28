#!/usr/bin/env python3
"""
Detect faculty whose AI-paper coverage in our master is materially smaller
than CrossRef thinks they have. Flags candidates for manual review or
targeted re-harvest.

Heuristic:
  for each faculty with >= 5 papers in master,
    query CrossRef for their last 24 months of journal articles,
    filter to titles+abstracts containing AI keywords,
    compare count to what's in master for the same window.
  Flag if external_count >= 2 and our_count / external_count <= 0.7.

Output: data/faculty_completeness_audit.json — a report listing each flagged
faculty with our count, CrossRef count, and a few example missing DOIs.
Designed to run as a monthly tail-end audit so the issue tracker post-monthly
catches drifting coverage early.

Usage:
  python audit_faculty_completeness.py             # full audit
  python audit_faculty_completeness.py --limit=50  # smoke test
"""
from __future__ import annotations
import argparse
import json
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import requests

from config import CONTACT_EMAIL

ROSTER_PATH = Path("data/bu_faculty_roster_verified.json")
MASTER_PATH = Path("data/sonnet_classification_bu_verified.json")
OUT_PATH = Path("data/faculty_completeness_audit.json")
HEADERS = {"User-Agent": f"BU-AI-Bibliography/1.0 (mailto:{CONTACT_EMAIL})"}

AI_RE = re.compile(
    r"\b(artificial intelligence|machine learning|deep learning|neural network"
    r"|generative ai|large language model|llm|natural language processing"
    r"|nlp|computer vision|reinforcement learning|transformer|gpt|chatbot"
    r"|algorithm|automated|predictive model|ai\b)",
    re.IGNORECASE,
)


def query_crossref_count(name: str, since: str, session: requests.Session) -> tuple[int, list[dict]]:
    """Count CrossRef hits for the faculty since `since`, filter to AI-keyword
    matches in title or container-title. Returns (count, sample_items)."""
    try:
        r = session.get(
            "https://api.crossref.org/works",
            params={
                "query.author": name,
                "filter": f"from-pub-date:{since},type:journal-article",
                "rows": 100,
                "select": "DOI,title,author,published-print,published-online,container-title",
            },
            headers=HEADERS, timeout=20,
        )
    except requests.RequestException:
        return 0, []
    if r.status_code != 200:
        return 0, []
    items = (r.json().get("message") or {}).get("items") or []
    last = name.split()[-1].lower()
    ai_hits = []
    for it in items:
        # Author surname must appear (CrossRef's author search is loose)
        authors = it.get("author") or []
        if not any(last in (a.get("family") or "").lower() for a in authors):
            continue
        title = ((it.get("title") or [""])[0]) or ""
        ctitle = ((it.get("container-title") or [""])[0]) or ""
        if AI_RE.search(title) or AI_RE.search(ctitle):
            ai_hits.append(it)
    return len(ai_hits), ai_hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--min-master", type=int, default=5,
                    help="only audit faculty with >= N papers in master")
    ap.add_argument("--gap-threshold", type=float, default=0.7,
                    help="flag if our count / external count <= threshold")
    ap.add_argument("--rate", type=float, default=0.05)
    args = ap.parse_args()

    master = json.loads(MASTER_PATH.read_text())
    roster = json.loads(ROSTER_PATH.read_text())

    # Count master papers per BU author over the last 24 months
    since = (datetime.now() - timedelta(days=24*30)).date().isoformat()
    since_year = int(since[:4])
    by_author = defaultdict(set)
    for p in master:
        if not p.get("year") or p["year"] < since_year:
            continue
        for n in (p.get("bu_author_names") or []):
            by_author[n].add(p.get("doi") or p.get("title_fingerprint"))

    # Filter to faculty meeting min-master in the window
    targets = [r for r in roster
               if r.get("name") and len(by_author.get(r["name"], set())) >= args.min_master]
    if args.limit:
        targets = targets[: args.limit]
    print(f"Auditing {len(targets)} faculty with >= {args.min_master} master papers since {since}")

    session = requests.Session()
    flagged = []
    for i, r in enumerate(targets):
        name = r["name"]
        ours = len(by_author.get(name, set()))
        ext_count, ext_items = query_crossref_count(name, since, session)
        time.sleep(args.rate)
        if ext_count < 2:
            continue
        ratio = ours / ext_count if ext_count else 1.0
        if ratio <= args.gap_threshold:
            our_dois = {(p.get("doi") or "").lower() for p in master
                        if name in (p.get("bu_author_names") or [])}
            missing = []
            for it in ext_items:
                doi = (it.get("DOI") or "").lower()
                if doi and doi not in our_dois:
                    title = (it.get("title") or [""])[0]
                    venue = (it.get("container-title") or [""])[0]
                    missing.append({"doi": doi, "title": title[:100], "venue": venue})
                if len(missing) >= 5:
                    break
            flagged.append({
                "name": name,
                "school": r.get("school"),
                "master_count_24m": ours,
                "crossref_ai_count_24m": ext_count,
                "ratio": round(ratio, 2),
                "sample_missing": missing,
            })

        if (i + 1) % 50 == 0:
            print(f"  {i+1}/{len(targets)}  flagged={len(flagged)}")

    OUT_PATH.write_text(json.dumps({
        "since": since,
        "audited": len(targets),
        "flagged": flagged,
    }, indent=2, ensure_ascii=False))
    print(f"\nDone. Flagged {len(flagged)} faculty with > {1-args.gap_threshold:.0%} coverage gap.")
    print(f"Output: {OUT_PATH}")
    if flagged:
        print("\nTop 10:")
        for f in sorted(flagged, key=lambda x: x["ratio"])[:10]:
            print(f"  {f['name']:30} school={f['school']:35} ours={f['master_count_24m']:3} ext={f['crossref_ai_count_24m']:3} ratio={f['ratio']}")


if __name__ == "__main__":
    main()
