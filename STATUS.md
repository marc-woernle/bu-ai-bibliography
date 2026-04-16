# BU AI Bibliography -- Status
**Updated:** 2026-04-16

## Numbers
- **Papers:** 11,879 in `data/sonnet_classification_bu_verified.json`
- **Sources:** 13 (OpenAlex, PubMed, DBLP, SSRN, NBER, Scholarly Commons, OpenBU, NIH Reporter, NSF Awards, arXiv, CrossRef, Semantic Scholar, bioRxiv)
- **Roster:** 5,896 entries, 141 with school = unspecified, 4,473 with OpenAlex IDs
- **Validation:** 0 failures, 32 warnings (all genuine, non-actionable)
- **Web app:** live at marc-woernle.github.io/bu-ai-bibliography
- **Classification model:** Sonnet 4.6 (claude-sonnet-4-6-20250514)

## In progress
- **Monthly CI run triggered Apr 16** (run 24528812356). API credits topped up. Expecting ~3,825 papers to classify (~$20 one-time backlog). This builds the rejection index so future runs cost ~$1. Check results at GitHub Actions or look for a GitHub Issue (posted automatically on completion).

## Previous session (Apr 14-16)
- Pipeline resilience overhaul: resilient_get with exponential backoff, per-source time budgets (10-20 min), partial result capture, 18-month date filtering, quarterly full sweep logic
- S2 harvester: trimmed 13 keyword queries to 6, per-query limit 200 (was 500). Went from timing out to 28s on CI.
- Two CI runs (Apr 15): harvest passed perfectly (13/13 sources, 0 failures). Classification blocked by Anthropic API credits/outage. Fixed classify_via_sonnet to abort on billing/auth errors instead of looping forever.
- Added rejected papers index (`data/rejected_papers_index.json`): dedup against previously rejected papers to avoid re-classifying known junk every month.
- Upgraded classification model to Sonnet 4.6
- Added GitHub profile link button to site header and footer
- Updated repo description

## TODO
1. **Check CI run results** (should complete within ~2 hours of trigger)
2. **Delete weekly pipeline** after monthly CI test passes (remove update_weekly.py and .github/workflows/weekly-update.yml)
3. **Site search improvements**: quoted exact match, smarter search UX (planned, not started)
4. 141 unspecified roster entries, CFA roster cleanup
5. Claude still showing as GitHub contributor (cache not cleared after 2+ days, likely needs repo nuke)

## Known issues
- ~3,128 papers tagged "Boston University (unspecified)", mostly authors not in roster
- ~6,950 papers still link to doi.org (no OA version found in OpenAlex)
- 141 roster entries still "Boston University (unspecified)"
- OpenBU metadata bug: all authors get "Boston University" affiliation regardless
- Scholarly Commons uploads full back-catalog, no date filtering
- Weekly pipeline still exists (pending deletion after monthly CI test)
- Claude still showing as GitHub contributor (cache issue, may need repo nuke)
