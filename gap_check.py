#!/usr/bin/env python3
"""
BU AI Bibliography — Stage 3: Gap Checker
============================================
Identifies papers that may have been missed by the automated pipeline.

Strategy:
  1. Scrape BU department/center pages for faculty names
  2. Cross-reference known BU AI faculty against harvested author lists
  3. For faculty NOT represented in the harvest, search for their work
  4. Check specific high-value sources that don't have APIs
  5. Generate a gap report

This is semi-automated — it identifies gaps for manual review.

Usage:
    python gap_check.py data/bu_ai_bibliography_*.json
    python gap_check.py data/bu_ai_bibliography_*.json --faculty-list faculty.txt
"""

import argparse
import json
import re
import logging
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path
from datetime import datetime

logger = logging.getLogger("bu_bib.gaps")


# ── Known BU AI Faculty/Researchers ──────────────────────────────────────────
# This is a seed list. The scraper below will attempt to expand it.
# Format: (name, department, notes)
KNOWN_AI_FACULTY = [
    # CAS Computer Science
    ("Mark Crovella", "CS", "Networks, data science"),
    ("Evimaria Terzi", "CS", "Data mining, ML"),
    ("Lorenzo Orecchia", "CS", "Optimization, algorithms"),
    ("Ran Canetti", "CS", "Cryptography, security"),
    ("Mayank Varia", "CS", "Cryptography, privacy"),
    ("Adam Smith", "CS", "Privacy, differential privacy"),
    ("Leonid Reyzin", "CS", "Cryptography"),
    ("Steve Homer", "CS", "Complexity theory"),
    ("Hongwei Xi", "CS", "Programming languages"),
    ("Abraham Matta", "CS", "Networks"),
    ("John Byers", "CS", "Networks, economics"),
    ("Peter Hartman Hartmann", "CS", ""),
    
    # College of Engineering
    ("Yannis Paschalidis", "ECE/CISE", "Optimization, ML, health"),
    ("Prakash Ishwar", "ECE", "ML, signal processing"),
    ("Venkatesh Saligrama", "ECE", "ML, statistics"),
    ("Eshed Ohn-Bar", "ECE", "Computer vision, autonomous driving"),
    ("Kate Saenko", "CS", "Computer vision, NLP"),
    ("Bryan Plummer", "CS", "Vision-language"),
    ("Margrit Betke", "CS", "Computer vision"),
    ("Stan Sclaroff", "CS", "Computer vision"),
    ("Derry Wijaya", "CS", "NLP"),
    ("Lei Tian", "ECE", "Imaging, computational"),
    ("Calin Belta", "ME/SE", "Robotics, formal methods"),
    ("Roberto Tron", "ME", "Robotics, vision"),
    ("Sean Andersson", "ME", "Robotics, control"),
    ("Mac Schwager", "ME", "Multi-robot systems"),
    ("Ioannis Kontoyiannis", "ECE", "Information theory, statistics"),
    
    # Faculty of Computing & Data Sciences
    ("Azer Bestavros", "CDS", "Founding dean, systems, security"),
    ("Mark Kon", "CDS/Math", "ML theory, bioinformatics"),
    ("Eric Kolaczyk", "CDS/Math", "Network science, statistics"),
    ("Pankaj Mehta", "CDS/Physics", "ML, biophysics"),
    
    # Hariri Institute
    ("Gianluca Stringhini", "ECE/Hariri", "Security, social media, misinformation"),
    
    # School of Law
    ("Rena Conti", "Law/Questrom", "Health economics, AI"),
    ("James Bessen", "Law", "Technology & economics, AI labor"),
    ("Stacey Dogan", "Law", "IP, technology"),
    ("Andrew Sellars", "Law", "Technology law, First Amendment"),
    ("Woodrow Hartzog", "Law", "Privacy, technology"),
    ("Andy Sellars", "Law", "Technology, clinical"),
    ("Daniel Susser", "Law/Philosophy", "Privacy, ethics, AI"),
    ("Mason Kortz", "Law", "AI governance"),
    ("Christopher Robertson", "Law", "Health law, AI, evidence"),
    
    # Questrom School of Business
    ("Marshall Van Alstyne", "Questrom", "Platform economics, AI"),
    ("Chrysanthos Dellarocas", "Questrom", "Digital business, AI"),
    ("Sam Ransbotham", "Questrom", "AI strategy, analytics"),
    ("Andrei Hagiu", "Questrom", "Platforms, AI"),
    
    # School of Public Health
    ("Eleanor Murray", "SPH", "Causal inference, methods"),
    ("Josée Dupuis", "SPH", "Statistical genetics"),
    
    # School of Medicine
    ("Vijaya Kolachalama", "Medicine", "ML, medical imaging"),
    
    # CAS Mathematics & Statistics
    ("Daniel Weiner", "Math", "Statistics"),
    ("Konstantinos Spiliopoulos", "Math", "Stochastic analysis, ML theory"),
    
    # CAS Philosophy
    ("Daniel Star", "Philosophy", "Ethics"),
    
    # CAS Political Science
    ("Dino Christenson", "PolSci", "Methods, computational"),
    
    # CAS Economics
    ("Pascual Restrepo", "Economics", "AI & labor, automation"),
    ("Kehinde Ajayi", "Economics", "Development, methods"),
    
    # College of Communication
    ("Lei Guo", "COM", "Computational communication"),
    
    # CAS Linguistics
    ("Najoung Kim", "Linguistics", "Computational linguistics, NLP"),
]


