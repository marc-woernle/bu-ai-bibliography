#!/usr/bin/env python3
"""
Build a comprehensive BU faculty roster by scraping department web pages
and resolving OpenAlex author IDs.

This replaces the hand-curated FACULTY_LOOKUP with authoritative data.
Run periodically (monthly) to catch new hires.

Usage:
    python build_faculty_roster.py              # Full scrape + OpenAlex resolve
    python build_faculty_roster.py --skip-openalex  # Scrape only (faster, for testing)
"""

import argparse
import json
import logging
import os
import re
import time
from datetime import date

import requests
from bs4 import BeautifulSoup

from config import BU_ROR_ID, CONTACT_EMAIL

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("build_roster")

OUTPUT_PATH = "data/bu_faculty_roster_verified.json"
EXISTING_ROSTER_PATH = "data/bu_faculty_roster.json"
OA_HEADERS = {"User-Agent": f"BU-AI-Bibliography/1.0 (mailto:{CONTACT_EMAIL})"}

# Map department URLs to school names (matching school_mapper.py naming)
DEPARTMENT_URLS = {
    # ── Arts & Sciences ──
    "CAS — Computer Science": ["https://www.bu.edu/cs/people/faculty/"],
    "CAS — Mathematics & Statistics": ["https://www.bu.edu/math/people/faculty/"],
    "CAS — Economics": ["https://www.bu.edu/econ/people/faculty/"],
    "CAS — Political Science": ["https://www.bu.edu/polisci/people/faculty/"],
    "CAS — Philosophy": ["https://www.bu.edu/philo/people/faculty/"],  # /philo/ not /philosophy/
    "CAS — Psychology & Brain Sciences": ["https://www.bu.edu/psych/people/faculty/"],
    "CAS — Biology": ["https://www.bu.edu/biology/people/faculty/"],
    "CAS — Linguistics": ["https://www.bu.edu/linguistics/people/faculty/"],
    "CAS — Physics": ["https://www.bu.edu/physics/people/faculty/"],
    "CAS — Earth & Environment": ["https://www.bu.edu/earth/people/faculty/"],
    # ── Professional Schools ──
    "School of Law": [
        "https://www.bu.edu/law/faculty-research/faculty-profiles/",
        "https://www.bu.edu/law/faculty-research/faculty-profiles/?profile_type=full-time-faculty",
        "https://www.bu.edu/law/faculty-research/faculty-profiles/?profile_type=professors",
        "https://www.bu.edu/law/faculty-research/faculty-profiles/?profile_type=emeritus",
        "https://www.bu.edu/law/faculty-research/faculty-profiles/?profile_type=visiting",
    ],
    "College of Engineering": [
        "https://www.bu.edu/eng/departments/ece/people/",
        "https://www.bu.edu/eng/departments/me/people/",
        "https://www.bu.edu/eng/departments/bme/people/",
        "https://www.bu.edu/eng/departments/se/people/",
        "https://www.bu.edu/eng/departments/mse/people/",
    ],
    "School of Medicine": [
        # Paginated with ?num=N, 132 pages, names in h4 tags
        "https://www.bumc.bu.edu/camed/about/directory/",
    ],
    "School of Public Health": [
        "https://www.bu.edu/sph/about/directory/",
        "https://www.bu.edu/sph/departments/biostatistics/faculty-staff/",
        "https://www.bu.edu/sph/departments/epidemiology/faculty-staff/",
        "https://www.bu.edu/sph/departments/environmental-health/faculty-staff/",
        "https://www.bu.edu/sph/departments/health-law-policy-and-management/faculty-staff/",
        "https://www.bu.edu/sph/departments/community-health-sciences/faculty-staff/",
        "https://www.bu.edu/sph/departments/global-health/faculty-staff/",
    ],
    "Faculty of Computing & Data Sciences": [
        "https://www.bu.edu/cds-faculty/culture-community/faculty/",
    ],
    "Questrom School of Business": [
        # JS-rendered — use WP REST API (handled specially in scraper)
        "QUESTROM_WP_API",
    ],
    "Wheelock College of Education & Human Development": [
        "https://www.bu.edu/wheelock/affiliation/faculty/",  # paginated, 11 pages
    ],
    "College of Communication": [
        "https://www.bu.edu/com/profiles/faculty/",
    ],
    "School of Theology": [
        "https://www.bu.edu/sth/academics/faculty/",
    ],
    "College of Fine Arts": [
        "https://www.bu.edu/cfa/about/contact-directions/directory/",  # paginated with ?num=N
    ],
    "Sargent College of Health & Rehabilitation Sciences": [
        "https://www.bu.edu/sargent/directory/",
    ],
    "School of Dental Medicine": [
        "https://www.bu.edu/dental/profiles/affiliation/faculty/",  # paginated, 34 pages
    ],
    "Pardee School of Global Studies": [
        "https://www.bu.edu/pardeeschool/academics/faculty/",
    ],
    # ── Research Centers ──
    "Hariri Institute for Computing": [
        "https://www.bu.edu/hic/people/",
    ],
}

