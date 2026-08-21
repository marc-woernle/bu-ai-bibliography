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
from datetime import datetime
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
# The system prompt is sent as a cached block, so after the first request it is
# billed at the cache-read rate rather than the input rate. Ignoring that is not
# a rounding error: the system prompt is ~1,230 of the ~1,366 average input
# tokens per request, so an uncached estimate overstates a title-heavy batch by
# well over 2x. The 9,465-paper backfill was quoted at ~$28 and actually cost
# $12.96.
PRICE_CACHE_READ = PRICE_INPUT * 0.1
PRICE_CACHE_WRITE = PRICE_INPUT * 1.25
# Ephemeral cache entries live ~5 minutes. A batch that takes hours re-writes
# the block many times; this is a deliberately pessimistic guess at how often,
# because being wrong in the direction of "cheaper than quoted" is the safe way
# to be wrong about someone else's money.
ASSUMED_CACHE_WRITES = 200

COST_HISTORY_PATH = "data/classification_cost_history.json"

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
    total_user_tokens = 0
    title_only_user_tokens = 0
    system_tokens = estimate_tokens(SYSTEM_PROMPT)

    with open(BATCH_FILE, "w") as f:
        for i, paper in enumerate(papers):
            user_text = paper_to_prompt_text(paper)
            this_user = estimate_tokens(user_text)
            total_user_tokens += this_user
            total_input_tokens += system_tokens + this_user
            if not (paper.get("abstract") or "").strip():
                title_only_user_tokens += this_user

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

    # A candidate with no abstract is not the same purchase as one with an
    # abstract. The pre-filter passes abstract-less papers through rather than
    # dropping what it cannot judge, which is the right call, but it means a
    # backlog batch can be majority bare titles -- and a title-only
    # classification is both cheaper per paper and much weaker evidence.
    # Splitting the estimate is the difference between "this costs $150" and
    # "$60 of this is Sonnet guessing from titles", which are different
    # decisions.
    n_title_only = sum(1 for p in papers if not (p.get("abstract") or "").strip())

    return {
        "num_requests": len(papers),
        "input_tokens": total_input_tokens,
        "output_tokens": est_output_tokens,
        "system_tokens": system_tokens,
        "user_tokens": total_user_tokens,
        "title_only": n_title_only,
        "with_abstract": len(papers) - n_title_only,
        "title_only_user_tokens": title_only_user_tokens,
    }


# ── Commands ─────────────────────────────────────────────────────────────────

