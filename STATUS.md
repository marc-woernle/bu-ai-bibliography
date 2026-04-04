# BU AI Bibliography — Status
**Updated:** 2026-04-04

## Numbers
- **Papers:** 10,456 in `data/sonnet_classification_bu_verified.json`
- **Roster:** 5,893 entries, 4,492 with OpenAlex IDs, 792 with school = unspecified
- **School tags:** 0 UNCLASSIFIED, 0 false Dental tags, ~3,900 unspecified-only, ~6,540 with specific school
- **Dental papers:** 41
- **Economics papers:** 44
- **Law papers:** 196
- **Web app:** live at marc-woernle.github.io/bu-ai-bibliography
- **Validation:** 0 failures, 47 warnings

## This session
- Investigated underrepresented schools (Theology 0.03 P/F, Social Work 0.04, Pardee 0.05) — genuinely thin on AI, not harvesting failures
- Harvested 669 NBER papers via OpenAlex → 0 new AI-relevant (existing pipeline already captures them via published journal versions)
- Added 5 papers: Ireland CHCD dataset (Theology), Muroff 2016 ICIP + 2025 EUSIPCO (Social Work), 2 SSRN Econ/Questrom
- Fixed 6 false Dental tags via NAME_MATCH_BLOCKLIST in school_mapper.py (Bing Liu, Claire Chang, Li Liu, Andrew Miller, Rashi Sharma)
- Fixed Woodward (Pardee) roster — had wrong OpenAlex ID (a chemist). Cleared OAID.
- Fixed stale FACULTY_LOOKUP import in update_pipeline.py — replaced with FACULTY_BY_FULLNAME
- Updated CLAUDE.md (6 stale items fixed, added STATUS.md format spec)
- Updated README.md (counts, source table, file structure, school classification description)
- Updated GitHub repo description and homepage URL
- Added Scholarly Commons to web app footer source list

## TODO
1. Fix 792 unspecified roster entries — main lever for reducing ~3,900 unspecified papers
2. Handle dual appointments (e.g., Wildman is STH + CDS — currently only tagged CDS)
3. Add Woodward's RAND biometrics publications manually (no DOIs, not in OpenAlex)
4. Test auto-update pipeline end-to-end (`python update_weekly.py --dry-run`)
5. Investigate roster_coverage warnings — 47 faculty with many OpenAlex works but 0 papers

## Known issues
- ~3,900 papers tagged "Boston University (unspecified)" — authors not in roster or roster entry lacks school
- OpenBU metadata bug: all authors get "Boston University" affiliation regardless
- Tim Duncan (Law): practitioner, zero publications expected — not a gap
- Bernard Chao: not BU (Denver), is_bu=False
- Scholarly Commons uploads full back-catalog — no date filtering applied
- OpenAlex not exhaustive — must cross-reference with other sources
