#!/usr/bin/env python3
"""
Generate compact data.js files for the BU AI Bibliography web app.

Converts the master dataset (sonnet_classification_bu_verified.json) into
the compact window.PAPERS_DATA format used by the static HTML app.

Writes to three locations:
  - output/bibliography_app/data.js        (public, no abstracts)
  - output/bibliography_app/data_private.js (includes abstracts)
  - docs/data.js                            (public, for GitHub Pages)
"""

import json
import os
import sys
import hashlib

MASTER_PATH = "data/sonnet_classification_bu_verified.json"

OUTPUT_PUBLIC = "output/bibliography_app/data.js"
OUTPUT_PRIVATE = "output/bibliography_app/data_private.js"
OUTPUT_DOCS = "docs/data.js"


def paper_to_compact(paper: dict, include_abstract: bool = False) -> dict:
    """Convert a master record to the compact data.js format.

    Key mapping:
      t   = title
      a   = authors (comma-joined string)
      y   = year
      v   = venue
      c   = citation_count
      r   = ai_relevance
      s   = one_line_summary
      n   = annotation
      cat = bu_category
      sch = bu_schools
      bu  = bu_author_names
      dom = domains
      sub = subfields
      u   = best_url
      oa  = is_open_access (only if True)
      doi = doi
      src = all_sources
      abs = abstract (private only)

    Falsy values are omitted to minimize file size.
    """
    # Build author string from author objects or use existing string
    authors = paper.get("authors", [])
    if isinstance(authors, list) and len(authors) > 0:
        if isinstance(authors[0], dict):
            author_str = ", ".join(
                a.get("name", "") for a in authors if a.get("name")
            )
        else:
            author_str = ", ".join(str(a) for a in authors)
    else:
        author_str = ""

    rec = {}
    rec["t"] = paper.get("title", "")
    rec["a"] = author_str
    if paper.get("year"):
        rec["y"] = paper["year"]
    if paper.get("venue"):
        rec["v"] = paper["venue"]
    if paper.get("citation_count"):
        rec["c"] = paper["citation_count"]
    rec["r"] = paper.get("ai_relevance", "")
    if paper.get("one_line_summary"):
        rec["s"] = paper["one_line_summary"]
    if paper.get("annotation"):
        rec["n"] = paper["annotation"]
    rec["cat"] = paper.get("bu_category", "")
    if paper.get("bu_schools"):
        rec["sch"] = paper["bu_schools"]
    if paper.get("bu_author_names"):
        rec["bu"] = paper["bu_author_names"]
    if paper.get("domains"):
        rec["dom"] = paper["domains"]
    if paper.get("subfields"):
        rec["sub"] = paper["subfields"]
    if paper.get("best_url"):
        rec["u"] = paper["best_url"]
    if paper.get("is_open_access"):
        rec["oa"] = True
    if paper.get("doi"):
        rec["doi"] = paper["doi"]
    if paper.get("all_sources"):
        rec["src"] = paper["all_sources"]
    if include_abstract and paper.get("abstract"):
        rec["abs"] = paper["abstract"]

    return rec


def build_metadata(papers: list[dict]) -> dict:
    """Build metadata object for the web app."""
    from datetime import date
    sources = set()
    for p in papers:
        for s in p.get("all_sources", []):
            sources.add(s)
    return {
        "updated": date.today().isoformat(),
        "paper_count": len(papers),
        "sources": len(sources),
    }


def write_data_js(records: list[dict], output_path: str, meta: dict = None) -> int:
    """Write records as window.PAPERS_DATA = [...]; to a JS file.

    If meta is provided, also writes window.PAPERS_META = {...};
    Returns the file size in bytes.
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    parts = []
    if meta:
        parts.append("window.PAPERS_META = " + json.dumps(meta, separators=(",", ":")) + ";")
    parts.append("window.PAPERS_DATA = " + json.dumps(records, separators=(",", ":"), ensure_ascii=False) + ";")
    content = "\n".join(parts)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)
    return len(content.encode("utf-8"))


def generate_all(master_path: str = MASTER_PATH) -> dict:
    """Regenerate all three data.js files from the master dataset.

    Returns dict with paper_count, public_size, private_size, docs_size.
    """
    with open(master_path, "r") as f:
        papers = json.load(f)

    public_records = [paper_to_compact(p, include_abstract=False) for p in papers]
    private_records = [paper_to_compact(p, include_abstract=True) for p in papers]
    meta = build_metadata(papers)

    pub_size = write_data_js(public_records, OUTPUT_PUBLIC, meta)
    priv_size = write_data_js(private_records, OUTPUT_PRIVATE, meta)
    docs_size = write_data_js(public_records, OUTPUT_DOCS, meta)

    return {
        "paper_count": len(papers),
        "public_size_mb": round(pub_size / (1024 * 1024), 1),
        "private_size_mb": round(priv_size / (1024 * 1024), 1),
        "docs_size_mb": round(docs_size / (1024 * 1024), 1),
    }


def compute_master_hash(master_path: str = MASTER_PATH) -> str:
    """SHA-256 hash of the master dataset for integrity tracking."""
    h = hashlib.sha256()
    with open(master_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def validate_data_js(path: str) -> bool:
    """Check that a data.js file is syntactically valid."""
    try:
        with open(path, "r") as f:
            content = f.read()
        # Extract the PAPERS_DATA array (may be preceded by PAPERS_META)
        if "window.PAPERS_DATA = " not in content:
            return False
        data_part = content.split("window.PAPERS_DATA = ", 1)[1].rstrip(";")
        data = json.loads(data_part)
        return isinstance(data, list) and len(data) > 0
    except (json.JSONDecodeError, FileNotFoundError):
        return False


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else MASTER_PATH

    if not os.path.exists(path):
        print(f"Error: {path} not found")
        sys.exit(1)

    print(f"Generating data.js files from {path}...")
    result = generate_all(path)
    print(f"  Papers: {result['paper_count']}")
    print(f"  Public:  {result['public_size_mb']} MB → {OUTPUT_PUBLIC}")
    print(f"  Private: {result['private_size_mb']} MB → {OUTPUT_PRIVATE}")
    print(f"  Docs:    {result['docs_size_mb']} MB → {OUTPUT_DOCS}")

    # Validate all outputs
    for path in [OUTPUT_PUBLIC, OUTPUT_PRIVATE, OUTPUT_DOCS]:
        if validate_data_js(path):
            print(f"  ✓ {path} valid")
        else:
            print(f"  ✗ {path} INVALID")
            sys.exit(1)

    print("Done.")
