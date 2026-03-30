#!/usr/bin/env python3
"""
BU AI Bibliography — Stage 4: Output Formatter
=================================================
Takes classified papers and produces formatted annotated bibliographies.

Outputs:
  - Markdown (organized by domain, then year)
  - JSON (structured, for further processing)
  - CSV (for spreadsheet review)
  - BibTeX (for LaTeX integration)

Usage:
    python format_output.py data/classified_*.json
    python format_output.py data/classified_*.json --format markdown
    python format_output.py data/classified_*.json --format bibtex
    python format_output.py data/classified_*.json --min-relevance primary
    python format_output.py data/classified_*.json --domain "Law & Regulation"
"""

import argparse
import json
import re
import logging
import os
from collections import defaultdict
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("bu_bib.format")


def load_papers(path: str) -> list[dict]:
    """Load classified papers."""
    with open(path) as f:
        papers = json.load(f)
    return papers


def filter_papers(
    papers: list[dict],
    min_relevance: str = "methodological",
    domain: str = None,
    min_year: int = None,
    max_year: int = None,
) -> list[dict]:
    """Filter papers by classification criteria."""
    relevance_order = {
        "primary": 0,
        "methodological": 1,
        "peripheral": 2,
        "not_relevant": 3,
        "unknown": 4,
        "error": 5,
    }
    min_level = relevance_order.get(min_relevance, 2)

    filtered = []
    for p in papers:
        clf = p.get("classification", {})

        # Relevance filter
        rel = clf.get("ai_relevance", "unknown")
        if relevance_order.get(rel, 5) > min_level:
            continue

        # Domain filter
        if domain:
            domains = clf.get("domains", [])
            if not any(domain.lower() in d.lower() for d in domains):
                continue

        # Year filter
        year = p.get("year")
        if min_year and year and year < min_year:
            continue
        if max_year and year and year > max_year:
            continue

        filtered.append(p)

    return filtered


def format_authors(authors: list[dict], max_authors: int = 5) -> str:
    """Format author list."""
    names = [a.get("name", "Unknown") for a in authors]
    if len(names) > max_authors:
        return ", ".join(names[:max_authors]) + f", et al. ({len(names)} authors)"
    return ", ".join(names)


def format_authors_bibtex(authors: list[dict]) -> str:
    """Format authors for BibTeX."""
    names = []
    for a in authors:
        name = a.get("name", "")
        parts = name.split()
        if len(parts) >= 2:
            names.append(f"{parts[-1]}, {' '.join(parts[:-1])}")
        else:
            names.append(name)
    return " and ".join(names)


def make_bibtex_key(paper: dict) -> str:
    """Generate a BibTeX citation key."""
    authors = paper.get("authors", [])
    first_author = authors[0].get("name", "Unknown") if authors else "Unknown"
    last_name = first_author.split()[-1] if first_author.split() else "unknown"
    last_name = re.sub(r'[^\w]', '', last_name)
    year = paper.get("year", "nodate")
    title_word = ""
    title = paper.get("title", "")
    for word in title.split():
        clean = re.sub(r'[^\w]', '', word)
        if len(clean) > 3 and clean.lower() not in {"with", "from", "that", "this", "have", "been"}:
            title_word = clean.lower()
            break
    return f"{last_name}{year}{title_word}"


# ── Markdown Formatter ────────────────────────────────────────────────────────

