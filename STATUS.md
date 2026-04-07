# BU AI Bibliography -- Status
**Updated:** 2026-04-07

## Numbers
- **Papers:** 10,498 in `data/sonnet_classification_bu_verified.json`
- **Roster:** 5,896 entries, 141 with school = unspecified
- **Validation:** 0 failures, 33 warnings (all genuine, non-actionable)
- **Web app:** live at marc-woernle.github.io/bu-ai-bibliography

## This session
- Fixed web app: clicking author name now clears all filters so all papers show regardless of school
- Fixed PubMed weekly harvest: was only querying 10 of 56 AI keywords ([:10] slice bug in update_pipeline.py:261)
- Removed silent 2000 PMID hard cap in weekly PubMed, added >5000 anomaly warning
- Cleared wrong OAID for Taymaz Davoodi (was Telli Davoodi at SAIC, set to null)
- Fixed stale master_paper_count in update_state.json (10,349 -> 10,466)
- Ran weekly dry-run successfully: 1,903 harvested -> 30 candidates, $0.16 est. cost
- PubMed backfill: full harvest (3,634 raw) -> 58 after filters -> 32 AI-relevant added. Cost: $0.32
- Built source_dblp.py (API-based, untested, DBLP was 503 all session)
- Built backfill_pubmed.py (one-time, can be deleted)

## TODO
1. DBLP harvest: either test API module when DBLP is back, or use DBLP XML dump (~1GB gz from drops.dagstuhl.de). Dump approach preferred for bulk backfill. Estimated yield: 500-1000 new conference papers.
2. Pipeline hardening (Session 4): make monthly optional sources required, add CrossRef + DBLP to monthly, add quarterly_review.yml workflow, expand source_health tracking
3. After DBLP + pipeline hardening, audit auto functions end-to-end to ensure all sources are covered
4. Clean CFA roster (305 entries includes staff/artists, needs title field scraping)

## Known issues
- ~3,110 papers tagged "Boston University (unspecified)", mostly authors not in roster
- OpenBU metadata bug: all authors get "Boston University" affiliation regardless
- Scholarly Commons uploads full back-catalog, no date filtering
- Some SPH roster entries are Harvard/BWH adjuncts with correct OAIDs but primarily publish under other affiliations
- Weekly pipeline covers 4 of 11 sources; monthly covers 10 but optional ones fail silently
- update_log.csv has only 1 row
- source_dblp.py exists but is untested (DBLP API was down 2026-04-07)
- backfill_pubmed.py is a one-time script, can be deleted
