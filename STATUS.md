# BU AI Bibliography — Status
**Updated:** 2026-04-04

## Numbers
- **Papers:** 10,456 in `data/sonnet_classification_bu_verified.json`
- **Roster:** 5,896 entries, 141 with school = unspecified (was 792)
- **Unspecified-only papers:** 3,110 (was 3,861)
- **Validation:** 0 failures, 35 warnings
- **Web app:** live at marc-woernle.github.io/bu-ai-bibliography

## This session
- Audited database against ground truths: papers/faculty ratios, source coverage, faculty OAIDs
- Harvested 669 NBER papers via OpenAlex → 0 new AI-relevant (already captured via journal versions)
- Added 5 papers: Ireland CHCD (Theology), Muroff 2016+2025 (Social Work), 2 SSRN Econ/Questrom
- Fixed 6 false Dental tags via NAME_MATCH_BLOCKLIST in school_mapper.py
- Cleared 19 wrong OpenAlex IDs on roster entries (name collisions with researchers at other institutions)
- Resolved 651 of 785 unspecified roster entries via OpenAlex raw_affiliation_strings
- Extended SCHOOL_PATTERNS with 30+ department-level patterns (economics, psychology, physics, medical departments, etc.)
- Added 3 COM faculty (Donovan, Gordon, Jing Yang)
- Implemented dual appointments via secondary_school field (Wildman→STH, DK Lee→Questrom, Van Alstyne→Questrom, Su→COM)
- Fixed stale FACULTY_LOOKUP import in update_pipeline.py
- Updated README, CLAUDE.md, STATUS.md, GitHub repo description

## TODO
1. Investigate remaining 35 validation warnings — all genuine (non-AI faculty with many publications, or school coverage thresholds)
2. Add DBLP + OpenReview as sources (fills conference paper gap — OpenAlex undercounts CS conferences)
3. Handle remaining 141 unspecified roster entries (ambiguous affiliations or left BU)
4. Clean CFA roster of non-faculty (305 entries includes staff/artists — needs title field scraping)
5. Test auto-update pipeline end-to-end (`python update_weekly.py --dry-run`)

## Known issues
- ~3,110 papers tagged "Boston University (unspecified)" — remaining authors not in roster or roster entry still unspecified
- OpenBU metadata bug: all authors get "Boston University" affiliation regardless
- Scholarly Commons uploads full back-catalog — no date filtering applied
- resolve_openalex_ids.py uses last-name-only matching — causes wrong OAID assignments for common names
- Some SPH roster entries are Harvard/BWH adjuncts (Buring, Chibnik, Huybrechts, etc.) — correct OAIDs but primarily publish under other affiliations
