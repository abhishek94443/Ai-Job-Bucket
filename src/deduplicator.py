"""
Job Bucket — Canonical Key & Multi-Tier Deduplication Engine
Inspired by the proven deduplication logic of ai-job-search (job_key.py).

Provides:
1. Deterministic canonical key generation:
   - Corporate legal suffix & noise stripping (Pvt Ltd, LLC, Inc, etc.).
   - Role title normalization & abbreviation expansion.
   - City & location alias normalization (Bangalore -> Bengaluru, etc.).
   - Length-capped slug generation with SHA-256 fallback hash.
2. Multi-tier deduplication:
   - Tier 1: Exact (platform, platform_job_id) and normalized_url.
   - Tier 2: Cross-platform semantic match via canonical_key.
   - Tier 3: Mass-posting cluster detection via description content hashing.
"""

import re
import hashlib
import unicodedata
from typing import List, Dict, Tuple, Set, Optional, Any
from .models import JobRecord


# Common corporate legal suffixes and operational noise to strip when matching company names
CORPORATE_SUFFIX_PATTERNS = [
    # Indian corporate entities
    r"\bpvt\.?\s*ltd\.?\b",
    r"\bprivate\s+limited\b",
    r"\bltd\.?\b",
    r"\blimited\b",
    # Global corporate entities
    r"\binc\.?\b",
    r"\bincorporated\b",
    r"\bcorp\.?\b",
    r"\bcorporation\b",
    r"\bllc\.?\b",
    r"\bl\.l\.c\.?\b",
    r"\bllp\.?\b",
    r"\bgmbh\b",
    r"\ba/s\b",
    r"\baps\b",
    r"\bco\.?\b",
    r"\bcompany\b",
    # Common tech company division noise
    r"\btechnologies\b",
    r"\btechnology\b",
    r"\bsolutions\b",
    r"\bservices\b",
    r"\binfotech\b",
    r"\bconsulting\b",
    r"\bconsultancy\b",
    r"\bsoftware\b",
    r"\bsystems\b",
    r"\bgroup\b",
    r"\bholdings\b",
    r"\bdigital\b",
    r"\bglobal\b",
    r"\benterprise\b",
]

# Indian & Global metro alias normalizations
CITY_ALIASES = {
    "bangalore": "bengaluru",
    "bengaluru": "bengaluru",
    "bombay": "mumbai",
    "mumbai": "mumbai",
    "gurgaon": "gurugram",
    "gurugram": "gurugram",
    "calcutta": "kolkata",
    "kolkata": "kolkata",
    "madras": "chennai",
    "chennai": "chennai",
    "poona": "pune",
    "pune": "pune",
    "new delhi": "delhi",
    "ncr": "delhi",
    "delhi": "delhi",
    "hyderabad": "hyderabad",
    "secunderabad": "hyderabad",
}

# Role title abbreviation expansions
TITLE_ABBREVIATIONS = {
    r"\bsr\.?\b": "senior",
    r"\bjr\.?\b": "junior",
    r"\bdev\b": "developer",
    r"\beng\.?\b": "engineer",
    r"\bengr\.?\b": "engineer",
    r"\bsw\b": "software",
    r"\bswe\b": "software engineer",
    r"\bsde\s*1\b": "software development engineer i",
    r"\bsde\s*2\b": "software development engineer ii",
    r"\bsde\s*3\b": "software development engineer iii",
    r"\bsde\b": "software development engineer",
    r"\bbackend\b": "backend",
    r"\bback-end\b": "backend",
    r"\bfrontend\b": "frontend",
    r"\bfront-end\b": "frontend",
    r"\bfullstack\b": "fullstack",
    r"\bfull-stack\b": "fullstack",
}


def clean_company_name(name: Optional[str]) -> str:
    """
    Normalizes company names by:
    - Stripping corporate entity suffixes (Pvt Ltd, LLC, Inc, etc.).
    - Removing bracketed/parenthetical additions (e.g. '(India)', '(HQ)').
    - Removing special characters, punctuation, and extra whitespace.
    """
    if not name or not name.strip():
        return "unknown-company"

    # Normalize unicode to NFKC
    text = unicodedata.normalize("NFKC", name.strip().lower())

    # Strip parentheticals like (India), (Bengaluru), [USA]
    text = re.sub(r"[\(\[\{].*?[\)\]\}]", " ", text)

    # Strip legal entity suffixes
    for pattern in CORPORATE_SUFFIX_PATTERNS:
        text = re.sub(pattern, " ", text, flags=re.IGNORECASE)

    # Remove non-alphanumeric characters (keep basic word chars)
    text = re.sub(r"[^a-z0-9\s]", " ", text)

    # Collapse multiple whitespaces
    tokens = [t for t in text.split() if t]
    return "-".join(tokens) if tokens else "unknown-company"


def clean_job_title(title: Optional[str]) -> str:
    """
    Normalizes job titles by:
    - Stripping job requisitions or IDs (e.g. '[REQ-123]', '(Job ID: 456)').
    - Expanding abbreviations (sr -> senior, dev -> developer, etc.).
    - Removing recruitment fluff (e.g. 'Urgent Hiring!', 'Immediate Joiner').
    """
    if not title or not title.strip():
        return "unknown-title"

    text = unicodedata.normalize("NFKC", title.strip().lower())

    # Strip parentheticals and bracketed requisition numbers
    text = re.sub(r"[\(\[\{].*?[\)\]\}]", " ", text)

    # Strip common recruitment hype phrases
    text = re.sub(r"\b(urgent\s+hiring|immediate\s+joiner|immediate\s+joining|hiring|walkin|walk-in)\b", " ", text, flags=re.IGNORECASE)

    # Expand abbreviations
    for pattern, replacement in TITLE_ABBREVIATIONS.items():
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

    # Remove punctuation
    text = re.sub(r"[^a-z0-9\s]", " ", text)

    tokens = [t for t in text.split() if t]
    return "-".join(tokens) if tokens else "unknown-title"


