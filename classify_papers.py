#!/usr/bin/env python3
"""
Claude Sonnet classification pipeline for BU AI Bibliography.

Runs Sonnet directly on all pre-filtered papers for rich classification
(ai_relevance tier, domains, subfields, annotation). Papers marked
"not_relevant" are filtered out at the end.

Uses Anthropic Message Batches API for cost-efficient bulk processing.

Usage:
    python classify_papers.py estimate    # Show cost/token estimates, build batch file
    python classify_papers.py submit      # Submit batch to Anthropic
    python classify_papers.py status      # Check batch progress
    python classify_papers.py collect     # Download results, save JSON
"""

import json
import sys
import os
import logging
from pathlib import Path

import anthropic

logger = logging.getLogger("bu_bib.classify")

# ── Paths ────────────────────────────────────────────────────────────────────

INPUT_FILE = "data/ai_prefiltered_27k.json"
BATCH_FILE = "data/classify_batch_requests.jsonl"
BATCH_ID_FILE = "data/classify_batch_id.txt"
RESULTS_FILE = "data/sonnet_classification_results.json"

# ── Model & Pricing ─────────────────────────────────────────────────────────

MODEL = "claude-sonnet-4-6"
# Batch API = 50% of standard pricing
PRICE_INPUT = 1.50    # $/MTok
PRICE_OUTPUT = 7.50   # $/MTok

# ── Prompt ───────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are annotating papers for the Boston University AI Bibliography, a scholarly reference that catalogs BU-affiliated AI research.

Determine whether AI is meaningfully relevant to this paper in ANY of these ways:
(a) AI/ML is the primary subject or contribution
(b) AI/ML is used as a method or tool in the research
(c) The paper discusses AI's impact, regulation, ethics, or implications
(d) The paper proposes or evaluates an algorithm, model, or automated system that qualifies as AI/ML

Then classify and annotate the paper. Return ONLY valid JSON:
{
  "ai_relevance": "primary" | "methodological" | "peripheral" | "not_relevant",
  "confidence": 0.0-1.0,
  "publication_status": "peer-reviewed article" | "preprint" | "working paper" | "conference paper" | "thesis/dissertation" | "book/chapter" | "grant" | "review article" | "editorial" | "other",
  "one_line_summary": "Single sentence, max 20 words, describing what the paper is about.",
  "domains": ["1-3 from the fixed domain list below"],
  "subfields": ["1-4 from the fixed subfield list below"],
  "annotation": "2-3 sentence scholarly description of what the paper contributes and how AI is involved."
}

Domains — choose ONLY from this list:
"Computer Science", "Law & Regulation", "Medicine & Health", "Public Health & Epidemiology", "Business & Economics", "Engineering", "Ethics & Philosophy", "Political Science & Policy", "Education", "Natural Sciences", "Social Sciences", "Linguistics & NLP", "Neuroscience & Cognitive Science", "Environmental Science", "Information Science", "Robotics & Autonomous Systems", "Cybersecurity & Privacy", "Arts & Humanities"

Subfields — choose ONLY from this list:
"Machine Learning", "Deep Learning", "NLP", "Computer Vision", "Reinforcement Learning", "Robotics", "Knowledge Representation", "Planning & Search", "Multi-agent Systems", "Speech & Audio", "Generative AI", "AI Safety & Alignment", "AI Ethics & Fairness", "AI Governance & Regulation", "Recommender Systems", "Information Retrieval", "Federated Learning", "Explainable AI", "Optimization", "Signal Processing", "Bioinformatics & Computational Biology", "Medical Imaging", "Drug Discovery", "Autonomous Vehicles", "Data Mining", "Statistical Learning", "Bayesian Methods", "Graph Neural Networks", "Transformer Models"

Relevance tiers:
- "primary": AI/ML is the main subject or contribution of the paper
- "methodological": AI/ML is used as a tool/method but the paper's core contribution is in another domain
- "peripheral": Paper discusses AI implications, policy, or ethics without technical AI content
- "not_relevant": AI has no meaningful connection to this paper — e.g. it merely mentions "algorithm" in a generic mathematical context, or discusses biological neural systems with no connection to artificial neural networks, or was tagged with AI concepts by a database but is actually about an unrelated topic

