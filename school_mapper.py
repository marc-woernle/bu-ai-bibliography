"""
BU AI Bibliography — School & Department Classifier
======================================================
Maps each paper to the BU school(s) and department(s) of its BU-affiliated
authors. Primary partition: LAW vs NON-LAW. Secondary: specific school/dept.

Strategy:
  1. Match author affiliations against known BU school/dept patterns
  2. Cross-reference against a known faculty → department lookup table
  3. For ambiguous cases, flag for manual review

The output adds two fields to each paper:
  - bu_schools: list of BU schools represented (e.g., ["School of Law", "CAS"])
  - bu_category: "LAW" | "NON-LAW" | "BOTH" (if authors from law + another school)
"""

import re
import json
import logging
import argparse
import unicodedata
from collections import defaultdict
from pathlib import Path
from datetime import datetime

logger = logging.getLogger("bu_bib.school_map")

ROSTER_PATH = Path("data/bu_faculty_roster_verified.json")


# ── Affiliation Pattern Matching ──────────────────────────────────────────────
# Order matters: more specific patterns first.

SCHOOL_PATTERNS = [
    # LAW
    (r"school of law|law school|bu law|boston university law|bulaw",
     "School of Law", "LAW"),

    # MEDICINE / HEALTH
    (r"school of medicine|medical school|busm|bumc|medical center|medical campus",
     "School of Medicine", "NON-LAW"),
    (r"school of public health|sph\b|public health",
     "School of Public Health", "NON-LAW"),
    (r"school of dental medicine|dental|henry m\. goldman",
     "School of Dental Medicine", "NON-LAW"),
    (r"sargent college|sargent|health.* rehabilitation",
     "Sargent College of Health & Rehabilitation Sciences", "NON-LAW"),
    (r"school of social work|ssw\b|social work",
     "School of Social Work", "NON-LAW"),
    (r"chobanian.* avedisian|school of medicine",
     "Chobanian & Avedisian School of Medicine", "NON-LAW"),

    # ENGINEERING / COMPUTING
    (r"college of engineering|coe\b|eng\b.*bu|ece\b|electrical.*computer.*eng|"
     r"mechanical eng|biomedical eng|systems eng|materials sci.*eng",
     "College of Engineering", "NON-LAW"),
    (r"computing.*data sci|cds\b|faculty of computing",
     "Faculty of Computing & Data Sciences", "NON-LAW"),

    # CAS — specific departments first
    (r"computer science|cs dept|cas.*cs\b",
     "CAS — Computer Science", "NON-LAW"),
    (r"mathematics|math dept|statistics.*dept|math.*stat",
     "CAS — Mathematics & Statistics", "NON-LAW"),
    (r"economics dept|cas.*econ",
     "CAS — Economics", "NON-LAW"),
    (r"political sci|polisci|cas.*poli",
     "CAS — Political Science", "NON-LAW"),
    (r"philosophy dept|cas.*phil",
     "CAS — Philosophy", "NON-LAW"),
    (r"psychology dept|cas.*psych",
     "CAS — Psychology & Brain Sciences", "NON-LAW"),
    (r"biology dept|cas.*bio\b",
     "CAS — Biology", "NON-LAW"),
    (r"physics dept|cas.*phys",
     "CAS — Physics", "NON-LAW"),
    (r"chemistry dept|cas.*chem",
     "CAS — Chemistry", "NON-LAW"),
    (r"linguistics|cas.*ling",
     "CAS — Linguistics", "NON-LAW"),
    (r"earth.*environment|cas.*earth",
     "CAS — Earth & Environment", "NON-LAW"),
    (r"international rel|cas.*ir\b",
     "CAS — International Relations", "NON-LAW"),
    (r"sociology|cas.*soc\b",
     "CAS — Sociology", "NON-LAW"),
    (r"college of arts.*sciences|cas\b",
     "CAS (unspecified department)", "NON-LAW"),

    # BUSINESS
    (r"questrom|school of business|business school|business admin",
     "Questrom School of Business", "NON-LAW"),

    # EDUCATION
    (r"wheelock|college of education|education.*human dev",
     "Wheelock College of Education & Human Development", "NON-LAW"),

    # COMMUNICATION
    (r"college of communication|com\b.*bu|communication dept",
     "College of Communication", "NON-LAW"),

    # GLOBAL STUDIES / PARDEE
    (r"pardee|global studies|frederick s\. pardee",
     "Pardee School of Global Studies", "NON-LAW"),

    # FINE ARTS
    (r"college of fine arts|cfa\b|fine arts",
     "College of Fine Arts", "NON-LAW"),

    # THEOLOGY
    (r"school of theology|sth\b|theology",
     "School of Theology", "NON-LAW"),

    # RESEARCH CENTERS (cross-cutting, assign to NON-LAW but note center)
    (r"hariri|hic\b|hariri institute",
     "Hariri Institute for Computing", "NON-LAW"),
    (r"cise\b|center for info.*systems|information.*systems eng",
     "Center for Information & Systems Engineering", "NON-LAW"),
    (r"bu spark|spark!",
     "BU Spark!", "NON-LAW"),
    (r"rafik b\. hariri",
     "Hariri Institute for Computing", "NON-LAW"),
    (r"red lab|research on ethical.*data",
     "RED Lab (Research on Ethical Data)", "NON-LAW"),

    # Catch-all BU
    (r"boston university|bu\b",
     "Boston University (unspecified)", "NON-LAW"),
]