# Pages that need pagination: (max_pages, url_pattern)
# Patterns: "page" = /page/N/, "num" = ?num=N, "paged" = ?paged=N
PAGINATED_URLS = {
    "https://www.bumc.bu.edu/camed/about/directory/": (132, "num"),
    "https://www.bu.edu/wheelock/affiliation/faculty/": (11, "page"),
    "https://www.bu.edu/dental/profiles/affiliation/faculty/": (34, "page"),
    "https://www.bu.edu/cfa/about/contact-directions/directory/": (20, "num"),
}


def fetch_page(url: str) -> BeautifulSoup | None:
    """Fetch and parse a web page."""
    try:
        resp = requests.get(url, timeout=20, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
        }, allow_redirects=True)
        if resp.status_code == 404:
            logger.warning(f"  404: {url}")
            return None
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        logger.error(f"  Fetch error for {url}: {e}")
        return None


def clean_name(name: str) -> str:
    """Clean up a scraped faculty name."""
    # Remove titles, degrees, etc.
    name = re.sub(r'\b(Ph\.?D\.?|M\.?D\.?|J\.?D\.?|M\.?S\.?|M\.?A\.?|Dr\.?|Prof\.?|Professor)\b', '', name, flags=re.I)
    # Remove parenthetical notes
    name = re.sub(r'\(.*?\)', '', name)
    # Remove extra whitespace
    name = re.sub(r'\s+', ' ', name).strip()
    # Remove trailing/leading punctuation
    name = name.strip('., ')
    return name


def _is_person_name(text: str) -> bool:
    """Check if a string looks like a person name."""
    words = text.split()
    if len(words) < 2 or len(words) > 6:
        return False
    if any(c.isdigit() for c in text):
        return False
    if len(text) > 60:
        return False
    # Filter out common non-name headings
    skip = ['meet our', 'faculty &', 'result', 'department', 'division', 'refine',
            'also see', 'recent news', 'upcoming', 'award', 'previous', 'academic',
            'about', 'research', 'contact', 'overview', 'program', 'major', 'minor']
    if any(s in text.lower() for s in skip):
        return False
    return True


