import logging
from typing import List, Dict, Any, Optional
from .mcp_client import OpeningsMCPClient
from .models import JobRecord, ValidationEvidence, JobStatus, ApplicationType
from .normalizer import normalize_url, extract_platform_job_id
from .classifier import ConservativeClassifier, FreshnessClassifier
from .candidate_profile import DEFAULT_CANDIDATE_PROFILE, CandidateProfile

logger = logging.getLogger(__name__)

class JobDiscoveryService:
    """
    Discovers job listings across LinkedIn, Indeed, and Freehire using Openings MCP.
    Integrates resume-skill-first discovery, strict freshness checks (preceding 24 hours),
    and India tech hubs prioritization.
    """
    def __init__(self, mcp_client: Optional[OpeningsMCPClient] = None, profile: Optional[CandidateProfile] = None):
        self.client = mcp_client or OpeningsMCPClient()
        self.profile = profile or DEFAULT_CANDIDATE_PROFILE

    DEFAULT_PAGE_SIZE: int = 10
    DEFAULT_MAX_PAGES: int = 3
    DEFAULT_MAX_RESULTS: int = 30

    async def search_freehire(
        self,
        query: str = "java",
        country_code: str = "in",
        posted_within_days: int = 1,
        limit: int = 10,
        cities: Optional[str] = None,
        max_pages: int = 3,
        max_results: Optional[int] = None,
        offset: int = 0,
    ) -> List[JobRecord]:
        """
        Discovers tech jobs from Freehire via freehire_search_jobs MCP tool.
        - q: skill-based search query (e.g. 'java', 'spring boot')
        - cities: when provided, passes canonical city name and omits country to prevent OR-group widening.
        - countries: lowercase ISO 3166-1 alpha-2 ('in' for India) used only when cities is omitted.
        - posted_within_days: filter by posting age.
        - limit: page size (max 100).
        - offset: starting offset.
        - max_pages: maximum page requests.
        - max_results: upper bound on returned records.
        """
        results: List[JobRecord] = []
        seen_keys = set()
        page_size = min(100, max(1, limit))
        target_limit = max_results if max_results is not None else (page_size * max_pages)
        current_offset = max(0, offset)
        exhausted = False

        for page_idx in range(max_pages):
            if len(results) >= target_limit:
                break

            logger.info(
                f"Searching Freehire (page {page_idx + 1}/{max_pages}): q='{query}', "
                f"cities='{cities}', country='{country_code if not cities else '(omitted for city search)'}', "
                f"offset={current_offset}, limit={page_size}"
            )

            payload: Dict[str, Any] = {
                "q": query,
                "posted_within_days": posted_within_days,
                "limit": page_size,
                "offset": current_offset,
            }
            if cities and cities.strip():
                payload["cities"] = cities.strip()
            elif country_code and country_code.strip():
                payload["countries"] = country_code.strip()

            try:
                res = await self.client.call_tool("freehire_search_jobs", payload)
            except Exception as e:
                logger.error(f"Error calling freehire_search_jobs on page {page_idx + 1}: {e}")
                break

            if not res or not isinstance(res, dict) or "data" not in res:
                break

            raw_items = res.get("data", [])
            if not isinstance(raw_items, list) or len(raw_items) == 0:
                exhausted = True
                break

            page_added = 0
            for item in raw_items:
                if len(results) >= target_limit:
                    break

                raw_url = item.get("url") or ""
                pub_slug = str(item.get("public_slug") or "").strip()
                ext_id = str(item.get("external_id") or "").strip()
                chosen_id = pub_slug if pub_slug and len(pub_slug) <= 100 else (ext_id if ext_id and len(ext_id) <= 100 else (pub_slug or ext_id))
                platform_id, _ = extract_platform_job_id("freehire", raw_url, fallback_id=chosen_id[:100] if chosen_id else "")
                norm_url = normalize_url(raw_url)

                dedup_key = f"{platform_id or ext_id}|{norm_url}"
                if dedup_key in seen_keys:
                    continue
                seen_keys.add(dedup_key)

                posted_at = item.get("posted_at")
                updated_at = item.get("updated_at")
                reality_meta = item.get("reality") or {}
                work_mode = item.get("work_mode")

                # Freshness classification
                freshness, f_reason = FreshnessClassifier.evaluate(
                    posted_date_str=posted_at,
                    updated_date_str=updated_at,
                    reality_meta=reality_meta,
                )

                # Conservative validation evidence
                evidence = ConservativeClassifier.classify(
                    platform="freehire",
                    job_url=norm_url,
                    detail_payload={"apply_url": raw_url},
                    initial_source="freehire_search_jobs",
                )
                evidence.evidence_notes = f"{evidence.evidence_notes}; Freshness: {f_reason}"

                description = item.get("description")
                loc = item.get("location")
                if not loc and item.get("cities"):
                    c_list = item.get("cities")
                    loc = ", ".join(c_list) if isinstance(c_list, list) else str(c_list)

                # Map structured requirements & experience without inventing values
                enrichment = item.get("enrichment")
                source_exp_min: Optional[int] = None
                structured_reqs: Optional[List[Dict[str, Any]]] = None

                if isinstance(enrichment, dict):
                    raw_exp = enrichment.get("experience_years_min")
                    if raw_exp is not None:
                        try:
                            source_exp_min = int(raw_exp)
                        except (ValueError, TypeError):
                            source_exp_min = None

                    raw_reqs = enrichment.get("requirements")
                    if isinstance(raw_reqs, list) and len(raw_reqs) > 0:
                        valid_reqs = [r for r in raw_reqs if isinstance(r, dict)]
                        if valid_reqs:
                            structured_reqs = valid_reqs

                is_partial = False
                if item.get("source") == "telegram" or (description and len(description.strip()) < 250):
                    is_partial = True

                record = JobRecord(
                    platform="freehire",
                    platform_job_id=str(platform_id or ext_id),
                    title=item.get("title") or "Unknown Title",
                    company=item.get("company") or "Unknown Company",
                    location=loc,
                    url=raw_url,
                    normalized_url=norm_url,
                    posted_date=posted_at,
                    updated_date=updated_at,
                    freshness_status=freshness,
                    work_mode=work_mode,
                    description_snippet=description if description else None,
                    is_partial_description=is_partial,
                    source_experience_years_min=source_exp_min,
                    structured_requirements=structured_reqs,
                    validation=evidence,
                )
                results.append(record)
                page_added += 1

            if page_added == 0:
                logger.info("Freehire page returned no new records (all duplicates); stopping pagination.")
                break

            if len(raw_items) < page_size:
                exhausted = True
                break

            total_count = res.get("total")
            if isinstance(total_count, int) and current_offset + len(raw_items) >= total_count:
                exhausted = True
                break

            current_offset += len(raw_items)
            if current_offset >= 10000:
                break

        if len(results) >= target_limit and not exhausted:
            logger.info(
                f"Freehire search reached configured limit ({len(results)} items, target_limit={target_limit}); "
                "discovery may not be exhaustive."
            )
        else:
            logger.info(f"Freehire discovery finished: retrieved {len(results)} unique jobs.")

        return results

    async def search_linkedin(
        self,
        keyword: str = "Java Backend Developer",
        location: str = "Bengaluru, Karnataka, India",
        posted_within: Optional[str] = "Past day",
        limit: int = 10,
        fetch_details: bool = False,
        max_pages: int = 3,
        max_results: Optional[int] = None,
        start: int = 0,
    ) -> List[JobRecord]:
        results: List[JobRecord] = []
        seen_keys = set()
        page_size = 10  # LinkedIn public guest endpoint fixed page size
        target_limit = max_results if max_results is not None else (limit if max_pages == 1 else (page_size * max_pages))
        current_start = max(0, start)
        exhausted = False

        for page_idx in range(max_pages):
            if len(results) >= target_limit:
                break

            logger.info(
                f"Searching LinkedIn (page {page_idx + 1}/{max_pages}): keyword='{keyword}', "
                f"location='{location}', posted_within='{posted_within}', start={current_start}"
            )

            payload: Dict[str, Any] = {
                "keyword": keyword,
                "location": location,
                "start": current_start,
            }
            if posted_within in ["Past day", "Past week", "Past month"]:
                payload["posted_within"] = posted_within

            try:
                res = await self.client.call_tool("linkedin_search_jobs", payload)
            except Exception as e:
                logger.error(f"Error calling linkedin_search_jobs on page {page_idx + 1}: {e}")
                break

            if not res or not isinstance(res, dict) or "data" not in res:
                break

            raw_items = res.get("data", [])
            if not isinstance(raw_items, list) or len(raw_items) == 0:
                exhausted = True
                break

            page_added = 0
            for item in raw_items:
                if len(results) >= target_limit:
                    break

                raw_url = item.get("url") or ""
                platform_id, _ = extract_platform_job_id("linkedin", raw_url, fallback_id=str(item.get("id", "")))
                norm_url = normalize_url(raw_url)

                dedup_key = f"{platform_id}|{norm_url}"
                if dedup_key in seen_keys:
                    continue
                seen_keys.add(dedup_key)

                detail_data = None
                if fetch_details and platform_id:
                    try:
                        detail_data = await self.client.call_tool("linkedin_get_job_detail", {
                            "job_id": str(platform_id)
                        }, timeout_seconds=15.0)
                    except Exception as e:
                        logger.warning(f"Could not fetch LinkedIn detail for job {platform_id}: {e}")

                evidence = ConservativeClassifier.classify(
                    platform="linkedin",
                    job_url=norm_url,
                    detail_payload=detail_data if isinstance(detail_data, dict) else None,
                    initial_source="linkedin_search_jobs",
                )

                description = None
                if detail_data and isinstance(detail_data, dict):
                    description = detail_data.get("description")

                posted_date = item.get("posted_date")
                freshness, f_reason = FreshnessClassifier.evaluate(posted_date_str=posted_date)
                evidence.evidence_notes = f"{evidence.evidence_notes}; Freshness: {f_reason}"

                record = JobRecord(
                    platform="linkedin",
                    platform_job_id=str(platform_id or ""),
                    title=item.get("title") or "Unknown Title",
                    company=item.get("company") or "Unknown Company",
                    location=item.get("location"),
                    url=raw_url,
                    normalized_url=norm_url,
                    posted_date=posted_date,
                    freshness_status=freshness,
                    description_snippet=description if description else None,
                    is_partial_description=(not description or len(description.strip()) < 250),
                    validation=evidence,
                )
                results.append(record)
                page_added += 1

            if page_added == 0:
                logger.info("LinkedIn page returned no new records (all duplicates); stopping pagination.")
                break

            if len(raw_items) < page_size:
                exhausted = True
                break

            current_start += 10

        if len(results) >= target_limit and not exhausted:
            logger.info(
                f"LinkedIn search reached configured limit ({len(results)} items, target_limit={target_limit}); "
                "discovery may not be exhaustive."
            )
        else:
            logger.info(f"LinkedIn discovery finished: retrieved {len(results)} unique jobs.")

        return results

    async def search_indeed(
        self,
        keyword: str = "Java Backend Developer",
        location: str = "Bengaluru",
        country: str = "India",
        hours_old: Optional[int] = 24,
        limit: int = 10,
        fetch_details: bool = True,
        max_pages: int = 3,
        max_results: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> List[JobRecord]:
        results: List[JobRecord] = []
        seen_keys = set()
        seen_cursors: Set[str] = set()
        page_size = max(1, limit)
        target_limit = max_results if max_results is not None else (limit if max_pages == 1 else (page_size * max_pages))
        current_cursor = cursor
        exhausted = False

        for page_idx in range(max_pages):
            if len(results) >= target_limit:
                break

            logger.info(
                f"Searching Indeed (page {page_idx + 1}/{max_pages}): keyword='{keyword}', "
                f"location='{location}', country='{country}', hours_old={hours_old}, "
                f"cursor='{current_cursor or 'INITIAL'}'"
            )

            payload: Dict[str, Any] = {
                "keyword": keyword,
                "location": location,
                "country": country,
            }
            if hours_old is not None and hours_old > 0:
                payload["hours_old"] = hours_old
            if current_cursor and current_cursor.strip():
                payload["cursor"] = current_cursor.strip()

            try:
                res = await self.client.call_tool("indeed_search_jobs", payload)
            except Exception as e:
                logger.error(f"Error calling indeed_search_jobs on page {page_idx + 1}: {e}")
                break

            if not res or not isinstance(res, dict) or "data" not in res:
                break

            raw_items = res.get("data", [])
            if not isinstance(raw_items, list) or len(raw_items) == 0:
                exhausted = True
                break

            page_added = 0
            for item in raw_items:
                if len(results) >= target_limit:
                    break

                raw_url = item.get("url") or ""
                job_key = item.get("key") or ""
                platform_id, _ = extract_platform_job_id("indeed", raw_url, fallback_id=str(job_key))
                norm_url = normalize_url(raw_url)

                dedup_key = f"{platform_id}|{norm_url}"
                if dedup_key in seen_keys:
                    continue
                seen_keys.add(dedup_key)

                detail_data = None
                if fetch_details and job_key:
                    try:
                        detail_data = await self.client.call_tool("indeed_get_job_detail", {
                            "job_key": str(job_key),
                            "country": country,
                        }, timeout_seconds=15.0)
                    except Exception as e:
                        logger.warning(f"Could not fetch Indeed detail for job {job_key}: {e}")

                evidence = ConservativeClassifier.classify(
                    platform="indeed",
                    job_url=norm_url,
                    detail_payload=detail_data if isinstance(detail_data, dict) else None,
                    initial_source="indeed_search_jobs",
                )

                description = None
                if detail_data and isinstance(detail_data, dict):
                    description = detail_data.get("description")

                posted_date = item.get("posted_date")
                freshness, f_reason = FreshnessClassifier.evaluate(posted_date_str=posted_date)
                evidence.evidence_notes = f"{evidence.evidence_notes}; Freshness: {f_reason}"

                is_partial = False
                if not description or len(description.strip()) < 250:
                    is_partial = True

                record = JobRecord(
                    platform="indeed",
                    platform_job_id=str(platform_id or ""),
                    title=item.get("title") or "Unknown Title",
                    company=item.get("company") or "Unknown Company",
                    location=item.get("location"),
                    url=raw_url,
                    normalized_url=norm_url,
                    posted_date=posted_date,
                    freshness_status=freshness,
                    description_snippet=description if description else None,
                    is_partial_description=is_partial,
                    validation=evidence,
                )
                results.append(record)
                page_added += 1

            if page_added == 0:
                logger.info("Indeed page returned no new records (all duplicates); stopping pagination.")
                break

            next_cursor = res.get("next_cursor")
            if not next_cursor or not isinstance(next_cursor, str) or not next_cursor.strip():
                exhausted = True
                break

            next_cursor_str = next_cursor.strip()
            if next_cursor_str in seen_cursors or next_cursor_str == current_cursor:
                logger.warning(f"Indeed returned repeated cursor '{next_cursor_str}'; terminating pagination to avoid loop.")
                break

            seen_cursors.add(next_cursor_str)
            current_cursor = next_cursor_str

        if len(results) >= target_limit and not exhausted:
            logger.info(
                f"Indeed search reached configured limit ({len(results)} items, target_limit={target_limit}); "
                "discovery may not be exhaustive."
            )
        else:
            logger.info(f"Indeed discovery finished: retrieved {len(results)} unique jobs.")

        return results

