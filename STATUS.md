# BU AI Bibliography — Status (Updated April 3, 2026)

## Current State
- **Papers:** 10,390 verified, classified, live at marc-woernle.github.io/bu-ai-bibliography
- **Faculty roster:** 6,238 entries, 4,509 with OpenAlex author IDs
- **Repo:** github.com/marc-woernle/bu-ai-bibliography
- **Last commit:** fix false school tags from initial-match collisions, add SSRN papers

## Just Completed (last session, may need push)
- Fixed `classify_paper()` in `school_mapper.py` — now uses OpenAlex author IDs (FACULTY_BY_OAID) as Strategy 1 before name matching. Removed the `(last_name, first_initial)` fallback that caused 342+ false school tags (29 Law, 282 Dental, 31 Theology).
- Updated `source_openalex.py` `_parse_work()` to store `openalex_id` on each author record for future harvests.
- Updated `verify_bu_authors()` in `update_pipeline.py` — same ID-first, full-name-only logic. No more initial-match fallback.
- Tests passed: FACULTY_BY_OAID has 4,509 entries, OA ID matching works, full-name matching works, initial-only correctly returns None.
- Removed Neil Richards (WashU, not BU) from roster
- Removed 17 pre-2019 Citron papers + 4 physics false positives
- Removed 36 pre-BU Law papers using bu_start_year filter
- Fixed Rory Van Loo → School of Law
- Added "Dark Patterns as Disloyal Design" (Hartzog), "Against Engagement" (Hartzog), "Amazon's Pricing Paradox" (Van Loo)
- Added data limitations note to site footer
- Added bu_start_year to 17 Law faculty
- Fixed 342 false school tags from initial-match collisions
- Corrected Arthur Lee OpenAlex ID, cleared Jun Fan wrong ID, fixed 4 Lee duplicates
- Added 7 SSRN Econ/Questrom papers (Restrepo, Hagiu, Burtch, etc.)
- Codebase cleanup: atomic saves, dedup fixes, dead code removal (classify.py, harvest.py, install_schedules.py deleted)
- CI fix: GitHub Actions now uses requirements-ci.txt with sentence-transformers

## Immediate TODO (before anything else)
1. **Check git status** — there may be uncommitted changes from the school_mapper fix. If so, commit and push.
2. **Re-run classify_paper() on entire master dataset** — the function is fixed but the fix hasn't been re-applied to all 10,390 papers. Many papers still have stale school tags from the old initial-match logic.
3. **Regenerate data.js files and push.**
4. **Run validate_dataset.py** — confirm 0 failures.

## Pending Tasks (priority order)
1. **Classify 13 SSRN Econ/Questrom candidates** — saved in data/ but not yet through Sonnet. Most have no abstracts (CrossRef/SSRN limitation). Could triage by title or try enriching abstracts from SSRN directly.
2. **Investigate NBER as new source** — BU Economics faculty publish heavily on NBER. Our pipeline doesn't harvest NBER at all. Check if NBER has API or if CrossRef indexes NBER papers. This is probably why Economics is at only 41 papers.
3. **Launch research agents for underrepresented schools:**
   - CAS Economics (41 papers, 59 faculty, 0.7 papers/faculty)
   - Pardee School of Global Studies (25 papers, 67 faculty, 0.4 papers/faculty)
   - School of Social Work (61 papers, 114 faculty, 0.5 papers/faculty)
   For each: pick 3 senior faculty, Google their publications, compare against our dataset, report coverage gaps.
4. **Resolve 1,354 faculty without OpenAlex IDs** — mostly Medicine (597), Fine Arts (201), Dental (126), Questrom (123). Most are clinicians/lecturers who don't publish AI research. Low priority.
5. **Test auto-update pipeline end-to-end** — run `python update_weekly.py --dry-run` with full requirements to verify harvest→prefilter→classify→verify→merge→push works.

## Known Issues (documented in CLAUDE.md)
- ~4,500 papers tagged "Boston University (unspecified)" — authors not in roster or roster lacks school info
- OpenAlex is NOT exhaustive — cross-reference with Scholarly Commons, SSRN, PubMed, CrossRef, faculty CVs
- Scholarly Commons uploads full back-catalog when faculty join BU — must filter by bu_start_year
- Tim Duncan (Law AI Program Director) has zero publications — he's a practitioner
- Bernard Chao incorrectly listed as BU in OpenAlex — actually University of Denver
- Economics has ~41 papers for 59 faculty — SSRN/NBER source gap

## Architecture Quick Reference
- **Core pipeline (root):** config.py, utils.py, source_*.py (6 active), classify_papers.py, school_mapper.py, generate_data_js.py, validate_dataset.py, update_pipeline.py, update_weekly.py, update_monthly.py, quarterly_review.py
- **Web app:** docs/ (GitHub Pages public), output/bibliography_app/ (local with private version)
- **Data:** data/sonnet_classification_bu_verified.json (master), data/bu_faculty_roster_verified.json (roster), data/bu_authors_from_openalex.json (82K BU author names)
- **Key rule:** ONLY update data.js files, NEVER touch index.html programmatically

## Validation
Run `python validate_dataset.py` after any data change. 0 failures = safe to push. Current: 0 failures, ~50 warnings (mostly roster coverage for non-AI faculty).