Err on the side of inclusion: if in doubt between not_relevant and peripheral, choose peripheral.
For not_relevant papers, set domains and subfields to empty lists and annotation to a brief reason why it is not AI-related."""


def load_papers():
    with open(INPUT_FILE) as f:
        return json.load(f)


def derived_fields(paper: dict) -> dict:
    """Compute bu_author_names, best_url, and is_open_access from existing paper data."""
    # BU author names
    bu_names = [
        a.get("name", "")
        for a in paper.get("authors", [])
        if a.get("is_bu")
    ]

    # Best URL: prefer readable/OA versions over DOI (often paywalled)
    # Priority: pdf_url (OA) > non-DOI url (repos, arxiv) > DOI fallback
    doi = paper.get("doi")
    pdf_url = paper.get("pdf_url")
    url = paper.get("url")
    best_url = None
    if pdf_url:
        best_url = pdf_url
    elif url and "openalex.org" not in url:
        best_url = url
    elif doi:
        best_url = f"https://doi.org/{doi}"

    # Open access flag
    extra = paper.get("extra", {}) or {}
    is_oa = extra.get("is_oa")  # None if not present

    return {
        "bu_author_names": bu_names,
        "best_url": best_url,
        "is_open_access": is_oa,
    }


def paper_to_prompt_text(paper: dict) -> str:
    """Format a paper's metadata into a compact prompt string."""
    title = paper.get("title", "")
    abstract = paper.get("abstract", "") or ""
    if len(abstract) > 800:
        abstract = abstract[:800] + "..."
    concepts = paper.get("concepts", [])
    concept_str = ", ".join(
        str(c) for c in concepts[:8] if c and not isinstance(c, list)
    )
    venue = paper.get("venue", "") or ""
    pub_type = paper.get("publication_type", "") or ""
    year = paper.get("year", "") or ""

    parts = [f"Title: {title}"]
    if year:
        parts.append(f"Year: {year}")
    if venue:
        parts.append(f"Venue: {venue}")
    if pub_type:
        parts.append(f"Type: {pub_type}")
    if abstract:
        parts.append(f"Abstract: {abstract}")
    if concept_str:
        parts.append(f"Topics: {concept_str}")
    return "\n".join(parts)


