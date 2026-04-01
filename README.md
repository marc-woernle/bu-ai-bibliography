# BU AI Bibliography

A comprehensive, multi-source pipeline for harvesting, deduplicating, classifying, and annotating **all AI-related academic publications by Boston University faculty**. Currently **10,446 papers** across 25 schools and departments, classified by Claude Sonnet via the Batch API.

**Live site:** [marc-woernle.github.io/bu-ai-bibliography](https://marc-woernle.github.io/bu-ai-bibliography/)

## How it works

```
Sources (OpenAlex, PubMed, SSRN, Scholarly Commons, OpenBU, NIH, NSF, bioRxiv)
  → harvest by BU ROR ID (exact institutional match)
  → dedup by DOI + title fingerprint
  → keyword pre-filter (AI terms in title/abstract)
  → embedding pre-filter (sentence-transformers semantic similarity)
  → Claude Sonnet classification (relevance tier, domains, subfields, annotation)
  → BU author verification (6,239-entry faculty roster with OpenAlex IDs)
  → school/department classification (25 BU schools)
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
| **OpenAlex** | 9,162 | Primary source, 250M+ works | BU ROR ID (exact) |
| **OpenBU** | 812 | Theses, dissertations, tech reports | Native (all BU) |
| **Semantic Scholar** | 190 | CS/ML papers | Text search |
| **SSRN** | 140 | Law/policy/business working papers | Faculty name search |
| **Scholarly Commons** | 137 | BU Law faculty scholarship | Native (BU Law) |
| **PubMed** | 134 | Biomedical AI work | Affiliation field |
| **NIH Reporter** | 104 | Federal grants | Organization name |
| **CrossRef** | 46 | Journal articles catch-all | Text search |
| **NSF Awards** | 13 | NSF-funded AI research | Awardee name |
| **bioRxiv/medRxiv** | 6 | Biomedical preprints | CrossRef DOI prefix |

## Faculty Roster

The pipeline uses a **6,239-entry faculty roster** (`data/bu_faculty_roster_verified.json`) with OpenAlex author IDs for 4,885 faculty. This replaces the original hand-curated 75-name lookup. The roster is loaded by `school_mapper.py` at import time and used for:

- BU author verification (ID matching first, name matching fallback)
- School/department classification
- New faculty candidate detection

Built by scraping 24+ BU department web pages (`build_faculty_roster.py`) and resolving OpenAlex IDs (`resolve_openalex_ids.py`).

## File Structure

```
bu-ai-bibliography/
│
├── ── Core Pipeline ────────────────────────────
├── config.py               # Constants: ROR ID, keywords, rate limits
├── utils.py                # Deduplication, rate limiter, paper record factory
├── update_pipeline.py      # Shared pipeline functions (1200 lines)
├── update_weekly.py        # Weekly auto-update orchestrator
├── update_monthly.py       # Monthly deep update + reports
├── quarterly_review.py     # Quarterly diagnostic
│
├── ── Sources ──────────────────────────────────
├── source_openalex.py      # OpenAlex (primary, 87% of papers)
├── source_pubmed.py        # PubMed (biomedical)
├── source_ssrn.py          # SSRN via CrossRef
├── source_scholarly_commons.py  # BU Law Scholarly Commons
├── source_openbu.py        # OpenBU institutional repository
├── source_in_progress.py   # NIH Reporter + NSF Awards
│
├── ── Classification & Output ──────────────────
├── classify_papers.py      # Sonnet batch classification (prompt, API)
├── school_mapper.py        # School/dept classifier (loads from roster)
├── generate_data_js.py     # Master JSON → compact data.js
├── validate_dataset.py     # Ground truth validation (anchor faculty, consistency)
├── harvest_bulk_openalex.py  # One-query bulk historical backfill
│
├── ── Data ─────────────────────────────────────
├── data/
│   ├── sonnet_classification_bu_verified.json  # Master dataset (10,446 papers)
│   ├── bu_faculty_roster_verified.json         # Faculty roster (6,239 entries)
│   ├── bu_authors_from_openalex.json           # 82K BU author names
│   ├── update_state.json                       # Auto-update state
│   └── update_log.csv                          # Run history
│
├── ── Web App ──────────────────────────────────
├── output/bibliography_app/
│   ├── index.html          # Interactive bibliography (public)
│   ├── index_private.html  # Private version (with abstracts)
│   ├── data.js             # Paper data (compact, no abstracts)
│   └── data_private.js     # Paper data (with abstracts)
├── docs/                   # GitHub Pages deployment
│
├── ── One-Time Utilities ───────────────────────
├── build_faculty_roster.py   # Scrape BU department pages for faculty
├── resolve_openalex_ids.py   # Match faculty → OpenAlex author IDs
├── classify_harvest.py       # Sonnet batch for harvest candidates
├── audit_law_papers.py       # Law faculty paper audit
│
├── CLAUDE.md               # Project guide for Claude Code sessions
└── README.md
```

## Built With

Built with [Claude Code](https://claude.ai/code). Classification via **Claude Sonnet 4 Batch API** (~$0.003/paper). Embedding pre-filter via **sentence-transformers** (`all-MiniLM-L6-v2`).
