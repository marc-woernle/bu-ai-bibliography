"""
BU AI Bibliography Harvester — Utilities
=========================================
Shared helpers: rate limiting, deduplication, text normalization, persistence,
resilient HTTP requests.
"""

import json
import signal
import time
import hashlib
import re
import os
import logging
import threading
import requests
from datetime import datetime
from functools import wraps
from pathlib import Path

logger = logging.getLogger("bu_bib")


# ── Rate Limiter ──────────────────────────────────────────────────────────────

class RateLimiter:
    """Simple token-bucket rate limiter."""

    def __init__(self, calls_per_second: float):
        self.min_interval = 1.0 / calls_per_second
        self.last_call = 0.0

    def wait(self):
        now = time.time()
        elapsed = now - self.last_call
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self.last_call = time.time()


# ── Resilient HTTP Requests ───────────────────────────────────────────────────

class HarvestBudgetExceeded(Exception):
    """Raised when a source exceeds its time budget."""
    pass


def resilient_get(
    url: str,
    params: dict | None = None,
    headers: dict | None = None,
    *,
    rate_limiter: RateLimiter | None = None,
    max_retries: int = 5,
    base_delay: float = 5.0,
    max_delay: float = 120.0,
    timeout: float = 30.0,
    deadline: float | None = None,
) -> requests.Response:
    """HTTP GET with exponential backoff, retry classification, and time budget.

    Args:
        deadline: absolute time.time() after which HarvestBudgetExceeded is raised.

    Retries on: 429, 500, 502, 503, 504, ConnectionError, Timeout.
    Does NOT retry on: 400, 401, 403, 404 (client errors are not transient).
    """
    last_exc = None
    for attempt in range(max_retries + 1):
        if deadline and time.time() > deadline:
            raise HarvestBudgetExceeded(f"Time budget exceeded after {attempt} attempts")

        if rate_limiter:
            rate_limiter.wait()

        try:
            resp = requests.get(url, params=params, headers=headers, timeout=timeout)

            if resp.status_code == 429:
                delay = min(base_delay * (2 ** attempt), max_delay)
                retry_after = resp.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    delay = max(delay, min(int(retry_after), max_delay))
                logger.warning(f"429 rate limited, backoff {delay:.0f}s (attempt {attempt + 1}/{max_retries + 1})")
                time.sleep(delay)
                continue

            if resp.status_code >= 500:
                delay = min(base_delay * (2 ** attempt), max_delay)
                logger.warning(f"HTTP {resp.status_code}, backoff {delay:.0f}s (attempt {attempt + 1}/{max_retries + 1})")
                time.sleep(delay)
                continue

            resp.raise_for_status()
            return resp

        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            last_exc = e
            delay = min(base_delay * (2 ** attempt), max_delay)
            logger.warning(f"Network error: {e}, backoff {delay:.0f}s (attempt {attempt + 1}/{max_retries + 1})")
            time.sleep(delay)
            continue
        except requests.exceptions.HTTPError:
            raise

    raise requests.exceptions.RetryError(
        f"Failed after {max_retries + 1} attempts: {last_exc or 'rate limited'}"
    )