def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token for English text."""
    return len(text) // 4


# ── Build batch ──────────────────────────────────────────────────────────────

def build_batch():
    """Build JSONL batch file for Sonnet classification."""
    papers = load_papers()
    total_input_tokens = 0
    system_tokens = estimate_tokens(SYSTEM_PROMPT)

    with open(BATCH_FILE, "w") as f:
        for i, paper in enumerate(papers):
            user_text = paper_to_prompt_text(paper)
            total_input_tokens += system_tokens + estimate_tokens(user_text)

            req = {
                "custom_id": f"p_{i}_{paper.get('source', 'unk')}",
                "params": {
                    "model": MODEL,
                    "max_tokens": 512,
                    "temperature": 0.0,
                    "system": SYSTEM_PROMPT,
                    "messages": [{"role": "user", "content": user_text}],
                },
            }
            f.write(json.dumps(req) + "\n")

    # ~175 tokens per response (JSON with annotation + publication_status + one_line_summary)
    est_output_tokens = len(papers) * 175

    return {
        "num_requests": len(papers),
        "input_tokens": total_input_tokens,
        "output_tokens": est_output_tokens,
    }


# ── Commands ─────────────────────────────────────────────────────────────────

def estimate():
    """Build batch file and show cost/token estimates."""
    print("Building batch file and estimating costs...\n")
    stats = build_batch()

    cost_in = stats["input_tokens"] / 1_000_000 * PRICE_INPUT
    cost_out = stats["output_tokens"] / 1_000_000 * PRICE_OUTPUT
    total = cost_in + cost_out

    print(f"Sonnet classification ({MODEL})")
    print(f"  Requests:       {stats['num_requests']:,}")
    print(f"  Input tokens:   {stats['input_tokens']:,} (~{stats['input_tokens']/1_000_000:.1f}M)")
    print(f"  Output tokens:  {stats['output_tokens']:,} (~{stats['output_tokens']/1_000_000:.1f}M)")
    print(f"  Batch file:     {BATCH_FILE}")
    print()
    print(f"  Estimated cost (batch pricing):")
    print(f"    Input:   ${cost_in:.2f}  ({stats['input_tokens']/1_000_000:.1f}M × ${PRICE_INPUT}/MTok)")
    print(f"    Output:  ${cost_out:.2f}  ({stats['output_tokens']/1_000_000:.1f}M × ${PRICE_OUTPUT}/MTok)")
    print(f"    {'─'*40}")
    print(f"    TOTAL:   ${total:.2f}")
    print()
    print(f"  Run 'python classify_papers.py submit' to start.")


def submit():
    """Submit batch to Anthropic API."""
    if not os.path.exists(BATCH_FILE):
        print("Batch file not found. Run 'estimate' first.")
        return

    client = anthropic.Anthropic()

    requests = []
    with open(BATCH_FILE) as f:
        for line in f:
            requests.append(json.loads(line))

    print(f"Submitting batch with {len(requests):,} requests...")
    batch = client.messages.batches.create(requests=requests)

    with open(BATCH_ID_FILE, "w") as f:
        f.write(batch.id)

    print(f"Batch submitted: {batch.id}")
    print(f"Status: {batch.processing_status}")
    print(f"\nRun 'python classify_papers.py status' to check progress.")


def status():
    """Check batch progress."""
    if not os.path.exists(BATCH_ID_FILE):
        print("No batch ID found. Run 'submit' first.")
        return

    client = anthropic.Anthropic()
    batch_id = Path(BATCH_ID_FILE).read_text().strip()
    batch = client.messages.batches.retrieve(batch_id)

    counts = batch.request_counts
    total = counts.processing + counts.succeeded + counts.errored + counts.canceled + counts.expired
    pct = counts.succeeded / total * 100 if total else 0

    print(f"Batch:      {batch.id}")
    print(f"Status:     {batch.processing_status}")
    print(f"Progress:   {counts.succeeded:,}/{total:,} ({pct:.1f}%)")
    print(f"  Succeeded:  {counts.succeeded:,}")
    print(f"  Processing: {counts.processing:,}")
    print(f"  Errored:    {counts.errored:,}")
    if counts.canceled:
        print(f"  Canceled:   {counts.canceled:,}")
    if counts.expired:
        print(f"  Expired:    {counts.expired:,}")

    if batch.processing_status == "ended":
        print(f"\nReady! Run 'python classify_papers.py collect' to download results.")


def collect():
    """Download results, merge with paper data, save."""
    if not os.path.exists(BATCH_ID_FILE):
        print("No batch ID found.")
        return

    client = anthropic.Anthropic()
    batch_id = Path(BATCH_ID_FILE).read_text().strip()

    batch = client.messages.batches.retrieve(batch_id)
    if batch.processing_status != "ended":
        print(f"Batch not done yet. Status: {batch.processing_status}")
        counts = batch.request_counts
        print(f"  Succeeded: {counts.succeeded:,}, Processing: {counts.processing:,}")
        return

    papers = load_papers()
    results = {}
    errors = 0

    print("Downloading results...")
    for result in client.messages.batches.results(batch_id):
        custom_id = result.custom_id
        idx = int(custom_id.split("_")[1])
        paper = papers[idx]

        if result.result.type == "succeeded":
            msg = result.result.message
            text = msg.content[0].text if msg.content else "{}"
            # Strip markdown fences if present
            clean = text.strip()
            if clean.startswith("```"):
                clean = clean.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            try:
                parsed = json.loads(clean)
            except json.JSONDecodeError:
                parsed = {
                    "ai_relevance": "peripheral",
                    "domains": [], "subfields": [],
                    "annotation": text[:300],
                    "_parse_error": True,
                }
                errors += 1

            results[idx] = {
                "index": idx,
                # ── Source paper fields ──
                "title": paper.get("title", ""),
                "authors": paper.get("authors", []),
                "year": paper.get("year"),
                "doi": paper.get("doi"),
                "venue": paper.get("venue"),
                "citation_count": paper.get("citation_count"),
                "abstract": paper.get("abstract"),
                "publication_type": paper.get("publication_type"),
                "source": paper.get("source", ""),
                "source_id": paper.get("source_id", ""),
                "all_sources": paper.get("all_sources", [paper.get("source", "")]),
                # ── Sonnet classification ──
                "ai_relevance": parsed.get("ai_relevance", "peripheral"),
                "confidence": parsed.get("confidence", 0.5),
                "publication_status": parsed.get("publication_status", "other"),
                "one_line_summary": parsed.get("one_line_summary", ""),
                "domains": parsed.get("domains", []),
                "subfields": parsed.get("subfields", []),
                "annotation": parsed.get("annotation", ""),
                # ── BU institutional ──
                "bu_category": paper.get("bu_category", ""),
                "bu_schools": paper.get("bu_schools", []),
                # ── Derived fields ──
                **derived_fields(paper),
                # ── API usage ──
                "input_tokens": msg.usage.input_tokens,
                "output_tokens": msg.usage.output_tokens,
            }
        else:
            paper_err = papers[idx]
            results[idx] = {
                "index": idx,
                "title": paper_err.get("title", ""),
                "authors": paper_err.get("authors", []),
                "year": paper_err.get("year"),
                "doi": paper_err.get("doi"),
                "venue": paper_err.get("venue"),
                "citation_count": paper_err.get("citation_count"),
                "abstract": paper_err.get("abstract"),
                "publication_type": paper_err.get("publication_type"),
                "source": paper_err.get("source", ""),
                "source_id": paper_err.get("source_id", ""),
                "all_sources": paper_err.get("all_sources", [paper_err.get("source", "")]),
                "ai_relevance": "unknown",
                "bu_category": paper_err.get("bu_category", ""),
                "bu_schools": paper_err.get("bu_schools", []),
                **derived_fields(paper_err),
                "error": str(result.result),
            }
            errors += 1

    output = sorted(results.values(), key=lambda x: x["index"])

    with open(RESULTS_FILE, "w") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # Stats
    tiers = {}
    for r in output:
        tier = r.get("ai_relevance", "unknown")
        tiers[tier] = tiers.get(tier, 0) + 1

    total_in = sum(r.get("input_tokens", 0) for r in output)
    total_out = sum(r.get("output_tokens", 0) for r in output)
    cost_in = total_in / 1_000_000 * PRICE_INPUT
    cost_out = total_out / 1_000_000 * PRICE_OUTPUT

    relevant = sum(v for k, v in tiers.items() if k != "not_relevant" and k != "unknown")

    print(f"\nClassification complete:")
    print(f"  Total papers:    {len(output):,}")
    print(f"  Errors:          {errors:,}")
    print()
    print(f"  By relevance tier:")
    for tier in ["primary", "methodological", "peripheral", "not_relevant", "unknown"]:
        if tier in tiers:
            print(f"    {tier:<20} {tiers[tier]:>6,}")
    print(f"    {'─'*30}")
    print(f"    {'AI-relevant total':<20} {relevant:>6,}")
    print()
    print(f"  Actual tokens:   {total_in:,} in / {total_out:,} out")
    print(f"  Actual cost:     ${cost_in + cost_out:.2f}")
    print(f"  Saved to:        {RESULTS_FILE}")


# ── CLI ──────────────────────────────────────────────────────────────────────

COMMANDS = {
    "estimate": estimate,
    "submit": submit,
    "status": status,
    "collect": collect,
}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(f"Usage: python classify_papers.py <command>")
        print(f"Commands: {', '.join(COMMANDS.keys())}")
        sys.exit(1)

    COMMANDS[sys.argv[1]]()
