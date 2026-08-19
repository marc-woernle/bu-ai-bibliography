# BU AI Bibliography -- Status
**Updated:** 2026-08-19

## Numbers
- **Papers:** 11,903 in `data/sonnet_classification_bu_verified.json` (11,045 after `clean_master_bu_years.py --apply`)
- **Sources:** 13 canonical (OpenAlex, PubMed, DBLP, SSRN, NBER, Scholarly Commons, OpenBU, NIH Reporter, NSF Awards, arXiv, CrossRef, Semantic Scholar, bioRxiv). 11 distinct source tags in data; NBER and arXiv harvest via OpenAlex
- **Schools:** 27 named schools/departments
- **Roster:** 5,888 entries, 4,465 with OpenAlex IDs. 159 entries now carry `alternate_openalex_ids` for known split profiles; Robertson manually patched
- **AI keywords:** 300 (172 before this session)
- **Pre-filter recall:** 95.3% against confirmed-AI papers with abstracts (86.8% before)
- **BU author registry:** 18,431 identities (4,501 verified against OpenAlex author profiles, 50 confirmed never at BU), 18,844 names
- **Rejection index:** 46 DOIs + 51 fingerprints
- **Non-BU AI index:** 9,049 entries (4,584 DOIs + 4,465 fingerprints)
- **Validation:** 0 failures, 31 warnings (all pre-existing)
- **Web app:** live at marc-woernle.github.io/bu-ai-bibliography
- **Classifier:** Sonnet 4.6 (`claude-sonnet-4-6`)
- **Relevance labels (user-facing):** Core AI / Applied AI / AI Studies (internal values still primary/methodological/peripheral)

## This session (Aug 19)

**Coverage, measured against every source rather than reported as effort.**

| source | what exists | we hold | never evaluated |
|---|---|---|---|
| OpenAlex, both AI taxonomies | 21,557 | 6,694 | **14,863 (69%)** |
| PubMed, BU affiliation + AI terms | 2,423 | 1,330 | **1,093 (45%)** |
| arXiv, cs.AI/LG/CV/CL | 88 | **0** | 88 |

Through the widened pre-filter that is **9,465 candidates, about $28** at batch rates, sitting in `data/backfill_candidates.json`. The decade profile is the finding: 2000s 2,044, 1990s 965, 1980s 429. The gap is not recent work the monthly run fumbled, it is historical depth that every harvester's date window cuts off.

Run it from the Actions tab, no terminal: **Backfill classification** workflow, `stage: estimate` -> `submit` -> `collect`. It uses the existing `ANTHROPIC_API_KEY` secret and commits its own results.

**A gold set, and an honest label on it.** `data/gold_set.json` (300 entries) plus `eval_classifier.py`. Every number this project has produced is recall; precision has never been measured, and nothing would have revealed classification drift. The set is 60 per tier plus 120 negatives, half of it deliberately borderline, because obvious cases never move and therefore detect nothing.

It is seeded from Sonnet's own high-confidence labels, so as it stands it is a **drift detector, not a correctness oracle** — it can tell you an answer changed, not that the original was right, because Sonnet wrote both sides. Every entry carries `"reviewed": false`. Reviewing the 120 borderline ones by hand is what converts it into a real oracle.

**arXiv contributes zero papers.** 0 of 11,903 carry an arXiv tag. The date-bound bug is fixed but the source has never produced anything, and arXiv's affiliation metadata is too sparse for `all:"Boston University"` to work — the real path is per-author, which is not built.

**Measured actual coverage against OpenAlex, and it is not good.** With the key in hand, asked OpenAlex what BU has rather than reporting what we harvested:

- **200,554** BU works all-time
- **18,475** carry an AI concept — fetched in **93 requests for $0.0093**
- **6,242 (33.8%)** of those are in master
- **12,233 (66.2%) have never been evaluated**, and 4,896 of them predate 2010, which is exactly the depth the other harvesters' date windows cut off

Through the new pre-filter, 7,253 of the 12,233 survive to classification: **about $22 at batch rates.** That is the cheapest recall available anywhere in this project, and it was invisible because nothing ever compared what we hold against what exists.

New `source_openalex_concepts.py`, wired into the harvest. It asks OpenAlex directly for the works it has already tagged as AI instead of paging all 200,554 and filtering. Deliberately not date-windowed. Verified live: 18,475/18,475, no truncation, 93 requests, $0.0093. Folding its authorships into the registry took it from **10,351 to 18,431 identities** for that same cent.

The concept tags are noisy both ways — they catch "On the compactification of strongly pseudoconvex surfaces" and they miss applied work — so this supplements the ROR sweep and the keyword sweep rather than replacing either.

**The pre-filter was dropping 1 in 8 real AI papers. Fixed, measured.**
Recall against the papers Sonnet had already confirmed: **86.8% -> 95.3%** (end to end, including the abstract-less passthrough, a random sample measures 96.8%). The cost is that admission on a control of 3,877 real BU biomedical abstracts goes 35.1% -> 50.9%, which the rejection memory makes a one-time bill rather than a monthly one.

The 1,259 misses were not random. They clustered into families the keyword list had no words for at all:

