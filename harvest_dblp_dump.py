"""
BU AI Bibliography -- DBLP XML Dump Harvester
===============================================
Streams the DBLP XML dump (~1GB gz) and extracts papers by BU faculty.

Two-tier matching to handle name ambiguity:
  - Unique names (704 faculty): trust the name match
  - Ambiguous names (63 faculty with common surnames): name-match only,
    but flag as "needs_verification". Downstream dedup + OpenAlex OAID
    verification will filter false positives.

DBLP names use disambiguation suffixes (e.g., "Wei Wang 0001").
We strip these before matching.

Usage:
    python harvest_dblp_dump.py                    # full run
    python harvest_dblp_dump.py --limit 100000     # stop after N records (testing)
    python harvest_dblp_dump.py --dry-run          # just count matches, don't save
"""

import argparse
import gzip
import json
import logging
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

from lxml import etree

from utils import (
    make_paper_record,
    normalize_doi,
    normalize_title,
    save_checkpoint,
    setup_logging,
    title_fingerprint,
)

logger = logging.getLogger("bu_bib.dblp_dump")

# DBLP record types we care about
RECORD_TAGS = {"article", "inproceedings", "incollection", "proceedings", "phdthesis"}

# Default dump path
DEFAULT_DUMP = Path(__file__).parent / "data" / "dblp-2026-03-01.xml.gz"

# Schools likely to publish in DBLP venues
DBLP_SCHOOLS = {
    "CAS — Computer Science",
    "College of Engineering",
    "Faculty of Computing & Data Sciences",
    "CAS — Mathematics & Statistics",
    "CAS — Physics",
}

# Common surnames that cause false positives in DBLP name matching
AMBIGUOUS_SURNAMES = {
    "wang", "zhang", "liu", "li", "chen", "yang", "sun", "kim",
    "lee", "wu", "zhou", "he", "zhao", "lin", "fang", "xia",
}


def _normalize_name(name: str) -> str:
    """Normalize a name: strip accents, lowercase, collapse whitespace."""
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_name = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", ascii_name.lower().strip())


def _strip_dblp_suffix(name: str) -> str:
    """Strip DBLP disambiguation suffix like ' 0001' from a name."""
    return re.sub(r"\s+\d{4}$", "", name)


def _is_ambiguous_name(normalized_name: str) -> bool:
    """Check if a name is likely to cause false positives."""
    parts = normalized_name.split()
    if not parts:
        return True
    last = parts[-1]
    return last in AMBIGUOUS_SURNAMES and len(parts) <= 2


def _build_name_index(roster_path: Path) -> tuple[dict, int, set]:
    """
    Build a lookup: normalized_name -> list of faculty records.
    Returns (index, count, ambiguous_names_set).
    """
    with open(roster_path) as f:
        roster = json.load(f)

    index = defaultdict(list)
    ambiguous = set()
    count = 0
    for entry in roster:
        school = entry.get("school", "")
        if school not in DBLP_SCHOOLS:
            continue
        name = entry.get("name", "")
        if not name:
            continue
        norm = _normalize_name(name)
        index[norm].append(entry)
        count += 1

        if _is_ambiguous_name(norm):
            ambiguous.add(norm)

        # Also index without middle initial: "John W. Byers" -> "john byers"
        parts = norm.split()
        if len(parts) == 3 and (len(parts[1]) <= 2 or parts[1].endswith(".")):
            short = f"{parts[0]} {parts[2]}"
            index[short].append(entry)
            if _is_ambiguous_name(short):
                ambiguous.add(short)

    return dict(index), count, ambiguous


def _load_existing_identifiers(master_path: Path) -> tuple[set, set]:
    """Load DOIs and title fingerprints from the master dataset for dedup."""
    with open(master_path) as f:
        master = json.load(f)

    dois = set()
    fps = set()
    for p in master:
        if p.get("doi"):
            dois.add(p["doi"].lower())
        fp = p.get("title_fingerprint") or title_fingerprint(p.get("title", ""))
        if fp:
            fps.add(fp)

    return dois, fps


