# BU AI Bibliography -- Status
**Updated:** 2026-04-07

## Numbers
- **Papers:** 10,466 in `data/sonnet_classification_bu_verified.json`
- **Roster:** 5,896 entries, 141 with school = unspecified
- **Unspecified-only papers:** 3,110
- **Validation:** 0 failures, 34 warnings (all genuine, non-actionable)
- **Web app:** live at marc-woernle.github.io/bu-ai-bibliography

## This session
- Fixed web app: clicking author name now clears all filters so all papers show regardless of school
- Fixed PubMed weekly harvest: was only querying 10 of 56 AI keywords ([:10] slice bug in update_pipeline.py:261)
- Removed silent 2000 PMID hard cap in weekly PubMed, added >5000 anomaly warning
- Cleared wrong OAID for Taymaz Davoodi (was Telli Davoodi at SAIC, set to null)
- Fixed stale master_paper_count in update_state.json (10,349 -> 10,466)
- Ran weekly dry-run successfully: 1,903 harvested -> 30 candidates after filtering, $0.16 est. cost
- Created 5-session plan for audit gaps (PubMed backfill, DBLP, pipeline hardening)

## TODO
1. One-time PubMed backfill via source_pubmed.py full harvest (~400+ new papers, ~$1.20)
2. Build source_dblp.py + one-time harvest (~500-1000 new papers, ~$1.50-3)
3. Pipeline hardening: make monthly optional sources required, add CrossRef + DBLP to monthly, add quarterly_review.yml workflow, expand source_health tracking
4. Clean CFA roster (305 entries includes staff/artists, needs title field scraping)
5. Test monthly pipeline dry-run end-to-end

## Known issues
- ~3,110 papers tagged "Boston University (unspecified)", mostly authors not in roster
- OpenBU metadata bug: all authors get "Boston University" affiliation regardless
- Scholarly Commons uploads full back-catalog, no date filtering
- Some SPH roster entries are Harvard/BWH adjuncts (Buring, Chibnik, Huybrechts) with correct OAIDs but primarily publish under other affiliations
- Weekly pipeline covers 4 of 11 sources; monthly covers 10 but optional ones fail silently
- update_log.csv has only 1 row (needs to accumulate going forward)
