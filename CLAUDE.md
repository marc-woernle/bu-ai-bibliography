> Always read STATUS.md at the start of every session for current project state.
> When asked to "update status", rewrite STATUS.md following the format below.

## Git commits
- Never add Co-Authored-By trailers or any attribution to Claude/Anthropic in commit messages.
- All commits must be authored by the user (Marc Woernle), never by Claude or any AI identity.

## STATUS.md format

STATUS.md is a **snapshot of now**, not a log. Each update **replaces** the whole file. Git history keeps the old versions.

When writing STATUS.md, follow this structure:

```
# BU AI Bibliography — Status
**Updated:** YYYY-MM-DD

## Numbers
Papers, roster entries, key metrics. Actual counts, not approximations.

## This session
What changed this session. Brief bullets — not a narrative. Include concrete numbers (e.g. "added 34 papers", not "added papers").

## TODO
Open items, ordered by priority. Each item = one line. Remove items that are done. If something was partially done, update the item to reflect remaining work only.

## Known issues
Persistent data quality issues and quirks that future sessions need to know about. Not bugs to fix — things to be aware of.
```

Rules:
- **Replace, don't append.** Every field reflects current state. If a TODO is done, delete it. If a number changed, update it.
- **No stale references.** Don't leave "change X to Y" after Y is done. Don't describe the old state.
- **Timestamp every update.** The date at the top is when STATUS.md was last rewritten.
- **Keep it short.** If it's longer than ~60 lines, cut. Details belong in CLAUDE.md or git history, not here.

# BU AI Bibliography — Project Guide

## What this is

A bibliography of every AI-related paper by Boston University faculty. ~12,000 papers classified by Sonnet, displayed in a static web app at GitHub Pages. Auto-updated monthly via GitHub Actions.

## Key files — don't break these

- `data/sonnet_classification_bu_verified.json` — THE master dataset. 40MB. Everything flows from this.
- `data/bu_faculty_roster_verified.json` — 5,893 faculty (4,492 with OpenAlex IDs). Loaded by school_mapper.py at import time.
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
- Papers with ≤30 authors: 4-tier OAID-first matching (see school_mapper.py). No initial matching.
- Papers with >30 authors (CERN-style): Tier 1 (OAID) + Tier 2 (affiliation regex) only. No name matching.
- OpenBU papers: all authors get "Boston University" affiliation as a metadata bug. Verify per-paper.

### Name consistency
Author names must be identical in `authors[].name` and `bu_author_names[]`. The web app highlights by exact string match. Run the name consolidation (normalize accents, middle initials) after any merge.

### What NOT to commit
- API keys (ANTHROPIC_API_KEY, S2_API_KEY) — GitHub Secrets only
- `data/openalex_bu_authors_cache.json` (98K entries, 10MB) — local cache only
- `data/faculty_harvest_cache/` — local cache directory
- Backup files (`*.backup_*.json`)

## Data source philosophy
OpenAlex is our primary source but it is NOT exhaustive or authoritative. It is one database among many — it has gaps, stale data, name collisions, and merged/split author profiles. Do not treat OpenAlex as ground truth. Cross-reference with other sources (Scholarly Commons, SSRN, PubMed, CrossRef, faculty CVs). When a paper is known to exist but isn't in OpenAlex, add it manually with source="manual". The same applies to every other source — none of them are complete.

## Known data issues
- ~3,900 papers tagged "Boston University (unspecified)" — authors not in roster or roster entry has school = unspecified
- 792 roster entries still have school = "Boston University (unspecified)" — main lever for reducing unspecified papers
- Tim Duncan (Law AI Program Director) has zero publications — he's a practitioner, not a researcher
- Bernard Chao: not BU (University of Denver), is_bu set to False
- Economics has 43 papers for 59 faculty — SSRN/NBER source gap
- Scholarly Commons uploads full back-catalog when faculty join BU — no date filtering (bu_start_year removed)
- ~5 false Dental tags from common-name full-name matching (Bing Liu, Claire Chang, etc.) — no OAID to catch mismatch

## Validation
Run `python validate_dataset.py` after any data change. 0 failures = safe to push. Warnings are informational. The anchor faculty list in that file defines who MUST have papers — if they show 0, something is broken.