def clean_location_name(location: Optional[str]) -> str:
    """
    Extracts and normalizes primary city/metro or work mode:
    - Maps city aliases (Bangalore -> Bengaluru, Bombay -> Mumbai).
    - Detects 'remote' or 'hybrid'.
    """
    if not location or not location.strip():
        return "flexible"

    text = unicodedata.normalize("NFKC", location.strip().lower())

    if "remote" in text or "anywhere" in text:
        return "remote"

    # Split on commas, hyphens, slashes to find known city tokens
    parts = re.split(r"[,/|;\s]+", text)
    for part in parts:
        clean_part = part.strip()
        if clean_part in CITY_ALIASES:
            return CITY_ALIASES[clean_part]

    # If no known alias matches, use first alphanumeric token
    clean_text = re.sub(r"[^a-z0-9\s]", "", text)
    tokens = clean_text.split()
    return tokens[0] if tokens else "flexible"


def compute_canonical_job_key(
    company: Optional[str],
    title: Optional[str],
    location: Optional[str] = None
) -> str:
    """
    Computes a deterministic, collision-resistant canonical job key:
    Format: `{company_slug}|{title_slug}|{location_slug}`
    Caps length at 80 characters with SHA-256 hash slice fallback.
    """
    c_slug = clean_company_name(company)
    t_slug = clean_job_title(title)
    l_slug = clean_location_name(location)

    raw_key = f"{c_slug}|{t_slug}|{l_slug}"

    if len(raw_key) <= 80:
        return raw_key

    # Length-cap with hash slice for stability
    hash_slice = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:12]
    return f"{raw_key[:65]}-{hash_slice}"


def compute_description_content_hash(description: Optional[str]) -> Optional[str]:
    """
    Computes a content hash of job description text to detect
    mass-posted identical listings across multiple locations.
    """
    if not description or len(description.strip()) < 100:
        return None

    # Normalize whitespace and lowercase
    normalized = " ".join(re.sub(r"[^a-z0-9\s]", "", description.lower()).split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class Deduplicator:
    """
    Multi-Tier Deduplicator enforcing data integrity:
    Tier 1: Exact (platform, platform_job_id) and normalized_url.
    Tier 2: Cross-platform semantic match via canonical_key.
    Tier 3: Mass-posting cluster detection via description content hash.
    """

    def __init__(self, cross_platform: bool = True, detect_mass_postings: bool = True):
        self.cross_platform = cross_platform
        self.detect_mass_postings = detect_mass_postings

        self._seen_platform_ids: Set[Tuple[str, str]] = set()
        self._seen_normalized_urls: Set[str] = set()
        self._seen_canonical_keys: Set[str] = set()
        self._seen_content_hashes: Dict[str, JobRecord] = {}  # hash -> first seen job

    def compute_key(self, job: JobRecord) -> str:
        """Derives or caches the canonical key on the job record."""
        if job.canonical_key:
            return job.canonical_key
        key = compute_canonical_job_key(job.company, job.title, job.location)
        job.canonical_key = key
        return key

    def is_duplicate(self, job: JobRecord) -> bool:
        """
        Evaluates whether a job is a duplicate under Tier 1 or Tier 2.
        """
        # Tier 1: Exact identity match
        platform_key = (job.platform.lower(), str(job.platform_job_id))
        if job.platform_job_id and platform_key in self._seen_platform_ids:
            return True

        if job.normalized_url and job.normalized_url in self._seen_normalized_urls:
            return True

        # Tier 2: Cross-platform semantic match
        if self.cross_platform:
            canonical_key = self.compute_key(job)
            if canonical_key in self._seen_canonical_keys:
                return True

        return False

    def is_mass_posting(self, job: JobRecord) -> Tuple[bool, Optional[JobRecord]]:
        """
        Tier 3: Checks if an identical description was already seen from the same company.
        Returns (is_mass_posted, original_job).
        """
        if not self.detect_mass_postings:
            return False, None

        c_hash = compute_description_content_hash(job.description_snippet)
        if not c_hash:
            return False, None

        if c_hash in self._seen_content_hashes:
            original = self._seen_content_hashes[c_hash]
            # Verify company similarity to avoid cross-company false positives
            if clean_company_name(job.company) == clean_company_name(original.company):
                return True, original

        return False, None

    def add(self, job: JobRecord) -> bool:
        """
        Adds a job if not duplicate.
        Returns True if added, False if duplicate.
        """
        if self.is_duplicate(job):
            return False

        # Register Tier 1 keys
        if job.platform_job_id:
            self._seen_platform_ids.add((job.platform.lower(), str(job.platform_job_id)))
        if job.normalized_url:
            self._seen_normalized_urls.add(job.normalized_url)

        # Register Tier 2 key
        canonical_key = self.compute_key(job)
        self._seen_canonical_keys.add(canonical_key)

        # Register Tier 3 hash
        c_hash = compute_description_content_hash(job.description_snippet)
        if c_hash and c_hash not in self._seen_content_hashes:
            self._seen_content_hashes[c_hash] = job

        return True

    def deduplicate(self, jobs: List[JobRecord]) -> List[JobRecord]:
        """Filters a list of jobs, retaining only the first unique instance."""
        unique_jobs: List[JobRecord] = []
        for job in jobs:
            if self.add(job):
                unique_jobs.append(job)
        return unique_jobs