- classical computer vision, from before anyone wrote "deep learning" — head tracking, modal matching, saliency, action recognition
- BU's own Center for Adaptive Systems tradition — Adaptive Resonance Theory, laminar thalamocortical circuits, memristive memory
- control and formal methods — temporal logic, control barrier functions
- network science, and computational biology where the method is machine learning and every word is biology

Fixes: `AI_KEYWORDS_UNDERSERVED` in `config.py`, 128 terms (172 -> 300). Each appears in at least one confirmed paper the old filter dropped, and each was scored against the control set — the worst single term admits 1.7% of it, most admit none. Together they recover 326 of the 1,259. And 18 new `AI_REFERENCE_TEXTS` (15 -> 33): MiniLM scores topical similarity, and none of the old fifteen talk like a 1998 vision paper or a cortical model. At the same 0.30 threshold they take the embedding arm from recovering 5.9% of the missed papers to 57.3%.

**OpenAlex metering: the answer is a free key, not money.**
Measured today. Filtered list queries — every harvest query we make — cost **$0.10 per 1,000** against a daily budget that resets at midnight UTC. Single-entity GETs (`/authors/A123`) and `/autocomplete` are **free**. Keyless gets **$0.10/day**; a **free** key gets **$1/day**, ten times as much. A full BU sweep is ~250k records, about 1,250 calls at `per_page=200` — roughly three cents with a key, and impossible without one.

- `config.openalex_headers()` is now the only way to build an OpenAlex request, and it attaches `OPENALEX_API_KEY` when set. All ten call sites across the repo were rewired to it.
- A 429 whose body says "Insufficient budget" is no longer retried. It is not transient, and backing off through it burned a source's whole time budget and then reported a generic rate-limit failure.
- `OPENALEX_API_KEY` wired into the monthly workflow. **Action: create the key at https://openalex.org/settings/api and add it as a repo secret.**

**858 papers that were never BU's, removed.**
`clean_master_bu_years.py` (dry-run by default). DBLP has no affiliation data, so a roster name match imported the matched person's entire career: Mari Ostendorf's 67 Washington papers, Vladimir Pavlovic's 61 from Rutgers, Mac Schwager's 55 from Stanford, Christopher Amato's 30 from Northeastern. Removed records are archived to `data/removed_outside_bu_years.json` and folded into the non-BU AI index (7,360 -> 9,049 entries) so they are never re-harvested. **Not yet applied to the repo's master** — run `python clean_master_bu_years.py --apply` to do it; the script backs the file up first. Master goes 11,903 -> 11,045.

**Dead code: 8 files, 2,376 lines deleted.**
`format_output.py` (superseded by `generate_data_js.py`), `harvest_bulk_openalex.py` and `harvest_by_faculty_id.py` (both superseded by `source_openalex.py`, both still hardcoding their own copy of the BU ROR), `backfill_author_oaids.py`, `gap_check.py` (superseded by `quarterly_review.faculty_gap_check`), `merge_all.py` (mirrors a `harvest.py` that no longer exists), `audit_law_papers.py`, `backfill_pubmed.py`. Verified unreachable from every entry point and from the workflows, including lazy in-function imports. 39 -> 31 Python files.

**Earlier the same day: BU author identification, operationalized.** `data/bu_author_registry.json`, 10,351 identities keyed by OpenAlex author ID with the years each published from BU. Grown free from affiliation strings during every harvest; filled in by `resolve_bu_authors.py` from OpenAlex author profiles (4,346 in 13 minutes, zero errors) and by autocomplete for the 1,273 roster entries that have no OpenAlex ID. `verify_bu_authors` checks it before accepting any name or ID match — DBLP 1,586 -> 728, non-DBLP 10,317 -> 10,317 untouched. `is_bu` is not treated as evidence: only 18,917 of 25,288 `is_bu` authorships carry a BU affiliation string.

## TODO
1. **Add the OpenAlex key as the `OPENALEX_API_KEY` repo secret** (Settings -> Secrets and variables -> Actions). The key exists; nothing in the repo carries it, and CI has $0.10/day without it.
2. **Apply the master cleanup**: `python clean_master_bu_years.py --apply` (11,903 -> 11,045).
3. **Catch-up classification.** Two options now: the 7,253 never-evaluated OpenAlex AI-concept works at ~$22, or the full 13-source backlog at ~$275. The $22 one is strictly higher yield per dollar; do it first.
4. **No gold set / eval.** Nothing in the repo would reveal classification drift. The 400-paper spot check is ad hoc.
5. **141 unspecified roster entries, CFA roster cleanup.**
6. **10 roster entries with no OpenAlex ID came from `openalex_resolve`,** i.e. from a resolver that failed to resolve. They are pure name-match liability.

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
- One-shot maintenance: `clean_master_bu_years.py` (dry-run by default)
- OpenAlex auth: `config.openalex_headers()` — the only sanctioned way to build an OpenAlex request
- Venue resolver: `resolve_repository_venues.py` (CrossRef → OpenAlex → Semantic Scholar fallback chain)
- Batch CLI: `classify_papers.py` (`estimate`/`submit`/`status`/`collect`)
- Batch merge: `merge_batch_results.py`
- Quarterly audit: `quarterly_review.py`
