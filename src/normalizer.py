import re
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
from typing import Optional, Tuple

# Tracking & session query parameters to strip
TRACKING_PARAMS = {
    "refid", "trackingid", "trk", "trkref", "midtoken", "fromsearch",
    "utmsource", "utmmedium", "utmcampaign", "utmterm", "utmcontent",
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "gclid", "fbclid", "msclkid", "fref", "spref", "position", "page_num",
    "currentjobid", "originalsubdomain", "eType", "jobtype", "vjk", "tk"
}


def normalize_url(url: str) -> str:
    """
    Cleans URLs by:
    - Normalizing scheme and netloc to lowercase.
    - Stripping known marketing/tracking/session query parameters.
    - Sorting remaining query parameters deterministically.
    - Removing trailing slashes from path (except root '/').
    - Dropping URL fragments (#hash).
    """
    if not url or not url.strip():
        return ""

    raw_url = url.strip()
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", raw_url):
        raw_url = f"https://{raw_url}"

    parsed = urlparse(raw_url)
    scheme = (parsed.scheme or "https").lower()
    netloc = parsed.netloc.lower()

    # Clean path: normalize slashes
    path = parsed.path
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    # Parse and filter query parameters
    query_params = parse_qsl(parsed.query, keep_blank_values=False)
    filtered_params = [
        (k, v) for k, v in query_params
        if k.lower() not in TRACKING_PARAMS
    ]
    # Sort deterministically
    filtered_params.sort(key=lambda x: x[0])
    query = urlencode(filtered_params)

    # Reconstruct without fragment
    return urlunparse((scheme, netloc, path, "", query, ""))


def extract_platform_job_id(platform: str, url: str, fallback_id: Optional[str] = None) -> Tuple[Optional[str], str]:
    """
    Extracts canonical platform job ID from URL or returns sanitized fallback ID.
    Returns (job_id, platform_canonical_key).
    """
    platform_key = platform.strip().lower()

    if "linkedin" in platform_key:
        # Match patterns like: /jobs/view/1234567890 or currentJobId=1234567890
        match = re.search(r"/jobs/view/(\d+)", url)
        if match:
            return match.group(1), "linkedin"
        match = re.search(r"currentJobId=(\d+)", url)
        if match:
            return match.group(1), "linkedin"
        if fallback_id and fallback_id.isdigit():
            return fallback_id, "linkedin"

    elif "indeed" in platform_key:
        # Match jk=xxxx or jk=xxxx in params or /viewjob?jk=xxxx
        match = re.search(r"[?&]jk=([a-zA-Z0-9]+)", url)
        if match:
            return match.group(1), "indeed"
        match = re.search(r"/viewjob\?.*jk=([a-zA-Z0-9]+)", url)
        if match:
            return match.group(1), "indeed"
        match = re.search(r"/rc/clk\?.*jk=([a-zA-Z0-9]+)", url)
        if match:
            return match.group(1), "indeed"
    elif "freehire" in platform_key:
        # Match public_slug or external_id or job URL segment
        match = re.search(r"/jobs/[^/]+/(\d+)", url)
        if match:
            return match.group(1)[:100], "freehire"
        match = re.search(r"/opportunity/(\d+)", url)
        if match:
            return match.group(1)[:100], "freehire"
        if fallback_id:
            return fallback_id[:100], "freehire"

    sanitized_id = fallback_id[:100] if fallback_id else None
    return sanitized_id, platform_key
