import re
from typing import Tuple


class PrivacyScrubber:
    """
    Sanitizes raw resume text before transmission to external AI models.
    Removes personal contact details (email, phone, PIN codes, personal profile handles)
    while strictly preserving technical capabilities, projects, work experience, and timeline facts.
    """

    # Regex for international and Indian phone formats (10-digit, +91, with spaces/hyphens)
    PHONE_REGEX = re.compile(
        r"(?:\+?\d{1,3}[-.\s]?)?\(?\d{3,5}\)?[-.\s]?\d{3,5}[-.\s]?\d{3,5}\b"
    )

    # Standard email regex
    EMAIL_REGEX = re.compile(
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
    )

    # Indian 6-digit postal PIN codes
    PIN_REGEX = re.compile(r"\b[1-9][0-9]{5}\b")

    # Personal profile URLs with user handles
    SOCIAL_URL_REGEX = re.compile(
        r"https?://(?:www\.)?(?:linkedin\.com/in/|github\.com/|twitter\.com/|x\.com/)[A-Za-z0-9_-]+/?",
        re.IGNORECASE,
    )

    @classmethod
    def scrub_text(cls, text: str, candidate_name: str = "") -> Tuple[str, dict]:
        """
        Removes PII from resume text.
        Returns (scrubbed_text, redaction_summary_counts).
        """
        if not text:
            return "", {"emails": 0, "phones": 0, "pins": 0, "links": 0, "name_redacted": False}

        scrubbed = text
        counts = {"emails": 0, "phones": 0, "pins": 0, "links": 0, "name_redacted": False}

        # 1. Redact Emails
        emails_found = cls.EMAIL_REGEX.findall(scrubbed)
        counts["emails"] = len(emails_found)
        scrubbed = cls.EMAIL_REGEX.sub("[REDACTED_EMAIL]", scrubbed)

        # 2. Redact Social Profile Links
        links_found = cls.SOCIAL_URL_REGEX.findall(scrubbed)
        counts["links"] = len(links_found)
        scrubbed = cls.SOCIAL_URL_REGEX.sub("[REDACTED_PROFILE_URL]", scrubbed)

        # 3. Redact Phone Numbers (filter out potential date/version false positives)
        def _replace_phone(match):
            val = match.group(0).strip()
            # Do not redact 4-digit years (e.g. 2020-2024)
            if re.match(r"^\d{4}[-–]\d{4}$", val) or re.match(r"^\d{4}$", val):
                return val
            # Only count as phone if at least 10 digits
            digits_count = sum(c.isdigit() for c in val)
            if digits_count >= 10:
                counts["phones"] += 1
                return "[REDACTED_PHONE]"
            return val

        scrubbed = cls.PHONE_REGEX.sub(_replace_phone, scrubbed)

        # 4. Redact Indian PIN codes
        pins_found = cls.PIN_REGEX.findall(scrubbed)
        counts["pins"] = len(pins_found)
        scrubbed = cls.PIN_REGEX.sub("[REDACTED_PIN]", scrubbed)

        # 5. Redact Candidate Name if provided
        if candidate_name and candidate_name.strip():
            name_clean = candidate_name.strip()
            if len(name_clean) > 2:
                name_pattern = re.compile(rf"\b{re.escape(name_clean)}\b", re.IGNORECASE)
                if name_pattern.search(scrubbed):
                    counts["name_redacted"] = True
                    scrubbed = name_pattern.sub("[CANDIDATE_NAME]", scrubbed)

        return scrubbed, counts