def _render_paper_entry(p: dict) -> list[str]:
    """Render a single paper as Markdown lines."""
    lines = []
    clf = p.get("classification", {})
    authors_str = format_authors(p.get("authors", []))
    year = p.get("year", "n.d.")
    title = p.get("title", "Untitled")
    venue = p.get("venue", "")
    doi = p.get("doi", "")
    url = p.get("url", "")
    citation_count = p.get("citation_count")
    relevance = clf.get("ai_relevance", "unknown")
    annotation = clf.get("annotation", "")
    subfields = clf.get("ai_subfields", [])
    pub_type = p.get("publication_type", "")
    schools = p.get("bu_schools", [])

    # Format entry
    lines.append(f"#### {title}\n")
    lines.append(f"**{authors_str}** ({year})")

    if venue:
        lines.append(f"*{venue}*")

    # School affiliations
    if schools:
        lines.append(f"\nBU School(s): {', '.join(schools)}")

    # Metadata line
    meta_parts = []
    if relevance and relevance != "unknown":
        meta_parts.append(f"AI Relevance: **{relevance}**")
    if subfields:
        meta_parts.append(f"Subfields: {', '.join(subfields)}")
    if citation_count is not None:
        meta_parts.append(f"Citations: {citation_count}")
    if pub_type and pub_type in ("grant", "preprint"):
        meta_parts.append(f"Type: **{pub_type}**")
    if meta_parts:
        lines.append(f"\n{' | '.join(meta_parts)}")

    # Links
    link_parts = []
    if doi:
        link_parts.append(f"[DOI](https://doi.org/{doi})")
    if url:
        link_parts.append(f"[Source]({url})")
    pdf_url = p.get("pdf_url")
    if pdf_url:
        link_parts.append(f"[PDF]({pdf_url})")
    if link_parts:
        lines.append(f"\n{' · '.join(link_parts)}")

    # Annotation
    if annotation:
        lines.append(f"\n> {annotation}")

    lines.append("")  # blank line
    return lines


def to_markdown(papers: list[dict]) -> str:
    """
    Generate a Markdown annotated bibliography organized by:
      1. LAW vs NON-LAW (primary split)
      2. School/Department (secondary grouping)
      3. Domain (tertiary, within each school)
      4. Year descending (sort order within each group)
    
    Papers with authors in BOTH law and another school appear in both sections.
    """
    # Partition papers
    law_papers = []
    nonlaw_papers = []
    unclassified_papers = []

    for p in papers:
        cat = p.get("bu_category", "UNCLASSIFIED")
        if cat == "LAW":
            law_papers.append(p)
        elif cat == "NON-LAW":
            nonlaw_papers.append(p)
        elif cat == "BOTH":
            # Cross-school papers appear in BOTH sections
            law_papers.append(p)
            nonlaw_papers.append(p)
        else:
            unclassified_papers.append(p)

    lines = []
    lines.append("# BU Faculty AI Research — Annotated Bibliography")
    lines.append(f"\n*Generated: {datetime.now().strftime('%B %d, %Y')}*")
    lines.append(f"\n*Total papers: {len(papers)}*")
    lines.append(f"*LAW faculty: {len(law_papers)} · NON-LAW faculty: {len(nonlaw_papers)} · "
                 f"Unclassified: {len(unclassified_papers)}*\n")

    # Table of contents
    lines.append("## Table of Contents\n")
    lines.append(f"- [Part I: School of Law Faculty](#part-i-school-of-law-faculty) "
                 f"({len(law_papers)} papers)")
    lines.append(f"- [Part II: Non-Law Faculty](#part-ii-non-law-faculty) "
                 f"({len(nonlaw_papers)} papers)")
    if unclassified_papers:
        lines.append(f"- [Part III: Unclassified](#part-iii-unclassified) "
                     f"({len(unclassified_papers)} papers)")

    lines.append("\n---\n")

    # ── PART I: LAW ──
    lines.append("# Part I: School of Law Faculty\n")
    lines.extend(_render_section_by_school(law_papers, "LAW"))

    # ── PART II: NON-LAW ──
    lines.append("\n---\n")
    lines.append("# Part II: Non-Law Faculty\n")
    lines.extend(_render_section_by_school(nonlaw_papers, "NON-LAW"))

    # ── PART III: UNCLASSIFIED ──
    if unclassified_papers:
        lines.append("\n---\n")
        lines.append("# Part III: Unclassified\n")
        lines.append("*These papers have BU authors whose school/department could not be "
                     "determined from available affiliation data. Manual review recommended.*\n")
        unclassified_papers.sort(
            key=lambda p: (-(p.get("year") or 0), -(p.get("citation_count") or 0))
        )
        for p in unclassified_papers:
            lines.extend(_render_paper_entry(p))

    return "\n".join(lines)


