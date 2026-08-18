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

Relevance tiers — choose the strictest tier that fits:

- "primary": AI/ML is the MAIN subject or contribution. The paper builds,
  evaluates, theorizes about, or critiques an AI/ML model, algorithm, or
  system AS the core scholarly contribution. The work belongs in an AI/ML
  publication venue.
  Example: "DGM: A deep learning algorithm for solving partial differential
  equations" — a new ML algorithm IS the contribution.
  Example: "Attention Is All You Need" — defines a new architecture.

- "methodological": AI/ML is used as a research TOOL but the paper's central
  contribution is in another domain (medicine, biology, economics, social
  science, etc.). The model isn't novel; it's a means to a non-AI end. The
  paper would belong in a domain venue (a medical journal, an economics
  journal, etc.), not an AI venue.
  Example: "Whole Brain Segmentation" — uses neural-network-based image
  analysis to advance neuroscience, not to advance computer vision.
  Example: "Deep learning for breast cancer screening" — applies known CNNs
  to a clinical question.

- "peripheral": The paper is ABOUT AI's place in the world — its policy,
  regulation, law, ethics, economics, social impact, governance,
  philosophical or sociotechnical critique — but does NOT itself build,
  train, or apply an AI/ML model. This is legitimate scholarship on AI in
  another disciplinary lens. Most law-and-AI, ethics-of-AI, AI-policy,
  AI-economics work lands here.
  Example: "Artificial Intelligence and Jobs: Evidence from Online
  Vacancies" — economics paper on labor-market effects of AI adoption.
  Example: "Algorithmic Discrimination and Health Equity" — law/policy
  analysis of harms from medical AI.

- "not_relevant": AI has no meaningful connection to this paper — e.g. it
  merely mentions "algorithm" in a generic mathematical context, discusses
  biological neural systems with no connection to artificial neural
  networks, or was tagged with AI concepts by a database but is actually
  about an unrelated topic.

Boundary heuristics:
- Building/improving a model/algorithm = primary. Using an existing model
  to study something else = methodological. Talking about AI without
  running any = peripheral.
- A paper "wholly about AI" by topic does NOT make it primary. A law
  review article entirely about AI regulation is peripheral, not primary,
  because it doesn't build AI.
- Err on the side of inclusion: if in doubt between not_relevant and
  peripheral, choose peripheral.

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


# ── Response validation ──────────────────────────────────────────────────────
# The prompt fixes these vocabularies but nothing ever checked the response
# against them. Measured in the current master: 387 papers carry an off-list
# domain (25 distinct values, mostly subfield names leaking into the domain
# slot), 411 carry an off-list subfield (113 distinct, including case-variant
# duplicates like "Pattern Recognition" vs "Pattern recognition" that fragment
# the site's facets), and 7 have confidence: "high" -- a string where the schema
# says 0.0-1.0, which is what makes quarterly_review.py crash on sort.

VALID_TIERS = {"primary", "methodological", "peripheral", "not_relevant"}

VALID_DOMAINS = {
    "Medicine & Health", "Computer Science", "Engineering", "Law & Policy",
    "Business & Economics", "Social Sciences", "Natural Sciences",
    "Mathematics & Statistics", "Education", "Humanities", "Communication & Media",
    "Public Health", "Neuroscience", "Biology", "Physics", "Chemistry",
    "Environmental Science", "Psychology",
}

VALID_STATUSES = {
    "published", "preprint", "working_paper", "conference_paper",
    "book_chapter", "thesis", "dissertation", "technical_report",
    "grant", "other",
}


def _coerce_confidence(v):
    """Confidence must be a float. The model sometimes answers 'high'."""
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return {"high": 0.9, "medium": 0.6, "low": 0.3}.get(v.strip().lower(), 0.5)
    return 0.5


def _normalize_vocab(values, allowed=None):
    """Trim, title-case-normalize and de-duplicate a list of tags.

    Off-list values are kept rather than dropped -- they are often meaningful,
    just not in the enum -- but case variants are collapsed so the site's facets
    stop fragmenting. `allowed` maps a lowercased form back to its canonical
    spelling.
    """
    if not isinstance(values, list):
        return []
    canon = {a.lower(): a for a in (allowed or set())}
    out, seen = [], set()
    for v in values:
        if not isinstance(v, str):
            continue
        v = " ".join(v.split())
        if not v:
            continue
        v = canon.get(v.lower(), v)
        if v.lower() not in seen:
            seen.add(v.lower())
            out.append(v)
    return out


def validate_classification(parsed: dict) -> tuple[dict, list[str]]:
    """Coerce a parsed model response into schema. Returns (parsed, warnings)."""
    warnings = []
    tier = parsed.get("ai_relevance")
    if tier not in VALID_TIERS:
        warnings.append(f"off-list ai_relevance: {tier!r}")
        parsed["ai_relevance"] = "parse_error"
    parsed["confidence"] = _coerce_confidence(parsed.get("confidence"))
    parsed["domains"] = _normalize_vocab(parsed.get("domains"), VALID_DOMAINS)
    parsed["subfields"] = _normalize_vocab(parsed.get("subfields"))
    for d in parsed["domains"]:
        if d not in VALID_DOMAINS:
            warnings.append(f"off-list domain: {d!r}")
    if parsed.get("publication_status") not in VALID_STATUSES:
        parsed["publication_status"] = "other"
    return parsed, warnings


def _content_key(paper: dict) -> str:
    """Short stable hash of a paper's identity, embedded in the batch custom_id
    so collect can prove a result landed on the paper it was built from."""
    import hashlib
    basis = (paper.get("doi") or paper.get("title") or "").strip().lower()
    return hashlib.md5(basis.encode("utf-8")).hexdigest()[:10]


