#!/usr/bin/env python3
"""
Classify the faculty harvest candidates using Sonnet Batch API.

Reads candidates from data/faculty_harvest_candidates.json (or data/bulk_harvest_candidates.json),
builds a Sonnet batch, submits it, and merges results back.

Usage:
    python classify_harvest.py build      # Build batch JSONL + cost estimate
    python classify_harvest.py submit     # Submit batch to Anthropic
    python classify_harvest.py status     # Check batch status
    python classify_harvest.py collect    # Download results and update master
"""

import json
import sys
import os
import re
import logging
from pathlib import Path

logger = logging.getLogger("classify_harvest")

# Reuse the existing classification infrastructure
from classify_papers import (
    MODEL, SYSTEM_PROMPT, paper_to_prompt_text, estimate_tokens,
    PRICE_INPUT, PRICE_OUTPUT,
)

CANDIDATES_PATH = Path("data/faculty_harvest_candidates.json")
BATCH_FILE = Path("data/harvest_batch_requests.jsonl")
BATCH_ID_FILE = Path("data/harvest_batch_id.txt")
MASTER_PATH = Path("data/sonnet_classification_bu_verified.json")


def load_candidates():
    """Load candidates that need Sonnet classification."""
    # Check both possible files
    for path in [CANDIDATES_PATH, Path("data/bulk_harvest_candidates.json")]:
        if path.exists():
            with open(path) as f:
                papers = json.load(f)
            # Filter to those needing classification
            needs = [p for p in papers if p.get("_needs_sonnet") or not p.get("ai_relevance")]
            print(f"Loaded {len(papers)} from {path}, {len(needs)} need classification")
            return needs

    # Also check master for papers flagged _needs_sonnet
    with open(MASTER_PATH) as f:
        master = json.load(f)
    needs = [p for p in master if p.get("_needs_sonnet")]
    print(f"Found {len(needs)} papers in master flagged for Sonnet enrichment")
    return needs


def build_batch():
    """Build JSONL batch file."""
    papers = load_candidates()
    if not papers:
        print("No papers need classification")
        return

    total_input_tokens = 0
    system_tokens = estimate_tokens(SYSTEM_PROMPT)

    with open(BATCH_FILE, "w") as f:
        for i, paper in enumerate(papers):
            user_text = paper_to_prompt_text(paper)
            input_tokens = system_tokens + estimate_tokens(user_text)
            total_input_tokens += input_tokens

            request = {
                "custom_id": f"harvest_{i}",
                "params": {
                    "model": MODEL,
                    "max_tokens": 300,
                    "messages": [{"role": "user", "content": user_text}],
                    "system": SYSTEM_PROMPT,
                },
            }
            f.write(json.dumps(request) + "\n")

    output_tokens_est = len(papers) * 180  # ~180 output tokens per paper
    input_cost = total_input_tokens / 1_000_000 * PRICE_INPUT
    output_cost = output_tokens_est / 1_000_000 * PRICE_OUTPUT
    total_cost = input_cost + output_cost

    print(f"\nBatch built: {BATCH_FILE}")
    print(f"Papers: {len(papers)}")
    print(f"Est. input tokens: {total_input_tokens:,}")
    print(f"Est. output tokens: {output_tokens_est:,}")
    print(f"Est. cost: ${total_cost:.2f} (input: ${input_cost:.2f}, output: ${output_cost:.2f})")


def submit_batch():
    """Submit batch to Anthropic."""
    import anthropic

    if not BATCH_FILE.exists():
        print(f"No batch file at {BATCH_FILE}. Run 'build' first.")
        return

    client = anthropic.Anthropic()

    with open(BATCH_FILE) as f:
        requests_list = [json.loads(line) for line in f]

    print(f"Submitting {len(requests_list)} requests...")
    batch = client.messages.batches.create(requests=requests_list)
    print(f"Batch created: {batch.id}")
    print(f"Status: {batch.processing_status}")

    with open(BATCH_ID_FILE, "w") as f:
        f.write(batch.id)
    print(f"Batch ID saved to {BATCH_ID_FILE}")


