# BU AI Bibliography -- Status
**Updated:** 2026-04-25

## Numbers
- **Papers:** 11,877 in `data/sonnet_classification_bu_verified.json`
- **Sources:** 13 canonical (OpenAlex, PubMed, DBLP, SSRN, NBER, Scholarly Commons, OpenBU, NIH Reporter, NSF Awards, arXiv, CrossRef, Semantic Scholar, bioRxiv). 11 distinct source tags in data (NBER and arXiv harvest via OpenAlex filters)
- **Schools:** 27 named schools/departments
- **Roster:** 5,896 entries, 4,473 with OpenAlex IDs
- **Rejection index:** 46 DOIs + 51 fingerprints
- **Non-BU AI index:** 3,737 DOIs + 3,625 fingerprints (added 284+291 this session)
- **Validation:** 0 failures, 31 warnings (all pre-existing)
- **Web app:** live at marc-woernle.github.io/bu-ai-bibliography
- **Classifier:** Sonnet 4.6 (`claude-sonnet-4-6`)

## This session (Apr 25-26)
**n3 root-caused: the "Lei Guo trojan" in name verification.** `school_mapper._normalize_name` strips non-Latin characters via `re.sub(r"[^a-z\s-]", "", ...)`, collapsing any Cyrillic/Greek/CJK string to `''` or `' '`. The roster contained one entry whose normalized form was `' '` (Cyrillic 'Лэй Гуо' from `openalex_resolve`), so `FACULTY_BY_FULLNAME[' ']` had exactly one match. Every paper with a non-Latin author hit Tier 3 unique-match and was tagged BU. Fixed in `school_mapper.py:_load_faculty_roster` (don't index empty/whitespace keys into `FACULTY_BY_FULLNAME` or `FACULTY_BY_ALTNAME`) and `update_pipeline.py:verify_bu_authors` Tier 3 (skip empty/whitespace fkeys). Defense in depth at producer and consumer. Future non-Latin roster entries cannot create the same trojan.

**Master cleanup: 291 trojan-era false positives removed.** Audit replayed verify_bu_authors against master with the new logic + reset of cached `is_bu` flags. 291 papers had no legitimate BU evidence (no roster OAID, no BU affiliation string, no non-empty unique name match). Sources: semantic_scholar 144, ssrn 106, crossref 24, biorxiv 4, openalex 4, others. Spot-checked OpenAlex ones: confirmed FPs (authors at Boston College, USC, Emory, Stanford, Univ of Arkansas). Backup at `data/sonnet_classification_bu_verified.backup_trojan_cleanup_20260425.json`. Master: 12,168 → 11,877. DOIs/fingerprints added to non_bu_ai_index so they don't re-harvest.

**Auto-propagation pipeline.** Added `config.DATA_SOURCES` (canonical 13) and `config.CLASSIFIER_DISPLAY_NAME` ('Sonnet 4.6'). `generate_data_js.py:build_metadata` now writes `paper_count`, `sources`, `sources_list`, and `model` to `data.js`. Site (docs + output) reads model and source list from meta with sensible fallbacks. Footer source list is now meta-driven (not hardcoded 11 names). Header "Spanning 1962" replaced with `${dataYearMin}` (computed from data). New `propagate_counts.py` patches README.md (paper count, school count, source mention table, DBLP papers count, master-dataset line, roster line) and updates the GitHub repo description via `gh repo edit`. Hooked into `update_pipeline.regenerate_all_outputs` so every monthly run keeps README + repo description + site in sync automatically.

## TODO
1. **Next monthly run (May 1)**: verify (a) indexes persist across CI runs, (b) propagate_counts runs successfully end-to-end. CI runner needs `gh` CLI installed for the description update; if missing, README still updates.
2. **Wrong-OAID audit**: 387 of 897 `openalex_resolve` roster entries have a non-BU `last_institution` in the OpenAlex cache. Some are legit (ex-BU faculty who moved), some are wrong assignments (Henry Lam→Columbia, Christos Panayiotou→Cyprus). Walk all 897, query OpenAlex for full institutions list, drop entries with no BU institutional history. Separate from n3.
3. **141 unspecified roster entries, CFA roster cleanup**.
4. **Optional Scope 2 search improvements** (Marc said he would tackle these): field-scoped queries, better tiebreaker ranking.

## Known issues
- ~3,100 papers tagged "Boston University (unspecified)", mostly authors not in roster
- ~6,950 papers still link to doi.org (no OA version found in OpenAlex)
- 141 roster entries still "Boston University (unspecified)"
- OpenBU metadata bug: all authors get "Boston University" affiliation regardless
- Scholarly Commons uploads full back-catalog, no date filtering
- `openalex_resolve` produced ~897 roster entries with auto-resolved OAIDs; not all OAIDs are guaranteed BU. Audit pending.

## Layout
- Monthly CI workflow: `.github/workflows/monthly-update.yml` (timeout 120 min, triggered 1st of month 8am UTC, also `workflow_dispatch` with optional `save_candidates=true` for Batch API runs)
- Entry point: `update_monthly.py` (phases 1-6: roster, harvest, filter+classify, merge+maintenance, validate+push, report)
- Shared pipeline: `update_pipeline.py` (harvest orchestration, dedup, classification, BU verification, merge, regen, propagate, git push)
- Counts propagation: `propagate_counts.py` (called from `regenerate_all_outputs`; patches README + GitHub description)
- Batch CLI: `classify_papers.py` (`estimate`/`submit`/`status`/`collect` with `--input=PATH`)
- Batch merge: `merge_batch_results.py`
- Quarterly audit: `quarterly_review.py`