def _system_block():
    """System prompt as a cached block.

    The prompt is ~1,230 tokens and was re-sent uncached on every request --
    about 85% of the input tokens for a paper. Caching it roughly halves
    classification cost (a 5,000-paper batch goes from ~$17.70 to ~$9).
    """
    return [{
        "type": "text",
        "text": SYSTEM_PROMPT,
        "cache_control": {"type": "ephemeral"},
    }]


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

            # custom_id carries a content key, not just a position. The join on
            # collect was purely positional against a regenerable, gitignored
            # input file -- if that file changed between submit and collect (a
            # re-run of estimate, a CI --save-candidates dispatch, a batch left
            # for a day), every classification would land on the wrong paper
            # with nothing detecting it, because each result record copies its
            # title and DOI from papers[idx] and stays internally consistent.
            req = {
                "custom_id": f"p_{i}_{_content_key(paper)}",
                "params": {
                    "model": MODEL,
                    "max_tokens": 512,
                    "temperature": 0.0,
                    "system": _system_block(),
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

    mismatches = []
    vocab_warnings = 0

    print("Downloading results...")
    for result in client.messages.batches.results(batch_id):
        custom_id = result.custom_id
        parts = custom_id.split("_")
        idx = int(parts[1])
        paper = papers[idx]

        # Prove the positional join still holds. Old batches (custom_id carried
        # the source tag, not a content hash) simply skip this check.
        if len(parts) > 2 and len(parts[2]) == 10:
            if parts[2] != _content_key(paper):
                mismatches.append(custom_id)
                continue

        if result.result.type == "succeeded":
            msg = result.result.message
            text = msg.content[0].text if msg.content else "{}"
            parsed = None
            try:
                # Fence stripping used to sit outside the try. A response like
                # ```json{...}``` with no newline made split("\n", 1) return one
                # element -> IndexError -> the exception escaped the download
                # loop, and since RESULTS_FILE is only written after the loop,
                # a single malformed response destroyed the entire collected
                # batch.
                clean = text.strip()
                if clean.startswith("```"):
                    clean = clean.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
                parsed = json.loads(clean)
            except Exception:
                parsed = None

            if parsed is None:
                # The old fallback labelled a parse failure "peripheral" with
                # confidence 0.5 and put 300 chars of raw model output in the
                # annotation field, then dropped the _parse_error flag when
                # building the record -- so merge_batch_results' parse-error
                # filter could never fire and these shipped to the public site
                # as AI Studies papers. The tier is now a sentinel that the
                # merge step refuses.
                parsed = {
                    "ai_relevance": "parse_error",
                    "confidence": 0.0,
                    "domains": [], "subfields": [],
                    "annotation": "",
                    "one_line_summary": "",
                    "_parse_error": True,
                    "_raw_response": text[:500],
                }
                errors += 1
            else:
                parsed, warns = validate_classification(parsed)
                vocab_warnings += len(warns)

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
                # Persisted, unlike before: the fallback set _parse_error but the
                # record builder copied only named keys, so the flag never
                # reached disk and the downstream filter was dead code.
                "_parse_error": parsed.get("_parse_error", False),
                "_raw_response": parsed.get("_raw_response"),
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
                # Was "unknown", which merge_batch_results happily let through
                # (it only filters not_relevant), so an expired or errored
                # request became a master record the web app renders with a raw
                # "unknown" badge, missing from every relevance count and
                # unreachable by any filter -- and permanently un-reharvestable,
                # since its DOI is now in the dedup index.
                "ai_relevance": "api_error",
                "_parse_error": True,
                "bu_category": paper_err.get("bu_category", ""),
                "bu_schools": paper_err.get("bu_schools", []),
                **derived_fields(paper_err),
                "error": str(result.result),
            }
            errors += 1

    output = sorted(results.values(), key=lambda x: x["index"])

    # Nothing used to check that every submitted paper came back. A batch that
    # expires at 24h with requests still processing just produced a shorter
    # results file, and those papers ended up in no index anywhere -- not master,
    # not rejected, not non-BU. Simply gone from the run.
    missing = sorted(set(range(len(papers))) - set(results))
    if missing:
        missing_path = RESULTS_FILE.replace(".json", "_missing.json")
        with open(missing_path, "w") as f:
            json.dump([{"index": i,
                        "title": (papers[i].get("title") or "")[:200],
                        "doi": papers[i].get("doi")} for i in missing],
                      f, ensure_ascii=False, indent=2)
        print(f"\n  WARNING: {len(missing)} of {len(papers)} submitted papers "
              f"returned no result. Written to {missing_path}")
    if mismatches:
        print(f"  WARNING: {len(mismatches)} results skipped -- custom_id content "
              f"key did not match the input file. The input changed between "
              f"submit and collect; re-submit rather than merging these.")
    if vocab_warnings:
        print(f"  {vocab_warnings} off-vocabulary values normalized")

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
    # Optional --input=PATH flag lets the batch CLI target a specific candidates file
    # instead of the default ai_prefiltered_27k.json. The corresponding batch file,
    # batch-id file, and results file are derived from the input stem so you can run
    # parallel batches without clobbering each other.
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a.split("=", 1)[0][2:]: a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--") and "=" in a}
    if "input" in flags:
        INPUT_FILE = flags["input"]
        stem = Path(INPUT_FILE).stem
        BATCH_FILE = f"data/{stem}_batch_requests.jsonl"
        BATCH_ID_FILE = f"data/{stem}_batch_id.txt"
        RESULTS_FILE = f"data/{stem}_results.json"

    if not args or args[0] not in COMMANDS:
        print(f"Usage: python classify_papers.py <command> [--input=PATH]")
        print(f"Commands: {', '.join(COMMANDS.keys())}")
        sys.exit(1)

    COMMANDS[args[0]]()
