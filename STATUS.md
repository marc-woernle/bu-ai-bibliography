# BU AI Bibliography — Status (Updated April 3, 2026)

## Current State
- **Papers:** 10,451 verified, classified, live at marc-woernle.github.io/bu-ai-bibliography
- **Faculty roster:** 5,893 entries, 4,492 with OpenAlex author IDs
- **Repo:** github.com/marc-woernle/bu-ai-bibliography
- **Last commit:** `56a97f2` — restore 57 removed papers, fix false Law tags, harden classify_paper

## Session Summary (April 3, 2026)

This session rebuilt the entire school tagging and author name system from scratch.

### Roster Cleanup
- Merged 345 same-person duplicate roster entries (6,238 → 5,893)
- Cleared 45 wrongly-shared OpenAlex IDs (different people merged by OpenAlex)
- Fixed schools: Robertson, Silbey, Citron, Li, Sellars → School of Law
- Auto-assigned 77 unspecified entries from paper affiliation evidence
- Removed `bu_start_year` from all 16 roster entries — no date filtering

### School Tag Rebuild (OAID-first, zero initial matching)
- Backfilled OpenAlex author IDs onto 9,352 papers via 190 API calls. 21,833 BU authors now have OAIDs (was 0). New script: `backfill_author_oaids.py`.
- Normalized 1,582 author names to roster canonical forms. New script: `normalize_author_names.py`.
- Wiped ALL school tags and reclassified from zero using 4-tier strategy:
  1. OpenAlex author ID → roster (zero false positives)
  2. Affiliation text → regex (only for BU-affiliated authors)
  3. Full-name → roster (with OAID-mismatch guard)
  4. Alt-names cache → roster (unambiguous only)
- **Deleted all initial-matching code** — `FACULTY_LOOKUP`, `FACULTY_BY_INITIAL` removed permanently.
- Added `FACULTY_BY_ALTNAME` index (14,642 entries) from 98K OpenAlex author profiles.

### Paper Restoration & False Tag Fixes
- Restored 57 papers removed by `bu_start_year` filter (49 Law + 4 CERN + 4 other)
- Fixed 3 false Law tags: Guidoboni (U of Maine, not BU), 2 O'Brien pathology papers (different person)
- Fixed 19 `is_bu` flags on non-BU co-authors (Richards/WashU, Solove/GWU, Chao/Denver, Gray/UMD, etc.)
- Removed 2 papers with zero BU authors after fixes

### Code Hardening
- Tier 2 (affiliation): only matches if author is_bu or affiliation contains "boston university"/"bu"
- Tier 3 (full-name): OAID-mismatch guard — skips match if author's OAID differs from roster entry's OAID
- These prevent the two classes of false tags found during audit

### Final Numbers
| Metric | Start of Session | End of Session |
|--------|-----------------|----------------|
| Total papers | 10,396 | 10,451 |
| Roster entries | 6,238 | 5,893 |
| Papers with specific school | 6,916 | ~6,540 |
| Unspecified-only | 3,092 | ~3,900 |
| UNCLASSIFIED | 388 | 0 |
| False tags from initial matching | 342+ | 0 |
| Robertson → School of Law | 5/10 | 10/10 |
| School of Law papers | 135 | 196 |
| Author OAIDs on papers | 0 | 54,396 |
| Validation failures | 0 | 0 |

## Immediate TODO
1. **Fix ~5 remaining false Dental tags** — common names (Bing Liu, Claire Chang, Li Liu, Andrew Miller, Rashi Sharma) matching Dental roster entries. The OAID-mismatch guard catches cases where the author HAS an OAID, but these authors may lack OAIDs entirely. Could add a "known false match" blocklist or skip name matching for common names.
2. **Investigate NBER as new source** — Economics at 43 papers for 59 faculty. SSRN/NBER source gap.
3. **Classify 13 SSRN Econ/Questrom candidates** — saved in data/ but not yet through Sonnet.
4. **Fix 792 unspecified roster entries** — the main lever for reducing ~3,900 "unspecified" papers. Options: scrape BU directory, query OpenAlex for department-level affiliation.

## Pending Tasks
1. Launch research agents for underrepresented schools (Economics, Pardee, Social Work)
2. Test auto-update pipeline end-to-end — `python update_weekly.py --dry-run`
3. Update CLAUDE.md to reflect new architecture

## Known Issues
- ~3,900 papers tagged "Boston University (unspecified)" — authors not in roster or roster lacks school info
- ~5 false Dental tags from full-name matching common names (no OAID to catch mismatch)
- 792 roster entries still have school = "unspecified" (from openalex_resolve)
- OpenAlex is NOT exhaustive — cross-reference with Scholarly Commons, SSRN, PubMed, CrossRef
- Tim Duncan (Law AI Program Director) has zero publications — he's a practitioner
- Economics has ~43 papers for 59 faculty — SSRN/NBER source gap

## Architecture Quick Reference
- **Core pipeline:** config.py, utils.py, source_*.py (6 active), classify_papers.py, school_mapper.py, generate_data_js.py, validate_dataset.py, update_pipeline.py, update_weekly.py, update_monthly.py
- **New scripts:** backfill_author_oaids.py, normalize_author_names.py
- **Web app:** docs/ (GitHub Pages), output/bibliography_app/ (local)
- **Data:** data/sonnet_classification_bu_verified.json (10,451 papers), data/bu_faculty_roster_verified.json (5,893 entries), data/openalex_bu_authors_cache.json (98K author profiles)
- **School classification:** 4-tier OAID-first in school_mapper.py. NO initial matching. Guards: BU-only affiliation regex, OAID-mismatch on full-name match.
- **Key rule:** ONLY update data.js files, NEVER touch index.html

## Validation
Run `python validate_dataset.py` after any data change. Current: 0 failures, 47 warnings.