def extract_faculty_generic(soup: BeautifulSoup, url: str) -> list[dict]:
    """Try multiple strategies to extract faculty names from a BU page."""
    faculty = []
    seen = set()

    def add(name, title=""):
        name = clean_name(name)
        if name and _is_person_name(name) and name.lower() not in seen:
            seen.add(name.lower())
            faculty.append({"name": name, "title": title})

    # Strategy 0: SPH profile cards (unique structure)
    cards = soup.select('.sph-profile-basic-card')
    if cards:
        for card in cards:
            content = card.select_one('.sph-profile-basic-content')
            if content:
                text = content.get_text(strip=True)
                # Format: "Name,DegreeTitle,Department..."
                parts = text.split(',')
                if parts:
                    name = parts[0].strip()
                    title = parts[1].strip() if len(parts) > 1 else ""
                    add(name, title)
        if faculty:
            return faculty

    # Strategy 1: BU profile-item cards (CDS, SPH, many schools)
    items = soup.select('div.profile-item, li.profile-item')
    if items:
        for item in items:
            # Name is in the profile-name or first h3/link
            name_el = item.select_one('.profile-name, h3 a, h4 a, h3, h4')
            title_el = item.select_one('.profile-title, .profile-department')
            if name_el:
                add(name_el.get_text(strip=True), title_el.get_text(strip=True) if title_el else "")
        if faculty:
            return faculty

    # Strategy 2: BU filtering result items (Law, some others)
    items = soup.select('li.bu-filtering-result-item')
    if items:
        for item in items:
            link = item.select_one('a')
            if link:
                add(link.get_text(strip=True))
        if faculty:
            return faculty

    # Strategy 3: Faculty card pattern (CS, Bio, etc.)
    for selector in ['div.faculty-card', 'div.person-card', 'div.profile-card',
                     'article.profile', 'div.faculty-member']:
        cards = soup.select(selector)
        if cards:
            for card in cards:
                name_el = card.select_one('span.name, h3.name, h2.name, a.name, '
                                          '.profile-name, h3 a, h2 a')
                if not name_el:
                    name_el = card.select_one('h3, h2, h4')
                if name_el:
                    title_el = card.select_one('span.title, .profile-title, p.title, .position')
                    add(name_el.get_text(strip=True), title_el.get_text(strip=True) if title_el else "")
            if faculty:
                return faculty

    # Strategy 4: Profile listing (Physics, some Engineering)
    items = soup.select('div.profile-listing')
    if items:
        for item in items:
            for link in item.select('a[href*="profile"]'):
                add(link.get_text(strip=True))
        if faculty:
            return faculty

    # Strategy 5: h3/h4/h5 with names (Engineering people pages, generic)
    # Look for headings that are person names (have comma+degree or just 2-3 word names)
    for h in soup.select('h3, h4, h5'):
        text = h.get_text(strip=True)
        # Engineering pattern: "Name, PhD" or "Name"
        if ',' in text:
            name_part = text.split(',')[0].strip()
            add(name_part)
        else:
            add(text)
    if faculty:
        return faculty

    # Strategy 6: Any heading with a link to a profile page
    for h in soup.select('h2 a, h3 a, h4 a'):
        href = h.get('href', '')
        if 'profile' in href or 'people' in href or 'faculty' in href:
            add(h.get_text(strip=True))

    return faculty


def resolve_openalex_id(name: str) -> dict | None:
    """Look up a faculty member's OpenAlex author ID."""
    time.sleep(0.15)  # Rate limit
    try:
        resp = requests.get(
            "https://api.openalex.org/authors",
            params={
                "search": name,
                "filter": f"affiliations.institution.ror:{BU_ROR_ID}",
                "per_page": 3,
            },
            headers=OA_HEADERS,
            timeout=15,
        )
        if resp.status_code == 429:
            time.sleep(5)
            return resolve_openalex_id(name)  # retry once
        if resp.status_code != 200:
            return None

        results = resp.json().get("results", [])
        if not results:
            return None

        # Take best match — check name similarity
        best = results[0]
        oa_name = best.get("display_name", "").lower()
        query_name = name.lower()

        # Basic similarity: last names must match
        query_last = query_name.split()[-1] if query_name.split() else ""
        oa_last = oa_name.split()[-1] if oa_name.split() else ""

        if query_last != oa_last:
            # Try second result
            if len(results) > 1:
                best = results[1]
                oa_name = best.get("display_name", "").lower()
                oa_last = oa_name.split()[-1] if oa_name.split() else ""
                if query_last != oa_last:
                    return None
            else:
                return None

        return {
            "openalex_id": best["id"],
            "display_name": best.get("display_name", ""),
            "works_count": best.get("works_count", 0),
            "cited_by_count": best.get("cited_by_count", 0),
        }

    except Exception as e:
        logger.debug(f"  OpenAlex lookup error for {name}: {e}")
        return None


def check_name_rarity(name: str, all_bu_authors: set) -> bool:
    """Check if a name is rare (appears ≤3 times in the BU author roster)."""
    parts = name.lower().split()
    if len(parts) < 2:
        return True
    last = parts[-1]
    # Count how many BU authors share this last name
    count = sum(1 for a in all_bu_authors if a.split()[-1] == last)
    return count <= 3


