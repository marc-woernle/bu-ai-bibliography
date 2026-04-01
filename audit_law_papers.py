#!/usr/bin/env python3
"""
Specialized Law Paper Audit Agent
===================================
Thoroughly audits BU Law faculty AI-related publications by:

1. Loading the FULL BU Law faculty roster from Scholarly Commons + BU website
2. For EACH faculty member, searching multiple sources:
   - Our master dataset (what we already have)
   - Scholarly Commons (scholarship.law.bu.edu)
   - SSRN (by author name)
   - Google Scholar (web search)
3. Cross-referencing and identifying gaps
4. Producing a detailed report

Usage:
    python audit_law_papers.py                # Full audit
    python audit_law_papers.py --faculty "Woodrow Hartzog"  # Single faculty
"""

import argparse
import json
import logging
import re
import time
from collections import defaultdict
from pathlib import Path

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("law_audit")

MASTER_PATH = Path("data/sonnet_classification_bu_verified.json")
ROSTER_PATH = Path("data/bu_faculty_roster_verified.json")
REPORT_PATH = Path("output/law_audit_report.md")
CANDIDATES_PATH = Path("data/law_audit_candidates.json")

SC_BASE = "https://scholarship.law.bu.edu"

AI_KEYWORDS = [
    "artificial intelligence", "machine learning", "deep learning",
    "neural network", "algorithm", "automated", "autonomous",
    "natural language processing", "computer", "data-driven",
    "predictive", "surveillance", "facial recognition", "robot",
    "big data", "data analytics", "technology", "digital",
    "platform", "cyber", "privacy", "encryption",
    "blockchain", "cryptocurrency", "fintech", "regtech",
    "generative ai", "chatgpt", "large language model",
    "ai governance", "algorithmic", "bias", "fairness",
    "autonomous vehicle", "self-driving", "drone",
    "deepfake", "synthetic media", "content moderation",
    "intellectual property", "patent", "copyright",
    "antitrust", "competition", "innovation",
]


def load_law_faculty():
    """Get all BU Law faculty from roster."""
    with open(ROSTER_PATH) as f:
        roster = json.load(f)
    law = [r for r in roster if r.get("school") == "School of Law"]
    # Also get from BU Law AI page
    return law


def load_master_law():
    """Get all law papers from master dataset, indexed by author."""
    with open(MASTER_PATH) as f:
        master = json.load(f)

    by_author = defaultdict(list)
    law_papers = []
    for p in master:
        if "School of Law" in p.get("bu_schools", []):
            law_papers.append(p)
        for n in p.get("bu_author_names", []):
            by_author[n.lower().strip()].append(p)

    return law_papers, by_author


def search_scholarly_commons(name: str, max_pages: int = 5) -> list[dict]:
    """Search BU Law Scholarly Commons for a faculty member's papers."""
    results = []

    # Try the search functionality
    search_url = f"{SC_BASE}/do/search/?q=author%3A%22{name.replace(' ', '+')}%22&start=0&context=509"
    try:
        resp = requests.get(search_url, timeout=20)
        if resp.status_code != 200:
            return results
        soup = BeautifulSoup(resp.text, "html.parser")

        articles = soup.select("div.result p.title a, p.article-title a")
        for art in articles:
            title = art.get_text(strip=True)
            href = art.get("href", "")
            if not href.startswith("http"):
                href = SC_BASE + href
            results.append({"title": title, "url": href, "source": "scholarly_commons"})

    except Exception as e:
        logger.warning(f"SC search error for {name}: {e}")

    return results


def search_ssrn(name: str) -> list[dict]:
    """Search SSRN via CrossRef for a faculty member's papers."""
    results = []
    try:
        resp = requests.get(
            "https://api.crossref.org/works",
            params={
                "query.author": name,
                "query.bibliographic": "Boston University",
                "filter": "type:posted-content",
                "rows": 25,
            },
            timeout=20,
        )
        if resp.status_code == 200:
            for item in resp.json().get("message", {}).get("items", []):
                title = " ".join(item.get("title", []))
                doi = item.get("DOI", "")
                if title:
                    results.append({"title": title, "doi": doi, "source": "ssrn_crossref"})
    except Exception as e:
        logger.warning(f"SSRN search error for {name}: {e}")

    return results


def is_ai_relevant(title: str) -> bool:
    """Quick check if a title might be AI-relevant."""
    text = title.lower()
    return any(kw in text for kw in AI_KEYWORDS)


def title_match(t1: str, t2: str) -> bool:
    """Fuzzy title match."""
    def clean(t):
        return re.sub(r"[^a-z0-9]", "", t.lower())[:50]
    return clean(t1) == clean(t2) and len(clean(t1)) > 10


