# BU AI Bibliography — Project Guide

## What this is

A bibliography of every AI-related paper by Boston University faculty. 10,446 papers classified by Sonnet, displayed in a static web app at GitHub Pages. Auto-updated weekly/monthly via GitHub Actions.

## Key files — don't break these

- `data/sonnet_classification_bu_verified.json` — THE master dataset. 40MB. Everything flows from this.
- `data/bu_faculty_roster_verified.json` — 6,239 faculty with OpenAlex IDs. Loaded by school_mapper.py at import time.
- `output/bibliography_app/index.html` — the web app. Single-file HTML/JS/CSS. DO NOT regenerate or overwrite.
- `output/bibliography_app/data.js` / `data_private.js` — compact data for the web app. Regenerate with `python generate_data_js.py`.
- `docs/data.js` — GitHub Pages copy. Must stay in sync with output/bibliography_app/data.js.

## Data flow

```
Sources (OpenAlex, PubMed, SSRN, Scholarly Commons, OpenBU, NIH, NSF, bioRxiv)
  → harvest (update_pipeline.py)
  → dedup by DOI + title fingerprint
  → keyword pre-filter (AI_KEYWORDS in config.py)
  → embedding pre-filter (sentence-transformers, threshold 0.25)
  → Sonnet classification (classify_papers.py, batch API, ~$0.003/paper)
  → BU author verification (roster + affiliation check)
  → school classification (school_mapper.py from roster)
  → merge into master
  → generate_data_js.py → data.js files
  → validate_dataset.py → ground truth checks
  → git push
```

## Rules

### Before removing papers
Always back up first. Show what's being removed. Verify with multiple sources. Never bulk-remove based on a single signal. See the Lei Guo incident (17 legitimate papers removed because their name was stored in Cyrillic).

### Before making API calls
Calculate total calls needed. If >200, find a bulk endpoint. OpenAlex bulk: `filter=authorships.institutions.ror:https://ror.org/05qwgg493,concepts.id:C154945302|C119857082` with cursor pagination. Never loop over individual IDs. If rate limited, STOP — don't retry in a loop.

### BU author matching
- Papers with ≤30 authors: match against roster by full name or (last, first_initial)
- Papers with >30 authors (CERN-style): ONLY trust affiliation field containing "boston university". Name matching produces mass false positives.
- OpenBU papers: all authors get "Boston University" affiliation as a metadata bug. Verify per-paper.

### Name consistency
Author names must be identical in `authors[].name` and `bu_author_names[]`. The web app highlights by exact string match. Run the name consolidation (normalize accents, middle initials) after any merge.

### What NOT to commit
- API keys (ANTHROPIC_API_KEY, S2_API_KEY) — GitHub Secrets only
- `data/openalex_bu_authors_cache.json` (98K entries, 10MB) — local cache only
- `data/faculty_harvest_cache/` — local cache directory
- Backup files (`*.backup_*.json`)

## Known data issues
- 4,701 papers tagged "Boston University (unspecified)" — authors not in roster
- Tim Duncan (Law AI Program Director) has zero publications — he's a practitioner, not a researcher
- Bernard Chao incorrectly listed as BU in OpenAlex — actually University of Denver
- Economics has 41 papers for 59 faculty — SSRN/NBER source gap
- 389 papers have bu_category=UNCLASSIFIED — need school_mapper re-run

## Validation
Run `python validate_dataset.py` after any data change. 0 failures = safe to push. Warnings are informational. The anchor faculty list in that file defines who MUST have papers — if they show 0, something is broken.