def scrape_all_departments() -> tuple[list[dict], dict]:
    """Scrape all BU department pages for faculty.
    Returns (faculty_list, school_counts) where each entry has name, title, school, source_url.
    """
    all_faculty = []
    school_counts = {}

    for school, urls in DEPARTMENT_URLS.items():
        logger.info(f"Scraping: {school}")
        school_faculty = []

        for url in urls:
            # Special handler: Questrom WP REST API
            if url == "QUESTROM_WP_API":
                logger.info(f"  Using Questrom WP REST API")
                import html as html_mod
                qpage = 1
                while True:
                    try:
                        qresp = requests.get(
                            "https://www.bu.edu/questrom/wp-json/wp/v2/profiles",
                            params={"per_page": 100, "page": qpage, "directory-types": 35},
                            timeout=15, headers={"User-Agent": "Mozilla/5.0"}
                        )
                        if qresp.status_code != 200 or not qresp.json():
                            break
                        for p in qresp.json():
                            title = p.get("title", {})
                            name = html_mod.unescape(title.get("rendered", "") if isinstance(title, dict) else str(title)).strip()
                            name = clean_name(name)
                            if name and _is_person_name(name):
                                school_faculty.append({"name": name, "title": "", "school": school, "source_url": "questrom-wp-api"})
                        qpage += 1
                    except Exception as e:
                        logger.error(f"  Questrom API error: {e}")
                        break
                logger.info(f"  Found: {len(school_faculty)} faculty via WP API")
                continue

            logger.info(f"  URL: {url}")

            # Check if this URL needs pagination
            pag_info = PAGINATED_URLS.get(url)
            max_pages = pag_info[0] if pag_info else 1
            pag_style = pag_info[1] if pag_info else "page"
            for page_num in range(1, max_pages + 1):
                if page_num == 1:
                    page_url = url
                elif pag_style == "num":
                    if not url.endswith('/'):
                        url_base = url + '/'
                    else:
                        url_base = url
                    page_url = url_base + f"?num={page_num}"
                elif pag_style == "paged":
                    page_url = url.rstrip('/') + f"?paged={page_num}"
                else:
                    page_url = url.rstrip('/') + f"/page/{page_num}/"

                soup = fetch_page(page_url)
                if not soup:
                    break

                found = extract_faculty_generic(soup, page_url)
                if not found and page_num > 3:
                    break

                for f in found:
                    f["school"] = school
                    f["source_url"] = url
                    school_faculty.append(f)

                if page_num == 1:
                    logger.info(f"  Found: {len(found)} names" + (f" (paginated, up to {max_pages} pages)" if max_pages > 1 else ""))
                elif page_num % 10 == 0:
                    logger.info(f"    Page {page_num}/{max_pages}...")

                if max_pages > 1:
                    time.sleep(0.3)

        # Dedup within school
        seen = set()
        deduped = []
        for f in school_faculty:
            key = f["name"].lower()
            if key not in seen:
                seen.add(key)
                deduped.append(f)

        school_counts[school] = len(deduped)
        all_faculty.extend(deduped)
        logger.info(f"  Total for {school}: {len(deduped)} unique faculty")

    # Global dedup
    seen = set()
    unique = []
    for f in all_faculty:
        key = f["name"].lower()
        if key not in seen:
            seen.add(key)
            unique.append(f)
    logger.info(f"Total unique faculty scraped: {len(unique)}")

    return unique, school_counts


def merge_with_existing(scraped: list[dict], existing_roster: list[dict], school_counts: dict) -> list[dict]:
    """Safely merge scraped faculty with existing roster.
    Regression protection: if a school's scrape returns <50% of previous count,
    keep old entries for that school.
    Returns merged roster (without OAIDs, those are resolved separately).
    """
    # Count existing per school
    existing_school_counts = {}
    for e in existing_roster:
        s = e.get("school", "")
        existing_school_counts[s] = existing_school_counts.get(s, 0) + 1

    warnings = []
    # Check for regressions
    protected_schools = set()
    for school, old_count in existing_school_counts.items():
        new_count = school_counts.get(school, 0)
        if old_count > 10 and new_count < old_count * 0.5:
            warnings.append(f"{school}: scraped {new_count} vs existing {old_count}, keeping old entries")
            logger.warning(f"Regression protection: {school} scraped {new_count} vs existing {old_count}")
            protected_schools.add(school)

    # Build merged list: scraped faculty + old entries for protected schools
    merged = list(scraped)
    scraped_names = {f["name"].lower() for f in scraped}

    for entry in existing_roster:
        school = entry.get("school", "")
        name_lower = entry["name"].lower()
        # Add from protected schools if not already scraped
        if school in protected_schools and name_lower not in scraped_names:
            merged.append(entry)
            scraped_names.add(name_lower)
        # Also keep entries from schools we didn't scrape at all
        if school not in school_counts and name_lower not in scraped_names:
            merged.append(entry)
            scraped_names.add(name_lower)

    logger.info(f"Merged roster: {len(merged)} entries ({len(warnings)} regression warnings)")
    return merged, warnings


