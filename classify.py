#!/usr/bin/env python3
"""
BU AI Bibliography — Stage 2: Claude-Powered Classification
=============================================================
Takes harvested papers and classifies each one using Claude API:
  1. Is AI a primary subject, a method/tool used, or not relevant?
  2. What domain(s) does it relate to?
  3. Generate a 2-3 sentence annotation.

Uses Anthropic Batch API for cost efficiency on large volumes.
For smaller batches (<100 papers), uses standard API.

Prerequisites:
    export ANTHROPIC_API_KEY=sk-ant-...

Usage:
    python classify.py data/bu_ai_bibliography_YYYYMMDD_HHMMSS.json
    python classify.py data/bu_ai_bibliography_*.json --batch   # Use Batch API
    python classify.py data/bu_ai_bibliography_*.json --sample 50  # Test on 50 papers
"""

import argparse
import json
import sys
import os
import time
import logging
from pathlib import Path
from datetime import datetime

logger = logging.getLogger("bu_bib.classify")

# Classification prompt template
CLASSIFY_PROMPT = """You are classifying academic papers for an annotated bibliography of AI-related research at Boston University. For each paper, analyze the title and abstract (if available) and provide:

1. **ai_relevance**: One of:
   - "primary" — AI/ML/NLP/CV/robotics IS the subject of the paper
   - "methodological" — AI/ML is used as a tool/method but the paper's subject is another field
   - "peripheral" — Tangentially related (mentions AI, uses basic statistical methods that could be called ML, etc.)
   - "not_relevant" — Not meaningfully related to AI

2. **confidence**: 0.0-1.0 how confident you are in the classification

3. **domains**: List of 1-3 primary domains from this list:
   Computer Science, Law & Regulation, Medicine & Health, Public Health & Epidemiology,
   Business & Economics, Engineering, Ethics & Philosophy, Political Science & Policy,
   Education, Natural Sciences, Social Sciences, Linguistics & NLP,
   Neuroscience & Cognitive Science, Environmental Science, Information Science,
   Robotics & Autonomous Systems, Cybersecurity & Privacy, Other

4. **ai_subfields**: If ai_relevance is "primary" or "methodological", list specific AI subfields:
   Machine Learning, Deep Learning, NLP, Computer Vision, Reinforcement Learning,
   Robotics, Knowledge Representation, Planning & Search, Multi-agent Systems,
   Speech & Audio, Generative AI, AI Safety & Alignment, AI Ethics & Fairness,
   Recommender Systems, Information Retrieval, Other

5. **annotation**: A 2-3 sentence scholarly annotation summarizing:
   - What the paper does/argues
   - Methodology or approach
   - Key contribution or finding

Respond ONLY with valid JSON. No markdown, no preamble.

Paper to classify:
Title: {title}
Abstract: {abstract}
Venue: {venue}
Year: {year}
Concepts: {concepts}
"""

RESPONSE_SCHEMA = {
    "ai_relevance": "primary|methodological|peripheral|not_relevant",
    "confidence": 0.0,
    "domains": [],
    "ai_subfields": [],
    "annotation": "",
}


def classify_single(paper: dict, api_key: str) -> dict:
    """Classify a single paper using the Anthropic API."""
    import requests

    prompt = CLASSIFY_PROMPT.format(
        title=paper.get("title", "Unknown"),
        abstract=paper.get("abstract", "No abstract available"),
        venue=paper.get("venue", "Unknown"),
        year=paper.get("year", "Unknown"),
        concepts=", ".join(paper.get("concepts", [])[:10]),
    )

    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 500,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    # Parse response
    text = data["content"][0]["text"]
    try:
        # Strip markdown code fences if present
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        text = text.strip()
        return json.loads(text)
    except json.JSONDecodeError:
        logger.warning(f"Failed to parse classification for: {paper.get('title', '?')}")
        return {"ai_relevance": "unknown", "confidence": 0, "annotation": text[:200]}


def classify_batch_standard(papers: list[dict], api_key: str) -> list[dict]:
    """Classify papers one at a time (for smaller batches)."""
    results = []
    total = len(papers)

    for i, paper in enumerate(papers):
        try:
            classification = classify_single(paper, api_key)
            paper["classification"] = classification
            results.append(paper)
            logger.info(
                f"  [{i+1}/{total}] {classification.get('ai_relevance', '?')}: "
                f"{paper.get('title', '?')[:60]}"
            )
        except Exception as e:
            logger.error(f"  [{i+1}/{total}] FAILED: {e}")
            paper["classification"] = {"ai_relevance": "error", "error": str(e)}
            results.append(paper)

        # Rate limiting
        time.sleep(0.5)

    return results


def prepare_batch_file(papers: list[dict], output_path: str) -> str:
    """Prepare a JSONL file for the Anthropic Batch API."""
    with open(output_path, 'w') as f:
        for i, paper in enumerate(papers):
            prompt = CLASSIFY_PROMPT.format(
                title=paper.get("title", "Unknown"),
                abstract=paper.get("abstract", "No abstract available"),
                venue=paper.get("venue", "Unknown"),
                year=paper.get("year", "Unknown"),
                concepts=", ".join(paper.get("concepts", [])[:10]),
            )

            batch_request = {
                "custom_id": f"paper_{i}",
                "params": {
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 500,
                    "messages": [{"role": "user", "content": prompt}],
                },
            }
            f.write(json.dumps(batch_request) + "\n")

    logger.info(f"Batch file prepared: {output_path} ({len(papers)} requests)")
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Classify harvested papers using Claude")
    parser.add_argument("input_file", help="JSON file from harvest stage")
    parser.add_argument("--batch", action="store_true", help="Prepare for Batch API")
    parser.add_argument("--sample", type=int, default=0, help="Classify only N papers (for testing)")
    parser.add_argument("--filter-relevant", action="store_true",
                        help="Output only papers classified as primary or methodological")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key and not args.batch:
        print("ERROR: Set ANTHROPIC_API_KEY environment variable")
        sys.exit(1)

    # Load papers
    with open(args.input_file) as f:
        papers = json.load(f)
    logger.info(f"Loaded {len(papers)} papers from {args.input_file}")

    if args.sample > 0:
        papers = papers[:args.sample]
        logger.info(f"Sampling {len(papers)} papers")

    if args.batch:
        # Just prepare the batch file
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        batch_path = f"data/batch_classify_{ts}.jsonl"
        prepare_batch_file(papers, batch_path)
        print(f"\nBatch file ready: {batch_path}")
        print(f"Submit via: anthropic batch create --file {batch_path}")
        return

    # Standard classification
    classified = classify_batch_standard(papers, api_key)

    # Stats
    relevance_counts = {}
    for p in classified:
        rel = p.get("classification", {}).get("ai_relevance", "unknown")
        relevance_counts[rel] = relevance_counts.get(rel, 0) + 1

    print(f"\nClassification Results:")
    for rel, count in sorted(relevance_counts.items()):
        print(f"  {rel}: {count}")

    # Filter if requested
    if args.filter_relevant:
        classified = [
            p for p in classified
            if p.get("classification", {}).get("ai_relevance") in ("primary", "methodological")
        ]
        logger.info(f"Filtered to {len(classified)} relevant papers")

    # Save
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = f"data/classified_{ts}.json"
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(classified, f, ensure_ascii=False, indent=2)
    print(f"Saved: {output_path}")


if __name__ == "__main__":
    main()
