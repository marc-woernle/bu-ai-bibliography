# BU AI Bibliography -- Status
**Updated:** 2026-04-28

## Numbers
- **Papers:** 11,877 in `data/sonnet_classification_bu_verified.json`
- **Sources:** 13 canonical (OpenAlex, PubMed, DBLP, SSRN, NBER, Scholarly Commons, OpenBU, NIH Reporter, NSF Awards, arXiv, CrossRef, Semantic Scholar, bioRxiv). 11 distinct source tags in data; NBER and arXiv harvest via OpenAlex
- **Schools:** 27 named schools/departments
- **Roster:** 5,888 entries, 4,465 with OpenAlex IDs. 159 entries now carry `alternate_openalex_ids` for known split profiles; Robertson manually patched
- **Rejection index:** 46 DOIs + 51 fingerprints
- **Non-BU AI index:** 3,738 DOIs + 3,626 fingerprints
- **Validation:** 0 failures, 31 warnings (all pre-existing)
- **Web app:** live at marc-woernle.github.io/bu-ai-bibliography
- **Classifier:** Sonnet 4.6 (`claude-sonnet-4-6`)
- **Relevance labels (user-facing):** Core AI / Applied AI / AI Studies (internal values still primary/methodological/peripheral)

## This session (Apr 28)
**Robust harvest — pipeline upgrades.**
- Roster gained `alternate_openalex_ids` field; `school_mapper._load_faculty_roster` indexes every alt OAID into `FACULTY_BY_OAID` so split-profile faculty (Robertson: A5050547091 + A5016962908) tag correctly.
- New `audit_split_profiles.py`: queries OpenAlex by name + BU ROR, identifies candidate alternate profiles, requires Boston-area `last_known_institutions` for auto-apply. Shipped 159 alternates.
- New `harvest_crossref_per_faculty` (`update_pipeline.py`): for every faculty in Med / SPH / Law / Sargent / Wheelock / CDS, query CrossRef by author for high-impact venues (JAMA, NEJM, Lancet, Nature, Science, top law reviews). Hooked into `harvest_all_sources` so every monthly run does the back-fill. Catches papers the OpenAlex-ROR-only harvest misses when faculty have split profiles or recent papers haven't yet been ROR-linked.
- Bug fix in the new harvester: `_parse_crossref_item` is tuned for SSRN and hardcodes `venue="SSRN Electronic Journal"` and `source="ssrn"`. Override both with the actual CrossRef container-title and `source="crossref"`.
- New `audit_faculty_completeness.py`: monthly post-harvest audit, queries CrossRef counts by faculty for the last 24 months filtered to AI keywords, compares to master, flags >30% coverage gaps. Hooked into `update_monthly.generate_report` so the GitHub Issue post-monthly surfaces drift.

**Robertson recovery (one-shot).**
- Manually added Robertson's confirmed alt OAID `A5016962908` to roster (audit's auto-filter requires Boston-area last_known and his alt's last_known is "Boston Public Schools" — too broad to auto-accept).
- Pulled "The First AI Drug Prescriber" (`10.1001/jama.2026.3533`) via the new per-faculty harvester, classified by Sonnet as `peripheral` (AI Studies tier — research about an AI prescribing system, not building one), tagged School of Law via name match, merged. Master: 11,876 → 11,877.
- "A New Legal Standard for Medical Malpractice" (`10.1001/jama.2025.0097`) classified `not_relevant` by Sonnet (no actual AI content despite the title); not added.

**Classification clarity (Track B).**
- Tightened the Sonnet system prompt with worked examples for each tier and an explicit boundary heuristic: "a paper wholly about AI by topic does NOT make it primary. A law review article entirely about AI regulation is peripheral, not primary, because it doesn't build AI." Sharpens the methods-vs-studies boundary.
- Renamed user-facing labels: methodological → "Applied AI", peripheral → "AI Studies". Internal `ai_relevance` values unchanged. Legend dots have hover-tooltips with one-line definitions and example titles for each tier.
- No bulk re-classification — current peripheral tags are correct per the new prompt; just relabeled to be more dignifying for AI-policy / AI-law / AI-ethics work.

**Search v2 (Track C).**
- Field-scoped queries: `author:Robertson`, `venue:JAMA`, `year:2024..2026`, `school:Law`, `domain:Healthcare`, `subfield:"Medical Imaging"`. Negation `-author:X` works. Search box `title=` attribute documents the syntax.
- Author fuzzy: strips punctuation, matches first-name prefixes. `Chris` finds `Christopher T.`, `C. Robertson` finds him too.
- Stemming/typo tolerance and global search rebuild deliberately deferred — small ROI at 12k papers, revisit when dataset grows.

**Venue resolution (Track D).**
- `resolve_repository_venues.py` now chains CrossRef → OpenAlex → Semantic Scholar with shared scoring (title sim ≥0.85, year ±1, author surname overlap). First confident match wins. SS ran on the 74 still-unmatched SSRN/SC papers and confirmed they're true working papers — none have a real journal version in any of the three databases.

## TODO
1. **Next monthly run (May 1)** verifies end-to-end: per-faculty CrossRef back-fill harvests new high-impact-venue papers, completeness audit posts to the Issue, propagate_counts updates README + repo description, the relabeled legend ships to GitHub Pages.
2. **141 unspecified roster entries, CFA roster cleanup**.
3. **Re-classification under tightened prompt** — deferred. Re-evaluate if specific cases keep mis-tagging.
4. **Global search rebuild** — Marc tabled; revisit after current rollout.

## Known issues
- ~3,100 papers tagged "Boston University (unspecified)", mostly authors not in roster
- ~6,950 papers still link to doi.org (no OA version found in OpenAlex)
- 141 roster entries still "Boston University (unspecified)"
- OpenBU metadata bug: all authors get "Boston University" affiliation regardless
- Scholarly Commons uploads full back-catalog, no date filtering
- OpenAlex de-merge events can invalidate previously-resolved OAIDs; `_verify_bu_in_affiliations` catches most. Re-run `audit_openalex_resolve.py` and `audit_split_profiles.py` quarterly.
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
- Audits (run quarterly or post-monthly): `audit_openalex_resolve.py`, `audit_split_profiles.py`, `audit_faculty_completeness.py`
- Venue resolver: `resolve_repository_venues.py` (CrossRef → OpenAlex → Semantic Scholar fallback chain)
- Batch CLI: `classify_papers.py` (`estimate`/`submit`/`status`/`collect`)
- Batch merge: `merge_batch_results.py`
- Quarterly audit: `quarterly_review.py`
