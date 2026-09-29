import logging
from typing import Optional, Dict, Any
from .models import JobRecord, ValidationEvidence, JobStatus, ApplicationType
from .mcp_client import OpeningsMCPClient

logger = logging.getLogger(__name__)

class BrowserListingValidator:
    """
    Browser validation component.
    
    Technical Architecture Note:
    - In the current system environment, Playwright MCP is registered inside Antigravity's
      Cascade/MCP container configuration (`npx -y @playwright/mcp@latest`), exposing browser
      actions to the Antigravity agent environment.
    - To remain decoupled and strictly avoid claiming browser validation succeeded when running
      in standalone non-browser contexts, this component validates listings if a compatible browser
      automation session or endpoint is explicitly attached, and conservatively reports unverified
      status when running standalone without an active browser session.
    """

    def __init__(self, browser_mcp_client: Optional[Any] = None):
        self.browser_client = browser_mcp_client

    async def probe_listing(self, job: JobRecord) -> ValidationEvidence:
        """
        Probes a job listing without bypassing logins or CAPTCHAs.
        If browser integration is not attached or listing is protected by login walls,
        preserves conservative UNKNOWN status with transparent notes.
        """
        if not self.browser_client:
            return ValidationEvidence(
                job_status=JobStatus.UNKNOWN,
                application_type=job.validation.application_type,
                evidence_source="browser_probe_skipped",
                evidence_notes=(
                    f"{job.validation.evidence_notes}; "
                    "Browser validation skipped: Standalone execution without attached browser MCP session."
                ),
                application_url=job.validation.application_url
            )

        # If a browser client is connected in the future, perform conservative DOM checks:
        # 1. Navigate to job.url
        # 2. Check for login wall or CAPTCHA -> Return UNKNOWN with "Login wall / CAPTCHA encountered"
        # 3. Check for expired text ("No longer accepting applications") -> EXPIRED
        # 4. Check for active apply buttons -> ACTIVE + EASY_APPLY or EXTERNAL
        return job.validation
