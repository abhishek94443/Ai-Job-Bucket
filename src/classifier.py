import re
from datetime import datetime
from urllib.parse import urlparse
from typing import Optional, Dict, Any, Tuple
from .models import JobRecord, ValidationEvidence, JobStatus, ApplicationType, FreshnessStatus
from .normalizer import normalize_url


class ConservativeClassifier:
    """
    Enforces strict, conservative classification of job listings:
    - Never infers ACTIVE merely because a search result or detail response exists.
    - Never infers EASY_APPLY merely because of platform name or search filters.
    - Accurately classifies external application URLs (Greenhouse, Lever, Workday, etc.) or company career links.
    - Fully tracks evidence sources, notes, and timestamps.
    """

    KNOWN_ATS_DOMAINS = {
        "grnh.se", "greenhouse.io", "lever.co", "workday.com", "myworkdayjobs.com",
        "smartrecruiters.com", "icims.com", "taleo.net", "jobvite.com",
        "bamboohr.com", "ashbyhq.com", "recruitee.com", "breezy.hr"
    }

    @classmethod
    def classify(
        cls,
        platform: str,
        job_url: str,
        detail_payload: Optional[Dict[str, Any]] = None,
        initial_source: str = "search_api"
    ) -> ValidationEvidence:
        normalized_job_url = normalize_url(job_url)
        
        # Rule 1 & 2: By default, without direct DOM inspection or explicit application submission confirmation,
        # status remains UNKNOWN.
        status = JobStatus.UNKNOWN
        app_type = ApplicationType.UNKNOWN
        evidence_notes = ["Listing retrieved via API; job status is UNKNOWN (requires active DOM verification)."]
        evidence_source = initial_source
        app_url = None

        if detail_payload and isinstance(detail_payload, dict):
            evidence_source = f"{initial_source}+detail_api"
            
            # Check for explicit apply_url
            raw_apply_url = detail_payload.get("apply_url") or detail_payload.get("application_url")
            if raw_apply_url and isinstance(raw_apply_url, str):
                normalized_apply_url = normalize_url(raw_apply_url)
                app_url = normalized_apply_url

                # Check if application URL differs from the job listing URL
                if normalized_apply_url != normalized_job_url:
                    parsed_app = urlparse(normalized_apply_url)
                    netloc = parsed_app.netloc.lower()

                    # Check if domain matches known external ATS
                    is_ats = any(ats in netloc for ats in cls.KNOWN_ATS_DOMAINS)
                    if is_ats:
                        app_type = ApplicationType.EXTERNAL
                        evidence_notes.append(f"External ATS detected ({netloc}) via apply_url.")
                    else:
                        # Non-platform, non-ATS external destination (likely company career site)
                        parsed_job = urlparse(normalized_job_url)
                        if parsed_app.netloc != parsed_job.netloc:
                            app_type = ApplicationType.EXTERNAL
                            evidence_notes.append(f"External destination ({netloc}) detected via apply_url.")
                        else:
                            evidence_notes.append("Apply URL points to internal listing page; type UNKNOWN.")
                else:
                    evidence_notes.append("Apply URL matches listing URL; application flow remains unverified.")

            # Look for explicit flags if provided by platform detail
            is_easy_apply_flag = detail_payload.get("is_easy_apply")
            if is_easy_apply_flag is True:
                # Platform detail explicitly confirms native easy apply
                app_type = ApplicationType.EASY_APPLY
                evidence_notes.append("Platform detail payload explicitly declared is_easy_apply=True.")

        return ValidationEvidence(
            job_status=status,
            application_type=app_type,
            evidence_source=evidence_source,
            evidence_notes="; ".join(evidence_notes),
            application_url=app_url
        )


class FreshnessClassifier:
    """
    Evaluates freshness strictly based on the original posting date:
    - Target: jobs originally posted in the preceding 24 hours (CONFIRMED_FRESH).
    - Preserves and checks original posting date separately from update date, first-seen date, or DB insertion date.
    - If updated recently but original posted date is old -> STALE (do not treat updated old job as newly posted).
    - If signals contradict (e.g. reality class stale vs posted_at today, or reposted) -> CONTRADICTORY.
    - If missing or unparseable -> UNKNOWN_FRESHNESS.
    """
    @classmethod
    def evaluate(
        cls,
        posted_date_str: Optional[str],
        updated_date_str: Optional[str] = None,
        reality_meta: Optional[Dict[str, Any]] = None,
        reference_time: Optional[datetime] = None
    ) -> Tuple[FreshnessStatus, str]:
        from .models import FreshnessStatus
        
        if not posted_date_str or not isinstance(posted_date_str, str) or not posted_date_str.strip():
            return FreshnessStatus.UNKNOWN_FRESHNESS, "Original posting date missing or unavailable"

        clean_posted = posted_date_str.strip()
        match = re.match(r"^(\d{4})-(\d{2})-(\d{2})", clean_posted)
        if not match:
            return FreshnessStatus.UNKNOWN_FRESHNESS, f"Unparseable posting date format: '{clean_posted}'"

        try:
            year, month, day = map(int, match.groups())
            p_date = datetime(year, month, day).date()
        except ValueError:
            return FreshnessStatus.UNKNOWN_FRESHNESS, f"Invalid calendar date: '{clean_posted}'"

        ref_date = reference_time.date() if reference_time else datetime.now().date()
        age_days = (ref_date - p_date).days

        # Check for contradictory signals if source metadata is provided
        if reality_meta and isinstance(reality_meta, dict):
            reality_class = reality_meta.get("class")
            fake_freshness = reality_meta.get("fake_freshness")
            repost_count = reality_meta.get("repost_count") or 0

            if fake_freshness is True or reality_class == "stale" and age_days <= 1:
                return FreshnessStatus.CONTRADICTORY, f"Contradictory freshness signals: posted {clean_posted} but flagged as {reality_class} / fake_freshness={fake_freshness}"

        # Evaluate updated date separately if available:
        # If updated recently but originally posted long ago, it is STALE, not fresh.
        if updated_date_str and isinstance(updated_date_str, str):
            up_match = re.match(r"^(\d{4})-(\d{2})-(\d{2})", updated_date_str.strip())
            if up_match:
                try:
                    u_year, u_month, u_day = map(int, up_match.groups())
                    u_date = datetime(u_year, u_month, u_day).date()
                    orig_age = (ref_date - p_date).days
                    if orig_age > 1 and (ref_date - u_date).days <= 1:
                        return FreshnessStatus.STALE, f"Updated recently ({updated_date_str}) but originally posted {clean_posted} ({orig_age} days ago)"
                except ValueError:
                    pass

        if age_days <= 1:
            return FreshnessStatus.CONFIRMED_FRESH, f"Confirmed originally posted within preceding 24h ({clean_posted})"
        elif age_days <= 7:
            return FreshnessStatus.RECENT, f"Posted {age_days} days ago ({clean_posted})"
        else:
            return FreshnessStatus.STALE, f"Stale listing: originally posted {age_days} days ago ({clean_posted})"

