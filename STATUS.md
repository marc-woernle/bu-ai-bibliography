# BU AI Bibliography -- Status
**Updated:** 2026-04-21

## Numbers
- **Papers:** 12,165 in `data/sonnet_classification_bu_verified.json`
- **Sources:** 13 (OpenAlex, PubMed, DBLP, SSRN, NBER, Scholarly Commons, OpenBU, NIH Reporter, NSF Awards, arXiv, CrossRef, Semantic Scholar, bioRxiv)
- **Roster:** 5,896 entries, 141 with school = unspecified, 4,473 with OpenAlex IDs
- **Rejection index:** 97 entries (46 DOIs + 51 fingerprints), skipped in future harvests
- **Non-BU AI index:** 3,453 DOIs + 3,334 fingerprints, skipped in future harvests (saves ~$10/month re-classification)
- **Validation:** 0 failures, 31 warnings (all genuine, non-actionable)
- **Web app:** live at marc-woernle.github.io/bu-ai-bibliography
- **Classification model:** Sonnet 4.6 (claude-sonnet-4-6)

## This session (Apr 21)
- Cleared the backlog via Batch API. Harvest-only CI run 24580925294 (Apr 17) produced 3,842 candidates as an artifact. Downloaded locally, submitted as Anthropic Batch msgbatch_01Y53EHrDdkH88rT6UBZrrnd: 3,842/3,842 succeeded, 0 errors, $11.17 actual cost.
- 55 not_relevant papers recorded in rejection index so future harvests skip them. 3,787 classified AI-relevant, of which 291 passed BU verification and were merged into master. Master: 11,881 to 12,172 (+291).
- New tool: merge_batch_results.py (load collect output, split rejected vs kept, record rejections, verify BU, school map, merge into master, regenerate data.js). Dry-run mode available.
- Added non-BU AI index (data/non_bu_ai_index.json) mirroring the rejection-index pattern. dedup_against_master now skips papers previously classified AI-relevant but not BU-verified. Both pipelines (update_monthly.py and merge_batch_results.py) call record_non_bu_ai after verify_bu_authors. Retroactively populated with this batch's 3,496 non-verified papers.
- Sanity-checked 10 random newly-merged papers: 9 legit (Saenko+Saligrama, Pacchiano, Gurari, Neidle, Han, etc.), 1 bogus (the Cyrillic #7). Ran full non-Latin audit across master: 8 papers with Cyrillic/Greek/Chinese bu_author_names. Verified against affiliations: 7 removed as false positives (UIUC/Columbia/Cyprus/Kyrgyzstan/Chechen/Ukrainian authors that got mis-flagged as BU), 1 kept (idx=1019 1994 Zervas+Lei Guo IEEE Trans, real Northeastern+BU affiliations). Removed DOIs/fingerprints added to non-BU AI index. Backup at data/sonnet_classification_bu_verified.backup_nonLatin_20260421.json.

## This session (Apr 16-17)
- Site search upgrades shipped: phrase matching ("quoted strings" match adjacent words), word-boundary matching for short tokens (RAG, LLM, GAN stop hitting paragraph/storage/etc.), negation (-term, -"phrase"), venue indexed, relevance ranking when a query is active. Shipped to docs/ and output/. Before/after on live data: RAG 917 to 8, GAN 908 to 42, quoted "machine learning" 0 (broken) to 3,973. 7-15ms per query.
- Classifier fixes: MODEL constant "claude-sonnet-4-6-20250514" (404s) corrected to "claude-sonnet-4-6". Added not_found_error / 404 detection to the abort list in classify_via_sonnet so a bad MODEL constant fails fast instead of burning 2h.
- Rate limiter bumped 1 to 5 rps (cosmetic, since serial throughput is latency-bound at ~0.6 rps).
- Batch API offramp: --save-candidates flag on update_monthly.py dumps filtered candidates and exits before classification. --input=PATH flag on classify_papers.py routes the Batch CLI to any candidate file. workflow_dispatch.inputs.save_candidates on the monthly workflow uploads the candidates as a GitHub artifact.
- After today's merge, next monthly run should hit a much smaller backlog since 55 known-rejections are now pre-filtered and 291 newly-merged papers won't re-trigger classification.

## TODO
1. Trigger one normal monthly CI run to confirm end-to-end (harvest+classify+merge) works post-fixes. Expected small backlog since rejection + non-BU indexes are live.
2. **Delete weekly pipeline** AFTER the monthly test above succeeds (remove update_weekly.py and .github/workflows/weekly-update.yml)
3. 141 unspecified roster entries, CFA roster cleanup
4. Investigate why is_bu was True for all 7 removed non-Latin papers despite their affiliations being clearly non-BU. school_mapper or verify_bu_authors has a gap letting non-Latin names through name matching. Lei Guo pattern, recurring.
5. Claude still showing as GitHub contributor (cache issue, may need repo nuke)
6. Optional Scope 2 search improvements: field-scoped queries (author:Smith, title:"..."), better tiebreaker ranking.

## Known issues
- ~3,128 papers tagged "Boston University (unspecified)", mostly authors not in roster
- ~6,950 papers still link to doi.org (no OA version found in OpenAlex)
- 141 roster entries still "Boston University (unspecified)"
- OpenBU metadata bug: all authors get "Boston University" affiliation regardless
- Scholarly Commons uploads full back-catalog, no date filtering
- Weekly pipeline still exists (pending deletion)
- BU verification allows non-Latin author names through in some cases (Lei Guo incident pattern, confirmed again in this session's sanity check)
