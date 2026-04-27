# BU AI Bibliography -- Status
**Updated:** 2026-04-26

## Numbers
- **Papers:** 11,876 in `data/sonnet_classification_bu_verified.json`
- **Sources:** 13 canonical (OpenAlex, PubMed, DBLP, SSRN, NBER, Scholarly Commons, OpenBU, NIH Reporter, NSF Awards, arXiv, CrossRef, Semantic Scholar, bioRxiv). 11 distinct source tags in data (NBER and arXiv harvest via OpenAlex filters)
- **Schools:** 27 named schools/departments
- **Roster:** 5,888 entries, 4,465 with OpenAlex IDs (8 wrong-OAID entries dropped this session)
- **Rejection index:** 46 DOIs + 51 fingerprints
- **Non-BU AI index:** 3,738 DOIs + 3,626 fingerprints
- **Validation:** 0 failures, 31 warnings (all pre-existing)
- **Web app:** live at marc-woernle.github.io/bu-ai-bibliography
- **Classifier:** Sonnet 4.6 (`claude-sonnet-4-6`)

## This session (Apr 25-26)
**n3 root-caused: the "Lei Guo trojan" in name verification.** `school_mapper._normalize_name` strips non-Latin characters via `re.sub(r"[^a-z\s-]", "", ...)`, collapsing any Cyrillic/Greek/CJK string to `''` or `' '`. The roster contained one entry whose normalized form was `' '` (Cyrillic 'Лэй Гуо' from `openalex_resolve`), so `FACULTY_BY_FULLNAME[' ']` had exactly one match. Every paper with a non-Latin author hit Tier 3 unique-match and was tagged BU. Fixed in `school_mapper.py:_load_faculty_roster` (don't index empty/whitespace keys into `FACULTY_BY_FULLNAME` or `FACULTY_BY_ALTNAME`) and `update_pipeline.py:verify_bu_authors` Tier 3 (skip empty/whitespace fkeys). Defense in depth at producer and consumer. Future non-Latin roster entries cannot create the same trojan.

**Master cleanup: 291 trojan-era false positives removed.** Audit replayed verify_bu_authors against master with the new logic + reset of cached `is_bu` flags. 291 papers had no legitimate BU evidence (no roster OAID, no BU affiliation string, no non-empty unique name match). Sources: semantic_scholar 144, ssrn 106, crossref 24, biorxiv 4, openalex 4, others. Spot-checked OpenAlex ones: confirmed FPs (authors at Boston College, USC, Emory, Stanford, Univ of Arkansas). Backup at `data/sonnet_classification_bu_verified.backup_trojan_cleanup_20260425.json`. Master: 12,168 → 11,877. DOIs/fingerprints added to non_bu_ai_index so they don't re-harvest.

**Auto-propagation pipeline.** Added `config.DATA_SOURCES` (canonical 13) and `config.CLASSIFIER_DISPLAY_NAME` ('Sonnet 4.6'). `generate_data_js.py:build_metadata` now writes `paper_count`, `sources`, `sources_list`, and `model` to `data.js`. Site (docs + output) reads model and source list from meta with sensible fallbacks. Footer source list is now meta-driven (not hardcoded 11 names). Header "Spanning 1962" replaced with `${dataYearMin}` (computed from data). New `propagate_counts.py` patches README.md (paper count, school count, source mention table, DBLP papers count, master-dataset line, roster line) and updates the GitHub repo description via `gh repo edit`. Hooked into `update_pipeline.regenerate_all_outputs` so every monthly run keeps README + repo description + site in sync automatically.

**OAID audit + resolver hardening.** Walked all 897 `openalex_resolve` roster entries against OpenAlex's full affiliations history (`audit_openalex_resolve.py`). 879 confirmed BU (current or former), 8 wrong (no BU history at all): Rasheed Zakaria (Alder Hey Hospital UK), Nan Feng (Beijing), Jiyeong Hong (Korea/GEOMAR), Monica Chang (Alameda/CMU), Eunah Chung (Cincinnati Children's), Yuqing Zhang (MGH, 1 work), Chenyu Wang (MGH/Wuhan), Sanket Kaushik (AIIMS India). Dropped all 8 from roster (5,896 → 5,888). 1 paper in master depended on a wrong OAID with no other BU evidence (Hybrid Vision-Force Control via Nan Feng wrong OAID); removed and added to non_bu_ai_index. The other 4 papers had real "Boston University" affiliation strings on the wrong-OAID author so kept (those Sanket Kaushik / Eunah Chung / Rasheed Zakaria / Chenyu Wang names are real BU-affiliated authors with currently-unknown OAIDs).

To prevent recurrence: added `_verify_bu_in_affiliations` live-check to `resolve_openalex_ids.match_faculty`. Every resolved candidate is now re-queried against OpenAlex's full affiliations list and rejected if BU's ROR doesn't appear. Catches stale-cache cases where OpenAlex has de-merged a wrongly-merged author profile after our cache was built. One extra API call per resolution (~10 calls/month), polite-rate paced.

## TODO
1. **Next monthly run (May 1)**: verify (a) indexes persist across CI runs, (b) propagate_counts runs end-to-end (CI runner needs `gh` CLI for the description update; if missing, README still updates), (c) the new resolver verification step (`_verify_bu_in_affiliations`) works on any newly-resolved faculty.
2. **141 unspecified roster entries, CFA roster cleanup**.
3. **Optional Scope 2 search improvements** (Marc said he would tackle these): field-scoped queries, better tiebreaker ranking.

## Known issues
- ~3,100 papers tagged "Boston University (unspecified)", mostly authors not in roster
- ~6,950 papers still link to doi.org (no OA version found in OpenAlex)
- 141 roster entries still "Boston University (unspecified)"
- OpenBU metadata bug: all authors get "Boston University" affiliation regardless
- Scholarly Commons uploads full back-catalog, no date filtering
- OpenAlex de-merge events can quietly invalidate a previously-resolved OAID. The new `_verify_bu_in_affiliations` check at resolve time catches most cases; re-run `audit_openalex_resolve.py` periodically (quarterly) to catch any that drift after assignment.

## Layout
- Monthly CI workflow: `.github/workflows/monthly-update.yml` (timeout 120 min, triggered 1st of month 8am UTC, also `workflow_dispatch` with optional `save_candidates=true` for Batch API runs)
- Entry point: `update_monthly.py` (phases 1-6: roster, harvest, filter+classify, merge+maintenance, validate+push, report)
- Shared pipeline: `update_pipeline.py` (harvest orchestration, dedup, classification, BU verification, merge, regen, propagate, git push)
- Counts propagation: `propagate_counts.py` (called from `regenerate_all_outputs`; patches README + GitHub description from master)
- Source/model truth: `config.DATA_SOURCES` (canonical 13), `config.CLASSIFIER_DISPLAY_NAME` ('Sonnet 4.6'). Site reads these via `data.js` meta.
- OAID resolver: `resolve_openalex_ids.py` (live BU-affiliation verification on every assignment)
- OAID audit: `audit_openalex_resolve.py` (quarterly check for stale OAIDs; report at `data/openalex_resolve_audit.json`)
- Batch CLI: `classify_papers.py` (`estimate`/`submit`/`status`/`collect` with `--input=PATH`)
- Batch merge: `merge_batch_results.py`
- Quarterly audit: `quarterly_review.py`
