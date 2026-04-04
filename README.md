# BU AI Bibliography

A multi-source pipeline for harvesting, deduplicating, classifying, and annotating **all AI-related academic publications by Boston University faculty**. Currently **10,456 papers** across 24 schools and departments.

**Live site:** [marc-woernle.github.io/bu-ai-bibliography](https://marc-woernle.github.io/bu-ai-bibliography/)

## How it works

```
Sources (OpenAlex, PubMed, SSRN, Scholarly Commons, OpenBU, NIH, NSF, arXiv, CrossRef, bioRxiv)
  → harvest by BU ROR ID + keyword/concept filters
  → dedup by DOI + title fingerprint
  → keyword pre-filter (187 AI terms in title/abstract)
  → embedding pre-filter (sentence-transformers semantic similarity)
  → Claude Sonnet classification (relevance tier, domains, subfields, annotation)
  → BU author verification (5,896-entry faculty roster with OpenAlex IDs)
  → school/department classification (4-tier OAID-first matching)
  → merge into master dataset
  → generate static web app
  → validate against ground truth anchors
  → push to GitHub Pages
```

## Methodology

### Harvesting
Papers are collected from 10 sources. OpenAlex is the primary source, queried by BU's ROR identifier for exact institutional matching. Other sources use a mix of affiliation text search, MeSH terms, faculty name matching, and direct repository access. Each source has known biases — OpenAlex undercounts conference proceedings, PubMed only covers biomedical literature, SSRN skews toward law and business. We cast a wide net and filter downstream rather than relying on any single source.

### Filtering and classification
Raw harvests go through three stages of filtering. A keyword pre-filter checks for 187 AI-related terms (from "machine learning" to "algorithmic fairness"). An embedding pre-filter uses sentence-transformers to compute semantic similarity against AI reference texts (threshold 0.25 — intentionally permissive). Finally, Claude Sonnet classifies each paper into relevance tiers (primary, methodological, peripheral, not_relevant) with domain tags, subfield tags, and a 2-3 sentence annotation. Papers classified as not_relevant are excluded. Cost is ~$0.003/paper via the Anthropic Batch API.

### Author matching and school classification
BU authorship is verified against a faculty roster of 5,896 entries scraped from 24+ BU department web pages, with OpenAlex author IDs resolved for ~4,500 faculty. School tags are assigned via a 4-tier strategy: (1) OpenAlex author ID matching against the roster (zero false positives), (2) affiliation text regex against 60+ school/department patterns, (3) full-name roster matching with an OAID-mismatch guard and common-name blocklist, (4) alt-names cache from 98K OpenAlex author profiles. Faculty with dual appointments (e.g., Computing & Data Sciences + Questrom) are tagged to both schools.

## Limitations and known issues

**Coverage gaps.** Fields with established preprint and open-access traditions (CS, physics, medicine) are well-represented. Fields that primarily publish in proprietary law reviews, book chapters, or non-indexed venues are underrepresented. Conference workshop papers without DOIs are frequently missing from OpenAlex.

**Author disambiguation.** OpenAlex sometimes merges different people under a single author ID, especially for common names. We maintain a blocklist for known false matches and have cleared 19+ wrong IDs from the roster, but more may exist. The roster resolution script (`resolve_openalex_ids.py`) uses last-name matching as a fallback, which is too permissive for common names.

**Unspecified school tags.** ~3,100 papers (30%) are tagged "Boston University (unspecified)" because the BU author is not in our faculty roster, or the roster entry lacks a school assignment. This primarily affects interdisciplinary researchers, postdocs, and visiting scholars.

**Roster composition.** Some school rosters include administrative staff and adjuncts alongside research faculty (notably College of Fine Arts at 305 entries). This inflates faculty counts and deflates the papers-per-faculty ratio for those schools.

**Source overlap.** Papers frequently appear in multiple sources. The source count table below reflects total mentions, not unique papers per source.

## Auto-updates

The bibliography stays current via scheduled GitHub Actions:

| Schedule | Script | What it does |
|----------|--------|-------------|
| **Weekly** (Sunday 3am) | `update_weekly.py` | Harvests new papers from 4 sources, deduplicates, filters, classifies via Sonnet, verifies BU affiliation, merges, regenerates web app, pushes. |
| **Monthly** (1st of month) | `update_monthly.py` | Everything weekly does, plus: wider harvest window (12 months), citation refresh, preprint tracking, broken URL detection, roster refresh, domain trends, Scholarly Commons harvest. |
| **Quarterly** | `quarterly_review.py` | Read-only diagnostic: faculty gaps, random sample for review, year-over-year trends, cross-school collaboration. |

Cost controls: $5/weekly cap, $10/monthly cap, paper count gates, running total in `data/update_state.json`.

## Source coverage

| Source | Mentions | What it catches | Affiliation filter |
|--------|----------|----------------|-------------------|
| **OpenAlex** | 15,446 | Primary source, 250M+ works | BU ROR ID (exact) |
| **OpenBU** | 1,470 | Theses, dissertations, tech reports | Native (all BU) |
| **PubMed** | 1,425 | Biomedical AI work | Affiliation field |
| **NIH Reporter** | 349 | Federal grants | Organization name |
| **Semantic Scholar** | 223 | CS/ML papers | Text search |
| **SSRN** | 179 | Law/policy/business working papers | Faculty name search |
| **Scholarly Commons** | 139 | BU Law faculty scholarship | Native (BU Law) |
| **CrossRef** | 90 | Journal articles catch-all | Text search |
| **NSF Awards** | 13 | NSF-funded AI research | Awardee name |
| **bioRxiv/medRxiv** | 7 | Biomedical preprints | CrossRef DOI prefix |

Papers often appear in multiple sources — total mentions exceed the 10,456 deduplicated paper count.

## Setup

```bash
git clone https://github.com/marc-woernle/bu-ai-bibliography.git
cd bu-ai-bibliography
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
```

### Running updates
```bash
python update_weekly.py --dry-run     # Preview
python update_weekly.py               # Full weekly update
python update_monthly.py              # Monthly deep update
python validate_dataset.py            # Ground truth validation
```

## File structure

```
bu-ai-bibliography/
│
├── Core Pipeline
├── config.py                  # Constants: ROR ID, keywords, rate limits
├── utils.py                   # Deduplication, rate limiter, paper record factory
├── update_pipeline.py         # Shared pipeline functions
├── update_weekly.py           # Weekly auto-update orchestrator
├── update_monthly.py          # Monthly deep update + reports
├── quarterly_review.py        # Quarterly diagnostic
│
├── Sources
├── source_openalex.py         # OpenAlex (primary)
├── source_pubmed.py           # PubMed (biomedical)
├── source_ssrn.py             # SSRN via CrossRef
├── source_crossref.py         # CrossRef (journal articles)
├── source_arxiv.py            # arXiv (CS/ML preprints)
├── source_scholarly_commons.py  # BU Law Scholarly Commons
├── source_openbu.py           # OpenBU institutional repository
├── source_semantic_scholar.py # Semantic Scholar (optional)
├── source_in_progress.py      # NIH Reporter + NSF Awards
│
├── Classification & Output
├── classify_papers.py         # Sonnet batch classification
├── school_mapper.py           # 4-tier OAID-first school classifier
├── generate_data_js.py        # Master JSON → compact data.js
├── validate_dataset.py        # Ground truth validation
│
├── Data
├── data/
│   ├── sonnet_classification_bu_verified.json  # Master dataset (10,456 papers)
│   ├── bu_faculty_roster_verified.json         # Faculty roster (5,896 entries)
│   ├── openalex_bu_authors_cache.json          # 98K OpenAlex author profiles (local)
│   ├── update_state.json                       # Auto-update state tracking
│   └── update_log.csv                          # Run history
│
├── Web App
├── output/bibliography_app/
│   ├── index.html             # Interactive bibliography
│   ├── data.js                # Paper data (compact)
│   └── data_private.js        # Paper data with abstracts (not committed)
├── docs/                      # GitHub Pages deployment
│
├── One-Time Utilities
├── build_faculty_roster.py    # Scrape BU department pages for faculty
├── resolve_openalex_ids.py    # Match faculty → OpenAlex author IDs
├── backfill_author_oaids.py   # Backfill OpenAlex author IDs onto papers
├── normalize_author_names.py  # Normalize author names to roster canonical forms
├── enrich_unspecified_roster.py  # Resolve unspecified roster entries via OpenAlex affiliations
├── harvest_bulk_openalex.py   # One-query bulk historical backfill
│
├── CLAUDE.md                  # Project guide for Claude Code sessions
├── STATUS.md                  # Current project state
└── README.md
```

## Built with

Classification via **Claude Sonnet 4 Batch API**. Embedding pre-filter via **sentence-transformers** (`all-MiniLM-L6-v2`). Development assisted by [Claude Code](https://claude.ai/code).