# Compile patterns for performance
_COMPILED_PATTERNS = [
    (re.compile(pattern, re.IGNORECASE), school, category)
    for pattern, school, category in SCHOOL_PATTERNS
]


# ── Faculty Roster Loader ────────────────────────────────────────────────────
# Loads from bu_faculty_roster_verified.json (6K+ entries with OpenAlex IDs)
# instead of a hardcoded list. Falls back to empty dict if file missing.

FACULTY_BY_OAID = {}     # openalex_id → (name, school, category)
FACULTY_BY_FULLNAME = {} # "last first" → [(school, category)]
FACULTY_BY_ALTNAME = {}  # normalized_name → (school, category)  [from OpenAlex alt_names cache]


def _normalize_name(name: str) -> str:
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    name = name.lower().strip()
    name = re.sub(r"[^a-z\s-]", "", name)
    return re.sub(r"\s+", " ", name)


def _name_key(name: str) -> str:
    parts = _normalize_name(name).split()
    if len(parts) < 2:
        return _normalize_name(name)
    return f"{parts[-1]} {parts[0]}"


ALTNAMES_CACHE_PATH = Path("data/openalex_bu_authors_cache.json")


def _load_faculty_roster():
    """Load faculty roster and build lookup indexes."""
    global FACULTY_BY_OAID, FACULTY_BY_FULLNAME, FACULTY_BY_ALTNAME

    if not ROSTER_PATH.exists():
        logger.warning(f"Roster not found at {ROSTER_PATH}, using empty lookup")
        return

    with open(ROSTER_PATH) as f:
        roster = json.load(f)

    for entry in roster:
        name = entry.get("name", "")
        school = entry.get("school", "Boston University (unspecified)")
        category = "LAW" if school == "School of Law" else "NON-LAW"
        oa_id = entry.get("openalex_id")

        # Index by OpenAlex ID
        if oa_id:
            FACULTY_BY_OAID[oa_id] = (name, school, category)

        # Index by full normalized name
        fkey = _name_key(name)
        FACULTY_BY_FULLNAME.setdefault(fkey, []).append((school, category))

    # Build alt_names index from OpenAlex cache (unambiguous roster matches only)
    if ALTNAMES_CACHE_PATH.exists():
        with open(ALTNAMES_CACHE_PATH) as f:
            cache = json.load(f)

        # normalized_name → set of OAIDs
        name_to_oaids = defaultdict(set)
        for entry in cache:
            oa_id = entry.get("id", "")
            for alt in entry.get("alt_names", []):
                norm = _normalize_name(alt)
                if norm:
                    name_to_oaids[norm].add(oa_id)

        # Only keep: one OAID per name, and that OAID must be in roster
        for norm_name, oaids in name_to_oaids.items():
            if len(oaids) == 1:
                oa_id = next(iter(oaids))
                if oa_id in FACULTY_BY_OAID:
                    _, school, category = FACULTY_BY_OAID[oa_id]
                    FACULTY_BY_ALTNAME[norm_name] = (school, category)

        logger.info(f"Loaded alt_names index: {len(FACULTY_BY_ALTNAME)} unambiguous entries")

    logger.info(
        f"Loaded faculty roster: {len(roster)} entries, "
        f"{len(FACULTY_BY_OAID)} OA IDs, {len(FACULTY_BY_FULLNAME)} name keys"
    )