def estimate():
    """Build batch file and show cost/token estimates."""
    print("Building batch file and estimating costs...\n")
    stats = build_batch()

    n = stats["num_requests"]
    sys_tok = stats["system_tokens"]
    user_tok = stats["user_tokens"]

    # Uncached: what the old estimator reported, kept as the ceiling.
    ceiling = ((sys_tok * n + user_tok) / 1_000_000 * PRICE_INPUT
               + stats["output_tokens"] / 1_000_000 * PRICE_OUTPUT)

    writes = min(ASSUMED_CACHE_WRITES, n)
    cost_cache_w = writes * sys_tok / 1_000_000 * PRICE_CACHE_WRITE
    cost_cache_r = max(n - writes, 0) * sys_tok / 1_000_000 * PRICE_CACHE_READ
    cost_user = user_tok / 1_000_000 * PRICE_INPUT
    cost_out = stats["output_tokens"] / 1_000_000 * PRICE_OUTPUT
    total = cost_cache_w + cost_cache_r + cost_user + cost_out

    print(f"Sonnet classification ({MODEL})")
    print(f"  Requests:       {n:,}")
    print(f"  System prompt:  {sys_tok:,} tokens, cached, sent once per request")
    print(f"  Paper text:     {user_tok:,} tokens total"
          f" (~{user_tok/max(n,1):.0f} per paper)")
    print(f"  Output tokens:  {stats['output_tokens']:,}"
          f" (~{stats['output_tokens']/1_000_000:.1f}M)")
    print(f"  Batch file:     {BATCH_FILE}")
    print()
    print(f"  Estimated cost (batch pricing, system prompt cached):")
    print(f"    Cache writes:  ${cost_cache_w:>8.2f}"
          f"   ({writes:,} assumed re-writes of the cached block)")
    print(f"    Cache reads:   ${cost_cache_r:>8.2f}")
    print(f"    Paper text:    ${cost_user:>8.2f}")
    print(f"    Output:        ${cost_out:>8.2f}"
          f"   ({cost_out/max(total, 0.01):.0%} of the bill)")
    print(f"    {'-'*46}")
    print(f"    TOTAL:         ${total:>8.2f}"
          f"   (${total/max(n,1):.4f} per paper)")
    print(f"    ceiling if caching does not work at all: ${ceiling:.2f}")
    print()

    # An estimate is a model. A previous run is a measurement. When both exist,
    # lead with the measurement.
    hist = _load_cost_history()
    if hist:
        last = hist[-1]
        rate = last["cost_per_paper"]
        print(f"  Measured, not modelled: the last completed batch"
              f" ({last['papers']:,} papers on {last['date']})")
        print(f"  cost ${last['cost']:.2f}, i.e. ${rate:.4f} per paper."
              f"  At that rate this batch is ${rate * n:.2f}.")
        print()

    n_title = stats.get("title_only", 0)
    if n_title:
        # Output is charged per response regardless of how much input produced
        # it, so a title-only paper costs nearly as much as one with an
        # abstract. The saving from dropping them is close to proportional.
        share_out = n_title / max(n, 1) * cost_out
        t_total = share_out + stats["title_only_user_tokens"] / 1_000_000 * PRICE_INPUT
        print(f"  Of that total, what you are buying:")
        print(f"    {stats['with_abstract']:>8,} papers with an abstract"
              f"   ${total - t_total:>8.2f}")
        print(f"    {n_title:>8,} papers with a title only"
              f"   ${t_total:>8.2f}   ({t_total/max(total, 0.01):.0%} of the bill)")
        print()
        print(f"  A title-only classification is a real judgement, not a coin")
        print(f"  flip, but it is the weakest evidence this pipeline produces.")
        print(f"  Dropping those requests saves ${t_total:.2f} and loses whatever")
        print(f"  share of {n_title:,} papers are AI-relevant with a title that")
        print(f"  does not say so. Nothing else in the pipeline finds them later.")
        print()

    print(f"  Run 'python classify_papers.py submit' to start.")


def _load_cost_history() -> list:
    if not os.path.exists(COST_HISTORY_PATH):
        return []
    try:
        with open(COST_HISTORY_PATH) as f:
            h = json.load(f)
        return h if isinstance(h, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _record_actual_cost(papers: int, cost: float):
    """Append what a finished batch actually cost.

    Estimates drift; a recorded price does not. Without this, every future
    estimate is a model argued from first principles against a bill nobody
    wrote down.
    """
    if papers <= 0:
        return
    hist = _load_cost_history()
    hist.append({
        "date": datetime.now().strftime("%Y-%m-%d"),
        "model": MODEL,
        "papers": papers,
        "cost": round(cost, 2),
        "cost_per_paper": round(cost / papers, 6),
    })
    os.makedirs(os.path.dirname(COST_HISTORY_PATH), exist_ok=True)
    with open(COST_HISTORY_PATH, "w") as f:
        json.dump(hist[-50:], f, indent=2)


def submit():
    """Submit batch to Anthropic API."""
    if not os.path.exists(BATCH_FILE):
        # sys.exit, not return. Returning meant the process exited 0 and every
        # caller -- CI included -- reported a green run that had submitted
        # nothing. A missing prerequisite is a failure and has to look like one.
        sys.exit(f"Batch file not found: {BATCH_FILE}. Run 'estimate' first.")

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
        sys.exit(f"No batch ID at {BATCH_ID_FILE}. Run 'submit' first.")

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
        sys.exit(f"No batch ID at {BATCH_ID_FILE}. Run 'submit' first.")

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
    total_cost = cost_in + cost_out
    print(f"  Actual tokens:   {total_in:,} in / {total_out:,} out")
    print(f"  Actual cost:     ${total_cost:.2f}"
          f"  (${total_cost/max(len(output),1):.4f} per paper)")
    print(f"  Saved to:        {RESULTS_FILE}")
    _record_actual_cost(len(output), total_cost)
    print(f"  Cost recorded in {COST_HISTORY_PATH} so the next estimate can"
          f" quote a measured price instead of a modelled one.")


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