def resilient_post(
    url: str,
    json_body: dict | None = None,
    headers: dict | None = None,
    *,
    rate_limiter: RateLimiter | None = None,
    max_retries: int = 5,
    base_delay: float = 5.0,
    max_delay: float = 120.0,
    timeout: float = 30.0,
    deadline: float | None = None,
) -> requests.Response:
    """HTTP POST with exponential backoff. Same retry logic as resilient_get."""
    last_exc = None
    for attempt in range(max_retries + 1):
        if deadline and time.time() > deadline:
            raise HarvestBudgetExceeded(f"Time budget exceeded after {attempt} attempts")

        if rate_limiter:
            rate_limiter.wait()

        try:
            resp = requests.post(url, json=json_body, headers=headers, timeout=timeout)

            if resp.status_code == 429:
                delay = min(base_delay * (2 ** attempt), max_delay)
                logger.warning(f"429 rate limited, backoff {delay:.0f}s (attempt {attempt + 1}/{max_retries + 1})")
                time.sleep(delay)
                continue

            if resp.status_code >= 500:
                delay = min(base_delay * (2 ** attempt), max_delay)
                logger.warning(f"HTTP {resp.status_code}, backoff {delay:.0f}s (attempt {attempt + 1}/{max_retries + 1})")
                time.sleep(delay)
                continue

            resp.raise_for_status()
            return resp

        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            last_exc = e
            delay = min(base_delay * (2 ** attempt), max_delay)
            logger.warning(f"Network error: {e}, backoff {delay:.0f}s (attempt {attempt + 1}/{max_retries + 1})")
            time.sleep(delay)
            continue
        except requests.exceptions.HTTPError:
            raise

    raise requests.exceptions.RetryError(
        f"Failed after {max_retries + 1} attempts: {last_exc or 'rate limited'}"
    )


# ── Text Normalization ────────────────────────────────────────────────────────

def normalize_title(title: str) -> str:
    """Normalize a paper title for dedup comparison."""
    if not title:
        return ""
    t = title.lower().strip()
    t = re.sub(r'[^\w\s]', '', t)       # remove punctuation
    t = re.sub(r'\s+', ' ', t)          # collapse whitespace
    return t


def title_fingerprint(title: str) -> str:
    """Create a hash fingerprint from a normalized title."""
    norm = normalize_title(title)
    return hashlib.md5(norm.encode('utf-8')).hexdigest()


def normalize_doi(doi: str) -> str | None:
    """Normalize a DOI string."""
    if not doi:
        return None
    doi = doi.strip().lower()
    # Strip URL prefixes
    for prefix in ["https://doi.org/", "http://doi.org/", "doi:", "doi.org/"]:
        if doi.startswith(prefix):
            doi = doi[len(prefix):]
    return doi if doi else None


# ── Unified Paper Record ──────────────────────────────────────────────────────

def make_paper_record(
    title: str,
    authors: list[dict],          # [{"name": "...", "affiliation": "...", "orcid": "..."}]
    year: int | None,
    doi: str | None,
    abstract: str | None,
    source: str,                  # "openalex", "semantic_scholar", "pubmed", "arxiv", etc.
    source_id: str | None,        # source-specific ID
    url: str | None = None,
    pdf_url: str | None = None,
    venue: str | None = None,     # journal/conference name
    concepts: list[str] | None = None,
    citation_count: int | None = None,
    publication_type: str | None = None,
    extra: dict | None = None,    # source-specific metadata
) -> dict:
    """Create a standardized paper record."""
    return {
        "title": title,
        "title_normalized": normalize_title(title),
        "title_fingerprint": title_fingerprint(title),
        "authors": authors or [],
        "year": year,
        "doi": normalize_doi(doi) if doi else None,
        "abstract": abstract,
        "source": source,
        "source_id": source_id,
        "url": url,
        "pdf_url": pdf_url,
        "venue": venue,
        "concepts": concepts or [],
        "citation_count": citation_count,
        "publication_type": publication_type,
        "extra": extra or {},
        "harvested_at": datetime.utcnow().isoformat(),
    }


# ── Deduplication ─────────────────────────────────────────────────────────────