# Load on import
_load_faculty_roster()


# ── Classification Functions ──────────────────────────────────────────────────

def classify_affiliation(affiliation_text: str) -> tuple[str, str] | None:
    """
    Classify an affiliation string into (school, category).
    Returns None if no match found.
    """
    if not affiliation_text:
        return None

    text = affiliation_text.lower()

    for pattern, school, category in _COMPILED_PATTERNS:
        if pattern.search(text):
            return (school, category)

    return None


def classify_author_by_name(author_name: str) -> tuple[str, str] | None:
    """
    Look up an author by full name in the faculty roster.
    Returns (school, category) or None. No initial-matching fallback.
    """
    fkey = _name_key(author_name)
    matches = FACULTY_BY_FULLNAME.get(fkey, [])
    if len(matches) == 1:
        return (matches[0][0], matches[0][1])
    if len(matches) > 1:
        # Multiple matches — only return if all point to same school
        schools = set(m[0] for m in matches)
        if len(schools) == 1:
            return (matches[0][0], matches[0][1])
    return None


def classify_author_by_altname(author_name: str) -> tuple[str, str] | None:
    """
    Look up an author via the OpenAlex alt_names cache.
    Returns (school, category) or None. Only returns unambiguous matches.
    """
    norm = _normalize_name(author_name)
    return FACULTY_BY_ALTNAME.get(norm)


def classify_author_by_openalex_id(oa_id: str) -> tuple[str, str, str] | None:
    """
    Look up an author by OpenAlex ID. Returns (name, school, category) or None.
    Zero false positives.
    """
    return FACULTY_BY_OAID.get(oa_id)


