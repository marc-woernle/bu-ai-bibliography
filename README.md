# BU AI Bibliography

A multi-source pipeline for harvesting, deduplicating, classifying, and annotating **all AI-related academic publications by Boston University faculty**. Currently **11,903 papers** across 27 schools and departments.

**Static site:** [marc-woernle.github.io/bu-ai-bibliography](https://marc-woernle.github.io/bu-ai-bibliography/)

## How it works

```
13 sources (OpenAlex, PubMed, DBLP, SSRN, NBER, Scholarly Commons, OpenBU,
            NIH Reporter, NSF Awards, arXiv, CrossRef, Semantic Scholar, bioRxiv)
  -> harvest by BU ROR ID + keyword/concept filters + faculty name matching
  -> dedup by DOI + title fingerprint
  -> keyword pre-filter (300 AI terms in title/abstract)
  -> embedding pre-filter (sentence-transformers semantic similarity)
  -> Claude Sonnet classification (relevance tier, domains, subfields, annotation)
  -> BU author verification (faculty roster + a 10,351-identity BU author registry with years)
  -> school/department classification (4-tier OAID-first matching)
  -> merge into master dataset
  -> generate static web app
  -> validate against ground truth anchors
  -> push to GitHub Pages
```

## Methodology

### Harvesting
Papers are collected from 13 sources covering journals, conferences, preprints, grants, and institutional repositories. OpenAlex is the primary source, queried by BU's ROR identifier for exact institutional matching. DBLP covers CS conference proceedings (the main gap in OpenAlex) via monthly XML dump processing with two-tier name matching and OpenAlex verification. PubMed handles biomedical literature via MeSH terms. SSRN and NBER cover working papers in law, business, and economics. Remaining sources fill niche gaps: arXiv for preprints, Scholarly Commons for BU Law, OpenBU for theses, NIH/NSF for grant-linked work.

### Filtering and classification
Raw harvests go through three stages of filtering. A keyword pre-filter checks for 300 AI-related terms (from "machine learning" to "adaptive resonance" to "control barrier"). An embedding pre-filter uses sentence-transformers to compute semantic similarity against 33 AI reference texts (threshold 0.30). The two are an OR, not a chain: either signal is enough, and a paper with no abstract passes through untested because title-only text is too thin for either to mean anything.

Both were rebuilt in August 2026 after measuring them against the papers Sonnet had already confirmed. The keyword list had no vocabulary for classical computer vision, for BU's own Center for Adaptive Systems tradition of neural and cognitive modelling, for control and formal methods, or for computational biology; the reference texts were 2015-era and scored a CNN-based clinical paper as far from every one of them. Recall against confirmed-AI papers with abstracts went from 86.8% to 95.3%; end to end on a random sample, including the abstract-less passthrough, 96.8%. Finally, Claude Sonnet classifies each paper into relevance tiers (primary, methodological, peripheral, not_relevant) with domain tags, subfield tags, and a one-line summary. Papers classified as not_relevant are excluded. The initial dataset was classified via the Anthropic Batch API (~$0.003/paper at 50% discount); monthly updates use the standard API.

### Author matching and school classification
BU authorship is verified in two steps. A faculty roster of 5,888 entries, scraped from 24+ BU department web pages with OpenAlex author IDs resolved for ~4,500 of them, answers "is this name associated with BU". A BU author registry of 10,351 identities -- keyed by OpenAlex author ID, because an ID is an identity and a name is not -- answers the question the roster cannot: *when* was this person at BU. It is grown for free during every harvest from the affiliation strings on papers already being processed, and filled in by `resolve_bu_authors.py` from OpenAlex author profiles, whose `affiliations[]` gives the exact years. A name match outside an author's documented BU years is refused. School tags are assigned via a 4-tier strategy: (1) OpenAlex author ID matching against the roster (zero false positives), (2) affiliation text regex against 60+ school/department patterns, (3) full-name roster matching with an OAID-mismatch guard and common-name blocklist, (4) alt-names cache from 98K OpenAlex author profiles. Faculty with dual appointments (e.g., Computing & Data Sciences + Questrom) are tagged to both schools.

## Limitations and known issues

**Coverage gaps.** Fields that primarily publish in proprietary law reviews, book chapters, or non-indexed venues are underrepresented. Conference workshop papers without DOIs can be missed. CS conference proceedings are well-covered via the DBLP source, which contributes 1,586 papers.

**Author disambiguation.** OpenAlex sometimes merges different people under a single author ID, especially for common names. We maintain a blocklist for known false matches and have cleared 19+ wrong IDs from the roster, but more may exist. Separately, a roster name match used to import the matched person's entire career: DBLP carries no affiliation data at all, so 858 papers written at Stanford, Washington and Northeastern by BU alumni sat in the dataset. The BU author registry now bounds every name match by the author's documented BU years; `clean_master_bu_years.py` removes the ones already merged.

**Unspecified school tags.** ~3,100 papers (~26%) are tagged "Boston University (unspecified)" because the BU author is not in our faculty roster, or the roster entry lacks a school assignment. This primarily affects interdisciplinary researchers, postdocs, and visiting scholars.

**Source overlap.** Papers frequently appear in multiple sources. The source count table below reflects total mentions, not unique papers per source.

## Auto-updates

The bibliography stays current via a single monthly GitHub Actions workflow that runs on the 1st of each month. The pipeline is fully automated with zero human intervention required. A comprehensive report is posted as a GitHub Issue after each run.

**Monthly pipeline phases:**

1. **Faculty roster refresh** -- scrape 24+ department pages, resolve new OpenAlex IDs, enrich unspecified entries (with regression protection)
2. **Harvest all 13 sources** -- fault-isolated, each source reports status independently
3. **Filter + classify** -- dedup against master, rejection index, and non-BU AI index; keyword filter; embedding filter; Sonnet classification ($15 cap). Rejected papers and kept-but-not-BU papers feed their respective indexes so future runs skip them.
4. **Maintenance** -- citation refresh, preprint-to-publication tracking, broken URL detection, domain trend analysis, new faculty candidate detection
5. **Validate + push** -- ground truth checks, paper count gates, data.js regeneration
6. **Report** -- full markdown report posted as GitHub Issue with source-by-source breakdown, school distribution, citation milestones, alerts

Cost controls: $15/run hard cap, paper count gates, cumulative cost tracking in `data/update_state.json`.

## Source coverage

| Source | Mentions | What it catches | Affiliation filter |
|--------|----------|----------------|-------------------|
| **OpenAlex** | 15,466 | Primary source, 250M+ works | BU ROR ID (exact) |
| **DBLP** | 1,586 | CS conference proceedings | Faculty name match + OpenAlex verification |
| **OpenBU** | 1,474 | Theses, dissertations, tech reports | Native (all BU) |
| **PubMed** | 1,465 | Biomedical AI work | Affiliation + MeSH terms |
| **NIH Reporter** | 345 | Federal grants | Organization name |
| **Semantic Scholar** | 102 | CS/ML papers | Text search |
| **SSRN** | 57 | Law/policy/business working papers | Faculty name search via CrossRef |
| **Scholarly Commons** | 139 | BU Law faculty scholarship | Native (BU Law) |
| **CrossRef** | 124 | Journal articles catch-all | Text search |
| **NSF Awards** | 15 | NSF-funded AI research | Awardee name |
| **bioRxiv/medRxiv** | 3 | Biomedical preprints | CrossRef DOI prefix |
| **NBER** | -- | Economics working papers | BU ROR via OpenAlex |
| **arXiv** | -- | CS/ML preprints | Category + affiliation |

Papers often appear in multiple sources, so total mentions exceed the 11,903 deduplicated paper count. NBER and arXiv counts are included in the OpenAlex total since they're harvested via OpenAlex filters.

## Setup

```bash
git clone https://github.com/marc-woernle/bu-ai-bibliography.git
cd bu-ai-bibliography
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
export OPENALEX_API_KEY=...   # free, https://openalex.org/settings/api
```

### Running updates
```bash
python update_monthly.py --dry-run    # Preview harvest counts and cost estimate
python update_monthly.py --ci         # Full automated run (CI mode)
python update_monthly.py              # Full run with interactive cost gates
python validate_dataset.py            # Ground truth validation
```

## File structure

```
bu-ai-bibliography/
|
|- Pipeline
|- config.py                    # Constants: ROR ID, 300 AI keywords, OpenAlex auth, rate limits
|- utils.py                     # Deduplication, rate limiter, paper record factory
|- update_pipeline.py           # Shared functions: harvest, filter, classify, merge, validate
|- update_monthly.py            # Monthly auto-update orchestrator (6-phase pipeline)
|
|- Sources
|- source_openalex.py           # OpenAlex (primary, concept + keyword sweeps)
|- source_pubmed.py             # PubMed (MeSH terms + affiliation search)
|- source_dblp.py               # DBLP API harvester (per-faculty search)
|- source_ssrn.py               # SSRN via CrossRef (faculty name search)
|- source_crossref.py           # CrossRef (AI keyword search)
|- source_arxiv.py              # arXiv (category + affiliation search)
|- source_scholarly_commons.py  # BU Law Scholarly Commons (HTML scraping)
|- source_openbu.py             # OpenBU institutional repository (DSpace API)
|- source_semantic_scholar.py   # Semantic Scholar (optional, needs S2_API_KEY)
|- source_in_progress.py        # NIH Reporter + NSF Awards
|- harvest_dblp_dump.py         # DBLP XML dump processor (monthly, auto-download)
|- harvest_nber.py              # NBER working papers via OpenAlex
|
|- Classification & Output
|- classify_papers.py           # Sonnet classification (prompts, parsing, cost tracking)
|- school_mapper.py             # 4-tier OAID-first school classifier
|- generate_data_js.py          # Master JSON -> compact data.js for web app
|- validate_dataset.py          # Ground truth anchor validation
|
|- Faculty Roster
|- build_faculty_roster.py      # Scrape 24+ BU department pages for faculty
|- resolve_openalex_ids.py      # Batch-match faculty to OpenAlex author IDs
|- enrich_unspecified_roster.py # Resolve "unspecified" entries via affiliation strings
|- resolve_bu_authors.py # BU years per author, from OpenAlex profiles (free endpoints)
|- clean_master_bu_years.py # One-shot: drop papers outside their author's BU years
|
|- Data
|- data/
|   |- sonnet_classification_bu_verified.json  # Master dataset (11,903 papers)
|   |- bu_faculty_roster_verified.json         # Faculty roster (5,888 entries)
|   |- bu_author_registry.json                 # BU author identities + their BU years (10,351)
|   |- non_bu_ai_index.json                    # AI papers that are not BU's; never re-harvested
|   |- prefilter_seen_index.json               # Pre-filter rejections, versioned by filter hash
|   |- openalex_bu_authors_cache.json          # 98K OpenAlex author profiles (local)
|   |- update_state.json                       # Auto-update state + source health
|   |- update_log.csv                          # Run history
|
|- Web App
|- output/bibliography_app/
|   |- index.html               # Interactive bibliography (single-file HTML/JS/CSS)
|   |- data.js                  # Paper data (compact, ~12 MB)
|- docs/                        # GitHub Pages deployment
|
|- CLAUDE.md                    # Project guide for Claude Code sessions
|- STATUS.md                    # Current project state
|- README.md
```

## Built with

Classification via **Claude Sonnet 4.6** (Batch API for bulk, standard API for monthly updates). Embedding pre-filter via **sentence-transformers** (`all-MiniLM-L6-v2`).