def _render_section_by_school(papers: list[dict], section_type: str) -> list[str]:
    """Render a section organized by school, then by domain."""
    lines = []

    if section_type == "LAW":
        # For LAW, group by topic domain (all same school)
        by_domain = defaultdict(list)
        for p in papers:
            clf = p.get("classification", {})
            domains = clf.get("domains", ["Uncategorized"])
            primary_domain = domains[0] if domains else "Uncategorized"
            by_domain[primary_domain].append(p)

        for domain in sorted(by_domain.keys()):
            domain_papers = by_domain[domain]
            domain_papers.sort(
                key=lambda p: (-(p.get("year") or 0), -(p.get("citation_count") or 0))
            )
            lines.append(f"## {domain} ({len(domain_papers)} papers)\n")
            for p in domain_papers:
                lines.extend(_render_paper_entry(p))
    else:
        # For NON-LAW, group by school first, then domain within each school
        by_school = defaultdict(list)
        for p in papers:
            schools = p.get("bu_schools", ["Unknown"])
            # Use the first non-Law school
            assigned = False
            for s in schools:
                if "Law" not in s:
                    by_school[s].append(p)
                    assigned = True
                    break
            if not assigned:
                by_school[schools[0] if schools else "Unknown"].append(p)

        for school in sorted(by_school.keys()):
            school_papers = by_school[school]
            lines.append(f"## {school} ({len(school_papers)} papers)\n")

            # Sub-group by domain within this school
            by_domain = defaultdict(list)
            for p in school_papers:
                clf = p.get("classification", {})
                domains = clf.get("domains", ["Uncategorized"])
                primary_domain = domains[0] if domains else "Uncategorized"
                by_domain[primary_domain].append(p)

            for domain in sorted(by_domain.keys()):
                domain_papers = by_domain[domain]
                domain_papers.sort(
                    key=lambda p: (-(p.get("year") or 0), -(p.get("citation_count") or 0))
                )
                lines.append(f"### {domain} ({len(domain_papers)} papers)\n")
                for p in domain_papers:
                    lines.extend(_render_paper_entry(p))

    return lines


# ── BibTeX Formatter ──────────────────────────────────────────────────────────

def to_bibtex(papers: list[dict]) -> str:
    """Generate BibTeX entries."""
    entries = []
    seen_keys = set()

    for p in papers:
        key = make_bibtex_key(p)
        # Handle duplicate keys
        if key in seen_keys:
            suffix = 'b'
            while f"{key}{suffix}" in seen_keys:
                suffix = chr(ord(suffix) + 1)
            key = f"{key}{suffix}"
        seen_keys.add(key)

        pub_type = p.get("publication_type", "article")
        if "preprint" in str(pub_type).lower():
            entry_type = "misc"
        elif "proceedings" in str(pub_type).lower() or "conference" in str(pub_type).lower():
            entry_type = "inproceedings"
        elif "thesis" in str(pub_type).lower() or "dissertation" in str(pub_type).lower():
            entry_type = "phdthesis"
        else:
            entry_type = "article"

        fields = []
        fields.append(f"  title = {{{p.get('title', '')}}}")
        fields.append(f"  author = {{{format_authors_bibtex(p.get('authors', []))}}}")

        if p.get("year"):
            fields.append(f"  year = {{{p['year']}}}")
        if p.get("venue"):
            if entry_type == "inproceedings":
                fields.append(f"  booktitle = {{{p['venue']}}}")
            else:
                fields.append(f"  journal = {{{p['venue']}}}")
        if p.get("doi"):
            fields.append(f"  doi = {{{p['doi']}}}")
        if p.get("url"):
            fields.append(f"  url = {{{p['url']}}}")

        # Add annotation as a note
        clf = p.get("classification", {})
        if clf.get("annotation"):
            # Escape special BibTeX chars
            annotation = clf["annotation"].replace("{", "\\{").replace("}", "\\}")
            fields.append(f"  note = {{{annotation}}}")
        if clf.get("ai_relevance"):
            fields.append(f"  keywords = {{{clf['ai_relevance']}, {', '.join(clf.get('domains', []))}}}")

        entry = f"@{entry_type}{{{key},\n" + ",\n".join(fields) + "\n}\n"
        entries.append(entry)

    header = (
        f"% BU Faculty AI Research — Annotated Bibliography\n"
        f"% Generated: {datetime.now().strftime('%Y-%m-%d')}\n"
        f"% Total entries: {len(entries)}\n\n"
    )
    return header + "\n".join(entries)


