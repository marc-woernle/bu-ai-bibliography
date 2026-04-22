# BU AI Bibliography -- Status
**Updated:** 2026-04-22

## Numbers
- **Papers:** 12,168 in `data/sonnet_classification_bu_verified.json`
- **Sources:** 13 (OpenAlex, PubMed, DBLP, SSRN, NBER, Scholarly Commons, OpenBU, NIH Reporter, NSF Awards, arXiv, CrossRef, Semantic Scholar, bioRxiv)
- **Roster:** 5,896 entries, 141 with school = unspecified, 4,473 with OpenAlex IDs
- **Rejection index:** 46 DOIs + 51 fingerprints (papers Sonnet tagged not_relevant)
- **Non-BU AI index:** 3,453 DOIs + 3,334 fingerprints (AI-relevant but failed BU verification, so future harvests skip them)
- **Validation:** 0 failures, 31 warnings (all genuine: 27 roster-coverage mostly SPH epidemiologists, 2 school-coverage for Dental/Wheelock, 1 source-diversity for Earth&Env, 1 anchor-faculty for Stacey Dogan)
- **Web app:** live at marc-woernle.github.io/bu-ai-bibliography
- **Classification model:** Sonnet 4.6 (claude-sonnet-4-6)

## This session (Apr 21-22)
**Backlog cleared via Batch API.** Apr 17 harvest-only CI run 24580925294 produced 3,842 candidates as a downloadable artifact. Downloaded locally, submitted as Anthropic batch msgbatch_01Y53EHrDdkH88rT6UBZrrnd, got 3,842/3,842 succeeded, 0 errors, $11.17 actual cost (vs $22 serial). 55 not_relevant papers and 3,496 AI-relevant-but-not-BU papers went into their respective indexes. 291 BU-verified merged: master 11,881 to 12,172.

**Cyrillic false-positive audit.** Sanity-checked 10 random newly-merged papers; 9 legit but 1 had Cyrillic author names mapped to College of Communication. Ran a full non-Latin-script scan across the 12,172-paper master: 8 hits. Verified each against the real affiliation records. Removed 7 confirmed false positives (authors at UIUC, Columbia, Univ of Cyprus, Kyrgyzstan, Chechen State Univ, Ukrainian journals, Chinese EFL journal). Kept 1 legit (idx=1019, Zervas+Lei Guo 1994 IEEE Trans Neural Networks, real "Northeastern University; Boston University" affiliations). Backup at `data/sonnet_classification_bu_verified.backup_nonLatin_20260421.json`. Master: 12,172 to 12,165.

**Monthly pipeline confirmed working end-to-end.** Triggered full CI run 24751362348. Finished in 1h46m (14 min margin on 2h budget). Harvest 25,096 to 19,773 after dedup (`1,835 in master, 59 previously rejected, 3,429 previously non-BU`, proving the indexes now earn their keep). Keyword+embedding filters narrowed to 89 candidates. Classified 89/89 for $0.51. 3 BU-verified merged, 86 recorded non-BU. Master: 12,165 to 12,168.

**Weekly pipeline deleted.** Removed `update_weekly.py` and `.github/workflows/weekly-update.yml`. Dropped "weekly/monthly" to "monthly" in CLAUDE.md, updated README to mention the indexes in phase 3, bumped paper count. `quarterly_review.py` still reads `last_weekly_run` from state for historical display; left alone (harmless).

**Bug fix during STATUS prep.** Found that `git_commit_and_push` in `update_pipeline.py` was not staging `REJECTED_PATH` or `NON_BU_AI_PATH`, so both indexes were written to the CI runner and then lost when the container tore down. Apr 21 CI run logged "Recorded 86 non-BU AI papers (171 new index entries)" but those never made it to the repo. Fixed now: staging list includes both indexes. Next monthly run will re-tag those 86 papers (~$0.50 one-time) and they will commit going forward.

## This session (Apr 16-17)
- **Site search upgrades** (docs/index.html and output/bibliography_app/index.html): phrase matching ("quoted strings" match adjacent words, previously tokenized to zero), word-boundary for short tokens (RAG, LLM, GAN stop hitting paragraph/storage/etc.), negation syntax (-term, -"phrase"), venue added to the searchable hay, relevance ranking that overrides the sort dropdown when a query is active. Before/after on live data: RAG 917 to 8, GAN 908 to 42, quoted "machine learning" 0 (broken) to 3,973. 7-15ms per query.
- **Classifier robustness**: MODEL constant `claude-sonnet-4-6-20250514` (404s, non-existent snapshot) corrected to `claude-sonnet-4-6`. Added `not_found_error` / 404 detection to the abort list in `classify_via_sonnet` so a bad MODEL constant fails fast instead of burning 2h like Apr 15's run did.
- **Rate limiter** bumped 1 to 5 rps. Cosmetic in practice, since serial call throughput is latency-bound at ~0.6 rps regardless.
- **Batch API offramp**: `--save-candidates=PATH` flag on `update_monthly.py` dumps the filtered candidate list and exits before classification. `--input=PATH` flag on `classify_papers.py` routes the batch CLI to arbitrary candidate files. `workflow_dispatch.inputs.save_candidates` on the monthly workflow uploads candidates as a GitHub artifact. `merge_batch_results.py` handles post-collect merge (rejections + non-BU records, BU verify, school map, master merge, regen data.js). Dry-run mode available.

## TODO
1. **Next monthly run (May 1)**: verify indexes now persist across CI runs. Should see the 86 Apr-21 non-BU entries committed, plus whatever the May harvest surfaces.
2. **141 unspecified roster entries, CFA roster cleanup**: faculty records where school = "Boston University (unspecified)". Not blocking; reduces the "unspecified" count in papers when fixed.
3. **Investigate why `is_bu` was True for the 7 removed non-Latin papers** despite their author records showing clearly non-BU affiliations. `school_mapper` or `verify_bu_authors` has a gap letting non-Latin names through name matching. Lei Guo pattern, recurring.
4. **Claude still showing as GitHub contributor**: cache issue, may need repo nuke.
5. **Optional Scope 2 search improvements** (Marc said he would tackle these after everything else): field-scoped queries (author:Smith, title:"..."), better tiebreaker ranking beyond the current score+year.

## Known issues
- ~3,100 papers tagged "Boston University (unspecified)", mostly authors not in roster
- ~6,950 papers still link to doi.org (no OA version found in OpenAlex)
- 141 roster entries still "Boston University (unspecified)"
- OpenBU metadata bug: all authors get "Boston University" affiliation regardless
- Scholarly Commons uploads full back-catalog, no date filtering
- BU verification allows non-Latin author names through in some cases (Lei Guo incident pattern, confirmed again this session; 7 false positives removed, root cause still not fixed)

## Layout (post-weekly-deletion)
- Monthly CI workflow: `.github/workflows/monthly-update.yml` (timeout 120 min, triggered 1st of month 8am UTC, also `workflow_dispatch` with optional `save_candidates=true` input for Batch API runs)
- Entry point: `update_monthly.py` (phases 1-6: roster, harvest, filter+classify, merge+maintenance, validate+push, report)
- Shared pipeline: `update_pipeline.py` (harvest orchestration, dedup, classification, BU verification, merge, git push)
- Batch CLI: `classify_papers.py` (`estimate`/`submit`/`status`/`collect` with `--input=PATH`)
- Batch merge: `merge_batch_results.py` (takes collect output, records rejections + non-BU, verifies BU, school maps, merges, regens data.js)
- Quarterly audit: `quarterly_review.py` (still reads `last_weekly_run` for historical display)
