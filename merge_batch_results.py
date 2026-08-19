"""Merge Sonnet Batch-API classification results into the master dataset.

Takes the output of `classify_papers.py collect --input=PATH` and:
1. Records rejected papers (ai_relevance=not_relevant) to the rejection index
2. Verifies BU authorship on the kept papers
3. Maps schools for verified papers
4. Merges into master, saves, and regenerates data.js

Does NOT commit or push. Review with `git diff --stat` then commit manually.

Usage:
    python merge_batch_results.py --input=data/backlog_candidates_results.json
    python merge_batch_results.py --input=... --dry-run
"""

import argparse
import json
import logging
import sys
from pathlib import Path

from update_pipeline import (
    load_master,
    merge_into_master,
    record_non_bu_ai,
    record_rejections,
    regenerate_all_outputs,
    save_master,
    verify_bu_authors,
)
from school_mapper import classify_all

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("bu_bib.merge_batch")


def load_batch_results(path: str) -> list[dict]:
    """classify_papers.py collect saves a dict keyed by index; flatten to a list."""
    with open(path) as f:
        data = json.load(f)
    if isinstance(data, dict):
        return list(data.values())
    return data


def backfill_missing_authors(papers: list[dict]) -> int:
    """Re-fetch authorships for records that arrived without any.

    verify_bu_authors decides BU authorship from the author list. A record with
    an empty author list is not "not BU" -- it is unanswerable -- but the
    function has no way to say so, and everything unanswerable is filed as
    non-BU. That index is a permanent block: once a DOI is in it the harvester
    skips the paper forever.

    This is not hypothetical. The first backfill merge ran against candidate
    records built from a title/abstract/DOI projection, with authors stripped to
    keep the file under an upload size limit. All 1,833 AI-relevant papers came
    back with zero BU authors and were filed as non-BU -- papers that had been
    selected by a filter on BU's own ROR in the first place. After re-fetching
    authorships, 1,433 of them carry a ROR-verified BU author.

    So: never hand an empty author list to the verifier. Fifty DOIs per request,
    about $0.004 for a batch this size.
    """
    missing = [p for p in papers if not p.get("authors") and p.get("doi")]
    if not missing:
        return 0
    logger.warning(
        f"{len(missing)} of {len(papers)} records have no authors. Re-fetching "
        f"from OpenAlex before verification -- an empty author list is a missing "
        f"answer, and verify_bu_authors would read it as 'not BU'."
    )
    import requests
    from config import CONTACT_EMAIL, openalex_headers
    from source_openalex import _parse_work
    from utils import normalize_doi

    by_doi, headers = {}, openalex_headers()
    dois = [normalize_doi(p["doi"]) for p in missing]
    for i in range(0, len(dois), 50):
        chunk = [d for d in dois[i:i + 50] if d]
        if not chunk:
            continue
        try:
            r = requests.get(
                "https://api.openalex.org/works",
                params={"filter": "doi:" + "|".join(chunk), "per_page": 50,
                        "mailto": CONTACT_EMAIL},
                headers=headers, timeout=60,
            )
            if r.status_code != 200:
                logger.warning(f"  author backfill: HTTP {r.status_code}, stopping")
                break
            for w in r.json().get("results", []):
                parsed = _parse_work(w)
                if parsed and parsed.get("doi"):
                    by_doi[normalize_doi(parsed["doi"])] = parsed
        except Exception as e:
            logger.warning(f"  author backfill request failed: {e}")
            break

    filled = 0
    for p in missing:
        src = by_doi.get(normalize_doi(p["doi"]))
        if not src:
            continue
        p["authors"] = src.get("authors") or []
        p["venue"] = p.get("venue") or src.get("venue")
        if src.get("citation_count") is not None:
            p["citation_count"] = src["citation_count"]
        filled += 1
    logger.info(
        f"  author backfill: filled {filled} of {len(missing)}; "
        f"{sum(1 for p in missing if any(a.get('is_bu') for a in (p.get('authors') or [])))}"
        f" now carry a BU-affiliated author"
    )
    return filled


def main():
    parser = argparse.ArgumentParser(
        description="Merge batch classification results into master"
    )
    parser.add_argument(
        "--input", required=True,
        help="Path to batch results JSON (from classify_papers.py collect)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would happen without writing"
    )
    args = parser.parse_args()

    if not Path(args.input).exists():
        logger.error(f"Input file not found: {args.input}")
        sys.exit(1)

    papers = load_batch_results(args.input)
    logger.info(f"Loaded {len(papers)} batch results from {args.input}")

    # Quarantine anything whose tier is not one of the four real ones. Parse
    # failures and API errors now carry sentinel tiers instead of masquerading
    # as "peripheral" or "unknown"; they must never reach master, because once
    # a paper is in master its DOI is in the dedup index and it can never be
    # re-harvested or re-classified.
    VALID = {"primary", "methodological", "peripheral", "not_relevant"}
    quarantined = [p for p in papers if p.get("ai_relevance") not in VALID]
    if quarantined:
        qpath = Path(args.input).with_name(Path(args.input).stem + "_quarantined.json")
        qpath.write_text(json.dumps(quarantined, ensure_ascii=False, indent=2))
        logger.warning(
            f"{len(quarantined)} papers quarantined (parse error / API error / "
            f"off-list tier), written to {qpath}. Re-classify these; they are "
            f"NOT in master and NOT in any index."
        )
        papers = [p for p in papers if p.get("ai_relevance") in VALID]

    rejected = [p for p in papers if p.get("ai_relevance") == "not_relevant"]
    kept = [p for p in papers if p.get("ai_relevance") != "not_relevant"]
    logger.info(f"  Rejected (not_relevant): {len(rejected)}")
    logger.info(f"  Kept:                    {len(kept)}")

    backfill_missing_authors(kept)

    verified = verify_bu_authors(kept)
    logger.info(f"  BU-verified:             {len(verified)}")

    verified_keys = {id(p) for p in verified}
    non_bu = [p for p in kept if id(p) not in verified_keys]
    logger.info(f"  Non-BU (AI but not BU): {len(non_bu)}")

    if verified:
        classify_all(verified)

    if args.dry_run:
        logger.info("DRY RUN, no changes written")
        return

    if rejected:
        record_rejections(rejected)
        logger.info(f"Recorded {len(rejected)} rejections to rejection index")

    if non_bu:
        record_non_bu_ai(non_bu)
        logger.info(f"Recorded {len(non_bu)} non-BU AI papers to non-BU index")

    if verified:
        master = load_master()
        old_count = len(master)
        master = merge_into_master(master, verified)
        save_master(master)
        logger.info(f"Master: {old_count} -> {len(master)} papers (+{len(verified)})")

        regenerate_all_outputs()
        logger.info("Regenerated data.js")

    logger.info("Done. Review with `git diff --stat`, then commit.")


if __name__ == "__main__":
    main()