# ── Summary Statistics ────────────────────────────────────────────────────────

def generate_stats(papers: list[dict]) -> str:
    """Generate summary statistics for the bibliography."""
    from collections import Counter

    lines = []
    lines.append("# BU AI Bibliography — Summary Statistics\n")
    lines.append(f"Total papers: {len(papers)}\n")

    # LAW vs NON-LAW breakdown
    cat_counts = Counter(p.get("bu_category", "UNCLASSIFIED") for p in papers)
    lines.append("## LAW vs NON-LAW")
    for cat in ["LAW", "NON-LAW", "BOTH", "UNCLASSIFIED"]:
        count = cat_counts.get(cat, 0)
        pct = count / len(papers) * 100 if papers else 0
        lines.append(f"  {cat:<20} {count:>6} ({pct:.1f}%)")

    # By BU School
    school_counts = Counter()
    for p in papers:
        for s in p.get("bu_schools", []):
            school_counts[s] += 1
    lines.append("\n## By BU School/Department")
    for school, count in school_counts.most_common():
        lines.append(f"  {school:<50} {count:>6}")

    # By publication type (show grants and preprints prominently)
    type_counts = Counter(p.get("publication_type", "unknown") for p in papers)
    lines.append("\n## By Publication Type")
    for ptype, count in type_counts.most_common(15):
        lines.append(f"  {str(ptype):<30} {count:>6}")

    # By relevance
    relevance = Counter(
        p.get("classification", {}).get("ai_relevance", "unclassified")
        for p in papers
    )
    lines.append("## By AI Relevance")
    for rel, count in relevance.most_common():
        pct = count / len(papers) * 100
        lines.append(f"  {rel:<20} {count:>6} ({pct:.1f}%)")

    # By domain
    domain_counts = Counter()
    for p in papers:
        for d in p.get("classification", {}).get("domains", []):
            domain_counts[d] += 1
    lines.append("\n## By Domain (papers may appear in multiple)")
    for domain, count in domain_counts.most_common(20):
        lines.append(f"  {domain:<35} {count:>6}")

    # By subfield
    subfield_counts = Counter()
    for p in papers:
        for s in p.get("classification", {}).get("ai_subfields", []):
            subfield_counts[s] += 1
    lines.append("\n## By AI Subfield")
    for sf, count in subfield_counts.most_common(20):
        lines.append(f"  {sf:<35} {count:>6}")

    # By year
    year_counts = Counter(p.get("year") for p in papers if p.get("year"))
    lines.append("\n## By Year (recent)")
    for year in sorted(year_counts.keys(), reverse=True)[:15]:
        count = year_counts[year]
        bar = "█" * (count // 3)
        lines.append(f"  {year}  {count:>5}  {bar}")

    # Most-cited
    cited = sorted(
        [p for p in papers if p.get("citation_count")],
        key=lambda p: p["citation_count"],
        reverse=True,
    )[:20]
    lines.append("\n## Top 20 Most-Cited Papers")
    for p in cited:
        first_author = p.get("authors", [{}])[0].get("name", "?")
        lines.append(
            f"  [{p['citation_count']:>5} citations] "
            f"{first_author} ({p.get('year', '?')}) — "
            f"{p.get('title', '?')[:70]}"
        )

    # Prolific authors
    author_counts = Counter()
    for p in papers:
        for a in p.get("authors", []):
            if a.get("is_bu"):
                author_counts[a["name"]] += 1
    lines.append("\n## Most Prolific BU Authors (AI papers)")
    for name, count in author_counts.most_common(30):
        lines.append(f"  {name:<35} {count:>4} papers")

    return "\n".join(lines)


def to_csv(papers: list[dict]) -> str:
    """Generate CSV summary."""
    import csv, io
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "title", "authors", "year", "doi", "venue", "citation_count",
        "ai_relevance", "confidence", "publication_status", "one_line_summary",
        "domains", "subfields", "annotation",
        "bu_category", "bu_schools", "bu_author_names",
        "best_url", "is_open_access", "source", "all_sources",
    ])
    for p in papers:
        clf = p.get("classification", {})
        authors_str = "; ".join(a.get("name", "") for a in p.get("authors", []))
        bu_names = [a.get("name", "") for a in p.get("authors", []) if a.get("is_bu")]
        doi = p.get("doi", "")
        best_url = f"https://doi.org/{doi}" if doi else p.get("pdf_url") or p.get("url") or ""
        writer.writerow([
            p.get("title", ""),
            authors_str,
            p.get("year", ""),
            doi,
            p.get("venue", ""),
            p.get("citation_count", ""),
            clf.get("ai_relevance", ""),
            clf.get("confidence", ""),
            clf.get("publication_status", ""),
            clf.get("one_line_summary", ""),
            "; ".join(clf.get("domains", [])),
            "; ".join(clf.get("ai_subfields", [])),
            clf.get("annotation", ""),
            p.get("bu_category", ""),
            "; ".join(p.get("bu_schools", [])),
            "; ".join(bu_names),
            best_url,
            p.get("is_open_access", ""),
            p.get("source", ""),
            "; ".join(p.get("all_sources", [p.get("source", "")])),
        ])
    return buf.getvalue()


