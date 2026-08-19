#!/usr/bin/env python3
"""Ask OpenAlex directly for BU's AI work, instead of sweeping everything and filtering.

Why this exists
---------------
The pipeline's OpenAlex harvester pages every BU work and lets the pre-filter
decide. That is the right net for finding things OpenAlex has not classified,
but it is a bad way to find the things OpenAlex HAS classified: BU has 200,554
works all-time, and paging all of them to reach the AI ones is 1,003 requests
and a very large classification bill.

OpenAlex already tags works with concepts. Filtering server-side to the AI
concepts returns 18,475 BU works in 93 requests for under a cent.

Measured against the master dataset on 2026-08-19:

    18,475  BU works OpenAlex tags with an AI concept
     6,242  already in master (33.8%)
    12,233  never evaluated (66.2%), 4,896 of them published before 2010
     7,253  of those survive the pre-filter -> about $22 of Sonnet at batch rates

The concept tags are noisy in both directions -- they catch "On the
compactification of strongly pseudoconvex surfaces" and they miss plenty of
applied work -- so this does not replace the ROR sweep or the keyword sweep. It
is the cheapest high-yield source we have, and it reaches back before the date
windows the other harvesters use, which is where most of the gap turned out to
be.
"""
import logging
import time

import requests

from config import BU_ROR_ID, CONTACT_EMAIL, openalex_headers
from source_openalex import _parse_work

logger = logging.getLogger(__name__)

# OpenAlex legacy concept IDs. Kept explicit rather than resolved by name at
# runtime: a name lookup is a metered request, and these IDs are stable.
# The newer "topics" taxonomy is a separate index, and OpenAlex will not OR
# across different filter keys (concepts.id:X|topics...:Y is a 400), so this is
# a second sweep unioned by OpenAlex work ID. Measured: 5,877 BU works carry the
# AI subfield, 3,082 of which the concept sweep does not return, and 2,630 of
# those are works we have never evaluated. It is noisier -- subfield 1702 also
# catches "Review of Particle Physics" -- but 30 requests for $0.003 is not a
# price worth optimising, and Sonnet is the gate.
AI_TOPIC_SUBFIELD = "subfields/1702"  # Artificial Intelligence

AI_CONCEPTS = {
    "C154945302": "Artificial intelligence",
    "C119857082": "Machine learning",
    "C108583219": "Deep learning",
    "C31972630": "Computer vision",
    "C204321447": "Natural language processing",
    "C50644808": "Artificial neural network",
}


def harvest(since_year: int | None = None, deadline: float | None = None,
            _partial: list | None = None, _registry: dict | None = None) -> list[dict]:
    """Every BU work OpenAlex tags with an AI concept.

    since_year is optional and deliberately so. The other harvesters window by
    date because they are expensive; this one is not, and most of what it finds
    that we do not already hold is old -- 4,896 of 12,233 predate 2010.
    """
    window = f",from_publication_date:{since_year}-01-01" if since_year else ""
    filters = [
        f"institutions.ror:{BU_ROR_ID},concepts.id:{'|'.join(AI_CONCEPTS)}{window}",
        f"institutions.ror:{BU_ROR_ID},topics.subfield.id:{AI_TOPIC_SUBFIELD}{window}",
    ]
    papers, seen = [], set()
    for filt in filters:
        papers.extend(p for p in _sweep(filt, deadline, _partial, _registry, seen))
    logger.info(f"  openalex_concepts: {len(papers):,} distinct works across both taxonomies")
    return papers


def _sweep(filt, deadline, _partial, _registry, seen: set) -> list[dict]:
    papers, cursor, calls, cost, retries, fetched = [], "*", 0, 0.0, 0, 0
    expected = None
    while cursor:
        if deadline and time.time() > deadline:
            logger.warning(f"  openalex_concepts: deadline hit after {len(papers):,} works")
            break
        try:
            r = requests.get(
                "https://api.openalex.org/works",
                params={"filter": filt, "per_page": 200, "cursor": cursor,
                        "mailto": CONTACT_EMAIL},
                headers=openalex_headers(), timeout=60,
            )
        except requests.RequestException as e:
            retries += 1
            if retries > 5:
                logger.error(f"  openalex_concepts: giving up after {e}")
                break
            time.sleep(min(5 * 2 ** (retries - 1), 60))
            continue

        if r.status_code == 429 and "Insufficient budget" in r.text:
            logger.error(
                "  openalex_concepts: OpenAlex daily budget exhausted. Set "
                "OPENALEX_API_KEY (free, $1/day vs $0.10/day keyless)."
            )
            break
        if r.status_code == 429 or r.status_code >= 500:
            retries += 1
            if retries > 5:
                logger.error(f"  openalex_concepts: giving up after {r.status_code}")
                break
            time.sleep(min(5 * 2 ** (retries - 1), 60))
            continue
        if r.status_code != 200:
            logger.error(f"  openalex_concepts: HTTP {r.status_code}, stopping")
            break

        retries = 0
        data = r.json()
        calls += 1
        cost += data.get("meta", {}).get("cost_usd") or 0
        if expected is None:
            expected = data.get("meta", {}).get("count")
            logger.info(f"  openalex_concepts: {expected:,} BU works carry an AI concept")

        results = data.get("results", [])
        if not results:
            break
        fetched += len(results)
        page = []
        for w in results:
            try:
                if w.get("id") in seen:
                    continue
                seen.add(w.get("id"))
                p = _parse_work(w)
                if p:
                    p["source"] = "openalex"
                    papers.append(p)
                    page.append(p)
                    # Stream per record: a hard cutoff can land mid-page.
                    if _partial is not None:
                        _partial.append(p)
            except Exception as e:
                logger.debug(f"parse error: {e}")
        if _registry is not None and page:
            try:
                from update_pipeline import fold_papers_into_bu_registry
                fold_papers_into_bu_registry(page, _registry)
            except Exception as e:
                logger.debug(f"registry fold failed: {e}")

        cursor = data.get("meta", {}).get("next_cursor")

    logger.info(
        f"  openalex_concepts: {fetched:,} works seen, {len(papers):,} new, "
        f"{calls} requests, ${cost:.4f}"
        + (f" (OpenAlex reported {expected:,})" if expected else "")
    )
    # `fetched` counts what came back; `papers` counts what was new. The second
    # sweep legitimately returns far fewer papers than expected because most of
    # its results were already seen, so truncation is judged on fetched.
    if expected and fetched < expected * 0.9:
        logger.warning(
            f"  openalex_concepts: TRUNCATED -- saw {fetched:,} of {expected:,}"
        )
    return papers


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    got = harvest()
    print(f"{len(got):,} works")
