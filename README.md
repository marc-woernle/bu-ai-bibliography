# BU AI Bibliography

A multi-source pipeline for harvesting, deduplicating, classifying, and annotating **all AI-related academic publications by Boston University faculty**. Currently **10,456 papers** across 24 schools and departments, classified by Claude Sonnet via the Batch API.

**Live site:** [marc-woernle.github.io/bu-ai-bibliography](https://marc-woernle.github.io/bu-ai-bibliography/)

## How it works

```
Sources (OpenAlex, PubMed, SSRN, Scholarly Commons, OpenBU, NIH, NSF, arXiv, CrossRef, bioRxiv)
  → harvest by BU ROR ID + keyword/concept filters
  → dedup by DOI + title fingerprint
  → keyword pre-filter (187 AI terms in title/abstract)
  → embedding pre-filter (sentence-transformers semantic similarity)
  → Claude Sonnet classification (relevance tier, domains, subfields, annotation)
  → BU author verification (5,893-entry faculty roster with OpenAlex IDs)
  → school/department classification (4-tier OAID-first matching)
  → merge into master dataset
  → generate static web app
  → validate against ground truth anchors
  → push to GitHub Pages
```

## Auto-Updates

The bibliography stays current via scheduled GitHub Actions:

| Schedule | Script | What it does |
|----------|--------|-------------|
| **Weekly** (Sunday 3am) | `update_weekly.py` | Harvests new papers from 4 sources, deduplicates, filters, classifies via Sonnet, verifies BU affiliation, merges, regenerates web app, pushes. |
| **Monthly** (1st of month) | `update_monthly.py` | Everything weekly does, plus: wider harvest window (12 months), citation refresh, preprint tracking, broken URL detection, roster refresh, domain trends, Scholarly Commons harvest. |
| **Quarterly** | `quarterly_review.py` | Read-only diagnostic: faculty gaps, random sample for review, year-over-year trends, cross-school collaboration. |

Cost controls: $5/weekly cap, $10/monthly cap, paper count gates, running total in `data/update_state.json`.

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

## Source Coverage

| Source | Papers | What it catches | Affiliation filter |
|--------|--------|----------------|-------------------|
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

Note: papers often appear in multiple sources — total source mentions exceed the 10,456 deduplicated paper count.

## Faculty Roster

The pipeline uses a **5,893-entry faculty roster** (`data/bu_faculty_roster_verified.json`) with OpenAlex author IDs for 4,492 faculty. Used for:

- **School classification** via 4-tier OAID-first matching in `school_mapper.py`:
  1. OpenAlex author ID → roster (zero false positives)
  2. Affiliation text → regex patterns (BU-affiliated authors only)
  3. Full-name → roster (with OAID-mismatch guard + common-name blocklist)
  4. Alt-names cache → roster (unambiguous matches from 98K OpenAlex profiles)
- BU author verification
- New faculty candidate detection

Built by scraping 24+ BU department web pages (`build_faculty_roster.py`) and resolving OpenAlex IDs (`resolve_openalex_ids.py`).

## File Structure

```
bu-ai-bibliography/
│
├── ── Core Pipeline ────────────────────────────
├── config.py               # Constants: ROR ID, keywords, rate limits
├── utils.py                # Deduplication, rate limiter, paper record factory
├── update_pipeline.py      # Shared pipeline functions
├── update_weekly.py        # Weekly auto-update orchestrator
├── update_monthly.py       # Monthly deep update + reports
├── quarterly_review.py     # Quarterly diagnostic
│
├── ── Sources ──────────────────────────────────
├── source_openalex.py      # OpenAlex (primary)
├── source_pubmed.py        # PubMed (biomedical)
├── source_ssrn.py          # SSRN via CrossRef
├── source_crossref.py      # CrossRef (journal articles)
├── source_arxiv.py         # arXiv (CS/ML preprints)
├── source_scholarly_commons.py  # BU Law Scholarly Commons
├── source_openbu.py        # OpenBU institutional repository
├── source_semantic_scholar.py   # Semantic Scholar (optional)
├── source_in_progress.py   # NIH Reporter + NSF Awards
│
├── ── Classification & Output ──────────────────
├── classify_papers.py      # Sonnet batch classification (prompt, API)
├── school_mapper.py        # 4-tier OAID-first school classifier
├── generate_data_js.py     # Master JSON → compact data.js
├── validate_dataset.py     # Ground truth validation
│
├── ── Data ─────────────────────────────────────
├── data/
│   ├── sonnet_classification_bu_verified.json  # Master dataset (10,456 papers)
│   ├── bu_faculty_roster_verified.json         # Faculty roster (5,893 entries)
│   ├── openalex_bu_authors_cache.json          # 98K OpenAlex author profiles (local)
│   ├── update_state.json                       # Auto-update state
│   └── update_log.csv                          # Run history
│
├── ── Web App ──────────────────────────────────
├── output/bibliography_app/
│   ├── index.html          # Interactive bibliography
│   ├── data.js             # Paper data (compact)
│   └── data_private.js     # Paper data (with abstracts, not committed)
├── docs/                   # GitHub Pages deployment
│
├── ── One-Time Utilities ───────────────────────
├── build_faculty_roster.py     # Scrape BU department pages for faculty
├── resolve_openalex_ids.py     # Match faculty → OpenAlex author IDs
├── backfill_author_oaids.py    # Backfill OpenAlex author IDs onto papers
├── normalize_author_names.py   # Normalize author names to roster canonical forms
├── harvest_bulk_openalex.py    # One-query bulk historical backfill
│
├── CLAUDE.md               # Project guide for Claude Code sessions
├── STATUS.md               # Current project state (snapshot, not log)
└── README.md
```

## Classification

Each paper is classified by Claude Sonnet with:
- **Relevance tier:** primary, methodological, peripheral, or not_relevant
- **Domains:** from 18 fixed categories (Computer Science, Law & Regulation, Medicine & Health, etc.)
- **Subfields:** from 29 fixed categories (Machine Learning, NLP, Computer Vision, etc.)
- **Annotation:** 2-3 sentence scholarly description
- **Publication status:** peer-reviewed, preprint, working paper, etc.

Cost: ~$0.003/paper via the Anthropic Batch API.

## Built With

Classification via **Claude Sonnet 4 Batch API**. Embedding pre-filter via **sentence-transformers** (`all-MiniLM-L6-v2`). Development assisted by [Claude Code](https://claude.ai/code).