def main():
    parser = argparse.ArgumentParser(description="Format classified bibliography")
    parser.add_argument("input_file", help="Path to classified JSON")
    parser.add_argument("--format", choices=["markdown", "bibtex", "csv", "stats", "all"],
                        default="all", help="Output format")
    parser.add_argument("--min-relevance",
                        choices=["primary", "methodological", "peripheral"],
                        default="methodological",
                        help="Minimum AI relevance to include")
    parser.add_argument("--domain", help="Filter to specific domain")
    parser.add_argument("--min-year", type=int, help="Minimum publication year")
    parser.add_argument("--max-year", type=int, help="Maximum publication year")
    parser.add_argument("--output-dir", default="data", help="Output directory")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

    papers = load_papers(args.input_file)
    logger.info(f"Loaded {len(papers)} papers")

    # Filter
    papers = filter_papers(
        papers,
        min_relevance=args.min_relevance,
        domain=args.domain,
        min_year=args.min_year,
        max_year=args.max_year,
    )
    logger.info(f"After filtering: {len(papers)} papers")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.format in ("markdown", "all"):
        md = to_markdown(papers)
        path = output_dir / f"bu_ai_bibliography_{ts}.md"
        path.write_text(md, encoding='utf-8')
        print(f"Markdown: {path}")

    if args.format in ("bibtex", "all"):
        bib = to_bibtex(papers)
        path = output_dir / f"bu_ai_bibliography_{ts}.bib"
        path.write_text(bib, encoding='utf-8')
        print(f"BibTeX: {path}")

    if args.format in ("csv", "all"):
        csv_out = to_csv(papers)
        path = output_dir / f"bu_ai_bibliography_{ts}.csv"
        path.write_text(csv_out, encoding='utf-8')
        print(f"CSV: {path}")

    if args.format in ("stats", "all"):
        stats = generate_stats(papers)
        path = output_dir / f"bu_ai_bibliography_stats_{ts}.md"
        path.write_text(stats, encoding='utf-8')
        print(f"Stats: {path}")
        print(stats)


if __name__ == "__main__":
    main()
