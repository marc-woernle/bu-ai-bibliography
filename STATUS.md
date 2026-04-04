# BU AI Bibliography — Status (Updated April 3, 2026)

## Current State
- **Papers:** 10,396 verified, classified, live at marc-woernle.github.io/bu-ai-bibliography
- **Faculty roster:** 5,893 entries (cleaned from 6,238), 4,492 with OpenAlex author IDs
- **Repo:** github.com/marc-woernle/bu-ai-bibliography
- **Last commit:** `580ba44` — rebuild school tags: OAID-first classification, zero initial matching

## Just Completed (this session)

### Roster Cleanup (commit `28fa5b3`)
- Merged 345 same-person duplicate roster entries (same OpenAlex ID)
- Cleared 45 wrongly-shared OpenAlex IDs (different people merged by OpenAlex)
- Fixed schools: Robertson, Silbey, Citron, Li, Sellars → School of Law
- Auto-assigned 77 unspecified roster entries from paper affiliation evidence
- Roster: 6,238 → 5,893 entries

### School Tag Rebuild (commit `580ba44`)
- Backfilled OpenAlex author IDs onto 9,352 papers via 190 API calls. 21,833 BU authors now have OAIDs (was 0).
- Normalized 1,582 author names to roster canonical forms (e.g., "Christopher Robertson" → "Christopher T. Robertson" everywhere).
- Wiped ALL school tags and reclassified from zero using 4-tier OAID-first strategy:
  1. OpenAlex author ID → roster (zero false positives)
  2. Affiliation text → regex patterns
  3. Full-name → roster (BU authors only)
  4. Alt-names cache → roster (unambiguous only)
- **Deleted all initial-matching code** — `FACULTY_LOOKUP`, `FACULTY_BY_INITIAL` removed. No `(last_name, first_initial)` matching anywhere.
- Added `FACULTY_BY_ALTNAME` index from 98K OpenAlex author profiles (14,642 unambiguous entries).
- New scripts: `backfill_author_oaids.py`, `normalize_author_names.py`

### Results
| Metric | Before | After |
|--------|--------|-------|
| Papers with specific school | 6,916 | 6,540 |
| Unspecified-only | 3,092 | 3,856 |
| UNCLASSIFIED | 388 | 0 |
| False tags from initial matching | 342+ | 0 |
| Robertson → School of Law | 5/10 | 10/10 |
| Author name variants | 603 groups | ~0 |

### Post-Rebuild Audit Findings
- **Law (146 papers):** All correct — law, regulation, policy, privacy, IP topics
- **Dental (47 papers):** ~5 likely false tags from full-name matching common names (Bing Liu, Claire Chang, Li Liu, Andrew Miller, Rashi Sharma) to Dental roster entries. These are different people who share names with Dental faculty.
- **Top 5 schools (CS, Med, Eng, Psych, SPH):** 50 random samples all look correct
- **Same-name different-OAID conflicts:** 8 papers found, all from large physics collaborations, no school tag impact
- **Fixable:** Add OAID-mismatch guard to Tier 3 full-name matching — if author has an OAID that doesn't match the roster entry's OAID, skip the full-name match

## Immediate TODO
1. **Fix ~5 false Dental tags** — add OAID-mismatch guard to `classify_paper()` Tier 3, re-run on affected papers only
2. **Investigate NBER as new source** — Economics at 43 papers for 59 faculty. SSRN/NBER source gap.
3. **Classify 13 SSRN Econ/Questrom candidates** — saved in data/ but not yet through Sonnet
4. **Fix 792 unspecified roster entries** — the main lever for reducing the 3,856 "unspecified" papers. Options: scrape BU directory, query OpenAlex for department-level affiliation, manual lookup for high-impact faculty.

## Pending Tasks (priority order)
1. **Launch research agents for underrepresented schools:**
   - CAS Economics (43 papers, 59 faculty)
   - Pardee School of Global Studies (3 papers, 67 faculty)
   - School of Social Work (5 papers, 114 faculty)
2. **Test auto-update pipeline end-to-end** — `python update_weekly.py --dry-run`
3. **Update CLAUDE.md** to reflect new architecture (OAID-first, no initial matching)

## Known Issues
- ~3,856 papers tagged "Boston University (unspecified)" — authors not in roster or roster lacks school info
- ~5 false Dental tags from full-name matching common names (fixable with OAID-mismatch guard)
- 792 roster entries still have school = "Boston University (unspecified)" (from openalex_resolve, no school directory data)
- OpenAlex is NOT exhaustive — cross-reference with Scholarly Commons, SSRN, PubMed, CrossRef, faculty CVs
- Scholarly Commons uploads full back-catalog when faculty join BU — must filter by bu_start_year
- Tim Duncan (Law AI Program Director) has zero publications — he's a practitioner
- Economics has ~43 papers for 59 faculty — SSRN/NBER source gap

## Architecture Quick Reference
- **Core pipeline (root):** config.py, utils.py, source_*.py (6 active), classify_papers.py, school_mapper.py, generate_data_js.py, validate_dataset.py, update_pipeline.py, update_weekly.py, update_monthly.py, quarterly_review.py
- **New scripts:** backfill_author_oaids.py (OAID backfill), normalize_author_names.py (name normalization)
- **Web app:** docs/ (GitHub Pages public), output/bibliography_app/ (local with private version)
- **Data:** data/sonnet_classification_bu_verified.json (master, 10,396 papers), data/bu_faculty_roster_verified.json (roster, 5,893 entries), data/openalex_bu_authors_cache.json (98K author profiles with alt_names)
- **Key rule:** ONLY update data.js files, NEVER touch index.html programmatically
- **School classification:** 4-tier OAID-first in school_mapper.py. NO initial matching. Zero false positives from Tier 1 (OAID).

## Validation
Run `python validate_dataset.py` after any data change. 0 failures = safe to push. Current: 0 failures, 48 warnings (roster coverage for non-AI faculty).
