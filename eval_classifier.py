#!/usr/bin/env python3
"""Catch the classifier drifting, by re-deciding cases whose answer is already fixed.

What a gold set is, and is not
------------------------------
It is not a set of examples shown to the classifier -- the prompt already has
those. It is a frozen set of papers whose correct answer is recorded, re-run
after every change to the prompt, the keyword list, the reference texts or the
model, and compared against the record. Its job is to answer one question that
nothing in this repo could answer before: "did that change quietly make the
classification worse somewhere I was not looking?"

A useful gold set is not a pile of obvious AI papers. Obvious cases never move,
so they detect nothing. It needs three kinds of entry:

  * clear positives, so a change that breaks everything is caught immediately
  * clear negatives -- BU papers that are emphatically not AI -- because the
    failure this project is most exposed to is the filter widening until it
    admits the whole university
  * borderline cases, which is where all the information is. A law review
    article about AI regulation is AI Studies, not Core AI. A clinical trial
    that used a CNN is Applied AI. Those two boundaries are where the tiers
    actually get decided, and where a prompt edit does its damage.

Honesty about this seed set
---------------------------
The entries below are seeded from Sonnet's own high-confidence classifications
plus the rejection indexes. That makes this a DRIFT detector, not a correctness
oracle: it will tell you that an answer changed, and it cannot tell you that
the original answer was right, because Sonnet wrote both sides. Any entry
carrying "reviewed": false is provisional in exactly that way. Reviewing them
by hand is what converts this into a real gold set, and it is worth doing for
the borderline ones first -- they are only about a fifth of the file.

Usage:
  python eval_classifier.py --build      # regenerate the seed set from master
  python eval_classifier.py              # score the current classifier against it
"""
import argparse
import collections
import json
import os
import random
import sys

GOLD_PATH = "data/gold_set.json"
MASTER_PATH = "data/sonnet_classification_bu_verified.json"
N_PER_TIER = 60
N_NEGATIVE = 120


def _conf(p) -> str:
    """confidence is sometimes a float, sometimes the string 'high'."""
    c = p.get("confidence")
    if isinstance(c, str):
        return c.lower()
    if isinstance(c, (int, float)):
        return "high" if c >= 0.8 else "medium" if c >= 0.5 else "low"
    return "unknown"


def build():
    master = json.load(open(MASTER_PATH))
    random.seed(20260819)
    by_tier = collections.defaultdict(list)
    for p in master:
        by_tier[p.get("ai_relevance")].append(p)

    gold = []
    for tier, papers in by_tier.items():
        if not tier:
            continue
        confident = [p for p in papers if _conf(p) == "high"]
        pool = confident or papers
        for p in random.sample(pool, min(N_PER_TIER, len(pool))):
            gold.append({
                "title": p.get("title"),
                "doi": p.get("doi"),
                "year": p.get("year"),
                "abstract": (p.get("abstract") or "")[:4000],
                "expected_tier": tier,
                "expected_domains": p.get("domains") or [],
                # Borderline by construction: the two tiers whose boundary is
                # the one people actually argue about.
                "borderline": tier in ("methodological", "peripheral"),
                "reviewed": False,
                "note": "seeded from a high-confidence Sonnet label; not human-verified",
            })

    # Negatives. Papers the pipeline already decided are not AI. Without these
    # the eval cannot see the failure mode where the filter widens forever.
    neg = 0
    try:
        idx = json.load(open("data/rejected_papers_index.json"))
        for fp in list(idx.get("fingerprints", []))[:N_NEGATIVE]:
            gold.append({"title": None, "fingerprint": fp, "expected_tier": "not_relevant",
                         "borderline": False, "reviewed": False,
                         "note": "previously classified not_relevant; title not retained in the index"})
            neg += 1
    except FileNotFoundError:
        pass

    with open(GOLD_PATH, "w") as f:
        json.dump(gold, f, ensure_ascii=False, indent=1)
    tiers = collections.Counter(g["expected_tier"] for g in gold)
    print(f"wrote {GOLD_PATH}: {len(gold)} entries")
    for t, n in tiers.most_common():
        print(f"  {t:16} {n}")
    print(f"  borderline       {sum(1 for g in gold if g.get('borderline'))}")
    print(f"  human-reviewed   {sum(1 for g in gold if g.get('reviewed'))}  <- review these to make it an oracle")
    return 0


def evaluate():
    """Score the current classifier against the frozen answers.

    Deliberately uses the same Batch API path as production rather than a
    one-off synchronous call. An eval that exercises a different code path from
    the thing it is evaluating tests the wrong system, and the batch path is
    where the prompt caching, the vocabulary normalisation and the parse-error
    sentinel all live. 300 cases is about $0.90.

    Two runs:  --build once, then submit / collect like any other batch.
    """
    if not os.path.exists(GOLD_PATH):
        print(f"No {GOLD_PATH}. Run: python eval_classifier.py --build", file=sys.stderr)
        return 1
    gold = json.load(open(GOLD_PATH))
    cases = [g for g in gold if g.get("abstract")]
    results_path = "data/gold_set_results.json"

    if not os.path.exists(results_path):
        print(f"{len(cases)} gold cases with abstracts.\n")
        print("Classify them through the normal batch path, then re-run this:\n")
        print("  python - <<'EOF'")
        print("  import json")
        print(f"  g=[x for x in json.load(open('{GOLD_PATH}')) if x.get('abstract')]")
        print("  json.dump(g, open('data/gold_set.json.candidates','w'))")
        print("  EOF")
        print("  python classify_papers.py submit  --input=data/gold_set.json.candidates")
        print("  python classify_papers.py collect --input=data/gold_set.json.candidates")
        print(f"  mv data/gold_set.json.candidates_results.json {results_path}")
        print("  python eval_classifier.py")
        return 1

    got = {(_key(r)): r for r in json.load(open(results_path))}
    agree = dis = 0
    disagreements = []
    for g in cases:
        r = got.get(_key(g))
        if r is None:
            continue
        tier = r.get("ai_relevance")
        if tier == g["expected_tier"]:
            agree += 1
        else:
            dis += 1
            disagreements.append((g.get("title"), g["expected_tier"], tier, g.get("borderline")))

    scored = agree + dis
    if not scored:
        print("No overlap between the gold set and the results file.", file=sys.stderr)
        return 1
    print(f"agreement {agree}/{scored} ({agree/scored:.1%})")
    bl = [d for d in disagreements if d[3]]
    print(f"disagreements: {dis}  ({len(bl)} borderline, {dis - len(bl)} on clear cases)")
    print("\nClear-case disagreements are the ones that matter -- a change that moves")
    print("these moved something it should not have:")
    for title, exp, tier, borderline in disagreements:
        if not borderline:
            print(f"  expected {exp:16} got {str(tier):16} {(title or '')[:66]}")
    return 0


def _key(rec: dict) -> str:
    doi = (rec.get("doi") or "").strip().lower()
    if doi:
        return "doi:" + doi
    return "title:" + " ".join((rec.get("title") or "").lower().split())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    args = ap.parse_args()
    sys.exit(build() if args.build else evaluate())