def _extract_text(elem) -> str:
    """Extract all text content from an element (including sub-elements)."""
    text = elem.text_content() if hasattr(elem, "text_content") else "".join(elem.itertext())
    return text.strip()


def harvest_dump(
    dump_path: Path = DEFAULT_DUMP,
    limit: int = 0,
    dry_run: bool = False,
    since_year: int | None = None,
) -> list[dict]:
    """
    Stream-parse the DBLP XML dump and extract papers by BU faculty.

    Args:
        dump_path: Path to the DBLP XML gz dump file.
        limit: Stop after N records (for testing). 0 = no limit.
        dry_run: Just count matches, don't save papers.
        since_year: If provided, skip papers with year < since_year.
    """
    base_dir = Path(__file__).parent
    roster_path = base_dir / "data" / "bu_faculty_roster_verified.json"
    master_path = base_dir / "data" / "sonnet_classification_bu_verified.json"

    name_index, faculty_count, ambiguous_names = _build_name_index(roster_path)
    logger.info(f"Built name index: {len(name_index)} name variants for {faculty_count} faculty")
    logger.info(f"Ambiguous name variants: {len(ambiguous_names)}")

    existing_dois, existing_fps = _load_existing_identifiers(master_path)
    logger.info(f"Existing dataset: {len(existing_dois)} DOIs, {len(existing_fps)} title fingerprints")

    papers = []
    records_seen = 0
    matches_total = 0
    matches_unique = 0
    matches_ambiguous_kept = 0
    matches_ambiguous_skipped = 0
    already_have = 0
    faculty_hits = Counter()

    logger.info(f"Streaming {dump_path} ...")

    with gzip.open(dump_path, "rb") as gz:
        context = etree.iterparse(
            gz,
            events=("end",),
            tag=list(RECORD_TAGS),
            recover=True,
            resolve_entities=True,
            dtd_validation=False,
            load_dtd=True,
            huge_tree=True,
        )

        for event, elem in context:
            tag = elem.tag

            records_seen += 1
            if limit and records_seen > limit:
                break

            if records_seen % 1_000_000 == 0:
                logger.info(
                    f"  {records_seen:,} records | "
                    f"{matches_unique} unique + {matches_ambiguous_kept} ambig-kept + "
                    f"{matches_ambiguous_skipped} ambig-skipped | "
                    f"{already_have} already in dataset"
                )

            # Extract authors
            author_elems = elem.findall("author")
            if not author_elems:
                elem.clear()
                while elem.getprevious() is not None:
                    del elem.getparent()[0]
                continue

            # Check if any author matches our faculty
            matched_faculty = []
            matched_ambiguous_only = True
            raw_authors = []
            for a in author_elems:
                author_name = _extract_text(a)
                raw_authors.append(author_name)

                stripped = _strip_dblp_suffix(author_name)
                norm = _normalize_name(stripped)

                if norm in name_index:
                    matched_faculty.extend(name_index[norm])
                    if norm not in ambiguous_names:
                        matched_ambiguous_only = False

            if not matched_faculty:
                elem.clear()
                while elem.getprevious() is not None:
                    del elem.getparent()[0]
                continue

            matches_total += 1

            # Extract title early for dedup check
            title_elem = elem.find("title")
            title = _extract_text(title_elem) if title_elem is not None else ""
            if title.endswith("."):
                title = title[:-1]

            if not title:
                elem.clear()
                while elem.getprevious() is not None:
                    del elem.getparent()[0]
                continue

            # Extract DOI for dedup check
            doi = None
            for ee in elem.findall("ee"):
                ee_text = _extract_text(ee)
                if "doi.org/" in ee_text:
                    doi = ee_text.split("doi.org/", 1)[-1]
                    break

            norm_doi = normalize_doi(doi) if doi else None
            fp = title_fingerprint(title)

            # Check if we already have this paper
            if (norm_doi and norm_doi in existing_dois) or (fp and fp in existing_fps):
                already_have += 1
                elem.clear()
                while elem.getprevious() is not None:
                    del elem.getparent()[0]
                continue

            # For ambiguous-only matches, skip (too many false positives)
            if matched_ambiguous_only:
                matches_ambiguous_skipped += 1
                elem.clear()
                while elem.getprevious() is not None:
                    del elem.getparent()[0]
                continue

            # This is a match via a unique name
            if matched_ambiguous_only:
                matches_ambiguous_kept += 1
            else:
                matches_unique += 1

            # Extract remaining metadata
            year_elem = elem.find("year")
            year = None
            if year_elem is not None and year_elem.text:
                try:
                    year = int(year_elem.text)
                except ValueError:
                    pass

            # Client-side date filter
            if since_year and year and year < since_year:
                elem.clear()
                while elem.getprevious() is not None:
                    del elem.getparent()[0]
                continue

            venue = None
            for venue_tag in ("booktitle", "journal"):
                ve = elem.find(venue_tag)
                if ve is not None:
                    venue = _extract_text(ve)
                    break

            url = None
            ee_elems = elem.findall("ee")
            if ee_elems:
                url = _extract_text(ee_elems[0])

            dblp_key = elem.get("key", "")

            # Build author list
            authors = []
            for a_name in raw_authors:
                stripped = _strip_dblp_suffix(a_name)
                norm = _normalize_name(stripped)
                is_match = norm in name_index
                authors.append({
                    "name": stripped,
                    "affiliation": "Boston University" if is_match else "",
                    "is_bu": is_match,
                })

            # Track which faculty had hits
            for f in matched_faculty:
                faculty_hits[f["name"]] += 1

            if not dry_run:
                paper = make_paper_record(
                    title=title,
                    authors=authors,
                    year=year,
                    doi=doi,
                    abstract=None,
                    source="dblp",
                    source_id=dblp_key,
                    url=url,
                    venue=venue,
                    concepts=[],
                    publication_type=tag,
                    extra={
                        "dblp_key": dblp_key,
                        "dblp_type": tag,
                    },
                )
                papers.append(paper)

            elem.clear()
            while elem.getprevious() is not None:
                del elem.getparent()[0]

    logger.info(f"Done. Scanned {records_seen:,} records")
    logger.info(f"Total name matches: {matches_total}")
    logger.info(f"  Unique-name matches (kept): {matches_unique}")
    logger.info(f"  Ambiguous-name matches (skipped): {matches_ambiguous_skipped}")
    logger.info(f"  Already in dataset: {already_have}")
    logger.info(f"Faculty with hits: {len(faculty_hits)} / {faculty_count}")

    logger.info("Top 30 faculty by DBLP papers:")
    for name, count in faculty_hits.most_common(30):
        logger.info(f"  {count:4d}  {name}")

    zero_hit = faculty_count - len(faculty_hits)
    logger.info(f"Faculty with zero DBLP papers: {zero_hit}")

    if not dry_run and papers:
        save_checkpoint(papers, "dblp_dump")
        logger.info(f"Saved {len(papers)} papers to checkpoint")

    return papers


if __name__ == "__main__":
    setup_logging()

    parser = argparse.ArgumentParser(description="DBLP XML dump harvester")
    parser.add_argument("--dump", type=Path, default=DEFAULT_DUMP,
                        help="Path to dblp XML gz file")
    parser.add_argument("--limit", type=int, default=0,
                        help="Stop after N records (for testing)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Just count matches, don't save papers")
    args = parser.parse_args()

    papers = harvest_dump(dump_path=args.dump, limit=args.limit, dry_run=args.dry_run)
    print(f"\nHarvested {len(papers)} papers from DBLP dump")
