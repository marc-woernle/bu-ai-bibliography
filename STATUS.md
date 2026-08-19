# BU AI Bibliography -- Status
**Updated:** 2026-08-19

## Numbers
- **Papers:** 11,877 in `data/sonnet_classification_bu_verified.json`
- **Sources:** 13 canonical (OpenAlex, PubMed, DBLP, SSRN, NBER, Scholarly Commons, OpenBU, NIH Reporter, NSF Awards, arXiv, CrossRef, Semantic Scholar, bioRxiv). 11 distinct source tags in data; NBER and arXiv harvest via OpenAlex
- **Schools:** 27 named schools/departments
- **Roster:** 5,888 entries, 4,465 with OpenAlex IDs. 159 entries now carry `alternate_openalex_ids` for known split profiles; Robertson manually patched
- **BU author registry:** 10,351 identities (4,501 verified against OpenAlex author profiles, 50 confirmed never at BU), 11,386 names
- **Rejection index:** 46 DOIs + 51 fingerprints
- **Non-BU AI index:** 3,738 DOIs + 3,626 fingerprints
- **Validation:** 0 failures, 31 warnings (all pre-existing)
- **Web app:** live at marc-woernle.github.io/bu-ai-bibliography
- **Classifier:** Sonnet 4.6 (`claude-sonnet-4-6`)
- **Relevance labels (user-facing):** Core AI / Applied AI / AI Studies (internal values still primary/methodological/peripheral)

## This session (Aug 19)
**BU author identification, operationalized.** Answering "is this person at BU, and when" without the batch API and without a paid key.

- New `data/bu_author_registry.json` -- 10,351 identities keyed by OpenAlex author ID, each with the year span they published from BU. Two writers, one file:
  - **Free tier, every run.** `fold_papers_into_bu_registry` records every authorship whose own affiliation string names BU, folded per OpenAlex page *before* the AI filter -- a BU chemist's 2004 paper is not going in the bibliography but is still evidence they were at BU in 2004. Zero extra requests.
  - **New `resolve_bu_authors.py`.** Reads each roster OpenAlex ID's author profile and takes BU years straight from `affiliations[]`. 4,346 resolved in 13 minutes, zero errors. A second pass resolves the 1,273 roster entries that have no OpenAlex ID at all, via `/autocomplete/authors` -> candidate profile -> BU years; 59 real BU identities recovered. Replaces `audit_openalex_resolve.py`, which only ever covered the 889 `openalex_resolve` entries.
- **`verify_bu_authors` now checks the registry before accepting a name or ID match.** This used to be a gate bolted onto the DBLP call sites; it belongs where every name-matched source passes through. Measured on master: DBLP 1,586 -> 728, non-DBLP 10,317 -> 10,317 (untouched -- those carry real affiliations and never reach the check). Li Fei-Fei is on our faculty roster and had 14 Stanford papers in master; all 14 now refused, on the evidence that the BU person by that name published 2005-2007.
- **`is_bu` is not evidence and is no longer treated as such.** Of 25,288 `is_bu` authorships in master only 18,917 carry a BU affiliation string: OpenBU stamps "Boston University" onto every author it holds including external co-authors, NIH Reporter and Semantic Scholar carry no affiliation at all, and `verify_bu_authors` itself sets the flag on a name match. Building the registry off `is_bu` would have laundered name matches into evidence and then used that evidence to approve name matches.
- **Direct observation outranks a derived summary.** OpenAlex's `affiliations[]` lists no BU for the Jian Wu whose four BU-affiliated papers we hold from 1988-2003. The resolver no longer writes `bu: false` over an identity we have already observed on a BU paper. Refusals also never apply to a name shared by more than one identity. False refusals measured against master: 0.
- Deleted `gate_affiliationless_by_bu_years` and `build_bu_year_windows` (superseded), and `audit_openalex_resolve.py` (superseded).
- Fixed `fold_papers_into_bu_registry(papers[-len(results):])`, which folded the entire accumulated harvest on any page where nothing parsed.

