# BU AI Bibliography -- Status
**Updated:** 2026-04-21

## Numbers
- **Papers:** 12,172 in `data/sonnet_classification_bu_verified.json`
- **Sources:** 13 (OpenAlex, PubMed, DBLP, SSRN, NBER, Scholarly Commons, OpenBU, NIH Reporter, NSF Awards, arXiv, CrossRef, Semantic Scholar, bioRxiv)
- **Roster:** 5,896 entries, 141 with school = unspecified, 4,473 with OpenAlex IDs
- **Rejection index:** 97 entries (46 DOIs + 51 fingerprints) so these are skipped in future harvests
- **Validation:** 0 failures, 31 warnings (all genuine, non-actionable)
- **Web app:** live at marc-woernle.github.io/bu-ai-bibliography
- **Classification model:** Sonnet 4.6 (claude-sonnet-4-6)

## This session (Apr 21)
- Cleared the backlog via Batch API. Harvest-only CI run 24580925294 (Apr 17) produced 3,842 candidates as an artifact. Downloaded locally, submitted as Anthropic Batch msgbatch_01Y53EHrDdkH88rT6UBZrrnd: 3,842/3,842 succeeded, 0 errors, $11.17 actual cost.
- 55 not_relevant papers recorded in rejection index so future harvests skip them. 3,787 classified AI-relevant, of which 291 passed BU verification and were merged into master. Master: 11,881 to 12,172 (+291).
- New tool: merge_batch_results.py (load collect output, split rejected vs kept, record rejections, verify BU, school map, merge into master, regenerate data.js). Dry-run mode available.

## This session (Apr 16-17)
- Site search upgrades shipped: phrase matching ("quoted strings" match adjacent words), word-boundary matching for short tokens (RAG, LLM, GAN stop hitting paragraph/storage/etc.), negation (-term, -"phrase"), venue indexed, relevance ranking when a query is active. Shipped to docs/ and output/. Before/after on live data: RAG 917 to 8, GAN 908 to 42, quoted "machine learning" 0 (broken) to 3,973. 7-15ms per query.
- Classifier fixes: MODEL constant "claude-sonnet-4-6-20250514" (404s) corrected to "claude-sonnet-4-6". Added not_found_error / 404 detection to the abort list in classify_via_sonnet so a bad MODEL constant fails fast instead of burning 2h.
- Rate limiter bumped 1 to 5 rps (cosmetic, since serial throughput is latency-bound at ~0.6 rps).
- Batch API offramp: --save-candidates flag on update_monthly.py dumps filtered candidates and exits before classification. --input=PATH flag on classify_papers.py routes the Batch CLI to any candidate file. workflow_dispatch.inputs.save_candidates on the monthly workflow uploads the candidates as a GitHub artifact.
- After today's merge, next monthly run should hit a much smaller backlog since 55 known-rejections are now pre-filtered and 291 newly-merged papers won't re-trigger classification.

## TODO
1. **Delete weekly pipeline** after confirming this merge holds (remove update_weekly.py and .github/workflows/weekly-update.yml)
2. 141 unspecified roster entries, CFA roster cleanup
3. Claude still showing as GitHub contributor (cache issue, may need repo nuke)
4. Inefficiency worth considering: ~3,496 papers classified AI-relevant but NOT BU-verified this run will appear in next harvest and get reclassified at ~$10 again. Could record them in a "non-BU index" at filter time to skip. Not critical yet.
5. Optional Scope 2 search improvements: field-scoped queries (author:Smith, title:"..."), better tiebreaker ranking.

## Known issues
- ~3,128 papers tagged "Boston University (unspecified)", mostly authors not in roster
- ~6,950 papers still link to doi.org (no OA version found in OpenAlex)
- 141 roster entries still "Boston University (unspecified)"
- OpenBU metadata bug: all authors get "Boston University" affiliation regardless
- Scholarly Commons uploads full back-catalog, no date filtering
- Weekly pipeline still exists (pending deletion)
- Non-BU AI papers rechallenged every month (TODO item 4)