class Deduplicator:
    """Tracks seen papers by DOI and title fingerprint.
    
    When a duplicate is found, merges source info rather than discarding.
    """

    def __init__(self):
        self.by_doi: dict[str, int] = {}           # doi → index in records
        self.by_fingerprint: dict[str, int] = {}    # title_fingerprint → index
        self.records: list[dict] = []

    def add(self, paper: dict) -> bool:
        """Add paper. Returns True if new, False if duplicate (merged)."""
        doi = paper.get("doi")
        fp = paper.get("title_fingerprint")

        # Check DOI first (strongest signal)
        if doi and doi in self.by_doi:
            self._merge(self.by_doi[doi], paper)
            return False

        # Check title fingerprint
        if fp and fp in self.by_fingerprint:
            self._merge(self.by_fingerprint[fp], paper)
            return False

        # New paper
        idx = len(self.records)
        self.records.append(paper)
        if doi:
            self.by_doi[doi] = idx
        if fp:
            self.by_fingerprint[fp] = idx
        return True

    def _merge(self, idx: int, new_paper: dict):
        """Merge new_paper info into existing record at idx."""
        existing = self.records[idx]

        # Track all sources
        if "all_sources" not in existing:
            existing["all_sources"] = [existing["source"]]
        existing["all_sources"].append(new_paper["source"])

        # Fill in missing fields
        for key in ["abstract", "doi", "url", "pdf_url", "venue", "citation_count"]:
            if not existing.get(key) and new_paper.get(key):
                existing[key] = new_paper[key]

        # Merge concepts (flatten nested lists, stringify everything)
        if new_paper.get("concepts"):
            def _flatten_concepts(lst):
                for item in lst:
                    if isinstance(item, list):
                        yield from _flatten_concepts(item)
                    elif item is not None:
                        yield str(item)

            existing_concepts = set(_flatten_concepts(existing.get("concepts", [])))
            existing_concepts.update(_flatten_concepts(new_paper["concepts"]))
            existing["concepts"] = list(existing_concepts)

        # Merge extra
        if new_paper.get("extra"):
            existing.setdefault("extra", {}).update(new_paper["extra"])

    def get_all(self) -> list[dict]:
        return self.records

    @property
    def count(self) -> int:
        return len(self.records)


# ── Persistence ───────────────────────────────────────────────────────────────

def save_checkpoint(records: list[dict], source_name: str, output_dir: str = "data"):
    """Save harvested records as a JSON checkpoint."""
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(output_dir, f"{source_name}_{ts}.json")
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
    logger.info(f"Checkpoint saved: {path} ({len(records)} records)")
    return path


def load_checkpoint(path: str) -> list[dict]:
    """Load records from a checkpoint file."""
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_final(records: list[dict], output_dir: str = "data"):
    """Save the final deduplicated bibliography."""
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    # JSON (full data)
    json_path = os.path.join(output_dir, f"bu_ai_bibliography_{ts}.json")
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    # CSV (summary for quick review)
    csv_path = os.path.join(output_dir, f"bu_ai_bibliography_{ts}.csv")
    import csv
    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow([
            "bu_category", "bu_schools", "title", "authors", "year", "doi", "venue",
            "source", "pub_type", "concepts", "citation_count", "url", "has_abstract"
        ])
        for r in records:
            authors_str = "; ".join(a.get("name", "") for a in r.get("authors", []))
            concepts_str = "; ".join(str(c) for c in r.get("concepts", [])[:5] if c and not isinstance(c, list))
            schools_str = "; ".join(r.get("bu_schools", []))
            writer.writerow([
                r.get("bu_category", ""),
                schools_str,
                r.get("title", ""),
                authors_str,
                r.get("year", ""),
                r.get("doi", ""),
                r.get("venue", ""),
                r.get("source", ""),
                r.get("publication_type", ""),
                concepts_str,
                r.get("citation_count", ""),
                r.get("url", ""),
                "yes" if r.get("abstract") else "no",
            ])

    logger.info(f"Final output saved: {json_path} and {csv_path}")
    return json_path, csv_path


# ── Logging Setup ─────────────────────────────────────────────────────────────

def setup_logging(log_dir: str = "logs"):
    """Configure structured logging."""
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = os.path.join(log_dir, f"harvest_{ts}.log")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(),
        ],
    )
    logger.info(f"Logging initialized → {log_file}")
    return log_file
