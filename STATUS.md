# BU AI Bibliography -- Status
**Updated:** 2026-04-16

## Numbers
- **Papers:** 11,879 in `data/sonnet_classification_bu_verified.json`
- **Sources:** 13 (OpenAlex, PubMed, DBLP, SSRN, NBER, Scholarly Commons, OpenBU, NIH Reporter, NSF Awards, arXiv, CrossRef, Semantic Scholar, bioRxiv)
- **Roster:** 5,896 entries, 141 with school = unspecified, 4,473 with OpenAlex IDs
- **Validation:** 0 failures, 32 warnings (all genuine, non-actionable)
- **Web app:** live at marc-woernle.github.io/bu-ai-bibliography
- **Classification model:** Sonnet 4.6 (claude-sonnet-4-6)

## In progress
- **Monthly CI run re-triggered Apr 16** (run 24534333871). Previous run 24528812356 burned its entire 2h budget looping on 404s because the MODEL constant was "claude-sonnet-4-6-20250514" (snapshot ID that does not exist); now fixed to "claude-sonnet-4-6". No API credits charged by the 404s. Expecting ~3,825 papers to classify (~$20 one-time backlog). Check results at GitHub Actions or look for a GitHub Issue (posted automatically on completion).

## This session (Apr 16)
- Site search upgrades shipped to docs/index.html and output/bibliography_app/index.html. Added phrase matching (quoted strings match adjacent words, previously broke to zero results), word-boundary matching for short tokens (RAG, LLM, GAN no longer match paragraph/storage/etc.), negation syntax (-term, -"phrase"), venue added to the searchable hay, and relevance ranking that overrides the sort dropdown while a query is active (title hits rank above summary hits above author/venue hits). Impact on real data: RAG went from 917 false positives to 8 true matches, GAN from 908 to 42, quoted "machine learning" from 0 (broken) to 3,973. Search time stays 7-15ms per query over 11,879 papers, no new deps.
- Classifier fixes: corrected MODEL constant in classify_papers.py and added not_found_error / 404 to the abort list in update_pipeline.py so a bad MODEL constant fails fast instead of burning 2h.

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
3. 141 unspecified roster entries, CFA roster cleanup
4. Claude still showing as GitHub contributor (cache not cleared after 2+ days, likely needs repo nuke)
5. Optional Scope 2 search improvements if needed: field-scoped queries (author:Smith, title:"..."), better tiebreaker ranking (currently relevance score then year)

## Known issues
- ~3,128 papers tagged "Boston University (unspecified)", mostly authors not in roster
- ~6,950 papers still link to doi.org (no OA version found in OpenAlex)
- 141 roster entries still "Boston University (unspecified)"
- OpenBU metadata bug: all authors get "Boston University" affiliation regardless
- Scholarly Commons uploads full back-catalog, no date filtering
- Weekly pipeline still exists (pending deletion after monthly CI test)
- Claude still showing as GitHub contributor (cache issue, may need repo nuke)