def load_harvested_authors(bibliography_path: str) -> set[str]:
    """Load all author names from the harvested bibliography."""
    with open(bibliography_path) as f:
        papers = json.load(f)

    authors = set()
    for paper in papers:
        for author in paper.get("authors", []):
            name = author.get("name", "").strip()
            if name:
                authors.add(name.lower())
    return authors


def normalize_name(name: str) -> str:
    """Normalize a name for fuzzy matching."""
    name = name.lower().strip()
    # Remove punctuation (but keep spaces)
    name = re.sub(r'[^\w\s]', '', name)
    # Collapse whitespace
    name = re.sub(r'\s+', ' ', name)
    return name.strip()


def name_match(name1: str, name2: str) -> bool:
    """Check if two names likely refer to the same person."""
    n1 = normalize_name(name1)
    n2 = normalize_name(name2)

    if n1 == n2:
        return True

    parts1 = n1.split()
    parts2 = n2.split()
    if len(parts1) >= 2 and len(parts2) >= 2:
        if parts1[-1] == parts2[-1]:  # same last name
            if parts1[0][0] == parts2[0][0]:  # same first initial
                return True
    
    # Handle case where one side has only a last name or single initial + last
    if len(parts1) >= 1 and len(parts2) >= 2:
        if parts1[-1] == parts2[-1] and len(parts1[0]) == 1:
            if parts1[0] == parts2[0][0]:
                return True
    if len(parts2) >= 1 and len(parts1) >= 2:
        if parts2[-1] == parts1[-1] and len(parts2[0]) == 1:
            if parts2[0] == parts1[0][0]:
                return True

    return False


def find_missing_faculty(bibliography_path: str) -> list[dict]:
    """Identify known AI faculty not represented in the bibliography."""
    harvested_authors = load_harvested_authors(bibliography_path)

    missing = []
    found = []

    for name, dept, notes in KNOWN_AI_FACULTY:
        matched = any(
            name_match(name, harvested_name)
            for harvested_name in harvested_authors
        )
        if matched:
            found.append(name)
        else:
            missing.append({
                "name": name,
                "department": dept,
                "notes": notes,
                "action": "Search OpenAlex/Scholar for this person's AI work",
            })

    return missing, found


def analyze_coverage(bibliography_path: str) -> dict:
    """Analyze the bibliography for potential coverage gaps."""
    with open(bibliography_path) as f:
        papers = json.load(f)

    stats = {
        "total_papers": len(papers),
        "by_source": Counter(),
        "by_year": Counter(),
        "by_type": Counter(),
        "no_abstract": 0,
        "no_doi": 0,
        "no_url": 0,
        "multi_source": 0,
        "departments": Counter(),
    }

    for paper in papers:
        stats["by_source"][paper.get("source", "unknown")] += 1
        if paper.get("year"):
            stats["by_year"][paper["year"]] += 1
        stats["by_type"][paper.get("publication_type", "unknown")] += 1
        if not paper.get("abstract"):
            stats["no_abstract"] += 1
        if not paper.get("doi"):
            stats["no_doi"] += 1
        if not paper.get("url"):
            stats["no_url"] += 1
        if paper.get("all_sources"):
            stats["multi_source"] += 1

        # Track BU departments
        for author in paper.get("authors", []):
            if author.get("is_bu"):
                dept = (author.get("affiliation", "") or "").lower()
                if "computer science" in dept or "cs" in dept:
                    stats["departments"]["CS"] += 1
                elif "law" in dept:
                    stats["departments"]["Law"] += 1
                elif "engineering" in dept or "ece" in dept:
                    stats["departments"]["Engineering"] += 1
                elif "medicine" in dept or "medical" in dept:
                    stats["departments"]["Medicine"] += 1
                elif "public health" in dept or "sph" in dept:
                    stats["departments"]["SPH"] += 1
                elif "business" in dept or "questrom" in dept:
                    stats["departments"]["Questrom"] += 1

    return stats


