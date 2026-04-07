# BU AI Bibliography -- Status
**Updated:** 2026-04-07

## Numbers
- **Papers:** 11,870 in `data/sonnet_classification_bu_verified.json`
- **Roster:** 5,896 entries, 141 with school = unspecified
- **Validation:** 0 failures, 32 warnings (all genuine, non-actionable)
- **Web app:** live at marc-woernle.github.io/bu-ai-bibliography

## This session
- DBLP XML dump integration: 8,946 harvested, enriched via OpenAlex (7,263 got abstracts), 1,537 passed keyword + embedding + BU filters, 1,432 classified by Sonnet ($8.28), 1,372 merged after dedup (1,275 primary + 125 methodological, 32 dropped)
- New papers dominated by Engineering (754) and CS (555), plus Math/Stats (57), Physics (9), CDS (8), Medicine (8)
- Fixed prior session's API key issue (ANTHROPIC_API_KEY not in background shell env)

## TODO
1. Pipeline hardening: make monthly optional sources required (no silent failures), add CrossRef + DBLP to monthly, add quarterly_review.yml workflow, expand source_health tracking
2. End-to-end source coverage audit after pipeline hardening
3. Clean CFA roster (305 entries includes staff/artists, needs title field scraping)
4. 141 roster entries still "Boston University (unspecified)"

## Known issues
- ~3,110 papers tagged "Boston University (unspecified)", mostly authors not in roster
- OpenBU metadata bug: all authors get "Boston University" affiliation regardless
- Scholarly Commons uploads full back-catalog, no date filtering
- Some SPH roster entries are Harvard/BWH adjuncts with correct OAIDs but primarily publish under other affiliations
- Weekly pipeline covers 4 of 11 sources; monthly covers 10 but optional ones fail silently
- update_log.csv has only 1 row
- source_dblp.py exists but is untested (DBLP API was down 2026-04-07)
