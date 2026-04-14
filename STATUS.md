# BU AI Bibliography -- Status
**Updated:** 2026-04-13

## Numbers
- **Papers:** 11,879 in `data/sonnet_classification_bu_verified.json`
- **Sources:** 13 (OpenAlex, PubMed, DBLP, SSRN, NBER, Scholarly Commons, OpenBU, NIH Reporter, NSF Awards, arXiv, CrossRef, Semantic Scholar, bioRxiv)
- **Roster:** 5,896 entries, 141 with school = unspecified, 4,473 with OpenAlex IDs
- **Validation:** 0 failures, 32 warnings (all genuine, non-actionable)
- **Web app:** live at marc-woernle.github.io/bu-ai-bibliography

## This session
- Rewrote README to reflect current state (13 sources, unified monthly pipeline, accurate limitations)
- Fixed author search display bug: authors matching search query but hidden beyond the 8-author truncation cut are now surfaced and highlighted
- Removed Claude from GitHub contributors by stripping Co-Authored-By trailers from 19 commits (history rewrite + force push)
- Updated repo description to "13 sources"

## Previous session (Apr 7)
- DBLP integration: +1,372 papers (10,498 -> 11,870)
- Improved paper links: 3,899 URLs upgraded to readable versions (arxiv, PubMed, repos)
- Added BibTeX citation format toggle
- Built unified monthly pipeline (6 phases, all 13 sources, auto roster refresh, DBLP dump download, fault isolation, GitHub Issue reports)

## TODO
1. **Trigger monthly workflow on GitHub to CI-test** -- Go to Actions -> Monthly Bibliography Update -> Run workflow. Must confirm it works before deleting weekly.
2. **Delete weekly pipeline** after monthly CI test passes -- remove update_weekly.py and .github/workflows/weekly-update.yml
3. Update STATUS.md TODO list to reflect post-pipeline-hardening priorities (141 unspecified roster entries, CFA roster cleanup)

## Known issues
- ~3,128 papers tagged "Boston University (unspecified)", mostly authors not in roster
- ~6,950 papers still link to doi.org (no OA version found in OpenAlex)
- 141 roster entries still "Boston University (unspecified)"
- OpenBU metadata bug: all authors get "Boston University" affiliation regardless
- Scholarly Commons uploads full back-catalog, no date filtering
- update_log.csv has only 1 row
- Weekly pipeline still exists (pending deletion after monthly CI test)