def classify_paper(paper: dict) -> dict:
    """
    Add school classification to a paper record.

    4-tier strategy per author:
      1. OpenAlex author ID → FACULTY_BY_OAID (zero false positives)
      2. Affiliation text → regex patterns
      3. Full-name → FACULTY_BY_FULLNAME (only for BU authors)
      4. Alt-names cache → FACULTY_BY_ALTNAME (only for BU authors, unambiguous)
      Fallback: is_bu but no school → "Boston University (unspecified)"

    Adds:
      paper["bu_schools"] — list of unique schools represented
      paper["bu_category"] — "LAW" | "NON-LAW" | "BOTH"
      paper["bu_authors_classified"] — per-author school assignments
    """
    schools = set()
    categories = set()
    author_classifications = []
    n_authors = len(paper.get("authors", []))
    is_big_paper = n_authors > 30

    for author in paper.get("authors", []):
        school = None
        category = None
        name = author.get("name", "")

        # Tier 1: OpenAlex author ID (highest confidence)
        oa_id = author.get("openalex_id")
        if oa_id:
            oa_result = classify_author_by_openalex_id(oa_id)
            if oa_result:
                _, school, category = oa_result

        # Tier 2: Affiliation text regex
        if school is None or school.endswith("(unspecified)"):
            aff = author.get("affiliation", "")
            if aff:
                result = classify_affiliation(aff)
                if result:
                    aff_school, aff_cat = result
                    if school is None or (
                        school.endswith("(unspecified)")
                        and not aff_school.endswith("(unspecified)")
                    ):
                        school, category = aff_school, aff_cat

        # Tier 3 & 4: Name-based matching — only for BU-affiliated authors,
        # and skip for CERN-style papers (>30 authors) to avoid false matches
        is_bu_author = author.get("is_bu") or (school is not None)
        if is_bu_author and not is_big_paper and name:
            if school is None or school.endswith("(unspecified)"):
                # Tier 3: Full-name roster match
                name_result = classify_author_by_name(name)
                if name_result:
                    school, category = name_result
                else:
                    # Tier 4: Alt-names cache match
                    alt_result = classify_author_by_altname(name)
                    if alt_result:
                        school, category = alt_result

        # Fallback: BU author but no school determined
        if school is None and author.get("is_bu"):
            school = "Boston University (unspecified)"
            category = "NON-LAW"

        if school:
            schools.add(school)
            categories.add(category)
            author_classifications.append({
                "name": name,
                "school": school,
                "category": category,
            })

    # Determine overall paper category
    if "LAW" in categories and len(categories - {"LAW"}) > 0:
        paper_category = "BOTH"
    elif "LAW" in categories:
        paper_category = "LAW"
    elif categories:
        paper_category = "NON-LAW"
    else:
        paper_category = "UNCLASSIFIED"

    paper["bu_schools"] = sorted(schools)
    paper["bu_category"] = paper_category
    paper["bu_authors_classified"] = author_classifications

    return paper


def classify_all(papers: list[dict]) -> list[dict]:
    """Classify all papers and return stats."""
    category_counts = defaultdict(int)
    school_counts = defaultdict(int)

    for paper in papers:
        classify_paper(paper)
        category_counts[paper["bu_category"]] += 1
        for school in paper.get("bu_schools", []):
            school_counts[school] += 1

    # Print summary
    logger.info("School classification summary:")
    logger.info(f"  LAW:          {category_counts.get('LAW', 0)}")
    logger.info(f"  NON-LAW:      {category_counts.get('NON-LAW', 0)}")
    logger.info(f"  BOTH:         {category_counts.get('BOTH', 0)}")
    logger.info(f"  UNCLASSIFIED: {category_counts.get('UNCLASSIFIED', 0)}")
    logger.info("")
    logger.info("By school:")
    for school, count in sorted(school_counts.items(), key=lambda x: -x[1]):
        logger.info(f"  {school:<50} {count:>5}")

    return papers


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Classify bibliography papers by BU school/department"
    )
    parser.add_argument("input_file", help="Path to bibliography JSON")
    parser.add_argument("--output", help="Output path (default: adds _schooled suffix)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

    with open(args.input_file) as f:
        papers = json.load(f)
    logger.info(f"Loaded {len(papers)} papers")

    classify_all(papers)

    # Save
    if args.output:
        output_path = args.output
    else:
        stem = Path(args.input_file).stem
        output_path = f"data/{stem}_schooled.json"

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(papers, f, ensure_ascii=False, indent=2)
    logger.info(f"Saved: {output_path}")

    # Print category breakdown
    law = [p for p in papers if p.get("bu_category") == "LAW"]
    nonlaw = [p for p in papers if p.get("bu_category") == "NON-LAW"]
    both = [p for p in papers if p.get("bu_category") == "BOTH"]
    unclassified = [p for p in papers if p.get("bu_category") == "UNCLASSIFIED"]

    print(f"\n{'='*50}")
    print(f"  LAW papers:          {len(law)}")
    print(f"  NON-LAW papers:      {len(nonlaw)}")
    print(f"  BOTH (cross-school): {len(both)}")
    print(f"  UNCLASSIFIED:        {len(unclassified)}")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
