# BU AI Bibliography -- Status
**Updated:** 2026-04-15

## Numbers
- **Papers:** 11,879 in `data/sonnet_classification_bu_verified.json`
- **Sources:** 13 (OpenAlex, PubMed, DBLP, SSRN, NBER, Scholarly Commons, OpenBU, NIH Reporter, NSF Awards, arXiv, CrossRef, Semantic Scholar, bioRxiv)
- **Roster:** 5,896 entries, 141 with school = unspecified, 4,473 with OpenAlex IDs
- **Validation:** 0 failures, 32 warnings (all genuine, non-actionable)
- **Web app:** live at marc-woernle.github.io/bu-ai-bibliography
- **Classification model:** Sonnet 4.6 (claude-sonnet-4-6-20250514)

## This session
- Pipeline resilience overhaul: resilient_get with exponential backoff, per-source time budgets (10-20 min), partial result capture, 18-month date filtering, quarterly full sweep logic
- S2 harvester: trimmed 13 keyword queries to 6, per-query limit 200 (was 500). Went from timing out to 28s on CI.
- CI run Apr 15: harvest phase passed (22,137 papers, 13/13 sources, 0 failures). Classification blocked by Anthropic API error (likely outage, not empty credits). Fixed classify_via_sonnet to abort on billing/auth errors instead of looping.
- Added rejected papers index (`data/rejected_papers_index.json`): stores DOI/fingerprint of papers Sonnet classified as not_relevant, so they are never re-classified. Prevents wasting ~$20/month re-classifying known junk.
- Upgraded classification model to Sonnet 4.6
- Added GitHub profile link button to site header and footer
- Updated repo description to "11,879 papers, monthly, 13 sources"

## TODO
1. **Re-trigger monthly workflow** when Anthropic API is stable. First run will classify ~3,800 backlog candidates (~$20 one-time), then build the rejection index. Future runs: dozens of papers, ~$1.
2. **Delete weekly pipeline** after monthly CI test passes (remove update_weekly.py and .github/workflows/weekly-update.yml)
3. **Site search improvements**: quoted exact match, smarter search UX (planned, not started)
4. 141 unspecified roster entries, CFA roster cleanup

## Known issues
- ~3,128 papers tagged "Boston University (unspecified)", mostly authors not in roster
- ~6,950 papers still link to doi.org (no OA version found in OpenAlex)
- 141 roster entries still "Boston University (unspecified)"
- OpenBU metadata bug: all authors get "Boston University" affiliation regardless
- Scholarly Commons uploads full back-catalog, no date filtering
- Weekly pipeline still exists (pending deletion after monthly CI test)
- Claude still showing as GitHub contributor (cache issue, may need repo nuke)