def check_status():
    """Check batch status."""
    import anthropic

    if not BATCH_ID_FILE.exists():
        print("No batch ID found. Run 'submit' first.")
        return

    batch_id = BATCH_ID_FILE.read_text().strip()
    client = anthropic.Anthropic()
    batch = client.messages.batches.retrieve(batch_id)

    print(f"Batch: {batch_id}")
    print(f"Status: {batch.processing_status}")
    print(f"Counts: {batch.request_counts}")


def collect_results():
    """Download results and merge into master."""
    import anthropic

    if not BATCH_ID_FILE.exists():
        print("No batch ID found.")
        return

    batch_id = BATCH_ID_FILE.read_text().strip()
    client = anthropic.Anthropic()

    # Check status first
    batch = client.messages.batches.retrieve(batch_id)
    if batch.processing_status != "ended":
        print(f"Batch not done yet: {batch.processing_status}")
        return

    # Collect results
    results = {}
    for result in client.messages.batches.results(batch_id):
        custom_id = result.custom_id
        idx = int(custom_id.replace("harvest_", ""))

        if result.result.type == "succeeded":
            msg = result.result.message
            text = msg.content[0].text

            try:
                classification = json.loads(text)
                results[idx] = {
                    "classification": classification,
                    "input_tokens": msg.usage.input_tokens,
                    "output_tokens": msg.usage.output_tokens,
                }
            except json.JSONDecodeError:
                # Try to extract JSON from text
                match = re.search(r'\{.*\}', text, re.DOTALL)
                if match:
                    try:
                        classification = json.loads(match.group())
                        results[idx] = {"classification": classification}
                    except json.JSONDecodeError:
                        logger.warning(f"Could not parse result {idx}")

    print(f"Collected {len(results)} results")

    # Load master and update papers flagged _needs_sonnet
    with open(MASTER_PATH) as f:
        master = json.load(f)

    # Find papers that need enrichment
    needs_sonnet = [i for i, p in enumerate(master) if p.get("_needs_sonnet")]
    print(f"Papers in master needing enrichment: {len(needs_sonnet)}")

    updated = 0
    removed = 0
    for batch_idx, master_idx in enumerate(needs_sonnet):
        if batch_idx not in results:
            continue

        r = results[batch_idx]
        c = r["classification"]
        paper = master[master_idx]

        if c.get("ai_relevance") == "not_relevant":
            # Mark for removal
            paper["_remove"] = True
            removed += 1
        else:
            paper["ai_relevance"] = c.get("ai_relevance", paper.get("ai_relevance"))
            paper["confidence"] = c.get("confidence", 0.8)
            paper["publication_status"] = c.get("publication_status", "peer-reviewed article")
            paper["one_line_summary"] = c.get("one_line_summary", paper.get("one_line_summary", ""))
            paper["domains"] = c.get("domains", paper.get("domains", []))
            paper["subfields"] = c.get("subfields", paper.get("subfields", []))
            paper["annotation"] = c.get("annotation", paper.get("annotation", ""))
            if "input_tokens" in r:
                paper["input_tokens"] = r["input_tokens"]
                paper["output_tokens"] = r["output_tokens"]
            updated += 1

        # Remove the flag
        paper.pop("_needs_sonnet", None)

    # Remove not_relevant papers
    master = [p for p in master if not p.get("_remove")]

    # Re-index
    for i, p in enumerate(master):
        p["index"] = i
        p.pop("_remove", None)

    with open(MASTER_PATH, "w") as f:
        json.dump(master, f, indent=2)

    print(f"Updated: {updated} papers")
    print(f"Removed (not_relevant): {removed}")
    print(f"Master: {len(master)} papers")


def main():
    if len(sys.argv) < 2:
        print("Usage: python classify_harvest.py [build|submit|status|collect]")
        return

    cmd = sys.argv[1]
    if cmd == "build":
        build_batch()
    elif cmd == "submit":
        submit_batch()
    elif cmd == "status":
        check_status()
    elif cmd == "collect":
        collect_results()
    else:
        print(f"Unknown command: {cmd}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
