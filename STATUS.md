# BU AI Bibliography — Status
**Updated:** 2026-04-04

## Numbers
- **Papers:** 10,456 in `data/sonnet_classification_bu_verified.json`
- **Roster:** 5,896 entries, 141 with school = unspecified
- **Unspecified-only papers:** 3,110
- **Validation:** 0 failures, 35 warnings (all genuine, non-actionable)
- **Web app:** live at marc-woernle.github.io/bu-ai-bibliography

## This session
- Audited full database: papers/faculty ratios, source coverage, faculty OAIDs, school-level gaps
- Harvested 669 NBER papers via OpenAlex, found 0 new AI-relevant (already captured via journal versions)
- Added 5 papers: Ireland CHCD (Theology), Muroff 2016+2025 (Social Work), 2 SSRN Econ/Questrom
- Fixed 6 false Dental tags via NAME_MATCH_BLOCKLIST in school_mapper.py
- Cleared 19 wrong OpenAlex IDs (name collisions with researchers at other institutions)
- Resolved 651/785 unspecified roster entries via OpenAlex raw_affiliation_strings
- Extended SCHOOL_PATTERNS with 30+ department-level regex patterns
- Added 3 COM faculty (Donovan, Gordon, Jing Yang)
- Implemented dual appointments via secondary_school field (Wildman, DK Lee, Van Alstyne, Su)
- Fixed stale FACULTY_LOOKUP import in update_pipeline.py
- Updated README with methodology, limitations, file structure
- Updated GitHub repo description, homepage URL, site footer, data note
- Removed "during time at BU" restriction from site data note

## TODO
1. Add DBLP + OpenReview as sources (conference paper gap, OpenAlex undercounts CS conferences)
2. Clean CFA roster (305 entries includes staff/artists, needs title field scraping)
3. Fix resolve_openalex_ids.py matching logic (last-name-only fallback causes wrong OAIDs)
4. Test auto-update pipeline end-to-end (`python update_weekly.py --dry-run`)

## Known issues
- ~3,110 papers tagged "Boston University (unspecified)", mostly authors not in roster
- OpenBU metadata bug: all authors get "Boston University" affiliation regardless
- Scholarly Commons uploads full back-catalog, no date filtering
- Some SPH roster entries are Harvard/BWH adjuncts (Buring, Chibnik, Huybrechts) with correct OAIDs but primarily publish under other affiliations
- resolve_openalex_ids.py uses last-name-only matching as fallback, causing wrong OAID assignments for common names