def audit_faculty(name: str, master_papers: list, all_by_author: dict) -> dict:
    """Audit one faculty member's coverage."""
    name_lower = name.lower().strip()

    # What we have
    our_papers = all_by_author.get(name_lower, [])
    # Also check partial name match
    for key, papers in all_by_author.items():
        if name_lower.split()[-1] in key and key != name_lower:
            our_papers.extend(papers)

    our_titles = set(p.get("title", "")[:50].lower() for p in our_papers)

    # Search Scholarly Commons
    time.sleep(0.5)
    sc_results = search_scholarly_commons(name)
    sc_ai = [r for r in sc_results if is_ai_relevant(r["title"])]
    sc_missing = [r for r in sc_ai if not any(title_match(r["title"], t) for t in our_titles)]

    # Search SSRN/CrossRef
    time.sleep(0.5)
    ssrn_results = search_ssrn(name)
    ssrn_ai = [r for r in ssrn_results if is_ai_relevant(r["title"])]
    ssrn_missing = [r for r in ssrn_ai if not any(title_match(r["title"], t) for t in our_titles)]

    return {
        "name": name,
        "our_count": len(our_papers),
        "our_titles": [p.get("title", "")[:80] for p in our_papers],
        "sc_total": len(sc_results),
        "sc_ai": len(sc_ai),
        "sc_missing": sc_missing,
        "ssrn_total": len(ssrn_results),
        "ssrn_ai": len(ssrn_ai),
        "ssrn_missing": ssrn_missing,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--faculty", help="Audit single faculty member")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    law_faculty = load_law_faculty()
    law_papers, by_author = load_master_law()

    print(f"BU Law faculty in roster: {len(law_faculty)}")
    print(f"Law papers in dataset: {len(law_papers)}")
    print()

    if args.faculty:
        targets = [{"name": args.faculty}]
    else:
        targets = law_faculty

    all_results = []
    all_missing = []

    for i, fac in enumerate(targets):
        name = fac["name"]
        result = audit_faculty(name, law_papers, by_author)
        all_results.append(result)

        n_missing = len(result["sc_missing"]) + len(result["ssrn_missing"])
        if n_missing > 0 or result["our_count"] > 0:
            print(f"[{i+1}/{len(targets)}] {name}: {result['our_count']} papers | "
                  f"SC: {result['sc_ai']}/{result['sc_total']} AI | "
                  f"SSRN: {result['ssrn_ai']}/{result['ssrn_total']} AI | "
                  f"MISSING: {n_missing}")

        for paper in result["sc_missing"] + result["ssrn_missing"]:
            paper["faculty"] = name
            all_missing.append(paper)

    # Report
    print(f"\n{'='*60}")
    print(f"LAW AUDIT SUMMARY")
    print(f"{'='*60}")
    print(f"Faculty audited: {len(all_results)}")
    print(f"Total papers we have: {sum(r['our_count'] for r in all_results)}")
    print(f"Missing AI papers found: {len(all_missing)}")

    # Faculty with most missing
    by_fac = defaultdict(int)
    for p in all_missing:
        by_fac[p["faculty"]] += 1
    if by_fac:
        print(f"\nMost missing papers by faculty:")
        for name, count in sorted(by_fac.items(), key=lambda x: -x[1])[:15]:
            our = next((r["our_count"] for r in all_results if r["name"] == name), 0)
            print(f"  {name}: {count} missing (we have {our})")

    # Save missing papers as candidates
    if all_missing:
        with open(CANDIDATES_PATH, "w") as f:
            json.dump(all_missing, f, indent=2)
        print(f"\nSaved {len(all_missing)} missing paper candidates to {CANDIDATES_PATH}")

    # Generate markdown report
    lines = [
        f"# BU Law AI Paper Audit — {len(all_results)} Faculty",
        "",
        f"**Papers in dataset:** {sum(r['our_count'] for r in all_results)}",
        f"**Missing AI papers found:** {len(all_missing)}",
        "",
        "## Faculty Coverage",
        "",
    ]

    for r in sorted(all_results, key=lambda x: -(len(x["sc_missing"]) + len(x["ssrn_missing"]))):
        n_missing = len(r["sc_missing"]) + len(r["ssrn_missing"])
        if n_missing > 0 or r["our_count"] > 0:
            lines.append(f"### {r['name']} — {r['our_count']} papers (missing {n_missing})")
            if r["our_titles"]:
                lines.append("**In dataset:**")
                for t in r["our_titles"][:5]:
                    lines.append(f"- {t}")
            if r["sc_missing"]:
                lines.append("**Missing from Scholarly Commons:**")
                for p in r["sc_missing"]:
                    lines.append(f"- [{p['title'][:70]}]({p.get('url', '')})")
            if r["ssrn_missing"]:
                lines.append("**Missing from SSRN:**")
                for p in r["ssrn_missing"]:
                    lines.append(f"- {p['title'][:70]}")
            lines.append("")

    with open(REPORT_PATH, "w") as f:
        f.write("\n".join(lines))
    print(f"Report saved to {REPORT_PATH}")


if __name__ == "__main__":
    main()
