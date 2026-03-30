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
├── harvest.py              # Main orchestrator (runs all sources + school mapping)
├── classify.py             # Claude-powered AI relevance classifier
├── school_mapper.py        # School/department classifier (23 BU units)
├── format_output.py        # Output formatter (Markdown, BibTeX, stats)
├── gap_check.py            # Coverage gap analyzer + manual checklist
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
├── data/                   # Harvested data and checkpoints
│   ├── sonnet_classification_bu_verified.json  # Final classified dataset
│   ├── bu_faculty_roster.json                  # Faculty lookup table
│   └── harvest_stats_*.json                    # Run statistics
├── output/
│   └── bibliography_app/   # Interactive web bibliography
│       ├── index.html      # Single-file app (HTML + inline JS/CSS)
│       └── data.js         # Paper data (loaded via script tag)
├── docs/                   # GitHub Pages deployment (copy of bibliography_app)
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

# Edit config
nano config.py  # Set CONTACT_EMAIL to a .edu email for polite pool API access

# Test connections
python harvest.py --dry-run

# Full harvest (all 10 sources + school classification)
python harvest.py

# Classify with Claude
export ANTHROPIC_API_KEY=sk-ant-...
python classify.py data/bu_ai_bibliography_*.json --sample 20   # test
python classify.py data/bu_ai_bibliography_*.json               # full run

# Format output
python format_output.py data/classified_*.json

# Check for gaps
python gap_check.py data/bu_ai_bibliography_*.json
```

## Pipeline at a Glance

```
harvest.py          →  10 sources → deduplicated JSON (school-tagged)
classify.py         →  Claude labels each paper: AI relevance, domain, annotation
format_output.py    →  Markdown (grouped by school → domain), BibTeX, stats
gap_check.py        →  Flags missing faculty, suggests manual checks
school_mapper.py    →  (runs inside harvest.py, or standalone for re-classification)
```

## Extending

### Adding a new source
1. Create `source_newname.py` with a `harvest() -> list[dict]` function
2. Use `make_paper_record()` from `utils.py` for standardized output
3. Register in `SOURCE_REGISTRY` in `harvest.py`
4. Add to `DEFAULT_SOURCE_ORDER`

### Adding faculty to the school mapper
Edit `school_mapper.py` and add entries to the `_add_faculty()` calls:
```python
_add_faculty("New Professor Name", "School of Law", "LAW")
_add_faculty("Another Professor", "CAS — Computer Science", "NON-LAW")
```

### Adding affiliation patterns
Add regex patterns to `SCHOOL_PATTERNS` in `school_mapper.py` (more specific patterns first).

## Built With

This project was built with the help of [Claude Code](https://claude.ai/code). The classification of 27,000+ pre-filtered papers was done via the **Claude Sonnet 4 Batch API**, and **sentence-transformers** (`all-MiniLM-L6-v2`) were used for embedding-based pre-filtering to narrow 243K harvested papers down to 27K AI candidates.