**OpenAlex is now metered, and we have no key.** Confirmed today: `/works?filter=` returns 502 "policy context unavailable" and `/authors?filter=` returns "Insufficient budget. This request costs $0.0001 but you only have $0 remaining. Resets at midnight UTC." Only `mailto=` is configured anywhere in the repo; CI holds `ANTHROPIC_API_KEY` and `S2_API_KEY` only. Single-entity GETs (`/authors/A123`) and `/autocomplete/authors` are still free and report `cost_usd 0.0`, which is what the author resolver is built on. The primary harvest path is not so lucky and needs a decision.

## TODO
1. **OpenAlex funding decision.** The primary harvest source is metered and we hold no key. Nothing else in the pipeline replaces institution-wide ROR filtering.
2. **Catch-up classification run.** Run #14 harvested 246,939 records, 100,166 survive the pre-filter and dedup: roughly $190 at batch rates.
3. **Retroactive cleanup of master.** 858 DBLP papers already merged sit outside their author's documented BU years. The new check only guards new harvests. Needs a backup and a review pass first.
4. **No gold set / eval.** Nothing in the repo would reveal classification drift.
5. **Simplification, still outstanding.** ~3,100 lines across 9 unreachable files.
6. **141 unspecified roster entries, CFA roster cleanup.**

## Known issues
- ~3,100 papers tagged "Boston University (unspecified)", mostly authors not in roster
- ~6,950 papers still link to doi.org (no OA version found in OpenAlex)
- 141 roster entries still "Boston University (unspecified)"
- OpenBU metadata bug: all authors get "Boston University" affiliation regardless
- Scholarly Commons uploads full back-catalog, no date filtering
- OpenAlex de-merge events can invalidate previously-resolved OAIDs. Re-run `resolve_bu_authors.py --refresh` and `audit_split_profiles.py` quarterly.
- 74 SSRN/Scholarly Commons papers have no real journal venue across any of CrossRef/OpenAlex/SS — confirmed working papers
- Per-faculty CrossRef harvest uses last-name match for BU verification on results; for very common names (Smith, Wang, etc.) some non-BU papers may slip through. Mitigated by Sonnet's relevance gate, but worth periodic spot-checking.

## Layout
- Monthly CI workflow: `.github/workflows/monthly-update.yml` (timeout 120 min, triggered 1st of month 8am UTC)
- Entry point: `update_monthly.py` (phases 1-6: roster, harvest, filter+classify, merge+maintenance, validate+push, report)
- Shared pipeline: `update_pipeline.py` (harvest orchestration, dedup, classification, BU verification, merge, regen, propagate, git push)
- Per-faculty back-fill: `harvest_crossref_per_faculty` in `update_pipeline.py` (high-impact venues for clinical and legal schools)
- Counts propagation: `propagate_counts.py` (called from `regenerate_all_outputs`; patches README + GitHub description)
- Source/model truth: `config.DATA_SOURCES` (canonical 13), `config.CLASSIFIER_DISPLAY_NAME` ('Sonnet 4.6')
- OAID resolver: `resolve_openalex_ids.py` (live BU-affiliation verification on every assignment)
- BU author registry: `data/bu_author_registry.json`, grown free every run by `update_pipeline.fold_papers_into_bu_registry`, filled from OpenAlex profiles by `resolve_bu_authors.py`
- Audits (run quarterly or post-monthly): `audit_split_profiles.py`, `audit_faculty_completeness.py`
- Venue resolver: `resolve_repository_venues.py` (CrossRef → OpenAlex → Semantic Scholar fallback chain)
- Batch CLI: `classify_papers.py` (`estimate`/`submit`/`status`/`collect`)
- Batch merge: `merge_batch_results.py`
- Quarterly audit: `quarterly_review.py`