def main():
    parser = argparse.ArgumentParser(description="Build BU faculty roster")
    parser.add_argument("--skip-openalex", action="store_true", help="Skip OpenAlex ID resolution")
    args = parser.parse_args()

    # Load existing roster for cross-reference
    existing = {}
    if os.path.exists(EXISTING_ROSTER_PATH):
        with open(EXISTING_ROSTER_PATH) as f:
            for entry in json.load(f):
                existing[entry["name"].lower()] = entry.get("department", "")

    # Load BU author names for rarity check
    bu_author_names = set()
    if os.path.exists("data/bu_authors_from_openalex.json"):
        with open("data/bu_authors_from_openalex.json") as f:
            for a in json.load(f):
                if a.get("name"):
                    bu_author_names.add(a["name"].lower())

    # Scrape
    scraped, school_counts = scrape_all_departments()

    # Load existing verified roster for merge
    existing_roster = []
    if os.path.exists(OUTPUT_PATH):
        with open(OUTPUT_PATH) as f:
            existing_roster = json.load(f)

    unique, merge_warnings = merge_with_existing(scraped, existing_roster, school_counts)

    # Cross-reference with existing roster
    in_existing = sum(1 for f in unique if f["name"].lower() in existing)
    logger.info(f"Found in existing roster: {in_existing}/{len(unique)}")

    # Resolve OpenAlex IDs
    if not args.skip_openalex:
        logger.info(f"\nResolving OpenAlex IDs for {len(unique)} faculty...")
        resolved = 0
        for i, f in enumerate(unique):
            if f.get("openalex_id"):
                resolved += 1
                continue
            result = resolve_openalex_id(f["name"])
            if result:
                f["openalex_id"] = result["openalex_id"]
                f["openalex_display_name"] = result["display_name"]
                f["works_count"] = result["works_count"]
                f["cited_by_count"] = result["cited_by_count"]
                resolved += 1
            else:
                f["openalex_id"] = None
                f["works_count"] = f.get("works_count", 0)

            f["is_rare_name"] = check_name_rarity(f["name"], bu_author_names)

            if (i + 1) % 50 == 0:
                logger.info(f"  {i+1}/{len(unique)} resolved ({resolved} found)")

        logger.info(f"OpenAlex IDs resolved: {resolved}/{len(unique)}")
    else:
        for f in unique:
            if not f.get("openalex_id"):
                f["openalex_id"] = None
            f["is_rare_name"] = check_name_rarity(f["name"], bu_author_names)

    # Add metadata
    for f in unique:
        f["scraped_at"] = date.today().isoformat()

    # Save
    with open(OUTPUT_PATH, "w") as f:
        json.dump(unique, f, indent=2, ensure_ascii=False)
    logger.info(f"\nSaved to {OUTPUT_PATH}")

    # Summary
    print(f"\n{'='*60}")
    print(f"FACULTY ROSTER SUMMARY")
    print(f"{'='*60}")
    print(f"Total unique faculty: {len(unique)}")
    print(f"With OpenAlex ID: {sum(1 for f in unique if f.get('openalex_id'))}")
    print(f"With rare name: {sum(1 for f in unique if f.get('is_rare_name'))}")
    print(f"\nBy school:")
    for school, count in sorted(school_counts.items(), key=lambda x: -x[1]):
        oa_count = sum(1 for f in unique if f["school"] == school and f.get("openalex_id"))
        print(f"  {school}: {count} faculty ({oa_count} with OpenAlex ID)")


if __name__ == "__main__":
    main()