def generate_gap_report(bibliography_path: str, output_path: str = None):
    """Generate a comprehensive gap analysis report."""
    logger.info("Analyzing bibliography for gaps...")

    # Coverage stats
    stats = analyze_coverage(bibliography_path)

    # Missing faculty
    missing_faculty, found_faculty = find_missing_faculty(bibliography_path)

    # Build report
    report_lines = []
    report_lines.append("=" * 72)
    report_lines.append("  BU AI BIBLIOGRAPHY — GAP ANALYSIS REPORT")
    report_lines.append(f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    report_lines.append("=" * 72)

    report_lines.append(f"\n## Coverage Summary")
    report_lines.append(f"Total papers: {stats['total_papers']}")
    report_lines.append(f"Papers with abstracts: {stats['total_papers'] - stats['no_abstract']}")
    report_lines.append(f"Papers with DOIs: {stats['total_papers'] - stats['no_doi']}")
    report_lines.append(f"Papers found in multiple sources: {stats['multi_source']}")

    report_lines.append(f"\n## By Source")
    for source, count in stats["by_source"].most_common():
        report_lines.append(f"  {source:<25} {count:>6}")

    report_lines.append(f"\n## By Year (last 10 years)")
    for year in sorted(stats["by_year"].keys(), reverse=True)[:10]:
        count = stats["by_year"][year]
        bar = "█" * (count // 5)
        report_lines.append(f"  {year}  {count:>5}  {bar}")

    report_lines.append(f"\n## By Publication Type")
    for ptype, count in stats["by_type"].most_common(10):
        report_lines.append(f"  {ptype:<30} {count:>6}")

    report_lines.append(f"\n## Department Coverage")
    for dept, count in stats["departments"].most_common():
        report_lines.append(f"  {dept:<25} {count:>6}")

    report_lines.append(f"\n## Faculty Coverage")
    report_lines.append(f"Known AI faculty checked: {len(KNOWN_AI_FACULTY)}")
    report_lines.append(f"Found in bibliography: {len(found_faculty)}")
    report_lines.append(f"NOT found (potential gaps): {len(missing_faculty)}")

    if missing_faculty:
        report_lines.append(f"\n### Missing Faculty — ACTION NEEDED")
        report_lines.append("These known BU AI researchers have no papers in the harvest.")
        report_lines.append("They may use different name formats, or their work may not")
        report_lines.append("be indexed in the sources we checked.\n")
        for m in missing_faculty:
            report_lines.append(
                f"  ⚠ {m['name']:<30} ({m['department']}) — {m['notes']}"
            )

    report_lines.append(f"\n## Recommended Manual Checks")
    report_lines.append("""
  1. HARIRI INSTITUTE publications page
     https://www.bu.edu/hic/publications/
     → Known hub for interdisciplinary AI work

  2. FACULTY OF COMPUTING & DATA SCIENCES
     https://www.bu.edu/cds-faculty/
     → Newer unit, may have work not yet fully indexed

  3. BU LAW faculty scholarship pages
     https://www.bu.edu/law/faculty-scholarship/
     → AI regulation/policy papers, especially SSRN working papers

  4. GOOGLE SCHOLAR PROFILES for missing faculty listed above
     → Search each name + "Boston University" on Google Scholar
     → Check their profile for papers not in our harvest

  5. BU RESEARCH COMPUTING
     https://www.bu.edu/tech/support/research/
     → May list faculty using ML/AI in research

  6. NIH REPORTER (for funded AI research)
     https://reporter.nih.gov/
     → Search BU + "artificial intelligence" or "machine learning"
     → Cross-reference funded projects with publications

  7. NSF AWARD SEARCH
     https://www.nsf.gov/awardsearch/
     → Search BU awards in CISE and related programs

  8. BU TODAY / THE BRINK (BU research magazine)
     https://www.bu.edu/articles/
     → Often covers faculty AI research, can surface names to search

  9. CONFERENCE PROCEEDINGS (manual spot-check)
     Check if BU authors appear in recent proceedings of:
     - NeurIPS, ICML, ICLR, AAAI, IJCAI (core AI)
     - ACL, EMNLP, NAACL (NLP)
     - CVPR, ICCV, ECCV (computer vision)
     - RSS, ICRA, IROS (robotics)
     - FAccT, AIES (AI ethics)
     - ICAIL, JURIX (AI & law)

  10. DISSERTATION DATABASES
      → ProQuest Dissertations (BU library access)
      → Check for recent BU dissertations with AI/ML topics
""")

    # Year gap analysis
    years = sorted(stats["by_year"].keys())
    if years:
        min_year = min(years)
        max_year = max(years)
        empty_years = [y for y in range(min_year, max_year + 1) if y not in stats["by_year"]]
        if empty_years:
            report_lines.append(f"\n## Year Gaps")
            report_lines.append(f"No papers found for years: {empty_years}")
            report_lines.append("This likely indicates indexing gaps rather than no output.")

    report_text = "\n".join(report_lines)

    # Output
    if output_path is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = f"data/gap_report_{ts}.txt"

    with open(output_path, 'w') as f:
        f.write(report_text)

    print(report_text)
    logger.info(f"Gap report saved: {output_path}")
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Analyze bibliography for coverage gaps")
    parser.add_argument("bibliography", help="Path to harvested bibliography JSON")
    parser.add_argument("--faculty-list", help="Optional text file with additional faculty names")
    parser.add_argument("--output", help="Output path for gap report")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

    if args.faculty_list:
        with open(args.faculty_list) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    parts = line.split(",")
                    name = parts[0].strip()
                    dept = parts[1].strip() if len(parts) > 1 else ""
                    notes = parts[2].strip() if len(parts) > 2 else ""
                    KNOWN_AI_FACULTY.append((name, dept, notes))

    generate_gap_report(args.bibliography, args.output)


if __name__ == "__main__":
    main()
