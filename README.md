# BU AI Bibliography

A comprehensive, multi-source pipeline for harvesting, deduplicating, classifying, and annotating **all AI-related academic publications by Boston University faculty**. Includes an interactive web bibliography for browsing and searching the results.

**Live site:** [marc-woernle.github.io/bu-ai-bibliography](https://marc-woernle.github.io/bu-ai-bibliography/)

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                           STAGE 1: HARVEST                                  │
│                                                                             │
│  ┌──────────┐ ┌────────────┐ ┌────────┐ ┌───────┐ ┌──────┐ ┌──────┐       │
│  │ OpenAlex │ │ Semantic   │ │ PubMed │ │ arXiv │ │ SSRN │ │OpenBU│       │
│  │ (primary)│ │ Scholar    │ │ (biomed│ │ (pre- │ │(law/ │ │(inst │       │
│  │ concepts │ │ (CS/ML)    │ │  AI)   │ │ print)│ │policy│ │ repo)│       │
│  │+keywords │ │            │ │        │ │       │ │)     │ │      │       │
│  └────┬─────┘ └──────┬─────┘ └───┬────┘ └──┬────┘ └──┬───┘ └──┬───┘       │
│       │              │           │          │         │        │            │
│  ┌────┴──────────────┴───────────┴──────────┴─────────┴────────┘            │
│  │                                                                          │
│  │  ┌────────────┐ ┌────────────┐ ┌──────────────┐                         │
│  │  │ CrossRef   │ │ NIH        │ │ bioRxiv/     │                         │
│  │  │ (catch-all)│ │ Reporter   │ │ medRxiv      │                         │
│  │  │            │ │ + NSF      │ │ (preprints)  │                         │
│  │  │            │ │ (grants)   │ │              │                         │
│  │  └──────┬─────┘ └─────┬──────┘ └──────┬───────┘                         │
│  └─────────┴──────────────┴──────────────┘                                  │
│                        │                                                    │
│             ┌──────────▼──────────┐                                         │
│             │   Deduplication     │                                         │
│             │ (DOI + title hash)  │                                         │
│             └──────────┬──────────┘                                         │
│                        │                                                    │
│             ┌──────────▼──────────┐                                         │
│             │  School/Dept Mapper │                                         │
│             │  23 schools & depts │                                         │
│             │  + faculty lookup   │                                         │
│             └──────────┬──────────┘                                         │
└────────────────────────┼────────────────────────────────────────────────────┘
                         │
              ┌──────────▼───────────┐
              │   STAGE 2: CLASSIFY  │
              │  Claude Sonnet 4     │
              │  (Batch API)         │
              │                      │
              │  • AI relevance tier │
              │  • Domain tagging    │
              │  • Subfield tagging  │
              │  • 2-3 sent. annot.  │
              └──────────┬───────────┘
                         │
              ┌──────────▼───────────┐
              │   OUTPUT             │
              │  Interactive web app │
              │  (by school → domain)│
              │                      │
              │  • Searchable/filter │
              │  • Export CSV        │
              │  • Citation copy     │
              │  • Static HTML/JS    │
              └──────────────────────┘
```

## Quick Start

### 1. Configure
Edit `config.py` and set your email:
```python
CONTACT_EMAIL = "yourname@bu.edu"
```
This unlocks "polite pool" access for OpenAlex and CrossRef (faster rate limits). Different institutions may have different databases and different access emails.

### 2. Test connections
```bash
python harvest.py --dry-run
```

### 3. Run the full harvest
```bash
python harvest.py
```
This runs all 10 sources sequentially. Estimated time: 30-90 minutes depending on volume. Papers are automatically tagged with their BU school/department based on author affiliations.

### 4. Run specific sources
```bash
python harvest.py --sources openalex pubmed in_progress
```

### 5. Maximum recall mode
```bash
python harvest.py --comprehensive
```
Adds a sweep of ALL BU publications from OpenAlex (tens of thousands), for downstream Claude classification.

### 6. Classify with Claude
```bash
# Test on 20 papers first
export ANTHROPIC_API_KEY=sk-ant-...
python classify.py data/bu_ai_bibliography_*.json --sample 20

# Full classification
python classify.py data/bu_ai_bibliography_*.json

# Or prepare for Batch API (50% cheaper, recommended for large volumes)
python classify.py data/bu_ai_bibliography_*.json --batch
```

### 7. Format the annotated bibliography
```bash
python format_output.py data/classified_*.json
```

Output is organized by school/department, then by topic domain within each school.

## Source Coverage

| Source | What it catches | Affiliation filter | API type |
|--------|----------------|-------------------|----------|
| **OpenAlex** | 250M+ works, strongest coverage | BU ROR ID (exact) | REST, cursor pagination |
| **Semantic Scholar** | Strong CS/ML papers | "Boston University" text search | REST, offset pagination |
| **PubMed** | Biomedical AI work (BUSM, SPH) | Affiliation field | E-utilities XML |
| **arXiv** | CS/ML preprints, unpublished work | Full-text search | Atom/XML |
| **SSRN** | Law/policy working papers | CrossRef DOI prefix 10.2139 | CrossRef REST |
| **OpenBU** | Theses, dissertations, tech reports | Native (all BU) | DSpace 7 REST |
| **CrossRef** | Journal articles catch-all | "Boston University" text search | REST |
| **NIH Reporter** | Active/recent federal grants | Organization name | REST JSON |
| **NSF Awards** | NSF-funded AI research | Awardee name | REST JSON |
| **bioRxiv/medRxiv** | Biomedical preprints | CrossRef DOI prefix 10.1101 | CrossRef REST |

## School Classification

Every paper is automatically tagged with:
- **`bu_schools`**: List of specific BU schools/departments (e.g., "School of Law", "CAS — Computer Science", "College of Engineering")
- **`bu_category`**: Derived shorthand — `LAW`, `NON-LAW`, `BOTH`, or `UNCLASSIFIED`

The pipeline recognizes 23 schools, colleges, and research centers across BU. Classification uses a three-tier strategy:
1. **Affiliation pattern matching** — regex against known BU school/department names in author metadata
2. **Faculty name lookup** — cross-references 60+ known AI faculty against a department table
3. **BU fallback** — authors confirmed as BU but without a specific school match are tagged as "Boston University (unspecified)"

## What Won't Be Caught (and How to Fill Gaps)

The pipeline is designed for high recall, but some items may slip through:

1. **Faculty book chapters** — CrossRef sometimes indexes these, but coverage is spotty. Consider manually checking faculty CV pages.
2. **Conference presentations without proceedings** — No database consistently indexes these. Faculty CV pages are the best source.
3. **Very recent preprints** (<48h) — OpenAlex and S2 have indexing delays. Re-run the harvest periodically.
4. **Papers where BU affiliation is missing/malformed** — Some authors list lab names instead of university names. The Semantic Scholar and arXiv sweeps use text search to partially mitigate this.
5. **Non-English publications** — OpenAlex indexes many, but keyword matching is English-only.
6. **Google Scholar-only items** — Some working papers and reports are only indexed by Google Scholar. Not included in the automated pipeline due to ToS concerns — use manually for spot-checks.

## File Structure

```
bu-ai-bibliography/
│
├── ── Initial Harvest Pipeline ──────────────────
├── harvest.py              # Main orchestrator (all 10 sources + school mapping)
├── classify.py             # Claude batch classifier (for large initial runs)
├── classify_papers.py      # Classification prompts, schemas, API logic
├── school_mapper.py        # School/department classifier (23 BU units)
├── format_output.py        # Output formatter (Markdown, BibTeX, stats)
├── gap_check.py            # Coverage gap analyzer
├── merge_all.py            # Multi-source merge utility
├── config.py               # All configuration and constants
├── utils.py                # Shared utilities (dedup, persistence, etc.)
├── source_openalex.py      # OpenAlex harvester (primary)
├── source_semantic_scholar.py
├── source_pubmed.py
├── source_arxiv.py
├── source_ssrn.py
├── source_crossref.py
├── source_openbu.py
├── source_in_progress.py   # NIH Reporter, NSF Awards, bioRxiv/medRxiv
│
├── ── Auto-Update System ────────────────────────
├── update_pipeline.py      # Shared pipeline (harvest, filter, classify, merge, validate)
├── update_weekly.py        # Weekly incremental update (--dry-run, --force, --test)
├── update_monthly.py       # Monthly deep update + citation refresh + reports
├── quarterly_review.py     # Quarterly diagnostic report for human review
├── generate_data_js.py     # Master JSON → compact data.js for the web app
├── install_schedules.py    # Claude Code trigger setup
│
├── ── Data ──────────────────────────────────────
├── data/
│   ├── sonnet_classification_bu_verified.json  # Master dataset (all classified papers)
│   ├── bu_faculty_roster.json                  # Faculty lookup table
│   ├── bu_authors_from_openalex.json           # 82K+ BU author names
│   ├── update_state.json                       # Auto-update persistent state
│   └── update_log.csv                          # Run history log
│
├── ── Web App ───────────────────────────────────
├── output/bibliography_app/
│   ├── index.html          # Interactive bibliography (public)
│   ├── index_private.html  # Private version (with LinkedIn post generator)
│   ├── data.js             # Paper data (public, compact format)
│   └── data_private.js     # Paper data (with abstracts)
├── docs/                   # GitHub Pages (serves index.html + data.js)
│
├── ── Reports ───────────────────────────────────
├── output/monthly_report_*.md
├── output/quarterly_review_*.md
│
└── README.md
```

## Setup

Developed and tested on a Mac Mini M4 Pro, but should work on any machine with Python 3.10+.

```bash
git clone https://github.com/marc-woernle/bu-ai-bibliography.git
cd bu-ai-bibliography
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Environment variables
```bash
export ANTHROPIC_API_KEY=sk-ant-...   # Required for classification
export S2_API_KEY=...                  # Optional, for Semantic Scholar
```

### Running the initial harvest (already done — 10,329 papers)
```bash
python harvest.py                     # Harvest from all 10 sources
python classify.py data/*.json        # Classify via Claude Sonnet Batch API
python format_output.py data/*.json   # Generate formatted output
```

### Auto-updates (the main workflow going forward)
```bash
python update_weekly.py --dry-run     # Preview what would be harvested
python update_weekly.py               # Run the full weekly update
python update_monthly.py              # Monthly deep update + citation refresh
python quarterly_review.py            # Quarterly diagnostic report
```

## Auto-Update System

The bibliography stays current automatically via scheduled updates:

| Schedule | Script | What it does |
|----------|--------|-------------|
| **Weekly** (Sunday 3am) | `update_weekly.py` | Harvests new papers from OpenAlex, PubMed, bioRxiv, SSRN. Deduplicates, pre-filters (keyword + embedding), classifies via Sonnet, verifies BU affiliation, merges into master dataset, regenerates the web app, pushes to GitHub. |
| **Monthly** (1st of month) | `update_monthly.py` | Everything weekly does, plus: wider harvest window (12 months), citation count refresh, preprint-to-publication tracking, broken URL detection, BU author roster refresh, domain trend analysis, new faculty candidate detection. Generates a monthly report. |
| **Quarterly** (Jan/Apr/Jul/Oct) | `quarterly_review.py` | Read-only diagnostic: faculty gap check, random sample for human review, year-over-year trends, cross-school collaboration analysis, cost summary. |

Updates are orchestrated by **Claude Code scheduled triggers** — meaning Claude is in the loop to handle API changes, edge cases, and bugs as they arise, rather than a static cron job that silently fails.

### Cost controls
- Pre-classification cost estimate before any API call
- Hard caps: $5/weekly, $10/monthly
- Paper count gates: >100 weekly or >300 monthly triggers a pause
- Running total tracked in `data/update_state.json`

### Notifications
- Routine updates: git commit message only
- Alerts (zero papers for 3+ weeks, cost exceeded, source failures): GitHub Issue created automatically
- Monthly/quarterly reports: GitHub Issue with summary + link to full report

## Pipeline at a Glance

```
── Initial Harvest (done once) ──
harvest.py          →  10 sources → 243K papers → deduplicated, school-tagged
classify_papers.py  →  Sonnet Batch API → 27K pre-filtered → 10K+ verified AI papers

── Auto-Updates (ongoing) ──
update_weekly.py    →  Incremental harvest → dedup → filter → classify → merge → push
update_monthly.py   →  + citation refresh, preprint tracking, reports
quarterly_review.py →  Diagnostic report for human review
```

## Extending

### Adding a new source
1. Create `source_newname.py` with a `harvest() -> list[dict]` function
2. Use `make_paper_record()` from `utils.py` for standardized output
3. Register in `SOURCE_REGISTRY` in `harvest.py`

### Adding faculty
Edit `school_mapper.py` and add entries to the `_add_faculty()` calls. The quarterly review also detects new faculty candidates automatically.

### Porting to another university
The pipeline is parameterized via `config.py`. To adapt for a different institution:
1. Change `BU_ROR_ID`, `BU_GRID_ID`, `BU_OPENALEX_INSTITUTION_ID` in `config.py`
2. Update `SCHOOL_PATTERNS` and `FACULTY_LOOKUP` in `school_mapper.py`
3. Update `CONTACT_EMAIL` for API polite pool access
4. Run the initial harvest + classification pipeline

## Built With

This project was built with the help of [Claude Code](https://claude.ai/code). The classification of 27,000+ pre-filtered papers was done via the **Claude Sonnet 4 Batch API**, and **sentence-transformers** (`all-MiniLM-L6-v2`) were used for embedding-based pre-filtering to narrow 243K harvested papers down to 27K AI candidates.
